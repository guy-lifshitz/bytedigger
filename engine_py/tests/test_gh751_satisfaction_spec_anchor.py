"""RED tests for GH751 — satisfaction-path spec-anchor (drift observability).

Spec: SHARED/memory/Decisions/2026-07-14_GH751_satisfaction_spec_anchor_spec.md
Class: SYSTEMATIC (drift-detection). Tier: Option-D (modifies existing engine_py
prod phase_6_review.py).

Production side of this change (`review_spec_anchor` event at reviewer-dispatch
and the `spec_sha` field on `satisfaction_ac_checklist` events) DOES NOT EXIST YET.
Per §1q extension D1CF5FDF the not-yet-existing behavior is asserted via captured
event payloads (dict.get) inside test bodies — never a module-level import of a
not-yet-existing symbol — so this file COLLECTS cleanly and FAILS at assert time.
`file_sha256` already exists in `verdict_verify` and is safe to import at module
scope. Mirrors `test_gh388_ac_checklist_satisfaction.py` builder/capture patterns
(no module-level sys.path manipulation, §1q/81F97F3D).
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.workflows import phase_6_review as p6
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.verdict_verify import file_sha256

# ─────────────────────────────────────────────────────────────────────────────
# Shared builders (mirrors test_gh388_ac_checklist_satisfaction.py)
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
        question="GH751 spec anchor test",
        session_id="test-GH751",
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


def _write_spec(tmp_path: Path, *, with_acs: bool = True) -> Path:
    """Writes the spec at scratch/specs/build-spec.md — matches SPEC_DOC_RELPATH,
    the same relpath `_build_review_prompt` reads from ctx's scratchpad, AND the
    relpath the gh388 satisfaction builders wire into `prev.data["spec_path"]`."""
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


def _make_prev_single(tmp_path: Path, *, raw_response: str, spec_path: Path) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Composite Review\n\n## Aggregated Findings\n\nVERDICT: PASS\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
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


def _make_prev_multi(tmp_path: Path, evaluator_responses: list, *, spec_path: Path) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
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
# AC1/AC2 — reviewer-dispatch: review_spec_anchor event with correct spec_sha/path
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac1_build_review_prompt_emits_spec_anchor_with_correct_sha(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    spec_path = _write_spec(tmp_path, with_acs=True)
    expected_sha = file_sha256(spec_path)
    ctx = _make_ctx(tmp_path)

    result = p6._build_review_prompt(ctx, StepResult(status="ok", data={}, duration_ms=0, step_name="prev"))

    assert result.status == "ok", f"GH751 AC1 setup FAIL: {getattr(result, 'error', None)!r}"
    anchor_events = _events_of_type(captured, "review_spec_anchor")
    assert any(p.get("spec_sha") == expected_sha for p in anchor_events), (
        f"GH751 AC1 FAIL: expected a 'review_spec_anchor' event with spec_sha="
        f"{expected_sha!r}; got {anchor_events!r}"
    )


def test_ac2_build_review_prompt_spec_anchor_carries_spec_path(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    spec_path = _write_spec(tmp_path, with_acs=True)
    ctx = _make_ctx(tmp_path)

    result = p6._build_review_prompt(ctx, StepResult(status="ok", data={}, duration_ms=0, step_name="prev"))

    assert result.status == "ok", f"GH751 AC2 setup FAIL: {getattr(result, 'error', None)!r}"
    anchor_events = _events_of_type(captured, "review_spec_anchor")
    assert any(p.get("spec_path") == str(spec_path) for p in anchor_events), (
        f"GH751 AC2 FAIL: expected a 'review_spec_anchor' event with spec_path="
        f"{str(spec_path)!r}; got {anchor_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — reviewer-dispatch: spec ABSENT -> no crash, spec_sha=None (or no event)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac3_build_review_prompt_absent_spec_no_crash_sha_none(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    # No _write_spec call -> scratch/specs/build-spec.md does not exist.
    ctx = _make_ctx(tmp_path)

    result = p6._build_review_prompt(ctx, StepResult(status="ok", data={}, duration_ms=0, step_name="prev"))

    assert result.status == "ok", (
        f"GH751 AC3 FAIL: missing spec file must not crash _build_review_prompt; "
        f"got status={result.status!r}, error={getattr(result, 'error', None)!r}"
    )
    anchor_events = _events_of_type(captured, "review_spec_anchor")
    assert anchor_events, (
        "GH751 AC3 FAIL: expected a 'review_spec_anchor' event to ALWAYS be emitted "
        "(even when the spec file is absent); got none"
    )
    assert any("spec_sha" in p and p["spec_sha"] is None for p in anchor_events), (
        f"GH751 AC3 FAIL: expected a 'review_spec_anchor' event whose payload CONTAINS "
        f"the 'spec_sha' key with value None; got {anchor_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — single-eval satisfaction: satisfaction_ac_checklist carries spec_sha
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac4_single_eval_satisfaction_ac_checklist_carries_spec_sha(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    spec_path = _write_spec(tmp_path, with_acs=True)
    expected_sha = file_sha256(spec_path)
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(tmp_path, raw_response=_raw_pass_no_checklist(score=95), spec_path=spec_path)

    p6._write_satisfaction_doc(ctx, prev)

    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert ac_events, "GH751 AC4 setup FAIL: no satisfaction_ac_checklist event captured"
    assert any(p.get("spec_sha") == expected_sha for p in ac_events), (
        f"GH751 AC4 FAIL: expected a satisfaction_ac_checklist event with spec_sha="
        f"{expected_sha!r}; got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — multi-eval satisfaction: every per-evaluator event carries the same spec_sha
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac5_multi_eval_satisfaction_ac_checklist_carries_spec_sha_per_evaluator(
    tmp_path: Path, monkeypatch
) -> None:
    captured = _capture_emit(monkeypatch)
    spec_path = _write_spec(tmp_path, with_acs=True)
    expected_sha = file_sha256(spec_path)
    ctx = _make_ctx(tmp_path, threshold=70, complexity="COMPLEX")
    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_pass_no_checklist(95), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_pass_no_checklist(92), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_pass_no_checklist(90), "error_code": None},
    ]
    prev = _make_prev_multi(tmp_path, evaluator_responses, spec_path=spec_path)

    p6._write_satisfaction_doc(ctx, prev)

    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert len(ac_events) == 3, f"GH751 AC5 setup FAIL: expected 3 events; got {len(ac_events)}: {ac_events!r}"
    assert all(p.get("spec_sha") == expected_sha for p in ac_events), (
        f"GH751 AC5 FAIL: expected every per-evaluator satisfaction_ac_checklist event to "
        f"carry spec_sha={expected_sha!r}; got {ac_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — pure-hash drift primitive (documents observability signal; passes today)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac6_file_sha256_detects_drift_on_byte_mutation(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, with_acs=True)
    sha_before = file_sha256(spec_path)
    spec_path.write_text(_spec_with_acs_text() + "\nmutated content\n", encoding="utf-8")
    sha_after = file_sha256(spec_path)
    assert sha_before != sha_after, (
        f"GH751 AC6 FAIL: hashing the same path before/after a byte mutation must differ "
        f"(drift-detectable primitive); got identical sha {sha_before!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — satisfaction: unreadable/missing spec -> spec_sha=None, no crash
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac7_satisfaction_unreadable_spec_sha_none_no_crash(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_emit(monkeypatch)
    missing_spec_path = tmp_path / "scratch" / "specs" / "does-not-exist.md"
    ctx = _make_ctx(tmp_path, threshold=70)
    prev = _make_prev_single(
        tmp_path, raw_response=_raw_pass_no_checklist(score=95), spec_path=missing_spec_path
    )

    result = p6._write_satisfaction_doc(ctx, prev)

    assert result.status in ("ok", "error"), (
        f"GH751 AC7 FAIL: unreadable spec must not raise an uncaught exception; "
        f"got status={result.status!r}"
    )
    ac_events = _events_of_type(captured, "satisfaction_ac_checklist")
    assert ac_events, "GH751 AC7 setup FAIL: no satisfaction_ac_checklist event captured"
    assert any("spec_sha" in p and p["spec_sha"] is None for p in ac_events), (
        f"GH751 AC7 FAIL: expected a satisfaction_ac_checklist event whose payload CONTAINS "
        f"the 'spec_sha' key with value None when the spec is missing/unreadable; "
        f"got {ac_events!r}"
    )
