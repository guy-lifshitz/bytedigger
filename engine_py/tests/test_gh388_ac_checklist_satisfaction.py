"""RED tests for GH388 — per-AC checklist forcing function in phase_6 satisfaction.

Spec: SHARED/memory/Decisions/2026-07-07_GH388_ac_checklist_satisfaction_spec.md
Class: SYSTEMATIC. Tier: Option-D (modifies existing engine_py prod phase_6_review.py).

New symbols under test (`_parse_spec_ac_ids`, `_parse_ac_checklist`, `_verify_ac_checklist`,
the `_write_satisfaction_doc`/`_write_satisfaction_doc_multi` AC-checklist wiring, and the
`_build_satisfaction_prompt` "## AC Checklist" section) DO NOT EXIST YET in production. Per
§1q extension D1CF5FDF, the new-symbol lookups are deferred to inside test bodies via
`getattr(p6, "...", None)` so the file COLLECTS cleanly and FAILS at assert time, never at
collect time. `phase_6_review` itself, `StepResult`, and `WorkflowContext` already exist and
are imported at module scope (mirrors test_4B9DF7D3_satisfaction_override.py / conftest
singleton sys.path — no module-level sys.path manipulation here, §1q/81F97F3D).
"""
from __future__ import annotations

from pathlib import Path

import phase_6_review as p6
from contracts import StepResult, WorkflowContext

# ─────────────────────────────────────────────────────────────────────────────
# Shared builders
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
        question="GH388 ac checklist test",
        session_id="test-GH388",
        persona="hal",
        framework=None,
        domain=None,
    )


def _spec_with_acs_text() -> str:
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


def _write_spec(tmp_path: Path, *, with_acs: bool) -> Path:
    specs_dir = tmp_path / "scratch" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / "build-spec.md"
    if with_acs:
        spec_path.write_text(_spec_with_acs_text(), encoding="utf-8")
    else:
        spec_path.write_text("# Spec\n\nNo acceptance criteria section here.\n", encoding="utf-8")
    return spec_path


def _raw_pass_no_checklist(score: int = 95) -> str:
    return (
        f"SCORE: {score}\n"
        "VERDICT: PASS\n"
        "\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": true, "fixes_required": []}\n'
        "```\n"
    )


def _raw_pass_with_checklist(score: int = 95) -> str:
    return _raw_pass_no_checklist(score) + (
        "\n## AC Checklist\n"
        "- AC1: PASS — foo.py:1 ev\n"
        "- AC2: PASS — foo.py:2 ev\n"
    )


def _raw_fail_score_only(score: int = 30) -> str:
    """score-only fail path (no structured block) -> reason_code score_only_below_threshold."""
    return f"SCORE: {score}\n"


def _make_prev_single(tmp_path: Path, *, raw_response: str, with_acs: bool) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Composite Review\n\n## Aggregated Findings\n\nVERDICT: PASS\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_path = _write_spec(tmp_path, with_acs=with_acs)
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


def _make_prev_multi(tmp_path: Path, evaluator_responses: list, *, with_acs: bool) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_path = _write_spec(tmp_path, with_acs=with_acs)
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


def _make_prompt_prev(tmp_path: Path) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: SUSPECT\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_path = _write_spec(tmp_path, with_acs=True)
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


def _capture_emit(monkeypatch) -> list:
    """Capture _emit_safe calls as (event_type, payload) tuples (infra capture, allowed)."""
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
# AC1 — _parse_spec_ac_ids: numbered items, mixed "1." / "2)", stops at next heading
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac1_parse_spec_ac_ids_numbered_mixed_stops_at_next_heading() -> None:
    fn = getattr(p6, "_parse_spec_ac_ids", None)
    assert fn is not None, "GH388 AC1 FAIL: _parse_spec_ac_ids not implemented in phase_6_review"
    spec_text = (
        "# Spec\n\n"
        "## Acceptance Criteria\n"
        "1. first thing\n"
        "2) second thing\n"
        "3. third thing\n\n"
        "## Open Questions\n"
        "4. this must NOT be collected\n"
    )
    assert fn(spec_text) == ["1", "2", "3"], (
        f"GH388 AC1 FAIL: expected ['1','2','3']; got {fn(spec_text)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — _parse_spec_ac_ids: table rows "| AC-7 | ... |" -> ["7"]
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac2_parse_spec_ac_ids_table_rows() -> None:
    fn = getattr(p6, "_parse_spec_ac_ids", None)
    assert fn is not None, "GH388 AC2 FAIL: _parse_spec_ac_ids not implemented"
    spec_text = (
        "## Acceptance Criteria\n"
        "| # | AC | Validation |\n"
        "|---|----|------------|\n"
        "| AC-7 | do X | val Y |\n\n"
        "## Next\n"
    )
    assert fn(spec_text) == ["7"], f"GH388 AC2 FAIL: expected ['7']; got {fn(spec_text)!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — _parse_spec_ac_ids: [] when section absent
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac3_parse_spec_ac_ids_empty_when_section_absent() -> None:
    fn = getattr(p6, "_parse_spec_ac_ids", None)
    assert fn is not None, "GH388 AC3 FAIL: _parse_spec_ac_ids not implemented"
    spec_text = "# Spec\n\nNo AC section anywhere in this document.\n"
    assert fn(spec_text) == [], f"GH388 AC3 FAIL: expected []; got {fn(spec_text)!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — _parse_ac_checklist: None when absent; dict (normalized upper) when present
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac4_parse_ac_checklist_none_when_absent_and_dict_when_present() -> None:
    fn = getattr(p6, "_parse_ac_checklist", None)
    assert fn is not None, "GH388 AC4 FAIL: _parse_ac_checklist not implemented"
    assert fn("# doc\nno checklist section here\n") is None, (
        "GH388 AC4 FAIL: expected None when '## AC Checklist' section is absent"
    )
    doc_text = (
        "# Satisfaction Evaluation\n\n"
        "## AC Checklist\n"
        "- AC1: PASS — a.py:1 ev\n"
        "- AC2: fail — b.py:2 ev\n\n"
        "## Concerns\n- none\n"
    )
    assert fn(doc_text) == {"1": "PASS", "2": "FAIL"}, (
        f"GH388 AC4 FAIL: expected {{'1':'PASS','2':'FAIL'}} (lowercase 'fail' normalized "
        f"upper); got {fn(doc_text)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — _verify_ac_checklist: skip/fail/pass verdicts, missing/failed lists, extras ignored
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac5_verify_ac_checklist_verdict_matrix() -> None:
    fn = getattr(p6, "_verify_ac_checklist", None)
    assert fn is not None, "GH388 AC5 FAIL: _verify_ac_checklist not implemented"

    verdict, detail = fn("", "anything")
    assert (verdict, detail.get("reason")) == ("skip", "no_spec_acs"), (
        f"GH388 AC5 FAIL: empty spec must skip(no_spec_acs); got {(verdict, detail)!r}"
    )

    spec_text = "## Acceptance Criteria\n1. one\n2. two\n"

    verdict, detail = fn(spec_text, "no checklist section here")
    assert (verdict, detail.get("reason")) == ("fail", "missing_section"), (
        f"GH388 AC5 FAIL: missing checklist section must fail(missing_section); got {(verdict, detail)!r}"
    )

    resp_partial = "## AC Checklist\n- AC1: PASS — x.py:1\n"
    verdict, detail = fn(spec_text, resp_partial)
    assert verdict == "fail" and detail.get("missing") == ["2"] and detail.get("failed") == [], (
        f"GH388 AC5 FAIL: expected fail with missing=['2']; got {(verdict, detail)!r}"
    )

    resp_fail = "## AC Checklist\n- AC1: PASS — x.py:1\n- AC2: FAIL — y.py:2\n"
    verdict, detail = fn(spec_text, resp_fail)
    assert verdict == "fail" and detail.get("failed") == ["2"] and detail.get("missing") == [], (
        f"GH388 AC5 FAIL: expected fail with failed=['2']; got {(verdict, detail)!r}"
    )

    resp_all_pass_with_extra = (
        "## AC Checklist\n- AC1: PASS — x.py:1\n- AC2: PASS — y.py:2\n- AC99: FAIL — z.py:9\n"
    )
    verdict, detail = fn(spec_text, resp_all_pass_with_extra)
    assert verdict == "pass", (
        f"GH388 AC5 FAIL: all spec ACs PASS (extra AC99 ignored) must pass; got {(verdict, detail)!r}"
    )
    assert detail.get("spec_ac_count") == 2, (
        f"GH388 AC5 FAIL: detail must carry spec_ac_count=2; got {detail!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — env kill-switch: HAL_AC_CHECKLIST_GATE=0 -> skip(env_disabled); would-fail flow -> ok
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac6_env_kill_switch_disables_gate(tmp_path: Path, monkeypatch) -> None:
    fn = getattr(p6, "_verify_ac_checklist", None)
    assert fn is not None, "GH388 AC6 FAIL: _verify_ac_checklist not implemented"
    monkeypatch.setenv("HAL_AC_CHECKLIST_GATE", "0")

    verdict, detail = fn("## Acceptance Criteria\n1. one\n", "no checklist here")
    assert (verdict, detail.get("reason")) == ("skip", "env_disabled"), (
        f"GH388 AC6 FAIL: kill-switch must yield skip(env_disabled); got {(verdict, detail)!r}"
    )

    # Integration: a would-fail flow (missing checklist, spec has ACs) must return ok
    # when the gate is disabled — the ac-checklist event itself must report skip/env_disabled.
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(tmp_path, raw_response=_raw_pass_no_checklist(score=95), with_acs=True)
    result = p6._write_satisfaction_doc(ctx, prev)
    assert result.status == "ok", (
        f"GH388 AC6 FAIL: HAL_AC_CHECKLIST_GATE=0 must not block a passing score/structured "
        f"flow even without '## AC Checklist'; got status={result.status!r}, "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(p.get("verdict") == "skip" for p in ac_events), (
        f"GH388 AC6 FAIL: expected a satisfaction_ac_checklist event with verdict='skip'; "
        f"got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — single-eval: missing checklist fails the gate with E_SATISFACTION_AC_CHECKLIST
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac7_single_eval_missing_checklist_fails_gate(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    reject_calls: list = []
    monkeypatch.setattr(
        p6, "record_satisfaction_reject",
        lambda *a, **kw: reject_calls.append((a, kw)),
    )
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(tmp_path, raw_response=_raw_pass_no_checklist(score=95), with_acs=True)

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"GH388 AC7 FAIL: passing score/structured but NO '## AC Checklist' (spec has 2 ACs) "
        f"must be gated to error; got status={result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_SATISFACTION_AC_CHECKLIST", (
        f"GH388 AC7 FAIL: expected error_code='E_SATISFACTION_AC_CHECKLIST'; "
        f"got {getattr(result, 'error_code', None)!r}"
    )
    assert "AC checklist gate failed" in (result.error or ""), (
        f"GH388 AC7 FAIL: error message must contain 'AC checklist gate failed'; "
        f"got {result.error!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(p.get("verdict") == "fail" for p in ac_events), (
        f"GH388 AC7 FAIL: expected satisfaction_ac_checklist event with verdict='fail'; "
        f"got {ac_events!r}"
    )
    assert len(reject_calls) == 1, (
        f"GH388 AC7 FAIL: expected record_satisfaction_reject called once; got {len(reject_calls)}"
    )
    args, _kw = reject_calls[0]
    assert args[0] == "ac_checklist_fail", (
        f"GH388 AC7 FAIL: record_satisfaction_reject must be called with reason_code "
        f"'ac_checklist_fail'; got {args[0]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — single-eval: checklist present, all spec ACs PASS -> gate stays ok
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac8_single_eval_with_full_pass_checklist_gate_ok(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(tmp_path, raw_response=_raw_pass_with_checklist(score=95), with_acs=True)

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"GH388 AC8 FAIL: passing score/structured WITH full-pass checklist must stay ok; "
        f"got status={result.status!r}, error_code={getattr(result, 'error_code', None)!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(p.get("verdict") == "pass" for p in ac_events), (
        f"GH388 AC8 FAIL: expected a satisfaction_ac_checklist event with verdict='pass'; "
        f"got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — non-masking: score<threshold wins over checklist-missing; event still emitted
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac9_non_masking_score_below_threshold_reason_wins(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(tmp_path, raw_response=_raw_fail_score_only(score=30), with_acs=True)

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "error", f"GH388 AC9 setup: expected error; got {result.status!r}"
    assert getattr(result, "error_code", None) == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"GH388 AC9 FAIL: a score-below-threshold reject must keep "
        f"E_SATISFACTION_BELOW_THRESHOLD (not masked by ac_checklist_fail); "
        f"got {getattr(result, 'error_code', None)!r}"
    )
    assert "satisfaction score" in (result.error or "") and "threshold" in (result.error or ""), (
        f"GH388 AC9 FAIL: error message must be the score-branch message; got {result.error!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert len(ac_events) == 1, (
        f"GH388 AC9 FAIL: satisfaction_ac_checklist event must STILL be emitted even when "
        f"the score-branch reason wins; got {ac_events!r}"
    )
    assert ac_events[0].get("verdict") == "fail", (
        f"GH388 AC9 FAIL: ac-checklist verdict should be 'fail' (missing section, spec has ACs); "
        f"got {ac_events[0]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC10 — back-compat skip: spec without '## Acceptance Criteria' -> verdict='skip'
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac10_backcompat_skip_when_spec_has_no_ac_section(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(tmp_path, raw_response=_raw_pass_no_checklist(score=95), with_acs=False)

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"GH388 AC10 setup: passing score/structured with no AC section must stay ok; "
        f"got {result.status!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert any(p.get("verdict") == "skip" for p in ac_events), (
        f"GH388 AC10 FAIL: expected satisfaction_ac_checklist event with verdict='skip' "
        f"(no spec ACs, backward-compat); got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC11 — multi-eval: majority missing checklist (2 of 3, spec has ACs) fails gate
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac11_multi_eval_majority_missing_checklist_fails_gate(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    reject_calls: list = []
    monkeypatch.setattr(
        p6, "record_satisfaction_reject",
        lambda *a, **kw: reject_calls.append(a),
    )
    ctx = _make_ctx(tmp_path, threshold=70, complexity="COMPLEX")
    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_pass_no_checklist(95), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_pass_no_checklist(92), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_pass_with_checklist(90), "error_code": None},
    ]
    prev = _make_prev_multi(tmp_path, evaluator_responses, with_acs=True)

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"GH388 AC11 FAIL: 2-of-3 evaluators missing '## AC Checklist' (spec has ACs) is a "
        f"majority checklist-fail and must flip a score-PASS gate to error; got "
        f"status={result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_SATISFACTION_AC_CHECKLIST", (
        f"GH388 AC11 FAIL: expected error_code='E_SATISFACTION_AC_CHECKLIST'; "
        f"got {getattr(result, 'error_code', None)!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert len(ac_events) == 3, (
        f"GH388 AC11 FAIL: expected 3 per-evaluator satisfaction_ac_checklist events; "
        f"got {len(ac_events)}: {ac_events!r}"
    )
    indices = sorted(p.get("evaluator_index") for p in ac_events)
    assert indices == [0, 1, 2], (
        f"GH388 AC11 FAIL: each event must carry a distinct evaluator_index 0,1,2; got {indices!r}"
    )
    assert len(reject_calls) == 1, (
        f"GH388 AC11 FAIL: expected record_satisfaction_reject called once on the flipped "
        f"gate; got {len(reject_calls)}"
    )
    assert reject_calls[0][0] == "ac_checklist_fail", (
        f"GH388 AC11 FAIL: expected reason_code 'ac_checklist_fail'; got {reject_calls[0][0]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC12 — prompt: '## AC Checklist' + cross-check instruction in COMPLEX and default
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac12_prompt_contains_ac_checklist_section_and_crosscheck_instruction(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("HAL_AC_CHECKLIST_GATE", raising=False)

    ctx_complex = _make_ctx(tmp_path, complexity="COMPLEX")
    prev_complex = _make_prompt_prev(tmp_path)
    result_complex = p6._build_satisfaction_prompt(ctx_complex, prev_complex)
    assert result_complex.status == "ok", (
        f"GH388 AC12 setup FAIL (COMPLEX): {getattr(result_complex, 'error', None)!r}"
    )
    prompt_complex = (result_complex.data or {}).get("prompt", "")
    assert "## AC Checklist" in prompt_complex, (
        "GH388 AC12 FAIL: '## AC Checklist' section missing from COMPLEX prompt"
    )
    assert "deterministically cross-checks" in prompt_complex, (
        "GH388 AC12 FAIL: cross-check instruction line missing from COMPLEX prompt"
    )
    # Existing control literals byte-preserved
    assert "# Satisfaction Evaluation" in prompt_complex
    assert "## Concerns" in prompt_complex
    assert "SCORE:" in prompt_complex

    ctx_default = _make_ctx(tmp_path, complexity=None)
    prev_default = _make_prompt_prev(tmp_path)
    result_default = p6._build_satisfaction_prompt(ctx_default, prev_default)
    assert result_default.status == "ok", (
        f"GH388 AC12 setup FAIL (default): {getattr(result_default, 'error', None)!r}"
    )
    prompt_default = (result_default.data or {}).get("prompt", "")
    assert "## AC Checklist" in prompt_default, (
        "GH388 AC12 FAIL: '## AC Checklist' section missing from default/SIMPLE prompt"
    )
    assert "deterministically cross-checks" in prompt_default, (
        "GH388 AC12 FAIL: cross-check instruction line missing from default/SIMPLE prompt"
    )
    assert "# Satisfaction Evaluation" in prompt_default
    assert "## Concerns" in prompt_default
    assert "SCORE:" in prompt_default
