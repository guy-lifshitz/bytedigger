"""RED tests for GH432 — AC-checklist parser false-FAIL: parse the resolved doc body.

Spec: SHARED/memory/Decisions/2026-07-08_GH432_ac_checklist_parser_contract_spec.md
Class: SYSTEMATIC. Tier: Option-D (modifies existing engine_py prod phase_6_review.py).

§1q-ext (D1CF5FDF): not-yet-existing symbols (AC_CHECKLIST_HEADER,
AC_CHECKLIST_ENTRY_FORMAT, AC_CHECKLIST_ENTRY_EXAMPLE) are looked up INSIDE test
bodies via getattr(p6, "...", None) so the file COLLECTS cleanly pre-GREEN and
FAILS at assert time, never at collect time. phase_6_review/StepResult/
WorkflowContext already exist and are imported at module scope (mirrors
test_gh388_ac_checklist_satisfaction.py; no module-level sys.path manipulation).
"""
from __future__ import annotations

from pathlib import Path

import phase_6_review as p6
from contracts import StepResult, WorkflowContext


# ─────────────────────────────────────────────────────────────────────────────
# Shared builders (local copies — do NOT import from test_gh388_*)
# ─────────────────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, threshold: int = 70, complexity: "str | None" = None) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "scratchpad_dir": str(scratch),
        "satisfaction_threshold": threshold,
    }
    if complexity is not None:
        cfg["complexity"] = complexity
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="GH432 ac checklist parser contract test",
        session_id="test-GH432",
        persona="hal",
        framework=None,
        domain=None,
    )


def _spec_with_two_acs_text() -> str:
    return (
        "# Spec\n\n"
        "## Acceptance Criteria\n"
        "1. first thing\n"
        "   Validation: t\n"
        "2) second thing\n"
        "   Validation: t\n\n"
        "## Open Questions\n"
        "none\n"
    )


def _write_spec(tmp_path: Path) -> Path:
    specs_dir = tmp_path / "scratch" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text(_spec_with_two_acs_text(), encoding="utf-8")
    return spec_path


def _control_signals_only_no_checklist(score: int = 95) -> str:
    """GH410/415 prompt-compliant happy path: raw carries ONLY control signals."""
    return (
        f"SCORE: {score}\n"
        "VERDICT: PASS\n"
        "\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": true, "fixes_required": []}\n'
        "```\n"
    )


def _worker_file_body(*, ac2_verdict: str = "PASS") -> str:
    return (
        "# Satisfaction Evaluation\n\n"
        "SCORE: 95\n"
        "VERDICT: PASS\n\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": true, "fixes_required": []}\n'
        "```\n\n"
        "## AC Checklist\n"
        "- AC1: PASS — foo.py:1 ev\n"
        f"- AC2: {ac2_verdict} — foo.py:2 ev\n\n"
        "## Concerns\n- none\n"
    )


def _make_prev_single(tmp_path: Path, *, raw_response: str, prewrite_doc_body: "str | None") -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    if prewrite_doc_body is not None:
        sat_doc.write_text(prewrite_doc_body, encoding="utf-8")
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Composite Review\n\n## Aggregated Findings\n\nVERDICT: PASS\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_path = _write_spec(tmp_path)
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(sat_doc),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )


def _make_prompt_prev(tmp_path: Path) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: SUSPECT\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_path = _write_spec(tmp_path)
    return StepResult(
        status="ok",
        data={
            "prompt": "dummy prompt",
            "doc_path": str(sat_doc),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "prompt_bytes": 12,
        },
        duration_ms=0,
        step_name="build_satisfaction_prompt",
    )


def _make_prev_multi(tmp_path: Path, evaluator_responses: list) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_path = _write_spec(tmp_path)
    ok_raws = [e["raw_response"] for e in evaluator_responses if e.get("status") == "ok" and e.get("raw_response")]
    return StepResult(
        status="ok",
        data={
            "raw_response": ok_raws[0] if ok_raws else "",
            "is_multi_evaluator": True,
            "evaluator_responses": evaluator_responses,
            "doc_path": str(sat_doc),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )


def _capture_emit(monkeypatch) -> list:
    captured: list = []
    monkeypatch.setattr(
        p6,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p)),
    )
    return captured


def _events_of_type(captured: list, event_type: str) -> list:
    return [p for et, p in captured if et == event_type]


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — GH410/415 regression: worker file has full-PASS checklist, raw doesn't ->
#        must PASS via resolved body, not false-FAIL on raw.
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac1_gh410_regression_worker_file_checklist_overrides_raw(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(
        tmp_path,
        raw_response=_control_signals_only_no_checklist(score=95),
        prewrite_doc_body=_worker_file_body(ac2_verdict="PASS"),
    )

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"GH432 AC1 FAIL: worker file has full-PASS AC Checklist but raw doesn't; "
        f"must NOT false-FAIL; got status={result.status!r}, "
        f"error_code={getattr(result, 'error_code', None)!r}, error={getattr(result, 'error', None)!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(p.get("verdict") == "pass" for p in ac_events), (
        f"GH432 AC1 FAIL: expected satisfaction_ac_checklist verdict='pass' via resolved "
        f"worker-file body; got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — worker file has a real AC2:FAIL entry -> gate must genuinely FAIL through
#        the resolved-body path (integrity preserved, not just fail-open).
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac2_worker_file_real_fail_entry_fails_gate(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(
        tmp_path,
        raw_response=_control_signals_only_no_checklist(score=95),
        prewrite_doc_body=_worker_file_body(ac2_verdict="FAIL"),
    )

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"GH432 AC2 FAIL: worker file has AC2:FAIL entry; gate must fail; got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_SATISFACTION_AC_CHECKLIST", (
        f"GH432 AC2 FAIL: expected E_SATISFACTION_AC_CHECKLIST; got "
        f"{getattr(result, 'error_code', None)!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(p.get("verdict") == "fail" and p.get("failed_count") == 1 for p in ac_events), (
        f"GH432 AC2 FAIL: expected verdict='fail' failed_count==1; got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — regression pin: neither worker file nor raw has the section -> still
#        verdict='fail' reason='missing_section' (GH388 semantics preserved).
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac3_neither_file_nor_raw_has_section_still_fails_missing_section(
    tmp_path: Path, monkeypatch
) -> None:
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(
        tmp_path,
        raw_response=_control_signals_only_no_checklist(score=95),
        prewrite_doc_body=None,
    )

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"GH432 AC3 setup: no checklist anywhere, spec has ACs -> must still fail; "
        f"got {result.status!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(
        p.get("verdict") == "fail" and p.get("reason") == "missing_section" for p in ac_events
    ), f"GH432 AC3 FAIL: expected verdict='fail' reason='missing_section'; got {ac_events!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — telemetry: satisfaction_ac_checklist event carries 'source' field matching
#        the resolved source (worker_file for AC1 fixture, raw_response_fallback
#        for AC3 fixture).
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac4_event_carries_source_field_matching_resolution(tmp_path: Path, monkeypatch) -> None:
    captured_worker = _capture_emit(monkeypatch)
    ctx1 = _make_ctx(tmp_path, threshold=70)
    prev1 = _make_prev_single(
        tmp_path,
        raw_response=_control_signals_only_no_checklist(score=95),
        prewrite_doc_body=_worker_file_body(ac2_verdict="PASS"),
    )
    p6._write_satisfaction_doc(ctx1, prev1)
    ac_events1 = _events_of_type(captured_worker, "satisfaction_ac_checklist")
    assert any(p.get("source") == "worker_file" for p in ac_events1), (
        f"GH432 AC4 FAIL: expected source='worker_file' on the event when a worker file was "
        f"resolved; got {ac_events1!r}"
    )

    tmp_path2 = tmp_path / "fallback_case"
    tmp_path2.mkdir()
    captured_fallback = _capture_emit(monkeypatch)
    ctx2 = _make_ctx(tmp_path2, threshold=70)
    prev2 = _make_prev_single(
        tmp_path2,
        raw_response=_control_signals_only_no_checklist(score=95),
        prewrite_doc_body=None,
    )
    p6._write_satisfaction_doc(ctx2, prev2)
    ac_events2 = _events_of_type(captured_fallback, "satisfaction_ac_checklist")
    assert any(p.get("source") == "raw_response_fallback" for p in ac_events2), (
        f"GH432 AC4 FAIL: expected source='raw_response_fallback' when no worker file exists; "
        f"got {ac_events2!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — §1g round-trip: AC_CHECKLIST_HEADER / AC_CHECKLIST_ENTRY_EXAMPLE constants
#        exist and are self-consistent with the header regex and the parser.
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac5_format_constants_round_trip(tmp_path: Path) -> None:
    header = getattr(p6, "AC_CHECKLIST_HEADER", None)
    example = getattr(p6, "AC_CHECKLIST_ENTRY_EXAMPLE", None)
    header_re = getattr(p6, "_AC_CHECKLIST_HEADER_RE", None)
    assert header is not None, "GH432 AC5 FAIL: AC_CHECKLIST_HEADER not defined in phase_6_review"
    assert example is not None, "GH432 AC5 FAIL: AC_CHECKLIST_ENTRY_EXAMPLE not defined"
    assert header_re is not None, "GH432 AC5 setup: _AC_CHECKLIST_HEADER_RE missing"

    assert header_re.match(header), (
        f"GH432 AC5 FAIL: _AC_CHECKLIST_HEADER_RE must match AC_CHECKLIST_HEADER literal; "
        f"header={header!r}"
    )
    parsed = p6._parse_ac_checklist(header + "\n" + example)
    assert parsed == {"1": "PASS"}, (
        f"GH432 AC5 FAIL: parsing header+example must yield {{'1':'PASS'}}; got {parsed!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — prompt single-source: both prompts contain the shared constants, no
#        header-literal drift (exactly one occurrence of the header per prompt).
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac6_prompts_use_shared_format_constants_no_drift(tmp_path: Path, monkeypatch) -> None:
    header = getattr(p6, "AC_CHECKLIST_HEADER", None)
    entry_format = getattr(p6, "AC_CHECKLIST_ENTRY_FORMAT", None)
    assert header is not None, "GH432 AC6 FAIL: AC_CHECKLIST_HEADER not defined"
    assert entry_format is not None, "GH432 AC6 FAIL: AC_CHECKLIST_ENTRY_FORMAT not defined"

    monkeypatch.delenv("HAL_AC_CHECKLIST_GATE", raising=False)

    ctx_complex = _make_ctx(tmp_path, complexity="COMPLEX")
    prev_complex = _make_prompt_prev(tmp_path)
    result_complex = p6._build_satisfaction_prompt(ctx_complex, prev_complex)
    assert result_complex.status == "ok", (
        f"GH432 AC6 setup FAIL (COMPLEX): {getattr(result_complex, 'error', None)!r}"
    )
    prompt_complex = (result_complex.data or {}).get("prompt", "")
    assert header in prompt_complex, "GH432 AC6 FAIL: header constant missing from COMPLEX prompt"
    assert entry_format in prompt_complex, (
        "GH432 AC6 FAIL: entry-format constant missing from COMPLEX prompt"
    )
    assert prompt_complex.count(header) == 1, (
        f"GH432 AC6 FAIL: header literal must appear exactly once in COMPLEX prompt; "
        f"count={prompt_complex.count(header)}"
    )

    ctx_default = _make_ctx(tmp_path, complexity=None)
    prev_default = _make_prompt_prev(tmp_path)
    result_default = p6._build_satisfaction_prompt(ctx_default, prev_default)
    assert result_default.status == "ok", (
        f"GH432 AC6 setup FAIL (default): {getattr(result_default, 'error', None)!r}"
    )
    prompt_default = (result_default.data or {}).get("prompt", "")
    assert header in prompt_default, "GH432 AC6 FAIL: header constant missing from default prompt"
    assert entry_format in prompt_default, (
        "GH432 AC6 FAIL: entry-format constant missing from default prompt"
    )
    assert prompt_default.count(header) == 1, (
        f"GH432 AC6 FAIL: header literal must appear exactly once in default prompt; "
        f"count={prompt_default.count(header)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — hardened bullet variants parse (bold/backtick-wrapped AC token, hyphenated id)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac7_hardened_bullet_variants_parse(tmp_path: Path) -> None:
    doc_text = (
        "## AC Checklist\n"
        "- **AC1**: PASS — ev\n"
        "- `AC2`: FAIL — ev\n"
        "* AC-3: pass — ev\n"
    )
    parsed = p6._parse_ac_checklist(doc_text)
    assert parsed == {"1": "PASS", "2": "FAIL", "3": "PASS"}, (
        f"GH432 AC7 FAIL: expected hardened variants to parse to "
        f"{{'1':'PASS','2':'FAIL','3':'PASS'}}; got {parsed!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — regression pin: strictness preserved, non-PASS/FAIL verdict tokens and
#        non-bullet lines are NOT captured as entries.
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac8_strictness_preserved_invalid_verdict_and_non_bullet_absent(tmp_path: Path) -> None:
    doc_text = (
        "## AC Checklist\n"
        "- AC1: MAYBE — ev\n"
        "AC1: PASS\n"
    )
    parsed = p6._parse_ac_checklist(doc_text)
    assert parsed == {}, (
        f"GH432 AC8 FAIL: invalid verdict token and non-bullet line must not be captured; "
        f"got {parsed!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — multi-eval regression pin: majority missing-checklist over raw_i still
#        fails the gate (GH388 AC11 semantics unchanged by the single-eval fix).
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac9_multi_eval_majority_missing_checklist_still_fails_gate(
    tmp_path: Path, monkeypatch
) -> None:
    captured = _capture_emit(monkeypatch)
    reject_calls: list = []
    monkeypatch.setattr(
        p6, "record_satisfaction_reject",
        lambda *a, **kw: reject_calls.append(a),
    )
    ctx = _make_ctx(tmp_path, threshold=70, complexity="COMPLEX")
    with_checklist = (
        _control_signals_only_no_checklist(90)
        + "\n## AC Checklist\n- AC1: PASS — foo.py:1 ev\n- AC2: PASS — foo.py:2 ev\n"
    )
    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _control_signals_only_no_checklist(95), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _control_signals_only_no_checklist(92), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": with_checklist, "error_code": None},
    ]
    prev = _make_prev_multi(tmp_path, evaluator_responses)

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"GH432 AC9 FAIL: 2-of-3 evaluators missing '## AC Checklist' in raw_i must still "
        f"flip the gate to error; got status={result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_SATISFACTION_AC_CHECKLIST", (
        f"GH432 AC9 FAIL: expected E_SATISFACTION_AC_CHECKLIST; got "
        f"{getattr(result, 'error_code', None)!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert len(ac_events) == 3, (
        f"GH432 AC9 FAIL: expected 3 per-evaluator satisfaction_ac_checklist events; "
        f"got {len(ac_events)}: {ac_events!r}"
    )
    assert len(reject_calls) == 1, (
        f"GH432 AC9 FAIL: expected record_satisfaction_reject called once; got {len(reject_calls)}"
    )
