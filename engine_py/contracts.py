"""
Pure dataclass contracts for HAL workflow engine.

Frozen WorkflowContext + StepResult/StepContract/WorkflowDefinition data
containers for multi-step workflow execution. No orchestration logic here —
see engine.py for the executor.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowContext:
    """Immutable context for workflow execution.

    Attributes:
        tenant_id: Tenant identifier
        scope: Organizational scope (e.g. "PODIUM", "OFFICE_IL", "OFFICE_MX")
        db_path: Path to database file, if any
        org_config: Organization configuration dictionary
        question: User's question/request
        session_id: Session identifier for multi-turn conversations
        persona: Agent persona ("analyst", "ciso", "auditor", etc.)
        framework: Security framework name (e.g. "NIST", "ISO", "CIS")
        domain: Security domain (e.g. "access_control", "incident_response")
        enable_rag: Whether to enable RAG retrieval (default True)
        enable_reranker: Whether to enable reranking (default True)
        message_history: Conversation message history, defaults to None
        llm_provider: LLM provider name (default "azure")
        llm_model: LLM model identifier (default "gpt-4.1-datazone")
    """

    tenant_id: str
    scope: str | None
    db_path: str | None
    org_config: dict | None
    question: str
    session_id: str
    persona: str
    framework: str | None
    domain: str | None
    enable_rag: bool = True
    enable_reranker: bool = True
    enable_domain_scoped_reranker: bool = False
    message_history: list[dict] | None = None
    llm_provider: str = "azure"
    llm_model: str = "gpt-4.1-datazone"
    domains: list[str] = field(default_factory=list)


@dataclass
class StepResult:
    """Result of a single step execution.

    Attributes:
        status: Execution status ("ok", "error", "skip", or "escalate")
        data: Step output data (any type)
        duration_ms: Execution duration in milliseconds
        step_name: Name of the step that produced this result
        error: Error message if status == "error"
        error_code: Error code if status == "error"
    """

    status: str
    data: Any
    duration_ms: int
    step_name: str
    error: str | None = None
    error_code: str | None = None
    suggestion: str | None = None   # NEW (#345): actionable suggestion on error
    recoverable: bool = True        # NEW (#345): can caller retry or fallback?
    metadata: dict[str, Any] = field(default_factory=dict)  # step-level debug/context data (#513)

    def __post_init__(self):
        """Validate status field."""
        if self.status not in ("ok", "error", "skip", "escalate"):
            raise ValueError(
                f"status must be one of 'ok', 'error', 'skip', 'escalate', got '{self.status}'"
            )


# ─── D5699904 boundary envelope — F2344E12 CC-1 + CC-3 + FI-4 + 6F08F5F5 ───


@dataclass
class WorkerExitResult:
    """Canonical batch-build/build boundary envelope payload (D5699904).

    Adopts StepResult's status/error_code/recoverable/suggestion/metadata
    shape so engine_py developers see a familiar API across the
    cross-process boundary. Workers serialize this to ``build-result.json``
    on exit; batch-build coordinator deserializes and classifies.

    F2344E12 closures:
        - CC-1: cost_usd, tokens_in/out, cache_read_tokens fields
        - CC-3: ``error_code`` carries ``thrash_detected`` for F96D3539
        - FI-4: ``recoverable`` distinguishes auth_lost (recoverable=True)
          from worker_lost (recoverable=False) — closes the
          worker_lost vs auth_lost classification race

    6F08F5F5 closure:
        - ``rate_limits`` field carries five_hour + seven_day snapshot
          at exit time, written into batch event log for coordinator
          forecasting

    Attributes:
        schema_version: Symphony-pattern envelope version. Coordinator
            dispatches on this. Bump when adding fields. Current: 1.
        wid: Worker ID assigned by coordinator at spawn.
        status: Exit status. One of: ``"completed"`` (worker finished
            full /build successfully), ``"failed"`` (terminal error,
            see error_code), ``"paused"`` (transient, restart later).
        exit_code: Underlying process exit code (usually 0 for
            completed, non-zero otherwise).
        sha: Resulting commit SHA when status == "completed". None
            otherwise.
        error_code: Canonical fail-class on status != "completed".
            See F2344E12 §8 classification table. Examples:
            "auth_lost", "rate_limit_window_5h", "thrash_detected",
            "worker_lost", "test_fail_permanent", "syntax_error".
        recoverable: True if a respawn could plausibly succeed.
            False for terminal errors (test failures, syntax errors,
            permission denied). Drives respawn decision at
            coordinator.
        suggestion: CLI-surfaceable hint, e.g. "wait for 5h window
            reset (43m)". May be None.
        cost_usd: USD-equivalent cost for this worker's lifetime
            (forecast/record only, not the abort signal — see
            BudgetPolicy.subscription_window_*).
        tokens_in: Input tokens consumed.
        tokens_out: Output tokens generated.
        cache_read_tokens: Cache-read tokens (cheaper than fresh
            input — kept separate for accurate cost reconstruction).
        rate_limits: Snapshot of subscription window state at exit.
            Shape: {"five_hour": {"used_percentage": int,
            "resets_at": int (epoch)}, "seven_day": {...}}.
            None if Claude Code did not provide it (older harness).
        attempts: 1-indexed retry attempt number for this worker.
            First spawn = 1.
        metadata: Open dict for debug context (phase reached, last
            event seq, etc.). Not load-bearing; do not key on.

    Validates: status ∈ {completed, failed, paused}; if status ==
    completed then sha must be non-None and error_code must be None;
    if status != completed then error_code must be set.
    """

    schema_version: int
    wid: str
    status: str
    exit_code: int
    sha: str | None
    error_code: str | None
    recoverable: bool
    suggestion: str | None
    cost_usd: float
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    rate_limits: dict | None
    attempts: int
    metadata: dict[str, Any] = field(default_factory=dict)

    _ALLOWED_STATUSES = ("completed", "failed", "paused")

    def __post_init__(self):
        if self.status not in self._ALLOWED_STATUSES:
            raise ValueError(
                f"WorkerExitResult.status must be one of {self._ALLOWED_STATUSES}, "
                f"got {self.status!r}"
            )
        if self.status == "completed":
            if self.sha is None:
                raise ValueError("WorkerExitResult.sha required when status='completed'")
            if self.error_code is not None:
                raise ValueError(
                    "WorkerExitResult.error_code must be None when status='completed'"
                )
        else:
            if self.error_code is None:
                raise ValueError(
                    f"WorkerExitResult.error_code required when status={self.status!r}"
                )
        if self.attempts < 1:
            raise ValueError("WorkerExitResult.attempts must be >= 1")
        if self.cost_usd < 0:
            raise ValueError("WorkerExitResult.cost_usd must be >= 0")
        for tk in ("tokens_in", "tokens_out", "cache_read_tokens"):
            if getattr(self, tk) < 0:
                raise ValueError(f"WorkerExitResult.{tk} must be >= 0")
        if self.rate_limits is not None:
            for window in ("five_hour", "seven_day"):
                if window not in self.rate_limits:
                    raise ValueError(
                        f"WorkerExitResult.rate_limits missing window '{window}'"
                    )
                w = self.rate_limits[window]
                if not isinstance(w, dict):
                    raise ValueError(
                        f"WorkerExitResult.rate_limits['{window}'] must be a dict"
                    )
                for required in ("used_percentage", "resets_at"):
                    if required not in w:
                        raise ValueError(
                            f"WorkerExitResult.rate_limits['{window}'] missing '{required}'"
                        )
                if not isinstance(w["used_percentage"], int):
                    raise ValueError(
                        f"WorkerExitResult.rate_limits['{window}']['used_percentage'] must be int, got {type(w['used_percentage']).__name__}"
                    )
                if not isinstance(w["resets_at"], int):
                    raise ValueError(
                        f"WorkerExitResult.rate_limits['{window}']['resets_at'] must be int, got {type(w['resets_at']).__name__}"
                    )

    def to_envelope_dict(self) -> dict:
        """Serialize for `build-result.json` envelope. Coordinator dispatches
        on schema_version then loads via from_envelope_dict().

        Deep-copies metadata + rate_limits so envelope mutations don't alias
        back into the source instance (Opus concern #1)."""
        from copy import deepcopy
        return {
            "schema_version": self.schema_version,
            "wid": self.wid,
            "status": self.status,
            "exit_code": self.exit_code,
            "sha": self.sha,
            "error_code": self.error_code,
            "recoverable": self.recoverable,
            "suggestion": self.suggestion,
            "cost_usd": self.cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read_tokens": self.cache_read_tokens,
            "rate_limits": deepcopy(self.rate_limits),
            "attempts": self.attempts,
            "metadata": deepcopy(self.metadata),
        }

    @classmethod
    def from_envelope_dict(cls, d: dict) -> "WorkerExitResult":
        """Deserialize from `build-result.json`. Caller must dispatch on
        ``d['schema_version']`` first — this loader assumes v1 shape.
        Raises KeyError on missing required fields, ValueError on invalid
        values via __post_init__.

        Deep-copies metadata + rate_limits so envelope mutations don't alias
        into the loaded instance (Opus concern #1)."""
        from copy import deepcopy
        return cls(
            schema_version=d["schema_version"],
            wid=d["wid"],
            status=d["status"],
            exit_code=d["exit_code"],
            sha=d.get("sha"),
            error_code=d.get("error_code"),
            recoverable=d["recoverable"],
            suggestion=d.get("suggestion"),
            cost_usd=d["cost_usd"],
            tokens_in=d["tokens_in"],
            tokens_out=d["tokens_out"],
            cache_read_tokens=d["cache_read_tokens"],
            rate_limits=deepcopy(d.get("rate_limits")),
            attempts=d["attempts"],
            metadata=deepcopy(d.get("metadata", {})),
        )


@dataclass
class StepContract:
    """Contract for a single workflow step.

    Attributes:
        name: Step name
        execute: Callable that executes the step (takes WorkflowContext and previous output)
        required_inputs: List of input names this step requires
        outputs: List of output names this step produces
        skip_on_error: Whether to skip this step if previous step errored
    """

    name: str
    execute: Callable[[WorkflowContext, "StepResult | None"], StepResult]
    required_inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    skip_on_error: bool = False
    required_ctx_fields: list[str] = field(default_factory=list)
    resume_sentinel: bool = False


@dataclass
class WorkflowDefinition:
    """Definition of a complete workflow.

    Attributes:
        name: Workflow name
        steps: List of StepContract definitions in execution order
        error_handler: Optional callable to handle step errors
    """

    name: str
    steps: list[StepContract]
    error_handler: Callable[[StepResult, Any], StepResult] | None = None


@dataclass
class LoopStepContract:
    """Declarative loop primitive (E2440DB5).

    Mirrors Archon's loop-node schema (packages/workflows/src/schemas/loop.ts).
    One declarative loop replaces hand-rolled cycle logic across phase_45_spec_lite
    (cycle-2 spec rewrite), phase_5_implement (RED→GREEN retry), and phase_6_review
    (iteration). Termination order each iteration: until_marker → until_bash →
    max_iterations.

    Attributes:
        name: Loop identifier (used in events/logging).
        body: Inner steps executed per iteration (sequential, prev threaded
            within the iteration via the same engine convention).
        max_iterations: Hard cap. Loop returns ``terminated_by="max_iterations"``
            if neither marker nor bash trips first. ``0`` returns a skip
            StepResult immediately.
        until_marker: When set, loop terminates if the last body step's
            ``data[marker_field]`` (string-coerced) contains this substring.
            LLM-judged exit — emit the marker text from inside an LLM
            response.
        marker_field: Key in the last body StepResult.data to scan for
            ``until_marker``. Default ``raw_response`` (matches
            llm_subprocess convention).
        until_bash: When set, loop runs this argv after each iteration;
            exit code 0 terminates. Deterministic exit — more reliable
            than LLM-judged when a unit test or a green-build check
            already exists.
        fresh_context: When True, each iteration's first body step
            receives ``prev=None`` (clean restart). When False (default),
            iter-1 sees the caller's prev, iter-N (N≥2) sees the previous
            iteration's last body StepResult.
    """

    name: str
    body: list[StepContract]
    max_iterations: int = 3
    until_marker: str | None = None
    marker_field: str = "raw_response"
    until_bash: list[str] | None = None
    fresh_context: bool = False
    consume_recoverable_retry: bool = False
    """GH641: when True, a body-step recoverable retry
    (``status=='error' and recoverable and data['retry_from_step'] is not
    None``) is consumed as an internal LoopRunner iteration instead of being
    returned up to the outer ``WorkflowEngine._execute_steps``. The
    composite becomes the SOLE cycle driver — it never escalates recoverable
    upward. Default False preserves the legacy return-up behavior for all
    existing consumers (phase_5 validation loop, etc.)."""

    def __post_init__(self):
        if not isinstance(self.body, list) or not self.body:
            raise ValueError("LoopStepContract.body must be a non-empty list of StepContract")
        if self.max_iterations < 0:
            raise ValueError("LoopStepContract.max_iterations must be >= 0")


def format_boundary_error(*, phase: str, field: str, producer: str, where: str, schema: str) -> str:
    """Format a boundary error with phase/field/producer/where/schema for downstream LLM fix-agents (agreement 03192214). TS S3 callers append a 6th `reason` field separately because TS validation distinguishes missing vs null vs empty vs sentinel."""
    return f"boundary_error phase={phase} field={field} producer={producer} where={where} schema={schema}"


# ─── F576B3F7: declarative retry + timeout primitive ────────────────────────


@dataclass
class RetryPolicy:
    """Co-located retry policy for step() (F576B3F7).

    Cloudflare-Workflows-style declarative retry: callers configure
    max_retries + delay + backoff at the step factory call site instead
    of encoding retry semantics in markdown contracts.

    StepResult.recoverable is the fatal-vs-transient signal. recoverable=False
    short-circuits — no retry on terminal errors regardless of policy.

    Attributes:
        max_retries: Number of RETRIES after the first attempt.
            Total attempts = max_retries + 1. ``0`` (default) means run once.
        initial_delay_sec: Delay before retry #1.
        backoff: ``"none"`` (constant initial_delay), ``"linear"`` (delay × attempt),
            ``"exponential"`` (delay × 2^(attempt-1)).
        max_delay_sec: Cap on per-retry sleep. Default 60s.
    """
    max_retries: int = 0
    initial_delay_sec: float = 0.0
    backoff: str = "none"
    max_delay_sec: float = 60.0

    def __post_init__(self):
        if self.max_retries < 0:
            raise ValueError("RetryPolicy.max_retries must be >= 0")
        if self.backoff not in ("none", "linear", "exponential"):
            raise ValueError("RetryPolicy.backoff must be one of: none, linear, exponential")

    def delay_for_attempt(self, attempt: int) -> float:
        """Delay BEFORE the next retry. ``attempt`` is 1-indexed (the # of failures so far)."""
        if self.backoff == "none":
            d = self.initial_delay_sec
        elif self.backoff == "linear":
            d = self.initial_delay_sec * attempt
        else:  # exponential
            d = self.initial_delay_sec * (2 ** (attempt - 1))
        return min(d, self.max_delay_sec)


# ─── F2344E12 / 6F08F5F5: declarative cost-cap primitive ────────────────────


@dataclass(frozen=True)
class BudgetPolicy:
    """Co-located cost-cap policy for batch-build runs (F2344E12 CC-2 + CC-6 + FI-1, 6F08F5F5).

    Frozen-by-construction: dict fields are MappingProxyType-wrapped, so neither attribute reassignment nor dict mutation can bypass __post_init__ validation.

    Parallels RetryPolicy — caller declares budget at the run-construction
    call site rather than threading config through env vars or markdown.

    Subscription windows (5h rolling, 7d rolling) come from Claude Code
    hook input JSON (`rate_limits.{five_hour, seven_day}.{used_percentage,
    resets_at}` — see statusline.sh:21-26). Workers write rate_limits
    snapshots into the event log on phase boundaries; coordinator reads
    those snapshots and forecasts spawn decisions against this policy.

    USD caps remain for forecasting + record (post-API-switch future).
    Subscription windows are the active abort signal on Claude MAX
    subscription. Trip on EITHER mechanism aborts the spawn / batch.

    Attributes:
        max_cost_per_item_usd: Hard ceiling per item (cumulative across
            all retries when ``per_item_cumulative_cost=True``). ``None``
            disables the cap. Default ``None`` — defer to caller.
        max_cost_per_run_usd: Hard ceiling for the whole batch run.
            ``None`` disables. Default ``None``.
        per_chain_restart_cap: Restarts allowed within a single chain
            before chain marked ``failed``. Closes FI-1 (global cap
            ignores chain independence). Default 4.
        global_restart_cap: Run-level restart ceiling, second-line cap.
            Default 8.
        subscription_window_abort_pct: Per-window utilization %% above
            which spawn is BLOCKED. Default 80%% on both windows
            (matches statusline.sh red threshold).
        subscription_window_warn_pct: Per-window utilization %% above
            which a warning is emitted but spawn proceeds. Default 60%%.
        subscription_window_hard_ceiling_pct: Absolute ceiling above
            which even in-flight workers gracefully drain. Default 95%%.
        per_item_cumulative_cost: When True (default), per-item cost
            cap is summed across all retry attempts (CC-6). When False,
            cap is per-attempt — naively allows N×retries × per-attempt cap.

    Raises ValueError for: negative caps, malformed window dicts,
    abort_pct < warn_pct, hard_ceiling_pct < abort_pct.
    """

    max_cost_per_item_usd: float | None = None
    max_cost_per_run_usd: float | None = None
    per_chain_restart_cap: int = 4
    global_restart_cap: int = 8
    subscription_window_abort_pct: dict[str, int] = field(default_factory=lambda: {
        "five_hour": 80,
        "seven_day": 80,
    })
    subscription_window_warn_pct: dict[str, int] = field(default_factory=lambda: {
        "five_hour": 60,
        "seven_day": 60,
    })
    subscription_window_hard_ceiling_pct: int = 95
    per_item_cumulative_cost: bool = True

    def __post_init__(self):
        if self.max_cost_per_item_usd is not None and self.max_cost_per_item_usd < 0:
            raise ValueError("BudgetPolicy.max_cost_per_item_usd must be >= 0")
        if self.max_cost_per_run_usd is not None and self.max_cost_per_run_usd < 0:
            raise ValueError("BudgetPolicy.max_cost_per_run_usd must be >= 0")
        if self.per_chain_restart_cap < 0:
            raise ValueError("BudgetPolicy.per_chain_restart_cap must be >= 0")
        if self.global_restart_cap < 0:
            raise ValueError("BudgetPolicy.global_restart_cap must be >= 0")
        for window in ("five_hour", "seven_day"):
            if window not in self.subscription_window_abort_pct:
                raise ValueError(
                    f"BudgetPolicy.subscription_window_abort_pct missing window '{window}'"
                )
            if window not in self.subscription_window_warn_pct:
                raise ValueError(
                    f"BudgetPolicy.subscription_window_warn_pct missing window '{window}'"
                )
            abort = self.subscription_window_abort_pct[window]
            warn = self.subscription_window_warn_pct[window]
            if not (0 <= warn <= abort <= 100):
                raise ValueError(
                    f"BudgetPolicy window '{window}': require 0 <= warn ({warn}) "
                    f"<= abort ({abort}) <= 100"
                )
        if set(self.subscription_window_abort_pct) != set(self.subscription_window_warn_pct):
            raise ValueError(
                "BudgetPolicy: subscription_window_abort_pct and "
                "subscription_window_warn_pct must have identical key sets"
            )
        if not (self.subscription_window_hard_ceiling_pct >=
                max(self.subscription_window_abort_pct.values())):
            raise ValueError(
                "BudgetPolicy.subscription_window_hard_ceiling_pct must be >= "
                "max abort_pct across windows"
            )
        if self.subscription_window_hard_ceiling_pct > 100:
            raise ValueError(
                "BudgetPolicy.subscription_window_hard_ceiling_pct must be <= 100"
            )
        # Freeze mutable dict fields — frozen=True only protects attribute reassignment;
        # without MappingProxyType, callers can mutate the dicts post-construction and
        # bypass the validation above. Wrap a COPY so caller-held references can't bleed.
        object.__setattr__(
            self,
            "subscription_window_abort_pct",
            MappingProxyType(dict(self.subscription_window_abort_pct)),
        )
        object.__setattr__(
            self,
            "subscription_window_warn_pct",
            MappingProxyType(dict(self.subscription_window_warn_pct)),
        )

    def trip_for_window(self, used_pct: int, window: str) -> str | None:
        """Return ``"abort"``, ``"warn"``, ``"ceiling"``, or None for a given
        window's used_percentage. Caller dispatches behaviour."""
        if window not in self.subscription_window_abort_pct:
            raise ValueError(f"unknown window '{window}'")
        if used_pct >= self.subscription_window_hard_ceiling_pct:
            return "ceiling"
        if used_pct >= self.subscription_window_abort_pct[window]:
            return "abort"
        if used_pct >= self.subscription_window_warn_pct[window]:
            return "warn"
        return None

    def fits_per_item(self, projected_usd: float, cumulative_so_far_usd: float = 0.0) -> bool:
        """True if a projected attempt fits within the per-item budget.
        ``cumulative_so_far_usd`` is summed when ``per_item_cumulative_cost``."""
        if self.max_cost_per_item_usd is None:
            return True
        spend = (cumulative_so_far_usd + projected_usd) if self.per_item_cumulative_cost else projected_usd
        return spend <= self.max_cost_per_item_usd

    def fits_per_run(self, projected_usd: float, run_so_far_usd: float) -> bool:
        """True if a projected attempt fits within the per-run budget."""
        if self.max_cost_per_run_usd is None:
            return True
        return (run_so_far_usd + projected_usd) <= self.max_cost_per_run_usd


class _StepTimeout(Exception):
    """Raised internally when a step's signal alarm fires."""


@contextmanager
def _alarm(seconds: float | None):
    """SIGALRM-based timeout. POSIX only. When invoked off the main thread
    (e.g. the DBOS recovery thread), SIGALRM registration is unavailable;
    this fails open (no timeout enforcement) rather than raising, logging a
    warning instead. Restores prior handler on exit on the main thread."""
    if seconds is None or seconds <= 0:
        yield
        return

    if threading.current_thread() is not threading.main_thread():
        logger.warning(
            "step timeout enforcement skipped: SIGALRM unavailable off the main "
            "thread (DBOS recovery thread); step runs without timeout"
        )
        yield
        return

    def _handler(_signum, _frame):
        raise _StepTimeout()

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def step(
    name: str,
    fn: Callable[[WorkflowContext, "StepResult | None"], StepResult],
    *,
    retries: RetryPolicy | None = None,
    timeout_sec: float | None = None,
    skip_on_error: bool = False,
    required_ctx_fields: list[str] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> StepContract:
    """Factory wrapping ``fn`` with retry + timeout (F576B3F7).

    Pairs with LoopStepContract (E2440DB5): declarative cycles + declarative
    retries collapse hand-rolled state machines into composition.

    Returns a StepContract whose execute applies retry policy and (POSIX)
    SIGALRM-based timeout enforcement around the closure. ``sleep_fn``
    is injectable so tests don't burn wall-clock time on backoff.
    """
    def _execute(ctx: WorkflowContext, prev: Any) -> StepResult:
        max_attempts = (retries.max_retries + 1) if retries else 1
        attempt = 0
        last: StepResult | None = None

        while attempt < max_attempts:
            attempt += 1
            try:
                with _alarm(timeout_sec):
                    result = fn(ctx, prev)
            except _StepTimeout:
                result = StepResult(
                    status="error", data=None, duration_ms=int((timeout_sec or 0) * 1000),
                    step_name=name,
                    error=f"step {name!r} exceeded {timeout_sec}s timeout",
                    error_code="E_STEP_TIMEOUT", recoverable=True,
                )

            if result.status in ("ok", "skip", "escalate"):
                if attempt > 1:
                    return replace(
                        result,
                        metadata={**(result.metadata or {}), "attempts": attempt},
                    )
                return result

            last = result
            if not result.recoverable:
                return replace(
                    result,
                    metadata={**(result.metadata or {}), "attempts": attempt},
                )
            if attempt >= max_attempts:
                break
            if retries and retries.initial_delay_sec > 0:
                sleep_fn(retries.delay_for_attempt(attempt))

        # All attempts exhausted on recoverable errors
        assert last is not None  # invariant: loop runs at least once
        return replace(
            last,
            metadata={**(last.metadata or {}), "attempts": attempt},
        )

    return StepContract(
        name=name,
        execute=_execute,
        skip_on_error=skip_on_error,
        required_ctx_fields=required_ctx_fields or [],
    )
