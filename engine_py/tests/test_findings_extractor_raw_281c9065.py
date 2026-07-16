"""RED tests for 281C9065 — schema-agnostic extract_structured_findings_raw core.

AC1, AC2, AC2b, AC3a-d, AC4, AC5, AC6, AC7a, AC7b.

Expected RED failures today (before GREEN ships):
  - test_ac1_raw_importable           — ImportError: extract_structured_findings_raw absent
  - test_ac2_verbatim_passthrough     — ImportError (same)
  - test_ac2b_no_coercion_int_id      — ImportError (same)
  - test_ac3a_none_on_empty           — ImportError (same)
  - test_ac3b_none_on_malformed_json  — ImportError (same)
  - test_ac3c_none_on_non_list_root   — ImportError (same)
  - test_ac3d_non_dicts_filtered      — ImportError (same)
  - test_ac7a_phase6_regex_gone       — AssertionError (_PHASE6_STRUCTURED_SECTION_RE still exists)
  - test_ac7b_phase6_binds_raw        — AssertionError (_extract_structured_findings_raw absent)

Expected GREEN (regression pins already passing today):
  - test_ac4_spec_lib_coercion        — extract_structured_findings already exists, coerces correctly
  - test_ac5_findings_for_writer      — extract_findings_for_writer already works
  - test_ac6_phase6_projection        — phase_6_review.extract_structured_findings already works
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent             # engine_py/
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# AC1 signal: this import will raise ImportError in RED — that's correct.
from plugins.checklist_convergence import (  # noqa: E402
    extract_structured_findings_raw,
    extract_structured_findings,
    extract_findings_for_writer,
    Finding,
)
import plugins.checklist_convergence as _cc_module  # noqa: E402
import phase_6_review  # noqa: E402


# ── shared fixture builder ────────────────────────────────────────────────────

def _struct(json_body: str) -> str:
    return (
        "# Review\n\nsome prose\n\n"
        "## Findings (structured)\n"
        f"```json\n{json_body}\n```\n\n"
        "## Next\n"
    )


# ── AC1 ───────────────────────────────────────────────────────────────────────

def test_ac1_raw_importable() -> None:
    """AC1 — extract_structured_findings_raw is importable and callable."""
    assert callable(extract_structured_findings_raw)


# ── AC2 ───────────────────────────────────────────────────────────────────────

def test_ac2_verbatim_passthrough() -> None:
    """AC2 — list-valued path, extra int key, string id all pass through unmodified."""
    json_body = (
        '[{"id":"1","severity":"HIGH","path":["a.py","b.py"],'
        '"description":"x","type":"bug","extra":42}]'
    )
    result = extract_structured_findings_raw(_struct(json_body))
    assert result == [
        {
            "id": "1",
            "severity": "HIGH",
            "path": ["a.py", "b.py"],
            "description": "x",
            "type": "bug",
            "extra": 42,
        }
    ], f"verbatim passthrough failed: {result}"


def test_ac2b_no_coercion_int_id() -> None:
    """AC2b — numeric id stays int, no key coercion."""
    json_body = '[{"id": 7, "description": "y"}]'
    result = extract_structured_findings_raw(_struct(json_body))
    assert result == [{"id": 7, "description": "y"}], (
        f"expected int id preserved, got {result}"
    )


# ── AC3 ───────────────────────────────────────────────────────────────────────

def test_ac3a_none_on_empty() -> None:
    """AC3a — empty string and no structured section both return None."""
    assert extract_structured_findings_raw("") is None
    no_struct = "## Verdict\nSHIP\n\n## Findings\n- some issue\n"
    assert extract_structured_findings_raw(no_struct) is None


def test_ac3b_none_on_malformed_json() -> None:
    """AC3b — structured section present but malformed JSON → None."""
    text = "## Findings (structured)\n```json\n{not json\n```\n"
    assert extract_structured_findings_raw(text) is None


def test_ac3c_none_on_non_list_root() -> None:
    """AC3c — structured section present, valid JSON, root is object → None."""
    text = '## Findings (structured)\n```json\n{"id": "x"}\n```\n'
    assert extract_structured_findings_raw(text) is None


def test_ac3d_non_dicts_filtered() -> None:
    """AC3d — non-dict list entries dropped, dicts passed through verbatim."""
    json_body = '[1, {"id":"keep","severity":"LOW"}, "str", null, {"id":"keep2"}]'
    result = extract_structured_findings_raw(_struct(json_body))
    assert result == [
        {"id": "keep", "severity": "LOW"},
        {"id": "keep2"},
    ], f"non-dict filter failed: {result}"


# ── AC4 ───────────────────────────────────────────────────────────────────────

def test_ac4_spec_lib_coercion() -> None:
    """AC4 — spec lib extract_structured_findings coerces to 4-key Finding TypedDict.

    Regression pin: already passes today; must continue after GREEN delegates to raw core.
    """
    # Full schema entry — only 4 spec keys retained, severity excluded
    json_body = (
        '[{"id":"a","type":"bug","evidence":"e","required_action":"fix it","severity":"HIGH"}]'
    )
    result = extract_structured_findings(_struct(json_body))
    assert result == [
        {"id": "a", "type": "bug", "evidence": "e", "required_action": "fix it"}
    ], f"spec coercion dropped unexpected keys or wrong output: {result}"

    # Missing keys → empty strings; numeric id → str-coerced
    result2 = extract_structured_findings(_struct('[{"id": 3}]'))
    assert result2 == [
        {"id": "3", "type": "", "evidence": "", "required_action": ""}
    ], f"missing-key defaults or id coercion wrong: {result2}"

    # Absent block → None
    assert extract_structured_findings("## Verdict\nSHIP\n") is None


# ── AC5 ───────────────────────────────────────────────────────────────────────

def test_ac5_findings_for_writer() -> None:
    """AC5 — extract_findings_for_writer: structured preferred, free-text fallback, none body.

    Regression pin.
    """
    # (a) Prefers structured block
    structured_review = (
        "## Findings\n- prose issue\n\n"
        "## Findings (structured)\n```json\n"
        '[{"id":"1","type":"fabrication","evidence":"e","required_action":"fix"}]\n'
        "```\n"
    )
    result_a = extract_findings_for_writer(structured_review)
    assert len(result_a) == 1
    assert result_a[0]["id"] == "1"

    # (b) Fallback to free-text numbered list when no structured block
    freetext_review = "## Findings\n1. First issue\n2. Second issue\n"
    result_b = extract_findings_for_writer(freetext_review)
    assert len(result_b) == 2
    assert result_b[0].get("type") == "issue"

    # (c) Returns [] when body is the literal "none"
    none_review = "## Findings\nnone\n\n## Rationale\nOK\n"
    assert extract_findings_for_writer(none_review) == []


# ── AC6 ───────────────────────────────────────────────────────────────────────

def test_ac6_phase6_projection() -> None:
    """AC6 — phase_6_review.extract_structured_findings projects onto 4 phase_6 keys.

    path stays un-coerced (list stays list); extra keys dropped; id str-coerced.
    Regression pin.
    """
    json_body = (
        '[{"id":"1","severity":"high","path":["x.py"],"description":"d",'
        '"type":"IGNORED","evidence":"IGNORED"}]'
    )
    result = phase_6_review.extract_structured_findings(_struct(json_body))
    assert result == [
        {"id": "1", "severity": "high", "path": ["x.py"], "description": "d"}
    ], f"phase_6 projection wrong: {result}"

    # Scalar path passes through as-is; id str-coerced; missing fields → ""
    result2 = phase_6_review.extract_structured_findings(
        _struct('[{"id": 9, "path": "single.py"}]')
    )
    assert result2 == [
        {"id": "9", "severity": "", "path": "single.py", "description": ""}
    ], f"phase_6 scalar path or coercion wrong: {result2}"

    # Absent block → None
    assert phase_6_review.extract_structured_findings("## Verdict\nSHIP\n") is None


# ── AC7 ───────────────────────────────────────────────────────────────────────

def test_ac7a_phase6_regex_gone() -> None:
    """AC7a — _PHASE6_STRUCTURED_SECTION_RE must NOT exist after refactor.

    FAILS today (RED): the duplicate regex still exists in phase_6_review.
    """
    assert not hasattr(phase_6_review, "_PHASE6_STRUCTURED_SECTION_RE"), (
        "_PHASE6_STRUCTURED_SECTION_RE still defined in phase_6_review — "
        "refactor must delete it and delegate to the raw core"
    )


def test_ac7b_phase6_binds_raw() -> None:
    """AC7b — phase_6_review._extract_structured_findings_raw is the same object
    as plugins.checklist_convergence.extract_structured_findings_raw.

    FAILS today (RED): the binding doesn't exist yet.
    """
    assert hasattr(phase_6_review, "_extract_structured_findings_raw"), (
        "phase_6_review must bind the raw core as _extract_structured_findings_raw"
    )
    assert phase_6_review._extract_structured_findings_raw is extract_structured_findings_raw, (
        "_extract_structured_findings_raw is not the same object as the plugin export"
    )
