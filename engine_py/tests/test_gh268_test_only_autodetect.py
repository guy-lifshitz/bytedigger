"""GH268 RED tests — shared test-only-intent auto-detect resolver.

Spec: SHARED/memory/Decisions/2026-07-16_GH268_test_only_autodetect_spec.md

New symbols under test (do NOT exist yet in production):
    phase_workflows_common.detect_test_only_intent
    phase_workflows_common.resolve_engine_mode
    phase_workflows_common._engine_mode_task_text

They are imported INSIDE each test body (not at module top level) so this
file COLLECTS cleanly today (§1q / D1CF5FDF non-collectable-RED guard) even
though the symbols don't exist — tests FAIL at assert/call time via
AttributeError/ImportError, not at collection time.

AC mapping:
    test_ac1_detect_fires_on_positive_phrases       -> AC1
    test_ac2_detect_false_on_negative_phrases        -> AC2
    test_ac3_marker_test_only_wins_no_autodetect_event -> AC3
    test_ac4_marker_other_mode_wins_over_intent      -> AC4
    test_ac5_markerless_intent_autodetects_with_event -> AC5
    test_ac6_markerless_no_intent_returns_none       -> AC6
    test_ac7_spec_path_none_question_carries_intent  -> AC7
    test_ac8_commit_red_tests_autodetects_skip        -> AC8
    test_ac9_commit_red_tests_benign_no_skip          -> AC9
    test_ac10_review_prompt_autodetects_note          -> AC10
    test_ac11_review_prompt_benign_no_note            -> AC11
    test_ac12_marker_regression_guard_still_skips     -> AC12

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_5_implement as _p5  # noqa: E402
import phase_6_review as p6  # noqa: E402


# ─── shared helpers ────────────────────────────────────────────────────────


def _make_ctx(
    scratchpad: Path | None = None,
    *,
    task_description: str | None = None,
    question: str = "some question",
    git_cwd: str | None = None,
) -> WorkflowContext:
    org: dict = {}
    if scratchpad is not None:
        org["scratchpad_dir"] = str(scratchpad)
    if task_description is not None:
        org["task_description"] = task_description
    if git_cwd:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh268",
        persona="hal",
        framework=None,
        domain=None,
    )


def _spec_with_content(tmp_path: Path, content: str, name: str = "spec.md") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _patch_emit_p5(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


def _make_prev(spec_path: str | None = None, **extra) -> StepResult:
    data: dict = {
        "red_log_path": "/tmp/test-gh268-red-output.log",
        "cycle": 1,
        "red_test_paths": None,
    }
    if spec_path is not None:
        data["spec_path"] = spec_path
    data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_red_artifact")


def _write_spec_p6(scratchpad: Path, content: str) -> Path:
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "build-spec.md"
    spec_path.write_text(content, encoding="utf-8")
    return spec_path


def _call_build_review_prompt(scratchpad: Path, *, task_description: str | None = None) -> StepResult:
    ctx = _make_ctx(scratchpad, task_description=task_description)
    with patch.object(p6, "git_diff_files", return_value=[]), \
         patch.object(p6, "_resolve_worktree_root", return_value=scratchpad):
        result = p6._build_review_prompt(ctx, None)
    return result


# ─── AC1: detect_test_only_intent fires on positive phrases ──────────────


@pytest.mark.parametrize(
    "text",
    [
        "This is a test-only change.",
        "Tests Only fix incoming.",
        "tests-only patch",
        "This PR has no production code.",
        "please only fix the tests",
        "fix the test only, do not touch other stuff",
        "do not touch production in this change",
        "don't modify the production code at all",
    ],
)
def test_ac1_detect_fires_on_positive_phrases(text):
    """AC1: detect_test_only_intent(text) is True for each enumerated phrase.

    Fails today: phase_workflows_common has no detect_test_only_intent symbol.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    assert pwc.detect_test_only_intent(text) is True, (
        f"Expected detect_test_only_intent to fire on {text!r}"
    )


# ─── AC2: detect_test_only_intent is False on negative/edge inputs ────────


@pytest.mark.parametrize(
    "text",
    [
        "fix the failing test by correcting the production parser",
        "add tests for the new feature",
        "fix the flaky test harness in CI and update prod retry logic",
        "",
        None,
        42,
    ],
)
def test_ac2_detect_false_on_negative_phrases(text):
    """AC2: detect_test_only_intent returns False for non-intent / non-str input.

    Fails today: symbol does not exist -> AttributeError.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    assert pwc.detect_test_only_intent(text) is False, (
        f"Expected detect_test_only_intent to be False for {text!r}"
    )


# ─── AC3: marker test_only wins, no autodetect event fires ───────────────


def test_ac3_marker_test_only_wins_no_autodetect_event(tmp_path):
    """AC3: spec with test_only marker + ctx WITHOUT intent -> 'test_only',
    and NO engine_mode_autodetected event fires.

    Fails today: resolve_engine_mode does not exist.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    spec = _spec_with_content(tmp_path, "<!-- engine-mode: test_only -->\n# spec\n")
    ctx = _make_ctx(question="benign build task, nothing special")

    captured = []
    with patch.object(pwc, "_emit_safe", lambda et, p, severity="warning": captured.append(et)):
        result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result == "test_only", f"Expected 'test_only' via marker, got {result!r}"
    assert "engine_mode_autodetected" not in captured, (
        "Marker path must not emit engine_mode_autodetected"
    )


# ─── AC4: marker with other mode wins over intent text ────────────────────


def test_ac4_marker_other_mode_wins_over_intent(tmp_path):
    """AC4: spec with '<!-- engine-mode: standard -->' + ctx WITH intent ->
    'standard' (manual override wins), no autodetect event.

    Fails today: resolve_engine_mode does not exist.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    spec = _spec_with_content(tmp_path, "<!-- engine-mode: standard -->\n# spec\n")
    ctx = _make_ctx(task_description="this is a test-only fix, no production code")

    captured = []
    with patch.object(pwc, "_emit_safe", lambda et, p, severity="warning": captured.append(et)):
        result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result == "standard", f"Expected manual override 'standard', got {result!r}"
    assert "engine_mode_autodetected" not in captured, (
        "Manual override must suppress autodetect event"
    )


# ─── AC5: marker-less spec, intent in task_description -> autodetect ─────


def test_ac5_markerless_intent_autodetects_with_event(tmp_path):
    """AC5: marker-less spec, ctx.org_config.task_description with intent ->
    'test_only' AND exactly one engine_mode_autodetected event with the
    expected payload shape.

    Fails today: resolve_engine_mode does not exist.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    spec = _spec_with_content(tmp_path, "# spec\nno marker here\n")
    ctx = _make_ctx(task_description="Fix the assertion — test-only, no production code")

    captured = []
    with patch.object(
        pwc, "_emit_safe",
        lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
    ):
        result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result == "test_only", f"Expected autodetected 'test_only', got {result!r}"
    events = [c for c in captured if c["type"] == "engine_mode_autodetected"]
    assert len(events) == 1, f"Expected exactly 1 engine_mode_autodetected event, got {len(events)}"
    assert events[0]["payload"].get("mode") == "test_only"
    assert events[0]["payload"].get("source") == "task_intent"


# ─── AC6: marker-less spec, no intent -> None, no event ──────────────────


def test_ac6_markerless_no_intent_returns_none(tmp_path):
    """AC6: marker-less spec, benign task_description AND question -> None,
    no event fires.

    Fails today: resolve_engine_mode does not exist.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    spec = _spec_with_content(tmp_path, "# spec\nnothing special\n")
    ctx = _make_ctx(
        task_description="add a new feature to the dashboard",
        question="add a new feature to the dashboard",
    )

    captured = []
    with patch.object(pwc, "_emit_safe", lambda et, p, severity="warning": captured.append(et)):
        result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result is None, f"Expected None for benign markerless spec, got {result!r}"
    assert "engine_mode_autodetected" not in captured


# ─── AC7: spec_path=None, question carries intent -> test_only ───────────


def test_ac7_spec_path_none_question_carries_intent():
    """AC7: spec_path=None, ctx.question carries 'test-only' (task_description
    absent) -> 'test_only'.

    Fails today: resolve_engine_mode does not exist.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    ctx = _make_ctx(question="This is a test-only change, please review")

    result = pwc.resolve_engine_mode(None, ctx)

    assert result == "test_only", f"Expected 'test_only' from question fallback, got {result!r}"


# ─── AC8: _commit_red_tests autodetects skip from task_description ───────


def test_ac8_commit_red_tests_autodetects_skip(tmp_path):
    """AC8: marker-less spec on disk + ctx.org_config.task_description
    declaring test-only intent -> _commit_red_tests skips with the same
    contract as the manual-marker path (commit_red_tests_skipped ==
    'test_only_mode', red_test_paths == [], red_commit_sha is None) and
    emits commit_red_tests_skipped {reason: 'test_only_mode'}.

    Fails today: _commit_red_tests still calls the bare _read_engine_mode
    (marker-only), so a marker-less spec never skips regardless of intent.
    """
    spec = _spec_with_content(tmp_path, "# spec\nno marker\n")
    mp = pytest.MonkeyPatch()
    captured: list[dict] = []
    mp.setattr(
        _p5, "_emit_safe",
        lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
    )

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(
        scratchpad,
        task_description="Fix the assertion — test-only, no production code",
    )
    prev = _make_prev(spec_path=str(spec))

    try:
        result = _p5._commit_red_tests(ctx, prev)
    finally:
        mp.undo()

    assert result.status == "ok", (
        f"AC8: expected status='ok' on autodetected test_only skip, got {result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    assert result.data is not None
    assert result.data.get("commit_red_tests_skipped") == "test_only_mode", (
        f"AC8: expected commit_red_tests_skipped='test_only_mode', "
        f"got {result.data.get('commit_red_tests_skipped')!r}"
    )
    assert result.data.get("red_test_paths") == []
    assert result.data.get("red_commit_sha") is None
    skip_events = [c for c in captured if c["type"] == "commit_red_tests_skipped"]
    assert len(skip_events) == 1, (
        f"AC8: expected exactly 1 commit_red_tests_skipped event, got {len(skip_events)}"
    )
    assert skip_events[0]["payload"].get("reason") == "test_only_mode"


# ─── AC9: _commit_red_tests benign task_description -> normal path ───────


def test_ac9_commit_red_tests_benign_no_skip(tmp_path, monkeypatch):
    """AC9: marker-less spec + benign task_description -> result does NOT
    carry commit_red_tests_skipped (normal path taken, no autodetect).

    This exercises resolve_engine_mode's negative branch through the real
    call site; may legitimately still fail today for the WRONG reason
    (current code doesn't call resolve_engine_mode at all) but the assertion
    itself targets the post-GREEN invariant.
    """
    monkeypatch.setattr(
        _p5, "_derive_red_paths_via_git_diff", lambda *a, **kw: [],
    )
    captured = _patch_emit_p5(monkeypatch)

    spec = _spec_with_content(tmp_path, "# spec\nno marker\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(
        scratchpad,
        task_description="add a shiny new dashboard widget",
        question="add a shiny new dashboard widget",
    )
    prev = _make_prev(spec_path=str(spec))

    result = _p5._commit_red_tests(ctx, prev)

    assert result.data is None or result.data.get("commit_red_tests_skipped") is None, (
        "AC9: benign task_description must not trigger test_only skip"
    )


# ─── AC10: phase_6 review prompt autodetects test-only note ──────────────


def test_ac10_review_prompt_autodetects_note(tmp_path):
    """AC10: marker-less spec + intent-carrying task_description -> prompt
    contains '## TEST-ONLY BUILD' AND phase_6_test_only_note_injected fires.

    Fails today: _build_review_prompt only consults the marker via
    _read_engine_mode; a marker-less spec never injects the note regardless
    of ctx content.
    """
    scratchpad = tmp_path / "scratch"
    _write_spec_p6(scratchpad, "# spec\nno marker at all\n")

    emitted: list[tuple[str, dict]] = []
    with patch.object(p6, "_emit_safe", lambda et, p: emitted.append((et, p))), \
         patch.object(p6, "git_diff_files", return_value=[]), \
         patch.object(p6, "_resolve_worktree_root", return_value=scratchpad):
        ctx = _make_ctx(
            scratchpad,
            task_description="Fix the assertion — test-only, no production code",
        )
        result = p6._build_review_prompt(ctx, None)

    assert result.status == "ok", f"AC10: expected status='ok', got {result.status!r}"
    prompt = result.data["prompt"]
    assert "## TEST-ONLY BUILD" in prompt, (
        "AC10: expected '## TEST-ONLY BUILD' injected via autodetected intent"
    )
    note_events = [e for e in emitted if e[0] == "phase_6_test_only_note_injected"]
    assert len(note_events) == 1, (
        f"AC10: expected exactly 1 phase_6_test_only_note_injected event, got {len(note_events)}"
    )


# ─── AC11: phase_6 review prompt benign -> no note ────────────────────────


def test_ac11_review_prompt_benign_no_note(tmp_path):
    """AC11: marker-less spec + benign ctx -> prompt does NOT contain
    '## TEST-ONLY BUILD'.
    """
    scratchpad = tmp_path / "scratch"
    _write_spec_p6(scratchpad, "# spec\nno marker at all\n")

    with patch.object(p6, "git_diff_files", return_value=[]), \
         patch.object(p6, "_resolve_worktree_root", return_value=scratchpad):
        ctx = _make_ctx(
            scratchpad,
            task_description="add a shiny new dashboard widget",
            question="add a shiny new dashboard widget",
        )
        result = p6._build_review_prompt(ctx, None)

    assert result.status == "ok", f"AC11: expected status='ok', got {result.status!r}"
    prompt = result.data["prompt"]
    assert "## TEST-ONLY BUILD" not in prompt, (
        "AC11: '## TEST-ONLY BUILD' must not appear for benign markerless spec"
    )


# ─── AC12: marker regression guard — existing marker flow unaffected ─────


def test_ac12_marker_regression_guard_still_skips(tmp_path, monkeypatch):
    """AC12: existing marker flow (marker=test_only, benign ctx) through
    _commit_red_tests still skips with reason='test_only_mode' — the
    resolve_engine_mode swap must not regress the manual-marker path.

    This is a presence-gated import: resolve_engine_mode must be reachable
    from phase_5_implement's module namespace (i.e. actually wired in per
    §2.2), not just importable from phase_workflows_common.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    assert hasattr(_p5, "resolve_engine_mode"), (
        "AC12: phase_5_implement must import resolve_engine_mode from "
        "phase_workflows_common per spec §2.2 call-site swap"
    )
    assert _p5.resolve_engine_mode is pwc.resolve_engine_mode

    captured = _patch_emit_p5(monkeypatch)
    spec = _spec_with_content(tmp_path, "<!-- engine-mode: test_only -->\n# spec\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, question="benign build task")
    prev = _make_prev(spec_path=str(spec))

    result = _p5._commit_red_tests(ctx, prev)

    assert result.status == "ok"
    assert result.data.get("commit_red_tests_skipped") == "test_only_mode", (
        f"AC12: marker regression — expected skip, got "
        f"{result.data.get('commit_red_tests_skipped')!r}"
    )
    skip_events = [c for c in captured if c["type"] == "commit_red_tests_skipped"]
    assert len(skip_events) == 1
    assert skip_events[0]["payload"].get("reason") == "test_only_mode"
