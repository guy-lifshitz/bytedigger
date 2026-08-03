"""Tests for α₀ prompt reorder — BA456198.

Verifies:
  1. `## Findings (structured)` moves to position 2 in both LITE and FEATURE schemas.
  2. Schemas contain concrete inline JSON examples (no placeholder-only evidence).
  3. Schemas include explicit omission warnings with consequence references.
  4. `findings_block_compliance` telemetry event is emitted on cycle-1 review writes.

ALL 9 tests FAIL on current production code (schema reordering and new telemetry
event not yet implemented).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec_lite import _review_output_schema as lite_schema, _write_review_doc as lite_write  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import _review_output_schema as feature_schema  # noqa: E402


# ─── helpers ─────────────────────────────────────────────────────────────────

def _section_index(schema: str, header: str) -> int:
    """Return char index of first occurrence of header in schema, or -1."""
    return schema.find(header)


def _count_freetext_findings(text: str) -> int:
    """Count numbered items under '## Findings' (free-text, not structured)."""
    block = re.search(r"## Findings\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if not block:
        return 0
    return len(re.findall(r"^\d+\.", block.group(1), re.MULTILINE))


def _make_review_prev(tmp_path: Path, raw: str, cycle: int = 1) -> StepResult:
    """Construct the prev StepResult that _write_review_doc expects."""
    spec = tmp_path / "specs" / "build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("spec content")
    doc = tmp_path / "specs" / "build-plan-review.md"
    return StepResult(
        status="ok",
        data={"raw_response": raw, "doc_path": str(doc), "spec_path": str(spec), "cycle": cycle},
        duration_ms=0,
        step_name="invoke_reviewer",
    )


# ─── Schema-structure tests ───────────────────────────────────────────────────

def test_lite_schema_structured_findings_position_2():
    """FAILS today: ## Findings (structured) is at position 5, not position 2."""
    schema = lite_schema()
    idx_structured = _section_index(schema, "## Findings (structured)")
    idx_freetext = schema.find("## Findings\n")
    idx_concerns = _section_index(schema, "## Concerns Checked")
    idx_rationale = _section_index(schema, "## Rationale")
    assert idx_structured != -1, "## Findings (structured) section missing"
    assert idx_structured < idx_freetext, (
        f"## Findings (structured) at {idx_structured} must precede ## Findings at {idx_freetext}"
    )
    assert idx_structured < idx_concerns, (
        f"## Findings (structured) at {idx_structured} must precede ## Concerns Checked at {idx_concerns}"
    )
    assert idx_structured < idx_rationale, (
        f"## Findings (structured) at {idx_structured} must precede ## Rationale at {idx_rationale}"
    )


def test_feature_schema_structured_findings_position_2():
    """FAILS today: ## Findings (structured) is at position 5, not position 2."""
    schema = feature_schema()
    idx_structured = _section_index(schema, "## Findings (structured)")
    idx_freetext = schema.find("## Findings\n")
    idx_concerns = _section_index(schema, "## Concerns Checked")
    idx_rationale = _section_index(schema, "## Rationale")
    assert idx_structured != -1, "## Findings (structured) section missing"
    assert idx_structured < idx_freetext, (
        f"## Findings (structured) at {idx_structured} must precede ## Findings at {idx_freetext}"
    )
    assert idx_structured < idx_concerns, (
        f"## Findings (structured) at {idx_structured} must precede ## Concerns Checked at {idx_concerns}"
    )
    assert idx_structured < idx_rationale, (
        f"## Findings (structured) at {idx_structured} must precede ## Rationale at {idx_rationale}"
    )


def test_lite_schema_has_inline_json_example_with_concrete_content():
    """FAILS today: evidence field is a placeholder '<short quote or pointer>'."""
    schema = lite_schema()
    assert '"id": "1"' in schema, 'Schema must contain concrete id example: "id": "1"'
    placeholder = "<short quote or pointer>"
    assert placeholder not in schema, (
        f"Schema still contains placeholder evidence value: {placeholder!r}. "
        "Replace with a concrete example like 'spec line 17 says ...'."
    )
    assert '"evidence":' in schema, 'Schema must contain "evidence": key in JSON example'


def test_feature_schema_has_inline_json_example_with_concrete_content():
    """FAILS today: evidence field is a placeholder '<short quote or pointer>'."""
    schema = feature_schema()
    assert '"id": "1"' in schema, 'Schema must contain concrete id example: "id": "1"'
    placeholder = "<short quote or pointer>"
    assert placeholder not in schema, (
        f"Schema still contains placeholder evidence value: {placeholder!r}. "
        "Replace with a concrete example like 'spec line 17 says ...'."
    )
    assert '"evidence":' in schema, 'Schema must contain "evidence": key in JSON example'


# ─── Omission-warning tests ───────────────────────────────────────────────────

def test_lite_schema_explicit_omission_warning():
    """FAILS today: no omission warning exists in schema string."""
    schema = lite_schema()
    has_omit = bool(re.search(r"[Oo]mitting|[Oo]mit\b", schema))
    assert has_omit, "Schema must contain a word like 'Omitting' or 'omit'"
    has_consequence = bool(re.search(r"REVISE.cap|cycle.2|W1|restricted", schema))
    assert has_consequence, (
        "Schema omission warning must reference a consequence: "
        "REVISE-cap, cycle-2, W1, or restricted"
    )


def test_feature_schema_explicit_omission_warning():
    """FAILS today: no omission warning exists in schema string."""
    schema = feature_schema()
    has_omit = bool(re.search(r"[Oo]mitting|[Oo]mit\b", schema))
    assert has_omit, "Schema must contain a word like 'Omitting' or 'omit'"
    has_consequence = bool(re.search(r"REVISE.cap|cycle.2|W1|restricted", schema))
    assert has_consequence, (
        "Schema omission warning must reference a consequence: "
        "REVISE-cap, cycle-2, W1, or restricted"
    )


# ─── Telemetry tests ──────────────────────────────────────────────────────────

_REVIEW_WITH_JSON = """\
## Verdict
REVISE

## Findings (structured)
```json
[
  {"id": "1", "type": "missing", "evidence": "AC1 has no Validation line", "required_action": "Add validation"},
  {"id": "2", "type": "fabrication", "evidence": "spec adds --dry-run not in request", "required_action": "Remove"}
]
```

## Findings
- AC1 missing validation
- fabricated flag

## Concerns Checked
- checked scope

## Rationale
Two clear issues.
"""

_REVIEW_WITHOUT_JSON = """\
## Verdict
REVISE

## Findings
1. AC1 missing validation line
2. fabricated --dry-run flag
3. Out of Scope section empty

## Concerns Checked
- checked scope

## Rationale
Three issues.
"""


def test_lite_emits_findings_block_compliance_when_json_present(tmp_path):
    """FAILS today: findings_block_compliance event not emitted."""
    captured = []

    mock_run = MagicMock()
    mock_log = MagicMock()
    mock_log.append.side_effect = lambda et, payload, run_id: captured.append({"event_type": et, "payload": payload})
    mock_run.event_log = mock_log
    mock_run.run_id = "test-run"

    prev = _make_review_prev(tmp_path, _REVIEW_WITH_JSON, cycle=1)
    with patch("bytedigger_engine.workflows.phase_45_spec_lite.telemetry_ctx") as mock_ctx:
        mock_ctx.get_current_run.return_value = mock_run
        lite_write(None, prev)

    compliance = [e for e in captured if e["event_type"] == "findings_block_compliance"]
    assert len(compliance) == 1, f"Expected 1 findings_block_compliance, got {len(compliance)}: {captured}"
    p = compliance[0]["payload"]
    assert p.get("phase") == "phase_45_spec_lite"
    assert p.get("cycle") == 1
    assert p.get("json_block_present") is True
    assert p.get("json_findings_count") == 2


def test_lite_emits_findings_block_compliance_when_json_absent(tmp_path):
    """FAILS today: findings_block_compliance event not emitted."""
    captured = []

    mock_run = MagicMock()
    mock_log = MagicMock()
    mock_log.append.side_effect = lambda et, payload, run_id: captured.append({"event_type": et, "payload": payload})
    mock_run.event_log = mock_log
    mock_run.run_id = "test-run"

    prev = _make_review_prev(tmp_path, _REVIEW_WITHOUT_JSON, cycle=1)
    with patch("bytedigger_engine.workflows.phase_45_spec_lite.telemetry_ctx") as mock_ctx:
        mock_ctx.get_current_run.return_value = mock_run
        lite_write(None, prev)

    compliance = [e for e in captured if e["event_type"] == "findings_block_compliance"]
    assert len(compliance) == 1, f"Expected 1 findings_block_compliance, got {len(compliance)}: {captured}"
    p = compliance[0]["payload"]
    assert p.get("phase") == "phase_45_spec_lite"
    assert p.get("cycle") == 1
    assert p.get("json_block_present") is False
    assert p.get("json_findings_count") is None
    assert p.get("freetext_findings_count") == 3


def test_feature_emits_findings_block_compliance(tmp_path):
    """FAILS today: phase_45_spec._write_review_doc has no telemetry_ctx import or emit."""
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc as feature_write  # noqa: E402
    captured = []

    mock_run = MagicMock()
    mock_log = MagicMock()
    mock_log.append.side_effect = lambda et, payload, run_id: captured.append({"event_type": et, "payload": payload})
    mock_run.event_log = mock_log
    mock_run.run_id = "test-run"

    prev = _make_review_prev(tmp_path, _REVIEW_WITH_JSON, cycle=1)
    with patch("bytedigger_engine.workflows.phase_45_spec.telemetry_ctx") as mock_ctx:
        mock_ctx.get_current_run.return_value = mock_run
        feature_write(None, prev)

    compliance = [e for e in captured if e["event_type"] == "findings_block_compliance"]
    assert len(compliance) == 1, f"Expected 1 findings_block_compliance, got {len(compliance)}: {captured}"
    p = compliance[0]["payload"]
    assert p.get("phase") == "phase_45_spec"
    assert p.get("cycle") == 1
    assert p.get("json_block_present") is True
    assert p.get("json_findings_count") == 2
