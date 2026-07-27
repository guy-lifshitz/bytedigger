"""GH268 / GH1245 regression floor — the prose-intent auto-detect resolver
is REMOVED, not shipped.

Original spec: SHARED/memory/Decisions/2026-07-16_GH268_test_only_autodetect_spec.md
Removal spec:  SHARED/memory/Decisions/2026-07-26_GH1245_test_only_red_boundary_spec.md

GH268 shipped a prose-intent auto-detect resolver (`detect_test_only_intent`,
`_engine_mode_task_text`, `_TEST_ONLY_INTENT_RE`) alongside the declared-field
marker. GH1245 found that resolver unsound by construction: its first
alternative `\\btests?[- ]only\\b` matches any correct description of TDD RED
discipline ("the RED diff is tests only"), so an ordinary TDD build describing
its own process classified itself as needing no RED commit and died in
commit_green_code with E_MISSING_RED_BOUNDARY (incident
forge-1785057041-6079411c). GH1245 §2.1 removes the prose branch entirely —
`resolve_engine_mode` now resolves ONLY from the explicit spec marker.

This file therefore now asserts the INVERSE of the original GH268 contract:
prose never selects a mode, the three now-unreachable symbols are gone, and
no `engine_mode_autodetected` event is ever emitted — a regression floor
against reintroducing the prose predicate. The marker-precedence tests
(declared field, explicit override) still hold and are retained unchanged.

New symbols under test (do NOT exist yet in production; §1q/D1CF5FDF —
imported INSIDE each test body so this file COLLECTS cleanly today):
    phase_workflows_common.resolve_engine_mode (existing symbol, new contract)

AC mapping:
    test_ac1_prose_symbols_absent                    -> AC2 (GH1245 spec)
    test_ac2_prose_never_selects_mode                -> AC1 (GH1245 spec)
    test_ac3_marker_test_only_wins_no_autodetect_event -> retained (marker precedence)
    test_ac4_marker_other_mode_wins_over_intent      -> retained (marker precedence)
    test_ac5_markerless_intent_no_longer_autodetects -> AC1 (GH1245 spec, inverted GH268 AC5)
    test_ac6_markerless_no_intent_returns_none       -> retained
    test_ac7_spec_path_none_question_intent_returns_none -> AC1 (GH1245 spec, inverted GH268 AC7)
    test_ac8_commit_red_tests_no_longer_autodetects_skip -> AC1 (GH1245 spec, inverted GH268 AC8)
    test_ac9_commit_red_tests_benign_no_skip         -> retained
    test_ac10_review_prompt_no_longer_autodetects_note -> AC1 (GH1245 spec, inverted GH268 AC10)
    test_ac11_review_prompt_benign_no_note           -> retained
    test_ac12_marker_regression_guard_still_skips    -> retained

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

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


def _real(p: Path) -> Path:
    """§1j: realpath-normalise a tmp_path before using it as a git/subprocess cwd."""
    return Path(os.path.realpath(str(p)))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


def _init_repo(cwd: Path) -> str:
    """Real, isolated git repo (one commit). Returns the initial commit SHA.

    Gate v2 MINOR-10: some ACs pass no git_cwd, so the test_only boundary
    resolution (GH1245 §2.2) would resolve `git rev-parse HEAD` against
    Path.cwd() — the developer's live HAL checkout — instead of an isolated
    fixture repo.
    """
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "gh268@example.com")
    _git(cwd, "config", "user.name", "GH268 Test")
    (cwd / "README.md").write_text("init\n", encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", "init")
    res = _git(cwd, "rev-parse", "HEAD")
    assert res.returncode == 0, f"git rev-parse HEAD failed: {res.stderr}"
    return res.stdout.strip()


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


# ─── AC2 (GH1245): the three unreachable symbols are gone ─────────────────


def test_ac1_prose_symbols_absent():
    """AC2 (GH1245): phase_workflows_common no longer defines
    detect_test_only_intent, _engine_mode_task_text, or _TEST_ONLY_INTENT_RE.

    Fails today: all three symbols still exist (this is the GH268 shipment
    GH1245 removes).
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    for name in ("detect_test_only_intent", "_engine_mode_task_text", "_TEST_ONLY_INTENT_RE"):
        assert not hasattr(pwc, name), (
            f"AC2 (GH1245): phase_workflows_common must no longer define {name!r}"
        )


# ─── AC1 (GH1245): prose never selects a mode ─────────────────────────────


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
def test_ac2_prose_never_selects_mode(text):
    """AC1 (GH1245): resolve_engine_mode(None, ctx) returns None for every
    phrase that used to trigger the GH268 autodetect — prose is no longer a
    source of the engine mode.

    Fails today: resolve_engine_mode's prose branch still fires and returns
    'test_only' for each of these phrases.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    ctx = _make_ctx(question=text)
    result = pwc.resolve_engine_mode(None, ctx)

    assert result is None, (
        f"AC1 (GH1245): prose {text!r} must not select a mode, got {result!r}"
    )


# ─── retained: marker test_only wins, no autodetect event fires ──────────


def test_ac3_marker_test_only_wins_no_autodetect_event(tmp_path):
    """Retained marker-precedence test: spec with test_only marker + ctx
    WITHOUT intent -> 'test_only', and NO engine_mode_autodetected event
    fires. Still holds post-GH1245 (declared field is untouched)."""
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


# ─── retained: manual override wins over (now-inert) intent text ─────────


def test_ac4_marker_other_mode_wins_over_intent(tmp_path):
    """Retained marker-precedence test: spec with '<!-- engine-mode: standard -->'
    + ctx text that used to carry intent -> 'standard' (manual override wins
    trivially now, since prose is never consulted at all)."""
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


# ─── AC1 (GH1245), inverted GH268 AC5: markerless intent no longer autodetects ──


def test_ac5_markerless_intent_no_longer_autodetects(tmp_path):
    """AC1 (GH1245): marker-less spec, ctx.org_config.task_description with
    former intent phrasing -> None, and NO engine_mode_autodetected event.

    Fails today: resolve_engine_mode still autodetects 'test_only' from
    task_description prose and emits engine_mode_autodetected.
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

    assert result is None, (
        f"AC1 (GH1245): markerless intent-shaped text must no longer "
        f"autodetect, expected None got {result!r}"
    )
    events = [c for c in captured if c["type"] == "engine_mode_autodetected"]
    assert len(events) == 0, (
        f"AC1 (GH1245): expected 0 engine_mode_autodetected events, got {len(events)}"
    )


# ─── retained: markerless, no intent -> None, no event ────────────────────


def test_ac6_markerless_no_intent_returns_none(tmp_path):
    """Retained: marker-less spec, benign task_description AND question ->
    None, no event fires. Held before and after GH1245."""
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


# ─── AC1 (GH1245), inverted GH268 AC7: question-carried intent no longer used ──


def test_ac7_spec_path_none_question_intent_returns_none():
    """AC1 (GH1245): spec_path=None, ctx.question carries former intent
    phrasing (task_description absent) -> None, not 'test_only'.

    Fails today: resolve_engine_mode still falls back to ctx.question for
    prose-intent detection and returns 'test_only'.
    """
    import phase_workflows_common as pwc  # noqa: PLC0415

    ctx = _make_ctx(question="This is a test-only change, please review")

    result = pwc.resolve_engine_mode(None, ctx)

    assert result is None, (
        f"AC1 (GH1245): question-carried intent must no longer resolve a "
        f"mode, expected None got {result!r}"
    )


# ─── AC1 (GH1245), inverted GH268 AC8: _commit_red_tests no longer autodetects ──


def test_ac8_commit_red_tests_no_longer_autodetects_skip(tmp_path):
    """AC1 (GH1245): marker-less spec on disk + ctx.org_config.task_description
    carrying former intent phrasing -> _commit_red_tests must NOT skip
    (commit_red_tests_skipped sentinel absent from the result), because
    resolve_engine_mode no longer resolves anything from prose.

    Fails today: _commit_red_tests's resolve_engine_mode call still
    autodetects 'test_only' from task_description and skips.
    """
    spec = _spec_with_content(tmp_path, "# spec\nno marker\n")
    mp = pytest.MonkeyPatch()
    captured: list[dict] = []
    mp.setattr(
        _p5, "_emit_safe",
        lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
    )
    # Live-repo mutation hazard (gate v1 MAJOR-2): once GH1245 lands,
    # resolve_engine_mode no longer autodetects from this task_description,
    # so _commit_red_tests falls through to the real git path instead of
    # short-circuiting. Without this stub, git_cwd resolves to Path.cwd()
    # (the developer's live HAL checkout) and execution reaches real
    # `git add`/`git commit` there. Stub the deriver exactly as the sibling
    # test_ac9_commit_red_tests_benign_no_skip does.
    mp.setattr(
        _p5, "_derive_red_paths_via_git_diff", lambda *a, **kw: [],
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

    assert result.data is None or result.data.get("commit_red_tests_skipped") is None, (
        f"AC1 (GH1245): markerless intent-shaped task_description must not "
        f"trigger a skip, got commit_red_tests_skipped="
        f"{result.data.get('commit_red_tests_skipped') if result.data else 'N/A'!r}"
    )
    skip_events = [c for c in captured if c["type"] == "commit_red_tests_skipped"]
    assert len(skip_events) == 0, (
        f"AC1 (GH1245): expected 0 commit_red_tests_skipped events, got {len(skip_events)}"
    )


# ─── retained: _commit_red_tests benign task_description -> normal path ──


def test_ac9_commit_red_tests_benign_no_skip(tmp_path, monkeypatch):
    """Retained: marker-less spec + benign task_description -> result does
    NOT carry commit_red_tests_skipped. Held before and after GH1245."""
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


# ─── AC1 (GH1245), inverted GH268 AC10: review prompt no longer autodetects ──


def test_ac10_review_prompt_no_longer_autodetects_note(tmp_path):
    """AC1 (GH1245): marker-less spec + intent-shaped task_description ->
    prompt does NOT contain '## TEST-ONLY BUILD' and
    phase_6_test_only_note_injected does NOT fire.

    Fails today: _build_review_prompt still injects the note via
    resolve_engine_mode's prose fallback.
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

    assert result.status == "ok", f"AC1 (GH1245): expected status='ok', got {result.status!r}"
    prompt = result.data["prompt"]
    assert "## TEST-ONLY BUILD" not in prompt, (
        "AC1 (GH1245): '## TEST-ONLY BUILD' must not be injected from prose intent"
    )
    note_events = [e for e in emitted if e[0] == "phase_6_test_only_note_injected"]
    assert len(note_events) == 0, (
        f"AC1 (GH1245): expected 0 phase_6_test_only_note_injected events, got {len(note_events)}"
    )


# ─── retained: benign markerless spec -> no note ──────────────────────────


def test_ac11_review_prompt_benign_no_note(tmp_path):
    """Retained: marker-less spec + benign ctx -> prompt does NOT contain
    '## TEST-ONLY BUILD'. Held before and after GH1245."""
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


# ─── retained: marker regression guard — existing marker flow unaffected ─


def test_ac12_marker_regression_guard_still_skips(tmp_path, monkeypatch):
    """Retained regression guard: existing marker flow (marker=test_only,
    benign ctx) through _commit_red_tests still skips with
    reason='test_only_mode' — the GH1245 prose removal must not regress the
    manual-marker path. resolve_engine_mode must still be reachable from
    phase_5_implement's module namespace (i.e. wired per spec §2.2)."""
    import phase_workflows_common as pwc  # noqa: PLC0415

    assert hasattr(_p5, "resolve_engine_mode"), (
        "phase_5_implement must import resolve_engine_mode from "
        "phase_workflows_common per spec §2.2 call-site swap"
    )
    assert _p5.resolve_engine_mode is pwc.resolve_engine_mode

    captured = _patch_emit_p5(monkeypatch)
    spec = _spec_with_content(tmp_path, "<!-- engine-mode: test_only -->\n# spec\n")

    # Gate v2 MINOR-10: pin to an isolated tmp_path git repo — post-GH1245 the
    # marker path resolves `git rev-parse HEAD` for the boundary SHA, so
    # without an isolated git_cwd this would reach the developer's live HAL
    # checkout.
    repo = _real(tmp_path / "repo")
    repo.mkdir()
    _init_repo(repo)

    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, question="benign build task", git_cwd=str(repo))
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
