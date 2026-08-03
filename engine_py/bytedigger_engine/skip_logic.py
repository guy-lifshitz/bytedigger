"""Shared skip logic for phases that self-skip when a decision_doc is present.

Used by phase_2_explore and phase_3_clarify (05F83B1B surface 6).

Condition: cfg["decision_doc"] is a non-empty string, cfg["complexity"] == "FEATURE"
(case-insensitive), AND the path actually exists on disk as a non-empty file.

If all three hold → phase returns early with status="ok", data={"skipped": True, ...}.

Path resolution replicates orchestrator's pre-build-gate two-root probe:
    1. If absolute: use as-is (must be an existing non-empty file).
    2. cwd-relative: Path.cwd() / decision_doc.
    3. HAL-root-relative: hal_root() / decision_doc.
First candidate that is_file() + non-empty wins.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult

from bytedigger_engine.config_provider import hal_root as _hal_root_fn  # noqa: E402

logger = logging.getLogger(__name__)

# ─── E6602155: frozen-spec detection helpers ──────────────────────────────────

FROZEN_SPEC_PREFLIGHT_MARKER = "§1-PREFLIGHT"
_AC_TABLE_RE = re.compile(r"(?mi)^#{1,4}\s*(?:§3[^\n]*)?Acceptance Criteria")


def is_frozen_spec_text(text: str) -> bool:
    """Return True iff text contains BOTH an AC-table heading AND the preflight marker."""
    return bool(_AC_TABLE_RE.search(text)) and FROZEN_SPEC_PREFLIGHT_MARKER in text


def detect_frozen_spec(decision_doc_raw: str | None) -> tuple[bool, Path | None]:
    """Detect whether a decision_doc path points to a frozen spec.

    Returns (True, Path) when the file exists and is_frozen_spec_text(text) is True.
    Returns (False, None) on None/empty input, missing file, or non-frozen content.

    Accepts absolute paths outside the containment roots (e.g. test tmp dirs) as a
    fallback when _resolve_decision_doc_path returns None — tests use mkdtemp paths
    that are outside cwd and the HAL root.
    """
    if not decision_doc_raw:
        return (False, None)
    path: Path | None = _resolve_decision_doc_path(decision_doc_raw)
    if path is None:
        # Fallback: accept absolute paths that exist on disk even if outside
        # the containment roots (serves test tmp dirs and abs production paths).
        raw = decision_doc_raw.strip()
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_absolute() and candidate.is_file():
                path = candidate
    if path is None or not path.is_file():
        return (False, None)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return (False, None)
    return (is_frozen_spec_text(text), path)


# ─── Core path resolution ─────────────────────────────────────────────────────


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)


def _resolve_decision_doc_path(decision_doc: str) -> Path | None:
    """Replicate orchestrator's pre-build-gate two-root probe.

    Returns first existing non-empty candidate as an absolute Path, else None.
    Uses is_file() (not exists()) to reject directories and missing paths.
    Empty files (0 bytes) are also rejected — they cannot carry a decision doc.
    Containment guarantee: returned path must be under cwd or the HAL root; ../
    escape attempts AND symlinks pointing outside both roots are rejected
    (Path.resolve() chases the symlink target before _is_contained checks it).

    9B04B9AB ITEM 3 (TOCTOU): a window exists between the is_file()+stat()
    +_is_contained() checks below and the caller's downstream read_text()
    (e.g. phase_45_spec._read_decision_doc_block). Single-process /build
    has no concurrent writer under cwd or the HAL root, so the worst-case
    exploitation requires a co-tenant with write access to one of the
    candidate roots — and even then the impact is reading an attacker-
    controlled file AS a decision doc, not privilege escalation. Documented
    for defense-in-depth; no runtime re-check is added.
    """
    raw = decision_doc.strip()
    if not raw:
        return None
    # 9B04B9AB ITEM 2: reject "~user" prefix (e.g. "~root/secrets.md") so
    # callers cannot probe other users' home dirs via Path.expanduser.
    # Bare "~" and "~/..." (current user) remain allowed.
    if raw.startswith("~") and len(raw) > 1 and raw[1] != "/":
        return None

    def _is_contained(p: Path) -> bool:
        resolved = p.resolve()
        return resolved.is_relative_to(Path.cwd().resolve()) or \
               resolved.is_relative_to(_hal_root_fn())

    p = Path(raw).expanduser()
    if p.is_absolute():
        if p.is_file() and p.stat().st_size > 0 and _is_contained(p):
            return p
        return None

    # cwd-relative
    candidate = Path.cwd() / raw
    if candidate.is_file() and candidate.stat().st_size > 0 and _is_contained(candidate):
        return candidate

    # ~/.claude-relative (matches orchestrator's HOME/.claude root)
    candidate = _hal_root_fn() / raw
    if candidate.is_file() and candidate.stat().st_size > 0 and _is_contained(candidate):
        return candidate

    return None


def frozen_short_circuit_enabled() -> bool:
    """Kill-switch for the GH531 frozen-spec SIMPLE-gate relax. Default ON."""
    return os.environ.get("HAL_FROZEN_SHORT_CIRCUIT", "1") != "0"


def should_skip_phase(cfg: dict) -> tuple[bool, str | None]:
    """Return (skip, decision_doc_path_str | None).

    True when decision_doc resolves to an existing non-empty file AND either:
    - complexity is in ("FEATURE", "COMPLEX"), or
    - complexity == "SIMPLE" AND the kill-switch is ON AND the doc is frozen
      (GH531: is_frozen_spec_text detects an AC-table heading + §1-PREFLIGHT marker).

    When decision_doc resolves but complexity is a non-empty variant that does
    not skip, emits a telemetry event so future variants surface in the event log.
    """
    decision_doc = cfg.get("decision_doc") or ""
    complexity = str(cfg.get("complexity") or "").upper()

    if not decision_doc:
        return False, None

    resolved = _resolve_decision_doc_path(decision_doc)

    if resolved is None:
        return False, None

    if complexity in ("FEATURE", "COMPLEX"):
        return True, str(resolved)

    if complexity == "SIMPLE":
        if frozen_short_circuit_enabled():
            frozen, frozen_path = detect_frozen_spec(str(resolved))
            if frozen:
                return True, str(frozen_path)
        # SIMPLE + (flag OFF OR not frozen) — fall through to drift emit below.

    # decision_doc resolves but complexity variant won't skip — emit drift event.
    if complexity:
        _emit_safe("decision_doc_skip_complexity_drift", {
            "decision_doc": str(resolved),
            "complexity": complexity,
        })
    return False, None


def make_skip_result(step_name: str, decision_doc_path: str) -> StepResult:
    """Return the canonical early-skip StepResult and emit phase_self_skipped telemetry."""
    _emit_safe("phase_self_skipped", {
        "phase": step_name,
        "step_name": step_name,
        "decision_doc": decision_doc_path,
        "reason": "decision_doc present + FEATURE complexity",
    })
    return StepResult(
        status="ok",
        data={
            "skipped": True,
            "reason": "decision_doc present + FEATURE complexity",
            "decision_doc": decision_doc_path,
        },
        duration_ms=0,
        step_name=step_name,
    )


def passthrough_if_skipped(prev: StepResult, step_name: str) -> StepResult | None:
    """If prev was a skip result, return a passthrough so subsequent steps no-op.

    Returns a new StepResult with updated step_name, or None if not a skip.
    """
    if (
        isinstance(prev, StepResult)
        and isinstance(prev.data, dict)
        and prev.data.get("skipped")
    ):
        return StepResult(
            status="ok",
            data=prev.data,
            duration_ms=0,
            step_name=step_name,
        )
    return None
