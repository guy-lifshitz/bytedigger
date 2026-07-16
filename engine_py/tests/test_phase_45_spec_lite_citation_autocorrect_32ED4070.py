"""RED tests for 32ED4070 (F88217EB-P3) — reviewer citation auto-correct in phase_45_spec_lite.

Agreement 32ED4070: 94% of cycle-1 structured findings contain off-by-N line citations.
Engine emits REVISE → cycle 2 re-emits REVISE → cap → terminal abort E_REVIEW_FAILED.
Fix: post-process reviewer findings, repair citations that are off-by-≤3 (Tier-B exact
in-window), exclude the auto-corrected findings from the REVISE/SHIP count so cycle-1
SHIPs when only citation-drift findings were present.

New symbols:
  _CITATION_LINE_RE          — module constant (compiled regex, re.MULTILINE)
  _CITATION_WINDOW_LINES     — module constant (int, 3)
  _repair_finding_citation   — module helper (finding, file_cache, cwd) -> (bool, dict|None)
  _auto_correct_citations    — module helper (findings, spec_path) -> set[str]
  _write_review_doc          — existing function, extended with citation auto-correct call

All 12 tests MUST FAIL until GREEN implements the contract.
Do NOT implement any contract here — RED-only file.

Pattern reference: F88217EB Prong 1 (test_phase_45_spec_model_aware_timeouts_F88217EB.py).
Engine_py self-modification per delegation rule 35F1A4EB (Option D).
"""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import phase_45_spec_lite  # noqa: E402
from phase_45_spec_lite import (  # noqa: E402
    VERDICT_REVISE,
    VERDICT_SHIP,
    _write_review_doc,
)
from contracts import StepResult  # noqa: E402


# ─── shared test helpers ───────────────────────────────────────────────────────


def _make_prev(tmp_path: Path, raw: str, cycle: int = 1, spec_path: str | None = None) -> StepResult:
    """Build a fake StepResult matching what _invoke_review_llm returns."""
    doc_path = tmp_path / "build-plan-review.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    sp = spec_path if spec_path is not None else str(tmp_path / "build-spec.md")
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": sp,
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Monkeypatch phase_45_spec_lite._emit_safe; capture all calls.

    _emit_safe signature: (event_type: str, payload: dict) — no severity kwarg.
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_45_spec_lite,
        "_emit_safe",
        lambda et, p: captured.append({"type": et, "payload": p}),
    )
    return captured


def _structured_findings_raw(findings: list[dict]) -> str:
    """Build a ## Findings (structured) JSON block string from a list of finding dicts."""
    return (
        "## Findings (structured)\n"
        "```json\n"
        + json.dumps(findings, indent=2)
        + "\n```\n"
    )


# ─── AC1 — _CITATION_LINE_RE constant ─────────────────────────────────────────


def test_ac1_citation_line_re_constant():
    """AC1: _CITATION_LINE_RE is defined at module scope; pattern and flags are correct."""
    from phase_45_spec_lite import _CITATION_LINE_RE  # noqa: PLC0415

    assert isinstance(_CITATION_LINE_RE, type(re.compile(""))), (
        "_CITATION_LINE_RE must be a compiled regex object"
    )
    assert _CITATION_LINE_RE.pattern == r"^>\s+([^:]+):(\d+):(.*)$", (
        f"unexpected pattern: {_CITATION_LINE_RE.pattern!r}"
    )
    # re.MULTILINE must be set
    assert _CITATION_LINE_RE.flags & re.MULTILINE, (
        f"re.MULTILINE must be in flags, got flags={_CITATION_LINE_RE.flags!r}"
    )


# ─── AC2 — _CITATION_WINDOW_LINES constant ────────────────────────────────────


def test_ac2_citation_window_lines_constant():
    """AC2: _CITATION_WINDOW_LINES is an int with value 3."""
    from phase_45_spec_lite import _CITATION_WINDOW_LINES  # noqa: PLC0415

    assert isinstance(_CITATION_WINDOW_LINES, int), (
        f"_CITATION_WINDOW_LINES must be int, got {type(_CITATION_WINDOW_LINES)}"
    )
    assert _CITATION_WINDOW_LINES == 3, (
        f"expected 3, got {_CITATION_WINDOW_LINES}"
    )


# ─── AC3 — _repair_finding_citation signature ─────────────────────────────────


def test_ac3_repair_finding_citation_signature():
    """AC3: _repair_finding_citation exists with parameters (finding, file_cache, cwd) in that order."""
    from phase_45_spec_lite import _repair_finding_citation  # noqa: PLC0415

    sig = inspect.signature(_repair_finding_citation)
    param_names = list(sig.parameters.keys())
    assert param_names == ["finding", "file_cache", "cwd"], (
        f"expected parameters ['finding', 'file_cache', 'cwd'], got {param_names}"
    )


# ─── AC4 — Tier-A (exact match at cited line) → no mutation, (False, None) ───


def test_ac4_tier_a_already_correct(tmp_path: Path):
    """AC4: citation already correct at cited line → (False, None), evidence unchanged."""
    from phase_45_spec_lite import _repair_finding_citation  # noqa: PLC0415

    # Create a file where line 5 is exactly "    pass"
    foo = tmp_path / "foo.py"
    lines = ["line1\n", "line2\n", "line3\n", "line4\n", "    pass\n"]
    foo.write_text("".join(lines), encoding="utf-8")

    # Option-C strips one leading separator space: "> path:N: <CONTENT>" → quoted = " <CONTENT>"[1:] = "<CONTENT>"
    # So evidence must have 1 separator-space + 4 content-spaces = 5 spaces total after the second colon.
    original_evidence = f"> {foo}:5:     pass"
    finding: dict = {
        "id": "F1",
        "type": "citation",
        "evidence": original_evidence,
        "required_action": "fix",
    }

    repaired, info = _repair_finding_citation(finding, {}, tmp_path)

    assert repaired is False, f"expected False (already correct), got {repaired}"
    assert info is None, f"expected None, got {info}"
    assert finding["evidence"] == original_evidence, (
        f"evidence must not be mutated; expected {original_evidence!r}, got {finding['evidence']!r}"
    )


# ─── AC5 — off-by-1 (delta +1) → repair, evidence mutated ────────────────────


def test_ac5_off_by_1_positive_delta(tmp_path: Path):
    """AC5: citation at line 5, actual content at line 6 → (True, info with delta=1), evidence updated."""
    from phase_45_spec_lite import _repair_finding_citation  # noqa: PLC0415

    foo = tmp_path / "foo.py"
    lines = ["line1\n", "line2\n", "line3\n", "x = 1\n", "    pass\n"]
    foo.write_text("".join(lines), encoding="utf-8")

    # evidence cites line 5 but actual "    pass" is at line 5... wait:
    # lines[4] = "    pass\n" → line 5 = "    pass" (1-indexed).
    # But we want: line 5 = "x = 1", line 6 = "    pass".
    # So we need 6 lines total: line 5 = "x = 1", line 6 = "    pass"
    lines6 = ["line1\n", "line2\n", "line3\n", "line4\n", "x = 1\n", "    pass\n"]
    foo.write_text("".join(lines6), encoding="utf-8")

    # Option-C strips one separator-space: evidence must use 1 separator + 4 content spaces = 5 total.
    original_evidence = f"> {foo}:5:     pass"
    finding: dict = {
        "id": "F1",
        "type": "citation",
        "evidence": original_evidence,
        "required_action": "fix",
    }

    repaired, info = _repair_finding_citation(finding, {}, tmp_path)

    assert repaired is True, f"expected True (off-by-1), got {repaired}"
    assert info is not None, "expected info dict, got None"
    assert info["original_lineno"] == 5, f"expected original_lineno=5, got {info['original_lineno']}"
    assert info["new_lineno"] == 6, f"expected new_lineno=6, got {info['new_lineno']}"
    assert info["delta"] == 1, f"expected delta=1, got {info['delta']}"
    assert ":6:" in finding["evidence"], (
        f"evidence should contain ':6:' after repair, got {finding['evidence']!r}"
    )
    assert ":5:" not in finding["evidence"], (
        f"old line ref ':5:' should be gone from evidence, got {finding['evidence']!r}"
    )


# ─── AC6 — off-by-3 (delta -3) → repair ──────────────────────────────────────


def test_ac6_off_by_3_negative_delta(tmp_path: Path):
    """AC6: citation at line 8, actual content 'target' at line 5 → (True, info with delta=-3)."""
    from phase_45_spec_lite import _repair_finding_citation  # noqa: PLC0415

    foo = tmp_path / "foo.py"
    # 9 lines; line 5 = "target", line 8 = "wrong"
    content_lines = [
        "line1\n",
        "line2\n",
        "line3\n",
        "line4\n",
        "target\n",   # line 5
        "line6\n",
        "line7\n",
        "wrong\n",    # line 8
        "line9\n",
    ]
    foo.write_text("".join(content_lines), encoding="utf-8")

    original_evidence = f"> {foo}:8:target"
    finding: dict = {
        "id": "F1",
        "type": "citation",
        "evidence": original_evidence,
        "required_action": "fix",
    }

    repaired, info = _repair_finding_citation(finding, {}, tmp_path)

    assert repaired is True, f"expected True (off-by-3), got {repaired}"
    assert info is not None, "expected info dict, got None"
    assert info["original_lineno"] == 8, f"expected original_lineno=8, got {info['original_lineno']}"
    assert info["new_lineno"] == 5, f"expected new_lineno=5, got {info['new_lineno']}"
    assert info["delta"] == -3, f"expected delta=-3, got {info['delta']}"
    # evidence should now cite line 5
    assert ":5:target" in finding["evidence"], (
        f"evidence should contain ':5:target', got {finding['evidence']!r}"
    )


# ─── AC7 — off-by-4 (out of window) → (False, None), evidence unchanged ──────


def test_ac7_out_of_window_no_repair(tmp_path: Path):
    """AC7: citation at line 1, actual content at line 5 → delta=-4 → out of window → (False, None)."""
    from phase_45_spec_lite import _repair_finding_citation  # noqa: PLC0415

    foo = tmp_path / "foo.py"
    content_lines = [
        "wrong\n",    # line 1
        "line2\n",
        "line3\n",
        "line4\n",
        "target\n",   # line 5
    ]
    foo.write_text("".join(content_lines), encoding="utf-8")

    original_evidence = f"> {foo}:1:target"
    finding: dict = {
        "id": "F1",
        "type": "citation",
        "evidence": original_evidence,
        "required_action": "fix",
    }

    repaired, info = _repair_finding_citation(finding, {}, tmp_path)

    assert repaired is False, f"expected False (out of window, delta 4), got {repaired}"
    assert info is None, f"expected None, got {info}"
    assert finding["evidence"] == original_evidence, (
        f"evidence must not be mutated; got {finding['evidence']!r}"
    )


# ─── AC8 — relative path resolved under cwd ───────────────────────────────────


def test_ac8_relative_path_resolved_under_cwd(tmp_path: Path):
    """AC8a: file doesn't exist → (False, None). AC8b: relative path off-by-1 → repair with absolute path in info."""
    from phase_45_spec_lite import _repair_finding_citation  # noqa: PLC0415

    # AC8a: relative path with nonexistent file → (False, None)
    finding_a: dict = {
        "id": "F1",
        "type": "citation",
        "evidence": "> sub/nonexistent.py:5:    pass",
        "required_action": "fix",
    }
    repaired_a, info_a = _repair_finding_citation(finding_a, {}, tmp_path)
    assert repaired_a is False, "nonexistent file must return (False, None)"
    assert info_a is None

    # AC8b: relative path with existing file that is off-by-1 → repair succeeds
    sub = tmp_path / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    foo = sub / "foo.py"
    # line 5 = "x = 1", line 6 = "    pass"
    content_lines = [
        "line1\n",
        "line2\n",
        "line3\n",
        "line4\n",
        "x = 1\n",    # line 5
        "    pass\n", # line 6
    ]
    foo.write_text("".join(content_lines), encoding="utf-8")

    # Option-C strips one separator-space; content "    pass" needs 1+4=5 spaces after colon.
    finding_b: dict = {
        "id": "F2",
        "type": "citation",
        "evidence": "> sub/foo.py:5:     pass",
        "required_action": "fix",
    }
    repaired_b, info_b = _repair_finding_citation(finding_b, {}, tmp_path)

    assert repaired_b is True, f"expected repair to succeed for relative path, got {repaired_b}"
    assert info_b is not None, "expected info dict, got None"
    # info["path"] must be absolute (contain tmp_path prefix)
    assert str(tmp_path) in info_b["path"], (
        f"info['path'] must be absolute (contain tmp_path prefix); got {info_b['path']!r}"
    )
    assert info_b["path"].endswith("sub/foo.py"), (
        f"info['path'] must end with 'sub/foo.py'; got {info_b['path']!r}"
    )
    assert info_b["new_lineno"] == 6
    assert info_b["delta"] == 1


# ─── AC9 — _auto_correct_citations returns set of repaired ids ────────────────


def test_ac9_auto_correct_citations_returns_repaired_ids(tmp_path: Path):
    """AC9: 3 findings — F1 off-by-1, F2 exact, F3 out-of-window → returns {'F1'}, F1 mutated, F2/F3 unchanged."""
    from phase_45_spec_lite import _auto_correct_citations  # noqa: PLC0415

    foo = tmp_path / "target.py"
    content_lines = [
        "line1\n",
        "line2\n",
        "line3\n",
        "line4\n",
        "x = 1\n",    # line 5
        "    pass\n", # line 6 — F1 actual location
    ]
    foo.write_text("".join(content_lines), encoding="utf-8")

    # spec_path in same dir so cwd resolves to tmp_path
    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text("# Spec\n", encoding="utf-8")

    # F1: cites line 5, actual "    pass" is at line 6 → off-by-1 → repairs
    # Option-C strips 1 separator-space: 1 sep + 4 content = 5 spaces total after colon.
    f1_evidence_original = f"> {foo}:5:     pass"
    f1: dict = {"id": "F1", "type": "citation", "evidence": f1_evidence_original, "required_action": "fix"}

    # F2: exact citation at line 6 "    pass" → Tier-A already correct → no repair
    f2_evidence_original = f"> {foo}:6:     pass"
    f2: dict = {"id": "F2", "type": "citation", "evidence": f2_evidence_original, "required_action": "fix"}

    # F3: cites line 1, actual "    pass" at line 6 → delta=5 → out-of-window
    f3_evidence_original = f"> {foo}:1:     pass"
    f3: dict = {"id": "F3", "type": "citation", "evidence": f3_evidence_original, "required_action": "fix"}

    findings = [f1, f2, f3]
    result = _auto_correct_citations(findings, str(spec_path))

    assert isinstance(result, set), f"expected set, got {type(result)}"
    assert result == {"F1"}, f"expected {{'F1'}}, got {result}"

    # F1 evidence should now reference line 6
    assert ":6:" in f1["evidence"], (
        f"F1 evidence should cite line 6 after repair, got {f1['evidence']!r}"
    )
    # F2 evidence unchanged
    assert f2["evidence"] == f2_evidence_original, (
        f"F2 evidence should not be mutated, got {f2['evidence']!r}"
    )
    # F3 evidence unchanged
    assert f3["evidence"] == f3_evidence_original, (
        f"F3 evidence should not be mutated, got {f3['evidence']!r}"
    )


# ─── AC10 — telemetry event emitted per repaired finding ──────────────────────


def test_ac10_auto_correct_emits_telemetry(tmp_path: Path, monkeypatch):
    """AC10: _auto_correct_citations emits exactly one 'spec_lite_citation_auto_corrected' per repair.
    Payload keys: phase, finding_id, original_lineno, new_lineno, delta, path, match_tier.
    phase=='phase_45_spec_lite', match_tier=='B'.
    """
    from phase_45_spec_lite import _auto_correct_citations  # noqa: PLC0415

    foo = tmp_path / "src.py"
    content_lines = [
        "line1\n",
        "line2\n",
        "line3\n",
        "line4\n",
        "x = 1\n",       # line 5
        "correct_line\n", # line 6 — actual location
    ]
    foo.write_text("".join(content_lines), encoding="utf-8")

    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text("# Spec\n", encoding="utf-8")

    f1_evidence = f"> {foo}:5:correct_line"
    finding: dict = {"id": "F1", "type": "citation", "evidence": f1_evidence, "required_action": "fix"}

    captured = _patch_emit(monkeypatch)
    result = _auto_correct_citations([finding], str(spec_path))

    # Must have repaired F1
    assert "F1" in result, f"expected F1 to be repaired, got result={result}"

    auto_events = [e for e in captured if e["type"] == "spec_lite_citation_auto_corrected"]
    assert len(auto_events) == 1, (
        f"expected exactly 1 spec_lite_citation_auto_corrected event, got {len(auto_events)}; all={captured}"
    )
    payload = auto_events[0]["payload"]
    required_keys = {"phase", "finding_id", "original_lineno", "new_lineno", "delta", "path", "match_tier"}
    missing = required_keys - payload.keys()
    assert not missing, f"payload missing keys: {missing}; got keys {set(payload.keys())}"
    assert payload["phase"] == "phase_45_spec_lite", (
        f"expected phase='phase_45_spec_lite', got {payload['phase']!r}"
    )
    assert payload["match_tier"] == "B", (
        f"expected match_tier='B', got {payload['match_tier']!r}"
    )
    assert payload["finding_id"] == "F1", f"expected finding_id='F1', got {payload['finding_id']!r}"
    assert payload["original_lineno"] == 5
    assert payload["new_lineno"] == 6
    assert payload["delta"] == 1


# ─── AC11 — _write_review_doc: 2 findings, both auto-correct → SHIP ──────────


def test_ac11_write_review_doc_both_corrected_verdict_ship(tmp_path: Path, monkeypatch):
    """AC11: cycle-1 path, 2 findings both auto-correctable → verdict==SHIP.
    spec_lite_structured_verdict payload: n_findings==0, n_findings_total==2, n_findings_auto_corrected==2.
    """
    # Set up two source files, each off-by-1
    foo1 = tmp_path / "foo1.py"
    foo1.write_text("line1\nline2\nline3\nline4\nx=1\n    return True\n", encoding="utf-8")
    # line 5 = "x=1", line 6 = "    return True"

    foo2 = tmp_path / "foo2.py"
    foo2.write_text("line1\nline2\nline3\nline4\ny=2\n    return False\n", encoding="utf-8")
    # line 5 = "y=2", line 6 = "    return False"

    # Both findings cite line 5 but actual content is at line 6.
    # Option-C strips 1 separator-space: 1 sep + 4 content-spaces = 5 spaces total after colon.
    findings_list = [
        {
            "id": "F1",
            "type": "citation",
            "evidence": f"> {foo1}:5:     return True",
            "required_action": "fix line ref",
        },
        {
            "id": "F2",
            "type": "citation",
            "evidence": f"> {foo2}:5:     return False",
            "required_action": "fix line ref",
        },
    ]
    raw = (
        "## Verdict\nREVISE\n\n"
        + _structured_findings_raw(findings_list)
    )

    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text("# Spec\n", encoding="utf-8")

    prev = _make_prev(tmp_path, raw, cycle=1, spec_path=str(spec_path))
    captured = _patch_emit(monkeypatch)

    result = _write_review_doc(None, prev)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    assert result.data is not None

    # Verdict must be SHIP (all findings auto-corrected → n == 0)
    returned_verdict = result.data.get("verdict")
    assert returned_verdict == VERDICT_SHIP, (
        f"expected VERDICT_SHIP when both findings auto-corrected, got {returned_verdict!r}"
    )

    # spec_lite_structured_verdict event
    verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
    assert len(verdict_events) == 1, (
        f"expected 1 spec_lite_structured_verdict event, got {len(verdict_events)}; all={captured}"
    )
    p = verdict_events[0]["payload"]
    assert p.get("n_findings") == 0, f"expected n_findings==0, got {p.get('n_findings')!r}"
    assert p.get("n_findings_total") == 2, f"expected n_findings_total==2, got {p.get('n_findings_total')!r}"
    assert p.get("n_findings_auto_corrected") == 2, (
        f"expected n_findings_auto_corrected==2, got {p.get('n_findings_auto_corrected')!r}"
    )


# ─── AC12 — _write_review_doc: 2 findings, 1 auto-corrects → REVISE ──────────


def test_ac12_write_review_doc_one_corrected_verdict_revise(tmp_path: Path, monkeypatch):
    """AC12: cycle-1 path, 2 findings, only 1 auto-correctable → verdict==REVISE.
    spec_lite_structured_verdict payload: n_findings==1, n_findings_total==2, n_findings_auto_corrected==1.
    """
    # F1: off-by-1, will repair
    foo1 = tmp_path / "bar1.py"
    foo1.write_text("line1\nline2\nline3\nline4\nx=1\n    return True\n", encoding="utf-8")
    # line 5 = "x=1", line 6 = "    return True"

    # F2: citation out-of-window (cite line 1, actual at line 6 → delta=5 → no repair)
    foo2 = tmp_path / "bar2.py"
    foo2.write_text("line1\nline2\nline3\nline4\nx=2\n    return False\n", encoding="utf-8")

    # Option-C strips 1 separator-space: 1 sep + 4 content-spaces = 5 spaces total after colon.
    findings_list = [
        {
            "id": "F1",
            "type": "citation",
            "evidence": f"> {foo1}:5:     return True",
            "required_action": "fix line ref",
        },
        {
            "id": "F2",
            "type": "citation",
            # cites line 1 but "    return False" is at line 6 → delta=5 → out of window
            "evidence": f"> {foo2}:1:     return False",
            "required_action": "fix line ref",
        },
    ]
    raw = (
        "## Verdict\nREVISE\n\n"
        + _structured_findings_raw(findings_list)
    )

    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text("# Spec\n", encoding="utf-8")

    prev = _make_prev(tmp_path, raw, cycle=1, spec_path=str(spec_path))
    captured = _patch_emit(monkeypatch)

    result = _write_review_doc(None, prev)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    assert result.data is not None

    # Verdict must be REVISE (1 finding remains after auto-correct)
    returned_verdict = result.data.get("verdict")
    assert returned_verdict == VERDICT_REVISE, (
        f"expected VERDICT_REVISE when 1 finding remains, got {returned_verdict!r}"
    )

    # spec_lite_structured_verdict event
    verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
    assert len(verdict_events) == 1, (
        f"expected 1 spec_lite_structured_verdict event, got {len(verdict_events)}; all={captured}"
    )
    p = verdict_events[0]["payload"]
    assert p.get("n_findings") == 1, f"expected n_findings==1 (1 remains), got {p.get('n_findings')!r}"
    assert p.get("n_findings_total") == 2, f"expected n_findings_total==2, got {p.get('n_findings_total')!r}"
    assert p.get("n_findings_auto_corrected") == 1, (
        f"expected n_findings_auto_corrected==1, got {p.get('n_findings_auto_corrected')!r}"
    )
