"""RED tests for E94AFD31 — phase_45_spec_lite cycle-1 reviewer cross-check directives.

5 tests, one per AC. AC1-AC4 MUST FAIL against current code (the directives are
absent from _review_output_schema()). AC5 MUST PASS (existing schema headers present).

Do NOT modify phase_45_spec_lite.py here — GREEN agent handles that.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent             # engine_py/
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows.phase_45_spec_lite import _review_output_schema  # noqa: E402


# ── AC1: ctx-json directive present ──────────────────────────────────────────

def test_ctx_json_directive_present() -> None:
    """AC1: _review_output_schema() must contain 'ctx-json' AND a directive to
    verify those keys against engine source (both substrings must appear, and
    'engine source' must co-occur in the same text so the reviewer knows *where*
    to look — not just that ctx-json exists).

    EXPECTED TO FAIL in RED: current prompt has no ctx-json key-authenticity
    directive; 'engine source' does not appear.
    """
    s = _review_output_schema()
    s_lower = s.lower()

    has_ctx_json = "ctx-json" in s_lower
    has_engine_source = "engine source" in s_lower

    assert has_ctx_json, (
        "AC1 FAIL: _review_output_schema() does not contain 'ctx-json' — "
        "the ctx-json key-authenticity directive is missing."
    )
    assert has_engine_source, (
        "AC1 FAIL: _review_output_schema() does not contain 'engine source' — "
        "the reviewer is not instructed to cross-check ctx-json keys against "
        "engine source."
    )


# ── AC2: override-key directive present ──────────────────────────────────────

def test_override_key_directive_present() -> None:
    """AC2: _review_output_schema() must reference _resolve_command OR reference
    both 'override keys' and 'target workflow' together, instructing the reviewer
    to verify override keys against the target workflow source.

    EXPECTED TO FAIL in RED: current prompt contains neither '_resolve_command'
    nor the combination 'override keys' + 'target workflow'.
    """
    s = _review_output_schema()
    s_lower = s.lower()

    has_resolve_command = "_resolve_command" in s
    has_override_and_workflow = (
        "override key" in s_lower and "target workflow" in s_lower
    )

    assert has_resolve_command or has_override_and_workflow, (
        "AC2 FAIL: _review_output_schema() contains neither '_resolve_command' "
        "nor 'override key' + 'target workflow' — the reviewer is not instructed "
        "to verify override keys against the target workflow dispatch table."
    )


# ── AC3: org_config wrapper directive present ─────────────────────────────────

def test_org_config_directive_present() -> None:
    """AC3: _review_output_schema() must contain 'org_config' AND a directive
    about verifying wrapper presence/absence matching the engine expectation.

    EXPECTED TO FAIL in RED: current prompt does not mention org_config at all.
    """
    s = _review_output_schema()

    has_org_config = "org_config" in s

    assert has_org_config, (
        "AC3 FAIL: _review_output_schema() does not contain 'org_config' — "
        "the reviewer is not instructed to verify that invocation examples "
        "include/exclude the org_config wrapper as the engine requires."
    )


# ── AC4: self-contradiction directive present ─────────────────────────────────

def test_self_contradiction_directive_present() -> None:
    """AC4: _review_output_schema() must contain a directive (case-insensitive
    'contradict') that specifically ties contradictions to invocation examples
    — i.e. 'contradict' must co-occur with 'invocation' OR 'example' in the
    same schema text, so the reviewer is told to detect contradictions *within*
    invocation examples, not merely generic contradictions.

    EXPECTED TO FAIL in RED: current schema mentions 'contradictions' on line
    ~267 but not in the context of invocation/example — the co-occurrence
    requirement ('invocation' or 'example') will not be satisfied.
    """
    s = _review_output_schema()
    s_lower = s.lower()

    has_contradict = "contradict" in s_lower
    has_invocation_or_example = "invocation" in s_lower or "example" in s_lower

    assert has_contradict and has_invocation_or_example, (
        "AC4 FAIL: _review_output_schema() must contain both 'contradict' "
        "(case-insensitive) AND either 'invocation' or 'example' — the "
        "reviewer must be instructed to detect self-contradictions specifically "
        "in the context of invocation examples, not generic contradictions. "
        f"has_contradict={has_contradict}, "
        f"has_invocation_or_example={has_invocation_or_example}."
    )


# ── AC5: existing schema section headers preserved ───────────────────────────

def test_existing_schema_preserved() -> None:
    """AC5: _review_output_schema() must still return all four required section
    headers so downstream parsers don't break after GREEN adds the new directives.

    EXPECTED TO PASS on current code (these headers already exist).
    """
    s = _review_output_schema()

    required_headers = (
        "## Verdict",
        "## Findings (structured)",
        "## Concerns Checked",
        "## Rationale",
    )

    for header in required_headers:
        assert header in s, (
            f"AC5 FAIL: _review_output_schema() is missing required section "
            f"header {header!r} — downstream parsers depend on this header."
        )
