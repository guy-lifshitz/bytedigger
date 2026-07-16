"""RED tests for GH804 — manifest consumer-side holes (deref crash + uncaught malformed).

Spec: SHARED/memory/Decisions/2026-07-14_GH804_manifest_consumer_holes_spec.md
Covers AC1-AC10 (§3).

Do NOT mock the units under test (`prev_data_corruption_reason`, `_ManifestError`,
`_commit_green_code`, `_commit_fix_code`, `_commit_fix_tests`, `_run_pytest_post_fix`,
`manifest_from_result` except the narrow AC9 resume-path spy assertion).

Not-yet-existing symbols (`prev_data_corruption_reason`, `_ManifestError`) are
imported INSIDE each test function body — never at module top level — so this
file stays collectable even before GREEN adds them (§1q extension / D1CF5FDF).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import pytest

from contracts import WorkflowContext


# ─── helpers ──────────────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _make_repo_with_red_commit(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED: add failing test")
    return repo, red_sha


def _make_ctx(scratchpad: Path | None = None, git_cwd: str | None = None, **org_extra) -> WorkflowContext:
    org: dict = {**org_extra}
    if scratchpad is not None:
        org["scratchpad_dir"] = str(scratchpad)
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


class _FakePrev:
    """Minimal prev StepResult-like object exposing only `.data`."""

    def __init__(self, data):
        self.data = data


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_prev_data_corruption_reason_flags_non_dict_ok_for_dict_and_none():
    from llm_subprocess import prev_data_corruption_reason

    for bad in ("s", ["l"], 3, 3.0, True):
        reason = prev_data_corruption_reason(_FakePrev(bad))
        assert reason is not None and isinstance(reason, str), (
            f"expected non-None reason for prev.data={bad!r}, got {reason!r}"
        )

    for good in ({}, {"k": 1}, None):
        assert prev_data_corruption_reason(_FakePrev(good)) is None


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_manifest_error_is_common_ancestor_and_all_subtypes_are_valueerror():
    from llm_subprocess import (
        _ManifestError,
        _ManifestMissingError,
        _ManifestMalformedError,
        _ManifestInvalidSourceError,
    )

    assert issubclass(_ManifestMissingError, _ManifestError)
    assert issubclass(_ManifestMalformedError, _ManifestError)
    assert issubclass(_ManifestInvalidSourceError, _ManifestError)
    assert issubclass(_ManifestError, ValueError)
    assert issubclass(_ManifestMissingError, ValueError)
    assert issubclass(_ManifestMalformedError, ValueError)
    assert issubclass(_ManifestInvalidSourceError, ValueError)


# ─── AC3 — phase_5 _commit_green_code non-dict deref crash ────────────────────


def test_ac3_commit_green_code_nondict_data_returns_terminal_error_no_crash():
    from phase_5_implement import _commit_green_code

    ctx = _make_ctx()
    prev = _FakePrev("corrupt")

    try:
        result = _commit_green_code(ctx, prev)
    except (AttributeError, TypeError) as exc:
        pytest.fail(f"_commit_green_code raised {type(exc).__name__} on non-dict prev.data: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"
    assert result.recoverable is False


# ─── AC4 — phase_6 _commit_fix_code non-dict deref crash (incl. spread) ───────


def test_ac4_commit_fix_code_nondict_data_returns_terminal_error_no_crash():
    from phase_6_review import _commit_fix_code

    ctx = _make_ctx()
    prev = _FakePrev("corrupt")

    try:
        result = _commit_fix_code(ctx, prev)
    except (AttributeError, TypeError) as exc:
        pytest.fail(f"_commit_fix_code raised {type(exc).__name__} on non-dict prev.data: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"
    assert result.recoverable is False


# ─── AC5 — phase_6 _commit_fix_tests non-dict deref crash ─────────────────────


def test_ac5_commit_fix_tests_nondict_data_returns_terminal_error_no_crash():
    from phase_6_review import _commit_fix_tests

    ctx = _make_ctx()
    prev = _FakePrev("corrupt")

    try:
        result = _commit_fix_tests(ctx, prev)
    except (AttributeError, TypeError) as exc:
        pytest.fail(f"_commit_fix_tests raised {type(exc).__name__} on non-dict prev.data: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"
    assert result.recoverable is False


# ─── AC6 — phase_6 _run_pytest_post_fix non-dict deref crash ──────────────────


def test_ac6_run_pytest_post_fix_nondict_data_returns_terminal_error_no_crash():
    from phase_6_review import _run_pytest_post_fix

    ctx = _make_ctx()
    prev = _FakePrev("corrupt")

    try:
        result = _run_pytest_post_fix(ctx, prev)
    except (AttributeError, TypeError) as exc:
        pytest.fail(f"_run_pytest_post_fix raised {type(exc).__name__} on non-dict prev.data: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"
    assert result.recoverable is False


# ─── AC7 — malformed manifest (_ManifestMalformedError) uncaught today ────────


def test_ac7_commit_green_code_malformed_manifest_returns_terminal_error(tmp_path):
    from phase_5_implement import _commit_green_code

    repo, red_sha = _make_repo_with_red_commit(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _FakePrev({
        "cycle": 1,
        "red_commit_sha": red_sha,
        "worker_written_paths": "not-a-list",  # malformed → _ManifestMalformedError
        "manifest_source": "harness_tool_record",
    })

    from llm_subprocess import _ManifestMalformedError

    try:
        result = _commit_green_code(ctx, prev)
    except _ManifestMalformedError as exc:
        pytest.fail(f"_commit_green_code let _ManifestMalformedError escape uncaught: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"


def test_ac7_commit_fix_code_malformed_manifest_returns_terminal_error(tmp_path):
    from phase_6_review import _commit_fix_code

    repo, red_sha = _make_repo_with_red_commit(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _FakePrev({
        "cycle": 1,
        "pre_fix_sha": red_sha,
        "worker_written_paths": "not-a-list",  # malformed → _ManifestMalformedError
        "manifest_source": "harness_tool_record",
    })

    from llm_subprocess import _ManifestMalformedError

    try:
        result = _commit_fix_code(ctx, prev)
    except _ManifestMalformedError as exc:
        pytest.fail(f"_commit_fix_code let _ManifestMalformedError escape uncaught: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"


# ─── AC8 — invalid manifest_source (_ManifestInvalidSourceError) uncaught today


def test_ac8_commit_green_code_invalid_source_returns_terminal_error(tmp_path):
    from phase_5_implement import _commit_green_code

    repo, red_sha = _make_repo_with_red_commit(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _FakePrev({
        "cycle": 1,
        "red_commit_sha": red_sha,
        "worker_written_paths": ["src/module.py"],
        "manifest_source": "bogus_enum_value",  # invalid → _ManifestInvalidSourceError
    })

    from llm_subprocess import _ManifestInvalidSourceError

    try:
        result = _commit_green_code(ctx, prev)
    except _ManifestInvalidSourceError as exc:
        pytest.fail(f"_commit_green_code let _ManifestInvalidSourceError escape uncaught: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"


def test_ac8_commit_fix_code_invalid_source_returns_terminal_error(tmp_path):
    from phase_6_review import _commit_fix_code

    repo, red_sha = _make_repo_with_red_commit(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _FakePrev({
        "cycle": 1,
        "pre_fix_sha": red_sha,
        "worker_written_paths": ["src/module.py"],
        "manifest_source": "bogus_enum_value",  # invalid → _ManifestInvalidSourceError
    })

    from llm_subprocess import _ManifestInvalidSourceError

    try:
        result = _commit_fix_code(ctx, prev)
    except _ManifestInvalidSourceError as exc:
        pytest.fail(f"_commit_fix_code let _ManifestInvalidSourceError escape uncaught: {exc}")

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"


# ─── AC9 (§1ab) — resume path unchanged for VALID dict prev.data ──────────────


def test_ac9_commit_green_code_resume_path_valid_dict_does_not_call_manifest_from_result(
    tmp_path, monkeypatch
):
    from phase_5_implement import _commit_green_code
    import phase_5_implement

    repo, red_sha = _make_repo_with_red_commit(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    calls = {"n": 0}

    def _spy_manifest_from_result(prev):
        calls["n"] += 1
        raise AssertionError("manifest_from_result must NOT be called on the resume path")

    monkeypatch.setattr(phase_5_implement, "manifest_from_result", _spy_manifest_from_result)

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _FakePrev({
        "cycle": 1,
        "red_commit_sha": red_sha,
        "green_complete_resume": True,
        "green_resume_paths": ["src/placeholder.py"],
    })

    result = _commit_green_code(ctx, prev)

    assert calls["n"] == 0, "manifest_from_result was called on the resume path"
    assert result.status != "error", f"resume path must not error, got: {getattr(result, 'error', None)}"


# ─── AC10 (§1ab) — resume path guard fires before deref on non-dict data ──────


def test_ac10_commit_green_code_resume_path_nondict_data_guard_fires_first():
    from phase_5_implement import _commit_green_code

    ctx = _make_ctx()
    prev = _FakePrev("corrupt")

    try:
        result = _commit_green_code(ctx, prev)
    except (AttributeError, TypeError) as exc:
        pytest.fail(
            f"_commit_green_code raised {type(exc).__name__} on non-dict prev.data "
            f"before the resume-branch guard could fire: {exc}"
        )

    assert result.status == "error"
    assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER"


def test_ac10_prev_data_corruption_reason_is_pure_and_replay_idempotent():
    from llm_subprocess import prev_data_corruption_reason

    prev = _FakePrev("corrupt")
    first = prev_data_corruption_reason(prev)
    second = prev_data_corruption_reason(prev)
    assert first == second
    assert first is not None
