"""RED tests for F6A13DF2 — phase_7_synthesize ``_emit_synthesize_disk_truth_telemetry``
silent-failure observability fix.

Agreement: F6A13DF2
Pattern: telemetry-first additive observability — NO behavior change.

MUST FAIL today (production has no error-event emit in the outer except).
MUST PASS after GREEN ships the fix.

New event contract:
    Event type: ``synthesize_disk_truth_error``
    Payload shape:
        {
            "error_class": str,   # type(exc).__name__
            "error_msg":   str,   # str(exc)
            "doc_path":    str,   # non-empty; str(prev.data["doc_path"])
        }

The event is emitted INSIDE the outer ``except Exception`` block of
``_emit_synthesize_disk_truth_telemetry`` instead of (or in addition to) the
current logger.warning — so the failure becomes observable in the event log.
Workflow MUST still return status="ok" (telemetry must never break the pipeline).

Per-test expected outcome today (before GREEN):
    test_error_event_emitted_on_parse_files_line_failure  → FAIL  (AC1)
    test_error_event_emitted_on_resolve_scratchpad_failure → FAIL  (AC2)
    test_no_error_event_on_happy_path                     → PASS  (AC3 — negative guard)
    test_error_event_emitted_before_artifact_returns_ok   → FAIL  (AC4)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_7_synthesize  # noqa: E402
from bytedigger_engine.workflows.phase_7_synthesize import (  # noqa: E402
    REPORT_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    FIX_DOC_RELPATH,
    SATISFACTION_DOC_RELPATH,
    _write_synthesizer_artifact,
)


# ─── helpers (self-contained copy of sibling conventions) ─────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    p = repo / "src/placeholder.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# placeholder\n")
    subprocess.run(["git", "add", "src/placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _make_ctx(scratchpad: Path, *, worktree: Path | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if worktree is not None:
        org["current_worktree_path"] = str(worktree)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_spec(scratchpad: Path) -> None:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## US1\nAdd foo\n")


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture every _emit_safe call in phase_7_synthesize."""
    captured: list[dict] = []

    def _capture(event_type, payload):
        captured.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(phase_7_synthesize, "_emit_safe", _capture)
    return captured


def _make_prev(scratchpad: Path, raw: str) -> StepResult:
    """Build the prev StepResult that _write_synthesizer_artifact expects."""
    report_path = scratchpad / REPORT_DOC_RELPATH
    spec_path = scratchpad / SPEC_DOC_RELPATH
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(report_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(scratchpad / REVIEW_DOC_RELPATH),
            "fix_doc_path": str(scratchpad / FIX_DOC_RELPATH),
            "satisfaction_doc_path": str(scratchpad / SATISFACTION_DOC_RELPATH),
        },
        duration_ms=0,
        step_name="invoke_synthesizer_llm",
    )


def _report_body(files_line: str | None = "Files: a.py", status: str = "DONE") -> str:
    """Build a minimal well-formed post-deploy report string."""
    files_part = f"\n{files_line}" if files_line is not None else ""
    return (
        "# Post-Deploy Report\n"
        "\n"
        "## Final Checkpoint\n"
        f"Done: shipped X{files_part}\n"
        "Review: PASS\n"
        "Docs: updated\n"
        "Next: done\n"
        "\n"
        "## Learnings\n"
        "- observed something\n"
        "\n"
        f"STATUS: {status}\n"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — error event emitted when _parse_files_line raises
# ═══════════════════════════════════════════════════════════════════════════════


def test_error_event_emitted_on_parse_files_line_failure(tmp_path: Path, monkeypatch):
    """When ``_parse_files_line`` raises RuntimeError, the outer except in
    ``_emit_synthesize_disk_truth_telemetry`` MUST emit a
    ``synthesize_disk_truth_error`` event instead of silently warning.

    Expectations:
    - ``_write_synthesizer_artifact`` still returns status="ok" (workflow safe).
    - Exactly one ``synthesize_disk_truth_error`` event captured.
    - Payload contains error_class="RuntimeError", error_msg contains "boom-ac1",
      doc_path == str(prev.data["doc_path"]).
    - NO ``synthesize_disk_truth`` (success) event emitted.

    MUST FAIL today — production outer except only logs; does not emit.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)

    # Force inner helper to raise so the outer except fires.
    monkeypatch.setattr(
        phase_7_synthesize,
        "_parse_files_line",
        lambda raw: (_ for _ in ()).throw(RuntimeError("boom-ac1")),
    )
    # git_diff_files should not be reached, but patch anyway for safety.
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: ["a.py"], raising=False
    )

    raw = _report_body(files_line="Files: a.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", (
        f"artifact write must return ok even when telemetry helper fails; got {result.status} ({result.error!r})"
    )

    error_events = [e for e in captured if e["type"] == "synthesize_disk_truth_error"]
    assert len(error_events) == 1, (
        f"expected exactly 1 synthesize_disk_truth_error event, got {len(error_events)}; all events: {captured}"
    )

    payload = error_events[0]["payload"]
    assert payload.get("error_class") == "RuntimeError", (
        f"error_class must be 'RuntimeError', got {payload.get('error_class')!r}"
    )
    assert "boom-ac1" in str(payload.get("error_msg", "")), (
        f"error_msg must contain 'boom-ac1', got {payload.get('error_msg')!r}"
    )
    expected_doc_path = str(prev.data["doc_path"])
    assert payload.get("doc_path") == expected_doc_path, (
        f"doc_path must be {expected_doc_path!r}, got {payload.get('doc_path')!r}"
    )

    success_events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(success_events) == 0, (
        f"NO synthesize_disk_truth (success) event should be emitted on error; got: {success_events}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — error event emitted when _resolve_scratchpad raises
# ═══════════════════════════════════════════════════════════════════════════════


def test_error_event_emitted_on_resolve_scratchpad_failure(tmp_path: Path, monkeypatch):
    """When ``_resolve_scratchpad`` raises ValueError, the outer except MUST
    emit a ``synthesize_disk_truth_error`` event (not just log a warning).

    Expectations:
    - ``_write_synthesizer_artifact`` returns status="ok".
    - Exactly one ``synthesize_disk_truth_error`` event with
      error_class="ValueError" and error_msg containing "boom-ac2-scratchpad".
    - NO success event emitted.

    MUST FAIL today — production outer except only logs; does not emit.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)

    # _resolve_scratchpad is the very first call inside the try block — raising
    # here guarantees the outer except fires before any other internal work.
    monkeypatch.setattr(
        phase_7_synthesize,
        "_resolve_scratchpad",
        lambda ctx: (_ for _ in ()).throw(ValueError("boom-ac2-scratchpad")),
    )

    raw = _report_body(files_line="Files: a.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", (
        f"artifact write must return ok even when telemetry scratchpad resolution fails; got {result.status}"
    )

    error_events = [e for e in captured if e["type"] == "synthesize_disk_truth_error"]
    assert len(error_events) == 1, (
        f"expected exactly 1 synthesize_disk_truth_error event, got {len(error_events)}; all: {captured}"
    )

    payload = error_events[0]["payload"]
    assert payload.get("error_class") == "ValueError", (
        f"error_class must be 'ValueError', got {payload.get('error_class')!r}"
    )
    assert "boom-ac2-scratchpad" in str(payload.get("error_msg", "")), (
        f"error_msg must contain 'boom-ac2-scratchpad', got {payload.get('error_msg')!r}"
    )
    # doc_path must still be present and non-empty (captured before the raise)
    assert payload.get("doc_path"), (
        f"doc_path must be present and non-empty in error payload, got {payload.get('doc_path')!r}"
    )

    success_events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(success_events) == 0, (
        f"NO synthesize_disk_truth (success) event should be emitted on error; got: {success_events}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — no error event on happy path (negative / backward-compat guard)
# ═══════════════════════════════════════════════════════════════════════════════


def test_no_error_event_on_happy_path(tmp_path: Path, monkeypatch):
    """When the telemetry helper succeeds, ZERO ``synthesize_disk_truth_error``
    events must be emitted. Exactly one ``synthesize_disk_truth`` (success) event
    must be present.

    This guards against the GREEN implementation accidentally emitting the error
    event on the normal code path.

    MUST PASS today (no error event is emitted at all currently) and stay
    green after GREEN ships.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    # Patch git_diff_files to return a known list (mirrors sibling test convention).
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: ["a.py"], raising=False
    )

    raw = _report_body(files_line="Files: a.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status}"

    error_events = [e for e in captured if e["type"] == "synthesize_disk_truth_error"]
    assert len(error_events) == 0, (
        f"ZERO synthesize_disk_truth_error events expected on happy path; got: {error_events}"
    )

    # After GREEN ships, exactly one success event should also be present.
    # We assert >= 0 here so this test is backward-compat today (no events at all).
    # The sibling test file (test_phase_7_step8_disk_truth.py) already covers the
    # exact-one-success-event assertion on the happy path.


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — error event emitted before _write_synthesizer_artifact returns ok
# (ordering / synchronous-call guard)
# ═══════════════════════════════════════════════════════════════════════════════


def test_error_event_emitted_before_artifact_returns_ok(tmp_path: Path, monkeypatch):
    """Same setup as AC1 (_parse_files_line raises RuntimeError).

    Explicit ordering assertion: because ``_write_synthesizer_artifact`` is
    synchronous, the ``synthesize_disk_truth_error`` event MUST be present in
    ``captured`` at the moment the call returns. This test verifies that the
    event is not deferred to a callback or background thread.

    In practice this is the same check as AC1's "exactly one error event" test,
    but documented separately to make the ordering contract explicit. We verify:
    - ``result.status == "ok"`` (workflow safe).
    - ``len(error_events) >= 1`` BEFORE any further code runs after the call.

    MUST FAIL today — production outer except only logs; does not emit.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)

    monkeypatch.setattr(
        phase_7_synthesize,
        "_parse_files_line",
        lambda raw: (_ for _ in ()).throw(RuntimeError("boom-ac1")),
    )
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: ["a.py"], raising=False
    )

    raw = _report_body(files_line="Files: a.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)

    # Ordering check: captured must already contain the error event immediately
    # after the synchronous call returns — no deferred emission allowed.
    assert result.status == "ok", f"expected ok, got {result.status}"

    error_events = [e for e in captured if e["type"] == "synthesize_disk_truth_error"]
    assert len(error_events) >= 1, (
        f"synthesize_disk_truth_error event must be captured synchronously before "
        f"_write_synthesizer_artifact returns; captured so far: {captured}"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "--tb=short"]))
