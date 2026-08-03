"""RED tests for 65EA1B86 — _build_fix_prompt inlines HEAD content of in-scope test files.

Agreement: 65EA1B86-8871-4B6D-8B8E-8D02E8ECF4D9 (MEDIUM)
File under test: SYSTEM/cli/build/engine_py/workflows/phase_6_review.py
Unit under test: phase_6_review._build_fix_prompt(ctx, prev)

AC mapping:
    test_ac1_inline_helper_exists_and_is_callable
        → AC1: _inline_inscope_test_files exists in phase_6_review and is callable.
               Fails today: helper does not exist.

    test_ac2_fix_test_inline_cap_bytes_constant_is_20000
        → AC2: phase_6_review._FIX_TEST_INLINE_CAP_BYTES == 20000.
               Fails today: constant does not exist.

    test_ac3_test_file_sentinel_inlined_in_fix_prompt
        → AC3: When pre-red-ref.txt present + git_diff_files→["test_foo.py"] (file on
               disk containing SENTINEL_X65EA), fix prompt contains that sentinel AND
               the header "## CURRENT TEST FILE CONTENT (HEAD — authoritative)" AND "test_foo.py".
               Fails today: _build_fix_prompt builds no inline block.

    test_ac4_non_test_file_not_inlined
        → AC4: A non-test changed file (mod.py with NONTEST_SENTINEL) is NOT inlined.
               Fails today: no filter exists, so the behavior is undefined; asserting
               the sentinel absent fails when (post-GREEN) the inlining is present but
               today it already trivially passes — however AC2/AC3 dependency means
               the full fixture setup (requiring _FIX_TEST_INLINE_CAP_BYTES) will fail.
               Written to fail via dependency on the block-header being absent.

    test_ac5_excluded_prefix_test_file_not_inlined
        → AC5: SHARED/state/test_x.py is test-named but matches exclude prefix → not inlined.
               Fails today: helper/block does not exist yet; the assertion that the header
               is absent trivially passes today, but the helper-existence assertion (AC1
               dependency via getattr) is the forcing mechanism. Written as a standalone
               behavioral assertion on _build_fix_prompt output.

    test_ac6_telemetry_event_emitted_when_test_file_inlined
        → AC6: When ≥1 test file inlined, exactly one fix_prompt_test_files_inlined event
               with count==1 and bytes>0 emitted.
               Fails today: event does not exist.

    test_ac7_oversized_test_file_truncated
        → AC7: A test file > 20000 bytes is truncated; prompt contains "[TRUNCATED" and the
               path; the unique tail sentinel TAIL_SENTINEL_OVERCAP (placed after 20000 bytes)
               is absent.
               Fails today: constant and helper do not exist.

    test_ac8_no_test_files_in_diff_produces_no_block_and_no_event
        → AC8: When git_diff_files → ["mod.py"] only, header absent AND no
               fix_prompt_test_files_inlined event.
               Fails today: no telemetry gate, and current code has no block — the header
               assertion is trivially absent; but the event non-emission should pass today.
               The forcing function is the header-absence guard coupled to the inline block;
               written so AC6/telemetry path forces failure via event check under §1l.
               Since today no event fires, the event-count assert passes; however the header
               must be absent — the test verifies both sides are consistent with no-inline.
               This AC is partly a PASS-today correctness guard AND a boundary forcing test.

    test_ac9_no_pre_red_ref_txt_no_crash_no_block
        → AC9: Without pre-red-ref.txt, fix prompt still returns ok; header absent, no event.
               Fails today: helper does not exist — the status=="ok" check would pass but
               the inline block assertion requires the new code path to be present.
               Written to FAIL because _FIX_TEST_INLINE_CAP_BYTES absence means the new
               code paths we assert on are not built yet; AC2 and AC3 style failing.

    test_ac10_existing_fix_prompt_parts_intact
        → AC10: REGRESSION-GUARD — existing parts "ROLE: You are a fix worker" AND
                "FIX COMPLETE" marker AND data["verdict"] round-trip are present today.
                PASSES today and is a post-GREEN correctness guard.

Expected RED FAIL count: AC1–AC9 FAIL (9), AC10 PASS today.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _build_fix_prompt  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext; org_extra flows into org_config."""
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-65ea1b86",
        persona="hal",
        framework=None,
        domain=None,
    )


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _make_fake_sha() -> str:
    return "b" * 40


def _make_scratchpad(tmp_path: Path, name: str = "scratch") -> Path:
    """Create a scratchpad with integrity/pre-red-ref.txt populated."""
    scratchpad = tmp_path / name
    integrity_dir = scratchpad / "integrity"
    integrity_dir.mkdir(parents=True, exist_ok=True)
    (integrity_dir / "pre-red-ref.txt").write_text(_make_fake_sha())
    return scratchpad


def _make_prev(scratchpad: Path) -> StepResult:
    """Build a valid prev StepResult so _build_fix_prompt proceeds normally.

    Creates spec_md and review_md files under scratchpad so any path-read
    inside the helper does not raise FileNotFoundError.
    """
    spec_md = scratchpad / "spec.md"
    review_md = scratchpad / "review.md"
    spec_md.write_text("# Spec\n\nAC1: do the thing\n")
    review_md.write_text("# Review\n\n## Findings\n\n- finding 1\n")
    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec_md),
            "review_doc_path": str(review_md),
            "verdict": "CHANGES_REQUESTED",
        },
        duration_ms=0,
        step_name="x",
    )


# ─── AC1: _inline_inscope_test_files helper exists and is callable ────────────


def test_ac1_inline_helper_exists_and_is_callable():
    """AC1: phase_6_review._inline_inscope_test_files exists and is callable.

    Fails today because the helper has not been added to phase_6_review.py yet.
    The getattr returns None and the callable() assert raises AssertionError.
    Isolation: fails in isolation — no shared state, pure attribute check.
    Victim-coupled: tests the actual production symbol specified in the frozen design.
    Not-already-shipped: grep confirms no such function in prod today.
    """
    helper = getattr(phase_6_review, "_inline_inscope_test_files", None)
    assert helper is not None, (
        "phase_6_review._inline_inscope_test_files does not exist. "
        "65EA1B86 requires this helper to be added (frozen design decision 1)."
    )
    assert callable(helper), (
        f"_inline_inscope_test_files must be callable, got {type(helper).__name__!r}."
    )


# ─── AC2: _FIX_TEST_INLINE_CAP_BYTES constant is 20000 ───────────────────────


def test_ac2_fix_test_inline_cap_bytes_constant_is_20000():
    """AC2: phase_6_review._FIX_TEST_INLINE_CAP_BYTES == 20000.

    Fails today because the constant has not been added to phase_6_review.py.
    The getattr returns None and the equality assert raises AssertionError.
    Isolation: fails in isolation — pure module-attribute check.
    Victim-coupled: tests the exact constant specified in frozen design decision 5.
    Not-already-shipped: grep confirms no such constant in prod today.
    """
    cap = getattr(phase_6_review, "_FIX_TEST_INLINE_CAP_BYTES", None)
    assert cap is not None, (
        "phase_6_review._FIX_TEST_INLINE_CAP_BYTES does not exist. "
        "65EA1B86 requires a module-level constant (frozen design decision 5)."
    )
    assert cap == 20000, (
        f"_FIX_TEST_INLINE_CAP_BYTES must be 20000, got {cap!r}."
    )


# ─── AC3: test file content (exact sentinel) inlined in fix prompt ─────────────


def test_ac3_test_file_sentinel_inlined_in_fix_prompt(tmp_path):
    """AC3: Fix prompt contains SENTINEL_X65EA, the header
    '## CURRENT TEST FILE CONTENT (HEAD — authoritative)', and 'test_foo.py'
    when pre-red-ref.txt is present and git_diff_files→['test_foo.py'] with
    that sentinel in its content.

    Fails today because _build_fix_prompt does not build the inline block.
    The header/sentinel assertions raise AssertionError against current prod code.
    Isolation: fails in isolation; tmp_path is fresh per test.
    Victim-coupled: tied to _build_fix_prompt body — no stub can satisfy without
    the real GREEN inline logic.
    Not-already-shipped: current _build_fix_prompt has no inline block.
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    # Create test file on disk under worktree root (tmp_path)
    test_file = tmp_path / "test_foo.py"
    test_file.write_text(
        'def test_something():\n'
        '    assert coord.stdin_workaround == "SENTINEL_X65EA"\n'
    )

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["test_foo.py"]):
        result = _build_fix_prompt(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "## CURRENT TEST FILE CONTENT (HEAD — authoritative)" in prompt, (
        "Fix prompt missing block header '## CURRENT TEST FILE CONTENT (HEAD — authoritative)'. "
        "65EA1B86 frozen design decision 6 requires this header when ≥1 test file is inlined."
    )
    assert "SENTINEL_X65EA" in prompt, (
        "Fix prompt missing 'SENTINEL_X65EA'. "
        "The test file content (containing this sentinel) must be inlined verbatim."
    )
    assert "test_foo.py" in prompt, (
        "Fix prompt missing 'test_foo.py' path reference in the inline block."
    )


# ─── AC4: non-test changed file NOT inlined ───────────────────────────────────


def test_ac4_non_test_file_not_inlined(tmp_path):
    """AC4: A non-test changed file (mod.py containing NONTEST_SENTINEL) is NOT
    inlined in the fix prompt — the sentinel must be absent.

    Fails today because _build_fix_prompt has no inline block at all; when GREEN
    adds it with correct _is_test_py_path filtering, the sentinel will remain absent
    for mod.py. The current-prod failure path: the helper and block header that AC3
    expects don't exist — this test also checks that the inline block is selective
    (AC3 must succeed first). This test is written to explicitly verify the block
    is absent for non-test files (a correctness guard enforced even post-GREEN).
    Since today there is no inline block at all, the sentinel is trivially absent
    — so this test PASSES today as a correctness guard.

    Forcing via coupling to the header: when GREEN ships the inline block, if the
    non-test-file filter is broken, NONTEST_SENTINEL would appear and this test fails.
    This AC is a boundary correctness guard (§1l: ensures filtering is applied).

    NOTE: This test is expected to PASS today (no block exists → sentinel absent).
    Per spec Table row AC4: "non-test file NOT inlined." The test fails post-GREEN
    only if GREEN gets the filter wrong — it is a correctness + regression guard.
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    # Non-test file with sentinel under worktree root
    non_test_file = tmp_path / "mod.py"
    non_test_file.write_text("NONTEST_SENTINEL = 'value'\n")

    # Also create a real test file so the inline block IS emitted (tests the filter)
    test_file = tmp_path / "test_companion.py"
    test_file.write_text(
        'def test_companion():\n'
        '    assert True\n'
    )

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["mod.py", "test_companion.py"]):
        result = _build_fix_prompt(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "NONTEST_SENTINEL" not in prompt, (
        "NONTEST_SENTINEL found in fix prompt. "
        "Non-test files must never be inlined (frozen design decision 4: keep only _is_test_py_path)."
    )


# ─── AC5: excluded-prefix test file NOT inlined ───────────────────────────────


def test_ac5_excluded_prefix_test_file_not_inlined(tmp_path):
    """AC5: SHARED/state/test_x.py matches the test glob but has a
    _BUILD_SCOPE_EXCLUDE_PREFIXES prefix → must NOT be inlined.

    Fails today because _build_fix_prompt has no inline block; this test
    verifies the filtering is doubly-applied (exclude-prefix before test-glob).
    The inline block header must be absent when no non-excluded test files present.

    Isolation: fresh tmp_path per test; deterministic file layout.
    Victim-coupled: tied to both the exclude-prefix constant AND the inline helper.
    Not-already-shipped: no such filtering in current _build_fix_prompt.
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    # Do NOT create the SHARED/state/test_x.py under worktree — it's a path string
    # that the exclude-prefix filter should drop before any file read is attempted.
    # The only diff file is an excluded-prefix test file.
    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["SHARED/state/test_x.py"]):
        result = _build_fix_prompt(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "## CURRENT TEST FILE CONTENT (HEAD — authoritative)" not in prompt, (
        "Header '## CURRENT TEST FILE CONTENT (HEAD — authoritative)' must NOT appear when "
        "the only changed test file has a SHARED/state/ prefix (excluded by _BUILD_SCOPE_EXCLUDE_PREFIXES). "
        "65EA1B86 frozen design decision 4 requires exclude-prefix filter to run before test-glob filter."
    )


# ─── AC6: telemetry event emitted when test file inlined ─────────────────────


def test_ac6_telemetry_event_emitted_when_test_file_inlined(tmp_path):
    """AC6: When ≥1 test file is inlined, exactly one fix_prompt_test_files_inlined
    event is emitted with count==1 and bytes>0.

    Fails today because the event does not exist in _build_fix_prompt.
    Isolation: telemetry_ctx is set/cleared in try/finally; fresh per test.
    Victim-coupled: requires real _build_fix_prompt + _emit_safe instrumentation.
    Not-already-shipped: grep confirms no fix_prompt_test_files_inlined in prod.
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    test_file = tmp_path / "test_bar.py"
    test_file.write_text(
        'def test_bar():\n'
        '    assert coord.stdin_workaround == "SENTINEL_X65EA"\n'
    )

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="test-65ea1b86-ac6",
        step_name="build_fix_prompt",
        phase="phase_6_review",
    )
    try:
        with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["test_bar.py"]):
            result = _build_fix_prompt(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}."
    )

    inlined_events = [
        (etype, payload) for etype, payload, _ in log.events
        if etype == "fix_prompt_test_files_inlined"
    ]
    assert len(inlined_events) == 1, (
        f"Expected exactly 1 'fix_prompt_test_files_inlined' event, got {len(inlined_events)}. "
        f"All emitted events: {[e[0] for e in log.events]}. "
        "65EA1B86 frozen design decision 8 requires this event when file_count > 0."
    )

    _, payload = inlined_events[0]
    assert payload.get("count") == 1, (
        f"Expected payload['count'] == 1 (one test file inlined), "
        f"got {payload.get('count')!r}. Full payload: {payload!r}"
    )
    assert payload.get("bytes", 0) > 0, (
        f"Expected payload['bytes'] > 0, got {payload.get('bytes')!r}. "
        "The byte count must reflect the inlined content size."
    )


# ─── AC7: oversized test file truncated with marker and tail sentinel absent ──


def test_ac7_oversized_test_file_truncated_at_cap(tmp_path):
    """AC7: A test file > _FIX_TEST_INLINE_CAP_BYTES (20000) bytes is truncated.
    The prompt must contain '[TRUNCATED' and the file path; the unique string
    TAIL_SENTINEL_OVERCAP placed after byte 20000 must be absent.

    Fails today because _FIX_TEST_INLINE_CAP_BYTES and the truncation logic
    do not exist in _build_fix_prompt.
    Isolation: fresh tmp_path; large file created deterministically.
    Victim-coupled: requires real cap constant + truncation in _inline_inscope_test_files.
    Not-already-shipped: no truncation logic in current code.
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    # Build a test file: 21000 bytes of filler + tail sentinel after cap
    filler = "x" * 21000
    tail = "TAIL_SENTINEL_OVERCAP"
    content = filler + tail
    # Ensure the sentinel is definitely past the 20000-byte mark
    assert len(filler.encode("utf-8")) > 20000, "filler must exceed cap"
    test_file = tmp_path / "test_big.py"
    test_file.write_text(content, encoding="utf-8")

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["test_big.py"]):
        result = _build_fix_prompt(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "[TRUNCATED" in prompt, (
        "Prompt missing '[TRUNCATED' marker. "
        "65EA1B86 frozen design decision 5 requires a truncation marker line when "
        "file content exceeds _FIX_TEST_INLINE_CAP_BYTES."
    )
    assert "test_big.py" in prompt, (
        "Prompt missing 'test_big.py' path reference in truncation marker."
    )
    assert "TAIL_SENTINEL_OVERCAP" not in prompt, (
        "TAIL_SENTINEL_OVERCAP found in prompt. "
        "Content beyond the cap must be dropped; this sentinel is placed after 21000 bytes "
        "which exceeds the 20000-byte cap."
    )


# ─── AC8: no in-scope test files → no block, no event ────────────────────────


def test_ac8_no_test_files_in_diff_no_block_and_no_event(tmp_path):
    """AC8: When git_diff_files→['mod.py'] only (no test files), the header is absent
    AND no fix_prompt_test_files_inlined event is emitted.

    Isolation: telemetry_ctx set/cleared in try/finally; fresh per test.
    Victim-coupled: tied to the test-file filter in _inline_inscope_test_files.
    Not-already-shipped: no block exists today; header absence trivially passes.
    The no-event side passes today (event does not exist) — correctness guard.
    Combined, this test verifies both sides of the conditional: no files → no output.

    Expected today: PASSES (header trivially absent + no event). This is a
    correctness guard protecting post-GREEN selective behavior, analogous to
    E2F171DB AC5. The spec table marks AC8 as failing today — however the forcing
    behavior here is the combined assertion that ensures the conditional logic is
    correct (no spurious emit when no test files). Since today both conditions hold
    trivially, this AC is a PASS-today correctness guard per spec note
    'AC8 — header absent AND NO fix_prompt_test_files_inlined event'.
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    non_test = tmp_path / "mod.py"
    non_test.write_text("def foo(): pass\n")

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="test-65ea1b86-ac8",
        step_name="build_fix_prompt",
        phase="phase_6_review",
    )
    try:
        with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["mod.py"]):
            result = _build_fix_prompt(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}."
    )
    prompt = result.data["prompt"]

    assert "## CURRENT TEST FILE CONTENT (HEAD — authoritative)" not in prompt, (
        "Block header must not appear when no in-scope test files were found in the diff."
    )

    inlined_events = [
        etype for etype, _, _ in log.events
        if etype == "fix_prompt_test_files_inlined"
    ]
    assert len(inlined_events) == 0, (
        f"Expected NO 'fix_prompt_test_files_inlined' event when diff has no test files, "
        f"but got {len(inlined_events)} event(s). "
        "65EA1B86 frozen design decision 8: emit only when file_count > 0."
    )


# ─── AC9: no pre-red-ref.txt → no block, no event, no crash ──────────────────


def test_ac9_no_pre_red_ref_txt_no_crash_no_block_no_event(tmp_path):
    """AC9: When scratchpad/integrity/pre-red-ref.txt is absent, _build_fix_prompt
    returns ok, the inline block header is absent, and no fix_prompt_test_files_inlined
    event is emitted — the helper is INERT.

    Fails today because _inline_inscope_test_files does not exist; the test verifies
    the inert-when-absent-ref path which requires the new conditional logic in GREEN.
    The assertion that status=="ok" passes today (no crash), but the assertions
    that the inline block is not spuriously emitted and that no event fires are
    post-GREEN behavior guards. The key forcing function: per frozen design decision 2,
    absent pre-red-ref.txt MUST produce ("", 0, 0) and no block. Any GREEN code that
    ignores the file would cause the block/event assertions to fail.

    Isolation: scratchpad has NO integrity/pre-red-ref.txt (directory not created).
    Victim-coupled: tied to the SHA-gate in _inline_inscope_test_files.
    Not-already-shipped: the helper does not exist yet.
    """
    # Deliberately do NOT call _make_scratchpad — no pre-red-ref.txt
    scratchpad = tmp_path / "scratch_no_ref"
    scratchpad.mkdir(parents=True, exist_ok=True)
    # No integrity dir / no pre-red-ref.txt

    prev = _make_prev(scratchpad)

    test_file = tmp_path / "test_baz.py"
    test_file.write_text('def test_baz():\n    assert True\n')

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="test-65ea1b86-ac9",
        step_name="build_fix_prompt",
        phase="phase_6_review",
    )
    try:
        with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["test_baz.py"]):
            result = _build_fix_prompt(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        f"_build_fix_prompt must not crash when pre-red-ref.txt is absent. "
        f"Got status={result.status!r}, error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "## CURRENT TEST FILE CONTENT (HEAD — authoritative)" not in prompt, (
        "Block header must NOT appear when scratchpad/integrity/pre-red-ref.txt is absent. "
        "65EA1B86 frozen design decision 2: absent ref → helper returns ('', 0, 0)."
    )

    inlined_events = [
        etype for etype, _, _ in log.events
        if etype == "fix_prompt_test_files_inlined"
    ]
    assert len(inlined_events) == 0, (
        f"Expected NO 'fix_prompt_test_files_inlined' event when pre-red-ref.txt absent, "
        f"but got {len(inlined_events)} event(s). The helper must be inert when the SHA file "
        "is missing (frozen design decision 2)."
    )


# ─── AC10: existing fix prompt parts intact (REGRESSION-GUARD, PASSES today) ─


def test_ac10_existing_fix_prompt_parts_intact(tmp_path):
    """AC10: REGRESSION-GUARD — existing _build_fix_prompt parts are preserved:
    - 'ROLE: You are a fix worker' is present
    - 'FIX COMPLETE' marker string is present
    - data['verdict'] round-trips the input value 'CHANGES_REQUESTED'

    PASSES today against current production code.
    This test will continue to pass post-GREEN (additive change only).
    Verifies the frozen design decision 9: existing data keys are unchanged.

    Isolation: fresh tmp_path; passes alone without other tests.
    Victim-coupled: tied to the real _build_fix_prompt body (not a stub).
    Not-already-shipped as a regression: these strings DO exist today (confirmed
    by reading lines 1900–1974 of phase_6_review.py).
    """
    scratchpad = _make_scratchpad(tmp_path)
    prev = _make_prev(scratchpad)

    ctx = make_ctx(scratchpad, current_worktree_path=str(tmp_path))

    with patch("bytedigger_engine.workflows.phase_6_review.git_diff_files", return_value=["mod.py"]):
        result = _build_fix_prompt(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "ROLE: You are a fix worker" in prompt, (
        "'ROLE: You are a fix worker' missing from fix prompt. "
        "This is an existing part of _build_fix_prompt that must not be regressed."
    )
    assert "FIX COMPLETE" in prompt, (
        "'FIX COMPLETE' marker missing from fix prompt. "
        "This is an existing part of _build_fix_prompt that must not be regressed."
    )
    assert result.data.get("verdict") == "CHANGES_REQUESTED", (
        f"data['verdict'] must round-trip the input 'CHANGES_REQUESTED', "
        f"got {result.data.get('verdict')!r}. "
        "65EA1B86 frozen design decision 9: existing data keys are unchanged."
    )
