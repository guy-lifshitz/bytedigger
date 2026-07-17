"""Workflow engine CLI runner — entrypoint for Bun↔Python bridge.

Usage:
    python3 run.py --workflow NAME [--ctx FILE | --ctx-json STR]
                   [--event-log PATH] [--run-id ID]
                   [--derive-state PATH]
    python3 run.py --list

Loads workflows from workflows/ package, executes the named one,
writes JSON of StepResult to stdout. Exit code: 0 ok/skip, 1 error, 2 usage.

Stage 1c additions:
    --event-log PATH    attach an EventLog so the engine emits events to PATH
    --run-id ID         caller-supplied run id (else random hex)
    --derive-state PATH read jsonl events at PATH, print derived state JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Local imports — sys.path setup so workflows/ + contracts.py + engine.py resolve
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "workflows"))

try:  # host-side config provider is optional in the OSS distribution
    import hal_config_provider  # noqa: E402
except ImportError:  # noqa: E402
    hal_config_provider = None  # noqa: E402
if hal_config_provider is not None:
    hal_config_provider.maybe_register_hal_config_provider(argv=sys.argv[1:])  # noqa: E402 — GH834 project-mode: conditionally register HAL config provider BEFORE event_log import (event_log.HAL_DIR consumed by reject_log/phase_1/phase_2)

from contracts import StepResult, WorkflowContext  # noqa: E402
from derive_state import replay  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402
from event_sink import get_event_sink  # noqa: E402
from lib.dbos_setup import init_dbos, execute_durable_workflow, teardown_dbos, hard_exit  # noqa: E402
from lib.dbos_list_runs import list_runs  # noqa: E402
from lib.dbos_status_run import status_run  # noqa: E402
from lib.restart_governor import governor_gate, governor_record_result, governor_record_pause, governor_reset, governor_reset_full, scratchpad_dir_from_config  # noqa: E402
from lib.env_limit import is_paused_result  # noqa: E402
from config_provider import get_config  # noqa: E402
import telemetry_ctx  # noqa: E402
import uuid as _uuid_mod  # noqa: E402
import workflows  # noqa: E402
try:
    import lib.reference_backends.anthropic_api as _anthropic_api_backend_mod  # noqa: F401  — #302 reference backend
    _anthropic_api_backend_mod.register()  # explicit (idempotent) so reload/reset re-registers, not just import side-effect
except Exception:
    pass  # reference battery optional; minimal-core packages may omit it
try:
    import lib.reference_backends.pydantic_openai as _pydantic_openai_backend_mod  # noqa: F401  — GH851 reference backend
    _pydantic_openai_backend_mod.register()  # explicit (idempotent, overwrite=True); raises ImportError w/ pip-extra hint if pydantic_ai absent — swallowed here
except Exception:
    pass  # reference battery optional; minimal-core packages / no-pydantic_ai installs may omit it
try:
    import lib.reference_backends.pydantic_anthropic as _pydantic_anthropic_backend_mod  # noqa: F401  — GH860 reference backend
    _pydantic_anthropic_backend_mod.register()  # explicit (idempotent, overwrite=True); raises ImportError w/ pip-extra hint if pydantic_ai/anthropic absent — swallowed here
except Exception:
    pass  # reference battery optional; minimal-core packages / no-pydantic_ai/anthropic installs may omit it
try:
    import lib.reference_backends.agent_sdk as _agent_sdk_backend_mod  # noqa: F401  — GH885 reference backend
    _agent_sdk_backend_mod.register()  # explicit (idempotent, overwrite=True); raises ImportError w/ pip hint if claude_agent_sdk absent — swallowed here
except Exception as _agent_sdk_reg_err:
    import logging  # noqa: PLC0415 — local import, module-level import order untouched
    logging.getLogger(__name__).debug("agent-sdk reference backend unavailable: %s", _agent_sdk_reg_err)  # optional battery; no-claude-agent-sdk installs omit it


def make_engine(event_log_path: str | None = None) -> WorkflowEngine:
    log = get_event_sink(event_log_path) if event_log_path else None
    eng = WorkflowEngine(event_log=log)
    workflows.register_all(eng)
    return eng


def parse_ctx(args) -> WorkflowContext:
    if args.ctx:
        data = json.loads(Path(args.ctx).read_text())
    elif args.ctx_json:
        data = json.loads(args.ctx_json)
    else:
        data = {}
    _session_id = data.get("session_id")
    # normalize legacy "unknown" sentinel from pre-S1 callers; engine boundary catches None
    if _session_id == "unknown":
        _session_id = None
    return WorkflowContext(
        tenant_id=data.get("tenant_id", "hal"),
        scope=data.get("scope"),
        db_path=data.get("db_path"),
        org_config=data.get("org_config"),
        question=data.get("question", ""),
        session_id=_session_id,
        persona=data.get("persona", "hal"),
        framework=data.get("framework"),
        domain=data.get("domain"),
    )


def main() -> int:
    if sys.argv[1:2] == ["doctor"]:
        from doctor import doctor_main
        return doctor_main(sys.argv[2:])

    p = argparse.ArgumentParser()
    p.add_argument("--workflow")
    p.add_argument("--ctx", help="path to JSON file with WorkflowContext fields")
    p.add_argument("--ctx-json", dest="ctx_json", help="inline JSON string with WorkflowContext fields")
    p.add_argument("--list", action="store_true", help="list registered workflows")
    p.add_argument("--event-log", dest="event_log", help="append events to PATH while executing")
    p.add_argument("--run-id", dest="run_id", help="run id for emitted events (default: random hex)")
    p.add_argument("--derive-state", dest="derive_state", help="read jsonl at PATH, print derived state, exit")
    p.add_argument("--list-runs", dest="list_runs", action="store_true", help="list known DBOS runs grouped by run_id")
    p.add_argument("--status", dest="status_run_id", metavar="RUN_ID", help="show per-phase status for one run_id")
    p.add_argument("--json", dest="json", action="store_true", help="emit JSON (modifies --list-runs or --status)")
    p.add_argument("--limit", type=int, default=100, help="max runs to emit (default 100)")
    p.add_argument(
        "--restart-reason",
        dest="restart_reason",
        help="reset restart-governor state with a logged reason (operator escape; requires --workflow)",
    )
    p.add_argument("--no-hal", action="store_true", help="project-mode: do not register the HAL config provider (neutral defaults)")
    args = p.parse_args()

    if args.restart_reason is not None and not args.restart_reason.strip():
        sys.stderr.write("--restart-reason requires a non-empty reason\n")
        return 2

    if args.derive_state:
        log = EventLog(args.derive_state)
        state = replay(log.read_all())
        print(json.dumps(state, default=str))
        return 0

    if args.list_runs:
        sys.stdout.write(list_runs(as_json=args.json, limit=args.limit))
        return 0

    if args.status_run_id:
        stdout, stderr, code = status_run(args.status_run_id, as_json=args.json)
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        return code

    eng = make_engine(args.event_log)

    if args.list:
        print(json.dumps(eng.registered()))
        return 0

    if not args.workflow:
        sys.stderr.write("--workflow is required (or use --list)\n")
        return 2

    try:
        ctx = parse_ctx(args)
        if args.run_id:
            from dataclasses import replace
            merged_org = dict(ctx.org_config or {})
            merged_org["run_id"] = args.run_id
            ctx = replace(ctx, org_config=merged_org)
        init_dbos()
        _resolved_run_id = args.run_id or _uuid_mod.uuid4().hex[:12]
        telemetry_ctx.set_invocation_run_id(_resolved_run_id)  # GH497 D3
        if args.restart_reason:
            if args.event_log:
                governor_reset_full(
                    Path(args.event_log).parent,
                    _resolved_run_id,
                    args.workflow,
                    args.restart_reason,
                    args.event_log,
                    scratchpad_dir=scratchpad_dir_from_config(ctx.org_config),
                )
            else:
                sys.stderr.write(
                    "--restart-reason ignored: no --event-log (governor inactive)\n"
                )
        _denial = governor_gate(args.event_log, _resolved_run_id, args.workflow, ctx.org_config)
        if _denial is not None:
            print(json.dumps(_denial))
            return 1
        _ret = execute_durable_workflow(
            args.workflow,
            asdict(ctx),
            _resolved_run_id,
            event_log_path=args.event_log,
        )
        result = StepResult(
            status=_ret["status"],
            data=_ret["data"],
            duration_ms=_ret["duration_ms"],
            step_name=_ret["step_name"],
            error=_ret.get("error"),
            error_code=_ret.get("error_code"),
            suggestion=_ret.get("suggestion"),
            recoverable=_ret.get("recoverable", True),
            # metadata omitted — default_factory=dict gives {}; bun toHaveProperty("metadata") passes
        )
        if args.event_log:
            # GH576 C474E073: PAUSED lane budget exemption — a spend-limit
            # pause refunds the start instead of recording a countable error.
            if get_config().gate_enabled("HAL_PAUSE_LANE") and is_paused_result(result.status, result.error_code):
                governor_record_pause(
                    Path(args.event_log).parent,
                    _resolved_run_id,
                    args.workflow,
                )
            else:
                governor_record_result(
                    Path(args.event_log).parent,
                    _resolved_run_id,
                    args.workflow,
                    result.error_code if result.status not in ("ok", "skip") else None,
                    recoverable=result.recoverable,
                )
    except KeyError as e:
        print(json.dumps({"status": "error", "error": str(e), "error_code": "E_NOT_REGISTERED"}))
        return 2
    except ValueError as e:
        print(json.dumps({"status": "error", "error": str(e), "error_code": "E_BAD_CTX"}))
        return 2
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "error": str(e), "error_code": "E_FILE_NOT_FOUND"}))
        return 2
    except Exception as e:  # last-resort
        print(json.dumps({
            "status": "error",
            "error": str(e),
            "error_code": "E_RUNNER",
            "exception_type": type(e).__name__,
        }))
        return 2
    finally:
        teardown_dbos()

    print(json.dumps(asdict(result), default=str))
    return 0 if result.status in ("ok", "skip") else 1


if __name__ == "__main__":
    hard_exit(main())
