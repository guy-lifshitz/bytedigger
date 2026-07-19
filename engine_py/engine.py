"""Workflow engine — sequential step executor with frozen-context threading.

Workflows are registered explicitly by name and executed directly — no
implicit intent routing.

Stage 1c (2026-04-25): optional `event_log` parameter wires append-only event
emission into each execute() call — workflow_started, step_started, step_finished,
workflow_finished. State is derived by replaying that log (see derive_state.py),
not mutated in build-state.yaml.

NOT a framework — routing + sequencing + formatting only.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from event_sink import EventSink

from contracts import (
    LoopStepContract,
    WorkflowContext,
    StepResult,
    WorkflowDefinition,
    format_boundary_error,
)
import telemetry_ctx
import llm_subprocess
from config_provider import hal_root as _hal_root_fn, path as _path_fn, rework_log_relpath as _rework_log_relpath, foreign_state_dirname as _foreign_state_dirname_fn, get_config
from lib.git_port import git_read
from execution_provenance import (
    SHADOW_EVENT_TYPE,
    execution_provenance,
    is_authoritative_execution,
    shadow_event_name,
)
from lib.step_sentinel import (
    invalidate_cycle_sentinels,
    maybe_read_sentinel,
    maybe_write_sentinel,
)
from lib.cost_rollup import compute_cost_rollup, annotate_invocation
from lib.env_limit import is_paused_result
from lib.spec_defect_ledger import mark_reroute_consumed, reroute_already_consumed
from lib.incident_ledger import emit_incident
from lib.dispatcher_report import emit_dispatcher_report
from lib.stuck_report import build_stuck_report, emit_stuck_report

logger = logging.getLogger(__name__)


# 3F5599A6 A3: manifest-sourced files_touched (17E43F91). DEFER-to-scan-source
# on ANY ValueError (missing/malformed manifest, missing StepResult.data) — a
# non-LLM step landing here by design owns no new error code (§1n).
def _manifest_paths_from_result(result: "StepResult") -> "set[str] | None":
    """Return set(worker_written_paths) for a valid manifest StepResult, else
    None (data is None, field missing/malformed, or any ValueError from the
    canonical validator)."""
    if result.data is None:
        return None
    try:
        paths, _source = llm_subprocess.manifest_from_result(result)
    except ValueError:
        return None
    return set(paths)


# ── DA19030A: durable rework-rate telemetry helpers ──────────────────────────

def _build_rework_summary(
    run_id: str,
    workflow_name: str,
    status: str,
    cycle_count: int,
    rework_stage: str,
    wall_ms: int,
) -> dict:
    """PURE helper: build a rework summary record with exactly 7 keys.

    Returns {run_id, ts, workflow_name, status, cycle_count, rework_stage, wall_ms}.
    ts is ISO8601 UTC with trailing Z.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "run_id": run_id,
        "ts": ts,
        "workflow_name": workflow_name,
        "status": status,
        "cycle_count": cycle_count,
        "rework_stage": rework_stage,
        "wall_ms": wall_ms,
    }


def default_rework_log_path() -> Path:
    """Resolve the default rework-log path lazily based on cwd.

    Mirrors reject_log.default_reject_log_path() / event_log.default_log_path():
    HAL dogfood (cwd inside HAL install root) → canonical central HAL rework log;
    foreign project → cwd-relative foreign_state_dirname()/build-rework-log.jsonl
    (no HAL-state pollution). HOME/cwd unresolvable → treat cwd as foreign (safe default).
    """
    try:
        hal_dir = _hal_root_fn()
        cwd = Path.cwd().resolve()
        if cwd == hal_dir or cwd.is_relative_to(hal_dir):
            return hal_dir / _rework_log_relpath()
    except (OSError, ValueError, RuntimeError):
        pass
    return Path.cwd() / _foreign_state_dirname_fn() / "build-rework-log.jsonl"


def _rework_log_path() -> Path:
    """Resolve the durable rework log path.

    Returns Path(HAL_REWORK_LOG) if the env var is set and non-empty,
    else default_rework_log_path() (cwd-graceful: HAL install root →
    canonical rework log; foreign cwd → foreign_state_dirname()/build-rework-log.jsonl).
    """
    return _path_fn("HAL_REWORK_LOG", default_rework_log_path())


def _append_rework_summary(record: dict) -> None:
    """Append a rework summary record to the durable log (best-effort, never raises).

    Opens the resolved path in append mode and writes json.dumps(record) + newline.
    Any exception is logged as a warning and swallowed — telemetry must not break a build.
    """
    try:
        path = _rework_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001
        logger.warning("_append_rework_summary failed for run_id=%s", record.get("run_id"), exc_info=True)


# ── end DA19030A helpers ──────────────────────────────────────────────────────


_RETRY_CONTROL_KEYS = frozenset({
    "retry_from_step",
    "cycle_count",
    "recoverable",
    "findings_truncated",
    "findings_original_bytes",
    "gate_budget_ok",
})

# GH641: defense-in-depth hard ceiling on total body-starts for an opt-in
# LoopStepContract (consume_recoverable_retry=True). Bounds
# c.max_iterations + _COMPOSITE_RETRY_HARD_CAP so a misbehaving gate that
# never caps (never returns recoverable=False) can never re-introduce an
# unbounded loop, even though the gate's own cap is expected to trip first.
_COMPOSITE_RETRY_HARD_CAP = 2


def _findings_to_str(findings: Any) -> str:
    """Normalize a retry StepResult's findings payload to str (GH615).

    Producers historically send str; green_lint PASS-with-warnings sends
    list-of-dicts. The retry boundary must hold both (§1o).
    """
    if isinstance(findings, str):
        return findings
    if not findings:
        return ""
    try:
        if isinstance(findings, list):
            parts = []
            for f in findings:
                if isinstance(f, dict):
                    parts.append(f"{f.get('check_id')} at {f.get('path')}:{f.get('start')}")
                else:
                    parts.append(str(f))
            return "; ".join(parts)
        return json.dumps(findings, ensure_ascii=False, default=str)
    except Exception:
        return str(findings)


class WorkflowEngine:
    """Executes registered workflows sequentially with frozen-context threading.

    Public API:
        register(name, workflow) -> None
            Register a WorkflowDefinition under a name.

        execute(workflow_name, context, run_id=None) -> tuple[StepResult, WorkflowContext]
            Execute the workflow registered under *workflow_name*. Returns the
            final StepResult and the (immutable) WorkflowContext. If an
            ``event_log`` was supplied at construction, emits four event types
            per call: workflow_started, step_started/finished (per step),
            workflow_finished.

    Each step receives (context, prev_step_result) and returns a StepResult.
    A non-skippable error stops the pipeline; ``skip_on_error=True`` continues.
    """

    _MAX_SAME_CYCLE_RETRIES = 3

    def __init__(self, event_log: "EventSink | None" = None) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._event_log = event_log  # duck-typed: any obj with .append(event_type, payload, run_id)
        # GH750 §2.3: per-instance same-cycle-retry attempt budget, keyed
        # (step_name, next_cycle). Reset per-run in execute() (below).
        self._same_cycle_retries: dict[tuple[str, int], int] = {}

    def register(self, name: str, workflow: WorkflowDefinition) -> None:
        if name in self._workflows:
            raise ValueError(f"workflow {name!r} already registered")
        self._workflows[name] = workflow

    def registered(self) -> list[str]:
        return sorted(self._workflows.keys())

    def execute(
        self,
        workflow_name: str,
        context: WorkflowContext,
        run_id: str | None = None,
    ) -> tuple[StepResult, WorkflowContext]:
        if workflow_name not in self._workflows:
            raise KeyError(f"workflow {workflow_name!r} not registered")
        workflow = self._workflows[workflow_name]
        rid = run_id or uuid.uuid4().hex[:12]

        # DA19030A §2.3: reset per-run high-water marks so a reused engine instance
        # does not leak a prior run's cycle count into this run.
        self._rework_cycle_high = 1
        self._rework_last_step: str | None = None
        # GH750 §2.3 (B4): reset the same-cycle-retry budget per run so a
        # reused engine instance does not leak a prior run's counters.
        self._same_cycle_retries = {}

        # GH767 §2.4a: outbound-leg cache-busting on a spec-defect reroute
        # entry. ONE-SHOT per (run_id, workflow, attempt) via mark-THEN-
        # invalidate: an invalidation we cannot durably record would be
        # repeated on every crash-restart, re-paying every LLM step without
        # bound — so if we cannot mark it, we do not invalidate it (fail-safe).
        reroute = (getattr(context, "org_config", None) or {}).get("phase_reroute")
        if reroute:
            _attempt = reroute.get("attempt")
            if not reroute_already_consumed(context, rid, workflow_name, _attempt) \
                    and mark_reroute_consumed(context, rid, workflow_name, _attempt):
                _removed = invalidate_cycle_sentinels(
                    context, workflow.steps, 1, rid, self._emit, workflow_name=workflow_name,
                )
                self._emit(
                    "phase_reroute_entry",
                    {
                        "workflow_name": workflow_name,
                        "from_phase": reroute.get("from_phase"),
                        "attempt": _attempt,
                        "sentinels_invalidated": len(_removed),
                    },
                    rid,
                )

        self._emit("workflow_started", {"workflow_name": workflow_name}, rid)
        start = time.monotonic()
        final_result = self._execute_steps(workflow, context, rid)
        wall_ms = int((time.monotonic() - start) * 1000)
        # GH452 §2.3: rollup emit strictly before workflow_finished; never fail
        # the run on telemetry error, and never fall back to a default log path.
        try:
            _ev_path = getattr(self._event_log, "path", None)
            if _ev_path:
                rollup = annotate_invocation(compute_cost_rollup(_ev_path, rid), rid)
                self._emit("workflow_cost_rollup", rollup, rid)
        except Exception:
            pass
        # GH576 C474E073: PAUSED lane — status=="paused" for the spend-limit
        # env-lane, gated by HAL_PAUSE_LANE (default ON). StepResult.status
        # itself stays "error" (§1n vocabulary unchanged); only the emitted
        # event/derived-state status is remapped.
        _emit_status = final_result.status
        if get_config().gate_enabled("HAL_PAUSE_LANE") and is_paused_result(final_result.status, final_result.error_code):
            _emit_status = "paused"
        self._emit("workflow_finished", {"workflow_name": workflow_name, "status": _emit_status, "wall_ms": wall_ms}, rid)
        # DA19030A §2.4: append durable rework summary IMMEDIATELY after workflow_finished.
        # workflow_finished emit is byte-identical to pre-DA19030A (derive_state.py:88 unchanged).
        # GH497 A3: annotate OUTSIDE the try/except above — a telemetry rollup
        # error must still leave the rework summary annotated.
        # GH700 §2.3: a zombie (non-authoritative) execution must not append
        # a phantom rework record for a run that is still live in the
        # foreground.
        if is_authoritative_execution():
            _append_rework_summary(annotate_invocation(_build_rework_summary(
                rid, workflow_name, final_result.status,
                self._rework_cycle_high, self._rework_last_step or "none", wall_ms,
            ), rid))
        if _emit_status in ("error", "escalate") and is_authoritative_execution():
            emit_dispatcher_report(
                run_id=rid, phase=workflow_name, status=_emit_status,
                error_code=final_result.error_code, error=final_result.error,
                wall_ms=wall_ms,
                events_path=getattr(self._event_log, "path", None),
            )
            # GH1041 §2.3.2: terminal non-recoverable finish -> stuck-report.
            # Recoverable terminal errors are owned by the governor breaker
            # (§2.2) once its budget exhausts; do NOT emit here for those.
            if final_result.recoverable is False:
                _ev_path = getattr(self._event_log, "path", None)
                if _ev_path:
                    _report = build_stuck_report(
                        run_id=rid,
                        workflow=workflow_name,
                        step_name=final_result.step_name,
                        breaker="terminal_failure",
                        error_code=final_result.error_code,
                        retry_count=0,
                        counters=None,
                        last_error=final_result.error,
                    )
                    emit_stuck_report(Path(_ev_path).parent, _report, rid, event_log_path=_ev_path)
        return final_result, context

    # Design A pipeline recovery (decree 2026-04-26): max workflow-level
    # validation retries. Hard-coded for v1; configurable in v2 once
    # telemetry data exists. See tests/test_pipeline_recovery.py.
    # MED #6: cap must stay in sync with workflow-level MAX_REVIEW_CYCLES constants.
    # Verified at import time by test_engine_cap_consistency (test_phase_45_spec.py).
    _MAX_VALIDATION_CYCLES = 2
    # GH625: per-gate budgets (recoverable_gate.py) are already bounded
    # (cycle_cap <= 2 per gate, matrix _recoverable_policy.py); this backstop
    # is a deterministic runaway guard should gate_attempts ever be dropped
    # by a step that rebuilds data.
    _GATE_BUDGET_HARD_BACKSTOP = 6

    def _execute_steps(
        self,
        workflow: WorkflowDefinition,
        context: WorkflowContext,
        run_id: str,
        initial_data: Any = None,
        start_step: int = 0,
        cycle: int = 1,
    ) -> StepResult:
        """Execute steps sequentially, threading prev result through each step.

        ``start_step`` lets the engine partial-replay a workflow from a given
        index — used by the validation-retry hook to re-invoke RED → validate
        without rerunning earlier phases.
        """
        if not workflow.steps:
            return StepResult(
                status="ok",
                data=None,
                duration_ms=0,
                step_name=workflow.name,
            )

        prev: Any = initial_data
        last_result: StepResult | None = None
        _escalated: bool = False  # 585E30E3: set True when any step returns escalate

        for step in workflow.steps[start_step:]:
            start_ms = int(time.monotonic() * 1000)
            self._emit("step_started", {"step_name": step.name}, run_id)

            # Decree 2026-04-26 cat A: push current run ctx so llm_subprocess
            # can emit subprocess_spawned/exited under this step.
            telemetry_ctx.set_current_run(
                event_log=self._event_log,
                run_id=run_id,
                step_name=step.name,
                phase=workflow.name,
                tier=(context.org_config or {}).get("complexity"),  # GH375
                cycle=cycle,
            )
            # Decree 2026-04-26 cat B: snapshot git baseline before step
            # (changes-since-HEAD set so we can subtract to get per-step delta).
            git_pre = _git_changes_vs_head()

            # 04E3B874 S1: pre-execute boundary check — hard-fail when a
            # declared required ctx field is missing/None/empty/falsy.
            # Uses getattr(..., None) so schema-drift typos surface as
            # E_REQUIRED_CTX_MISSING (not AttributeError). Truthy check
            # catches None, "", 0, [], {} uniformly. Halts via the existing
            # error-propagation path: set `result`, emit step_finished,
            # then the standard `if result.status == 'error'` block returns.
            missing = [
                k for k in step.required_ctx_fields
                if not getattr(context, k, None)
            ]
            if missing:
                error = " | ".join(
                    format_boundary_error(
                        phase=workflow.name,
                        field=k,
                        producer="orchestrator (ctx bootstrap)",
                        where="engine.py",
                        schema=f"WorkflowContext.{k}",
                    )
                    for k in missing
                )
                result = StepResult(
                    status="error",
                    data=None,
                    duration_ms=0,
                    step_name=step.name,
                    error=error,
                    error_code="E_REQUIRED_CTX_MISSING",
                    recoverable=False,
                )
                telemetry_ctx.clear_current_run()
            else:
                try:
                    cached = maybe_read_sentinel(context, step, cycle, run_id, self._emit, workflow_name=workflow.name)
                    result = cached if cached is not None else step.execute(context, prev)
                finally:
                    telemetry_ctx.clear_current_run()

            maybe_write_sentinel(context, step, cycle, run_id, result, workflow_name=workflow.name)

            if result.duration_ms == 0:
                elapsed = int(time.monotonic() * 1000) - start_ms
                result = replace(result, duration_ms=elapsed)

            # Decree 2026-04-26 cat B: emit files_touched for paths whose
            # status differs from pre-step baseline. Suppress empty events
            # to keep ordering tests stable.
            if git_pre is not None:
                git_post = _git_changes_vs_head()
                if git_post is not None:
                    delta = _diff_changes(git_pre, git_post)
                    if any(delta.values()):
                        paths_flat = sorted({p for ps in delta.values() for p in ps})
                        by_status = {k: v for k, v in delta.items() if v}
                        # 3F5599A6 A3: intersect the scan-delta against the
                        # StepResult manifest (worker_written_paths) when the
                        # producing step reported one. manifest is None (no
                        # manifest / malformed / ValueError) → DEFER to the
                        # scan-source paths unchanged (§2 D2).
                        payload: dict[str, Any] = {
                            "phase": workflow.name,
                            "step_name": step.name,
                        }
                        manifest = _manifest_paths_from_result(result)
                        if manifest is not None:
                            kept = [p for p in paths_flat if p in manifest]
                            by_status = {
                                k: [p for p in v if p in manifest]
                                for k, v in by_status.items()
                            }
                            by_status = {k: v for k, v in by_status.items() if v}
                            payload["paths"] = kept
                            payload["by_status"] = by_status
                            payload["source"] = "manifest"
                            payload["n_scan_only"] = len(paths_flat) - len(kept)
                        else:
                            payload["paths"] = paths_flat
                            payload["by_status"] = by_status
                            payload["source"] = "scan"
                        self._emit(
                            "files_touched",
                            payload,
                            run_id,
                        )

            self._emit(
                "step_finished",
                {
                    "step_name": step.name,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                },
                run_id,
            )

            if result.status in ("error", "escalate"):
                emit_incident(
                    run_id=run_id, phase=workflow.name, step_name=step.name, cycle=cycle,
                    status=result.status, error_code=result.error_code, error=result.error,
                    wall_ms=result.duration_ms,
                    events_path=getattr(self._event_log, "path", None),
                )

            last_result = result

            # 585E30E3: escalate — non-terminal signal. Surfaces the condition
            # (workflow outcome ≠ success) but does NOT abort the step chain.
            # Downstream steps continue; final_result.status will be "escalate"
            # if no subsequent step overwrites it with "error".
            if result.status == "escalate":
                self._emit(
                    "step_escalated",
                    {
                        "step_name": result.step_name,
                        "error_code": result.error_code,
                        "error": result.error,
                    },
                    run_id,
                )
                _escalated = True
                prev = result
                continue

            if result.status == "error" and not step.skip_on_error:
                # Design A retry: any step that signals retry intent via
                # StepResult(recoverable=True, data={"retry_from_step": N, ...}) triggers
                # engine recursion to step N. The error_code is diagnostic only —
                # no longer a whitelist gate (B07BC910). Audit verified all 6
                # `recoverable=True` sites either set retry_from_step (intentional retry)
                # or return data=None (blocked by isinstance check, e.g. E_STEP_TIMEOUT).
                # Cycle cap at _MAX_VALIDATION_CYCLES still enforced.
                if (
                    result.recoverable
                    and isinstance(result.data, dict)
                    and result.data.get("retry_from_step") is not None
                ):
                    cycle_count = int(result.data.get("cycle_count", 1))
                    findings = _findings_to_str(result.data.get("findings", ""))
                    gate_budget_ok = bool(result.data.get("gate_budget_ok"))
                    if cycle_count < self._MAX_VALIDATION_CYCLES or (
                        gate_budget_ok and cycle_count < self._GATE_BUDGET_HARD_BACKSTOP
                    ):
                        next_cycle = cycle_count + 1
                        red_index = result.data.get("retry_from_step")
                        start_step_for_retry = int(red_index) if red_index is not None else 0
                        # GH750 §2.2/§2.3: same-cycle retry detection, bounded
                        # attempt budget, and cycle-keyed sentinel invalidation.
                        # Pinned BEFORE the _rework_* mutations and the
                        # iteration_started emit — a capped attempt must emit
                        # neither and must not bump the high-water marks.
                        same_cycle = next_cycle <= cycle
                        if same_cycle:
                            attempts = self._same_cycle_retries.get((result.step_name, next_cycle), 0) + 1
                            if attempts > self._MAX_SAME_CYCLE_RETRIES:
                                self._emit(
                                    "same_cycle_retry_capped",
                                    {
                                        "phase": workflow.name,
                                        "step_name": result.step_name,
                                        "cycle": next_cycle,
                                        "attempts": attempts - 1,
                                    },
                                    run_id,
                                )
                                # GH1041 §2.3.1 / D2: guarded by
                                # is_authoritative_execution() — unlike the
                                # engine.py terminal site, this branch has no
                                # zombie guard by default; an unguarded emit
                                # would let a non-authoritative DBOS replay
                                # write a phantom stuck verdict for a run
                                # still live in the foreground.
                                if is_authoritative_execution():
                                    _ev_path = getattr(self._event_log, "path", None)
                                    if _ev_path:
                                        _report = build_stuck_report(
                                            run_id=run_id,
                                            workflow=workflow.name,
                                            step_name=result.step_name,
                                            breaker="same_cycle_retry_cap",
                                            error_code=result.error_code,
                                            retry_count=attempts - 1,
                                            counters=None,
                                            last_error=result.error,
                                        )
                                        emit_stuck_report(
                                            Path(_ev_path).parent, _report, run_id, event_log_path=_ev_path,
                                        )
                                return result
                            self._same_cycle_retries[(result.step_name, next_cycle)] = attempts
                            invalidate_cycle_sentinels(
                                context,
                                workflow.steps[start_step_for_retry:],
                                next_cycle,
                                run_id,
                                self._emit,
                                workflow_name=workflow.name,
                            )
                        # DA19030A §2.3: track high-water cycle count and the step that triggered retry.
                        self._rework_cycle_high = max(self._rework_cycle_high, next_cycle)
                        self._rework_last_step = result.step_name
                        self._emit(
                            "iteration_started",
                            {
                                "cycle_number": next_cycle,
                                "findings_summary": (findings or "")[:500],
                            },
                            run_id,
                        )
                        # MED #5: dedicated retry event alongside iteration_started.
                        self._emit(
                            "phase_retry_triggered",
                            {
                                "phase_name": workflow.name,
                                "step_name": result.step_name,
                                "cycle": next_cycle,
                                "error_code": result.error_code,
                                "findings_bytes": len((findings or "").encode("utf-8")),
                            },
                            run_id,
                        )
                        # HIGH #2: emit findings_truncated event when cap was applied.
                        if result.data.get("findings_truncated"):
                            self._emit(
                                "findings_truncated",
                                {
                                    "phase": workflow.name,
                                    "original_bytes": result.data.get("findings_original_bytes", 0),
                                    "truncated_to": len((findings or "").encode("utf-8")),
                                },
                                run_id,
                            )
                        # 56D695F2 Change 6: forward non-control keys from retry
                        # StepResult.data into initial_data so Class B retry-paths
                        # (e.g. green_lint recoverable) can thread red_commit_sha,
                        # spec_path, etc. across the retry boundary.
                        # _RETRY_CONTROL_KEYS + {"cycle", "findings"} are excluded:
                        # cycle/findings are placed explicitly; control keys are
                        # consumed by the engine and must NOT reach the next step's prev.
                        forwarded = {
                            k: v for k, v in (result.data or {}).items()
                            if k not in _RETRY_CONTROL_KEYS and k not in {"cycle", "findings"}
                        }
                        self._emit(
                            "retry_data_forwarded",
                            {
                                "phase": workflow.name,
                                "forwarded_keys": sorted(list(forwarded.keys())),
                            },
                            run_id,
                        )
                        retry_result = self._execute_steps(
                            workflow,
                            context,
                            run_id,
                            initial_data={**forwarded, "cycle": next_cycle, "findings": findings},
                            start_step=int(red_index) if red_index is not None else 0,
                            cycle=next_cycle,
                        )
                        # MED #7: emit raw verdict — never coerce UNKNOWN→PASS
                        # (UNKNOWN means the parser couldn't read the verdict; masking it
                        # as PASS silently relabels parse failures as success in telemetry).
                        verdict = "UNKNOWN"
                        if isinstance(retry_result.data, dict):
                            verdict = retry_result.data.get("verdict", verdict)
                        self._emit(
                            "iteration_finished",
                            {"cycle_number": next_cycle, "verdict": verdict},
                            run_id,
                        )
                        return retry_result
                # GH925: terminal (non-retried) error exit — if the failing step
                # declared "invalidate_cycle_sentinels_on_fail", the cycle's
                # cycle-keyed sentinels are stale (re-entry over the same
                # run_id must not replay them). Unlink before returning.
                if isinstance(result.data, dict) and result.data.get("invalidate_cycle_sentinels_on_fail"):
                    _removed = invalidate_cycle_sentinels(
                        context, workflow.steps, cycle, run_id, self._emit,
                        workflow_name=workflow.name, reason="terminal_fail_state_invalidation",
                    )
                    self._emit(
                        "terminal_fail_sentinels_invalidated",
                        {"phase": workflow.name, "step_name": result.step_name,
                         "error_code": result.error_code, "cycle": cycle,
                         "sentinels_invalidated": len(_removed)},
                        run_id,
                    )
                return result

            prev = result

        if last_result is None:
            raise RuntimeError("workflow executed zero steps; start_step beyond range")
        # 585E30E3: promote final ok to escalate when any step escalated.
        # Escalate-then-error yields "error" (error overwrites; this guard only fires on "ok").
        if _escalated and last_result.status == "ok":
            last_result = replace(last_result, status="escalate")
        return last_result

    def _emit(self, event_type: str, payload: dict, run_id: str) -> None:
        """Forward to event_log if attached; never raises (events are best-effort).

        GH700 §2.2: a DBOS-recovery re-execution running off the process
        main thread is not the authoritative execution. Its events are
        shadowed under a mangled type (SHADOW_EVENT_TYPE) so they can never
        be mistaken for a real terminal workflow_finished/workflow_started
        by downstream consumers. Gate HAL_ENGINE_SHADOW_EMITS (default ON)
        via the config-provider seam — reversible in one env flip (AC9).
        """
        if self._event_log is None:
            return
        try:
            if (get_config().gate_enabled("HAL_ENGINE_SHADOW_EMITS")
                    and not is_authoritative_execution()):
                self._event_log.append(
                    SHADOW_EVENT_TYPE,
                    {**payload,
                     "shadowed_event": shadow_event_name(event_type),
                     "provenance": execution_provenance()},
                    run_id,
                )
                return
            self._event_log.append(event_type, payload, run_id)
        except Exception as e:  # noqa: BLE001 — observability must not break execution
            logger.warning("event_log append failed for %s: %s", event_type, e)


class LoopRunner:
    """Executor for declarative LoopStepContract (E2440DB5).

    Independent of WorkflowEngine — callers compose: invoke a LoopRunner
    from inside a regular StepContract.execute, or wrap a body of steps
    that needs cycle semantics (until_marker / until_bash / max_iterations
    / fresh_context). Phases that previously emitted
    ``StepResult(error_code="E_VALIDATION_RETRY", recoverable=True, data={cycle_count, ...})``
    can migrate to a LoopStepContract block and stop hand-rolling cycle
    state.

    Returns a StepResult whose metadata carries:
        iterations    — how many iterations actually ran (0..max).
        terminated_by — "marker" | "bash" | "max_iterations" | "error".
    """

    def __init__(self, contract: LoopStepContract) -> None:
        self.contract = contract

    def run(self, ctx: WorkflowContext, prev: Any) -> StepResult:
        c = self.contract
        if c.max_iterations == 0:
            return StepResult(
                status="skip", data=None, duration_ms=0, step_name=c.name,
                metadata={"iterations": 0, "terminated_by": "max_iterations"},
            )

        # GH641: opt-in decoupled loop — a body-step recoverable retry is
        # consumed internally instead of being returned up to the outer
        # WorkflowEngine._execute_steps (§2.0/§2.2 of the GH641 spec).
        # Default False (this branch not taken) leaves every existing
        # LoopRunner consumer (e.g. phase_5's validation loop) byte-identical
        # to the loop below.
        if c.consume_recoverable_retry:
            return self._run_consuming(ctx, prev)

        last_iter_result: StepResult | None = None
        outer_prev = prev
        _escalated: bool = False  # 585E30E3: set True when any loop body step escalates
        # GH557: resume-seam extension — outer engine sets telemetry_ctx's
        # current run before invoking the containing step; read it once so
        # loop-body steps flagged resume_sentinel=True can cache/resume.
        # Keyed by loop iteration (1-based), not run_ctx.cycle, so RED→
        # validation cross-iteration cache poisoning cannot happen.
        run_ctx = telemetry_ctx.get_current_run()
        for i in range(c.max_iterations):
            iteration = i + 1
            if c.fresh_context:
                cur_prev: Any = None
            elif i == 0:
                cur_prev = outer_prev
            else:
                cur_prev = last_iter_result

            for body_step in c.body:
                if run_ctx is not None:
                    cached = maybe_read_sentinel(
                        ctx, body_step, iteration, run_ctx.run_id,
                        lambda et, payload, rid: telemetry_ctx.emit_safe(et, payload),
                        workflow_name=c.name, prev=cur_prev,
                    )
                    result = cached if cached is not None else body_step.execute(ctx, cur_prev)
                    maybe_write_sentinel(
                        ctx, body_step, iteration, run_ctx.run_id, result,
                        workflow_name=c.name, prev=cur_prev,
                    )
                else:
                    result = body_step.execute(ctx, cur_prev)
                cur_prev = result
                # 585E30E3: escalate — non-terminal, symmetric to _execute_steps dispatch.
                # LoopRunner has no event_log; telemetry is emitted by the outer engine
                # dispatch when the LoopRunner result reaches _execute_steps.
                if result.status == "escalate":
                    _escalated = True
                    continue
                if result.status == "error" and not body_step.skip_on_error:
                    return replace(
                        result,
                        metadata={
                            **(result.metadata or {}),
                            "iterations": iteration,
                            "terminated_by": "error",
                        },
                    )

            last_iter_result = cur_prev if isinstance(cur_prev, StepResult) else None

            # until_marker check (LLM-judged)
            if c.until_marker and last_iter_result is not None:
                marker_text = _extract_marker_text(last_iter_result, c.marker_field)
                if c.until_marker in marker_text:
                    return replace(
                        last_iter_result,
                        metadata={
                            **(last_iter_result.metadata or {}),
                            "iterations": iteration,
                            "terminated_by": "marker",
                        },
                    )

            # until_bash check (deterministic)
            if c.until_bash:
                try:
                    rc = subprocess.run(
                        c.until_bash, capture_output=True, text=True, timeout=30,
                    ).returncode
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    rc = 1
                if rc == 0:
                    base = last_iter_result or StepResult(
                        status="ok", data=None, duration_ms=0, step_name=c.name,
                    )
                    return replace(
                        base,
                        metadata={
                            **(base.metadata or {}),
                            "iterations": iteration,
                            "terminated_by": "bash",
                        },
                    )

        # Hit cap
        base = last_iter_result or StepResult(
            status="ok", data=None, duration_ms=0, step_name=c.name,
        )
        result_at_cap = replace(
            base,
            metadata={
                **(base.metadata or {}),
                "iterations": c.max_iterations,
                "terminated_by": "max_iterations",
            },
        )
        # 585E30E3: promote final ok to escalate when any loop body step escalated.
        if _escalated and result_at_cap.status == "ok":
            result_at_cap = replace(result_at_cap, status="escalate")
        return result_at_cap

    def _run_consuming(self, ctx: WorkflowContext, prev: Any) -> StepResult:
        """GH641: consume_recoverable_retry=True path — the composite is the
        SOLE cycle driver. Two decoupled counters:

        sentinel_iteration — monotonic, +1 on EVERY body-start (a consumed
            retry AND a completed review pass). Sentinel key, so a re-run of
            a flagged body step keys a fresh sentinel (no stale-cycle reread).
        review_iteration — the cap driver; +1 only on a body pass that
            completes WITHOUT a consumed retry. until_marker/until_bash/
            max_iterations fire against this value (REVISE budget unaffected
            by consumed retries).

        A body-step recoverable retry (status=='error' and recoverable and
        data['retry_from_step'] is not None) is threaded to the next body
        pass exactly as the outer WorkflowEngine._execute_steps threads a
        retry (engine.py _execute_steps ~L484-500), then the body loop
        breaks (mirrors re-entering the composite at the body start) and the
        outer while continues without spending review_iteration.

        A terminal error (including the recoverable-gate's cap result, which
        is recoverable=False) is returned up unmodified — bounded, since it
        is not consumable and the outer engine will not recurse on it.

        Hard ceiling: total body-starts bounded by
        c.max_iterations + _COMPOSITE_RETRY_HARD_CAP — defense-in-depth
        against a gate that never caps.
        """
        c = self.contract
        last_iter_result: StepResult | None = None
        outer_prev = prev
        _escalated: bool = False
        run_ctx = telemetry_ctx.get_current_run()

        sentinel_iteration = 0
        review_iteration = 0
        cur_prev: Any = outer_prev
        hard_cap = c.max_iterations + _COMPOSITE_RETRY_HARD_CAP

        while review_iteration < c.max_iterations:
            # GH641: sentinel_iteration advances ONCE per body-start (this
            # while-pass) — same key used by every body_step within the
            # pass, mirroring the base loop's `iteration = i + 1` convention
            # (engine.py:575) where one iteration key spans the whole body.
            sentinel_iteration += 1
            if sentinel_iteration > hard_cap:
                base = cur_prev if isinstance(cur_prev, StepResult) else (
                    last_iter_result or StepResult(
                        status="ok", data=None, duration_ms=0, step_name=c.name,
                    )
                )
                return replace(
                    base,
                    status="error",
                    recoverable=False,
                    metadata={
                        **(base.metadata or {}),
                        "iterations": sentinel_iteration - 1,
                        "terminated_by": "retry_ceiling",
                    },
                )

            if c.fresh_context:
                cur_prev = None
            elif sentinel_iteration == 1:
                cur_prev = outer_prev
            # else: cur_prev already carries the forwarded-retry result or
            # the prior completed pass's last_iter_result.

            retry_consumed = False

            for body_step in c.body:
                if run_ctx is not None:
                    cached = maybe_read_sentinel(
                        ctx, body_step, sentinel_iteration, run_ctx.run_id,
                        lambda et, payload, rid: telemetry_ctx.emit_safe(et, payload),
                        workflow_name=c.name, prev=cur_prev,
                    )
                    result = cached if cached is not None else body_step.execute(ctx, cur_prev)
                    maybe_write_sentinel(
                        ctx, body_step, sentinel_iteration, run_ctx.run_id, result,
                        workflow_name=c.name, prev=cur_prev,
                    )
                else:
                    result = body_step.execute(ctx, cur_prev)
                cur_prev = result

                if result.status == "escalate":
                    _escalated = True
                    continue

                if (
                    result.status == "error"
                    and result.recoverable
                    and isinstance(result.data, dict)
                    and result.data.get("retry_from_step") is not None
                    and not body_step.skip_on_error
                ):
                    # 56D695F2-style forwarding, mirrored from _execute_steps
                    # (engine.py ~L484-500): non-control keys survive the
                    # consumed-retry boundary into the next body-start's prev.
                    forwarded = {
                        k: v for k, v in (result.data or {}).items()
                        if k not in _RETRY_CONTROL_KEYS and k not in {"cycle", "findings"}
                    }
                    next_data = {
                        **forwarded,
                        "cycle": int(result.data.get("cycle_count", 1)) + 1,
                        "findings": result.data.get("findings", ""),
                    }
                    cur_prev = StepResult(
                        status="ok", data=next_data, duration_ms=0, step_name=result.step_name,
                    )
                    retry_consumed = True
                    break

                if result.status == "error" and not body_step.skip_on_error:
                    return replace(
                        result,
                        metadata={
                            **(result.metadata or {}),
                            "iterations": sentinel_iteration,
                            "terminated_by": "error",
                        },
                    )

            if retry_consumed:
                continue

            last_iter_result = cur_prev if isinstance(cur_prev, StepResult) else None
            review_iteration += 1
            cur_prev = last_iter_result

            # until_marker check (LLM-judged)
            if c.until_marker and last_iter_result is not None:
                marker_text = _extract_marker_text(last_iter_result, c.marker_field)
                if c.until_marker in marker_text:
                    return replace(
                        last_iter_result,
                        metadata={
                            **(last_iter_result.metadata or {}),
                            "iterations": review_iteration,
                            "terminated_by": "marker",
                        },
                    )

            # until_bash check (deterministic)
            if c.until_bash:
                try:
                    rc = subprocess.run(
                        c.until_bash, capture_output=True, text=True, timeout=30,
                    ).returncode
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    rc = 1
                if rc == 0:
                    base = last_iter_result or StepResult(
                        status="ok", data=None, duration_ms=0, step_name=c.name,
                    )
                    return replace(
                        base,
                        metadata={
                            **(base.metadata or {}),
                            "iterations": review_iteration,
                            "terminated_by": "bash",
                        },
                    )

        # Hit review cap
        base = last_iter_result or StepResult(
            status="ok", data=None, duration_ms=0, step_name=c.name,
        )
        result_at_cap = replace(
            base,
            metadata={
                **(base.metadata or {}),
                "iterations": review_iteration,
                "terminated_by": "max_iterations",
            },
        )
        # 585E30E3: promote final ok to escalate when any loop body step escalated.
        if _escalated and result_at_cap.status == "ok":
            result_at_cap = replace(result_at_cap, status="escalate")
        return result_at_cap


def _extract_marker_text(result: StepResult, field: str) -> str:
    """String-coerce result.data[field] for substring scan.

    Tolerates non-dict data (returns ""), missing field, and None.
    """
    data = result.data
    if not isinstance(data, dict):
        return ""
    val = data.get(field)
    if val is None:
        return ""
    return str(val)


def _index_of(workflow: WorkflowDefinition, step_name: str) -> int:
    """Return the index of the step named ``step_name`` in workflow.steps.

    Falls back to 0 when not found — caller will re-execute from the top
    rather than crash the retry loop.
    """
    for i, step in enumerate(workflow.steps):
        if step.name == step_name:
            return i
    return 0


def _git_changes_vs_head() -> dict[str, str] | None:
    """Return {path: status_letter} for all working-tree changes vs HEAD.

    Combines `git diff --name-status HEAD` (tracked) with
    `git ls-files --others --exclude-standard` (untracked → 'A').
    Returns None if cwd is not a git repo / git unavailable.
    """
    try:
        diff = git_read(["diff", "--name-status", "HEAD"], timeout=5)
        if diff.returncode != 0:
            return None
        others = git_read(["ls-files", "--others", "--exclude-standard"], timeout=5)
        if others.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    out: dict[str, str] = {}
    for line in diff.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        # rename/copy emit: status\told\tnew → key on new path
        # everything else: status\tpath
        path = parts[-1] if status[0] in ("R", "C") else parts[1]
        out[path] = status[0]  # normalise R100/C90 → first letter
    for line in others.stdout.splitlines():
        path = line.strip()
        if path:
            out[path] = "A"
    return out


def _diff_changes(
    pre: dict[str, str],
    post: dict[str, str],
) -> dict[str, list[str]]:
    """Return per-step delta: paths that newly appeared, whose status changed, OR were removed in post.

    Buckets by post-state status letter. Files that were already in `pre` with
    the same status are excluded — they belong to a prior step. Paths present
    in `pre` but absent from `post` are bucketed under "D" (deleted/reverted).
    """
    out: dict[str, list[str]] = {"A": [], "M": [], "D": []}
    for path, status in post.items():
        if pre.get(path) == status:
            continue
        out.setdefault(status, []).append(path)
    for path in pre:
        if path not in post:
            out["D"].append(path)
    return out
