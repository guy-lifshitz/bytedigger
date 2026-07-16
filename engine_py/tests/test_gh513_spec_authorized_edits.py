"""RED tests for GH513 — wire the spec-phase producer for `authorized-test-edits:`.

Spec: SHARED/memory/Decisions/2026-07-10_GH513_tampered_fp_spec_producer_spec.md
ACs 1-6 (spec §3).

Conventions (§1q / D1CF5FDF collectability guard): all imported modules and
symbols already exist today (``phase_45_spec``, ``phase_1_discovery``,
``phase_5_implement``, ``lib.run_allowlist``). Only the OUTPUT CONTENT of
those functions is missing the new marker/section text — so this file
collects cleanly and every AC fails at assert time, never at collection.

Stub-passability (§1l/7AD3D393): UUT symbols (``_spec_output_schema``,
``_review_output_schema``, ``phase_1_discovery._build_prompt``,
``run_allowlist.parse_authorized_test_edits``) are called directly, never
patched/mocked. ``phase_5_implement._TAMPER_REMEDIATION_HINT`` is inspected
via getattr/inspect.getsource, not mocked.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import phase_45_spec  # noqa: E402
import phase_1_discovery  # noqa: E402
import phase_5_implement  # noqa: E402
import lib.run_allowlist as run_allowlist_mod  # noqa: E402
from contracts import WorkflowContext  # noqa: E402


class TestGH513SpecAuthorizedEditsProducer:
    """GH513 — spec-phase producer for authorized-test-edits marker (ACs 1-6)."""

    def test_ac1_spec_output_schema_contains_marker_heading_and_error_code(self) -> None:
        """AC1: _spec_output_schema output has the marker, the section heading,
        and the E_RED_TESTS_TAMPERED token. Expected FAIL pre-GREEN."""
        out = phase_45_spec._spec_output_schema("specs/x.md")
        assert "authorized-test-edits:" in out, (
            "spec output schema missing the machine-read marker line "
            "'authorized-test-edits:' (GH513 §2.1 not implemented)"
        )
        assert "## Authorized Test Edits" in out, (
            "spec output schema missing the '## Authorized Test Edits' section heading"
        )
        assert "E_RED_TESTS_TAMPERED" in out, (
            "spec output schema missing the E_RED_TESTS_TAMPERED token that "
            "explains the consequence of an unlisted test edit"
        )

    def test_ac2_spec_output_schema_checklist_mentions_grep_and_authorized_test_edits(self) -> None:
        """AC2: pre-submission checklist has an item mentioning both
        'authorized-test-edits' and grep-the-test-tree. Expected FAIL pre-GREEN."""
        out = phase_45_spec._spec_output_schema("specs/x.md")
        checklist_items = [
            line for line in out.splitlines() if "[ ]" in line
        ]
        matching = [
            line for line in checklist_items
            if "authorized-test-edits" in line and ("grep" in line.lower())
        ]
        assert matching, (
            "expected a '[ ]' checklist item mentioning both "
            "'authorized-test-edits' and 'grep' (the test-tree grep instruction); "
            f"checklist items found: {checklist_items!r}"
        )

    def test_ac3_review_output_schema_contains_authorized_test_edits(self) -> None:
        """AC3: reviewer schema mentions authorized-test-edits coherence.
        Expected FAIL pre-GREEN."""
        out = phase_45_spec._review_output_schema()
        assert "authorized-test-edits" in out, (
            "reviewer output schema missing 'authorized-test-edits' coherence "
            "scrutiny item (GH513 §2.3 not implemented)"
        )

    def test_ac4_build_prompt_simple_branch_contains_marker_and_heading(self, tmp_path: Path) -> None:
        """AC4: phase_1_discovery._build_prompt SIMPLE branch output contains
        the marker and section heading. Expected FAIL pre-GREEN."""
        org_config = {"scratchpad_dir": str(tmp_path)}
        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config=org_config,
            question="Fix the thing",
            session_id="gh513-red",
            persona="hal",
            framework=None,
            domain=None,
        )
        prompt = phase_1_discovery._build_prompt(ctx, tmp_path, "SIMPLE")
        assert "authorized-test-edits:" in prompt, (
            "SIMPLE-branch prompt missing 'authorized-test-edits:' marker "
            "(GH513 §2.4 not implemented)"
        )
        assert "## Authorized Test Edits" in prompt, (
            "SIMPLE-branch prompt missing '## Authorized Test Edits' section heading"
        )

    def test_ac5_parse_authorized_test_edits_round_trip_bullet_variant(self, tmp_path: Path) -> None:
        """AC5 (bullet variant): a spec snippet in the exact §2.1-instructed
        format parses to the single listed path. Expected PASS today (this
        is a regression/interop guard over the pre-existing GH436 parser)."""
        spec_path = tmp_path / "spec.md"
        spec_path.write_text(
            "## Authorized Test Edits\n"
            "authorized-test-edits:\n"
            "- `engine_py/tests/test_x.py` — AC2 changes the return contract\n"
        )
        result = run_allowlist_mod.parse_authorized_test_edits(str(spec_path))
        assert result == ["engine_py/tests/test_x.py"]

    def test_ac5_parse_authorized_test_edits_round_trip_none_variant(self, tmp_path: Path) -> None:
        """AC5 (none variant): the 'none' non-bullet form parses to [].
        Expected PASS today."""
        spec_path = tmp_path / "spec.md"
        spec_path.write_text(
            "## Authorized Test Edits\n"
            "authorized-test-edits:\n"
            "none\n"
        )
        result = run_allowlist_mod.parse_authorized_test_edits(str(spec_path))
        assert result == []

    def test_ac6_tamper_remediation_hint_constant_used_in_error_string(self) -> None:
        """AC6: phase_5_implement._TAMPER_REMEDIATION_HINT exists, mentions
        authorized-test-edits, and is used in the E_RED_TESTS_TAMPERED error
        f-string. Expected FAIL pre-GREEN."""
        hint = getattr(phase_5_implement, "_TAMPER_REMEDIATION_HINT", None)
        assert hint is not None, (
            "phase_5_implement._TAMPER_REMEDIATION_HINT does not exist yet "
            "(GH513 §2.5 not implemented)"
        )
        assert "authorized-test-edits:" in hint

        source = inspect.getsource(phase_5_implement)
        idx_tampered = source.find("tampered RED test paths")
        assert idx_tampered != -1, "could not locate the tampered-RED-test-paths error string"
        idx_hint_use = source.find("_TAMPER_REMEDIATION_HINT}", idx_tampered)
        assert idx_hint_use != -1, (
            "_TAMPER_REMEDIATION_HINT is not interpolated into the "
            "E_RED_TESTS_TAMPERED error f-string after the "
            "'tampered RED test paths' literal"
        )
