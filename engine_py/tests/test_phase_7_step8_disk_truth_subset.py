"""RED tests for AC73D749 — subset semantics for synthesize_disk_truth drift signal.

Agreement: AC73D749
Contract: drift=True iff doc_set ⊄ disk_set (LLM fabricated a file not on disk).
Omission side (disk has files not in doc) stays as informative metadata but MUST NOT
trigger drift.

Prediction vs expected RED state:
- AC1: FAIL today (current code: drift = disk_set != doc_set → True when disk has extras;
       new contract wants False when doc ⊆ disk)
- AC2: PASS today (fabrication-only case already fires drift=True — regression guard)
- AC3: PASS today (mixed case: fabrication present → drift=True regardless — regression guard)
- AC4: PASS today (equal sets → drift=False — regression guard)
- AC5: PASS today (both empty → drift=False — regression guard)

Finding origin: F6A13DF2 ship surfaced this finding via $0 replay validation against
production scratchpad forge-1778331969-153ddb12 (5 disk paths vs 1 in doc → drift=True;
4 were unrelated noise).

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
    _write_synthesizer_artifact,
)


# ─── helpers (copied verbatim from sibling test_phase_7_step8_disk_truth.py) ──


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
# AC1 — drift=False when doc is a strict subset of actual (KEY RED GATE)
#
# MUST FAIL today: current code uses disk_set != doc_set, so when disk has
# extra files not in doc, drift fires True. New contract: drift=True iff
# doc_set ⊄ disk_set (only fabrication triggers drift).
# ═════════════════════════════════════════════════════════════════════════════


def test_drift_false_when_doc_is_subset_of_actual(tmp_path: Path, monkeypatch):
    """Doc claims ``Files: a.py``; disk returns [a.py, noise.md, b.json].

    Doc is a strict subset of disk (no fabrication). New contract: drift=False.
    Current code (disk_set != doc_set) yields drift=True → this test MUST FAIL
    today and become GREEN after the flip to subset semantics.

    Also asserts extra_in_doc==[] and missing_from_doc preserves the omission
    side as informative metadata (sorted).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    disk_files = ["a.py", "noise.md", "b.json"]
    monkeypatch.setattr(
        phase_7_synthesize,
        "git_diff_files",
        lambda *a, **kw: list(disk_files),
        raising=False,
    )

    raw = _report_body(files_line="Files: a.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"

    events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(events) == 1, (
        f"expected exactly 1 synthesize_disk_truth event, got {len(events)}: {captured}"
    )
    payload = events[0]["payload"]

    # The key assertion — FAILS today (current code returns True)
    assert payload["drift"] is False, (
        f"drift must be False when doc ⊆ disk (no fabrication); got payload={payload}"
    )
    # No fabrication
    assert payload["extra_in_doc"] == [], (
        f"expected extra_in_doc=[] (no fabrication), got {payload['extra_in_doc']!r}"
    )
    # Omission side preserved as informative metadata (sorted)
    assert payload["missing_from_doc"] == ["b.json", "noise.md"], (
        f"expected missing_from_doc=['b.json','noise.md'], got {payload['missing_from_doc']!r}"
    )
    # files_in_synthesis_doc reflects parsed doc
    assert payload["files_in_synthesis_doc"] == ["a.py"], (
        f"expected files_in_synthesis_doc=['a.py'], got {payload['files_in_synthesis_doc']!r}"
    )
    # files_actual matches what git_diff_files returned (sorted for comparison)
    assert sorted(payload["files_actual"]) == sorted(disk_files), (
        f"expected files_actual={sorted(disk_files)!r}, got {payload['files_actual']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC2 — drift=True when doc claims a file NOT on disk (fabrication)
#
# PASSES today (current symmetric diff already catches fabrication).
# Regression guard: must stay GREEN after subset-semantics flip.
# ═════════════════════════════════════════════════════════════════════════════


def test_drift_true_when_doc_claims_file_not_on_disk(tmp_path: Path, monkeypatch):
    """Doc claims ``Files: a.py, ghost.py``; disk returns [a.py].

    ghost.py is a fabrication (in doc, not on disk). drift MUST be True.
    Regression guard: subset semantics (doc_set ⊄ disk_set) still fires here.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize,
        "git_diff_files",
        lambda *a, **kw: ["a.py"],
        raising=False,
    )

    raw = _report_body(files_line="Files: a.py, ghost.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"

    events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(events) == 1, (
        f"expected exactly 1 synthesize_disk_truth event, got {len(events)}: {captured}"
    )
    payload = events[0]["payload"]

    assert payload["drift"] is True, (
        f"drift must be True when doc claims ghost.py not on disk; got payload={payload}"
    )
    assert payload["extra_in_doc"] == ["ghost.py"], (
        f"expected extra_in_doc=['ghost.py'], got {payload['extra_in_doc']!r}"
    )
    assert payload["missing_from_doc"] == [], (
        f"expected missing_from_doc=[], got {payload['missing_from_doc']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — drift=True when doc has BOTH a fabrication AND disk has extras
#
# PASSES today. Regression guard: subset semantics keeps drift=True here
# because doc_set ⊄ disk_set (ghost.py ∈ doc but ∉ disk).
# ═════════════════════════════════════════════════════════════════════════════


def test_drift_true_when_fabrication_and_disk_extras(tmp_path: Path, monkeypatch):
    """Doc claims ``Files: a.py, ghost.py``; disk returns [a.py, noise.md].

    ghost.py is a fabrication; noise.md is unrelated disk noise.
    drift MUST be True (ghost.py ∈ doc_set but ∉ disk_set → doc_set ⊄ disk_set).
    extra_in_doc=['ghost.py'], missing_from_doc=['noise.md'].
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize,
        "git_diff_files",
        lambda *a, **kw: ["a.py", "noise.md"],
        raising=False,
    )

    raw = _report_body(files_line="Files: a.py, ghost.py", status="DONE")
    prev = _make_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad, worktree=repo)

    result = _write_synthesizer_artifact(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"

    events = [e for e in captured if e["type"] == "synthesize_disk_truth"]
    assert len(events) == 1, (
        f"expected exactly 1 synthesize_disk_truth event, got {len(events)}: {captured}"
    )
    payload = events[0]["payload"]

    assert payload["drift"] is True, (
        f"drift must be True when fabrication present; got payload={payload}"
    )
    assert payload["extra_in_doc"] == ["ghost.py"], (
        f"expected extra_in_doc=['ghost.py'], got {payload['extra_in_doc']!r}"
    )
    assert payload["missing_from_doc"] == ["noise.md"], (
        f"expected missing_from_doc=['noise.md'], got {payload['missing_from_doc']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — drift=False when both sets are equal
#
# PASSES today. Regression guard (mirrors sibling AC3 contract).
# ═════════════════════════════════════════════════════════════════════════════


def test_drift_false_when_sets_equal(tmp_path: Path, monkeypatch):
    """Doc claims ``Files: a.py, b.py``; disk returns [a.py, b.py].

    Exact match → drift=False, extra_in_doc=[], missing_from_doc=[].
    Regression guard: must stay green after subset-semantics flip.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize,
        "git_diff_files",
        lambda *a, **kw: ["a.py", "b.py"],
        raising=False,
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

    assert payload["drift"] is False, (
        f"drift must be False when sets equal; got payload={payload}"
    )
    assert payload["extra_in_doc"] == [], (
        f"expected extra_in_doc=[], got {payload['extra_in_doc']!r}"
    )
    assert payload["missing_from_doc"] == [], (
        f"expected missing_from_doc=[], got {payload['missing_from_doc']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — drift=False when both empty
#
# PASSES today. Regression guard (mirrors sibling AC5 contract).
# ═════════════════════════════════════════════════════════════════════════════


def test_drift_false_when_both_empty(tmp_path: Path, monkeypatch):
    """Doc has no ``Files:`` line at all; disk returns [].

    Both sets empty → drift=False, files_line_present=False,
    files_in_synthesis_doc=[].
    Regression guard: must stay green after subset-semantics flip.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_7_synthesize,
        "git_diff_files",
        lambda *a, **kw: [],
        raising=False,
    )

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

    assert payload["files_line_present"] is False, (
        f"expected files_line_present=False; got {payload['files_line_present']!r}"
    )
    assert payload["files_in_synthesis_doc"] == [], (
        f"expected files_in_synthesis_doc=[]; got {payload['files_in_synthesis_doc']!r}"
    )
    assert payload["drift"] is False, (
        f"drift must be False when both empty; got payload={payload}"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "--tb=short"]))
