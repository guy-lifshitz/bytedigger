"""885AAE33 RED tests — phase_5 _commit_red_tests test-only-mode opt-out.

When a spec file carries the marker `<!-- engine-mode: test_only -->` on line 1
or 2, `_commit_red_tests` must skip git operations, emit a telemetry event, and
return ok with empty red_test_paths / a 40-hex pre-RED red_commit_sha equal to
`git rev-parse HEAD` (GH1245: the boundary is no longer discarded — see
test_gh1245_test_only_red_boundary.py).

All 12 tests FAIL against current phase_5_implement.py (_read_engine_mode does
not exist; _commit_red_tests has no test-only skip branch).
They PASS once GREEN implements _read_engine_mode + skip branch.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402

from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: E402  — used for monkeypatching


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, git_cwd: str | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="test question",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(spec_path: str | None = None, **extra) -> StepResult:
    data: dict = {
        "red_log_path": "/tmp/test-red-output.log",
        "cycle": 1,
        "red_test_paths": None,
    }
    if spec_path is not None:
        data["spec_path"] = spec_path
    data.update(extra)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture all _emit_safe calls in phase_5_implement."""
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


def _patch_git_diff(monkeypatch, paths: list[str]) -> None:
    """Stub _derive_red_paths_via_git_diff to return given paths."""
    monkeypatch.setattr(
        _p5,
        "_derive_red_paths_via_git_diff",
        lambda *a, **kw: paths,
    )


def _spec_with_content(tmp_path: Path, content: str, name: str = "spec.md") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _real(p: Path) -> Path:
    """§1j: realpath-normalise a tmp_path before using it as a git/subprocess cwd."""
    return Path(os.path.realpath(str(p)))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


def _init_repo(cwd: Path) -> str:
    """Real, isolated git repo (one commit). Returns the initial commit SHA.

    Gate v1 MINOR-8: AC1/AC2 previously passed no git_cwd, so the test_only
    boundary resolution (GH1245 §2.2) would resolve `git rev-parse HEAD`
    against Path.cwd() — the developer's live HAL checkout — instead of an
    isolated fixture repo.
    """
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "885aae33@example.com")
    _git(cwd, "config", "user.name", "885AAE33 Test")
    (cwd / "README.md").write_text("init\n", encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", "init")
    res = _git(cwd, "rev-parse", "HEAD")
    assert res.returncode == 0, f"git rev-parse HEAD failed: {res.stderr}"
    return res.stdout.strip()


# ─── AC1: marker on line 1 → skip + correct data ──────────────────────────────


def test_skip_when_marker_on_line_1(tmp_path, monkeypatch):
    """AC1: marker on line 1 → status=ok, red_test_paths=[], red_commit_sha=40-hex
    pre-RED SHA (GH1245) equal to the isolated fixture repo's real HEAD."""
    spec = _spec_with_content(
        tmp_path,
        "<!-- engine-mode: test_only -->\n# My spec\n## Details\n",
    )
    _patch_emit(monkeypatch)

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    expected_sha = _init_repo(repo)

    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok' on test_only skip, got {result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    assert result.data is not None, "Expected result.data to be a dict, got None"
    assert result.data.get("red_test_paths") == [], (
        f"Expected red_test_paths=[], got {result.data.get('red_test_paths')!r}"
    )
    _sha = result.data.get("red_commit_sha")
    assert isinstance(_sha, str) and len(_sha) == 40 and all(c in "0123456789abcdef" for c in _sha), (
        f"Expected a 40-hex pre-RED red_commit_sha (GH1245), got {_sha!r}"
    )
    assert _sha == expected_sha, (
        f"Expected red_commit_sha to equal the isolated fixture repo's real "
        f"HEAD {expected_sha!r}, got {_sha!r}"
    )
    assert result.data.get("commit_red_tests_skipped") == "test_only_mode", (
        f"Expected commit_red_tests_skipped='test_only_mode', "
        f"got {result.data.get('commit_red_tests_skipped')!r}"
    )


# ─── AC2: marker on line 2 (line 1 BOM/blank) → skip fires ───────────────────


def test_skip_when_marker_on_line_2(tmp_path, monkeypatch):
    """AC2: marker on line 2 (line 1 blank) → skip still fires, and the
    returned SHA equals the isolated fixture repo's real HEAD."""
    spec = _spec_with_content(
        tmp_path,
        "\n<!-- engine-mode: test_only -->\n# My spec\n",
    )
    _patch_emit(monkeypatch)

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    expected_sha = _init_repo(repo)

    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"AC2: Expected status='ok' when marker on line 2, got {result.status!r}"
    )
    assert result.data is not None
    assert result.data.get("red_test_paths") == []
    _sha2 = result.data.get("red_commit_sha")
    assert isinstance(_sha2, str) and len(_sha2) == 40 and all(c in "0123456789abcdef" for c in _sha2), (
        f"Expected a 40-hex pre-RED red_commit_sha (GH1245), got {_sha2!r}"
    )
    assert _sha2 == expected_sha, (
        f"Expected red_commit_sha to equal the isolated fixture repo's real "
        f"HEAD {expected_sha!r}, got {_sha2!r}"
    )
    assert result.data.get("commit_red_tests_skipped") == "test_only_mode"


# ─── AC3: marker on line 3+ → IGNORED, existing behavior runs ─────────────────


def test_marker_on_line_3_is_ignored(tmp_path, monkeypatch):
    """AC3: marker on line 3+ is ignored; existing E_RED_NO_MARKER path runs."""
    spec = _spec_with_content(
        tmp_path,
        "# Title\n## Section\n<!-- engine-mode: test_only -->\nsome body\n",
    )
    # git diff returns empty → should hit E_RED_NO_MARKER (not skip)
    _patch_git_diff(monkeypatch, [])
    _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)
    prev = _make_prev(spec_path=str(spec))

    result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"AC3: marker on line 3 must be ignored; expected error, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"AC3: Expected E_RED_NO_MARKER, got {getattr(result, 'error_code', None)!r}"
    )


# ─── AC4: skip emits commit_red_tests_skipped event exactly once ──────────────


def test_skip_emits_event_once(tmp_path, monkeypatch):
    """AC4: skip emits commit_red_tests_skipped exactly once with correct payload."""
    spec = _spec_with_content(
        tmp_path,
        "<!-- engine-mode: test_only -->\n# spec\n",
    )
    captured = _patch_emit(monkeypatch)

    # Gate v2 MINOR-10: pin to an isolated tmp_path git repo — the test_only
    # path resolves `git rev-parse HEAD`, so without an isolated git_cwd this
    # would reach the developer's live HAL checkout.
    repo = _real(tmp_path / "repo")
    repo.mkdir()
    _init_repo(repo)

    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    _commit_red_tests(ctx, prev)

    skip_events = [e for e in captured if e["type"] == "commit_red_tests_skipped"]
    assert len(skip_events) == 1, (
        f"AC4: Expected exactly 1 commit_red_tests_skipped event, "
        f"got {len(skip_events)}. All events: {[e['type'] for e in captured]}"
    )
    payload = skip_events[0]["payload"]
    assert payload == {"phase": 5, "step": "commit_red_tests", "reason": "test_only_mode"}, (
        f"AC4: Wrong event payload: {payload!r}"
    )


# ─── AC5: skip does NOT mutate prev.data ──────────────────────────────────────


def test_skip_does_not_mutate_prev_data(tmp_path, monkeypatch):
    """AC5: returned data dict is fresh; prev.data is not mutated."""
    spec = _spec_with_content(
        tmp_path,
        "<!-- engine-mode: test_only -->\n# spec\n",
    )
    _patch_emit(monkeypatch)

    # Gate v2 MINOR-10: pin to an isolated tmp_path git repo — the test_only
    # path resolves `git rev-parse HEAD`, so without an isolated git_cwd this
    # would reach the developer's live HAL checkout.
    repo = _real(tmp_path / "repo")
    repo.mkdir()
    _init_repo(repo)

    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    # Snapshot prev.data keys before
    prev_data_before = dict(prev.data)

    result = _commit_red_tests(ctx, prev)

    assert prev.data == prev_data_before, (
        f"AC5: prev.data was mutated. Before: {prev_data_before!r}, after: {prev.data!r}"
    )
    assert result.data is not prev.data, (
        "AC5: result.data must be a fresh dict, not the same object as prev.data"
    )
    # New keys present in result.data but NOT injected into prev.data
    assert "commit_red_tests_skipped" not in prev_data_before or (
        prev_data_before.get("commit_red_tests_skipped")
        == prev.data.get("commit_red_tests_skipped")
    ), "AC5: prev.data was mutated (commit_red_tests_skipped added)"


# ─── AC6: spec_path missing/None/not-str → falls through ─────────────────────


@pytest.mark.parametrize("spec_path_val", [None, 42, [], ""])
def test_missing_spec_path_falls_through(tmp_path, monkeypatch, spec_path_val):
    """AC6: when spec_path is missing/None/non-string, skip does not fire."""
    _patch_git_diff(monkeypatch, [])
    _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)

    if spec_path_val is None:
        # No spec_path key at all
        prev = _make_prev()
    else:
        prev = _make_prev(spec_path=spec_path_val)  # type: ignore[arg-type]
        # Force non-string into data directly (bypass _make_prev str guard)
        prev.data["spec_path"] = spec_path_val

    result = _commit_red_tests(ctx, prev)

    # Must NOT produce a skip (commit_red_tests_skipped sentinel absent)
    assert result.data is None or result.data.get("commit_red_tests_skipped") is None, (
        f"AC6: spec_path={spec_path_val!r} must not trigger skip, "
        f"got commit_red_tests_skipped={result.data.get('commit_red_tests_skipped') if result.data else 'N/A'!r}"
    )
    # Falls through to existing error path (E_RED_NO_MARKER on empty git diff)
    assert result.status == "error", (
        f"AC6: expected fallthrough to error path, got status={result.status!r}"
    )


# ─── AC7: spec_path unreadable (OSError) → falls through ─────────────────────


def test_unreadable_spec_falls_through(tmp_path, monkeypatch):
    """AC7: OSError reading spec → _read_engine_mode returns None → fallthrough."""
    _patch_git_diff(monkeypatch, [])
    _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)
    prev = _make_prev(spec_path="/nonexistent/path/to/spec_885AAE33.md")

    result = _commit_red_tests(ctx, prev)

    # Must not have skipped
    assert result.data is None or result.data.get("commit_red_tests_skipped") is None, (
        "AC7: unreadable spec must not trigger skip"
    )
    # Falls through to E_RED_NO_MARKER
    assert result.status == "error"
    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"AC7: Expected E_RED_NO_MARKER fallthrough, got {getattr(result, 'error_code', None)!r}"
    )


# ─── AC8: unknown mode value → does NOT skip ──────────────────────────────────


def test_unknown_mode_value_does_not_skip(tmp_path, monkeypatch):
    """AC8: engine-mode: foo is not test_only → no skip, existing path runs."""
    spec = _spec_with_content(
        tmp_path,
        "<!-- engine-mode: foo -->\n# spec\n",
    )
    _patch_git_diff(monkeypatch, [])
    _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)
    prev = _make_prev(spec_path=str(spec))

    result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"AC8: unknown mode 'foo' must not skip; expected error, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"AC8: Expected E_RED_NO_MARKER, got {getattr(result, 'error_code', None)!r}"
    )
    assert result.data is None or result.data.get("commit_red_tests_skipped") is None


# ─── AC9: whitespace-strict rejection ─────────────────────────────────────────


@pytest.mark.parametrize("bad_marker", [
    "<!-- engine-mode:test_only -->",     # no space after colon
    "<!--engine-mode: test_only-->",      # no inner spaces around content
    " <!-- engine-mode: test_only -->",   # leading whitespace before <!--
])
def test_marker_whitespace_strict(tmp_path, monkeypatch, bad_marker):
    """AC9: whitespace variants of marker are rejected (must not trigger skip)."""
    content = f"{bad_marker}\n# spec\n"
    spec = _spec_with_content(tmp_path, content)
    _patch_git_diff(monkeypatch, [])
    _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)
    prev = _make_prev(spec_path=str(spec))

    result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"AC9: malformed marker {bad_marker!r} must not trigger skip; "
        f"expected error, got {result.status!r}"
    )
    assert result.data is None or result.data.get("commit_red_tests_skipped") is None, (
        f"AC9: malformed marker {bad_marker!r} must not set commit_red_tests_skipped"
    )


# ─── AC10: default path preserved — non-empty git_diff → commits ──────────────


def test_default_path_unchanged_when_diff_nonempty(tmp_path, monkeypatch):
    """AC10: no marker + git_diff non-empty → step commits + returns red_test_paths."""
    # No spec with marker (or no spec at all)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    fake_paths = ["tests/test_new_feature.py"]
    _patch_git_diff(monkeypatch, fake_paths)
    # Stub git ops so we don't need a real repo
    monkeypatch.setattr(
        "bytedigger_engine.workflows.phase_5_implement.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="abc123\n", stderr=""),
    )
    _patch_emit(monkeypatch)

    ctx = _make_ctx(scratchpad)
    prev = _make_prev()  # no spec_path → no marker check

    result = _commit_red_tests(ctx, prev)

    # Must not skip
    assert result.data is None or result.data.get("commit_red_tests_skipped") is None, (
        "AC10: no marker → must not skip"
    )
    # Should proceed (not E_RED_NO_MARKER, since git_diff non-empty)
    assert getattr(result, "error_code", None) != "E_RED_NO_MARKER", (
        f"AC10: non-empty git diff must not produce E_RED_NO_MARKER, "
        f"got error_code={getattr(result, 'error_code', None)!r}"
    )


# ─── AC11: default path preserved — empty git_diff → E_RED_NO_MARKER ──────────


def test_default_path_unchanged_when_diff_empty(tmp_path, monkeypatch):
    """AC11: no marker + empty git_diff → E_RED_NO_MARKER (unchanged behavior)."""
    _patch_git_diff(monkeypatch, [])
    _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad)
    prev = _make_prev()  # no spec_path

    result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"AC11: empty git diff must produce error, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"AC11: Expected E_RED_NO_MARKER, got {getattr(result, 'error_code', None)!r}"
    )


# ─── AC12: _read_engine_mode scans only first 2 lines ────────────────────────


def test_read_engine_mode_reads_only_first_2_lines(tmp_path, monkeypatch):
    """AC12: _read_engine_mode scans at most 2 lines; marker on line 3 → None."""
    # Function-scope import so collection of other 11 tests still works
    # even though _read_engine_mode doesn't exist yet (import fails at test time,
    # not at module collection time).
    try:
        from bytedigger_engine.workflows.phase_5_implement import _read_engine_mode  # noqa: PLC0415
    except ImportError:
        pytest.fail(
            "_read_engine_mode not found in phase_5_implement — "
            "GREEN must implement this helper (AC12)"
        )

    # 1 MB string: first 2 lines are blank, marker on line 3
    big_content = "\n\n" + "<!-- engine-mode: test_only -->\n" + ("x\n" * 200_000)

    spec = tmp_path / "bigspec.md"
    spec.write_text(big_content, encoding="utf-8")

    result = _read_engine_mode(str(spec))

    assert result is None, (
        f"AC12: marker on line 3 in 1 MB spec must NOT be detected "
        f"(only first 2 lines scanned), but _read_engine_mode returned {result!r}"
    )

    # Bonus: marker on line 2 IS detected (verifies correct slice)
    big_content_line2 = "\n<!-- engine-mode: test_only -->\n" + ("x\n" * 200_000)
    spec2 = tmp_path / "bigspec2.md"
    spec2.write_text(big_content_line2, encoding="utf-8")

    result2 = _read_engine_mode(str(spec2))
    assert result2 == "test_only", (
        f"AC12: marker on line 2 in 1 MB spec MUST be detected, got {result2!r}"
    )
