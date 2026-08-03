"""RED tests for lib/plugins/checklist_convergence/ — C094A1E1 W1.

All tests MUST FAIL (ImportError) until GREEN agent creates the package.
Do NOT create the package here — this is RED-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent             # engine_py/
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.lib.plugins.checklist_convergence import (  # noqa: E402  — will ImportError in RED
    Finding,
    ReviewerVerdict,
    extract_findings_section,
    extract_structured_findings,
    extract_findings_for_writer,
    build_writer_prompt,
    build_reviewer_prompt,
    parse_per_finding_verdicts,
)

# ── Real fixture: forge-1778257920 cycle-1 review (4 numbered findings) ──────

_FIXTURE_REVIEW = """\
## Verdict
REVISE

## Concerns Checked
- Fabrication check: spec covers all authorized features.
- Length: 67 lines, within 30–80 SIMPLE target.

## Findings
1. **Open Questions falsely empty.** Spec invents details not in FEATURE REQUEST that \
are genuinely ambiguous — exit codes, coordinator default path, stuck event schema.

2. **AC5 testability gap.** Real invocation requires worker subprocesses; no harness \
specified. Either pin a stub strategy or scope down.

3. **AC4 dry-run path realism.** Verify that --resume codepath emits resume_cascade \
in --dry-run mode; --dry-run may short-circuit before cascade.

4. **AC3 stderr regex tightness.** Regex checks prefix only — does not validate counts \
or alphabetical ordering. Tighten or accept intentionally.

## Rationale
Spec is materially improved but three real Open Questions exist.
"""

_FIXTURE_STRUCTURED_REVIEW = """\
## Verdict
REVISE

## Findings
- some free-text issue

## Findings (structured)
```json
[
  {"id": "1", "type": "fabrication", "evidence": "exit codes invented", "required_action": "move to Open Questions"},
  {"id": "2", "type": "untestable",  "evidence": "no harness spec",     "required_action": "pin stub strategy"}
]
```

## Rationale
Two findings must be addressed.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# findings_extractor
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingsSection:
    """T1 — extract_findings_section."""

    def test_returns_body_for_valid_section(self) -> None:
        body = extract_findings_section(_FIXTURE_REVIEW)
        assert body, "expected non-empty body for review with ## Findings"
        assert "Open Questions" in body

    def test_empty_string_on_absent_section(self) -> None:
        text = "## Verdict\nSHIP\n\n## Rationale\nLooks good.\n"
        assert extract_findings_section(text) == ""

    def test_empty_string_on_none_body(self) -> None:
        text = "## Findings\nnone\n\n## Rationale\nOK\n"
        result = extract_findings_section(text)
        # Should return '' when body is 'none'
        assert result == "" or result.strip().lower() in ("none", "")


class TestStructuredFindings:
    """T2 & T3 — extract_structured_findings."""

    def test_parses_json_fenced_block(self) -> None:
        result = extract_structured_findings(_FIXTURE_STRUCTURED_REVIEW)
        assert result is not None, "expected list, got None"
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[0]["type"] == "fabrication"
        assert result[1]["id"] == "2"

    def test_returns_none_on_absent_block(self) -> None:
        result = extract_structured_findings(_FIXTURE_REVIEW)
        assert result is None

    def test_returns_none_on_malformed_json(self) -> None:
        text = "## Findings (structured)\n```json\n{not valid json\n```\n"
        assert extract_structured_findings(text) is None

    def test_returns_none_on_non_list_root(self) -> None:
        text = '## Findings (structured)\n```json\n{"id": "1"}\n```\n'
        assert extract_structured_findings(text) is None


class TestFindingsForWriter:
    """T4, T5, T6 — extract_findings_for_writer."""

    def test_prefers_structured_over_free_text(self) -> None:
        result = extract_findings_for_writer(_FIXTURE_STRUCTURED_REVIEW)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "1"

    def test_fallback_to_numbered_list(self) -> None:
        """Fixture has 4 numbered findings — fallback must return 4 items."""
        result = extract_findings_for_writer(_FIXTURE_REVIEW)
        assert isinstance(result, list), f"expected list, got {type(result)}"
        assert len(result) == 4, (
            f"expected 4 findings from numbered list, got {len(result)}: {result}"
        )

    def test_empty_list_on_missing_section(self) -> None:
        text = "## Verdict\nSHIP\n\n## Rationale\nAll good.\n"
        result = extract_findings_for_writer(text)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# restricted_writer_prompt
# ═══════════════════════════════════════════════════════════════════════════════

_SAMPLE_FINDINGS: list[Finding] = [
    {
        "id": "1",
        "type": "fabrication",
        "evidence": "exit codes invented",
        "required_action": "move to Open Questions",
    },
    {
        "id": "2",
        "type": "untestable",
        "evidence": "no worker harness",
        "required_action": "pin stub strategy",
    },
]

_SAMPLE_SPEC = "## Context\nBuild a batch-resume feature.\n\n## Acceptance Criteria\n1. AC1\n"


class TestRestrictedWriterPrompt:
    """T7 — build_writer_prompt."""

    def test_prompt_contains_required_substrings(self) -> None:
        prompt = build_writer_prompt(spec=_SAMPLE_SPEC, findings=_SAMPLE_FINDINGS)

        assert "ONLY modify lines" in prompt, "missing ONLY-modify constraint"
        assert "Do NOT add new ACs" in prompt, "missing no-new-ACs constraint"
        assert "CYCLE-1 REVIEWER FINDINGS:" in prompt, "missing findings header"
        assert "FINDING_1" in prompt, "missing FINDING_1 bullet"
        assert "FINDING_2" in prompt, "missing FINDING_2 bullet"
        assert "ORIGINAL SPEC:" in prompt, "missing original spec header"
        assert _SAMPLE_SPEC in prompt, "spec text not verbatim in prompt"
        assert "Output ONLY the full revised spec markdown" in prompt, (
            "missing output instruction"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# restricted_reviewer_prompt
# ═══════════════════════════════════════════════════════════════════════════════

_SAMPLE_NEW_SPEC = "## Context\nBuild a batch-resume feature.\n\n## Open Questions\n1. Exit codes?\n"


class TestRestrictedReviewerPrompt:
    """T8 — build_reviewer_prompt."""

    def test_prompt_contains_required_substrings(self) -> None:
        prompt = build_reviewer_prompt(
            findings=_SAMPLE_FINDINGS,
            new_spec=_SAMPLE_NEW_SPEC,
        )

        assert "CYCLE-1 FINDINGS" in prompt, "missing CYCLE-1 FINDINGS header"
        assert "may NOT introduce new findings" in prompt, (
            "missing no-new-findings constraint"
        )
        assert "FINDING_<id>: <RESOLVED|UNRESOLVED>" in prompt or (
            "FINDING_1:" in prompt and ("RESOLVED" in prompt or "UNRESOLVED" in prompt)
        ), "missing per-finding line-format instruction"
        assert "VERDICT: <PASS|REVISE>" in prompt or "VERDICT:" in prompt, (
            "missing VERDICT line format instruction"
        )
        assert "VERDICT=PASS iff ALL findings RESOLVED" in prompt, (
            "missing VERDICT=PASS rule"
        )
        assert "CYCLE-2 SPEC:" in prompt, "missing CYCLE-2 SPEC header"
        assert _SAMPLE_NEW_SPEC in prompt, "new_spec not verbatim in prompt"


# ═══════════════════════════════════════════════════════════════════════════════
# verdict_parser
# ═══════════════════════════════════════════════════════════════════════════════


class TestVerdictParser:
    """T9-T13 — parse_per_finding_verdicts."""

    def test_all_resolved_verdict_pass(self) -> None:
        """T9: 3/3 RESOLVED + VERDICT: PASS → PASS."""
        raw = (
            "FINDING_1: RESOLVED - addressed exit codes\n"
            "FINDING_2: RESOLVED - pinned stub strategy\n"
            "FINDING_3: RESOLVED - regex tightened\n"
            "VERDICT: PASS\n"
        )
        result = parse_per_finding_verdicts(raw)
        assert result["final_verdict"] == "PASS"
        assert result["n_resolved"] == 3
        assert result["n_total"] == 3

    def test_partial_resolved_verdict_revise(self) -> None:
        """T10: 2/3 RESOLVED + VERDICT: REVISE → REVISE."""
        raw = (
            "FINDING_1: RESOLVED - addressed\n"
            "FINDING_2: RESOLVED - pinned\n"
            "FINDING_3: UNRESOLVED - still missing harness\n"
            "VERDICT: REVISE\n"
        )
        result = parse_per_finding_verdicts(raw)
        assert result["final_verdict"] == "REVISE"
        assert result["n_resolved"] == 2
        assert result["n_total"] == 3

    def test_sanity_override_pass_with_unresolved(self) -> None:
        """T11: VERDICT: PASS but only 2/3 RESOLVED → coerce to REVISE."""
        raw = (
            "FINDING_1: RESOLVED - addressed\n"
            "FINDING_2: RESOLVED - pinned\n"
            "FINDING_3: UNRESOLVED - still open\n"
            "VERDICT: PASS\n"
        )
        result = parse_per_finding_verdicts(raw)
        assert result["final_verdict"] == "REVISE", (
            "sanity override must coerce fabricated PASS to REVISE when unresolved findings exist"
        )

    def test_empty_input_unparsed(self) -> None:
        """T12: empty input → UNPARSED."""
        result = parse_per_finding_verdicts("")
        assert result["final_verdict"] == "UNPARSED"

    def test_no_verdict_line_all_resolved_infer_pass(self) -> None:
        """T13: no VERDICT line + 3/3 RESOLVED → inferred PASS."""
        raw = (
            "FINDING_1: RESOLVED - addressed\n"
            "FINDING_2: RESOLVED - pinned\n"
            "FINDING_3: RESOLVED - regex tightened\n"
        )
        result = parse_per_finding_verdicts(raw)
        assert result["final_verdict"] == "PASS", (
            "all resolved with no VERDICT line should infer PASS"
        )
