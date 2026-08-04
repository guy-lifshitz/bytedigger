"""RED tests for 65695203 — phase_6 verified-only fix-doc gate.

Spec: SHARED/memory/Decisions/2026-06-09_65695203_verified_only_gate_spec.md

Design summary (rev1 — post Opus-reject):
  - New constant REVIEW_FIX_DOC_RELPATH = "reviews/build-review-fix.md" (sibling to
    REVIEW_DOC_RELPATH = "reviews/build-review.md").
  - Named helper _render_fix_doc(verified_findings, verdict) -> str (§1aa): renders
    ONLY verified findings; no suspect titles; no ## Suspect Findings header.
  - _aggregate_review_findings (step 3) forwards verified_findings list in StepResult.data.
  - _write_review_artifact (step 4) reads verified_findings from prev.data, writes the
    verified-only fix doc, returns new key review_fix_doc_path (NOT fix_doc_path —
    that key is occupied by the fix-worker output at L2257).
  - _build_fix_prompt references review_fix_doc_path from prev.data (NOT review_doc_path).
  - _build_satisfaction_prompt keeps referencing review_doc_path (full doc).
  - FixVerdict schema and E_FIX_BLOCKED literal unchanged (§1n — regression guard).

Expected pre-GREEN status:
  FAIL today: AC1, AC2, AC3, AC5, AC6, AC8, AC11  (new behaviour, not yet implemented)
  PASS today: AC4, AC7, AC9                        (regression guards — existing behaviour)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
WORKFLOWS = ENGINE_ROOT / "bytedigger_engine" / "workflows"

# Match the import pattern used by sibling tests
# (test_phase_6_fix_structured_verdict.py, test_phase_6_1F39FB1A_soft_tag.py)
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

# Top-level import of the module (it already exists — collects fine).
# NOT-YET-EXISTING symbols (REVIEW_FIX_DOC_RELPATH, _render_fix_doc) are accessed
# inside test bodies via getattr/hasattr to comply with §1q/D1CF5FDF.
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402

# Import existing symbols for regression-guard tests (these all exist today).
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    REVIEW_DOC_RELPATH,
    _build_satisfaction_prompt,
    _build_fix_prompt,
)
from bytedigger_engine.contracts import StepResult  # noqa: E402


# ─── fixture helpers ───────────────────────────────────────────────────────────


def _make_verified_finding(title: str, severity: str = "HIGH") -> dict:
    """Minimal verified finding dict matching the shape _aggregate_review_findings produces."""
    return {
        "title": title,
        "severity": severity,
        "verify_status": "verified-exact",
        "block": (
            f"### SEVERITY: {severity} — {title}\n"
            "> /tmp/fake_file.py:1: def some_func():\n"
            "Confidence: HIGH\n"
            "Description: a verified finding\n"
        ),
    }


def _make_suspect_finding(title: str, severity: str = "MEDIUM",
                          status: str = "suspect-no-match") -> dict:
    """Minimal suspect finding dict."""
    return {
        "title": title,
        "severity": severity,
        "verify_status": status,
        "block": (
            f"### SEVERITY: {severity} — {title}\n"
            "> /tmp/fake_file.py:1: `def some_func():`\n"
            "Confidence: HIGH\n"
            "Description: a suspect finding\n"
        ),
    }


def _make_write_review_prev(tmp_path: Path, *, include_fix_doc_path: bool = False) -> StepResult:
    """Build a StepResult that _build_fix_prompt / _build_satisfaction_prompt expect.

    For regression-guard AC6/AC7 tests: provides the keys that the CURRENT production
    code reads from prev.data.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text(
        "# Review\n\n## Aggregated Findings\n\n(no findings)\n\n"
        "## Suspect Findings\n\n### SEVERITY: MEDIUM — suspect-TDA-01 [verify: suspect-no-match]\n\n"
        "VERDICT: PASS\n",
        encoding="utf-8",
    )

    spec_doc = scratchpad / "specs/build-spec.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\nAdd foo\n", encoding="utf-8")

    fix_doc = scratchpad / "reviews/build-fix.md"
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE — done\n", encoding="utf-8")

    data: dict = {
        "review_doc_path": str(review_doc),
        "spec_path": str(spec_doc),
        "fix_doc_path": str(fix_doc),
        "verdict": "PASS",
        "prompt": "fix worker prompt stub",
        "raw_response": "FIX COMPLETE\n",
        "log_path": str(fix_doc),
    }

    if include_fix_doc_path:
        # For GREEN world: also add the verified-only fix doc path key
        review_fix_doc = scratchpad / "reviews/build-review-fix.md"
        review_fix_doc.write_text("# Fix Doc\n\n## Aggregated Findings\n\n(none)\n", encoding="utf-8")
        data["review_fix_doc_path"] = str(review_fix_doc)

    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="write_review_artifact",
    )


# ─── AC1: REVIEW_FIX_DOC_RELPATH constant ────────────────────────────────────


def test_ac1_review_fix_doc_relpath_constant_exists_and_differs():
    """AC1: phase_6_review exposes REVIEW_FIX_DOC_RELPATH == 'reviews/build-review-fix.md'
    and it is distinct from REVIEW_DOC_RELPATH.

    FAILS today: REVIEW_FIX_DOC_RELPATH does not exist in phase_6_review.
    """
    val = getattr(_p6, "REVIEW_FIX_DOC_RELPATH", None)
    assert val is not None, (
        "AC1: REVIEW_FIX_DOC_RELPATH not found in phase_6_review — new constant not yet added."
    )
    assert val == "reviews/build-review-fix.md", (
        f"AC1: expected REVIEW_FIX_DOC_RELPATH='reviews/build-review-fix.md', got {val!r}"
    )
    assert val != REVIEW_DOC_RELPATH, (
        f"AC1: REVIEW_FIX_DOC_RELPATH must differ from REVIEW_DOC_RELPATH={REVIEW_DOC_RELPATH!r}, "
        f"but both are {val!r}"
    )


# ─── AC2: _render_fix_doc helper (§1aa forcing-fn) ────────────────────────────


def test_ac2_render_fix_doc_excludes_suspect_titles_and_header():
    """AC2 (§1aa forcing-fn): _render_fix_doc(verified_findings, verdict="") callable;
    given a verified-only list, returns body containing EVERY verified title and NO
    '## Suspect Findings' header.

    Signature is _render_fix_doc(verified_findings, verdict="") — the 2nd param is
    a verdict string, NOT a suspect list. We call with the verified list only (single
    positional arg). The structural exclusion of suspects is guaranteed by the L1603
    partition in _aggregate_review_findings, which forwards only verified_findings;
    AC11 tests that partition directly. Here we assert the helper renders only what
    it receives and emits no suspect section header.
    """
    render_fn = getattr(_p6, "_render_fix_doc", None)
    assert render_fn is not None, (
        "AC2: _render_fix_doc not found in phase_6_review — helper not yet added. "
        "Per §1aa: named sourceable helper required for verified-only rendering."
    )

    verified = [
        _make_verified_finding("VerifiedAlpha", "HIGH"),
        _make_verified_finding("VerifiedBeta", "MEDIUM"),
    ]

    # Single positional arg — real signature is (verified_findings, verdict="")
    body = render_fn(verified)

    assert isinstance(body, str), (
        f"AC2: _render_fix_doc must return str, got {type(body)}"
    )

    # Every verified title must appear
    for f in verified:
        assert f["title"] in body, (
            f"AC2: verified finding title {f['title']!r} missing from fix doc body. "
            f"Fix doc must include ALL verified findings."
        )

    # No '## Suspect Findings' header — the helper must never emit this section
    assert "## Suspect Findings" not in body, (
        "AC2: '## Suspect Findings' header must NOT appear in fix doc body. "
        "Fix worker must never see suspect findings."
    )


# ─── AC4: Full doc still contains both sections (regression guard) ─────────────


def test_ac4_full_review_doc_still_has_both_sections(tmp_path, monkeypatch):
    """AC4: _aggregate_review_findings still renders BOTH '## Aggregated Findings'
    and '## Suspect Findings' in its aggregated_content output.
    Regression guard — must PASS today (1F39FB1A existing behaviour).

    This test verifies that the GREEN change does NOT remove the full-doc rendering.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    # Build a minimal fixture with one verified + one suspect finding already in
    # aggregated_content (simulated — we test _write_review_artifact's pass-through).
    # The full-doc rendering is in _aggregate_review_findings; we test the output shape
    # by invoking it with role files that produce both kinds of finding.
    # Simplest: build a manual aggregated_content string matching the expected shape.
    sample_content = (
        "## Aggregated Findings\n\n"
        "### SEVERITY: HIGH — some-verified-finding\n\n"
        "## Suspect Findings\n\n"
        "### SEVERITY: MEDIUM — some-suspect-finding [verify: suspect-no-match]\n\n"
        "VERDICT: FAIL\n"
    )

    # Assert the shape we depend on exists (regression: this is what today already produces)
    assert "## Aggregated Findings" in sample_content, (
        "AC4 fixture sanity: '## Aggregated Findings' must be present in full doc"
    )
    assert "## Suspect Findings" in sample_content, (
        "AC4 fixture sanity: '## Suspect Findings' must be present in full doc"
    )

    # Write the full doc to disk and verify _write_review_artifact passes through both sections.
    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"

    spec_doc = scratchpad / "specs/build-spec.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\nAdd foo\n", encoding="utf-8")

    red_log = scratchpad / "tests/build-red-output.log"
    red_log.parent.mkdir(parents=True, exist_ok=True)
    red_log.write_text("", encoding="utf-8")
    green_log = scratchpad / "tests/build-green-output.log"
    green_log.write_text("", encoding="utf-8")

    prev = StepResult(
        status="ok",
        data={
            "aggregated_content": sample_content,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )
    from bytedigger_engine.workflows.phase_6_review import _write_review_artifact  # noqa: E402
    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="q",
    )
    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC4: _write_review_artifact must return ok, got {result.status}: {result.error!r}"
    )
    written = doc_path.read_text(encoding="utf-8")
    assert "## Aggregated Findings" in written, (
        "AC4 (regression guard): '## Aggregated Findings' must remain in the full review doc "
        "after GREEN — this section is read by Opus satisfaction."
    )
    assert "## Suspect Findings" in written, (
        "AC4 (regression guard): '## Suspect Findings' must remain in the full review doc "
        "after GREEN — this section is read by Opus satisfaction (but not by fix-worker)."
    )


# ─── AC6: _build_fix_prompt references review_fix_doc_path (new verified-only doc) ──


def test_ac6_build_fix_prompt_references_fix_doc_path_not_review_doc_path(tmp_path, monkeypatch):
    """AC6: after GREEN, _build_fix_prompt must embed review_fix_doc_path (verified-only)
    in the 'fix every finding' line, NOT review_doc_path (the full doc).

    FAILS today: _build_fix_prompt reads prev.data['review_doc_path'] (the full doc)
    and embeds it in the prompt. The key 'review_fix_doc_path' for the verified-only
    doc does not yet exist or flow through prev.data at this step.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(_p6, "_maybe_role_template", lambda ctx: None, raising=False)
    monkeypatch.setattr(_p6, "_read_first_block", lambda scratchpad: "", raising=False)
    monkeypatch.setattr(_p6, "_inline_inscope_test_files", lambda ctx, s: ("", 0, 0), raising=False)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("full doc content with suspects\n", encoding="utf-8")

    # The verified-only fix doc that _build_fix_prompt should reference post-GREEN.
    review_fix_doc_relpath = getattr(_p6, "REVIEW_FIX_DOC_RELPATH", "reviews/build-review-fix.md")
    review_fix_doc = scratchpad / review_fix_doc_relpath
    review_fix_doc.parent.mkdir(parents=True, exist_ok=True)
    review_fix_doc.write_text("## Aggregated Findings\n\n(none)\n", encoding="utf-8")

    spec_doc = scratchpad / "specs/build-spec.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\nAdd foo\n", encoding="utf-8")

    # Build prev with BOTH path keys (post-GREEN shape).
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "review_fix_doc_path": str(review_fix_doc),
            "spec_path": str(spec_doc),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="write_review_artifact",
    )

    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo",
    )

    result = _build_fix_prompt(ctx, prev)
    assert result.status == "ok", (
        f"AC6: _build_fix_prompt must return ok, got {result.status!r}: {result.error!r}"
    )

    prompt_text = result.data.get("prompt", "")

    # Post-GREEN: the 'fix every finding' line must reference the verified-only doc path.
    assert str(review_fix_doc) in prompt_text, (
        f"AC6: _build_fix_prompt prompt must reference the verified-only fix doc "
        f"({review_fix_doc}) in the 'fix every finding' line. "
        f"Today it references the full review doc ({review_doc}). "
        f"This MUST FAIL today."
    )

    # And must NOT reference the full review doc in the 'fix every finding' line.
    # (The full doc must not be fed to the fix worker — that's the entire point of this spec.)
    fix_every_line = next(
        (line for line in prompt_text.splitlines()
         if "fix every finding" in line.lower()),
        None,
    )
    assert fix_every_line is not None, (
        "AC6: 'fix every finding' line not found in fix prompt — prompt shape changed unexpectedly."
    )
    assert str(review_doc) not in fix_every_line, (
        f"AC6: the 'fix every finding' line must NOT reference the full review doc "
        f"({review_doc}). Post-GREEN it must reference the verified-only doc."
    )


# ─── AC7: _build_satisfaction_prompt references review_doc_path (regression guard) ─


def test_ac7_build_satisfaction_prompt_still_references_review_doc_path(tmp_path, monkeypatch):
    """AC7 (regression guard): _build_satisfaction_prompt still references review_doc_path
    (the full doc with both Aggregated + Suspect sections).
    Must PASS today and continue to pass after GREEN.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(_p6, "_maybe_role_template", lambda ctx: None, raising=False)
    monkeypatch.setattr(_p6, "_read_first_block", lambda scratchpad: "", raising=False)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("## Aggregated Findings\n\n## Suspect Findings\n\nVERDICT: PASS\n",
                          encoding="utf-8")

    spec_doc = scratchpad / "specs/build-spec.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\n", encoding="utf-8")

    fix_doc = scratchpad / "reviews/build-fix.md"
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE\n", encoding="utf-8")

    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "spec_path": str(spec_doc),
            "fix_doc_path": str(fix_doc),
            "verdict": "PASS",
        },
        duration_ms=0,
        step_name="write_fix_artifact",
    )

    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo",
    )

    result = _build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"AC7: _build_satisfaction_prompt must return ok, got {result.status!r}: {result.error!r}"
    )

    prompt_text = result.data.get("prompt", "")
    assert str(review_doc) in prompt_text, (
        f"AC7 (regression guard): _build_satisfaction_prompt must still reference "
        f"review_doc_path ({review_doc}) in its prompt text. "
        f"Opus satisfaction must see the FULL doc including Suspect Findings."
    )


# ─── AC8: empty verified set → fix doc has no suspect entries ─────────────────


def test_ac8_empty_verified_set_produces_empty_fix_doc(tmp_path):
    """AC8 (incident repro — forge-1780984984-1CF92815): when verified_findings == []
    (all findings were suspect, excluded by the L1603 partition), _render_fix_doc([])
    returns a body indicating no actionable findings — nothing for the fix-worker to
    mark as 'remaining', so E_FIX_BLOCKED cannot fire from suspect findings.

    Signature is _render_fix_doc(verified_findings, verdict=""). Called with empty
    list only — suspect dicts are never passed to this helper; they are excluded
    upstream by _aggregate_review_findings (tested by AC11).
    """
    render_fn = getattr(_p6, "_render_fix_doc", None)
    assert render_fn is not None, (
        "AC8: _render_fix_doc not found in phase_6_review — helper not yet added. "
        "Incident repro (forge-1780984984-1CF92815): all TDA findings were suspect; "
        "fix-worker should receive an empty aggregated section."
    )

    # Empty verified list — real signature (verified_findings, verdict="")
    body = render_fn([])

    assert isinstance(body, str), (
        f"AC8: _render_fix_doc must return str, got {type(body)}"
    )

    # With zero verified findings, body must signal no actionable findings
    # (e.g. "(no findings)" placeholder — the standard empty-section sentinel)
    assert "(no findings)" in body or len(body.strip()) == 0 or "## Aggregated Findings" in body, (
        "AC8: _render_fix_doc([]) must render an empty/no-findings body. "
        "The fix-worker must see zero actionable findings when verified set is empty."
    )

    # No ## Suspect Findings header — the helper must never emit this section
    assert "## Suspect Findings" not in body, (
        "AC8: '## Suspect Findings' must NOT appear in the fix doc body. "
        "This would allow fix-worker to see suspect findings in 'remaining', "
        "driving E_FIX_BLOCKED — the root incident."
    )


# ─── AC9: FixVerdict schema and E_FIX_BLOCKED unchanged (regression guard) ────


def test_ac9_fix_verdict_schema_unchanged_and_e_fix_blocked_present():
    """AC9 (regression guard): FixVerdict in schema.py still has exactly fix_complete: bool
    and remaining fields; E_FIX_BLOCKED literal still present in phase_6_review.

    Must PASS today and continue to pass after GREEN (§1n — no new error codes).
    """
    import dataclasses

    # Import deferred inside test body (§1q/D1CF5FDF — import at assert time, not collect time)
    # ENGINE_ROOT/lib is on sys.path so the package path is plugins.disk_truth.schema
    from bytedigger_engine.lib.plugins.disk_truth.schema import FixVerdict  # noqa: E402

    assert dataclasses.is_dataclass(FixVerdict), (
        "AC9: FixVerdict must be a dataclass"
    )
    field_names = {f.name for f in dataclasses.fields(FixVerdict)}
    assert "fix_complete" in field_names, (
        f"AC9: FixVerdict must have 'fix_complete' field, got fields: {field_names}"
    )
    assert "remaining" in field_names, (
        f"AC9: FixVerdict must have 'remaining' field, got fields: {field_names}"
    )

    # Instance construction sanity
    v = FixVerdict(fix_complete=True, remaining=[])
    assert v.fix_complete is True
    assert v.remaining == []

    # E_FIX_BLOCKED literal must still be present in phase_6_review.py
    phase6_source = (WORKFLOWS / "phase_6_review.py").read_text(encoding="utf-8")
    assert "E_FIX_BLOCKED" in phase6_source, (
        "AC9: 'E_FIX_BLOCKED' literal must remain present in phase_6_review.py — "
        "the gate still fires on unfixable verified findings (§1n: no new error code)."
    )


# ─── AC3: dual-write side-effect anchor (§1l) ─────────────────────────────────


def test_ac3_write_review_artifact_produces_both_doc_files_with_correct_content(tmp_path, monkeypatch):
    """AC3 (§1l forcing-fn anti-stub): after _write_review_artifact runs with
    prev.data carrying both aggregated_content AND verified_findings, the on-disk
    reviews/build-review-fix.md must contain EVERY verified finding title, ZERO
    suspect titles, and NO '## Suspect Findings' header.

    FAILS today: _write_review_artifact does not perform the dual-write; the fix doc
    is absent so the file-read itself would fail — but the existence assert is the
    first gate. The content assertions ensure a stub (empty-file write) also fails.

    prev.data shape assumption: _write_review_artifact reads 'verified_findings'
    from prev.data (the forwarded list from step 3 — spec §2 item 3+4). This key
    does not exist in today's step-3 → step-4 contract; GREEN must add it.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    doc_path = reviews_dir / "build-review.md"

    spec_doc = scratchpad / "specs/build-spec.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\nAdd foo\n", encoding="utf-8")

    red_log = scratchpad / "tests/build-red-output.log"
    red_log.parent.mkdir(parents=True, exist_ok=True)
    red_log.write_text("", encoding="utf-8")
    green_log = scratchpad / "tests/build-green-output.log"
    green_log.write_text("", encoding="utf-8")

    verified_findings = [
        _make_verified_finding("VerifiedAC3-Alpha", "HIGH"),
        _make_verified_finding("VerifiedAC3-Beta", "MEDIUM"),
    ]
    suspect_findings_titles = ["SuspectAC3-TDA-01", "SuspectAC3-TDA-02"]

    aggregated_content = (
        "## Aggregated Findings\n\n"
        "### SEVERITY: HIGH — VerifiedAC3-Alpha\n\n"
        "### SEVERITY: MEDIUM — VerifiedAC3-Beta\n\n"
        "## Suspect Findings\n\n"
        "### SEVERITY: MEDIUM — SuspectAC3-TDA-01 [verify: suspect-no-match]\n\n"
        "### SEVERITY: LOW — SuspectAC3-TDA-02 [verify: suspect-no-quote]\n\n"
        "VERDICT: FAIL\n"
    )

    prev = StepResult(
        status="ok",
        data={
            "aggregated_content": aggregated_content,
            "verified_findings": verified_findings,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )

    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo",
    )

    from bytedigger_engine.workflows.phase_6_review import _write_review_artifact  # noqa: E402
    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC3: _write_review_artifact must return ok, got {result.status!r}: {result.error!r}"
    )

    # Full doc must exist (regression guard half — passes today)
    assert doc_path.exists(), (
        f"AC3: full review doc {doc_path} must exist after _write_review_artifact"
    )

    # Fix doc must ALSO exist (new dual-write — FAILS today)
    fix_doc_relpath = getattr(_p6, "REVIEW_FIX_DOC_RELPATH", "reviews/build-review-fix.md")
    fix_doc_path = scratchpad / fix_doc_relpath
    assert fix_doc_path.exists(), (
        f"AC3: verified-only fix doc {fix_doc_path} must exist after _write_review_artifact. "
        f"FAILS today: _write_review_artifact only writes the full review doc."
    )

    # Content forcing-function (anti-stub): read the on-disk bytes
    fix_body = fix_doc_path.read_text(encoding="utf-8")

    # Every verified title must appear in the fix doc
    for f in verified_findings:
        assert f["title"] in fix_body, (
            f"AC3: verified finding title {f['title']!r} missing from on-disk fix doc. "
            f"The fix doc must include ALL verified findings."
        )

    # Zero suspect titles may appear in the fix doc
    for title in suspect_findings_titles:
        assert title not in fix_body, (
            f"AC3: suspect finding title {title!r} found in on-disk fix doc body. "
            f"Fix doc must contain ZERO suspect findings."
        )

    # No ## Suspect Findings header in fix doc
    assert "## Suspect Findings" not in fix_body, (
        "AC3: '## Suspect Findings' header must NOT appear in the on-disk fix doc. "
        "Fix-worker must never see suspect findings via this file."
    )


# ─── AC5: StepResult.data carries both path keys (§1y reachability) ───────────


def test_ac5_write_review_artifact_returns_both_path_keys(tmp_path, monkeypatch):
    """AC5 (§1y reachability): after _write_review_artifact runs (prev.data carrying
    verified_findings), its returned StepResult.data contains BOTH keys:
      - 'review_doc_path'       (full doc — already present today)
      - 'review_fix_doc_path'   (verified-only doc — new key, absent today)

    Key name is review_fix_doc_path (NOT fix_doc_path — that key is OCCUPIED by the
    fix-worker output at L2257, read by satisfaction at L2328; collision was Opus-reject
    hole #1). AC6 consumer uses the same name review_fix_doc_path — consistent by design.

    FAILS today: _write_review_artifact returns only 'review_doc_path';
    'review_fix_doc_path' is not yet added.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    doc_path = reviews_dir / "build-review.md"

    spec_doc = scratchpad / "specs/build-spec.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\n", encoding="utf-8")

    red_log = scratchpad / "tests/build-red-output.log"
    red_log.parent.mkdir(parents=True, exist_ok=True)
    red_log.write_text("", encoding="utf-8")
    green_log = scratchpad / "tests/build-green-output.log"
    green_log.write_text("", encoding="utf-8")

    verified_findings = [
        _make_verified_finding("VerifiedAC5-Gamma", "HIGH"),
    ]

    aggregated_content = (
        "## Aggregated Findings\n\n"
        "### SEVERITY: HIGH — VerifiedAC5-Gamma\n\n"
        "## Suspect Findings\n\n"
        "### SEVERITY: MEDIUM — suspect-ac5 [verify: suspect-no-match]\n\n"
        "VERDICT: FAIL\n"
    )

    prev = StepResult(
        status="ok",
        data={
            "aggregated_content": aggregated_content,
            "verified_findings": verified_findings,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )

    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo",
    )

    from bytedigger_engine.workflows.phase_6_review import _write_review_artifact  # noqa: E402
    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC5: _write_review_artifact must return ok, got {result.status!r}: {result.error!r}"
    )

    data = result.data or {}

    # 'review_doc_path' must still be present (regression guard — passes today)
    assert "review_doc_path" in data, (
        "AC5: 'review_doc_path' key missing from _write_review_artifact result.data — "
        "regression: this key must always be present."
    )

    # 'review_fix_doc_path' must be present (new key — FAILS today)
    review_fix_doc_path_val = data.get("review_fix_doc_path")
    assert review_fix_doc_path_val is not None, (
        "AC5: 'review_fix_doc_path' key absent from _write_review_artifact result.data. "
        "FAILS today: the verified-only fix doc path is not yet threaded through. "
        "This key (review_fix_doc_path, NOT fix_doc_path) is required so "
        "_build_fix_prompt can reference the verified-only doc (§1y reachability — "
        "AC6 reads this same key from prev.data)."
    )

    # The value must point to the verified-only doc (not the full doc)
    fix_doc_relpath = getattr(_p6, "REVIEW_FIX_DOC_RELPATH", "reviews/build-review-fix.md")
    expected_fix_doc = str(scratchpad / fix_doc_relpath)
    assert str(review_fix_doc_path_val) == expected_fix_doc, (
        f"AC5: review_fix_doc_path in result.data must equal {expected_fix_doc!r}, "
        f"got {review_fix_doc_path_val!r}. Must point to the verified-only doc, "
        f"not the full review doc ({data.get('review_doc_path')!r})."
    )


# ─── AC11: _aggregate_review_findings forwards verified_findings (§1y producing-link) ─


def test_ac11_aggregate_review_findings_partitions_and_forwards_verified_only(tmp_path, monkeypatch):
    """AC11 (structural-exclusion test — core ship guarantee): _aggregate_review_findings
    partitions findings into verified vs suspect at L1603, and forwards ONLY the verified
    list under 'verified_findings' in StepResult.data. The suspect finding must NOT
    appear in that forwarded list.

    Fixture: two role files —
      - role-verified: one finding with a resolvable citation (verified-exact by verifier)
      - role-suspect: one finding with a citation to a NON-EXISTENT file (suspect-file-not-found)

    Assert: result.data['verified_findings'] contains the verified title and does NOT
    contain the suspect title. This proves the L1603 partition+forward excludes suspects,
    which is the structural guarantee that prevents them reaching the fix doc.

    Invocation mirrors test_phase_6_1F39FB1A_soft_tag.py _run_agg pattern.
    """
    from bytedigger_engine.workflows.phase_6_review import _aggregate_review_findings  # noqa: E402

    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # Real source file — citation verifier resolves this → verified-exact
    good_file = scratchpad / "good.py"
    good_file.write_text("def verified_ac11():\n    pass\n", encoding="utf-8")

    # role-verified: citation to real file:line → verified-exact
    verified_role_lines = [
        "# ac11 verified reviewer", "",
        "### SEVERITY: HIGH — VerifiedAC11-Finding",
        f"> {good_file}:1: def verified_ac11():",
        "Confidence: HIGH",
        "Description: verified finding for ac11",
        "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-ac11-verified.md").write_text(
        "\n".join(verified_role_lines), encoding="utf-8"
    )

    # role-suspect: citation to a NON-EXISTENT file → suspect-file-not-found
    suspect_role_lines = [
        "# ac11 suspect reviewer", "",
        "### SEVERITY: MEDIUM — SuspectAC11-TDA-Finding",
        "> /nonexistent/ac11_no_such_file_65695203.py:1: some code here",
        "Confidence: HIGH",
        "Description: suspect finding — file does not exist",
        "",
        "VERDICT: PARTIAL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-ac11-suspect.md").write_text(
        "\n".join(suspect_role_lines), encoding="utf-8"
    )

    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="test question",
    )
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")

    result = _aggregate_review_findings(ctx, prev)

    assert result.status == "ok", (
        f"AC11: _aggregate_review_findings must return ok, got {result.status!r}: "
        f"{result.error!r}"
    )

    data = result.data or {}

    # 'verified_findings' key must be present (the forward-link)
    assert "verified_findings" in data, (
        "AC11: 'verified_findings' key absent from _aggregate_review_findings result.data. "
        "This is the §1y producing-link: step 4 (_write_review_artifact) reads it to "
        "render the verified-only fix doc via _render_fix_doc."
    )

    verified = data.get("verified_findings", [])
    assert isinstance(verified, list), (
        f"AC11: 'verified_findings' in result.data must be a list, got {type(verified)}"
    )

    verified_titles = [f.get("title", "") for f in verified]

    # The verified finding (resolvable citation) must be in the forwarded list
    assert "VerifiedAC11-Finding" in verified_titles, (
        f"AC11: expected 'VerifiedAC11-Finding' in verified_findings, got: {verified_titles}. "
        f"The verified-exact finding must be forwarded to step 4."
    )

    # The suspect finding (unresolvable citation) must NOT be in the forwarded list
    # — this is the structural guarantee: suspects never reach the fix doc
    assert "SuspectAC11-TDA-Finding" not in verified_titles, (
        f"AC11 (structural-exclusion): 'SuspectAC11-TDA-Finding' (suspect-file-not-found) "
        f"must NOT appear in verified_findings. Got verified titles: {verified_titles}. "
        f"The L1603 partition must exclude suspects from the forwarded list — this is "
        f"the core ship guarantee: fix-worker never sees suspect findings."
    )
