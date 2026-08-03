"""RED tests for 95D3E5F6 Step 7 — phase_6_review W1 + disk_truth telemetry.

Contract (telemetry-first, additive observability — NO behavior change):

1. Cycle-1 review schema gains a ``## Findings (structured)`` JSON block whose
   per-finding shape is ``{id, severity, path, description}``. (Distinct from
   phase_45_spec's schema because phase_6 reviews CODE, not specs — severity +
   path matter most.)

2. After ``_write_fix_artifact`` succeeds, the engine emits:
   - ``fix_disk_truth_coverage`` with payload
     ``{n_findings, n_addressed, n_uncovered, coverage_ratio,
        structured_block_present}``. Coverage measures: of N findings (read
     from the structured block), how many had at least one ``path`` actually
     present in the ``git_diff_files`` list?
   - ``fix_files_drift`` (only when fix doc carries an LLM-claimed
     ``files_modified: a, b, c`` line) with payload
     ``{llm_claimed, disk_actual, extra_in_llm, missing_from_llm}``.

3. Legacy ``_parse_fix_status`` and the satisfaction gate are UNCHANGED —
   tests 6 + 7 below are backward-compat guards that MUST stay green.

4. ``phase_6_review`` module gains module-level imports of
   ``extract_structured_findings`` (W1 lib) and ``git_diff_files``
   (disk_truth lib).

Tests below MUST FAIL until GREEN ships those additions, EXCEPT the two
backward-compat guards.

Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    FIX_COMPLETE,
    FIX_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    SATISFACTION_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    _build_review_prompt,
    _parse_fix_status,
    _parse_satisfaction_score,
    _write_fix_artifact,
    _write_satisfaction_doc,
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
    org = {"scratchpad_dir": str(scratchpad)}
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
    """Capture every _emit_safe call in phase_6_review."""
    captured: list[dict] = []

    def _capture(event_type, payload):
        captured.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(phase_6_review, "_emit_safe", _capture)
    return captured


def _make_fix_prev(
    scratchpad: Path, raw: str, review_doc_path: Path, spec_path: Path
) -> StepResult:
    """Build the prev StepResult that _write_fix_artifact expects."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "log_path": str(scratchpad / FIX_DOC_RELPATH),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc_path),
            "verdict": "FAIL",
            "prompt": "fix worker prompt stub",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )


def _seed_review_doc_with_structured_findings(
    scratchpad: Path, findings: list[dict]
) -> Path:
    """Write a review doc carrying a ## Findings (structured) JSON block."""
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Composite Review\n"
        "\n"
        "## Aggregated Findings\n"
        "(legacy free-text section)\n"
        "\n"
        "## Findings (structured)\n"
        "```json\n"
        + json.dumps(findings, indent=2)
        + "\n```\n"
        "\n"
        "VERDICT: FAIL\n"
    )
    review_doc.write_text(body)
    return review_doc


def _seed_review_doc_no_structured(scratchpad: Path) -> Path:
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text(
        "# Composite Review\n\n## Aggregated Findings\n\nfree text only\n\nVERDICT: FAIL\n"
    )
    return review_doc


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1 — review prompt schema includes ## Findings (structured) block
# ═════════════════════════════════════════════════════════════════════════════


def test_review_output_schema_includes_structured_findings_block(tmp_path: Path):
    """Cycle-1 review schema MUST instruct emission of a ## Findings (structured)
    JSON block with keys id, severity, path, description.

    Currently fails — phase_6 review prompt today only specifies the per-role
    schema (severity/title/path:line/confidence) and a free-form FINDINGS list.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)
    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", f"prompt build failed: {result.error}"
    prompt = result.data["prompt"]

    assert "## Findings (structured)" in prompt, (
        "review prompt missing '## Findings (structured)' section header — "
        "Step 7 W1 schema not yet wired"
    )
    # JSON template must mention the per-finding keys (severity + path are the
    # phase_6-specific keys vs phase_45_spec's id/type/evidence/required_action).
    for key in ("id", "severity", "path", "description"):
        assert key in prompt, (
            f"review prompt missing '{key}' key in structured-findings template"
        )


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2 — module-level imports of W1 + disk_truth symbols
# ═════════════════════════════════════════════════════════════════════════════


def test_imports_w1_and_disk_truth_symbols():
    """phase_6_review module MUST bind names ``extract_structured_findings``
    and ``git_diff_files`` at module level so monkeypatch fixtures (and human
    readers) can locate them next to the call site.

    Currently fails — neither name is imported in phase_6_review.py.
    """
    assert hasattr(phase_6_review, "extract_structured_findings"), (
        "phase_6_review must `from bytedigger_engine.lib.plugins.checklist_convergence import "
        "extract_structured_findings` at module scope (Step 7 W1 wiring)"
    )
    assert hasattr(phase_6_review, "git_diff_files"), (
        "phase_6_review must `from bytedigger_engine.lib.plugins.disk_truth import git_diff_files` "
        "at module scope (Step 7 disk-truth wiring)"
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3 — fix_disk_truth_coverage emitted when structured findings present
# ═════════════════════════════════════════════════════════════════════════════


def test_fix_disk_truth_coverage_event_emitted_when_structured_findings_present(
    tmp_path: Path, monkeypatch
):
    """Review doc has 3 findings (paths a.py, b.py, c.py); fix succeeds;
    git_diff returns [a.py, b.py]. Engine MUST emit a single
    ``fix_disk_truth_coverage`` event with n_findings=3, n_addressed=2,
    n_uncovered=1, coverage_ratio≈0.667, structured_block_present=True.

    Currently fails — engine has no such telemetry path today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)
    review_doc = _seed_review_doc_with_structured_findings(
        scratchpad,
        [
            {"id": "1", "severity": "HIGH", "path": "a.py", "description": "issue A"},
            {"id": "2", "severity": "MEDIUM", "path": "b.py", "description": "issue B"},
            {"id": "3", "severity": "LOW", "path": "c.py", "description": "issue C"},
        ],
    )

    captured = _patch_emit(monkeypatch)

    # Mock git_diff_files at the module-level import site (test 2 pins the name).
    def _fake_git_diff(*args, **kwargs):
        return ["a.py", "b.py"]

    monkeypatch.setattr(phase_6_review, "git_diff_files", _fake_git_diff, raising=False)

    raw = "FIX COMPLETE — 2 of 3 findings fixed.\n"
    prev = _make_fix_prev(scratchpad, raw, review_doc, scratchpad / SPEC_DOC_RELPATH)
    ctx = _make_ctx(scratchpad, worktree=repo)

    _write_fix_artifact(ctx, prev)

    coverage_events = [e for e in captured if e["type"] == "fix_disk_truth_coverage"]
    assert len(coverage_events) == 1, (
        f"expected exactly 1 fix_disk_truth_coverage event, got "
        f"{len(coverage_events)}: {captured}"
    )
    payload = coverage_events[0]["payload"]
    assert payload["n_findings"] == 3, payload
    assert payload["n_addressed"] == 2, payload
    assert payload["n_uncovered"] == 1, payload
    assert abs(float(payload["coverage_ratio"]) - 0.667) < 0.01, payload
    assert payload["structured_block_present"] is True, payload


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4 — fix_disk_truth_coverage event behavior when no structured block
# ═════════════════════════════════════════════════════════════════════════════


def test_fix_disk_truth_coverage_event_omitted_when_no_structured_block(
    tmp_path: Path, monkeypatch
):
    """Review doc has NO ## Findings (structured) block. Engine MUST emit a
    single ``fix_disk_truth_coverage`` event with structured_block_present=False
    and n_findings=0 (opinionated: emit the event with explicit absence flag
    rather than silently skipping — keeps event-stream cardinality stable per
    fix run for downstream cleanup gates).

    Currently fails — engine has no such telemetry path today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)
    review_doc = _seed_review_doc_no_structured(scratchpad)

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_6_review, "git_diff_files", lambda *a, **kw: ["x.py"], raising=False
    )

    raw = "FIX COMPLETE — 0 of 0 findings fixed.\n"
    prev = _make_fix_prev(scratchpad, raw, review_doc, scratchpad / SPEC_DOC_RELPATH)
    ctx = _make_ctx(scratchpad, worktree=repo)

    _write_fix_artifact(ctx, prev)

    coverage_events = [e for e in captured if e["type"] == "fix_disk_truth_coverage"]
    assert len(coverage_events) == 1, (
        f"expected 1 fix_disk_truth_coverage event with absence flag, got "
        f"{len(coverage_events)}: {captured}"
    )
    payload = coverage_events[0]["payload"]
    assert payload["structured_block_present"] is False, payload
    assert payload["n_findings"] == 0, payload


# ═════════════════════════════════════════════════════════════════════════════
# TEST 5 — fix_files_drift event when LLM claims files_modified
# ═════════════════════════════════════════════════════════════════════════════


def test_fix_files_drift_event_emitted_when_llm_claims_files_modified(
    tmp_path: Path, monkeypatch
):
    """Fix doc contains ``files_modified: a.py, b.py, x.py`` (comma-separated
    list on a single line). git_diff returns [a.py, b.py]. Engine MUST emit
    ``fix_files_drift`` with extra_in_llm=['x.py'] and missing_from_llm=[].

    Convention chosen: ``files_modified: <comma-separated paths>`` on a single
    line in the fix doc body. Mirrors the FIX COMPLETE marker that already
    suggests ``Files: [path1, path2, ...]`` in _build_fix_prompt; GREEN may
    accept both forms.

    Currently fails — no parsing path today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    repo = _make_repo(tmp_path)
    _seed_spec(scratchpad)
    review_doc = _seed_review_doc_with_structured_findings(
        scratchpad,
        [{"id": "1", "severity": "HIGH", "path": "a.py", "description": "x"}],
    )

    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        phase_6_review, "git_diff_files", lambda *a, **kw: ["a.py", "b.py"], raising=False
    )

    raw = (
        "Edited a.py, b.py, x.py to address the finding.\n"
        "files_modified: a.py, b.py, x.py\n"
        "FIX COMPLETE — 1 of 1 findings fixed.\n"
    )
    prev = _make_fix_prev(scratchpad, raw, review_doc, scratchpad / SPEC_DOC_RELPATH)
    ctx = _make_ctx(scratchpad, worktree=repo)

    _write_fix_artifact(ctx, prev)

    drift_events = [e for e in captured if e["type"] == "fix_files_drift"]
    assert len(drift_events) == 1, (
        f"expected exactly 1 fix_files_drift event, got {len(drift_events)}: "
        f"{captured}"
    )
    payload = drift_events[0]["payload"]
    assert set(payload["llm_claimed"]) == {"a.py", "b.py", "x.py"}, payload
    assert set(payload["disk_actual"]) == {"a.py", "b.py"}, payload
    assert set(payload["extra_in_llm"]) == {"x.py"}, payload
    assert set(payload["missing_from_llm"]) == set(), payload


# ═════════════════════════════════════════════════════════════════════════════
# TEST 6 — backward-compat guard: _parse_fix_status unchanged
# ═════════════════════════════════════════════════════════════════════════════


def test_fix_status_parser_unchanged_backward_compat():
    """_parse_fix_status('FIX COMPLETE …') must still return FIX_COMPLETE.
    Step 7 is telemetry-only — legacy fix-status semantics MUST stay intact.

    Currently passes (backward-compat guard); MUST stay green post-impl.
    """
    assert _parse_fix_status("FIX COMPLETE — all done") == FIX_COMPLETE
    assert _parse_fix_status("FIX COMPLETE\nfollow-up notes") == FIX_COMPLETE


# ═════════════════════════════════════════════════════════════════════════════
# TEST 7 — backward-compat guard: satisfaction gate unchanged
# ═════════════════════════════════════════════════════════════════════════════


def test_satisfaction_gate_unchanged_backward_compat(tmp_path: Path):
    """_write_satisfaction_doc must still parse SCORE: 85 → score=85 + status=ok
    when threshold=75. Backward-compat guard for Step 7 telemetry-only ship.

    Currently passes; MUST stay green post-impl.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    raw = "SCORE: 85\nVERDICT: PASS\n"
    sat_doc = scratchpad / SATISFACTION_DOC_RELPATH
    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(sat_doc),
            "spec_path": str(scratchpad / SPEC_DOC_RELPATH),
            "review_doc_path": str(scratchpad / REVIEW_DOC_RELPATH),
            "fix_doc_path": str(scratchpad / FIX_DOC_RELPATH),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )
    ctx = _make_ctx(scratchpad)
    # Inject lower threshold so 85 passes deterministically.
    ctx.org_config["satisfaction_threshold"] = 75

    result = _write_satisfaction_doc(ctx, prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error})"
    assert result.data["score"] == 85
    # Sanity on the legacy parser too.
    assert _parse_satisfaction_score(raw) == 85


# ═════════════════════════════════════════════════════════════════════════════
# TEST 8 — extract_structured_findings roundtrip (smoke)
# ═════════════════════════════════════════════════════════════════════════════


def test_extract_structured_findings_roundtrip_in_review_doc(tmp_path: Path):
    """Write a review doc with 3 findings carrying the phase_6 schema
    {id, severity, path, description}. Calling
    ``phase_6_review.extract_structured_findings(review_doc.read_text())``
    must return a list of 3 dicts where each finding exposes the phase_6
    schema keys.

    NOTE TO REVIEWER: the W1 lib's existing ``Finding`` TypedDict carries
    {id, type, evidence, required_action} and DROPS phase_6's severity/path/
    description keys on parse. So this test will FAIL today because (a) the
    name is not bound on phase_6_review (test 2 also fails), and even when
    GREEN binds the W1 import, the lib coerces the wrong schema. GREEN's
    likely fix: add a phase_6-specific extractor that preserves severity/path/
    description (or extend the lib). Either path satisfies this test.

    Listed as RED gate (failing today).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    findings_in = [
        {"id": "1", "severity": "CRITICAL", "path": "a.py", "description": "A"},
        {"id": "2", "severity": "HIGH", "path": "b.py", "description": "B"},
        {"id": "3", "severity": "MEDIUM", "path": "c.py", "description": "C"},
    ]
    review_doc = _seed_review_doc_with_structured_findings(scratchpad, findings_in)

    extractor = getattr(phase_6_review, "extract_structured_findings", None)
    assert extractor is not None, (
        "phase_6_review.extract_structured_findings not bound — Step 7 W1 import missing"
    )

    parsed = extractor(review_doc.read_text())
    assert parsed is not None, "extractor returned None on a valid structured block"
    assert len(parsed) == 3, f"expected 3 findings, got {len(parsed)}: {parsed}"

    for raw, got in zip(findings_in, parsed):
        # Must preserve the phase_6 schema keys.
        for key in ("id", "severity", "path", "description"):
            assert key in got, (
                f"phase_6 finding missing key {key!r}: {got!r}"
            )
            assert str(got[key]) == raw[key], (
                f"phase_6 finding {key!r}: expected {raw[key]!r}, got {got[key]!r}"
            )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "--tb=short"]))
