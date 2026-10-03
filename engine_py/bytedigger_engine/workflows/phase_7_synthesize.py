"""Phase 7 (synthesize) as a WorkflowDefinition.

bd#89 P3c: the post-deploy report is written deterministically from the event
log and the scratchpad. No model, no tool, no subprocess and no network, so
subscription mode and API mode behave identically.

Steps (1):
    1. write_post_deploy_report — resolve the scratchpad, classify the spec /
       review / fix / satisfaction docs (PRESENT / MISSING / NOT_ASSESSED),
       read the completed phases (and, opt-in, the telemetry digest) from the
       event log, render ``_build_report_text`` and atomically write
       ``post-deploy/post-deploy-report.md``.

The step never errors and never raises. A missing doc is "not assessed" in the
report; an unresolvable scratchpad, a render fault or a write fault emits
``post_deploy_report_skipped`` (reason ``no_scratchpad`` / ``render_failed`` /
``write_failed``) and the step still returns ``ok`` with ``report_written``
False. Phase 8 falls back to the commit subject when the file is missing.

The retired org keys ``synthesizer_model``, ``synthesizer_llm_command`` and
``synthesizer_llm_timeout_sec`` are ignored; when any is set the step emits one
``synthesizer_config_ignored`` event before it renders, on every branch.

Inputs (via ``ctx.org_config``):
    scratchpad_dir           — Absolute path to scratchpad root.
    include_telemetry_digest — Optional. Adds a ``## Telemetry`` section.

``ctx.question`` carries the user's feature request text (``None`` is treated
as an empty request).

The same step derives ``reviews/learnings-raw.md`` (op9, ``_build_learnings_text``)
from the review doc and the event log, independent of the report outcome; it
does not run when the scratchpad cannot be resolved. Failures emit
``learnings_raw_skipped`` (``render_failed`` / ``write_failed``), never an error.

Outputs:
    $SCRATCHPAD/post-deploy/post-deploy-report.md
    $SCRATCHPAD/reviews/learnings-raw.md
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
from bytedigger_engine.derive_state import query_run_events
from bytedigger_engine import telemetry_ctx

from bytedigger_engine.io_utils import atomic_write  # noqa: E402

REPORT_DOC_RELPATH = "post-deploy/post-deploy-report.md"
SPEC_DOC_RELPATH = "specs/build-spec.md"
REVIEW_DOC_RELPATH = "reviews/build-review.md"
FIX_DOC_RELPATH = "reviews/build-fix.md"
SATISFACTION_DOC_RELPATH = "reviews/build-satisfaction.md"
LEARNINGS_RAW_RELPATH = "reviews/learnings-raw.md"

# GH1626 B: the satisfaction artifact has THREE states, not two. A stub left by
# a phase-6 abort is a file, so `is_file()` alone would report acceptance
# evidence that was never produced. The marker is phase 6's single emission
# point — see workflows/phase_6_review.NOT_ASSESSED_MARKER.
SATISFACTION_NOT_ASSESSED_MARKER = "SATISFACTION: NOT_ASSESSED"
SATISFACTION_PRESENT = "PRESENT"
SATISFACTION_MISSING = "MISSING"
SATISFACTION_NOT_ASSESSED = "NOT_ASSESSED"

_DONE_MAX_CHARS = 100
_IGNORED_ORG_KEYS = ("synthesizer_model", "synthesizer_llm_command", "synthesizer_llm_timeout_sec")

logger = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_7_synthesize")
    return Path(raw).expanduser().resolve()


def _telemetry_digest(ctx) -> str:
    """Per-phase wall + cost summary for the report (9AB90CA0).

    Opt-in via ``org_config.include_telemetry_digest=True`` so existing
    tests stay deterministic. Reads default build-events.jsonl, filters
    to workflow_finished + subprocess_exited events from phases other
    than phase_7_synthesize itself, and renders ≤10 lines.

    Best-effort: any error returns empty string so synthesis never fails
    on telemetry issues.
    """
    cfg = ctx.org_config or {}
    if not cfg.get("include_telemetry_digest"):
        return ""
    try:
        finished = query_run_events(event_type="workflow_finished")
        exited = query_run_events(event_type="subprocess_exited")
    except Exception:
        return ""
    if not finished and not exited:
        return ""

    phase_wall: dict[str, int] = {}
    for evt in finished:
        payload = evt.get("payload") or {}
        wn = payload.get("workflow_name", "")
        if not wn or wn == "phase_7_synthesize":
            continue
        wall = payload.get("wall_ms")
        if isinstance(wall, int):
            phase_wall[wn] = wall  # latest occurrence wins

    phase_cost: dict[str, float] = {}
    phase_n: dict[str, int] = {}
    for evt in exited:
        payload = evt.get("payload") or {}
        ph = payload.get("phase", "")
        if not ph or ph == "phase_7_synthesize":
            continue
        cost = payload.get("cost_usd")
        if isinstance(cost, (int, float)):
            phase_cost[ph] = phase_cost.get(ph, 0.0) + float(cost)
        phase_n[ph] = phase_n.get(ph, 0) + 1

    phases = sorted(set(phase_wall) | set(phase_cost))
    if not phases:
        return ""

    lines = ["TELEMETRY (per-phase wall + cost from build-events.jsonl):"]
    for ph in phases:
        wall_s = (phase_wall.get(ph, 0) or 0) / 1000.0
        cost = phase_cost.get(ph, 0.0)
        n = phase_n.get(ph, 0)
        cost_part = f", ${cost:.4f} / {n} subproc" if n else ""
        lines.append(f"  - {ph}: {wall_s:.1f}s{cost_part}")
    total_cost = sum(phase_cost.values())
    total_n = sum(phase_n.values())
    if total_n:
        lines.append(f"  Total subprocess: ${total_cost:.4f} / {total_n} invocations")
    return "\n".join(lines)


def _collect_completed_phases(ctx) -> list[str]:
    """Deduped, first-seen-order list of completed workflow phases (GH450).

    Best-effort: any error returns [] so synthesis never fails on this.
    """
    try:
        finished = query_run_events(event_type="workflow_finished")
    except Exception:
        return []
    if not finished:
        return []

    phases: list[str] = []
    seen: set[str] = set()
    for evt in finished:
        payload = evt.get("payload") or {}
        wn = payload.get("workflow_name", "")
        if not wn or wn == "phase_7_synthesize":
            continue
        if wn in seen:
            continue
        seen.add(wn)
        phases.append(wn)
    return phases


def _completed_phase_digest(ctx) -> str:
    """Engine-derived completed-phase evidence (GH450).

    Best-effort: any error returns empty string so synthesis never fails
    on digest issues (mirrors _telemetry_digest semantics).
    """
    phases = _collect_completed_phases(ctx)
    if not phases:
        return ""
    lines = ["COMPLETED PHASES (engine-derived from event log — authoritative):"]
    for ph in phases:
        lines.append(f"  - {ph}")
    return "\n".join(lines)


def _satisfaction_state(sat_doc: Path) -> str:
    """Classify the satisfaction artifact as PRESENT / MISSING / NOT_ASSESSED.

    NOT_ASSESSED is decided by phase 6's own marker and nothing else: an
    unreadable or unrecognised body is reported as it stands, never guessed at.
    """
    if not sat_doc.is_file():
        return SATISFACTION_MISSING
    try:
        body = sat_doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return SATISFACTION_MISSING
    if SATISFACTION_NOT_ASSESSED_MARKER in body:
        return SATISFACTION_NOT_ASSESSED
    return SATISFACTION_PRESENT


_ARTIFACT_RELPATHS = (
    ("spec", SPEC_DOC_RELPATH),
    ("review", REVIEW_DOC_RELPATH),
    ("fix", FIX_DOC_RELPATH),
    ("satisfaction", SATISFACTION_DOC_RELPATH),
)


def _done_line(question: str | None) -> str:
    """First non-empty request line, whitespace-collapsed, cut to 100 chars; '' if none."""
    for raw in str(question or "").splitlines():
        collapsed = " ".join(raw.split())
        if collapsed:
            return collapsed[:_DONE_MAX_CHARS]
    return ""


def _build_report_text(
    question: str | None,
    completed_phases: list[str],
    telemetry_digest: str,
    artifacts: dict[str, str],
) -> str:
    """Render the post-deploy report. Pure: no I/O, no clock."""
    lines: list[str] = ["# Post-Deploy Report", "", "## Final Checkpoint"]
    done = _done_line(question)
    if done:
        lines.append(f"Done: {done}")
    lines.append("Files: not assessed (deterministic report; see `git diff --stat`)")
    lines.append(
        f"Review: {artifacts.get('review', SATISFACTION_MISSING)}; "
        f"satisfaction: {artifacts.get('satisfaction', SATISFACTION_MISSING)}"
    )
    lines.append("Docs: See /ship cascade.")
    lines.append("Next: manual test / PR / done")
    lines.append("")

    lines.append("## Completed Phases")
    if completed_phases:
        lines.extend(f"- {ph}" for ph in completed_phases)
    else:
        lines.append("none recorded")
    lines.append("")

    if telemetry_digest:
        lines.append("## Telemetry")
        lines.append(telemetry_digest)
        lines.append("")

    lines.append("## Artifacts")
    for name, _rel in _ARTIFACT_RELPATHS:
        lines.append(f"- {name}: {artifacts.get(name, SATISFACTION_MISSING)}")
    lines.append("")

    lines.append("## Concerns")
    gaps = [
        f"- {name} not assessed: {rel} is {artifacts.get(name, SATISFACTION_MISSING)}"
        for name, rel in _ARTIFACT_RELPATHS
        if artifacts.get(name, SATISFACTION_MISSING) != SATISFACTION_PRESENT
    ]
    if gaps:
        lines.extend(gaps)
    else:
        lines.append("none")
    return "\n".join(lines) + "\n"


# ─── Step 1: write the post-deploy report ────────────────────────────────────


def _skipped(reason: str, error: Exception, extra: dict | None = None) -> StepResult:
    _emit_safe("post_deploy_report_skipped", {"reason": reason, "error": str(error)})
    data: dict = {"report_written": False}
    data.update(extra or {})
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="write_post_deploy_report",
    )


# ─── op9: derived reviews/learnings-raw.md ───────────────────────────────────

_FINDING_HEADER_RE = re.compile(
    r"^###\s+SEVERITY:\s+(\[UNVERIFIED\]\s+)?([A-Za-z]+)\s+(?:—|--|-)\s+(.+?)\s*$"
)
_VERIFY_TAG_RE = re.compile(r"\[verify:[^\]]*\]")
_MAX_FINDINGS = 10
_MAX_TITLE_CHARS = 200
_LEARNINGS_HEADING = "# Learnings (derived from the build record, not authored)"
_NOT_DERIVED_LINE = "# not derived: no review findings or process anomalies recorded"


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _count_field(audit: dict, key: str) -> int:
    v = audit.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        return 0
    return v if v > 0 else 0


def _build_learnings_text(review_text: str | None, event_facts: dict, artifacts: dict) -> str:
    """Render reviews/learnings-raw.md from the build record. Pure: no I/O, no clock."""
    entries: list[str] = []

    if review_text:
        in_refuted = False
        found = 0
        for line in review_text.splitlines():
            if line.startswith("## "):
                in_refuted = line.strip().startswith("## Refuted (Semantic)")
                continue
            if in_refuted or found >= _MAX_FINDINGS:
                continue
            m = _FINDING_HEADER_RE.match(line.strip())
            if not m:
                continue
            title = _one_line(_VERIFY_TAG_RE.sub("", m.group(3)))[:_MAX_TITLE_CHARS].strip()
            if not title:
                continue
            level = m.group(2).upper()
            prefix = f"UNVERIFIED {level}" if m.group(1) else level
            entries.append(f"- [review-finding] --- {prefix} finding: {title}")
            found += 1

    audit = event_facts.get("audit")
    if isinstance(audit, dict):
        n = _count_field(audit, "lost_to_prose")
        if n:
            entries.append(f"- [review-audit] --- {n} review findings were written in prose and not parsed")
        n = _count_field(audit, "malformed_headers")
        if n:
            entries.append(f"- [review-audit] --- {n} review finding headers were malformed")
        n = _count_field(audit, "filtered")
        if n:
            entries.append(f"- [review-audit] --- {n} review findings were filtered because the cited text was absent")
        if audit.get("suspect_withhold"):
            entries.append("- [review-audit] --- review findings were withheld as suspect")

    n = int(event_facts.get("watchdog_count") or 0)
    if n > 0:
        entries.append(f"- [fix-process] --- fix watchdog reported no progress {n} time(s)")
    n = int(event_facts.get("infra_count") or 0)
    if n > 0:
        reason = _one_line(event_facts.get("infra_last_reason") or "unknown")
        entries.append(
            f"- [fix-process] --- post-fix pytest infra error {n} time(s), last reason: {reason}"
        )

    if artifacts.get("satisfaction") == SATISFACTION_NOT_ASSESSED:
        entries.append(
            "- [acceptance] --- acceptance was not assessed: "
            "phase 6 stopped before the satisfaction evaluator ran"
        )

    lines = [_LEARNINGS_HEADING]
    if entries:
        lines.extend(entries)
    else:
        lines.append(_NOT_DERIVED_LINE)
    return "\n".join(lines) + "\n"


def _collect_event_facts() -> dict:
    audits = query_run_events(event_type="review_findings_audit")
    watchdog = query_run_events(event_type="fix_watchdog_no_progress")
    infra = query_run_events(event_type="post_fix_pytest_infra_error")
    last_audit = (audits[-1].get("payload") or {}) if audits else None
    last_reason = ""
    if infra:
        last_reason = (infra[-1].get("payload") or {}).get("reason", "") or ""
    return {
        "audit": last_audit,
        "watchdog_count": len(watchdog),
        "infra_count": len(infra),
        "infra_last_reason": last_reason,
    }


def _write_learnings_raw(scratchpad: Path) -> dict:
    """op9. Degrades, never raises. Independent of the report render/write outcome."""
    result: dict = {"learnings_written": False, "learnings_entries": 0, "learnings_raw_path": None}
    raw_path = scratchpad / LEARNINGS_RAW_RELPATH
    try:
        try:
            sat_state: str | None = _satisfaction_state(scratchpad / SATISFACTION_DOC_RELPATH)
        except Exception:  # noqa: BLE001
            sat_state = None
        review_text: str | None = None
        review_path = scratchpad / REVIEW_DOC_RELPATH
        try:
            if review_path.is_file():
                review_text = review_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            review_text = None
        try:
            facts = _collect_event_facts()
        except Exception:  # noqa: BLE001
            facts = {}
        text = _build_learnings_text(review_text, facts, {"satisfaction": sat_state})
        text = text.encode("utf-8", "replace").decode("utf-8")
        entries = sum(1 for ln in text.splitlines() if ln.startswith("- ["))
    except Exception as e:  # noqa: BLE001
        _emit_safe("learnings_raw_skipped", {"reason": "render_failed", "error": str(e)})
        return result

    try:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(raw_path, text)
    except Exception as e:  # noqa: BLE001
        _emit_safe("learnings_raw_skipped", {"reason": "write_failed", "error": str(e)})
        return result

    _emit_safe("learnings_raw_written", {"path": str(raw_path), "entries": entries})
    result.update(
        learnings_written=True, learnings_entries=entries, learnings_raw_path=str(raw_path)
    )
    return result


def _write_post_deploy_report(ctx, prev) -> StepResult:
    cfg = ctx.org_config or {}
    ignored = sorted(k for k in _IGNORED_ORG_KEYS if k in cfg)
    if ignored:
        _emit_safe("synthesizer_config_ignored", {"keys": ignored})

    no_learnings = {"learnings_written": False, "learnings_entries": 0, "learnings_raw_path": None}
    try:
        scratchpad = _resolve_scratchpad(ctx)
    except ValueError as e:
        return _skipped("no_scratchpad", e, no_learnings)

    learnings = _write_learnings_raw(scratchpad)

    try:
        paths = {name: scratchpad / rel for name, rel in _ARTIFACT_RELPATHS}
        states = {
            "spec": "PRESENT" if paths["spec"].is_file() else "MISSING",
            "review": "PRESENT" if paths["review"].is_file() else "MISSING",
            "fix": "PRESENT" if paths["fix"].is_file() else "MISSING",
            "satisfaction": _satisfaction_state(paths["satisfaction"]),
        }
        completed = _collect_completed_phases(ctx)
        digest = _telemetry_digest(ctx)
        text = _build_report_text(ctx.question, completed, digest, states)
        # Lone surrogates (e.g. from a request line) cannot be written as UTF-8.
        text = text.encode("utf-8", "replace").decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return _skipped("render_failed", e, learnings)

    report_path = scratchpad / REPORT_DOC_RELPATH
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(report_path, text)
    except Exception as e:  # noqa: BLE001
        return _skipped("write_failed", e, learnings)

    report_bytes = len(text.encode("utf-8"))
    gaps = [name for name, _rel in _ARTIFACT_RELPATHS if states[name] != SATISFACTION_PRESENT]
    _emit_safe(
        "synthesize_report_written",
        {"doc_path": str(report_path), "report_bytes": report_bytes, "gaps": gaps},
    )
    return StepResult(
        status="ok",
        data={
            "report_doc_path": str(report_path),
            "spec_path": str(paths["spec"]),
            "review_doc_path": str(paths["review"]),
            "fix_doc_path": str(paths["fix"]),
            "satisfaction_doc_path": str(paths["satisfaction"]),
            "report_bytes_written": report_bytes,
            "report_written": True,
            "artifact_states": states,
            "completed_phases": completed,
            **learnings,
        },
        duration_ms=0,
        step_name="write_post_deploy_report",
    )


# ─── workflow definition ─────────────────────────────────────────────────────


def phase_7_synthesize_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_7_synthesize",
        steps=[
            StepContract(name="write_post_deploy_report", execute=_write_post_deploy_report),
        ],
    )
