"""RED tests for FF2CB91D — phase_6 reviewer test-only note injection.

Spec: SHARED/memory/Decisions/2026-06-16_FF2CB91D_phase6_test_only_note_spec.md

AC mapping:
    test_ac1_read_engine_mode_returns_test_only_on_line1   → AC1
    test_ac2_read_engine_mode_returns_test_only_on_line2   → AC2
    test_ac3_read_engine_mode_returns_none_no_marker       → AC3
    test_ac4_read_engine_mode_returns_none_marker_on_line3 → AC4
    test_ac5_read_engine_mode_returns_none_missing_file    → AC5
    test_ac6_prompt_contains_test_only_build_header        → AC6
    test_ac7_prompt_contains_no_separate_production_diff   → AC7
    test_ac8_prompt_no_header_when_no_marker               → AC8
    test_ac9_prompt_no_header_when_other_mode              → AC9
    test_ac10_emit_safe_called_once_on_test_only_not_on_normal → AC10

ALL tests AC1–AC10 MUST FAIL against current production code because:
- _read_engine_mode does NOT exist in phase_6_review (AC1–AC5 raise AttributeError).
- _build_review_prompt does NOT inject ## TEST-ONLY BUILD (AC6, AC7, AC8, AC9 fail
  at assertion time — the prompt exists but the header is absent).
- _emit_safe is never called with phase_6_test_only_note_injected (AC10 fails).

Non-collectable-RED guard (D1CF5FDF): _read_engine_mode is imported INSIDE each
test function body via p6._read_engine_mode so the file collects even before GREEN.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# conftest.py (conftest-import-time singleton, §1q / 81F97F3D) already adds
# engine_py root + engine_py/workflows to sys.path before this module loads.
# No sys.path manipulation here.

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_6_review as p6  # noqa: E402  — module exists today; _read_engine_mode added by GREEN


# ─── shared helpers ────────────────────────────────────────────────────────────

_TEST_ONLY_MARKER = "<!-- engine-mode: test_only -->"
_OTHER_MODE_MARKER = "<!-- engine-mode: some_other_mode -->"


def _make_ctx(scratchpad: Path, *, question: str = "Add test-only feature") -> WorkflowContext:
    """Build a minimal WorkflowContext pointing at the given scratchpad dir."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-ff2cb91d",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_spec(scratchpad: Path, content: str) -> Path:
    """Write spec to <scratchpad>/specs/build-spec.md (SPEC_DOC_RELPATH)."""
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "build-spec.md"
    spec_path.write_text(content, encoding="utf-8")
    return spec_path


def _call_build_review_prompt(scratchpad: Path) -> StepResult:
    """Call _build_review_prompt with mocked git helpers to isolate spec-content logic."""
    ctx = _make_ctx(scratchpad)
    # Patch git helpers so the function reaches the spec-read logic without
    # hitting real git or file-system git objects.
    with patch.object(p6, "git_diff_files", return_value=[]), \
         patch.object(p6, "_resolve_worktree_root", return_value=scratchpad):
        result = p6._build_review_prompt(ctx, None)
    return result


# ─── AC1: _read_engine_mode returns "test_only" when marker is on line 1 ──────


def test_ac1_read_engine_mode_returns_test_only_on_line1(tmp_path):
    """AC1: _read_engine_mode("spec.md") returns "test_only" when
    <!-- engine-mode: test_only --> appears on line 1 of the spec file.

    Fails today: p6._read_engine_mode does not exist → AttributeError at call time.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(_TEST_ONLY_MARKER + "\n# Spec body\n", encoding="utf-8")

    result = p6._read_engine_mode(str(spec))  # AttributeError today

    assert result == "test_only", (
        f"Expected 'test_only' for marker on line 1, got {result!r}"
    )


# ─── AC2: _read_engine_mode returns "test_only" when marker is on line 2 ──────


def test_ac2_read_engine_mode_returns_test_only_on_line2(tmp_path):
    """AC2: _read_engine_mode("spec.md") returns "test_only" when
    <!-- engine-mode: test_only --> appears on line 2.

    Fails today: p6._read_engine_mode does not exist → AttributeError at call time.
    """
    spec = tmp_path / "spec.md"
    spec.write_text("# Title\n" + _TEST_ONLY_MARKER + "\n# Body\n", encoding="utf-8")

    result = p6._read_engine_mode(str(spec))  # AttributeError today

    assert result == "test_only", (
        f"Expected 'test_only' for marker on line 2, got {result!r}"
    )


# ─── AC3: _read_engine_mode returns None when no marker present ───────────────


def test_ac3_read_engine_mode_returns_none_no_marker(tmp_path):
    """AC3: _read_engine_mode("spec.md") returns None when no engine-mode marker
    appears anywhere in the file.

    Fails today: p6._read_engine_mode does not exist → AttributeError at call time.
    """
    spec = tmp_path / "spec.md"
    spec.write_text("# Normal spec\nNo marker here.\n", encoding="utf-8")

    result = p6._read_engine_mode(str(spec))  # AttributeError today

    assert result is None, (
        f"Expected None when no marker present, got {result!r}"
    )


# ─── AC4: _read_engine_mode returns None when marker is on line 3+ ────────────


def test_ac4_read_engine_mode_returns_none_marker_on_line3(tmp_path):
    """AC4: _read_engine_mode("spec.md") returns None when the engine-mode marker
    appears on line 3 or later (only lines 1–2 are checked per §2.1).

    Fails today: p6._read_engine_mode does not exist → AttributeError at call time.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Title\n"
        "## Section\n"
        + _TEST_ONLY_MARKER + "\n"
        "rest of body\n",
        encoding="utf-8",
    )

    result = p6._read_engine_mode(str(spec))  # AttributeError today

    assert result is None, (
        f"Expected None when marker is on line 3, got {result!r}"
    )


# ─── AC5: _read_engine_mode returns None for a non-existent path ─────────────


def test_ac5_read_engine_mode_returns_none_missing_file(tmp_path):
    """AC5: _read_engine_mode on a non-existent / unreadable path returns None
    without raising (spec §2.3 — read-only detection, absent → None).

    Fails today: p6._read_engine_mode does not exist → AttributeError at call time.
    """
    absent = tmp_path / "does_not_exist.md"
    assert not absent.exists(), "Precondition: file must not exist"

    result = p6._read_engine_mode(str(absent))  # AttributeError today

    assert result is None, (
        f"Expected None for missing file path, got {result!r}"
    )


# ─── AC6: prompt contains ## TEST-ONLY BUILD when spec carries marker ─────────


def test_ac6_prompt_contains_test_only_build_header(tmp_path):
    """AC6: when the on-disk spec at scratchpad/specs/build-spec.md carries
    <!-- engine-mode: test_only -->, _build_review_prompt returns a prompt
    containing the literal substring '## TEST-ONLY BUILD'.

    Fails today: _build_review_prompt never injects this header.
    """
    scratchpad = tmp_path / "scratch"
    _write_spec(scratchpad, _TEST_ONLY_MARKER + "\n# Spec content\n")

    result = _call_build_review_prompt(scratchpad)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}; error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]
    assert "## TEST-ONLY BUILD" in prompt, (
        "Expected '## TEST-ONLY BUILD' in the prompt when spec carries engine-mode: test_only. "
        "Phase_6 must inject the test-only note per FF2CB91D §2.2."
    )


# ─── AC7: prompt contains "no separate production diff" when spec has marker ──


def test_ac7_prompt_contains_no_separate_production_diff(tmp_path):
    """AC7: when the on-disk spec carries <!-- engine-mode: test_only -->, the
    prompt contains the literal substring 'no separate production diff'.

    Fails today: _build_review_prompt never injects this phrase.
    """
    scratchpad = tmp_path / "scratch"
    _write_spec(scratchpad, _TEST_ONLY_MARKER + "\n# Spec content\n")

    result = _call_build_review_prompt(scratchpad)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}"
    )
    prompt = result.data["prompt"]
    assert "no separate production diff" in prompt, (
        "Expected 'no separate production diff' in the prompt for test_only build. "
        "Reviewers must be told there is no production diff by design."
    )


# ─── AC8: prompt does NOT contain ## TEST-ONLY BUILD when no marker ───────────


def test_ac8_prompt_no_header_when_no_marker(tmp_path):
    """AC8: when the spec carries NO engine-mode marker, the prompt does NOT
    contain '## TEST-ONLY BUILD'.

    Fails today: while the header is currently absent (which would make this
    pass), AC8 becomes meaningful only after GREEN — it guards against
    over-injection. Included per spec §3 to protect post-GREEN invariant.

    NOTE: this test may PASS today (header absent = assertion holds). But AC6
    FAILS (header not injected when it should be), so the test file as a whole
    produces the required RED failures.
    """
    scratchpad = tmp_path / "scratch"
    _write_spec(scratchpad, "# Normal spec\nNo engine-mode marker.\n")

    result = _call_build_review_prompt(scratchpad)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}"
    )
    prompt = result.data["prompt"]
    assert "## TEST-ONLY BUILD" not in prompt, (
        "'## TEST-ONLY BUILD' must NOT appear in prompt when spec has no engine-mode marker."
    )


# ─── AC9: prompt does NOT contain ## TEST-ONLY BUILD for other engine modes ───


def test_ac9_prompt_no_header_when_other_mode(tmp_path):
    """AC9: when the spec carries <!-- engine-mode: some_other_mode -->, the
    prompt does NOT contain '## TEST-ONLY BUILD' (only test_only triggers injection).

    NOTE: like AC8, this may PASS today while AC6/AC7/AC10 provide the RED forcing.
    Included as a post-GREEN invariant guard per spec §3.
    """
    scratchpad = tmp_path / "scratch"
    _write_spec(scratchpad, _OTHER_MODE_MARKER + "\n# Spec content\n")

    result = _call_build_review_prompt(scratchpad)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}"
    )
    prompt = result.data["prompt"]
    assert "## TEST-ONLY BUILD" not in prompt, (
        "'## TEST-ONLY BUILD' must NOT appear in prompt for engine-mode: some_other_mode."
    )


# ─── AC10: _emit_safe called once on test_only, never on normal build ─────────


def test_ac10_emit_safe_called_once_on_test_only_not_on_normal(tmp_path, monkeypatch):
    """AC10: _emit_safe is called with event type 'phase_6_test_only_note_injected'
    exactly once on a test_only build, and that event is NOT emitted on a normal
    (no-marker) build.

    Fails today: _build_review_prompt never calls _emit_safe with this event type.
    """
    emitted_calls: list[tuple[str, dict]] = []

    def _capture_emit(event_type: str, payload: dict) -> None:
        emitted_calls.append((event_type, payload))

    target_event = "phase_6_test_only_note_injected"

    # --- test_only build: expect exactly one emission ---
    scratchpad_a = tmp_path / "scratch_a"
    _write_spec(scratchpad_a, _TEST_ONLY_MARKER + "\n# Spec\n")

    emitted_calls.clear()
    monkeypatch.setattr(p6, "_emit_safe", _capture_emit)
    with patch.object(p6, "git_diff_files", return_value=[]), \
         patch.object(p6, "_resolve_worktree_root", return_value=scratchpad_a):
        result_a = p6._build_review_prompt(_make_ctx(scratchpad_a), None)

    assert result_a.status == "ok", (
        f"test_only build: expected status='ok', got {result_a.status!r}"
    )
    test_only_event_calls = [e for e in emitted_calls if e[0] == target_event]
    assert len(test_only_event_calls) == 1, (
        f"Expected exactly 1 '{target_event}' event on a test_only build, "
        f"got {len(test_only_event_calls)}. All emitted events: "
        f"{[e[0] for e in emitted_calls]}"
    )

    # --- normal build: event must NOT be emitted ---
    scratchpad_b = tmp_path / "scratch_b"
    _write_spec(scratchpad_b, "# Normal spec\nNo marker.\n")

    emitted_calls.clear()
    # monkeypatch still active for p6._emit_safe
    with patch.object(p6, "git_diff_files", return_value=[]), \
         patch.object(p6, "_resolve_worktree_root", return_value=scratchpad_b):
        result_b = p6._build_review_prompt(_make_ctx(scratchpad_b), None)

    assert result_b.status == "ok", (
        f"normal build: expected status='ok', got {result_b.status!r}"
    )
    normal_event_calls = [e for e in emitted_calls if e[0] == target_event]
    assert len(normal_event_calls) == 0, (
        f"'{target_event}' must NOT be emitted on a normal (no-marker) build, "
        f"but got {len(normal_event_calls)} emission(s)."
    )
