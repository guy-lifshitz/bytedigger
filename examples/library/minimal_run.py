#!/usr/bin/env python3
"""Minimal ByteDigger library usage -- no LLM, no API key.

Builds the engine, runs the `echo` workflow against a toy context,
and replays the event log into derived state. This is the smallest
end-to-end loop: engine -> workflow -> append-only event log -> replay.

Run from anywhere after `pip install -e engine_py`:

    python3 examples/library/minimal_run.py
"""
import json
import sys
import tempfile
from pathlib import Path

# When running from a checkout without pip install, put engine_py on the path.
_ENGINE = Path(__file__).resolve().parents[2] / "engine_py"
if _ENGINE.is_dir():
    sys.path.insert(0, str(_ENGINE))

from bytedigger_engine.contracts import WorkflowContext          # noqa: E402
from bytedigger_engine.engine import WorkflowEngine              # noqa: E402
from bytedigger_engine.event_sink import get_event_sink          # noqa: E402
from bytedigger_engine.event_log import EventLog                 # noqa: E402
from bytedigger_engine.derive_state import replay                # noqa: E402
from bytedigger_engine import workflows                               # noqa: E402


def main() -> int:
    # 1. Event log -- append-only JSONL, the single source of truth for a run.
    log_path = Path(tempfile.mkdtemp(prefix="bytedigger-example-")) / "events.jsonl"

    # 2. Engine with every shipped workflow registered.
    engine = WorkflowEngine(event_log=get_event_sink(str(log_path)))
    workflows.register_all(engine)
    print(f"registered workflows: {', '.join(sorted(engine.registered()))}\n")

    # 3. Context -- the feature request rides in `question`. The rest is
    #    tenancy/config plumbing; None/defaults are fine for a toy run.
    ctx = WorkflowContext(
        tenant_id="example",
        scope=None,
        db_path=None,
        org_config=None,
        question="hello from the library example",
        session_id="example-session",
        persona="example",
        framework=None,
        domain=None,
    )

    # 4. Execute. `echo` is the deterministic plumbing-test workflow --
    #    same call shape as phase_0_research .. phase_7_synthesize.
    result, ctx = engine.execute("echo", ctx, run_id="example-run-1")
    print(f"status:  {result.status}")
    print(f"step:    {result.step_name}")
    print(f"data:    {result.data}")

    # 5. Replay the log into derived state -- how ByteDigger resumes
    #    interrupted builds instead of trusting in-memory state.
    state = replay(EventLog(str(log_path)).read_all())
    print(f"\nevent log: {log_path}")
    print(f"derived state: {json.dumps(state, default=str)[:200]}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
