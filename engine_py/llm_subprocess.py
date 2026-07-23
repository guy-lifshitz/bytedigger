"""Shared LLM-subprocess helper for opaque-LLM-step phases.

Extracted from phase_1_discovery + phase_4_architect after Stage 2.3 (rule of
three: phase_45_spec needs the same machinery twice for spec-writer + reviewer,
phase_5 will need it for RED/GREEN workers — extract before further duplication).

Single function: ``invoke_llm_subprocess`` runs a configurable subprocess with
the prompt fed via stdin and the response captured from stdout. Errors are
mapped to canonical ``error_code`` values that downstream tools (replay,
state derivation, hooks) can branch on.

Decree 2026-04-26 (category A): when telemetry_ctx has an active run, emits
``subprocess_spawned`` + ``subprocess_exited`` events around the spawn so the
engine event log captures lifecycle + tokens + cost for /build introspection.

23680DDA migration (2026-05-06): for ``claude_p`` callers without a
caller-supplied ``--output-format`` flag, auto-injection now emits
``--output-format stream-json --verbose`` instead of the bare ``--output-format
json``. Reasoning (cheap-repro postmortem 2026-05-06): bare ``json`` is
NON-streaming — claude CLI buffers the whole response before flushing — so
``proc.communicate(...)`` blocks at EOF and a 15min hang manifests as silence
on stdout. With stream-json, each NDJSON line is one event; the streaming
read loop iterates ``proc.stdout`` line-by-line and finds the LAST
``type:"result"`` event for raw_response + cost + tokens. Timeout is enforced
via a watchdog deadline (no more communicate(timeout=...)).

NOT a framework. The caller owns:
    - prompt assembly (per-phase)
    - StepResult.data field augmentation via ``extra_data``
    - the surrounding StepContract wiring
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
import subprocess
import threading
import time
import typing
import uuid  # 4C03CCED Ship 1B: request_nonce generation for in-session file-protocol
from pathlib import Path

from contracts import StepResult
from package_meta import EXTRA_AGENTIC_PYDANTIC, install_hint
import telemetry_ctx
from telemetry_ctx import _RunCtx
import config_provider
from config_provider import hal_root as _hal_root_fn
from lib.timeout_policy import classify_timeout_state, resolve_active_threshold_bytes
from lib.llm_provider import get_provider  # GH451: provider seam (build_argv/parse_result/rank/family/floor)
import lib.model_config as _model_config
from lib.env_limit import detect_env_limit

try:
    from lib.observability.emit_resolver import emit_resolver_resolved
except Exception:  # noqa: BLE001
    def emit_resolver_resolved(helper, source, value, extra=None):  # type: ignore[misc]
        pass

logger = logging.getLogger(__name__)

_TAIL_BYTES = 2048

# 4C03CCED Ship 1A: runner backend selector constants.
# 68E964FB: _KNOWN_BACKENDS now derives from _BACKENDS registry (§1g single source).
# _BACKENDS and _KNOWN_BACKENDS are defined below, after the handler defs.
_DEFAULT_BACKEND = "agent-sdk"
_BACKEND_ENV_VAR = "HAL_RUNNER_BACKEND"
_GATE_FLOOR_ENV_VAR = "HAL_GATE_MODEL_FLOOR"

# 4C03CCED Ship 1C: backend → manifest-source capability map.
# A backend registered here can produce a worker_written_paths manifest sourced
# from a producer the worker cannot forge (harness tool-call record OR
# orchestrator-observed file mutation). Backends ABSENT from this map fail at
# capability-probe (E_LLM_BACKEND_NO_MANIFEST) — fail-closed at registration,
# never fail-open at commit-time. F9F7E4FD invariant generalized off claude -p.
_BACKEND_MANIFEST_SOURCE: dict[str, str] = {
    "claude-subprocess": "harness_tool_record",
    "claude-in-session": "orchestrator_observed",
}

_ALLOWED_MANIFEST_SOURCES: frozenset[str] = frozenset(_BACKEND_MANIFEST_SOURCE.values())

# 4C03CCED Ship 1D: per-backend watchdog capability registry. Parallels
# _BACKEND_MANIFEST_SOURCE — single source-of-truth for "does backend X
# support capability Y". Fail-closed: backend not in dict for capability Y
# means capability Y unavailable; probe rejects watchdog requests.
_BACKEND_CAPABILITIES: dict[str, frozenset[str]] = {
    "claude-subprocess": frozenset({"manifest", "progress_since", "abort"}),
    "claude-in-session": frozenset({"manifest"}),
}

# Sentinel for distinguishing absent-key from None-value (both rejected, but
# the error message benefits from distinguishing internally during dev).
_MANIFEST_SENTINEL = object()


class _ManifestError(ValueError):
    """Base class for manifest contract violations."""


class _ManifestMissingError(_ManifestError):
    """Required manifest field absent or None."""


class _ManifestMalformedError(_ManifestError):
    """Manifest field present but wrong type or invalid element type."""


class _ManifestInvalidSourceError(_ManifestError):
    """manifest_source value not in allowed enum."""


def prev_data_corruption_reason(prev: typing.Any) -> typing.Optional[str]:
    """Return a reason string if prev.data is a non-None non-dict value, else None."""
    data = getattr(prev, "data", None)
    if data is not None and not isinstance(data, dict):
        return f"prev.data is {type(data).__name__}, expected dict or None"
    return None


# ---------------------------------------------------------------------------
# F5787804: in-session per-step cost helpers (§2.2)
# ---------------------------------------------------------------------------

def _load_pricing_table() -> dict:
    """Read the HAL models config → claude.pricing → {alias: (in_rate, out_rate)}.

    Best-effort: any read/parse error returns {} (never raises into build path).
    Path resolved via config_provider.models_config_path() (§1g).
    """
    try:
        models_path = config_provider.models_config_path()
        with open(models_path, encoding="utf-8") as f:
            raw = json.load(f)
        pricing = raw.get("claude", {}).get("pricing", {})
        table: dict = {}
        for alias, rates in pricing.items():
            if isinstance(rates, dict):
                in_rate = rates.get("in")
                out_rate = rates.get("out")
                if isinstance(in_rate, (int, float)) and isinstance(out_rate, (int, float)):
                    table[alias] = (float(in_rate), float(out_rate))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pricing-table load failed (%s) — cost telemetry will be $0", exc
        )
        return {}
    if not table:
        logger.warning(
            "pricing-table loaded but empty (no valid claude.pricing entries)"
            " — cost telemetry will be $0"
        )
    return table


def _load_effort(
    model: "str | None" = None, step_name: "str | None" = None
) -> "str | None":
    """Read HAL models config → claude.effort → reasoning-effort level, or None.

    claude.effort may be either:
      - a plain non-empty str (legacy 32ED59E2 shape): a global default applied
        regardless of model/step_name — preserved for back-compat.
      - a dict with "by_phase" and/or "by_model" sub-maps: resolution order is
        by_phase[step_name] (if step_name given and value is a truthy str) →
        by_model[_model_family(model)] (if model's family is known and value is
        a truthy str) → None.

    Best-effort: any read/parse error, or a missing/empty/non-str/non-dict value,
    or an unrecognized model family, returns None (never raises into the build
    path). Path resolved via config_provider.models_config_path() — the same
    host-neutral seam as _load_pricing_table (§1g / core-boundary). When None,
    the argv is byte-identical to before (inert-by-default): _apply_effort is a
    no-op.
    """
    try:
        models_path = config_provider.models_config_path()
        with open(models_path, encoding="utf-8") as f:
            raw = json.load(f)
        effort = raw.get("claude", {}).get("effort")
        if isinstance(effort, str) and effort:
            return effort
        if isinstance(effort, dict):
            by_phase = effort.get("by_phase") or {}
            if step_name and isinstance(by_phase.get(step_name), str) and by_phase.get(step_name):
                return by_phase.get(step_name)
            by_model = effort.get("by_model") or {}
            fam = _model_family(model)
            if fam and isinstance(by_model.get(fam), str) and by_model.get(fam):
                return by_model.get(fam)
            return None
        return None
    except Exception:  # noqa: BLE001
        return None


def _load_effort_gate(model: "str | None" = None) -> "str | None":
    """Read HAL models config → claude.effort.by_model[family] → effort pin, or None.

    GH439: an explicit per-model effort pin overrides the hard_gate exemption
    (32ED59E2 comment at the call-site) — a hard-gated (e.g. Opus validation)
    spawn normally keeps CLI-default effort, but an operator-set by_model pin
    for that model's family is honored even on the gated path. Only the
    by_model sub-map is consulted (by_phase is ignored here — it targets
    non-gate delegations via _load_effort). The legacy global-str claude.effort
    shape (32ED59E2) is NOT a per-model pin and returns None here.

    Best-effort: any read/parse error, missing/non-dict claude.effort, missing
    by_model, unrecognized model family, or non-str/empty value returns None
    (never raises into the build path). Path resolved via
    config_provider.models_config_path() — the same host-neutral seam as
    _load_effort/_load_pricing_table (§1g / core-boundary). When None, the
    argv is byte-identical to before (inert-by-default): _apply_effort is a
    no-op.
    """
    try:
        models_path = config_provider.models_config_path()
        with open(models_path, encoding="utf-8") as f:
            raw = json.load(f)
        effort = raw.get("claude", {}).get("effort")
        if not isinstance(effort, dict):
            return None
        by_model = effort.get("by_model") or {}
        fam = _model_family(model)
        if fam and isinstance(by_model.get(fam), str) and by_model.get(fam):
            return by_model.get(fam)
        return None
    except Exception:  # noqa: BLE001
        return None


def _apply_effort(command: "list[str]", effort: "str | None") -> "list[str]":
    """Append ['--effort', <level>] to a claude headless argv when effort is set.

    effort is a truthy str → return command + ['--effort', effort]; else return
    command unchanged (no-op). Pure (no I/O) so it is unit-testable in isolation.
    """
    if isinstance(effort, str) and effort:
        return command + ["--effort", effort]
    return command


def _load_tier_model(tier: "str | None") -> "str | None":
    """Read HAL models config → claude.model_by_tier[TIER] → model name, or None.

    Mirrors _load_effort's best-effort semantics exactly: tier key is
    uppercase-normalized; any read/parse error, missing key, non-dict
    model_by_tier, or non-str/empty value returns None (never raises into
    the build path). GH375.
    """
    if not tier:
        return None
    try:
        models_path = config_provider.models_config_path()
        with open(models_path, encoding="utf-8") as f:
            raw = json.load(f)
        by_tier = raw.get("claude", {}).get("model_by_tier")
        if not isinstance(by_tier, dict):
            return None
        val = by_tier.get(str(tier).upper())
        return val if isinstance(val, str) and val else None
    except Exception:  # noqa: BLE001
        return None


# GH375: tier override must never upgrade a cheaper caller-pinned model
# (haiku-pinned SIMPLE RED/GREEN stay haiku). _MODEL_RANK keys MUST stay in
# sync with _model_family's recognized families; both live in this module so
# the coupling is file-local. GH426: fable ranks above opus (top-tier model).
# GH451: sourced from the claude ProviderSpec — same mapping, now provider-owned.
_MODEL_RANK = get_provider("claude").model_rank


def _tier_model_is_downgrade(tier_model: "str", pinned_model: "str") -> bool:
    """True iff tier_model is strictly cheaper than pinned_model (known families only)."""
    tf, pf = _model_family(tier_model), _model_family(pinned_model)
    if tf is None or pf is None:
        return False
    tr = _MODEL_RANK.get(tf)
    pr = _MODEL_RANK.get(pf)
    if tr is None or pr is None:
        return False
    return tr < pr


def _price_for_model(
    model: "str | None", table: "dict | None" = None
) -> "tuple[float, float] | None":
    """Return (in_rate, out_rate) per MTok for *model*, or None if absent.

    *model* is normalized to lowercase. Dispatched alias (e.g. "opus") is
    looked up directly; a full id (e.g. "claude-opus-4-8") is mapped by
    substring to opus/sonnet/haiku before lookup.
    *table* is injectable for tests (defaults to _load_pricing_table()).
    """
    if not model:
        return None
    if table is None:
        table = _load_pricing_table()
    normalized = model.lower() if model else ""
    # Direct alias lookup first (dispatched_model is already an alias)
    if normalized in table:
        return table[normalized]
    # Substring fallback for full model IDs
    for alias in ("fable", "opus", "sonnet", "haiku"):
        if alias in normalized and alias in table:
            return table[alias]
    return None


def _derive_cost_usd(
    tokens_in: "int | float | None",
    tokens_out: "int | float | None",
    model: "str | None",
    table: "dict | None" = None,
) -> "float | None":
    """Derive metered-equivalent USD cost from token counts and model alias.

    Returns None when:
      - model is not in the pricing table (no rate available)
      - BOTH token sides are non-numeric (no counterfactual basis)

    Non-numeric sides are treated as 0 (conservative; mirrors subprocess
    _cost_non_numeric_string_type_guard). bool is excluded (isinstance(True,int)
    is True in Python — explicit exclusion matches spec §2.2).
    """
    rates = _price_for_model(model, table)
    if rates is None:
        return None
    in_rate, out_rate = rates

    def _coerce(v: object) -> "float | None":
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        return None

    ti = _coerce(tokens_in)
    to = _coerce(tokens_out)
    if ti is None and to is None:
        return None
    return ((ti or 0.0) * in_rate + (to or 0.0) * out_rate) / 1_000_000.0


def _validate_manifest_or_raise(result_obj: dict, backend: str) -> "tuple[list[str], str]":
    """Strict validator for runner-result manifest schema (4C03CCED Ship 1C G1).

    Returns (paths, source) on success. Raises ValueError-subclasses for each
    distinct rejection class — caller maps to E_LLM_MANIFEST_* code.

    Schema (REQUIRED, no defaults):
      - worker_written_paths: list[str] (deduped, repo-relative; empty list OK
        if worker genuinely wrote nothing — semantically distinct from absent)
      - manifest_source: str in _ALLOWED_MANIFEST_SOURCES

    The worker_self_report value is structurally rejected — F9F7E4FD anti-pattern
    is unrepresentable in the schema (G1-AC2).
    """
    paths_raw = result_obj.get("worker_written_paths", _MANIFEST_SENTINEL)
    if paths_raw is _MANIFEST_SENTINEL or paths_raw is None:
        raise _ManifestMissingError(
            f"runner-result missing required field 'worker_written_paths' (backend={backend})"
        )
    if not isinstance(paths_raw, list):
        raise _ManifestMalformedError(
            f"worker_written_paths must be list[str], got {type(paths_raw).__name__} (backend={backend})"
        )
    for i, p in enumerate(paths_raw):
        if not isinstance(p, str):
            raise _ManifestMalformedError(
                f"worker_written_paths[{i}] must be str, got {type(p).__name__} (backend={backend})"
            )

    source = result_obj.get("manifest_source", _MANIFEST_SENTINEL)
    if source is _MANIFEST_SENTINEL or source is None:
        raise _ManifestMissingError(
            f"runner-result missing required field 'manifest_source' (backend={backend})"
        )
    if source not in _ALLOWED_MANIFEST_SOURCES:
        raise _ManifestInvalidSourceError(
            f"manifest_source must be one of {sorted(_ALLOWED_MANIFEST_SOURCES)}, "
            f"got {source!r} (backend={backend})"
        )

    return list(paths_raw), str(source)


def _assert_backend_supports_manifest(backend: str) -> "StepResult | None":
    """Capability-probe gate (4C03CCED Ship 1C G1-AC5). Returns an error
    StepResult to be propagated, or None if the backend is OK.

    Backends registered in _KNOWN_BACKENDS but absent from
    _BACKEND_MANIFEST_SOURCE fail with E_LLM_BACKEND_NO_MANIFEST
    (recoverable=False — config error, retry won't help).

    Unknown backends (not in _KNOWN_BACKENDS) are out of scope here; caller's
    existing backend-validation handles them with E_LLM_BACKEND_UNKNOWN.
    """
    if backend not in _BACKEND_MANIFEST_SOURCE:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="invoke_llm_subprocess",
            error=(
                f"backend {backend!r} cannot produce a worker_written_paths manifest "
                f"sourced from a non-worker producer; commit-step would have nothing to "
                f"gate on. Fail-closed at registration per 4C03CCED Ship 1C G1-AC5."
            ),
            error_code="E_LLM_BACKEND_NO_MANIFEST",
            recoverable=False,
        )
    return None


def _assert_backend_supports_watchdog(
    backend: str,
    *,
    idle_enabled: bool,
    straggler_enabled: bool,
) -> "StepResult | None":
    if not idle_enabled and not straggler_enabled:
        return None
    caps = _BACKEND_CAPABILITIES.get(backend, frozenset())
    missing: list[str] = []
    if idle_enabled and "progress_since" not in caps:
        missing.append("progress_since")
    if straggler_enabled and "abort" not in caps:
        missing.append("abort")
    if not missing:
        return None
    return StepResult(
        status="error",
        data=None,
        duration_ms=0,
        step_name="invoke_llm_subprocess",
        error=f"backend {backend!r} does not support watchdog capabilities: {sorted(missing)}",
        error_code="E_LLM_WATCHDOG_UNSUPPORTED",
        recoverable=False,
    )


def manifest_from_result(prev: "StepResult") -> "tuple[list[str], str]":
    """Canonical consumer-side accessor for worker_written_paths + manifest_source
    (4C03CCED Ship 1C G1-AC3). Replaces every
    `(prev.data or {}).get("worker_written_paths") or []` pattern at
    commit / pytest-scope sites.

    Raises ValueError if prev.data is None or required fields are absent/malformed
    — indicates producer-consumer contract violation (producer side was supposed to
    validate via _validate_manifest_or_raise BEFORE returning). Callers wrap and
    map to E_LLM_MANIFEST_MISSING_AT_CONSUMER. A non-dict prev.data (any step may
    return a str/list/int) likewise raises _ManifestMissingError rather than an
    AttributeError from the validator — GH780.

    Returns (paths, source) — paths is a new list (caller may mutate), source is
    one of _ALLOWED_MANIFEST_SOURCES.
    """
    if prev.data is None:
        raise _ManifestMissingError(
            "prev.data is None; manifest accessor requires populated StepResult"
        )
    if not isinstance(prev.data, dict):
        raise _ManifestMissingError(
            f"prev.data is {type(prev.data).__name__}, not a dict; "
            "manifest accessor requires a mapping StepResult.data"
        )
    return _validate_manifest_or_raise(prev.data, backend="<consumer-side>")


# 4C03CCED Ship 1B: G3 file-protocol constants for claude-in-session backend.
_REQUEST_DIR_ENV_VAR = "HAL_RUNNER_REQUEST_DIR"
_INTEGRITY_MARKER_KEY = "__hal_integrity"
_INTEGRITY_MARKER_VALUE = "end"
_POLL_INTERVAL_SEC = 0.05  # bounded-poll wait granularity

# 775D6752: in-stream idle-timeout watchdog default. Disabled by default (None);
# pass ``idle_timeout_sec=N`` to enable per-call. If no NDJSON event arrives
# within N seconds on the stream-json path, kill the subprocess with
# ``E_LLM_NO_PROGRESS`` (recoverable=True). Mirrors green_watchdog's role for
# fix_llm — saves the wasted 14min next time something legitimately hangs while
# preserving the outer ``timeout_sec`` ceiling. Disable per-call by passing
# ``idle_timeout_sec=0`` or ``idle_timeout_sec=None``.
_DEFAULT_IDLE_TIMEOUT_SEC = None
POST_RESULT_WAIT_SEC = 5  # F4F26513/3C54A029: grace + post-kill reap timeout (seconds)

# CCBB65DC: straggler-abort watchdog defaults (opt-in via straggler_cfg kwarg).
STRAGGLER_PATIENCE_SEC = 60       # seconds the N-1 condition may persist before abort
STRAGGLER_POLL_INTERVAL_SEC = 5   # how often the watchdog scans the reviews dir
STRAGGLER_KILL_GRACE_SEC = 5      # SIGTERM → SIGKILL grace


class _StragglerWatchdog:
    """Module-private background watchdog for phase_6 composite-reviewer straggler abort.

    CCBB65DC: polls ``reviews_dir / role-*.md`` while the outer reviewer subprocess
    runs. Once N-1 of N role files exist AND ``patience_sec`` elapses since that
    condition was first observed → terminates then kills the subprocess.
    Strictly opt-in: constructed only when ``straggler_cfg`` is provided to
    ``invoke_llm_subprocess``. Daemon thread; never joined on the hot path.
    """

    def __init__(
        self,
        *,
        proc,
        reviews_dir,
        expected_n: int,
        patience_sec: float,
        poll_interval_sec: float,
        kill_grace_sec: float = STRAGGLER_KILL_GRACE_SEC,
    ) -> None:
        self.proc = proc
        self.reviews_dir = Path(reviews_dir)
        self.expected_n = int(expected_n)
        self.patience_sec = float(patience_sec)
        self.poll_interval_sec = float(poll_interval_sec)
        self.kill_grace_sec = float(kill_grace_sec)
        self.aborted: bool = False
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._timer_started: float | None = None

    def run(self) -> None:
        """Thread target: poll loop until subprocess exits, all files appear, or patience elapses."""
        while True:
            if self._stop_evt.is_set():
                return
            try:
                if self.proc.poll() is not None:
                    return
                try:
                    if self.reviews_dir.is_dir():
                        n = len(list(self.reviews_dir.glob("role-*.md")))
                    else:
                        n = 0
                except Exception:
                    n = 0
                if self._timer_started is None:
                    # WAITING_FOR_N_MINUS_1 state
                    if n >= self.expected_n:
                        return  # all done, no straggler
                    elif n >= self.expected_n - 1:
                        self._timer_started = time.monotonic()
                    # else: below N-1, keep waiting
                else:
                    # STRAGGLER_TIMER_RUNNING state
                    if n >= self.expected_n:
                        return  # straggler finished on its own
                    elif time.monotonic() - self._timer_started >= self.patience_sec:
                        # Abort: SIGTERM then SIGKILL after grace
                        self.proc.terminate()
                        try:
                            self.proc.wait(timeout=self.kill_grace_sec)
                        except Exception:
                            pass
                        try:
                            self.proc.kill()
                        except Exception:
                            pass
                        self.aborted = True
                        return
            except Exception:
                logger.warning("straggler watchdog poll failed", exc_info=True)
            self._stop_evt.wait(self.poll_interval_sec)

    def start(self) -> None:
        """Start the background watchdog daemon thread."""
        self._thread = threading.Thread(
            target=self.run,
            name="llm-straggler-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the watchdog to stop (non-blocking)."""
        self._stop_evt.set()


def _resolve_backend(
    kwarg: str | None,
    env: "Mapping[str, str]",
) -> "tuple[str, str]":
    """Resolve the runner backend from kwarg > env > default precedence.

    Returns ``(resolved_backend, source)`` where source is one of
    ``"kwarg"``, ``"env"``, or ``"default"``.

    4C03CCED Ship 1A: selector-only; dispatch branching lives in Ship 1B+.
    """
    if kwarg is not None:
        stripped = kwarg.strip()
        if stripped:
            return (stripped, "kwarg")
        # empty after strip → fall through to env
    env_val = env.get(_BACKEND_ENV_VAR)
    if env_val is not None:
        stripped_env = env_val.strip()
        if stripped_env:
            return (stripped_env, "env")
        # empty after strip → fall through to default
    return (_DEFAULT_BACKEND, "default")


# 4C03CCED Ship 1B: G3 file-protocol helpers for claude-in-session backend.

def _resolve_request_dir(env: "Mapping[str, str]") -> "tuple[str, str]":
    """Resolve runner-request artifact directory (§1g single-source).

    Returns ``(resolved_dir, source)`` where source is ``"env"`` or ``"default"``.
    """
    env_val = env.get(_REQUEST_DIR_ENV_VAR)
    if env_val is not None:
        stripped = env_val.strip()
        if stripped:
            return (os.path.expanduser(stripped), "env")
    return (str(_hal_root_fn() / "SHARED" / "state" / "runner-requests"), "default")


def _atomic_write_json(path: str, payload: dict) -> None:
    """Atomic publish per G3-AC3: tmp + fsync + rename.

    Writes ``payload`` (including integrity marker) to ``<path>.tmp``,
    fsyncs, then ``os.rename`` to ``path``. Reader observing ``path``
    either sees nothing or the complete file (POSIX rename atomicity).
    """
    payload_with_marker = dict(payload)
    payload_with_marker[_INTEGRITY_MARKER_KEY] = _INTEGRITY_MARKER_VALUE
    tmp = f"{path}.tmp"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload_with_marker, f)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


def _read_result_artifact(path: str, expected_nonce: str) -> "dict | None":
    """Read + validate runner-result artifact at ``path``.

    Returns parsed JSON dict iff:
      - file exists AND is parseable JSON
      - JSON carries ``__hal_integrity == "end"`` (torn-write defense, G3-AC3)
      - JSON ``request_nonce`` field == ``expected_nonce`` (stale rejection, G3-AC2)

    Returns ``None`` on ANY rejection (treated as absent — caller continues polling).
    Never raises (any exception swallowed → treated as absent).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get(_INTEGRITY_MARKER_KEY) != _INTEGRITY_MARKER_VALUE:
        return None  # torn write — keep polling
    if obj.get("request_nonce") != expected_nonce:
        return None  # stale artifact — keep polling
    return obj


def _build_claude_argv(model: str) -> list[str]:
    """Build the base Claude headless argv from a model name.
    The vendor-form ['claude','-p','--model',model] is now backend-internal.
    GH451: delegates to the registered provider (lib.llm_provider.get_provider)."""
    return get_provider().build_argv(model)


def _invoke_in_session(
    *,
    prompt: str,
    model: str,
    timeout_sec: int,
    step_name: str,
    extra_data: "dict | None",
    allowed_tools: "list[str] | None",
    run_ctx: "_RunCtx | None",
    hard_gate: bool = False,
    gate_label: "str | None" = None,
    straggler_cfg: "dict | None" = None,
    idle_timeout_sec: "int | float | None" = None,
    stable_prefix: str = "",
) -> StepResult:
    """In-session dispatch path (4C03CCED Ship 1B — G3 file-protocol).

    Called from ``invoke_llm_subprocess`` when ``resolved_backend == "claude-in-session"``.
    Returns a ``StepResult`` mirroring the claude-subprocess success shape.
    """
    started_monotonic = time.monotonic()

    # G3-AC1 single-source: run_id MUST come from active RunContext, never fallback.
    if run_ctx is None or not run_ctx.run_id:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=(run_ctx.step_name if run_ctx is not None else None) or step_name,
            error="claude-in-session requires an active RunContext with non-empty run_id",
            error_code="E_LLM_RUN_ID_MISSING",
            recoverable=False,
        )

    request_dir, dir_source = _resolve_request_dir(config_provider.env_mapping())
    emit_resolver_resolved(
        "runner_request_dir",
        dir_source,
        request_dir,
        {"backend": "claude-in-session"},
    )

    phase_segment = run_ctx.phase if run_ctx.phase else "_default"
    nonce = uuid.uuid4().hex
    nonce_dir = os.path.join(request_dir, run_ctx.run_id, phase_segment)
    request_path = os.path.join(nonce_dir, f"{nonce}.req.json")
    result_path = os.path.join(nonce_dir, f"{nonce}.res.json")

    # G3-AC4 pre-wait stale sweep — defense-in-depth atop nonce isolation.
    try:
        if os.path.exists(result_path):
            os.unlink(result_path)
    except OSError:
        pass  # best-effort; nonce-isolation is the real guarantee

    prompt_size = len(prompt.encode("utf-8"))

    # Write request artifact (atomic publish per G3-AC3).
    request_payload = {
        "request_nonce": nonce,
        "prompt": prompt,
        "step_name": step_name,
        "allowed_tools": list(allowed_tools) if allowed_tools is not None else None,
        "model": model,
        "cwd": os.getcwd(),
        "timeout_sec": timeout_sec,
        "phase": run_ctx.phase,
        "run_id": run_ctx.run_id,
    }
    # GH334 §2.0/§2.3: non-splitting adapter — prompt stays FULL/unchanged
    # always; stable_prefix is an ADDITIVE inert hint, added only when set.
    if stable_prefix:
        request_payload["stable_prefix"] = stable_prefix
    _atomic_write_json(request_path, request_payload)

    # Telemetry: runner_request_built (analog of subprocess_spawned, no pid).
    # Emitted BEFORE poll loop (A-MED-3: must precede runner_result_consumed).
    if run_ctx is not None and run_ctx.event_log is not None:
        _emit_safe(run_ctx.event_log, "runner_request_built", {
            "backend": "claude-in-session",
            "request_nonce": nonce,
            "request_path": request_path,
            "model": model,
            "prompt_size_bytes": prompt_size,
            "phase": run_ctx.phase,
            "step_name": run_ctx.step_name or step_name,
            "parent_skill": config_provider.env_opt("HAL_PARENT_SKILL"),
        }, run_ctx.run_id)

    # Bounded-poll wait for result artifact (G3-AC5 file-existence-only).
    deadline = started_monotonic + max(0.0, float(timeout_sec))
    result_obj = None
    while time.monotonic() < deadline:
        result_obj = _read_result_artifact(result_path, nonce)
        if result_obj is not None:
            break
        time.sleep(_POLL_INTERVAL_SEC)

    duration_ms = int((time.monotonic() - started_monotonic) * 1000)

    if result_obj is None:
        # G3-AC6: timeout → E_LLM_NO_RESULT_EVENT (taxonomy reuse, no new code).
        if run_ctx is not None and run_ctx.event_log is not None:
            _emit_safe(run_ctx.event_log, "runner_result_consumed", {
                "backend": "claude-in-session",
                "request_nonce": nonce,
                "result_artifact_path": result_path,
                "duration_ms": duration_ms,
                "outcome": "timeout",
                "phase": run_ctx.phase,
                "step_name": run_ctx.step_name or step_name,
            }, run_ctx.run_id)
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path, "duration_ms": duration_ms},
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"runner-result artifact absent within timeout ({timeout_sec}s, nonce={nonce})",
            error_code="E_LLM_NO_RESULT_EVENT",
            recoverable=True,
        )

    # Validate result shape — same subtype/result discipline as stream-json path.
    subtype = result_obj.get("subtype")
    if not isinstance(subtype, str):
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path},
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"runner-result missing/non-string subtype={subtype!r}",
            error_code="E_LLM_RESULT_MALFORMED",
            recoverable=True,
        )
    if subtype != "success":
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path, "subtype": subtype},
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"runner-result signaled error subtype={subtype!r}",
            error_code="E_LLM_RESULT_ERROR",
            recoverable=True,
        )
    raw_response = result_obj.get("raw_response")
    if not isinstance(raw_response, str) or not raw_response.strip():
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path},
            duration_ms=duration_ms,
            step_name=step_name,
            error="runner-result raw_response missing/empty/whitespace",
            error_code="E_LLM_RESULT_MALFORMED",
            recoverable=True,
        )

    # Build success StepResult mirroring claude-subprocess shape.
    # 4C03CCED Ship 1C G1-AC1+G1-AC2: strict manifest schema validation.
    try:
        manifest_paths, manifest_source = _validate_manifest_or_raise(
            result_obj, backend="claude-in-session"
        )
    except _ManifestMissingError as e:
        if run_ctx is not None and run_ctx.event_log is not None:
            _emit_safe(run_ctx.event_log, "manifest_validation_rejected", {
                "backend": "claude-in-session",
                "reason": "missing_field",
                "detail": str(e),
            }, run_ctx.run_id)
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path},
            duration_ms=duration_ms,
            step_name=step_name,
            error=str(e),
            error_code="E_LLM_MANIFEST_MISSING",
            recoverable=True,
        )
    except _ManifestMalformedError as e:
        if run_ctx is not None and run_ctx.event_log is not None:
            _emit_safe(run_ctx.event_log, "manifest_validation_rejected", {
                "backend": "claude-in-session",
                "reason": "malformed",
                "detail": str(e),
            }, run_ctx.run_id)
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path},
            duration_ms=duration_ms,
            step_name=step_name,
            error=str(e),
            error_code="E_LLM_MANIFEST_MALFORMED",
            recoverable=True,
        )
    except _ManifestInvalidSourceError as e:
        if run_ctx is not None and run_ctx.event_log is not None:
            _emit_safe(run_ctx.event_log, "manifest_validation_rejected", {
                "backend": "claude-in-session",
                "reason": "invalid_source",
                "detail": str(e),
            }, run_ctx.run_id)
        return StepResult(
            status="error",
            data={"request_nonce": nonce, "result_path": result_path},
            duration_ms=duration_ms,
            step_name=step_name,
            error=str(e),
            error_code="E_LLM_MANIFEST_INVALID_SOURCE",
            recoverable=True,
        )

    # 02FF48F4: in-session hard-gate — verify servicer dispatched Opus for hard-gate steps.
    gate_res = _assert_in_session_model_or_downgrade(result_obj, model, hard_gate, step_name, nonce)
    if gate_res is not None:
        return gate_res

    # GH#222 (220E5F63): warn-only model-pin drift for non-hard-gate in-session steps.
    drift = _detect_nonhardgate_model_drift(result_obj, model, hard_gate, step_name)
    if drift is not None and run_ctx is not None and run_ctx.event_log is not None:
        _emit_safe(run_ctx.event_log, "model_pin_mismatch",
                   {**drift, "phase": run_ctx.phase, "severity": "warning"}, run_ctx.run_id)

    # F5787804: derive metered-equivalent cost (best-effort; None when tokens absent).
    cost_usd = _derive_cost_usd(
        result_obj.get("tokens_in"),
        result_obj.get("tokens_out"),
        result_obj.get("dispatched_model") or model,
    )

    data: dict = {
        "raw_response": raw_response,
        "response_bytes": len(raw_response.encode("utf-8")),
        "model": model,
        "tokens_out": result_obj.get("tokens_out"),
        "tokens_in": result_obj.get("tokens_in"),
        "cost_usd": cost_usd,
        "worker_written_paths": manifest_paths,
        "manifest_source": manifest_source,
    }
    if extra_data:
        data.update(extra_data)

    if run_ctx is not None and run_ctx.event_log is not None:
        _emit_safe(run_ctx.event_log, "runner_result_consumed", {
            "backend": "claude-in-session",
            "request_nonce": nonce,
            "result_artifact_path": result_path,
            "duration_ms": duration_ms,
            "outcome": "success",
            "response_size_bytes": len(raw_response.encode("utf-8")),
            "tokens_in": result_obj.get("tokens_in"),
            "tokens_out": result_obj.get("tokens_out"),
            "cost_usd": cost_usd,
            "model": result_obj.get("dispatched_model") or model,
            "phase": run_ctx.phase,
            "step_name": run_ctx.step_name or step_name,
        }, run_ctx.run_id)

    # G3-AC7 cleanup hygiene: best-effort unlink of consumed artifacts.
    # Nonce isolation (G3-AC2) is the correctness guarantee; cleanup is hygiene.
    try:
        if os.path.exists(result_path):
            os.unlink(result_path)
        if os.path.exists(request_path):
            os.unlink(request_path)
    except OSError:
        pass

    return StepResult(status="ok", data=data, duration_ms=duration_ms, step_name=step_name)


def invoke_llm_subprocess(
    *,
    prompt: str,
    model: str,
    timeout_sec: int,
    step_name: str,
    extra_data: dict | None = None,
    hard_gate: bool = False,
    gate_label: str | None = None,
    idle_timeout_sec: int | float | None = _DEFAULT_IDLE_TIMEOUT_SEC,
    allowed_tools: list[str] | None = None,
    straggler_cfg: dict | None = None,
    backend: str | None = None,
    stable_prefix: str = "",
) -> StepResult:
    """Run ``command`` with ``prompt`` on stdin, return a StepResult.

    Success:
        status="ok", data merges {raw_response, response_bytes, command} with
        the caller's ``extra_data`` (caller's keys win on collision).
    Error codes:
        E_LLM_CMD_MISSING            — binary not found (recoverable=False)
        E_LLM_TIMEOUT                — exceeded timeout_sec (deadline check
                                       inside the streaming loop, OR
                                       communicate timeout for legacy path)
        E_LLM_NO_PROGRESS            — stream-json reader idled past
                                       ``idle_timeout_sec`` without emitting a
                                       new event (775D6752; recoverable=True)
        E_LLM_EXIT                   — non-zero return code
        E_LLM_RESULT_ERROR           — stream-json result event with
                                       non-success subtype (model-side error
                                       on a clean process exit)
        E_LLM_NO_RESULT_EVENT        — stream-json stream ended without ever
                                       emitting a type=result event
        E_HARD_GATE_MODEL_DOWNGRADE  — hard_gate=True and command not Opus

    775D6752 — idle-timeout watchdog (stream-json path only):
        ``idle_timeout_sec`` (keyword-only, default None — disabled) bounds the
        gap between consecutive NDJSON events from the subprocess. If the stream
        goes silent for longer than this cap, the watchdog kills the subprocess
        and returns ``E_LLM_NO_PROGRESS`` (recoverable=True). Default is disabled
        (only the outer ``timeout_sec`` applies); explicit per-call opt-in is
        the contract — see ``phase_6_review`` which pins ``idle_timeout_sec=60``
        on its fix_llm callsites (the documented hang-prone path). Pass
        ``idle_timeout_sec=0`` or ``None`` to disable explicitly. The
        legacy path (``_communicate_legacy``) accepts the kwarg for signature
        compat but IGNORES it — there are no per-line events to monitor on a
        single-shot communicate call. Effective per-poll budget is
        ``min(remaining_outer_budget, idle_timeout_sec)`` so an idle budget
        larger than the outer ceiling cannot extend the deadline.

    845F2C2C Layer 3 — ``allowed_tools`` (keyword-only, default None):
        When not None and ``cmd_kind == "claude_p"`` and no caller-supplied
        ``--allowed-tools`` / ``--allowedTools`` already present in ``command``
        (membership-based check, mirrors ``--output-format`` precedence), appends
        ``["--allowed-tools", " ".join(allowed_tools)]`` to ``effective_command``
        before spawn.  An empty list injects ``--allowed-tools ""`` (explicit
        "no tools allowed" signal), distinct from ``None`` (no flag injected).
        Silently skipped when ``cmd_kind != "claude_p"`` (would be invalid argv
        for shell-stub callers). Camel-case ``--allowedTools`` in ``command`` is
        treated as equivalent to kebab ``--allowed-tools`` for suppression.

    Hard-gate chokepoint (Wave 6 / CRIT #7 + #8 + HIGH #2):
        When ``hard_gate=True``, ``_assert_hard_gate_opus`` runs BEFORE the
        subprocess spawns. If the resolved command is not Opus (or is `claude
        -p` without ``--model``), this returns the gate's error StepResult and
        no subprocess is spawned, no ``subprocess_spawned`` event is emitted.
        ``gate_label`` is forwarded to the gate for telemetry/error message.
        Falls back to ``step_name`` when ``gate_label`` is None.

        Single-point enforcement closes the N+1-opt-in pattern that previously
        let phase_5_integrity (haiku default) and phase_45_spec_lite silently
        skip the gate. ``run_ctx`` is read from ``telemetry_ctx.get_current_run()``
        inside the chokepoint, so the ``hard_gate_refused`` event always fires
        when an active run is set — fixing HIGH #2 (workflows previously passed
        ``run_ctx=None`` so emission was a no-op).

    Telemetry (decree 2026-04-26 category A): if telemetry_ctx.get_current_run()
    is set, emits subprocess_spawned + subprocess_exited events. Failures in
    the event log are swallowed so observability never breaks execution.

    INVARIANT (E6F86B73): Auto-injecting any CLI flag that mutates stdout shape
    (e.g. --output-format stream-json) requires a paired unwrap step before
    raw_response is returned to callers. Never add shape-mutating auto-injection
    without updating the corresponding extract path. See
    _extract_result_text_from_events + the stream-json branch below. Under
    23680DDA, a successful auto-injected stream-json call sets raw_response
    to the LAST type=result event's "result" field; a stream with no such
    event yields status=error / E_LLM_NO_RESULT_EVENT (no silent-success
    fallback to raw stdout).

    WARNING (HIGH-4 — 23680DDA hardening pass): caller-supplied
    ``--output-format`` flags (any value) route through ``_communicate_legacy``,
    which uses ``proc.communicate(input=prompt, timeout=timeout_sec)`` — the
    blocking-on-EOF pattern that caused the 2026-05-06 15min hang for
    ``--output-format json``. For hang-free I/O, OMIT ``--output-format`` from
    your command list and let auto-injection select stream-json. The legacy
    path is preserved only for explicit caller opt-in (e.g. tests asserting
    'text' format or shell-stub commands); migrating it is out of 23680DDA's
    scope. New phases SHOULD NOT pass ``--output-format`` themselves.
    """
    run_ctx = telemetry_ctx.get_current_run()
    # 4C03CCED Ship 1A: backend selector gate — runs before hard_gate check so
    # unknown backends fail-closed immediately and resolver telemetry always fires.
    resolved_backend, resolved_source = _resolve_backend(backend, config_provider.env_mapping())
    emit_resolver_resolved(
        "invoke_llm_subprocess_backend",
        resolved_source,
        resolved_backend,
        {"known_backends": list(_KNOWN_BACKENDS)},
    )
    # GH1001/C0B6D653: default backend flipped to agent-sdk. If the resolved
    # default is not (yet) a known backend, gracefully rebind to
    # claude-subprocess rather than fail-closed — explicit kwarg/env selection
    # stays fail-loud (unaffected, resolved_source != "default").
    if resolved_source == "default" and resolved_backend not in _KNOWN_BACKENDS:
        resolved_backend = "claude-subprocess"
        resolved_source = "default-fallback"
    # CF2EE8ED §3.3: emit runner_backend_resolved per invocation for adoption-%
    # tracking + weekly-tripwire on unexpected selection. Ratified 2026-05-24.
    if run_ctx is not None and run_ctx.event_log is not None:
        _emit_safe(run_ctx.event_log, "runner_backend_resolved", {
            "backend": resolved_backend,
            "source": resolved_source,
            "step_name": step_name,
        }, run_ctx.run_id)
    if resolved_backend not in _KNOWN_BACKENDS:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=(run_ctx.step_name if run_ctx is not None else None) or step_name,
            error=_unknown_backend_error_message(resolved_backend),
            error_code="E_LLM_BACKEND_UNKNOWN",
            recoverable=False,
        )
    # 4C03CCED Ship 1C G1-AC5: capability-probe BEFORE subprocess spawn.
    # Runs for ALL known backends (after _KNOWN_BACKENDS membership check above).
    # Backends absent from _BACKEND_MANIFEST_SOURCE fail-closed here.
    capability_err = _assert_backend_supports_manifest(resolved_backend)
    if capability_err is not None:
        if run_ctx is not None and run_ctx.event_log is not None:
            _emit_safe(run_ctx.event_log, "runner_capability_probe_failed", {
                "backend": resolved_backend,
                "reason": "no_manifest_support",
            }, run_ctx.run_id)
        return capability_err
    # 4C03CCED Ship 1D G2-AC1: watchdog capability probe.
    idle_enabled = bool(idle_timeout_sec) and float(idle_timeout_sec) > 0  # type: ignore[arg-type]
    straggler_enabled = straggler_cfg is not None
    watchdog_err = _assert_backend_supports_watchdog(
        resolved_backend,
        idle_enabled=idle_enabled,
        straggler_enabled=straggler_enabled,
    )
    if watchdog_err is not None:
        if run_ctx is not None and run_ctx.event_log is not None:
            _emit_safe(run_ctx.event_log, "runner_capability_probe_failed", {
                "backend": resolved_backend,
                "reason": "no_watchdog_support",
                "idle_enabled": idle_enabled,
                "straggler_enabled": straggler_enabled,
            }, run_ctx.run_id)
        watchdog_err.step_name = (
            (run_ctx.step_name if run_ctx is not None else None) or step_name
        )
        return watchdog_err
    # GH375: tier-aware model dispatch — ONE layer for all phases + backends.
    # hard_gate-EXEMPT: hard-gated steps keep their pinned (opus) model, so
    # _assert_hard_gate_opus remains the critical-phase floor (E_HARD_GATE_MODEL_DOWNGRADE).
    # DOWNGRADE-ONLY: never upgrades a cheaper pin (haiku-pinned SIMPLE RED/GREEN stay haiku).
    if not hard_gate:
        _tier = run_ctx.tier if run_ctx is not None else None
        if _tier is not None:
            _tier_model = _load_tier_model(_tier)
            if _tier_model is not None:
                _apply = _tier_model != model and _tier_model_is_downgrade(_tier_model, model)
            else:
                _apply = False
            _src = "model_by_tier" if _apply else "caller_pinned"
            emit_resolver_resolved(
                "invoke_llm_subprocess_tier_model", _src,
                _tier_model if _apply and _tier_model is not None else model,
                {"tier": _tier, "pinned_model": model, "tier_model": _tier_model, "step_name": step_name},
            )
            if _apply and _tier_model is not None:
                model = _tier_model
    # 68E964FB: registry dispatch — replaces the hardcoded if/else tail.
    # _BACKENDS keys == _KNOWN_BACKENDS (single source); unknown backend already
    # returned above so this lookup is always key-safe.
    # GH334 §2.2: CONDITIONAL threading — only pass stable_prefix when
    # non-empty, so strict-signature backends/test-doubles lacking the param
    # stay call-compatible (back-compat byte-identity, §2.0/§2.3). Explicit
    # two-branch dispatch (not a dict-splat) so mypy sees typed kwargs against
    # the LLMBackend protocol.
    if stable_prefix:
        return _BACKENDS[resolved_backend](
            prompt=prompt,
            model=model,
            timeout_sec=timeout_sec,
            step_name=step_name,
            extra_data=extra_data,
            allowed_tools=allowed_tools,
            run_ctx=run_ctx,
            hard_gate=hard_gate,
            gate_label=gate_label,
            straggler_cfg=straggler_cfg,
            idle_timeout_sec=idle_timeout_sec,
            stable_prefix=stable_prefix,
        )
    return _BACKENDS[resolved_backend](
        prompt=prompt,
        model=model,
        timeout_sec=timeout_sec,
        step_name=step_name,
        extra_data=extra_data,
        allowed_tools=allowed_tools,
        run_ctx=run_ctx,
        hard_gate=hard_gate,
        gate_label=gate_label,
        straggler_cfg=straggler_cfg,
        idle_timeout_sec=idle_timeout_sec,
    )


def _invoke_subprocess(
    *,
    prompt: str,
    model: str,
    timeout_sec: int,
    step_name: str,
    extra_data: "dict | None",
    allowed_tools: "list[str] | None",
    run_ctx: "_RunCtx | None",
    hard_gate: bool = False,
    gate_label: "str | None" = None,
    straggler_cfg: "dict | None" = None,
    idle_timeout_sec: "int | float | None" = None,
    stable_prefix: str = "",
) -> StepResult:
    """Claude-subprocess backend handler (68E964FB: extracted from inline tail).

    Handles the claude-subprocess path: hard-gate Opus assert, flag auto-injection,
    Popen spawn, straggler/idle watchdog, StepResult return. Body is byte-identical
    to the former inline tail of invoke_llm_subprocess (L936→L1425 pre-68E964FB).
    25e75663: command param replaced by model:str; argv built internally via
    _build_claude_argv(model).
    """
    # Build the base argv from the model string (25e75663 §1.2 / §2.4).
    base_argv = _build_claude_argv(model)
    # (existing claude-subprocess path: if hard_gate → ... → Popen ...)
    if hard_gate:
        gate_err = _assert_hard_gate_opus(
            base_argv,
            step_name=step_name,
            gate_label=gate_label or step_name,
            run_ctx=run_ctx,
        )
        if gate_err is not None:
            return gate_err
    # 25e75663: default path is always claude_p now (base_argv is always a claude
    # headless argv). The auto-inject of --output-format stream-json --verbose and
    # --allowed-tools apply unconditionally.
    # 23680DDA: auto-inject stream-json (incremental) + --verbose.
    effective_command = base_argv + list(get_provider().stream_flags)
    output_format_auto_injected = True
    # 845F2C2C Layer 3: per-phase --allowed-tools profile injection.
    if allowed_tools is not None:
        effective_command = effective_command + ["--allowed-tools", " ".join(allowed_tools)]
    # 32ED59E2: auto-inject reasoning-effort from config for non-gate delegations.
    # hard_gate: no longer a blanket exemption — the Opus correctness-validation
    # spawn keeps CLI-default effort UNLESS an explicit per-model pin (GH439,
    # claude.effort.by_model[family]) overrides it. Inert when claude.effort is
    # unset (both paths no-op via _apply_effort).
    if not hard_gate:
        effective_command = _apply_effort(effective_command, _load_effort(model, step_name))
    else:
        effective_command = _apply_effort(effective_command, _load_effort_gate(model))
    # GH334 §2.4: subprocess split — hoist stable_prefix into a
    # --append-system-prompt flag and remove it from the stdin (variable) body.
    # Defensive fallback (empty, or not a substring of prompt): byte-identical
    # to today — no flag, full prompt on stdin.
    if stable_prefix and stable_prefix in prompt:
        effective_prompt = prompt.replace(stable_prefix, "", 1)
        effective_command = effective_command + ["--append-system-prompt", stable_prefix]
    else:
        effective_prompt = prompt
    prompt_size = len(prompt.encode("utf-8"))

    started_monotonic = time.monotonic()

    # Use Popen so we can capture pid before the process exits.
    try:
        proc = subprocess.Popen(  # bounded-spawn: allow llm-stream-deadline-enforced
            effective_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as e:
        # No spawn happened — emit nothing for category A (no pid).
        return StepResult(
            status="error",
            data={"command": effective_command},
            duration_ms=0,
            step_name=step_name,
            error=f"llm command not found: {e}",
            error_code="E_LLM_CMD_MISSING",
            recoverable=False,
        )

    # subprocess_spawned (after we have a pid)
    if run_ctx is not None:
        _emit_safe(run_ctx.event_log, "subprocess_spawned", {
            "cmd_kind": "claude_p",
            "cmd_tail": _cmd_tail_redacted(effective_command),
            "output_format": _detect_output_format(effective_command),
            "model": model,
            "prompt_size_bytes": prompt_size,
            "phase": run_ctx.phase,
            "step_name": run_ctx.step_name or step_name,
            "pid": proc.pid,
            "parent_skill": config_provider.env_opt("HAL_PARENT_SKILL"),
            "backend": "claude-subprocess",
            "cycle": run_ctx.cycle,
        }, run_ctx.run_id)

    # CCBB65DC: straggler-abort watchdog — strictly opt-in via straggler_cfg kwarg.
    # Constructed only after proc.pid is available; wrapped in try/except so a
    # watchdog construction failure never breaks the LLM call.
    straggler_watchdog = None
    if straggler_cfg is not None:
        try:
            straggler_watchdog = _StragglerWatchdog(
                proc=proc,
                reviews_dir=straggler_cfg["reviews_dir"],
                expected_n=int(straggler_cfg["expected_n"]),
                patience_sec=float(straggler_cfg.get("patience_sec") or STRAGGLER_PATIENCE_SEC),
                poll_interval_sec=float(straggler_cfg.get("poll_interval_sec") or STRAGGLER_POLL_INTERVAL_SEC),
            )
            straggler_watchdog.start()
        except Exception:
            logger.warning("failed to start straggler watchdog", exc_info=True)
            straggler_watchdog = None

    # 23680DDA: stream-json path uses an incremental read loop with a
    # deadline-based timeout (no communicate(timeout=)). Legacy path
    # (caller-supplied flags / shell commands) keeps the single-shot
    # communicate(timeout=) semantics so existing tests + non-claude
    # callers remain backwards-compatible.
    idle_aborted = False
    cli_lingered = False
    if output_format_auto_injected:
        timed_out, stdout, stderr, events, idle_aborted, cli_lingered = _stream_read_events(
            proc=proc,
            prompt=effective_prompt,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
        )
    else:
        # 775D6752: legacy path accepts idle_timeout_sec for signature compat
        # but cannot honour it — there are no per-line stream events to monitor
        # on a single-shot communicate() call. Pinned by RED test 4.
        timed_out, stdout, stderr = _communicate_legacy(
            proc=proc,
            prompt=effective_prompt,
            timeout_sec=timeout_sec,
        )
        events = None  # legacy path doesn't pre-parse events

    # CCBB65DC: stop the straggler watchdog now that the subprocess call is done.
    if straggler_watchdog is not None:
        straggler_watchdog.stop()

    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    exit_code = proc.returncode if proc.returncode is not None else -1

    # F4F26513: salvage path — CLI lingered after emitting terminal result event.
    # Emit observability event and override exit_code to 0 so the success branch
    # below is taken. The kernel-level returncode (typically -9 from SIGKILL) is
    # captured in proc.returncode for forensics but must not gate success here.
    # 3C54A029: capture kernel returncode BEFORE overriding exit_code — so forensics
    # payload retains the real -9 (SIGKILL) even though caller sees exit_code=0.
    kernel_returncode = proc.returncode if cli_lingered else None
    if cli_lingered and run_ctx is not None:
        _emit_safe(run_ctx.event_log, "cli_lingered_post_result", {
            "post_result_wait_ms": POST_RESULT_WAIT_SEC * 1000,
            "events_count": len(events or []),
            "has_result_event": True,
            "phase": run_ctx.phase,
            "step_name": run_ctx.step_name or step_name,
        }, run_ctx.run_id)
    if cli_lingered:
        exit_code = 0

    # CCBB65DC: synthetic-ok branch — placed AFTER the F4F26513 cli_lingered
    # salvage block but BEFORE all error-determination branches (idle_aborted,
    # timed_out, non-zero exit_code, E_LLM_NO_RESULT_EVENT). A watchdog-killed
    # proc exits non-zero; without this check, E_LLM_EXIT fires first and the
    # feature is inert. The intentional kill is NOT an error — return status="ok"
    # so the workflow continues to aggregate_review_findings on the N-1 role files.
    if straggler_watchdog is not None and getattr(straggler_watchdog, "aborted", False):
        if run_ctx is not None:
            _emit_safe(run_ctx.event_log, "straggler_abort", {
                "expected_n": int(straggler_cfg["expected_n"]) if straggler_cfg else None,
                "phase": getattr(run_ctx, "phase", None),
                "step_name": getattr(run_ctx, "step_name", None) or step_name,
                "pid": proc.pid,
            }, run_ctx.run_id)
        straggler_data: dict = {
            "raw_response": "",
            "response_bytes": 0,
            "command": effective_command,
            "straggler_aborted": True,
        }
        if extra_data:
            straggler_data.update(extra_data)
        return StepResult(
            status="ok",
            data=straggler_data,
            duration_ms=duration_ms,
            step_name=step_name,
        )

    # Tokens + cost extraction. Stream-json: walk events for the last result
    # event. Legacy: existing _parse_claude_json (last-non-empty-line of
    # single-shot stdout, only when caller explicitly asked --output-format json).
    if output_format_auto_injected and events is not None:
        tokens, cost_usd = _tokens_and_cost_from_events(events)
    else:
        tokens, cost_usd = _parse_claude_json(effective_command, stdout)
    tokens_in = tokens["input"] if tokens else None
    tokens_out = tokens["output"] if tokens else None

    if run_ctx is not None:
        # 775D6752: only set aborted_reason on idle-watchdog kills. Success
        # paths, exit-code errors, model-side errors, and outer-ceiling
        # timeouts MUST NOT carry aborted_reason="idle_timeout" (would burn
        # observability + mislabel the kill source). Tests pin both branches
        # of this discriminator (RED tests 5, 9, 10).
        exit_payload = {
            "pid": proc.pid,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "response_size_bytes": len(stdout.encode("utf-8")),
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "tokens": tokens,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "phase": run_ctx.phase,
            "step_name": run_ctx.step_name or step_name,
            "backend": "claude-subprocess",
            "cycle": run_ctx.cycle,
        }
        if idle_aborted:
            exit_payload["aborted_reason"] = "idle_timeout"
        if cli_lingered:  # 3C54A029: forensics — kernel returncode before salvage override
            exit_payload["kernel_returncode"] = kernel_returncode
        _emit_safe(run_ctx.event_log, "subprocess_exited", exit_payload, run_ctx.run_id)
        # 6923B6AC D3: idle-timeout alarm — emit ONCE per idle abort so the
        # event log carries an auditable signal independent of subprocess_exited.
        # Gated strictly on aborted_reason="idle_timeout" so success and outer-
        # timeout paths emit no alarm (D3b).
        if idle_aborted:
            _emit_safe(run_ctx.event_log, "idle_timeout_alarm", {
                "run_id": run_ctx.run_id,
                "phase": run_ctx.phase,
                "step_name": run_ctx.step_name or step_name,
                "duration_ms": duration_ms,
                "response_size_bytes": len(stdout.encode("utf-8")),
                "idle_timeout_sec": idle_timeout_sec,
                "tokens_out": tokens_out,
            }, run_ctx.run_id)

    if idle_aborted:
        # 775D6752: in-stream idle watchdog kill — emit a distinct error code
        # from E_LLM_TIMEOUT so callers can distinguish "model went silent
        # mid-stream" from "outer ceiling hit". recoverable=True is
        # informational for caller introspection only — engine.py:_execute_steps
        # only auto-retries when ``data.get("retry_from_step")`` is set
        # (which we don't set here), matching the green_watchdog precedent.
        stderr_tail = _tail(stderr or "")
        stdout_tail = _tail(stdout or "")
        cmd_tail = _cmd_tail_redacted(effective_command)
        # MED-3: this branch only fires when idle_enabled=True (line 580
        # requires truthy idle_timeout_sec > 0), so the fallback was dead
        # code. Self-document with an assert so any future regression that
        # reaches here with falsy idle_timeout_sec fails loudly.
        assert idle_timeout_sec, "idle path requires positive idle_timeout_sec"
        idle_budget = idle_timeout_sec
        return StepResult(
            status="error",
            data={
                "command": effective_command,
                "timeout_sec": timeout_sec,
                "idle_timeout_sec": idle_budget,
                "stdout_partial": (stdout or "")[:2000],
                "stderr_tail": stderr_tail,
                "stdout_tail": stdout_tail,
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "cmd_tail": cmd_tail,
            },
            duration_ms=duration_ms,
            step_name=step_name,
            error=(
                f"llm stream idle past {idle_budget}s "
                f"(duration_ms={duration_ms} exit_code={exit_code} cmd_tail={cmd_tail}); "
                f"stderr_tail={_short(stderr_tail)} stdout_tail={_short(stdout_tail)}"
            ),
            error_code="E_LLM_NO_PROGRESS",
            recoverable=True,
        )

    if timed_out:
        stderr_tail = _tail(stderr or "")
        stdout_tail = _tail(stdout or "")
        cmd_tail = _cmd_tail_redacted(effective_command)
        # MED-2: model errors and missing/timed-out result events are typically
        # transient; recoverable=True is informational only (engine.py
        # auto-retries only when ``data["retry_from_step"]`` is set, which
        # we don't set here). Inherits the default but pass explicitly so
        # the contract is self-documenting.
        # MED-1: do NOT ship a fake `raw_response` placeholder string — only
        # set raw_response on success branches. Absent key signals to callers
        # that no parseable result was produced.
        # GH285 C3: classify the timed-out partial output ACTIVE (model still
        # generating, ceiling too low) vs STUCK (no meaningful output, retry
        # is right) — payload/event only, error_code stays E_LLM_TIMEOUT.
        response_bytes = len((stdout or "").encode("utf-8"))
        active_threshold_bytes = resolve_active_threshold_bytes(
            config_provider.env_opt("HAL_TIMEOUT_ACTIVE_THRESHOLD_BYTES")
        )
        timeout_state = classify_timeout_state(response_bytes, active_threshold_bytes)
        if run_ctx is not None:
            _emit_safe(run_ctx.event_log, "llm_timeout_state", {
                "run_id": run_ctx.run_id,
                "phase": run_ctx.phase,
                "step_name": run_ctx.step_name or step_name,
                "timeout_state": timeout_state,
                "response_bytes": response_bytes,
                "active_threshold_bytes": active_threshold_bytes,
                "timeout_sec": timeout_sec,
                "duration_ms": duration_ms,
            }, run_ctx.run_id)
        return StepResult(
            status="error",
            data={
                "command": effective_command,
                "timeout_sec": timeout_sec,
                "stdout_partial": (stdout or "")[:2000],
                "stderr_tail": stderr_tail,
                "stdout_tail": stdout_tail,
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "cmd_tail": cmd_tail,
                "timeout_state": timeout_state,
                "response_bytes": response_bytes,
                "active_threshold_bytes": active_threshold_bytes,
            },
            duration_ms=timeout_sec * 1000,
            step_name=step_name,
            error=(
                f"llm command exceeded {timeout_sec}s timeout "
                f"(duration_ms={duration_ms} exit_code={exit_code} timeout_state={timeout_state} cmd_tail={cmd_tail}); "
                f"stderr_tail={_short(stderr_tail)} stdout_tail={_short(stdout_tail)}"
            ),
            error_code="E_LLM_TIMEOUT",
            recoverable=True,
        )

    if exit_code != 0:
        stderr_tail = _tail(stderr or "")
        stdout_tail = _tail(stdout or "")
        cmd_tail = _cmd_tail_redacted(effective_command)
        marker = None
        if config_provider.get_config().gate_enabled("HAL_ENV_LIMIT_CLASSIFY"):
            marker = detect_env_limit(stderr_tail, stdout_tail, stderr, stdout)
        if marker is not None:
            return StepResult(
                status="error",
                data={
                    "command": effective_command,
                    "exit_code": exit_code,
                    "stderr": (stderr or "")[:2000],
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "duration_ms": duration_ms,
                    "cmd_tail": cmd_tail,
                    "env_limit_marker": marker,
                    "pausable": True,
                },
                duration_ms=duration_ms,
                step_name=step_name,
                error=(
                    f"account spend/usage limit hit: llm command exited {exit_code} marker={marker!r} "
                    f"(duration_ms={duration_ms} cmd_tail={cmd_tail}); "
                    f"stderr_tail={_short(stderr_tail)} stdout_tail={_short(stdout_tail)}"
                ),
                error_code="E_LLM_SPEND_LIMIT",
                recoverable=True,
            )
        return StepResult(
            status="error",
            data={
                "command": effective_command,
                "exit_code": exit_code,
                "stderr": (stderr or "")[:2000],
                "stderr_tail": stderr_tail,
                "stdout_tail": stdout_tail,
                "duration_ms": duration_ms,
                "cmd_tail": cmd_tail,
            },
            duration_ms=duration_ms,
            step_name=step_name,
            error=(
                f"llm command exited {exit_code} "
                f"(duration_ms={duration_ms} cmd_tail={cmd_tail}); "
                f"stderr_tail={_short(stderr_tail)} stdout_tail={_short(stdout_tail)}"
            ),
            error_code="E_LLM_EXIT",
        )

    # 23680DDA: under stream-json, raw_response is the LAST type=result event's
    # "result" field. Errors raised by the model side (subtype != "success") or
    # an EOF without ever seeing a result event must NOT silently fall back to
    # raw stdout (per 2026-05-06 W1 post-mortem — silent-success was a bug class).
    if output_format_auto_injected:
        result_event = _find_last_result_event(events or [])
        if result_event is None:
            cmd_tail = _cmd_tail_redacted(effective_command)
            stderr_tail = _tail(stderr or "")
            stdout_tail = _tail(stdout or "")
            events_seen = len(events or [])
            # MED-1: do NOT ship a sentinel string (`<no_result_event>`) into
            # `raw_response` — it would mimic a successful payload to any
            # caller that doesn't inspect `error_code`. Absent-key shape is
            # self-documenting: only success branches set raw_response.
            # MED-2: recoverable=True is informational for caller
            # introspection only — engine.py auto-retries only when
            # ``data["retry_from_step"]`` is set (which we don't set here).
            # A respawn at the workflow layer can plausibly succeed
            # (claude CLI / network flake).
            return StepResult(
                status="error",
                data={
                    "command": effective_command,
                    "events_seen": events_seen,
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "duration_ms": duration_ms,
                    "exit_code": exit_code,
                    "cmd_tail": cmd_tail,
                },
                duration_ms=duration_ms,
                step_name=step_name,
                error=(
                    f"llm stream ended with no type=result event "
                    f"(events_seen={events_seen}, exit_code={exit_code}, "
                    f"cmd_tail={cmd_tail}); stderr_tail={_short(stderr_tail)}"
                ),
                error_code="E_LLM_NO_RESULT_EVENT",
                recoverable=True,
            )
        # MED-3: do NOT default `subtype` to "success" — a malformed result
        # event missing the field would silently pass as success. Instead
        # treat absent/non-string subtype as malformed (HIGH-3 family).
        subtype = result_event.get("subtype")
        # HIGH-3: empty / whitespace-only / non-string `result` on a
        # subtype=success event is the silent-success bug class the W1
        # post-mortem cited. Force a hard error code so retries / human
        # review kick in instead of swallowing an empty payload as ok.
        result_text = result_event.get("result")
        is_malformed_result = (
            not isinstance(result_text, str)
            or result_text.strip() == ""
        )
        if subtype is None or not isinstance(subtype, str):
            cmd_tail = _cmd_tail_redacted(effective_command)
            stderr_tail = _tail(stderr or "")
            stdout_tail = _tail(stdout or "")
            return StepResult(
                status="error",
                data={
                    "command": effective_command,
                    "subtype": subtype,
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "duration_ms": duration_ms,
                    "exit_code": exit_code,
                    "cmd_tail": cmd_tail,
                },
                duration_ms=duration_ms,
                step_name=step_name,
                error=(
                    f"llm result event missing/non-string subtype={subtype!r} "
                    f"(exit_code={exit_code}, cmd_tail={cmd_tail}); "
                    f"stderr_tail={_short(stderr_tail)}"
                ),
                error_code="E_LLM_RESULT_MALFORMED",
                recoverable=True,
            )
        if subtype != "success":
            cmd_tail = _cmd_tail_redacted(effective_command)
            stderr_tail = _tail(stderr or "")
            stdout_tail = _tail(stdout or "")
            # MED-2: recoverable=True explicit — model-side errors
            # (error_max_turns, etc.) often clear on retry with fresh state.
            return StepResult(
                status="error",
                data={
                    "command": effective_command,
                    "subtype": subtype,
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "duration_ms": duration_ms,
                    "exit_code": exit_code,
                    "cmd_tail": cmd_tail,
                },
                duration_ms=duration_ms,
                step_name=step_name,
                error=(
                    f"llm result event signaled error subtype={subtype!r} "
                    f"(exit_code={exit_code}, cmd_tail={cmd_tail}); "
                    f"stderr_tail={_short(stderr_tail)}"
                ),
                error_code="E_LLM_RESULT_ERROR",
                recoverable=True,
            )
        # HIGH-3: subtype=success but result missing/empty/whitespace =>
        # E_LLM_RESULT_MALFORMED. Keeps the silent-success bug class (W1
        # post-mortem 2026-05-06) sealed at the contract boundary.
        if is_malformed_result:
            cmd_tail = _cmd_tail_redacted(effective_command)
            stderr_tail = _tail(stderr or "")
            stdout_tail = _tail(stdout or "")
            return StepResult(
                status="error",
                data={
                    "command": effective_command,
                    "subtype": subtype,
                    "result_type": type(result_text).__name__,
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "duration_ms": duration_ms,
                    "exit_code": exit_code,
                    "cmd_tail": cmd_tail,
                },
                duration_ms=duration_ms,
                step_name=step_name,
                error=(
                    f"llm result event subtype=success but `result` field is "
                    f"missing/empty/whitespace (type={type(result_text).__name__}, "
                    f"exit_code={exit_code}, cmd_tail={cmd_tail}); "
                    f"stderr_tail={_short(stderr_tail)}"
                ),
                error_code="E_LLM_RESULT_MALFORMED",
                recoverable=True,
            )
        # E6F86B73: raw_response is the clean model text from the result event
        # — never the envelope, never concatenated assistant deltas.
        # Type already validated as non-empty str above (HIGH-3 guard).
        raw_response = result_text
    else:
        # Legacy path: caller-supplied --output-format json gets the JSON
        # envelope as raw_response (caller asked for JSON, give them JSON);
        # everything else passes raw stdout through unchanged.
        raw_response = stdout

    data: dict = {
        "raw_response": raw_response,
        "response_bytes": len(stdout.encode("utf-8")),
        "command": effective_command,
        "tokens_out": tokens_out,
        "tokens_in": tokens_in,
    }
    if extra_data:
        data.update(extra_data)
    # 4961254A: manifest of paths actually written by the worker subprocess —
    # derived from the harness's own tool-call transcript, NOT from the worker's
    # self-report.  Set AFTER extra_data merge so a caller cannot accidentally
    # shadow it via extra_data (name is reserved for this key).  Absent on all
    # error branches (self-documenting: only success has a valid manifest).
    data["worker_written_paths"] = _written_paths_from_events(events or [])
    # 4C03CCED Ship 1C G1: harness-tool-record manifest (stream-json transcript).
    data["manifest_source"] = "harness_tool_record"

    return StepResult(
        status="ok",
        data=data,
        duration_ms=duration_ms,
        step_name=step_name,
    )


# ---------------------------------------------------------------------------
# 68E964FB: LLM backend Protocol + registry (§2.2–§2.3)
# Placed after both handler defs so the registry can reference them directly.
# ---------------------------------------------------------------------------

class LLMBackend(typing.Protocol):
    """Protocol for LLM backend handlers (68E964FB §2.2).

    Both registered handlers (_invoke_subprocess, _invoke_in_session) accept
    this 11-param keyword-only superset. Dispatch via _BACKENDS[resolved_backend].
    25e75663: command param replaced by model:str across the Protocol + all handlers.
    """

    def __call__(
        self,
        *,
        prompt: str,
        model: str,
        timeout_sec: int,
        step_name: str,
        extra_data: "dict | None",
        allowed_tools: "list[str] | None",
        run_ctx: "_RunCtx | None",
        hard_gate: bool,
        gate_label: "str | None",
        straggler_cfg: "dict | None",
        idle_timeout_sec: "int | float | None",
        stable_prefix: str = "",
    ) -> StepResult:
        ...


_BACKENDS: dict[str, LLMBackend] = {
    "claude-subprocess": _invoke_subprocess,  # type: ignore[dict-item]
    "claude-in-session": _invoke_in_session,  # type: ignore[dict-item]
}

# §1g single source of truth: _KNOWN_BACKENDS derives from the registry.
# Adding a backend = one dict entry; _KNOWN_BACKENDS + the fail-loud guard
# in invoke_llm_subprocess automatically include it.
_KNOWN_BACKENDS = tuple(_BACKENDS)

# GH898: static install-hints for reference backends whose guarded
# registration (run.py try/except) silently skipped register() because the
# backend's deps are not importable. Static by design — the hint must be
# available exactly when the module itself cannot be imported. Keep names in
# sync with lib/reference_backends/* register() calls.
# Contract: resolved_backend is resolved/stripped upstream; register() names
# are lowercase; this dict's keys must match those lowercase names exactly.
_REFERENCE_BACKEND_INSTALL_HINTS: dict[str, str] = {
    "agent-sdk": "pip install claude-agent-sdk",
    "anthropic-api": "install a package build that bundles lib.reference_backends (stdlib-only backend; its module was not importable)",
    # pydantic-ai covers both providers, so both hints resolve to one extra
    # (GH1112: the `agentic-pydantic-anthropic` ghost extra never existed).
    "pydantic-openai": install_hint(EXTRA_AGENTIC_PYDANTIC),
    "pydantic-anthropic": install_hint(EXTRA_AGENTIC_PYDANTIC),
}


def _unknown_backend_error_message(resolved_backend: str) -> str:
    """GH898: E_LLM_BACKEND_UNKNOWN message; appends an install-hint when the
    name matches a known reference backend whose registration was skipped."""
    msg = f"unknown runner backend: {resolved_backend!r}"
    hint = _REFERENCE_BACKEND_INSTALL_HINTS.get(resolved_backend)
    if hint is not None:
        msg += (
            f" — {resolved_backend!r} is a known reference backend whose deps are "
            f"not importable, so its guarded registration was skipped. Fix: {hint} "
            "— installed into the venv HAL_BUILD_PYTHON points at (the engine "
            "subprocess interpreter), not necessarily your shell's active venv."
        )
    return msg


# --- OSS backend-injection seam (#302 / A60F1FE3) ------------------------
# Snapshot the built-in backends + their capability maps so reset_backends()
# can restore exactly the shipped defaults after runtime registrations.
_DEFAULT_BACKENDS = dict(_BACKENDS)
_DEFAULT_BACKEND_MANIFEST_SOURCE = dict(_BACKEND_MANIFEST_SOURCE)
_DEFAULT_BACKEND_CAPABILITIES = dict(_BACKEND_CAPABILITIES)


def register_backend(
    name: str,
    impl: "LLMBackend",
    *,
    manifest_source: str,
    capabilities: "frozenset[str] | set[str] | tuple[str, ...] | None" = None,
    overwrite: bool = False,
) -> None:
    """Public OSS injection seam (#302). Register an LLM backend so
    invoke_llm_subprocess(backend=name, ...) dispatches to `impl`.

    Updates the three single-source maps (_BACKENDS, _BACKEND_MANIFEST_SOURCE,
    _BACKEND_CAPABILITIES) AND rebinds the two derived snapshots
    (_KNOWN_BACKENDS, _ALLOWED_MANIFEST_SOURCES) so the fail-loud guard
    (E_LLM_BACKEND_UNKNOWN) and manifest-source validation accept the new
    backend at runtime — fixing the import-time frozen-snapshot gap.
    """
    global _KNOWN_BACKENDS, _ALLOWED_MANIFEST_SOURCES
    if not isinstance(name, str) or not name:
        raise ValueError("register_backend: name must be a non-empty str")
    if not callable(impl):
        raise TypeError("register_backend: impl must be callable (LLMBackend)")
    if not isinstance(manifest_source, str) or not manifest_source:
        raise ValueError("register_backend: manifest_source must be a non-empty str")
    if name in _BACKENDS and not overwrite:
        raise ValueError(
            f"register_backend: backend {name!r} already registered; "
            f"pass overwrite=True to replace"
        )
    _BACKENDS[name] = impl  # type: ignore[assignment]
    _BACKEND_MANIFEST_SOURCE[name] = manifest_source
    _BACKEND_CAPABILITIES[name] = frozenset(capabilities or ())
    _KNOWN_BACKENDS = tuple(_BACKENDS)
    _ALLOWED_MANIFEST_SOURCES = frozenset(_BACKEND_MANIFEST_SOURCE.values())


def reset_backends() -> None:
    """Restore the built-in claude backends; drop all runtime registrations.
    §1i test-teardown + OSS reset. Mirrors config_provider.reset_default_config_provider_factory."""
    global _KNOWN_BACKENDS, _ALLOWED_MANIFEST_SOURCES
    _BACKENDS.clear(); _BACKENDS.update(_DEFAULT_BACKENDS)
    _BACKEND_MANIFEST_SOURCE.clear(); _BACKEND_MANIFEST_SOURCE.update(_DEFAULT_BACKEND_MANIFEST_SOURCE)
    _BACKEND_CAPABILITIES.clear(); _BACKEND_CAPABILITIES.update(_DEFAULT_BACKEND_CAPABILITIES)
    _KNOWN_BACKENDS = tuple(_BACKENDS)
    _ALLOWED_MANIFEST_SOURCES = frozenset(_BACKEND_MANIFEST_SOURCE.values())


def _stream_read_events(
    *,
    proc,
    prompt: str,
    timeout_sec: int,
    idle_timeout_sec: int | float | None = None,
) -> tuple[bool, str, str, list[dict], bool, bool]:
    """Stream-json read loop: feed prompt to stdin via a daemon FEEDER thread
    (HIGH-1), iterate ``proc.stdout`` line-by-line in a separate daemon
    READER thread, all under a single deadline-based watchdog (HIGH-2).
    Replaces the old proc.communicate(timeout=) blocking-on-EOF pattern that
    caused the 2026-05-06 15min hang.

    HIGH-1 (stdin pipe-buffer deadlock fix):
        macOS pipe buffer is ~16KB. Phases like phase_4_architect /
        phase_6_review routinely send prompts >64KB. A naive
        ``stdin.write(prompt); stdin.close()`` on the main thread blocks
        forever if claude CLI doesn't drain stdin before producing stdout.
        Fix: spawn a stdin-feeder daemon thread that runs concurrently with
        the reader thread, so neither blocks on the other.

    HIGH-2 (wall-clock budget remaining):
        Both feeder and reader joins use the REMAINING budget
        (``timeout_sec - elapsed``), not a fresh ``timeout_sec``, so the
        deadline is honored end-to-end across both phases.

    MED-4 (FD close in finally):
        proc.stdout / proc.stderr are explicitly closed in ``finally``
        rather than relying on GC. Avoids FD leaks under repeated calls.

    775D6752 — idle-timeout watchdog:
        ``idle_timeout_sec`` (None or <=0 disables) bounds the gap between
        consecutive non-empty stdout lines from the reader. Implementation:
        the single ``reader_thread.join(timeout=_remaining())`` is replaced
        with a polling loop that wakes at ``min(remaining_outer, idle_remaining,
        1.0)`` cadence and checks whether ``last_event_monotonic[0]`` has
        been bumped within ``idle_remaining`` (the reader thread updates it
        on every non-empty stdout line). If not, kill the subprocess and
        set ``idle_aborted=True``.
        Effective per-poll budget is clamped so an idle budget larger than
        the outer ceiling cannot extend the deadline (RED test 11).

    Returns ``(timed_out, stdout_text, stderr_text, events, idle_aborted,
    cli_lingered_after_result)`` where events is a list of parsed NDJSON dicts
    (malformed lines are logged at debug and dropped). On timeout, calls
    ``proc.kill()`` and returns ``timed_out=True``; daemon threads are abandoned
    (they die when the interpreter exits). On idle abort, ``idle_aborted=True``
    AND ``timed_out=True`` — caller distinguishes the two via ``idle_aborted``
    (which short-circuits before the generic E_LLM_TIMEOUT branch).
    ``cli_lingered_after_result=True`` when ``result_event_seen=True`` AND
    ``proc.wait(timeout=5)`` raised ``TimeoutExpired`` — caller treats this as
    success (F4F26513 salvage path).
    """
    started_monotonic = time.monotonic()

    def _remaining() -> float:
        """Remaining seconds in the watchdog budget (HIGH-2)."""
        elapsed = time.monotonic() - started_monotonic
        return max(0.001, float(timeout_sec) - elapsed)

    raw_lines: list[str] = []
    events: list[dict] = []
    feeder_error: list[BaseException] = []
    # F4F26513: set True when result_event_seen=True AND proc.wait(timeout=5)
    # raises TimeoutExpired — CLI lingered after emitting the result event.
    # Caller treats this as success (salvage path).
    cli_lingered_after_result = False
    # 775D6752: shared progress marker. Reader thread bumps this on every
    # non-empty stdout line received (parsed OR malformed — a stream of
    # malformed-but-arriving NDJSON still proves the subprocess is alive).
    # Lock-free write/read of a list slot is OK in CPython because list
    # element assignment is atomic under the GIL; we don't read+write.
    last_event_monotonic = [time.monotonic()]
    # 775D6752: normalise the idle budget. None or <=0 → disabled.
    idle_enabled = bool(idle_timeout_sec) and float(idle_timeout_sec) > 0  # type: ignore[arg-type]
    idle_budget = float(idle_timeout_sec) if idle_enabled and idle_timeout_sec is not None else 0.0

    def _feeder():
        """HIGH-1: stdin write + close on a daemon thread so a >16KB prompt
        can't deadlock the main thread waiting for the CLI to drain.
        """
        try:
            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except (BrokenPipeError, OSError) as e:  # pragma: no cover — rare
            logger.warning("stdin write/close failed for stream-json: %s", e)
            feeder_error.append(e)
        except Exception as e:  # noqa: BLE001 — never crash the feeder
            logger.warning("stdin feeder thread crashed: %s", e, exc_info=True)
            feeder_error.append(e)

    def _reader():
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                # Defensive: under unittest.mock.MagicMock-shaped fixtures
                # readline() can return a non-string sentinel. Treat anything
                # non-str as EOF rather than crashing the reader thread.
                if not isinstance(line, (str, bytes)):
                    break
                if isinstance(line, bytes):
                    try:
                        line = line.decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        break
                raw_lines.append(line)
                # 775D6752: bump progress marker on EVERY non-empty stdout
                # line — malformed-but-flowing NDJSON still proves the
                # subprocess is alive (semantics: idle-timeout fires on stdout
                # silence, NOT on parse failure).
                stripped = line.strip()
                if stripped:
                    last_event_monotonic[0] = time.monotonic()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.debug(
                        "_stream_read_events: skipping non-JSON line %r",
                        stripped[:200],
                    )
                    continue
                if isinstance(parsed, dict):
                    events.append(parsed)
        except Exception as e:  # noqa: BLE001 — best-effort drain
            logger.warning("stream-json reader thread crashed: %s", e, exc_info=True)

    timed_out = False
    idle_aborted = False
    # 775D6752: tracks whether the polling loop exited because the result
    # event was already in hand (clean early-out). When True, we MUST NOT
    # treat the still-alive reader thread as a timeout — the work is done,
    # the daemon reader will be reaped by the finally-block stdout close.
    result_event_seen = False
    try:
        # Start BOTH threads before joining either. HIGH-1: feeder must not
        # block reader and vice-versa; both run concurrently under the
        # single watchdog deadline (HIGH-2).
        feeder_thread = threading.Thread(
            target=_feeder, name="llm-stream-feeder", daemon=True
        )
        reader_thread = threading.Thread(
            target=_reader, name="llm-stream-reader", daemon=True
        )
        # Reader started first so it's already consuming stdout when the
        # feeder begins writing — claude CLI can interleave reads/writes
        # without buffer-fill deadlocks on either side.
        reader_thread.start()
        feeder_thread.start()

        # 775D6752: replace the single reader_thread.join(timeout=_remaining())
        # with a polling loop that checks the idle watchdog between waits.
        # Polling cadence: min(remaining_outer, idle_remaining, 1.0). The 1.0s
        # cap keeps wall-clock overhead low (≤1s of slop on idle detection)
        # while still letting the loop exit promptly when the reader finishes.
        # When idle is DISABLED (idle_enabled=False), the loop reduces to the
        # original single-join behaviour (one wait of remaining_outer).
        # Note: ``_remaining()`` floors at 0.001 so callers never pass 0 to
        # join(); for the loop's outer-budget exit check we compute elapsed
        # directly to detect the actual ceiling crossing.
        deadline = started_monotonic + float(timeout_sec)
        while reader_thread.is_alive():
            now = time.monotonic()
            outer_remaining = deadline - now
            if outer_remaining <= 0:
                break
            # 775D6752: if a type=result event has already arrived we have
            # what we need — exit the polling loop cleanly rather than
            # spuriously aborting on idle. The reader is a daemon thread
            # and the ``finally`` block closes proc.stdout, which unblocks
            # any pending readline (including the test fixtures'
            # ``_TimedStdout.close``-driven shortcut). This mirrors real
            # claude CLI behaviour where stdout closes after result; the
            # branch only fires for fixtures / CLIs that linger past the
            # final result event.
            if _find_last_result_event(events) is not None:
                result_event_seen = True
                break
            if idle_enabled:
                idle_remaining = idle_budget - (now - last_event_monotonic[0])
                if idle_remaining <= 0:
                    idle_aborted = True
                    break
                # Cap effective wait by outer remaining (RED test 11) and a
                # 1s cadence floor so the loop wakes often enough to detect
                # idle promptly.
                wait = min(outer_remaining, idle_remaining, 1.0)
            else:
                # No idle watchdog — cap to a 1s cadence ceiling so result-
                # event detection latency stays bounded (matches the
                # enabled-branch ceiling). Pre-775D6752 used a single
                # remaining-budget join; this still exits promptly when the
                # reader finishes since the loop polls reader_thread.is_alive().
                wait = min(outer_remaining, 1.0)
            # Defensive: never pass <=0 to join (Python treats it as
            # non-blocking poll, but keep semantics explicit).
            reader_thread.join(timeout=max(0.001, wait))

        if idle_aborted:
            # 775D6752: in-stream idle watchdog kill. Record both flags so
            # the caller can branch on idle_aborted while existing code
            # paths that read timed_out continue to work (idle abort is a
            # specialisation of "didn't finish in budget").
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "proc.kill failed under stream-json idle-timeout",
                    exc_info=True,
                )
            timed_out = True
        elif result_event_seen:
            # 775D6752 / F4F26513 clean early-out: result event already
            # received, the reader may still be in a long readline waiting
            # for EOF. The ``finally`` block's stdout.close() will unblock it.
            # Do NOT set timed_out here — the work is done.
            # If proc.wait(timeout=5) below raises TimeoutExpired, the
            # F4F26513 salvage path below calls proc.kill() and sets
            # cli_lingered_after_result=True. On clean exit proc.kill is
            # never called (AC6 — test_success_path_omits_aborted_reason).
            pass
        elif reader_thread.is_alive():
            # Reader still blocked → subprocess hasn't EOF'd within budget.
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                logger.warning("proc.kill failed under stream-json timeout", exc_info=True)
            timed_out = True

        # Feeder may still be alive if write blocked too; give it the
        # remaining budget (often near-zero on timeout, fine — the kill
        # above broke its pipe so write returns BrokenPipeError quickly).
        feeder_thread.join(timeout=_remaining())
        if feeder_thread.is_alive():
            # Feeder still blocked even after kill — abandon as daemon.
            logger.warning(
                "stdin feeder thread still alive after timeout/kill; "
                "abandoning as daemon"
            )
            timed_out = True

        # Wait briefly for the kernel to reap the process so returncode is
        # set correctly. On non-timeout, the subprocess has already EOF'd
        # stdout, so wait() returns immediately. On timeout, we just killed
        # it, so wait() reaps within milliseconds.
        # F4F26513: if result_event_seen=True and proc.wait still times out,
        # the CLI lingered post-result — kill it (salvage path) and set the
        # cli_lingered_after_result flag so the caller can return success.
        try:
            proc.wait(timeout=POST_RESULT_WAIT_SEC)
        except subprocess.TimeoutExpired:
            if result_event_seen:
                # F4F26513 salvage: CLI lingered after emitting result event.
                # Kill it and reap so returncode is set; caller treats as success.
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "proc.kill failed on cli_lingered_post_result salvage path",
                        exc_info=True,
                    )
                try:
                    proc.wait(timeout=POST_RESULT_WAIT_SEC)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "proc.wait (reap) still timed out after kill (zombie?)",
                        exc_info=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "proc.wait (reap) failed on cli_lingered_post_result salvage path",
                        exc_info=True,
                    )
                try:
                    proc.wait(timeout=POST_RESULT_WAIT_SEC)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "proc.wait (reap) failed on cli_lingered_post_result salvage path",
                        exc_info=True,
                    )
                cli_lingered_after_result = True
            else:
                logger.warning("proc.wait still timed out 5s after kill")
        except Exception:  # noqa: BLE001
            logger.warning("proc.wait failed under stream-json", exc_info=True)

        # Drain stderr (may be empty on success). On the streaming path
        # stderr is small — the CLI emits status/debug there, not bulk.
        stderr = ""
        try:
            if proc.stderr is not None:
                raw_stderr = proc.stderr.read()
                if isinstance(raw_stderr, bytes):
                    raw_stderr = raw_stderr.decode("utf-8", errors="replace")
                stderr = raw_stderr if isinstance(raw_stderr, str) else ""
        except Exception:  # noqa: BLE001
            logger.warning("stderr drain failed under stream-json", exc_info=True)

        stdout = "".join(raw_lines)
        return timed_out, stdout, stderr, events, idle_aborted, cli_lingered_after_result
    finally:
        # MED-4: explicit FD close in finally — proc.stdout / proc.stderr
        # are otherwise reaped only via GC, which can leak FDs under
        # repeated invocation. close() is best-effort; swallow errors so
        # cleanup never masks the real result.
        for stream_attr in ("stdout", "stderr"):
            stream = getattr(proc, stream_attr, None)
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass


def _communicate_legacy(
    *,
    proc,
    prompt: str,
    timeout_sec: int,
) -> tuple[bool, str, str]:
    """Single-shot communicate(timeout=) path — preserved for non-stream-json
    callers (caller-supplied --output-format flags AND non-claude commands).

    23680DDA migration only changed the auto-injected claude_p path; this
    legacy path keeps existing semantics so the test_llm_subprocess.py shell
    fixtures (python3 echo / sleep / exit) plus caller-supplied JSON flow
    continue to work unchanged.

    WARNING (HIGH-4 — 23680DDA hardening pass): ``proc.communicate(input=...,
    timeout=timeout_sec)`` BLOCKS at EOF until the subprocess closes stdout.
    Any caller that injects ``--output-format json`` (NON-streaming) will
    re-trigger the 2026-05-06 15min-hang failure mode: claude CLI buffers the
    entire response into a single envelope, so no stdout is visible until the
    model finishes — and if the model legitimately takes long, the caller's
    timeout fires only after the full hang. For hang-free I/O, callers should
    OMIT ``--output-format`` and let auto-injection select stream-json (which
    routes through ``_stream_read_events`` instead). Migrating this legacy
    path to streaming is out of 23680DDA's scope — file a follow-up agreement
    if a phase legitimately needs caller-side ``--output-format json``
    semantics with non-blocking I/O.
    """
    # 0B6671A1: this path is the documented 15-min-hang vector (2026-05-06).
    # Production must route through stream-json (23680DDA auto-inject).
    # Escape hatch: HAL_ALLOW_LEGACY_COMMUNICATE=1 for intentional callers.
    assert (
        "PYTEST_CURRENT_TEST" in os.environ
        or config_provider.flag("HAL_ALLOW_LEGACY_COMMUNICATE")
    ), (
        "_communicate_legacy invoked outside test context — this is the "
        "documented 15-min-hang path. "
        "Route through stream-json (omit --output-format) or set BD_ALLOW_LEGACY_COMMUNICATE=1 (alias: HAL_ALLOW_LEGACY_COMMUNICATE=1)."
    )
    timed_out = False
    stdout = ""
    stderr = ""
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout_sec)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            logger.warning("post-kill drain failed", exc_info=True)
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
            stderr = ""
        timed_out = True
    return timed_out, stdout, stderr


def _find_last_result_event(events: list[dict]) -> dict | None:
    """Return the LAST event with ``type == "result"`` from a stream-json
    event list, or None if no such event exists. Trailing events (e.g. a
    late assistant delta) MUST NOT shadow the result — we walk in reverse.
    GH451: delegates to the registered provider's parse_result.
    """
    return get_provider().parse_result(events)


def _written_paths_from_events(events: list[dict]) -> list[str]:
    """Extract the set of file paths written by the worker from stream-json events.

    Scans events where ``ev.get("type") == "assistant"``, iterates
    ``ev["message"]["content"]`` (only when a list), and collects
    ``block["input"]["file_path"]`` for blocks with:
      - ``block.get("type") == "tool_use"``
      - ``block.get("name") in {"Write", "Edit", "MultiEdit", "NotebookEdit"}``

    For ``NotebookEdit``, falls back to ``input.get("notebook_path")`` when
    ``file_path`` is absent.

    Defensive: tolerates every missing key (best-effort, never raises) — mirrors
    the _find_last_result_event tolerance pattern.  Non-dict blocks, None content,
    missing message, absent input keys — all silently skipped.

    Returns a sorted, deduplicated list (deterministic, mirrors git_diff_files
    contract).  Returns [] when no write-tool blocks are found.

    4961254A: This is the canonical manifest source for commit-steps.  The
    stream-json transcript is the harness's own record of the subprocess's tool
    calls — NOT the worker's self-report — so it is immune to fabrication.
    """
    _WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
    paths: set[str] = set()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") != "assistant":
            continue
        message = ev.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name not in _WRITE_TOOLS:
                continue
            inp = block.get("input")
            if not isinstance(inp, dict):
                continue
            fp = inp.get("file_path")
            if isinstance(fp, str) and fp:
                paths.add(fp)
            elif name == "NotebookEdit":
                nb = inp.get("notebook_path")
                if isinstance(nb, str) and nb:
                    paths.add(nb)
    return sorted(paths)


def _tokens_and_cost_from_events(events: list[dict]) -> tuple[dict | None, float | None]:
    """Pull ``(tokens, cost_usd)`` from the LAST type=result event of a
    stream-json event list. Returns ``(None, None)`` when no usable result
    event is present (mirrors _parse_claude_json's graceful-degrade contract).
    """
    ev = _find_last_result_event(events)
    if ev is None:
        return None, None
    usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
    tokens = None
    if usage:
        tokens = {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_write": usage.get("cache_creation_input_tokens"),
        }
    cost = ev.get("total_cost_usd")
    if cost is not None and not isinstance(cost, (int, float)):
        cost = None
    return tokens, (float(cost) if cost is not None else None)


def _classify_cmd_kind(command: list[str]) -> str:
    """Return 'claude_p' if argv[0] looks like the claude binary, else 'shell'."""
    if not command:
        return "shell"
    head = command[0].rsplit("/", 1)[-1]
    return "claude_p" if "claude" in head else "shell"


def _extract_model(command: list[str]) -> str | None:
    """Return value of --model from argv (supports `--model X` and `--model=X`)."""
    for i, tok in enumerate(command):
        if tok == "--model" and i + 1 < len(command):
            return command[i + 1]
        if tok.startswith("--model="):
            return tok.split("=", 1)[1]
    return None


def _parse_claude_json(command: list[str], stdout: str) -> tuple[dict | None, float | None]:
    """MUST_UPDATE_BOTH: pairs with _extract_result_text — see E6F86B73.

    LEGACY HELPER: Used by the non-stream-json path (caller-supplied
    --output-format json). The 23680DDA stream-json path uses
    _tokens_and_cost_from_events instead.

    If --output-format json is in argv and stdout parses, return (tokens, cost).
    Returns (None, None) when JSON not requested or unparseable — graceful degrade.
    """
    if "--output-format" not in command:
        return None, None
    # require the value after --output-format to be 'json'
    try:
        idx = command.index("--output-format")
        if idx + 1 >= len(command) or command[idx + 1] != "json":
            return None, None
    except ValueError:
        return None, None

    # Parse the LAST non-empty line of stdout. Claude CLI with
    # --output-format json emits a single-line JSON summary at end of stream;
    # any preceding lines (warnings, partial output) are ignored.
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None, None
    try:
        obj = json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError):
        logger.debug("_parse_claude_json: failed to parse last line %r", lines[-1])
        return None, None

    if not isinstance(obj, dict):
        return None, None

    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
    tokens = None
    if usage:
        tokens = {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_write": usage.get("cache_creation_input_tokens"),
        }
    cost = obj.get("total_cost_usd")
    if cost is not None and not isinstance(cost, (int, float)):
        cost = None
    return tokens, (float(cost) if cost is not None else None)


def _extract_result_text(command: list[str], stdout: str) -> str | None:
    """MUST_UPDATE_BOTH: pairs with _parse_claude_json — see E6F86B73.

    LEGACY HELPER: Used by the non-stream-json path (caller-supplied
    --output-format json). The 23680DDA stream-json path uses
    _find_last_result_event(events) instead.

    Return parsed["result"] from claude --output-format json stdout, or None.
    Mirrors _parse_claude_json's last-non-empty-line parsing strategy. Returns
    None when --output-format json is not in argv, last line doesn't parse as a
    JSON object, or the parsed object lacks a string "result" field.
    """
    if "--output-format" not in command:
        return None
    try:
        idx = command.index("--output-format")
        if idx + 1 >= len(command) or command[idx + 1] != "json":
            return None
    except ValueError:
        return None

    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None
    result = obj.get("result")
    return result if isinstance(result, str) else None


def _tail(s: str) -> str:
    """Return last _TAIL_BYTES bytes of s (rough char-based — good enough for ASCII tails)."""
    if not s:
        return ""
    if len(s) <= _TAIL_BYTES:
        return s
    return s[-_TAIL_BYTES:]


_SHORT_BYTES = 200


def _short(s: str) -> str:
    """One-line short version for inclusion in error messages — keeps logs scannable."""
    if not s:
        return "<empty>"
    flat = s.replace("\n", "\\n").replace("\r", "")
    if len(flat) <= _SHORT_BYTES:
        return flat
    return flat[-_SHORT_BYTES:]


def _detect_output_format(command: list[str]) -> str | None:
    """Return value of --output-format flag if 'json' or 'stream-json', else None.

    23680DDA: extended to recognise 'stream-json' so subprocess_spawned event
    payloads accurately reflect the auto-injected format. Other values (e.g.
    'text') still return None — payload semantics: 'json'/'stream-json' means
    "we will parse this for tokens/cost", anything else means "raw passthrough".
    """
    try:
        idx = command.index("--output-format")
    except ValueError:
        return None
    if idx + 1 < len(command):
        val = command[idx + 1]
        if val in ("json", "stream-json"):
            return val
    return None


def _cmd_tail_redacted(command: list[str], n: int = 3) -> list[str]:
    """Return last n args with obvious-secret redaction (last-3 from argv).

    Full argv lives in StepResult.data['command']; this is for error messages
    and event payloads where a short identifier is more useful than the whole list.
    """
    tail = list(command[-n:]) if command else []
    return [_redact_arg(a) for a in tail]


def _redact_arg(arg: str) -> str:
    """Redact a single argv element when it looks like a secret."""
    if not isinstance(arg, str):
        return arg
    lowered = arg.lower()
    for needle in ("api_key=", "apikey=", "token=", "secret=", "password=", "bearer="):
        if needle in lowered:
            head, _, _ = arg.partition("=")
            return f"{head}=<redacted>"
    if arg.startswith(("sk-", "pk-")) and len(arg) >= 20:
        return f"{arg[:6]}<redacted>"
    return arg


def _emit_safe(event_log, event_type: str, payload: dict, run_id: str) -> None:
    """Append event; swallow exceptions (observability must not break execution)."""
    if event_log is None:
        return
    try:
        event_log.append(event_type, payload, run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s (payload keys: %s): %s", event_type, list(payload.keys()), e)


# GH451: taxonomy matchers moved to lib.llm_provider (CLAUDE_PROVIDER); re-exported
# here by name for sibling-test/backward-compat (test_gh426_fable_model_floor.py etc).
from lib.llm_provider import _is_fable_model, _is_opus_model  # noqa: E402


def _resolve_gate_floor(env: "Mapping[str, str] | None" = None) -> str:
    """Resolve the hard-gate model floor. Precedence: env _GATE_FLOOR_ENV_VAR
    (HAL_GATE_MODEL_FLOOR) > models.json claude.gate_floor > provider default_gate_floor.

    Invalid floor token (not in the provider's rank ladder) → WARN-log + fall
    through to the next source. Emits emit_resolver_resolved("gate_floor", source,
    value) on every resolution (source in {"env","config","provider_default"});
    emit errors are swallowed (observability must not break execution, mirrors
    llm_subprocess.py:683/:1019).
    """
    env = config_provider.env_mapping() if env is None else env
    provider = get_provider()
    rank = provider.model_rank

    env_floor = env.get(_GATE_FLOOR_ENV_VAR)
    if env_floor:
        if env_floor in rank:
            floor, source = env_floor, "env"
        else:
            logger.warning("gate_floor_invalid_token: env HAL_GATE_MODEL_FLOOR=%r not in provider rank", env_floor)
            floor, source = None, None
    else:
        floor, source = None, None

    if floor is None:
        config_floor = _model_config.get_gate_floor()
        if config_floor:
            if config_floor in rank:
                floor, source = config_floor, "config"
            else:
                logger.warning("gate_floor_invalid_token: config gate_floor=%r not in provider rank", config_floor)

    # floor/source are always assigned as a pair; the compound check is for
    # mypy's narrowing of `source` (GH485), not a distinct runtime case.
    if floor is None or source is None:
        floor, source = provider.default_gate_floor, "provider_default"

    try:
        emit_resolver_resolved("gate_floor", source, floor, {"floor": floor})
    except Exception:  # noqa: BLE001
        pass

    return floor


def _meets_gate_floor(model: "str | None") -> bool:
    """Return True if *model* meets or exceeds the configured hard-gate floor.

    Floor is resolved via ``_resolve_gate_floor`` (env > models.json > provider
    default) instead of a hardcoded "opus" — GH451 rank-as-config.
    """
    fam = get_provider().model_family(model)
    rank = get_provider().model_rank
    floor = _resolve_gate_floor()
    return fam is not None and rank.get(fam, -1) >= rank.get(floor, rank.get("opus", 2))


_meets_opus_floor = _meets_gate_floor  # backward-compat alias (module-level name kept)


def _assert_in_session_model_or_downgrade(
    result_obj: dict,
    pinned_model: "str | None",
    hard_gate: bool,
    step_name: str,
    nonce: str,
) -> "StepResult | None":
    """Return a downgrade StepResult if a hard_gate in-session step ran below the opus floor, else None.

    Mirrors ``_assert_hard_gate_opus`` for the claude-in-session path. Called after the
    result artifact is read and nonce-validated, before the success StepResult is built.

    Logic:
    - ``hard_gate=False`` → ``None`` (gate bypassed; mechanical step unaffected).
    - ``dispatched_model`` absent from ``result_obj`` → downgrade error (silent-omission bug 02FF48F4/ppba-E5).
    - ``dispatched_model`` present but below the opus floor (per ``_meets_opus_floor``) → downgrade error.
    - Opus-floor dispatched_model (opus or higher-ranked, e.g. fable) → ``None`` (gate passes).

    Comparison via ``_meets_opus_floor`` (NOT raw-equality to ``pinned_model``) because the
    servicer maps the ``.req.json`` ``model`` value (e.g. ``"opus"``) to a Task model id
    (e.g. ``"claude-opus-4-8"``); raw equality would false-positive.
    """
    if not hard_gate:
        return None
    dispatched = result_obj.get("dispatched_model")
    if dispatched is None or not _meets_opus_floor(dispatched):
        msg = (
            f"in-session hard gate '{step_name}' requires opus-floor model; "
            f"servicer dispatched {dispatched!r} — refusing (model downgrade)"
        )
        return StepResult(
            status="error",
            data={"observed_model": dispatched, "pinned_model": pinned_model, "request_nonce": nonce},
            duration_ms=0,
            step_name=step_name,
            error=msg,
            error_code="E_HARD_GATE_MODEL_DOWNGRADE",
            recoverable=False,
        )
    return None


def _model_family(model: "str | None") -> "str | None":
    """Return a canonical family string for *model*, or None if unrecognised.

    Families: "fable", "opus", "sonnet", "haiku". None/empty input → None.
    GH451: delegates to the registered provider's model_family.
    """
    return get_provider().model_family(model)


def _detect_nonhardgate_model_drift(
    result_obj: dict,
    pinned_model: "str | None",
    hard_gate: bool,
    step_name: str,
) -> "dict | None":
    """Return a drift-info dict when a non-hard-gate step ran on a different model family than pinned.

    WARN-only (non-blocking): mirrors ``_assert_in_session_model_or_downgrade`` for the
    non-hard-gate case. Callers emit a ``model_pin_mismatch`` telemetry event but do NOT
    alter control flow (step still proceeds to status="ok"). See GH#222 / agreement 220E5F63.

    Returns None when ANY of:
    - hard_gate is True (hard-gate path already enforced by _assert_in_session_model_or_downgrade);
    - dispatched_model absent/falsy in result_obj;
    - pinned_model is falsy;
    - either model's family is unrecognised (None);
    - the two families are equal (no drift).
    Otherwise returns {"observed_model": dispatched, "pinned_model": pinned_model, "step_name": step_name}.
    """
    if hard_gate:
        return None
    dispatched = result_obj.get("dispatched_model")
    if not dispatched:
        return None
    if not pinned_model:
        return None
    dispatched_family = _model_family(dispatched)
    pinned_family = _model_family(pinned_model)
    if dispatched_family is None or pinned_family is None:
        return None
    if dispatched_family == pinned_family:
        return None
    return {"observed_model": dispatched, "pinned_model": pinned_model, "step_name": step_name}


def _assert_hard_gate_opus(
    command: list[str],
    step_name: str,
    gate_label: str,
    *,
    run_ctx=None,
) -> "StepResult | None":
    """Return None if command passes the Opus hard gate; else return error StepResult.

    Two distinct cases:
    - ``claude_p`` binary with NO ``--model`` flag → FAIL. The claude CLI defaults
      to sonnet/haiku; silent downgrade is exactly the bug surface 24 was meant to fix.
      Error code: ``E_HARD_GATE_MODEL_DOWNGRADE``.
    - ``claude_p`` binary with explicit model below the opus floor → FAIL (same code).
    - Non-``claude_p`` command (test stubs, echo, python3) with any model → PASS.
      Stubs never carry ``--model`` and must not be refused.
    - ``claude_p`` binary with opus-floor model (opus or higher-ranked, e.g. fable) → PASS.

    Emits ``hard_gate_refused`` telemetry event when gate fires and ``run_ctx`` is set.
    Emission errors are swallowed (observability must not break execution).

    Uses anchored match via ``_meets_opus_floor`` to avoid substring false-positives
    (e.g. 'octopus-3').
    """
    cmd_kind = _classify_cmd_kind(command)
    model = _extract_model(command)

    should_fail = False
    if cmd_kind == "claude_p" and model is None:
        # claude binary without --model: CLI defaults to sonnet/haiku — FAIL.
        should_fail = True
    elif model is not None and not _meets_opus_floor(model):
        # Explicit below-floor model on any command kind — FAIL.
        # (Non-claude test stubs with no --model are exempted via the branch above.)
        should_fail = True

    if not should_fail:
        return None

    if model is None:
        msg = (
            f"{gate_label} gate requires opus-floor model; missing `--model` flag — "
            "refusing claude default (not guaranteed to meet the floor)"
        )
    else:
        msg = (
            f"{gate_label} gate requires opus-floor model, got {model!r}; refusing to invoke"
        )

    # Emit dedicated telemetry event for operator observability.
    if run_ctx is not None:
        _emit_safe(run_ctx.event_log, "hard_gate_refused", {
            "phase": run_ctx.phase,
            "step_name": step_name,
            "gate_label": gate_label,
            "observed_model": model,
            "command_kind": cmd_kind,
        }, run_ctx.run_id)

    return StepResult(
        status="error",
        data=None,
        duration_ms=0,
        step_name=step_name,
        error=msg,
        error_code="E_HARD_GATE_MODEL_DOWNGRADE",
        recoverable=False,
    )
