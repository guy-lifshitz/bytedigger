"""RED tests for 95D3E5F6 Step 8 — phase_7_synthesize disk_truth telemetry.

Contract (telemetry-first, additive observability — NO behavior change):

1. phase_7_synthesize module MUST bind ``git_diff_files`` at module scope
   so monkeypatch can locate it next to the call site (mirrors phase_6 pattern).

2. A new helper ``_parse_files_line(raw: str) -> list[str] | None`` parses
   the ``Files:`` line from the post-deploy report's Final Checkpoint section.
   Returns None when absent, [] when present-but-empty.

3. After ``_write_synthesizer_artifact`` returns status="ok", the engine emits
   exactly one ``synthesize_disk_truth`` event with payload:
       {
           "files_actual": list[str],          # from git_diff_files()
           "files_in_synthesis_doc": list[str], # parsed from "Files:" line
           "drift": bool,                       # True iff sets differ
           "doc_path": str,                     # absolute path to report
           "files_line_present": bool,          # False if no Files: line found
           "extra_in_doc": list[str],           # sorted set diff: doc - actual
           "missing_from_doc": list[str],       # sorted set diff: actual - doc
       }

4. NO event emitted when artifact write returns error (e.g. STATUS: BLOCKED).

5. ``_parse_synthesizer_status`` is UNCHANGED — backward-compat guard test
   must be GREEN today and stay GREEN post-impl.

Tests 1–6 MUST FAIL until GREEN ships the additions.
Test 7 (backward-compat) MUST PASS today and stay green post-impl.

Do NOT implement any contract here — RED-only file.
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
    STATUS_DONE,
    STATUS_DONE_WITH_CONCERNS,
    STATUS_BLOCKED,
    STATUS_NEEDS_CONTEXT,
    STATUS_NO_MARKER,
    _write_synthesizer_artifact,
    _parse_synthesizer_status,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
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


def _report_body(files_line: str | None = "Files: a.py, b.py", status: str = "DONE") -> str:
    """Build a minimal but well-formed post-deploy report string."""
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


# ═════════════════════════════════════════════════════════════════════════════
# AC1 — module-level import binding: git_diff_files
# ═════════════════════════════════════════════════════════════════════════════


def test_imports_disk_truth_symbols():
    """phase_7_synthesize module MUST bind ``git_diff_files`` at module scope.

    Currently fails — disk_truth is not imported in phase_7_synthesize.py.
    """
    assert hasattr(phase_7_synthesize, "git_diff_files"), (
        "phase_7_synthesize must `from bytedigger_engine.lib.plugins.disk_truth import git_diff_files` "
        "at module scope (Step 8 disk-truth wiring)"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC2 — _parse_files_line helper exists and parses canonical formats
# ═════════════════════════════════════════════════════════════════════════════


def test_parse_files_line_canonical_formats():
    """_parse_files_line must exist and handle 4 canonical input forms.

    Currently fails — helper not defined in phase_7_synthesize.py.
    """
    helper = getattr(phase_7_synthesize, "_parse_files_line", None)
    assert helper is not None, (
        "phase_7_synthesize._parse_files_line not defined — Step 8 helper missing"
    )

    # Case 1: plain comma-separated
    result = helper("# Post-Deploy Report\n\n## Final Checkpoint\nFiles: a.py, b.py, c.py\nNext: done\n")
    assert result == ["a.py", "b.py", "c.py"], (
        f"expected ['a.py','b.py','c.py'] for plain comma-separated, got {result!r}"
    )

    # Case 2: bracket-wrapped
    result = helper("## Final Checkpoint\nFiles: [a.py, b.py]\n")
    assert result == ["a.py", "b.py"], (
        f"expected ['a.py','b.py'] for bracket-wrapped, got {result!r}"
    )

    # Case 3: present but empty (trailing whitespace)
    result = helper("## Final Checkpoint\nFiles: \n")
    assert result == [], (
        f"expected [] for empty Files: line, got {result!r}"
    )

    # Case 4: no Files: line at all
    result = helper("## Final Checkpoint\nDone: shipped X\nNext: done\n")
    assert result is None, (
        f"expected None when no Files: line present, got {result!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — synthesize_disk_truth emitted on successful write (drift=False)
# ═════════════════════════════════════════════════════════════════════════════


def test_synthesize_disk_truth_emitted_no_drift(tmp_path: Path, monkeypatch):
    """Synthesizer report contains ``Files: a.py, b.py``; git_diff_files returns
    [a.py, b.py]. Engine MUST emit exactly one ``synthesize_disk_truth`` event
    with drift=False, files_line_present=True, extra_in_doc=[], missing_from_doc=[].

    Currently fails — no such telemetry path exists today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: ["a.py", "b.py"], raising=False
    )

    raw = _report_body(files_line="Files: a.py, b.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"

    events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(events) == 1, (
        f"expected exactly 1 synthesize_disk_truth event, got {len(events)}: {captured}"
    )
    payload = events[0]["payload"]
    assert sorted(payload["files_actual"]) == ["a.py", "b.py"], payload
    assert sorted(payload["files_in_synthesis_doc"]) == ["a.py", "b.py"], payload
    assert payload["drift"] is False, payload
    assert payload["files_line_present"] is True, payload
    assert payload["extra_in_doc"] == [], payload
    assert payload["missing_from_doc"] == [], payload
    # doc_path must be present and be a non-empty string
    assert payload.get("doc_path"), payload


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — synthesize_disk_truth emitted with drift=True when LLM list ≠ disk
# ═════════════════════════════════════════════════════════════════════════════


def test_synthesize_disk_truth_emitted_with_drift(tmp_path: Path, monkeypatch):
    """Report claims ``Files: a.py, b.py, x.py``; disk has [a.py, b.py].
    Event MUST have drift=True, extra_in_doc=['x.py'], missing_from_doc=[].

    Currently fails — no telemetry path today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: ["a.py", "b.py"], raising=False
    )

    raw = _report_body(files_line="Files: a.py, b.py, x.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"

    events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(events) == 1, (
        f"expected exactly 1 synthesize_disk_truth event, got {len(events)}: {captured}"
    )
    payload = events[0]["payload"]
    assert payload["drift"] is True, payload
    assert sorted(payload["files_in_synthesis_doc"]) == ["a.py", "b.py", "x.py"], payload
    assert sorted(payload["files_actual"]) == ["a.py", "b.py"], payload
    assert payload["extra_in_doc"] == ["x.py"], payload
    assert payload["missing_from_doc"] == [], payload


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — synthesize_disk_truth emitted when Files: line is absent
# ═════════════════════════════════════════════════════════════════════════════


def test_synthesize_disk_truth_emitted_when_files_line_absent(tmp_path: Path, monkeypatch):
    """Synthesizer report has no ``Files:`` line. Event still emitted with
    files_line_present=False, files_in_synthesis_doc=[]. When disk_actual
    is also [] both sets are empty → drift=False (explicit empty-vs-empty rule).

    Currently fails — no telemetry path today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    # Both doc and disk are empty → drift=False
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: [], raising=False
    )

    # Omit Files: line entirely (files_line=None)
    raw = _report_body(files_line=None, status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"

    events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(events) == 1, (
        f"expected exactly 1 synthesize_disk_truth event, got {len(events)}: {captured}"
    )
    payload = events[0]["payload"]
    assert payload["files_line_present"] is False, payload
    assert payload["files_in_synthesis_doc"] == [], payload
    assert payload["drift"] is False, payload  # empty vs empty = no drift


# ═════════════════════════════════════════════════════════════════════════════
# AC6 — NO event emitted when artifact write returns error (BLOCKED)
# ═════════════════════════════════════════════════════════════════════════════


def test_synthesize_disk_truth_NOT_emitted_on_blocked_status(tmp_path: Path, monkeypatch):
    """Raw synthesizer response ends with STATUS: BLOCKED.
    ``_write_synthesizer_artifact`` returns status='error'.
    NO ``synthesize_disk_truth`` event should be emitted.

    Currently fails — no emit guard today (and no emit at all).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize, "git_diff_files", lambda *a, **kw: ["a.py"], raising=False
    )

    raw = _report_body(files_line="Files: a.py", status="BLOCKED")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "error", (
        f"expected error status for BLOCKED response, got {result.status}"
    )

    disk_truth_events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(disk_truth_events) == 0, (
        f"expected NO synthesize_disk_truth event on BLOCKED, got: {disk_truth_events}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC7 — backward-compat: _parse_synthesizer_status unchanged (PASSES TODAY)
# ═════════════════════════════════════════════════════════════════════════════


def test_parse_synthesizer_status_unchanged_backward_compat():
    """_parse_synthesizer_status must correctly parse all four legacy markers
    plus NO_MARKER. Step 8 is telemetry-only — status-parsing semantics MUST
    stay intact.

    MUST PASS today and stay green post-impl (backward-compat guard).
    """
    assert _parse_synthesizer_status("...\nSTATUS: DONE\n") == STATUS_DONE, (
        "DONE marker not parsed correctly"
    )
    assert _parse_synthesizer_status("...\nSTATUS: DONE_WITH_CONCERNS\n") == STATUS_DONE_WITH_CONCERNS, (
        "DONE_WITH_CONCERNS marker not parsed correctly"
    )
    assert _parse_synthesizer_status("...\nSTATUS: BLOCKED\n") == STATUS_BLOCKED, (
        "BLOCKED marker not parsed correctly"
    )
    assert _parse_synthesizer_status("...\nSTATUS: NEEDS_CONTEXT\n") == STATUS_NEEDS_CONTEXT, (
        "NEEDS_CONTEXT marker not parsed correctly"
    )
    assert _parse_synthesizer_status("noop") == STATUS_NO_MARKER, (
        "missing marker should return STATUS_NO_MARKER"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "--tb=short"]))
