"""RED tests for bd#91 - pre-GREEN gate is strict-AND, covers every AC, no vote softening.

Spec: docs/decisions/2026-10-02-bd91-pregreen-strict-and.md (rev r5), section 3.
Covers AC1..AC14, AC17..AC29 (AC15/AC16 live in test_bd91_spec_review_verdict.py).

Conventions
- New symbols (`_resolve_gate_passed`, `_forward_map_coverage`, `has_ac_checks_section`)
  are imported DEFERRED inside each test body (section 1q): a missing symbol fails the
  test at assert-time, never at collection.
- No module-level sys.path manipulation, no conftest import.
- SHIELD tests are green BEFORE GREEN on purpose (regression guards); every other
  test fails today for the reason named in its docstring.
- Time/singleton resources are pre-staged, never raced (workflows.md section 1i):
  the reroute ledger is real files under tmp_path, run ids are fixed strings.
- Units under test are never mocked. Only leaf infra is replaced: the telemetry sink
  (a capture event log installed through the real telemetry_ctx), HAL_REJECT_LOG
  (redirected to tmp_path), and `ac_dsl.admit` where the spec itself requires a raising
  admit.
"""
from __future__ import annotations

import hashlib
import json
import re
import types
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows import phase_5_implement as p5

RUN_ID = "bd91-run"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class _CaptureLog:
    """Event sink installed as telemetry_ctx's event_log (append + read_all)."""

    def __init__(self, reject_path: Path) -> None:
        self.events: list[tuple[str, dict]] = []
        self.reject_path = reject_path

    def append(self, event_type, payload, run_id="x"):
        self.events.append((event_type, dict(payload)))

    def read_all(self):
        return []

    def of(self, event_type: str) -> list[dict]:
        return [p for (t, p) in self.events if t == event_type]

    def rejects(self) -> list[dict]:
        if not self.reject_path.is_file():
            return []
        return [
            json.loads(ln)
            for ln in self.reject_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]


@pytest.fixture(autouse=True)
def log(tmp_path, monkeypatch):
    from bytedigger_engine import telemetry_ctx

    for name in (
        "HAL_SPEC_DEFECT_REROUTE",
        "HAL_AC_DSL_GATE_ENFORCE",
        "HAL_AC_DSL_GATE",
    ):
        monkeypatch.delenv(name, raising=False)
    # unrelated advisory lint: keep the PASS path deterministic and side-effect free
    monkeypatch.setenv("HAL_VERDICT_GATE_LINT", "0")
    cap = _CaptureLog(tmp_path / "reject-reasons.jsonl")
    monkeypatch.setenv("HAL_REJECT_LOG", str(cap.reject_path))
    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_current_run(event_log=cap, run_id=RUN_ID, step_name="gate_on_validation")
    try:
        yield cap
    finally:
        telemetry_ctx.clear_current_run()


_APPROVE = (
    "## validation-output (structured)\n```json\n"
    '{"approve": true, "reject_reason": null}\n```\n'
)
_REJECT = (
    "## validation-output (structured)\n```json\n"
    '{"approve": false, "reject_reason": "gap"}\n```\n'
)

_SPEC_3AC = """\
# Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | a | b |
| AC2 | a | b |
| AC3 | a | b |
"""

_SPEC_2AC = """\
# Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | a | b |
| AC2 | a | b |
"""

_RED_FILE = '''"""Module docstring. none TBD."""


def test_alpha_one():
    assert True


def test_beta_two():
    assert True
'''


def _raw(fm_bullets, reverse_paths, verdict="PASS", block=_APPROVE, extra=""):
    return (
        "## Forward Map\n" + "\n".join(fm_bullets) + "\n\n"
        "## Reverse Map\n" + "\n".join(f"- {p} -> AC1" for p in reverse_paths) + "\n\n"
        "## Spec Compliance\n- AC1 → present: ok\n\n"
        "## Quality Findings\nnone\n\n"
        f"## Verdict\nVerdict: {verdict}\n\n" + (block or "") + extra
    )


def _write_prev(tmp_path, raw, spec_text, red_paths, cycle=1, spec_name="spec.md"):
    spec = tmp_path / spec_name
    if spec_text is not None:
        spec.write_text(spec_text, encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(tmp_path / "reviews" / "build-validation.md"),
            "spec_path": str(spec),
            "red_log_path": str(tmp_path / "red.log"),
            "red_test_paths": red_paths,
            "cycle": cycle,
            "red_commit_sha": "deadbeef",
        },
        duration_ms=0,
        step_name="invoke_validation_llm",
    )


def _red_file(tmp_path, text=_RED_FILE, name="test_red_bd91.py") -> Path:
    d = tmp_path / "tests"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(text, encoding="utf-8")
    return f


def _ctx(tmp_path, **cfg):
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    org = {"scratchpad_dir": str(scratch)}
    org.update(cfg)
    return types.SimpleNamespace(org_config=org, question="q-bd91")


def _chain(tmp_path, raw, spec_text, red_paths, cycle=1, ctx=None):
    """Real production chain: write -> citations -> gate, no hand-built ac_coverage."""
    ctx = ctx or _ctx(tmp_path)
    written = p5._write_validation_doc(ctx, _write_prev(tmp_path, raw, spec_text, red_paths, cycle))
    assert written.status == "ok", written
    verified = p5._verify_validation_citations(ctx, written)
    assert verified.status == "ok", verified
    gated = p5._gate_on_validation(ctx, verified)
    return written, verified, gated


def _manual_gate_prev(verdict, *, cycle=1, structured=None, spec_path="/tmp/s.md", raw="...",
                      **extra_data):
    data = {
        "verdict": verdict,
        "cycle": cycle,
        "validation_doc_path": "/tmp/v.md",
        "spec_path": spec_path,
        "red_log_path": "/tmp/r.log",
        "validation_raw": raw,
        "red_commit_sha": "deadbeef",
        "red_test_paths": [],
    }
    if structured is not None:
        data["structured_verdict"] = structured
    data.update(extra_data)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_validation_doc")


def _sv(approve, category=None, reason=None):
    return types.SimpleNamespace(approve=approve, verdict_category=category, reject_reason=reason)


_COV_OK = {"status": "covered", "uncovered": [], "reasons": {}, "bullets": {},
           "unverifiable_reason": None}
_COV_GAP = {"status": "uncovered", "uncovered": ["AC3"], "reasons": {"AC3": "absent"},
            "bullets": {}, "unverifiable_reason": None}
_COV_UNVERIFIABLE = {"status": "unverifiable", "uncovered": [], "reasons": {}, "bullets": {},
                     "unverifiable_reason": "no_spec_acs"}


# --------------------------------------------------------------------------- #
# AC1 / AC27 - real chain; coverage gap blocks despite PASS + approve:true
# --------------------------------------------------------------------------- #


def _ac1_fixture(tmp_path, cycle=1):
    red = _red_file(tmp_path)
    raw = _raw(
        ["- AC1: `test_alpha_one`", "- AC2: `test_beta_two`"],
        [str(red)],
    )
    return raw, [str(red)], cycle


def test_ac1_uncovered_ac_blocks_pass_through_real_chain(tmp_path, log):
    """FAILS today: no AC coverage exists, PASS + approve:true proceeds."""
    raw, red_paths, cycle = _ac1_fixture(tmp_path)
    _w, verified, gated = _chain(tmp_path, raw, _SPEC_3AC, red_paths, cycle)
    # AC27 fixture precondition: real verify_validation_doc leaves the verdict PASS
    assert verified.data["verdict"] == "PASS"
    assert verified.data["verify_completeness_issues"] == 0

    assert verified.data["ac_coverage"]["status"] == "uncovered"
    assert gated.status == "ok"  # below cap: loop-continue
    assert "PASS" not in str(gated.data["verdict"]), gated.data
    assert "red_commit_sha" not in gated.data
    assert gated.data["gate_reason"] == "ac_gap"
    assert gated.data["ac_gap_ids"] == ["AC3"]
    assert gated.data["findings"].startswith("ENGINE AC-COVERAGE GAP (cycle 1): AC3")
    gaps = log.of("ac_coverage_gap")
    assert len(gaps) == 1, log.events
    assert gaps[0]["uncovered"] == ["AC3"]
    assert "bullets" in gaps[0]
    rows = log.rejects()
    assert len(rows) == 1, rows
    assert rows[0]["reason_code"] == "VALIDATION_AC_GAP"


def test_ac27_fixture_precondition_real_verifier_keeps_pass_shield(tmp_path):
    """SHIELD (green today): a full Reverse Map and no quote lines keep the verdict PASS."""
    raw, red_paths, _ = _ac1_fixture(tmp_path)
    ctx = _ctx(tmp_path)
    written = p5._write_validation_doc(ctx, _write_prev(tmp_path, raw, _SPEC_3AC, red_paths))
    verified = p5._verify_validation_citations(ctx, written)
    assert verified.data["verdict"] == "PASS"
    assert verified.data["verify_unverified"] == 0
    assert verified.data["verify_completeness_issues"] == 0


# --------------------------------------------------------------------------- #
# AC2 - _forward_map_coverage unit matrix
# --------------------------------------------------------------------------- #

_RED = '''"""Module docstring. none TBD."""
import pytest
# AC3 is mentioned in prose here


def test_one():
    assert True


def test_m(): pass
def test_real_name(): pass
def test_alpha_one(): pass
def test_ac1_x(): pass
def it_works(): pass
def testFoo(): pass
def def_helper(): pass


class TestFoo:
    def test_sub(self): pass


class TestCls:
    def test_m(self): pass


it("rejects empty input", () => {})
'''


def _doc(forward_lines, compliance=None):
    s = "## Forward Map\n" + "\n".join(forward_lines) + "\n\n"
    s += "## Reverse Map\n- tests/test_red.py -> x\n"
    if compliance is not None:
        s += "\n## Spec Compliance\n" + "\n".join(compliance) + "\n"
    s += "\n## Verdict\nVerdict: PASS\n"
    return s


_ONE = ["AC1"]

_AC2_CASES = [
    # --- uncovered: reserved words / not-a-citation (no_test_cited)
    ("reserved_NA", ["- AC1: `N/A`"], _ONE, None, {"AC1": "no_test_cited"}),
    ("reserved_TBD", ["- AC1: `TBD`"], _ONE, None, {"AC1": "no_test_cited"}),
    ("reserved_none", ["- AC1: `none`"], _ONE, None, {"AC1": "no_test_cited"}),
    ("deferral_token", ["- AC1: `[GREEN-regression-deferral]`"], _ONE, None,
     {"AC1": "no_test_cited"}),
    ("ac_id_is_not_citation", ["- AC1: `AC3`"], _ONE, None, {"AC1": "no_test_cited"}),
    ("keyword_assert", ["- AC1: `assert`"], _ONE, None, {"AC1": "no_test_cited"}),
    ("keyword_def", ["- AC1: `def`"], _ONE, None, {"AC1": "no_test_cited"}),
    ("no_backtick_no_token", ["- AC1: covered by the suite"], _ONE, None,
     {"AC1": "no_test_cited"}),
    # --- uncovered: citation does not exist in RED text
    ("invented_name", ["- AC1: `test_invented_name`"], _ONE, None,
     {"AC1": "citation_not_found"}),
    ("identifier_boundary", ["- AC1: `test_a`"], _ONE, None, {"AC1": "citation_not_found"}),
    # --- uncovered: MISSING / absent / dup
    ("missing_bullet", ["- AC1: `MISSING`"], _ONE, None, {"AC1": "MISSING"}),
    ("absent", ["- AC9: `test_one`"], _ONE, None, {"AC1": "absent"}),
    ("dup_one_missing", ["- AC1: `test_one`", "- AC1: `MISSING`"], _ONE, None,
     {"AC1": "MISSING"}),
    ("ac1_does_not_match_ac10", ["- AC10: `test_one`"], ["AC1", "AC10"], None,
     {"AC1": "absent"}),
    ("missing_in_tail_is_not_other_ac", ["- AC1: `test_one`", "- AC5: `MISSING` (unlike AC1)"],
     ["AC1", "AC5"], None, {"AC5": "MISSING"}),
    ("see_also_does_not_cover_other_ac", ["- AC1: `test_one` (see also AC5)"],
     ["AC1", "AC5"], None, {"AC5": "absent"}),
    # --- uncovered: Spec Compliance status
    ("compliance_partial_arrow", ["- AC3: `test_one`"], ["AC3"],
     ["- AC3 → partial: not all paths"], {"AC3": "spec_compliance_partial"}),
    ("compliance_missing", ["- AC3: `test_one`"], ["AC3"],
     ["- AC3: missing - nothing"], {"AC3": "spec_compliance_missing"}),
    ("compliance_partial_bold", ["- AC3: `test_one`"], ["AC3"],
     ["- AC3: **partial** - half"], {"AC3": "spec_compliance_partial"}),
    # --- covered
    ("go_Test", ["- AC1: `TestFoo`"], _ONE, None, {}),
    ("swift_test", ["- AC1: `testFoo`"], _ONE, None, {}),
    ("ts_quoted_description", ['- AC1: `"rejects empty input"`'], _ONE, None, {}),
    ("ts_it_wrapper", ['- AC1: `it("rejects empty input")`'], _ONE, None, {}),
    ("ts_file_gt_description", ["- AC1: `file.test.ts > rejects empty input`"], _ONE, None, {}),
    ("legacy_bare_arrow", ["- AC1 -> test_one"], _ONE, None, {}),
    ("path_prefixed", ["- AC1: `tests/test_red.py::test_real_name`"], _ONE, None, {}),
    ("pytest_class_path", ["- AC1: `tests/x.py::TestCls::test_m`"], _ONE, None, {}),
    ("rust_mod_path", ["- AC1: `mod::tests::it_works`"], _ONE, None, {}),
    ("dotted_class_method", ["- AC1: `TestCls.test_m`"], _ONE, None, {}),
    ("pytest_param_id", ["- AC1: `test_m[case1]`"], _ONE, None, {}),
    ("call_parens", ["- AC1: `test_m()`"], _ONE, None, {}),
    ("go_subtest", ["- AC1: `TestFoo/empty_input`"], _ONE, None, {}),
    ("table_row", ["| AC | Test |", "|---|---|", "| AC1 | `test_one` |"], _ONE, None, {}),
    ("list_head", ["- AC1, AC2: `test_one`"], ["AC1", "AC2"], None, {}),
    ("range_en_dash", ["- AC1–AC3: `test_one`"], ["AC1", "AC2", "AC3"], None, {}),
    ("range_dots", ["- AC1..AC3: `test_one`"], ["AC1", "AC2", "AC3"], None, {}),
    ("suffix_case_insensitive", ["- AC1A: `test_one`"], ["AC1a"], None, {}),
    ("compliance_no_missing_negatives", ["- AC3: `test_one`"], ["AC3"],
     ["- AC3: no missing negatives found"], {}),
    ("compliance_present_arrow", ["- AC3: `test_one`"], ["AC3"],
     ["- AC3 → present: all good"], {}),
    ("dup_second_cites", ["- AC1: `test_one`", "- AC1: covered"], _ONE, None, {}),
]


@pytest.mark.parametrize(
    "lines,ac_ids,compliance,expected",
    [c[1:] for c in _AC2_CASES],
    ids=[c[0] for c in _AC2_CASES],
)
def test_ac2_forward_map_coverage_matrix(lines, ac_ids, compliance, expected):
    """FAILS today: `_forward_map_coverage` does not exist."""
    from bytedigger_engine.workflows.phase_5_implement import _forward_map_coverage

    res = _forward_map_coverage(_doc(lines, compliance), list(ac_ids), [_RED])
    assert sorted(res["uncovered"]) == sorted(expected), res
    for ac_id, code in expected.items():
        assert res["reasons"][ac_id] == code, res
    assert res["status"] == ("uncovered" if expected else "covered"), res


@pytest.mark.parametrize("red_texts", [None, []], ids=["none", "empty_list"])
def test_ac2_forward_map_unreadable_red_is_unverifiable(red_texts):
    """FAILS today: `_forward_map_coverage` does not exist."""
    from bytedigger_engine.workflows.phase_5_implement import _forward_map_coverage

    res = _forward_map_coverage(_doc(["- AC1: `test_one`"]), ["AC1"], red_texts)
    assert res["status"] == "unverifiable", res
    assert res["unverifiable_reason"] == "red_files_unreadable"


def test_ac2_all_six_reason_codes_are_reachable():
    """FAILS today: `_forward_map_coverage` does not exist."""
    from bytedigger_engine.workflows.phase_5_implement import _forward_map_coverage

    seen = set()
    for _id, lines, ac_ids, comp, expected in _AC2_CASES:
        res = _forward_map_coverage(_doc(lines, comp), list(ac_ids), [_RED])
        seen.update(res["reasons"].values())
    assert seen >= {
        "absent", "MISSING", "no_test_cited", "citation_not_found",
        "spec_compliance_missing", "spec_compliance_partial",
    }, seen


# --------------------------------------------------------------------------- #
# AC3 / AC4 / AC5 / AC6 - strict-AND outcomes through the real gate
# --------------------------------------------------------------------------- #


def test_ac3_markdown_fail_with_approve_true_does_not_pass():
    """FAILS today: structured.approve overrides a markdown FAIL."""
    from bytedigger_engine.engine import _extract_marker_text

    prev = _manual_gate_prev("FAIL", cycle=1, structured=_sv(True))
    r = p5._gate_on_validation(None, prev)
    assert r.status == "ok"
    assert r.data["cycle"] == 2 and "findings" in r.data
    assert "PASS" not in str(r.data["verdict"])
    assert "PASS" not in _extract_marker_text(r, "verdict")  # LoopRunner re-iterates
    assert r.data["gate_reason"] == "markdown_fail"
    capped = p5._gate_on_validation(
        None, _manual_gate_prev("FAIL", cycle=p5.MAX_VALIDATION_CYCLES, structured=_sv(True))
    )
    assert capped.status == "error" and capped.error_code == "E_VALIDATION_FAILED"


def test_ac3_citation_demotion_to_fail_with_approve_true_does_not_pass(tmp_path):
    """FAILS today: verify_validation_doc demotes PASS->FAIL (Reverse Map misses the RED
    path) but approve:true still overrides it."""
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`", "- AC2: `test_beta_two`"], ["tests/other.py"])
    _w, verified, gated = _chain(tmp_path, raw, _SPEC_2AC, [str(red)])
    assert verified.data["verdict"] == "FAIL"  # precondition: the real demotion happened
    assert gated.status == "ok"
    assert "PASS" not in str(gated.data["verdict"]), gated.data
    assert gated.data["gate_reason"] == "markdown_fail"


def test_ac4_unknown_markdown_is_fail_closed_even_with_approve_true():
    """FAILS today: UNKNOWN + approve:true passes."""
    r = p5._gate_on_validation(None, _manual_gate_prev("UNKNOWN", cycle=1, structured=_sv(True)))
    assert r.status == "ok" and "red_commit_sha" not in r.data
    assert r.data["gate_reason"] == "markdown_missing"


def test_ac4_unknown_markdown_without_structured_is_fail_closed():
    """FAILS today only through the missing `gate_reason` field."""
    r = p5._gate_on_validation(None, _manual_gate_prev("UNKNOWN", cycle=1))
    assert r.status == "ok" and "red_commit_sha" not in r.data
    assert r.data["gate_reason"] == "markdown_missing"


def test_ac5_markdown_pass_with_structured_veto_blocks_shield():
    """SHIELD (green today)."""
    r = p5._gate_on_validation(
        None, _manual_gate_prev("PASS", cycle=p5.MAX_VALIDATION_CYCLES, structured=_sv(False))
    )
    assert r.status == "error" and r.error_code == "E_VALIDATION_FAILED"
    r1 = p5._gate_on_validation(None, _manual_gate_prev("PASS", cycle=1, structured=_sv(False)))
    assert r1.status == "ok" and "PASS" not in str(r1.data["verdict"])


@pytest.mark.parametrize(
    "block",
    [
        None,
        "## validation-output (structured)\n```json\n{not json\n```\n",
        "## validation-output (structured)\n```json\n{\"approve\": \"maybe\"}\n```\n",
    ],
    ids=["absent", "json_error", "schema_violation"],
)
def test_ac6_structured_missing_markdown_pass_proceeds_shield(tmp_path, block):
    """SHIELD (green today): no second voice -> markdown decides."""
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`", "- AC2: `test_beta_two`"], [str(red)], block=block)
    _w, _v, gated = _chain(tmp_path, raw, _SPEC_2AC, [str(red)])
    assert gated.status == "ok"
    assert gated.data["verdict"] == "PASS"
    assert "red_commit_sha" in gated.data


@pytest.mark.parametrize(
    "block",
    [None, "## validation-output (structured)\n```json\n{not json\n```\n"],
    ids=["absent", "json_error"],
)
def test_ac6_structured_missing_reason_is_md_only(tmp_path, block):
    """FAILS today: no `gate_reason` on the PASS return."""
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`", "- AC2: `test_beta_two`"], [str(red)], block=block)
    _w, _v, gated = _chain(tmp_path, raw, _SPEC_2AC, [str(red)])
    assert gated.data["gate_reason"] == "md_only"


# --------------------------------------------------------------------------- #
# AC7 - full 32-cell matrix
# --------------------------------------------------------------------------- #

_MD = ["PASS", "FAIL", "UNKNOWN", "PARTIAL"]
_ST = ["approve_true", "approve_false", "absent", "unparsed"]
_CV = ["uncovered", "not_uncovered"]


def _expected(md, st, cv):
    if md == "FAIL":
        return False, "markdown_fail"
    if md == "UNKNOWN":
        return False, "markdown_missing"
    if md != "PASS":
        return False, "markdown_partial"
    if st == "approve_false":
        return False, "structured_veto"
    if cv == "uncovered":
        return False, "ac_gap"
    return True, ("and" if st == "approve_true" else "md_only")


def _structured_for(st):
    if st == "approve_true":
        return _sv(True)
    if st == "approve_false":
        return _sv(False)
    return None  # "absent" and "unparsed": no second voice


@pytest.mark.parametrize("md", _MD)
@pytest.mark.parametrize("st", _ST)
@pytest.mark.parametrize("cv", _CV)
def test_ac7_resolve_gate_passed_full_matrix(md, st, cv):
    """FAILS today: `_resolve_gate_passed` does not exist."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_gate_passed

    cov = _COV_GAP if cv == "uncovered" else _COV_OK
    passed, reason = _resolve_gate_passed(md, _structured_for(st), cov)
    assert (passed, reason) == _expected(md, st, cv)
    canonical = p5._canonical_gate_verdict(passed, md)
    assert ("PASS" in canonical) == passed


def test_ac7_partial_is_markdown_partial_in_all_eight_cells():
    """FAILS today: `_resolve_gate_passed` does not exist."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_gate_passed

    for st in _ST:
        for cov in (_COV_GAP, _COV_OK):
            assert _resolve_gate_passed("PARTIAL", _structured_for(st), cov) == (
                False, "markdown_partial")
    assert _resolve_gate_passed("WEIRD_TOKEN", _sv(True), _COV_OK) == (False, "markdown_partial")


def test_ac7_unverifiable_and_missing_coverage_are_not_uncovered():
    """FAILS today: `_resolve_gate_passed` does not exist."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_gate_passed

    assert _resolve_gate_passed("PASS", _sv(True), _COV_UNVERIFIABLE) == (True, "and")
    assert _resolve_gate_passed("PASS", _sv(True), None) == (True, "and")
    assert _resolve_gate_passed("PASS", None, None) == (True, "md_only")


def _reroute_setup(tmp_path, md="FAIL", raw="validator: the spec itself is defective"):
    scratch = tmp_path / "gate-scratch"
    scratch.mkdir(exist_ok=True)
    spec = tmp_path / "spec-defective.md"
    spec.write_text("# defective spec\n", encoding="utf-8")
    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(scratch)}, question="q-bd91")
    structured = _sv(False, p5.VERDICT_CATEGORY_SPEC_DEFECT)
    prev = _manual_gate_prev(md, cycle=1, structured=structured, spec_path=str(spec), raw=raw)
    return ctx, prev, scratch, spec


def test_ac7_category_validator_spec_defect_survives_ac_gap_veto_order(tmp_path):
    """FAILS today: no coverage/ordering, and reroute is OFF by default.
    PASS + approve false (SPEC_DEFECT) + uncovered -> structured_veto wins over ac_gap
    -> the validator's SPEC_DEFECT is honoured (reroute)."""
    ctx, prev, _s, _sp = _reroute_setup(tmp_path, md="PASS")
    prev.data["ac_coverage"] = dict(_COV_GAP)
    r = p5._gate_on_validation(ctx, prev)
    assert r.status == "error" and r.error_code == "E_SPEC_DEFECT", r


def test_ac7_category_ac_gap_forces_test_gap_not_spec_defect(tmp_path):
    """FAILS today: approve:true + uncovered passes. ac_gap is never a SPEC_DEFECT reroute."""
    ctx, prev, scratch, _sp = _reroute_setup(tmp_path, md="PASS")
    prev.data["structured_verdict"] = _sv(True, p5.VERDICT_CATEGORY_SPEC_DEFECT)
    prev.data["ac_coverage"] = dict(_COV_GAP)
    r = p5._gate_on_validation(ctx, prev)
    assert r.error_code is None and r.status == "ok", r
    assert r.data["gate_reason"] == "ac_gap"
    assert not list((scratch / "resume").glob("spec-defect-reroutes-*.json"))


# --------------------------------------------------------------------------- #
# AC8 - prompt
# --------------------------------------------------------------------------- #


def test_ac8_validation_prompt_has_no_deferral_and_has_strict_bullet_format(tmp_path):
    """FAILS today: prompt still carries the TDD-pure GREEN-regression deferral."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake_wt = tmp_path / "fake_worktree"
    fake_wt.mkdir()
    inj = scratch / "injection"
    inj.mkdir()
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")
    from bytedigger_engine.contracts import WorkflowContext

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "current_worktree_path": str(fake_wt)},
        question="q", session_id="s", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "red_log_path": str(scratch / "tests" / "build-red-output.log"),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "cycle": 1,
            "red_test_paths": ["tests/test_foo.py"],
        },
        duration_ms=0, step_name="commit_red_tests",
    )
    prompt = p5._build_validation_prompt(ctx, prev).data["prompt"]
    assert "GREEN-regression-deferral" not in prompt
    assert "TDD-pure" not in prompt
    assert "- AC<n>:" in prompt
    assert "passes-pre-fix" in prompt


# --------------------------------------------------------------------------- #
# AC9 / AC10 / AC11 / AC20 (reroute) - default ON
# --------------------------------------------------------------------------- #


def test_ac9_spec_defect_reroutes_by_default_without_env(tmp_path):
    """FAILS today: default is OFF (legacy TEST_GAP retry)."""
    from bytedigger_engine.config_provider import get_config

    assert get_config().gate_enabled("HAL_SPEC_DEFECT_REROUTE") is True
    ctx, prev, _s, _sp = _reroute_setup(tmp_path)
    r = p5._gate_on_validation(ctx, prev)
    assert r.status == "error" and r.error_code == "E_SPEC_DEFECT", r
    assert int(r.data["reroute_attempt"]) >= 1
    assert r.data["spec_defect_reason"]


def test_ac10_reroute_writes_durable_ledger_with_spec_sha(tmp_path):
    """FAILS today (production side effect): without env nothing is written."""
    ctx, prev, scratch, spec = _reroute_setup(tmp_path)
    r = p5._gate_on_validation(ctx, prev)
    assert r.error_code == "E_SPEC_DEFECT", r
    ledger = scratch.resolve() / "resume" / f"spec-defect-reroutes-{RUN_ID}.json"
    assert ledger.is_file(), list(scratch.rglob("*"))
    sha = hashlib.sha256(spec.read_bytes()).hexdigest()[:12]
    assert sha in json.loads(ledger.read_text(encoding="utf-8"))["reroutes"]


def test_ac11_explicit_zero_is_legacy_test_gap_and_emits_disabled_event(tmp_path, monkeypatch, log):
    """FAILS today: the `spec_defect_reroute_disabled` event does not exist."""
    monkeypatch.setenv("HAL_SPEC_DEFECT_REROUTE", "0")
    ctx, prev, scratch, _sp = _reroute_setup(tmp_path)
    r = p5._gate_on_validation(ctx, prev)
    # legacy TEST_GAP retry, byte-for-byte
    assert r.status == "ok" and r.error_code is None
    assert r.data["cycle"] == 2 and "findings" in r.data
    assert not (scratch / "resume").exists()
    ev = log.of("spec_defect_reroute_disabled")
    assert len(ev) == 1, log.events
    assert ev[0]["cycle"] == 1 and ev[0]["source"] == "explicit"


@pytest.mark.parametrize("value", ["false", "off", ""])
def test_ac20_reroute_any_non_zero_value_is_enabled(tmp_path, monkeypatch, value):
    """FAILS today: flag() is True only for exactly '1'."""
    monkeypatch.setenv("HAL_SPEC_DEFECT_REROUTE", value)
    ctx, prev, _s, _sp = _reroute_setup(tmp_path)
    r = p5._gate_on_validation(ctx, prev)
    assert r.status == "error" and r.error_code == "E_SPEC_DEFECT", r


def test_ac20_reroute_exact_zero_is_legacy_shield(tmp_path, monkeypatch):
    """SHIELD (green today)."""
    monkeypatch.setenv("HAL_SPEC_DEFECT_REROUTE", "0")
    ctx, prev, _s, _sp = _reroute_setup(tmp_path)
    r = p5._gate_on_validation(ctx, prev)
    assert r.error_code != "E_SPEC_DEFECT" and r.status == "ok"


# --------------------------------------------------------------------------- #
# AC12 / AC13 / AC14 / AC20 (enforce) / AC21 / AC23 - _verify_spec_ac_dsl
# --------------------------------------------------------------------------- #

_VALID_SPEC = """\
# Test Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | file exists |

### AC-checks
```yaml
AC1:
  type: file_contains
  path: foo.py
  expect: present
```
"""

_UNCOMPILABLE_SPEC = """\
# Test Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | does the thing |

### AC-checks
```yaml
AC1:
  type: totally_unknown_check_type
  foo: bar
```
"""

_NO_CHECKS_SPEC = """\
# Test Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | does the thing |
"""


def _dsl_ctx():
    # directed repair is default ON: skip it so no repair LLM is ever spawned
    return types.SimpleNamespace(org_config={"complexity": "SIMPLE", "directed_repair_skip": True})


def _dsl_prev(path):
    return StepResult(status="ok", data={"spec_path": str(path), "cycle": 1}, duration_ms=0,
                      step_name="prev")


def _spec_file(tmp_path, text, name="spec.md"):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return f


def test_ac12_present_but_uncompilable_ac_checks_block_by_default(tmp_path):
    """FAILS today: ENFORCE defaults OFF (warn-only)."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(_spec_file(tmp_path, _UNCOMPILABLE_SPEC)))
    assert r.status == "error" and r.error_code == "E_SPEC_AC_UNCOMPILABLE", r


def test_ac13_admit_exception_degrades_without_env(tmp_path, monkeypatch, log):
    """FAILS today: no `spec_ac_dsl_unverified` marker."""
    from bytedigger_engine import ac_dsl
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    def _boom(_text):
        raise RuntimeError("admit boom")

    monkeypatch.setattr(ac_dsl, "admit", _boom)
    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(_spec_file(tmp_path, _VALID_SPEC)))
    assert r.status == "ok", r
    assert r.data.get("spec_ac_dsl_unverified") is True
    assert len(log.of("spec_ac_dsl_driver_error")) == 1


def test_ac14_env_skip_without_ac_checks_shield(tmp_path, monkeypatch):
    """SHIELD (green today)."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    monkeypatch.setenv("HAL_AC_DSL_GATE", "0")
    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(_spec_file(tmp_path, _NO_CHECKS_SPEC)))
    assert r.status == "ok" and r.data["spec_ac_dsl_skipped"] == "env_skip"


@pytest.mark.parametrize("value", ["false", "off", ""])
def test_ac20_enforce_any_non_zero_value_blocks(tmp_path, monkeypatch, value):
    """FAILS today: flag() is True only for exactly '1'."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", value)
    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(_spec_file(tmp_path, _UNCOMPILABLE_SPEC)))
    assert r.status == "error" and r.error_code == "E_SPEC_AC_UNCOMPILABLE", r


def test_ac20_enforce_exact_zero_is_warn_only_shield(tmp_path, monkeypatch):
    """SHIELD (green today)."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "0")
    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(_spec_file(tmp_path, _UNCOMPILABLE_SPEC)))
    assert r.status == "ok"


def test_ac21_legacy_spec_without_ac_checks_skips_with_payload(tmp_path, monkeypatch, log):
    """FAILS today: no legacy branch (admit is called, no `reason`, no skipped marker)."""
    from bytedigger_engine import ac_dsl
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    calls = []
    real_admit = ac_dsl.admit

    def _spy(text):
        calls.append(text)
        return real_admit(text)

    monkeypatch.setattr(ac_dsl, "admit", _spy)
    f = _spec_file(tmp_path, _NO_CHECKS_SPEC)
    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(f))
    assert r.status == "ok", r
    assert r.data["spec_ac_dsl_skipped"] == "legacy_no_ac_checks"
    warns = log.of("spec_ac_dsl_warn")
    assert len(warns) == 1, warns
    assert warns[0]["reason"] == "legacy_no_ac_checks"
    assert any("AC-checks section not found" in str(x) for x in warns[0]["reasons"])
    assert warns[0]["spec_path"] == str(f)
    assert calls == [], "admit must not be called for a spec without ### AC-checks"


def test_ac21_has_ac_checks_section_predicate():
    """FAILS today: `ac_dsl.has_ac_checks_section` does not exist."""
    from bytedigger_engine.ac_dsl import has_ac_checks_section

    assert has_ac_checks_section(_VALID_SPEC) is True
    assert has_ac_checks_section(_NO_CHECKS_SPEC) is False


def test_ac23_unreadable_spec_in_dsl_gate_degrades(tmp_path):
    """FAILS today: the spec read is outside the try (raises)."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_ac_dsl

    missing = tmp_path / "nope.md"
    r = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(missing))
    assert r.status == "ok", r
    assert r.data.get("spec_ac_dsl_unverified") is True

    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe\xfa\x80 not utf8 \xc3\x28")
    r2 = _verify_spec_ac_dsl(_dsl_ctx(), _dsl_prev(bad))
    assert r2.status == "ok", r2
    assert r2.data.get("spec_ac_dsl_unverified") is True


def test_ac23_unreadable_spec_or_parser_error_in_write_doc_is_unverifiable(tmp_path, monkeypatch, log):
    """FAILS today: no `ac_coverage` in the written data."""
    from bytedigger_engine import verdict_verify

    ctx = _ctx(tmp_path)
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`"], [str(red)])
    r = p5._write_validation_doc(ctx, _write_prev(tmp_path, raw, None, [str(red)]))  # spec absent
    assert r.status == "ok"
    assert r.data["ac_coverage"]["status"] == "unverifiable"
    assert len(log.of("ac_coverage_unverifiable")) == 1

    def _boom(_t):
        raise RuntimeError("parser boom")

    monkeypatch.setattr(verdict_verify, "parse_spec_ac_ids", _boom)
    r2 = p5._write_validation_doc(
        ctx, _write_prev(tmp_path, raw, _SPEC_2AC, [str(red)], spec_name="spec2.md"))
    assert r2.status == "ok"
    assert r2.data["ac_coverage"]["status"] == "unverifiable"


# --------------------------------------------------------------------------- #
# AC17 - flags_catalog per-entry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("flag", ["HAL_SPEC_DEFECT_REROUTE", "HAL_AC_DSL_GATE_ENFORCE"])
def test_ac17_flag_entries_are_default_on_gates_with_retire_by(flag):
    """FAILS today: kind='flag', default='0', expired flip-by."""
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS[flag]
    assert entry["kind"] == "gate"
    assert entry["default"] == "1"
    desc = entry["description"]
    assert "flip-by" not in desc
    m = re.search(r"retire-by:\s*(\d{4}-\d{2}-\d{2})", desc)
    assert m and m.group(1) == "2027-01-15", desc  # fixed constant, not 'today'
    assert "#91" in desc


# --------------------------------------------------------------------------- #
# AC18 / AC22 - unverifiable events
# --------------------------------------------------------------------------- #


def test_ac18_spec_without_parsable_acs_emits_exactly_one_unverifiable_event(tmp_path, log):
    """FAILS today: no event. markdown + structured decide."""
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`"], [str(red)])
    written, verified, gated = _chain(tmp_path, raw, "# Spec\n\nNo acceptance section.\n", [str(red)])
    assert written.data["ac_coverage"]["status"] == "unverifiable"
    assert len(log.of("ac_coverage_unverifiable")) == 1, log.events
    assert gated.status == "ok" and gated.data["verdict"] == "PASS"
    assert gated.data["gate_reason"] == "and"


def _manual_pass_prev():
    return _manual_gate_prev("PASS", cycle=1, structured=_sv(True))


def test_ac22_missing_ac_coverage_key_does_not_raise_and_passes_shield():
    """SHIELD (green today): a hand-built prev without `ac_coverage` still passes."""
    r = p5._gate_on_validation(None, _manual_pass_prev())
    assert r.status == "ok" and r.data["verdict"] == "PASS"
    assert "red_commit_sha" in r.data


def test_ac22_missing_key_reason_and_single_event(log):
    """FAILS today: no `gate_reason` on the PASS return and no event."""
    r = p5._gate_on_validation(None, _manual_pass_prev())
    assert r.data["gate_reason"] == "and"
    ev = log.of("ac_coverage_unverifiable")
    assert len(ev) == 1, log.events
    assert ev[0]["reason"] == "missing_key"


# --------------------------------------------------------------------------- #
# AC19 - verdict_verify.parse_spec_ac_ids header widening (+ no second parser)
# --------------------------------------------------------------------------- #


def test_ac19_bare_acceptance_header_with_table_yields_ids():
    """FAILS today: header must contain 'acceptance criteria'."""
    from bytedigger_engine import verdict_verify

    spec = "# S\n\n## §3 Acceptance\n\n| AC | x |\n|---|---|\n| AC1 | a |\n| AC2 | b |\n"
    assert verdict_verify.parse_spec_ac_ids(spec) == {"AC1", "AC2"}


def test_ac19_ac_table_outside_the_section_gives_no_phantoms_guard():
    """GUARD (green today, must stay green)."""
    from bytedigger_engine import verdict_verify

    spec = (
        "# S\n\n## §3 Acceptance\n\n| AC | x |\n|---|---|\n| AC1 | a |\n\n"
        "## §5 Sibling tests\n\n| AC | x |\n|---|---|\n| AC9 | z |\n"
    )
    assert verdict_verify.parse_spec_ac_ids(spec) == {"AC1"}


def test_ac19_early_acceptance_harness_does_not_steal_criteria_section_guard():
    """GUARD (green today, must stay green): pass 1 wins over the looser pass 2."""
    from bytedigger_engine import verdict_verify

    spec = (
        "# S\n\n### Acceptance-test harness\n\n| AC | x |\n|---|---|\n| AC77 | h |\n\n"
        "## Acceptance Criteria\n\n| AC | x |\n|---|---|\n| AC1 | a |\n"
    )
    assert verdict_verify.parse_spec_ac_ids(spec) == {"AC1"}


def test_ac19_bare_acceptance_numbered_list_yields_no_phantoms_guard():
    """GUARD (green today, must stay green): numbered fallback is off in pass 2."""
    from bytedigger_engine import verdict_verify

    spec = "# S\n\n## Acceptance\n\n1. first thing\n2. second thing\n"
    assert verdict_verify.parse_spec_ac_ids(spec) == set()


def test_ac19_write_validation_doc_uses_the_single_parser(tmp_path, monkeypatch):
    """FAILS today: `_write_validation_doc` never parses spec ACs."""
    from bytedigger_engine import verdict_verify

    calls = []

    def _sentinel(text):
        calls.append(text)
        return {"AC1"}

    monkeypatch.setattr(verdict_verify, "parse_spec_ac_ids", _sentinel)
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`"], [str(red)])
    p5._write_validation_doc(_ctx(tmp_path), _write_prev(tmp_path, raw, _SPEC_2AC, [str(red)]))
    assert len(calls) == 1, "must delegate to verdict_verify.parse_spec_ac_ids (section 1g)"


# --------------------------------------------------------------------------- #
# AC24 - drift event
# --------------------------------------------------------------------------- #


def test_ac24_drift_event_carries_resolved_and_error_severity(tmp_path, monkeypatch):
    """FAILS today: payload has no `resolved`, severity is 'warning'."""
    seen = []

    def _rec(event_type, payload, severity="warning"):
        seen.append((event_type, dict(payload), severity))

    monkeypatch.setattr(p5, "_emit_safe", _rec)
    red = _red_file(tmp_path)
    raw = _raw(["- AC1: `test_alpha_one`"], [str(red)], block=_REJECT)  # md PASS vs approve false
    p5._write_validation_doc(_ctx(tmp_path), _write_prev(tmp_path, raw, _SPEC_2AC, [str(red)]))
    drift = [(p, s) for (t, p, s) in seen if t == "validation_verdict_drift"]
    assert len(drift) == 1, seen
    payload, severity = drift[0]
    assert "resolved" in payload and payload["resolved"] is not None
    assert severity == "error" or payload.get("severity") == "error"


# --------------------------------------------------------------------------- #
# AC25 - terminal ac_gap
# --------------------------------------------------------------------------- #


def test_ac25_ac_gap_at_cap_is_terminal_with_fields_and_reject_row(tmp_path, log):
    """FAILS today: PASS + approve:true proceeds; no ac_gap fields."""
    raw, red_paths, _ = _ac1_fixture(tmp_path)
    cap = p5.MAX_VALIDATION_CYCLES
    _w, _v, gated = _chain(tmp_path, raw, _SPEC_3AC, red_paths, cycle=cap)
    assert gated.status == "error" and gated.error_code == "E_VALIDATION_FAILED", gated
    assert gated.data["gate_reason"] == "ac_gap"
    assert gated.data["ac_gap_ids"] == ["AC3"]
    assert gated.data["findings"].startswith(f"ENGINE AC-COVERAGE GAP (cycle {cap}): AC3")
    rows = log.rejects()
    assert len(rows) == 1 and rows[0]["reason_code"] == "VALIDATION_AC_GAP", rows


# --------------------------------------------------------------------------- #
# AC26 - RED file resolution
# --------------------------------------------------------------------------- #


def _cov_write(tmp_path, ctx, red_paths, bullets):
    raw = _raw(bullets, ["x"])
    return p5._write_validation_doc(ctx, _write_prev(tmp_path, raw, _SPEC_2AC, red_paths))


def test_ac26a_all_red_files_unreadable_is_unverifiable_not_fail(tmp_path, log):
    """FAILS today: no ac_coverage."""
    ctx = _ctx(tmp_path)
    r = _cov_write(tmp_path, ctx, [str(tmp_path / "gone-a.py"), str(tmp_path / "gone-b.py")],
                   ["- AC1: `test_alpha_one`", "- AC2: `test_beta_two`"])
    cov = r.data["ac_coverage"]
    assert cov["status"] == "unverifiable"
    assert cov["unverifiable_reason"] == "red_files_unreadable"
    assert len(log.of("ac_coverage_unverifiable")) == 1


def test_ac26b_partially_readable_list_checks_against_the_readable_file(tmp_path):
    """FAILS today: no ac_coverage."""
    real = _red_file(tmp_path, "def test_real_one(): pass\n", "test_real.py")
    ctx = _ctx(tmp_path)
    r = _cov_write(tmp_path, ctx, [str(real), str(tmp_path / "gone.py")],
                   ["- AC1: `test_real_one`", "- AC2: `test_only_in_gone_file`"])
    cov = r.data["ac_coverage"]
    assert cov["status"] == "uncovered"
    assert cov["uncovered"] == ["AC2"]
    assert cov["reasons"]["AC2"] == "citation_not_found"


def _rel_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_rel.py").write_text("def test_rel_real(): pass\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return repo, elsewhere


def test_ac26c_relative_paths_resolve_from_git_cwd_not_process_cwd(tmp_path, monkeypatch):
    """FAILS today: no ac_coverage."""
    repo, elsewhere = _rel_repo(tmp_path)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(tmp_path, git_cwd=str(repo))
    r = _cov_write(tmp_path, ctx, ["tests/test_rel.py"],
                   ["- AC1: `test_rel_real`", "- AC2: `test_rel_real`"])
    assert r.data["ac_coverage"]["status"] == "covered", r.data["ac_coverage"]


def test_ac26c_relative_paths_invented_citation_is_not_found_not_unverifiable(tmp_path, monkeypatch):
    """FAILS today: no ac_coverage."""
    repo, elsewhere = _rel_repo(tmp_path)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(tmp_path, git_cwd=str(repo))
    r = _cov_write(tmp_path, ctx, ["tests/test_rel.py"],
                   ["- AC1: `test_rel_real`", "- AC2: `test_totally_invented`"])
    cov = r.data["ac_coverage"]
    assert cov["status"] == "uncovered", cov
    assert cov["reasons"]["AC2"] == "citation_not_found"


def test_ac26d_worktree_path_is_used_when_git_cwd_is_absent(tmp_path, monkeypatch):
    """FAILS today: no ac_coverage."""
    repo, elsewhere = _rel_repo(tmp_path)
    monkeypatch.chdir(elsewhere)
    ctx = types.SimpleNamespace(org_config={"current_worktree_path": str(repo)}, question="q")
    r = _cov_write(tmp_path, ctx, ["tests/test_rel.py"],
                   ["- AC1: `test_rel_real`", "- AC2: `test_rel_real`"])
    assert r.data["ac_coverage"]["status"] == "covered", r.data["ac_coverage"]


def test_ac26e_absolute_entry_is_used_as_is(tmp_path, monkeypatch):
    """FAILS today: no ac_coverage."""
    real = _red_file(tmp_path, "def test_abs_real(): pass\n", "test_abs.py")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.chdir(decoy)
    ctx = _ctx(tmp_path, git_cwd=str(decoy))
    r = _cov_write(tmp_path, ctx, [str(real)],
                   ["- AC1: `test_abs_real`", "- AC2: `test_abs_real`"])
    assert r.data["ac_coverage"]["status"] == "covered", r.data["ac_coverage"]


# --------------------------------------------------------------------------- #
# AC28 - PARTIAL fail-closed through the real chain
# --------------------------------------------------------------------------- #


def test_ac28_partial_from_citation_verifier_blocks_despite_approve_true(tmp_path):
    """FAILS today: approve:true overrides the PARTIAL demotion."""
    red = _red_file(tmp_path)
    quote = f"> {tmp_path}/no-such-file.py:3: some quoted line\n"
    raw = _raw(
        ["- AC1: `test_alpha_one`", "- AC2: `test_beta_two`"], [str(red)], extra=quote)
    _w, verified, gated = _chain(tmp_path, raw, _SPEC_2AC, [str(red)])
    assert verified.data["verdict"] == "PARTIAL"  # precondition: real verifier demoted
    assert gated.status == "ok"
    assert "PASS" not in str(gated.data["verdict"]), gated.data
    assert gated.data["gate_reason"] == "markdown_partial"


# --------------------------------------------------------------------------- #
# AC29 - pinning: verdict_verify widened, phase_6_review untouched
# --------------------------------------------------------------------------- #

_SPEC_BARE_ACCEPTANCE = "# S\n\n## §3 Acceptance\n\n| AC | x |\n|---|---|\n| AC1 | a |\n| AC2 | b |\n"


def test_ac29_verdict_verify_half_parses_bare_acceptance_and_documents_divergence():
    """FAILS today: bare '## §3 Acceptance' parses to an empty set; no divergence note."""
    from bytedigger_engine import verdict_verify

    assert verdict_verify.parse_spec_ac_ids(_SPEC_BARE_ACCEPTANCE) == {"AC1", "AC2"}
    doc = (verdict_verify.parse_spec_ac_ids.__doc__ or "").lower()
    assert "phase_6_review" in doc
    assert any(w in doc for w in ("diverge", "differ", "расхожд")), doc


def test_ac29_phase6_half_is_unchanged_shield():
    """SHIELD (green today, must stay green): phase 6 hard gate keeps skipping."""
    from bytedigger_engine.workflows import phase_6_review

    assert phase_6_review._parse_spec_ac_ids(_SPEC_BARE_ACCEPTANCE) == []
    verdict, detail = phase_6_review._verify_ac_checklist(
        _SPEC_BARE_ACCEPTANCE, "satisfaction answer without a checklist section")
    assert verdict == "skip" and detail["reason"] == "no_spec_acs"
