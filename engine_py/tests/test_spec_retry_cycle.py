"""RED tests for CF480CAE — extract lib/spec_retry_cycle.py.

All tests MUST FAIL (ImportError from within test body) until GREEN agent:
  (a) creates engine_py/lib/spec_retry_cycle.py with the 3 pure helpers +
      shared constants, AND
  (b) updates both phase_45_spec.py and phase_45_spec_lite.py to import from it.

Do NOT create lib/spec_retry_cycle.py here — this is RED-only.

§1q compliance: no module-level `from lib.spec_retry_cycle import …`.
Imports are deferred inside each test body so the file collects cleanly and
fails at assert/call time (not at collection time). See D1CF5FDF §1q extension.
Conftest (engine_py/tests/conftest.py) puts engine_py root on sys.path —
no module-level sys.path manipulation needed here (81F97F3D gate).
"""
from __future__ import annotations

from pathlib import Path


# ── AC1: all 3 functions + constants importable from lib.spec_retry_cycle ─────


class TestAC1Importable:
    """AC1 — module exists and the three helpers are importable."""

    def test_truncate_findings_importable(self) -> None:
        from lib.spec_retry_cycle import truncate_findings  # noqa: F401 — deferred

        # Call with trivial valid input to confirm it is callable.
        result, was_truncated = truncate_findings("hello")
        assert result == "hello"
        assert was_truncated is False

    def test_resolve_command_importable(self) -> None:
        from lib.spec_retry_cycle import resolve_command  # noqa: F401 — deferred

        result = resolve_command({}, "step_cmd", ["claude", "-p"])
        assert isinstance(result, list)

    def test_read_first_block_importable(self) -> None:
        from lib.spec_retry_cycle import read_first_block  # noqa: F401 — deferred

        output = read_first_block(Path("/tmp/scratchpad"))
        assert isinstance(output, str)
        assert "READ_FIRST" in output


# ── AC2: truncate_findings behavior — long input AND short input ───────────────


class TestAC2TruncateFindings:
    """AC2 — byte-identical behavior to old _truncate_findings.

    _FINDINGS_MAX_BYTES == 4096.
    Long path: encoded slice + sentinel suffix appended.
    Short path: text and flag returned unchanged.
    """

    def test_short_input_passthrough(self) -> None:
        from lib.spec_retry_cycle import truncate_findings

        short = "a" * 10
        text, was_truncated = truncate_findings(short)
        assert text == short
        assert was_truncated is False

    def test_exactly_at_limit_passthrough(self) -> None:
        from lib.spec_retry_cycle import truncate_findings

        # Exactly 4096 ASCII bytes — should NOT truncate.
        at_limit = "x" * 4096
        text, was_truncated = truncate_findings(at_limit)
        assert text == at_limit
        assert was_truncated is False

    def test_long_input_truncated_flag(self) -> None:
        from lib.spec_retry_cycle import truncate_findings

        long_input = "y" * 5000  # 5000 bytes (ASCII) > 4096
        text, was_truncated = truncate_findings(long_input)
        assert was_truncated is True

    def test_long_input_truncated_at_4096_bytes(self) -> None:
        from lib.spec_retry_cycle import truncate_findings

        long_input = "z" * 5000
        text, was_truncated = truncate_findings(long_input)
        # The truncated portion is encoded[:4096].decode — for ASCII that's 4096 chars.
        expected_head = "z" * 4096
        assert text.startswith(expected_head)

    def test_long_input_golden_suffix(self) -> None:
        from lib.spec_retry_cycle import truncate_findings

        long_input = "w" * 5000
        text, was_truncated = truncate_findings(long_input)
        # Exact sentinel appended by the real implementation:
        expected_suffix = "\n\n[... findings truncated at 4096B; (full review on disk) ...]"
        assert text.endswith(expected_suffix)

    def test_truncate_with_custom_max_bytes(self) -> None:
        """Extracted signature accepts optional max_bytes kwarg."""
        from lib.spec_retry_cycle import truncate_findings

        text, flag = truncate_findings("abcde", max_bytes=3)
        assert flag is True
        assert text.startswith("abc")


# ── AC7: constants resolve to their canonical values ─────────────────────────


class TestAC7Constants:
    """AC7 — _FINDINGS_MAX_BYTES and verdict constants unchanged post-extraction."""

    def test_findings_max_bytes_is_4096(self) -> None:
        from lib.spec_retry_cycle import _FINDINGS_MAX_BYTES

        assert _FINDINGS_MAX_BYTES == 4096

    def test_verdict_ship(self) -> None:
        from lib.spec_retry_cycle import VERDICT_SHIP

        assert VERDICT_SHIP == "SHIP"

    def test_verdict_revise(self) -> None:
        from lib.spec_retry_cycle import VERDICT_REVISE

        assert VERDICT_REVISE == "REVISE"

    def test_verdict_unknown(self) -> None:
        from lib.spec_retry_cycle import VERDICT_UNKNOWN

        assert VERDICT_UNKNOWN == "UNKNOWN"


# ── resolve_command resolution order + ValueError ─────────────────────────────


class TestResolveCommand:
    """AC1 extension — full contract for resolve_command."""

    def test_per_step_override_wins(self) -> None:
        from lib.spec_retry_cycle import resolve_command

        cfg = {"my_step_cmd": ["opus"], "llm_command": ["sonnet"]}
        result = resolve_command(cfg, "my_step_cmd", ["haiku"])
        assert result == ["opus"]

    def test_fallback_to_llm_command(self) -> None:
        from lib.spec_retry_cycle import resolve_command

        cfg = {"llm_command": ["sonnet"]}
        result = resolve_command(cfg, "step_cmd_missing", ["haiku"])
        assert result == ["sonnet"]

    def test_fallback_to_default(self) -> None:
        from lib.spec_retry_cycle import resolve_command

        cfg: dict = {}
        result = resolve_command(cfg, "step_cmd_missing", ["haiku"])
        assert result == ["haiku"]

    def test_no_resolution_raises_value_error(self) -> None:
        import pytest

        from lib.spec_retry_cycle import resolve_command

        with pytest.raises(ValueError):
            resolve_command({}, "step_cmd_missing")  # no default → should raise

    def test_returns_list(self) -> None:
        from lib.spec_retry_cycle import resolve_command

        result = resolve_command({"llm_command": ("claude", "-p")}, "x", None)
        assert isinstance(result, list)
        assert result == ["claude", "-p"]


# ── read_first_block — happy path + deterministic content ─────────────────────


class TestReadFirstBlock:
    """AC1 extension — read_first_block behavior matches phase_45_spec._read_first_block."""

    def test_happy_path_contains_five_filenames(self) -> None:
        from lib.spec_retry_cycle import read_first_block

        output = read_first_block(Path("/tmp/sp"))
        for fname in [
            "hal-memory.md",
            "constitution.md",
            "quality-gate.md",
            "producer-rules.md",
            "active-work.md",
        ]:
            assert fname in output, f"expected {fname!r} in read_first_block output"

    def test_happy_path_contains_injection_subpath(self) -> None:
        from lib.spec_retry_cycle import read_first_block

        scratchpad = Path("/var/scratchpad")
        output = read_first_block(scratchpad)
        # The function constructs inj = scratchpad / "injection"
        assert str(scratchpad / "injection") in output

    def test_happy_path_starts_with_read_first(self) -> None:
        from lib.spec_retry_cycle import read_first_block

        output = read_first_block(Path("/tmp/sp"))
        assert output.startswith("READ_FIRST")

    def test_missing_injection_files_message_present(self) -> None:
        from lib.spec_retry_cycle import read_first_block

        # The sentinel message about missing files must be present.
        output = read_first_block(Path("/tmp/sp"))
        assert "injection files missing" in output
