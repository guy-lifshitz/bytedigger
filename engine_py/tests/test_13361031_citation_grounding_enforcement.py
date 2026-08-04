"""RED tests for 13361031 — make the spec-reviewer citation-grounding count
non-decorative (Path B self-enforcement + telemetry hook).

Spec: SHARED/memory/Decisions/2026-05-12_13361031_citation_grounding_enforcement_spec.md.

ALL tests must FAIL today (no production change yet):
  AC1 — _citation_grounding_rubric() still has the old soft phrase and no MUST rule.
  AC2 — _review_output_schema() has no grounding cross-reference near ## Verdict.
  AC3 — _GROUNDING_COUNT_RE does not exist on the module.
  AC4–AC8 — _write_review_doc emits no citation_grounding_* events at all.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest  # noqa: F401

HERE = Path(__file__).parent
ENGINE_PY = HERE.parent
sys.path.insert(0, str(ENGINE_PY))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import (  # noqa: E402
    _citation_grounding_rubric,
    _review_output_schema,
    _write_review_doc,
)
from bytedigger_engine.workflows import phase_45_spec as _m45  # noqa: E402  — used for monkeypatch + _GROUNDING_COUNT_RE probe


# ---------------------------------------------------------------------------
# Helpers — minimal ctx / prev for _write_review_doc
# ---------------------------------------------------------------------------

def _make_review_prev(tmp_path: Path, raw: str, *, cycle: int = 1) -> StepResult:
    """Build a StepResult shaped like _invoke_review_llm → _write_review_doc input.

    _write_review_doc reads:
      prev.data["raw_response"]  — the raw review text
      prev.data["doc_path"]      — where to write the review file
      prev.data["spec_path"]     — path to the spec (just needs to be a string)
      prev.data.get("cycle", 1)  — review cycle number
    """
    review_file = tmp_path / "specs" / "build-plan-review.md"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    # spec_path need not exist for the telemetry hook (only used for data passthrough)
    spec_file = tmp_path / "specs" / "build-plan.md"
    spec_file.write_text("## Context\nstub spec\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(review_file),
            "spec_path": str(spec_file),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )


def _make_ctx(tmp_path: Path) -> types.SimpleNamespace:
    """Minimal ctx for _write_review_doc — it only uses _ctx for org_config
    lookups that are not on the code path exercised here (cycle-1 happy path).
    The function signature is _write_review_doc(_ctx, prev) and _ctx is unused
    in the paths we exercise (cycle 1, structured findings absent → falls
    through to classic _parse_verdict path)."""
    return types.SimpleNamespace(
        org_config={"scratchpad_dir": str(tmp_path)},
        tenant_id="hal",
        session_id="test-13361031",
    )


# ---------------------------------------------------------------------------
# AC1 — _citation_grounding_rubric() must be hardened to a MUST rule
# ---------------------------------------------------------------------------

def test_ac1_rubric_contains_must_revise_hard_rule():
    """AC1: _citation_grounding_rubric() must contain the literal format-spec
    substring 'Citation grounding count: <grounded>/<total> grounded (<ungrounded>
    ungrounded)', must say MUST and REVISE near each other, must say 'ungrounded',
    and must NOT contain the old soft phrase 'should push the verdict toward REVISE'.

    FAILS today: the old soft phrase is still present; no MUST rule exists.
    """
    r = _citation_grounding_rubric()

    # The format-spec substring that the engine regex will parse must be literal.
    assert (
        "Citation grounding count: <grounded>/<total> grounded (<ungrounded> ungrounded)"
        in r
    ), "rubric must contain the literal format-spec substring"

    # Hard enforcement words required.
    assert "MUST" in r, "rubric must contain the word MUST (hard rule)"
    assert "REVISE" in r, "rubric must contain the word REVISE"
    assert "ungrounded" in r, "rubric must mention 'ungrounded'"

    # Proximity check: MUST and REVISE must appear close together (hard rule sentence).
    assert re.search(r"MUST\b.{0,80}\bREVISE", r, re.S), (
        "MUST and REVISE must appear within ~80 chars of each other (hard rule)"
    )

    # OLD soft phrase must be gone.
    assert "should push the verdict toward REVISE" not in r, (
        "old soft phrase 'should push the verdict toward REVISE' must be removed"
    )


# ---------------------------------------------------------------------------
# AC2 — _review_output_schema() must cross-reference grounding near ## Verdict
# ---------------------------------------------------------------------------

# Pre-change length of _review_output_schema() — measured from the source as written
# at spec-freeze time (lines 498-534 of phase_45_spec.py). Used as a size guard:
# GREEN must add text so length strictly exceeds this value.
#
# The string spans from "OUTPUT — your response IS..." through "...anti-scope discipline.\n"
# Computed by summing the string literals: approximately 748 chars.
# We use 700 as a conservative floor (any truncation of the current text
# would break other tests anyway). The real assertion is the substring checks.
_SCHEMA_CURRENT_LEN = 700  # conservative lower-bound of pre-change length


def test_ac2_schema_mentions_grounding_near_verdict():
    """AC2: _review_output_schema() must contain 'grounding' (case-insensitive)
    and 'REVISE' near the ## Verdict section, and must be longer than the
    pre-change baseline.

    FAILS today: the ## Verdict section has no grounding cross-reference.
    'grounding' does not appear in the schema string at all.
    """
    s = _review_output_schema()

    # After GREEN: 'grounding' must appear (case-insensitive).
    assert "grounding" in s.lower(), (
        "_review_output_schema() must mention 'grounding' (case-insensitive) "
        "as a cross-reference near the ## Verdict section"
    )

    # 'REVISE' already present; guard against accidental removal.
    assert "REVISE" in s, "_review_output_schema() must still contain 'REVISE'"

    # Grounding mention must be in the ## Verdict vicinity (within 200 chars after it).
    verdict_idx = s.find("## Verdict")
    assert verdict_idx >= 0, "## Verdict section must still exist in schema"
    vicinity = s[verdict_idx : verdict_idx + 200]
    assert "grounding" in vicinity.lower(), (
        "'grounding' must appear within 200 chars after '## Verdict' in the schema "
        "(the cross-reference should be co-located with the verdict format)"
    )

    # Schema must grow after GREEN adds the cross-reference line.
    assert len(s) > _SCHEMA_CURRENT_LEN, (
        f"_review_output_schema() length ({len(s)}) must exceed pre-change "
        f"baseline ({_SCHEMA_CURRENT_LEN}); GREEN must add the grounding line"
    )


# ---------------------------------------------------------------------------
# AC3 — _GROUNDING_COUNT_RE must exist and parse correctly
# ---------------------------------------------------------------------------

def test_ac3_grounding_count_re_parses_correctly():
    """AC3: _GROUNDING_COUNT_RE must exist on the module and correctly parse
    the 'Citation grounding count: N/N grounded (N ungrounded)' line with
    whitespace/case tolerance.

    FAILS today: _GROUNDING_COUNT_RE does not exist → AttributeError.
    """
    # Reference via getattr so only this test fails if the attribute is missing
    # (rather than failing the entire module import).
    _GROUNDING_COUNT_RE = getattr(_m45, "_GROUNDING_COUNT_RE", None)
    assert _GROUNDING_COUNT_RE is not None, (
        "phase_45_spec._GROUNDING_COUNT_RE does not exist — GREEN must add it"
    )

    # Canonical form.
    m = _GROUNDING_COUNT_RE.search(
        "Citation grounding count: 2/5 grounded (3 ungrounded)"
    )
    assert m is not None, "regex must match canonical form"
    assert m.group(1) == "2", f"group(1) should be '2', got {m.group(1)!r}"
    assert m.group(2) == "5", f"group(2) should be '5', got {m.group(2)!r}"
    assert m.group(3) == "3", f"group(3) should be '3', got {m.group(3)!r}"

    # Whitespace + case tolerant form.
    m2 = _GROUNDING_COUNT_RE.search(
        "citation GROUNDING count:  10 / 10  GROUNDED  ( 0  UNGROUNDED )"
    )
    assert m2 is not None, "regex must be whitespace/case-tolerant"
    assert m2.groups() == ("10", "10", "0"), (
        f"whitespace/case groups should be ('10','10','0'), got {m2.groups()!r}"
    )

    # Non-matching text.
    assert _GROUNDING_COUNT_RE.search("no count here") is None, (
        "regex must not match unrelated text"
    )


# ---------------------------------------------------------------------------
# AC4–AC8 shared fixture helpers
# ---------------------------------------------------------------------------

def _run_write_review_doc(tmp_path, monkeypatch, raw_review: str, *, cycle: int = 1):
    """Call _write_review_doc with monkeypatched _emit_safe spy.

    Returns (result, spy_list).
    spy_list contains (event_type, payload) tuples for every _emit_safe call.
    """
    spy: list[tuple[str, dict]] = []
    monkeypatch.setattr(_m45, "_emit_safe", lambda et, payload: spy.append((et, payload)))

    ctx = _make_ctx(tmp_path)
    prev = _make_review_prev(tmp_path, raw_review, cycle=cycle)
    result = _write_review_doc(ctx, prev)
    return result, spy


def _grounding_events(spy):
    """Filter spy for citation_grounding_self_enforcement events."""
    return [(et, p) for et, p in spy if et == "citation_grounding_self_enforcement"]


def _missing_events(spy):
    """Filter spy for citation_grounding_count_missing events."""
    return [(et, p) for et, p in spy if et == "citation_grounding_count_missing"]


# ---------------------------------------------------------------------------
# AC4 — SHIP + 3 ungrounded → self_enforcement event with compliant=False
# ---------------------------------------------------------------------------

def test_ac4_ship_with_ungrounded_emits_noncompliant_event(tmp_path, monkeypatch):
    """AC4: review text SHIP + 3 ungrounded → exactly one citation_grounding_self_enforcement
    event with grounded=2, total=5, ungrounded=3, verdict='SHIP', compliant=False.

    FAILS today: _write_review_doc emits no citation_grounding_* events.
    """
    raw = (
        "## Verdict\nSHIP\n\n"
        "## Findings (structured)\n[]\n\n"
        "## Concerns Checked\n"
        "Citation grounding count: 2/5 grounded (3 ungrounded)\n"
        "- something\n"
    )
    result, spy = _run_write_review_doc(tmp_path, monkeypatch, raw)

    # Function must still return ok (hook is observe-only).
    assert result.status == "ok", f"_write_review_doc must return status='ok', got {result.status!r}"

    events = _grounding_events(spy)
    assert len(events) == 1, (
        f"expected exactly 1 'citation_grounding_self_enforcement' event, got {len(events)}: {spy}"
    )
    _, payload = events[0]
    assert payload["grounded"] == 2, f"grounded should be 2, got {payload['grounded']}"
    assert payload["total"] == 5, f"total should be 5, got {payload['total']}"
    assert payload["ungrounded"] == 3, f"ungrounded should be 3, got {payload['ungrounded']}"
    assert payload["verdict"] == "SHIP", f"verdict should be 'SHIP', got {payload['verdict']!r}"
    assert payload["compliant"] is False, (
        "compliant must be False when ungrounded>=1 and verdict==SHIP"
    )


# ---------------------------------------------------------------------------
# AC5 — REVISE + 4 ungrounded → self_enforcement event with compliant=True
# ---------------------------------------------------------------------------

def test_ac5_revise_with_ungrounded_emits_compliant_event(tmp_path, monkeypatch):
    """AC5: review text REVISE + 4 ungrounded → citation_grounding_self_enforcement
    with compliant=True (REVISE is the correct verdict when ungrounded>=1).

    FAILS today: no citation_grounding_* events emitted.
    """
    raw = (
        "## Verdict\nREVISE\n\n"
        "## Findings (structured)\n[]\n\n"
        "## Concerns Checked\n"
        "Citation grounding count: 1/5 grounded (4 ungrounded)\n"
    )
    result, spy = _run_write_review_doc(tmp_path, monkeypatch, raw)

    assert result.status == "ok"
    events = _grounding_events(spy)
    assert len(events) == 1, (
        f"expected exactly 1 'citation_grounding_self_enforcement' event, got {len(events)}"
    )
    _, payload = events[0]
    assert payload["compliant"] is True, (
        "compliant must be True when verdict==REVISE (reviewer self-enforced correctly)"
    )
    assert payload["ungrounded"] == 4, f"ungrounded should be 4, got {payload['ungrounded']}"
    assert payload["verdict"] == "REVISE", f"verdict should be 'REVISE', got {payload['verdict']!r}"


# ---------------------------------------------------------------------------
# AC6 — SHIP + 0 ungrounded → self_enforcement event with compliant=True
# ---------------------------------------------------------------------------

def test_ac6_ship_with_zero_ungrounded_emits_compliant_event(tmp_path, monkeypatch):
    """AC6: review text SHIP + 0 ungrounded → citation_grounding_self_enforcement
    with compliant=True (all citations grounded, SHIP is fine).

    FAILS today: no citation_grounding_* events emitted.
    """
    raw = (
        "## Verdict\nSHIP\n\n"
        "## Findings (structured)\n[]\n\n"
        "## Concerns Checked\n"
        "Citation grounding count: 5/5 grounded (0 ungrounded)\n"
    )
    result, spy = _run_write_review_doc(tmp_path, monkeypatch, raw)

    assert result.status == "ok"
    events = _grounding_events(spy)
    assert len(events) == 1, (
        f"expected exactly 1 'citation_grounding_self_enforcement' event, got {len(events)}"
    )
    _, payload = events[0]
    assert payload["compliant"] is True, (
        "compliant must be True when ungrounded==0"
    )
    assert payload["ungrounded"] == 0, f"ungrounded should be 0, got {payload['ungrounded']}"


# ---------------------------------------------------------------------------
# AC7 — no count line → citation_grounding_count_missing event, no self_enforcement
# ---------------------------------------------------------------------------

def test_ac7_no_count_line_emits_missing_event(tmp_path, monkeypatch):
    """AC7: review text with NO 'Citation grounding count:' line → no
    citation_grounding_self_enforcement event; exactly one
    citation_grounding_count_missing event with verdict='SHIP'.

    FAILS today: neither event is emitted.
    """
    raw = (
        "## Verdict\nSHIP\n\n"
        "## Findings (structured)\n[]\n\n"
        "## Concerns Checked\n"
        "- ok\n"
    )
    result, spy = _run_write_review_doc(tmp_path, monkeypatch, raw)

    assert result.status == "ok", (
        f"_write_review_doc must not raise/error when count line is absent; got {result.status!r}"
    )

    enforcement_events = _grounding_events(spy)
    assert len(enforcement_events) == 0, (
        f"must NOT emit citation_grounding_self_enforcement when count line absent; got {enforcement_events}"
    )

    missing_events = _missing_events(spy)
    assert len(missing_events) == 1, (
        f"expected exactly 1 'citation_grounding_count_missing' event, got {len(missing_events)}: {spy}"
    )
    _, payload = missing_events[0]
    assert payload.get("verdict") == "SHIP", (
        f"citation_grounding_count_missing payload must carry verdict='SHIP', got {payload!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — telemetry hook is observe-only; verdict in returned doc stays SHIP
# ---------------------------------------------------------------------------

def test_ac8_verdict_unchanged_by_telemetry_hook(tmp_path, monkeypatch):
    """AC8: the telemetry hook must NOT change the verdict. AC4 setup (SHIP + 3
    ungrounded) → _write_review_doc's written file still starts with '## Verdict\nSHIP',
    and result.data['verdict'] is still 'SHIP'.

    FAILS today: no citation_grounding_* events, but that also means the assertion
    that citation_grounding_self_enforcement event count==1 from AC4 is a prerequisite;
    we re-run independently here to confirm the verdict is untouched after GREEN ships.
    """
    raw = (
        "## Verdict\nSHIP\n\n"
        "## Findings (structured)\n[]\n\n"
        "## Concerns Checked\n"
        "Citation grounding count: 2/5 grounded (3 ungrounded)\n"
        "- something\n"
    )
    result, spy = _run_write_review_doc(tmp_path, monkeypatch, raw)

    assert result.status == "ok"

    # The returned StepResult must still carry verdict='SHIP'.
    assert isinstance(result.data, dict), "result.data must be a dict"
    assert result.data.get("verdict") == "SHIP", (
        f"result.data['verdict'] must remain 'SHIP' (observe-only hook); "
        f"got {result.data.get('verdict')!r}"
    )

    # The written file on disk must still start with the SHIP verdict header.
    review_file = Path(result.data["review_path"])
    assert review_file.exists(), "review file must have been written"
    written = review_file.read_text(encoding="utf-8")
    assert written.startswith("## Verdict\nSHIP"), (
        f"on-disk review doc must still start with '## Verdict\\nSHIP'; "
        f"first 60 chars: {written[:60]!r}"
    )

    # Sanity: the telemetry event IS fired (proves GREEN wired the hook);
    # this assertion fails today (AC4 already covers the event payload in detail).
    events = _grounding_events(spy)
    assert len(events) == 1, (
        f"expected 1 citation_grounding_self_enforcement event (telemetry fired "
        f"but verdict must be unchanged); got {len(events)}"
    )


if __name__ == "__main__":
    import pytest as _pytest
    import sys as _sys
    _sys.exit(_pytest.main([__file__, "-v"]))
