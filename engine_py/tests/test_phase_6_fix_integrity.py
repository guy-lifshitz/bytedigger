"""Tests for phase_6_fix_integrity workflow — FIX-side mirror of phase_5_integrity.

Agreement 1C6AFE11. Three steps. Diff boundary: pre_fix_sha..fix_commit_sha (two
committed SHAs, not working-tree). Short-circuit on empty diff or equal SHAs.
Hard gate on ASSERTION_GAMING / missing marker — same cautious semantics as p5i.

25e75663 migration (R2+R4): shell-stub argv replaced by register_backend spy;
fix_integrity_llm_command= key replaced by fix_integrity_model= string.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext, StepResult  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from llm_subprocess import register_backend, reset_backends  # noqa: E402
from phase_6_fix_integrity import (  # noqa: E402
    DEFAULT_INTEGRITY_TIMEOUT_SEC,
    DEFAULT_LLM_COMMAND,
    DIFF_PATCH_RELPATH,
    PRE_FIX_REF_RELPATH,
    FIX_COMMIT_SHA_RELPATH,
    REVIEW_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    phase_6_fix_integrity_workflow,
)


# ─── stub backend (R2: replaces echo_stub/fail_stub shell subprocesses) ────────

_STUB_MODEL = "fix-integrity-stub"


class _TextBackend:
    """Returns a canned raw_response text directly (no subprocess)."""

    def __init__(self, response_text: str) -> None:
        self._text = response_text

    def __call__(self, **kw) -> StepResult:
        data: dict = {
            "raw_response": self._text,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_fix_integrity_llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


@pytest.fixture(autouse=True)
def _reset_backends_fi():
    """§1i: restore _BACKENDS singleton after every test."""
    yield
    reset_backends()


def _register_text_stub(text: str) -> None:
    """Register a text-returning stub as the claude-subprocess backend."""
    register_backend(
        "claude-subprocess",
        _TextBackend(text),
        manifest_source="harness_tool_record",
        overwrite=True,
    )


def make_ctx(scratchpad: Path, *, question: str = "Fix the bug", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-fix",
        persona="hal",
        framework=None,
        domain=None,
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> str:
    """Commit file and return the resulting SHA."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def make_engine_with_wf() -> WorkflowEngine:
    """Return a WorkflowEngine with phase_6_fix_integrity registered directly."""
    eng = WorkflowEngine()
    wf = phase_6_fix_integrity_workflow()
    eng.register("phase_6_fix_integrity", wf)
    return eng


def seed_repo_with_fix_test_diff(repo: Path):
    """Three commits: init, RED (add test), FIX (modify test — gaming).
    Returns (pre_fix_sha, fix_commit_sha).
    """
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    pre_sha = commit_file(
        repo,
        "tests/test_foo.py",
        "def test_foo():\n    assert foo() == 1\n",
        "build: red cycle",
    )
    fix_sha = commit_file(
        repo,
        "tests/test_foo.py",
        "def test_foo():\n    assert foo() == 2\n",  # assertion changed — gaming!
        "fix: gamed assertion",
    )
    return pre_sha, fix_sha


def seed_repo_no_test_diff(repo: Path):
    """Three commits: init, RED (add test), FIX (only src/ changed).
    Returns (pre_fix_sha, fix_commit_sha).
    """
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    pre_sha = commit_file(
        repo,
        "tests/test_foo.py",
        "def test_foo():\n    assert foo() == 1\n",
        "build: red cycle",
    )
    fix_sha = commit_file(
        repo,
        "src/foo.py",
        "def foo():\n    return 1\n",  # only src/ — no test change
        "fix: code only",
    )
    return pre_sha, fix_sha


# ─── AC1: workflow definition shape ──────────────────────────────────────────


def test_workflow_definition_shape():
    """AC1: factory returns WorkflowDefinition with correct name and 3 step names."""
    wf = phase_6_fix_integrity_workflow()
    assert wf.name == "phase_6_fix_integrity"
    assert [s.name for s in wf.steps] == [
        "build_fix_integrity_prompt",
        "invoke_fix_integrity_llm",
        "classify_fix_diff_verdict",
    ]


# ─── AC2: defaults are sensible ───────────────────────────────────────────────


def test_defaults_are_sensible():
    """AC2: DEFAULT_INTEGRITY_TIMEOUT_SEC==600; DEFAULT_LLM_COMMAND uses get_claude_critical."""
    assert DEFAULT_INTEGRITY_TIMEOUT_SEC == 600
    # DEFAULT_LLM_COMMAND must be the Opus-pinned command list via get_claude_critical().
    # Do NOT hardcode the model string; verify the structure and that it routes through
    # the critical model (whatever ModelConfig reports for 'critical').
    from model_config import get_claude_critical  # noqa: PLC0415
    critical = get_claude_critical()
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", critical]
    # Verify the constants reflect FIX-side doc paths (not reused from phase_5_integrity).
    assert DIFF_PATCH_RELPATH == "integrity/fix-test-diff.patch"
    assert PRE_FIX_REF_RELPATH == "integrity/pre-fix-ref.txt"
    assert FIX_COMMIT_SHA_RELPATH == "integrity/fix-commit-sha.txt"
    assert REVIEW_DOC_RELPATH == "reviews/build-fix-integrity-review.md"
    assert SPEC_DOC_RELPATH == "specs/build-spec.md"


# ─── AC3: two-SHA diff command assembled correctly ────────────────────────────


def test_two_sha_diff_command(tmp_path):
    """AC3: when both SHAs are in org_config, diff command is
    ['git','diff',<pre>,<post>,'--','*test*','*spec*','*.test.*'].
    """
    from phase_6_fix_integrity import _resolve_fix_diff_command  # noqa: PLC0415

    pre = "abc1234"
    post = "def5678"
    cfg = {"pre_fix_sha": pre, "fix_commit_sha": post}
    cmd = _resolve_fix_diff_command(cfg, scratchpad=None)
    assert cmd == ["git", "diff", pre, post, "--", "*test*", "*spec*", "*.test.*"]


# ─── AC4: NO_CHANGES short-circuit (empty diff) ──────────────────────────────


def test_empty_diff_no_changes_short_circuit(tmp_path):
    """AC4: when git diff exits 0 with empty stdout, step1 returns verdict_override=NO_CHANGES,
    step2 skips LLM (skipped=True), step3 returns verdict=NO_CHANGES, review doc NOT written.
    """
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            # LLM must NOT be called on NO_CHANGES path; no stub needed.
        ),
    )

    assert result.status == "ok"
    assert result.data["verdict"] == "NO_CHANGES"
    assert result.data.get("skipped") is True
    # Diff patch file is written (empty contents).
    patch_path = scratchpad / DIFF_PATCH_RELPATH
    assert patch_path.is_file()
    assert patch_path.read_text() == ""
    # Review doc must NOT be written on NO_CHANGES.
    assert not (scratchpad / REVIEW_DOC_RELPATH).exists()


# ─── AC5: NO_CHANGES short-circuit (equal SHAs) ──────────────────────────────


def test_equal_shas_no_changes_short_circuit(tmp_path):
    """AC5: when pre_fix_sha == fix_commit_sha, step1 returns NO_CHANGES WITHOUT
    invoking git diff. Steps 2 and 3 short-circuit identically.
    """
    repo = tmp_path / "repo"
    init_repo(repo)
    sha = commit_file(repo, "src/foo.py", "x\n", "init")
    scratchpad = tmp_path / "scratch"

    # Sentinel: if git diff is actually called with the two equal SHAs it will
    # produce empty output, so the end result is still NO_CHANGES. But we verify
    # no LLM was invoked (fail_stub would explode) AND the diff patch is NOT
    # written (since we short-circuit before even running git).
    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=sha,
            fix_commit_sha=sha,   # same SHA → equal SHAs; LLM never invoked
        ),
    )

    assert result.status == "ok"
    assert result.data["verdict"] == "NO_CHANGES"
    assert result.data.get("skipped") is True
    # Review doc must NOT be written.
    assert not (scratchpad / REVIEW_DOC_RELPATH).exists()
    # AC5 strict: equal-SHA short-circuit must skip git diff entirely.
    # If git diff WAS invoked it would write the (empty) patch file, so
    # the file's absence proves the short-circuit happened.
    patch_path = scratchpad / DIFF_PATCH_RELPATH
    assert not patch_path.exists(), \
        "git diff should be skipped on equal SHAs (no patch file should exist)"


# ─── AC6: ASSERTION_GAMING blocks ────────────────────────────────────────────


def test_assertion_gaming_blocks(tmp_path):
    """AC6: LLM emits VERDICT: ASSERTION_GAMING → error E_FIX_INTEGRITY_ASSERTION_GAMING,
    recoverable=False, review doc written.
    """
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_with_fix_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_text_stub("Caught it.\nVERDICT: ASSERTION_GAMING\n")
    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            fix_integrity_model=_STUB_MODEL,
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_FIX_INTEGRITY_ASSERTION_GAMING"
    assert result.recoverable is False
    assert result.data["verdict"] == "ASSERTION_GAMING"
    # Review doc must be written even on block.
    assert (scratchpad / REVIEW_DOC_RELPATH).is_file()


def test_assertion_gaming_case_insensitive_blocks(tmp_path):
    """AC6 (case-insensitive): lowercase/bold variant must still block."""
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_with_fix_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_text_stub("**verdict: assertion_gaming**\n")
    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            fix_integrity_model=_STUB_MODEL,
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_FIX_INTEGRITY_ASSERTION_GAMING"


# ─── AC7: SPEC_CHANGE / LEGITIMATE_REFACTOR pass ─────────────────────────────


def test_spec_change_passes(tmp_path):
    """AC7a: VERDICT: SPEC_CHANGE yields ok with verdict in data, review doc written."""
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_with_fix_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_text_stub("Analysis.\nVERDICT: SPEC_CHANGE\n")
    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            fix_integrity_model=_STUB_MODEL,
        ),
    )

    assert result.status == "ok"
    assert result.data["verdict"] == "SPEC_CHANGE"
    assert (scratchpad / REVIEW_DOC_RELPATH).is_file()


def test_legitimate_refactor_passes(tmp_path):
    """AC7b: VERDICT: LEGITIMATE_REFACTOR yields ok with verdict in data."""
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_with_fix_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_text_stub("VERDICT: LEGITIMATE_REFACTOR\n")
    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            fix_integrity_model=_STUB_MODEL,
        ),
    )

    assert result.status == "ok"
    assert result.data["verdict"] == "LEGITIMATE_REFACTOR"
    assert (scratchpad / REVIEW_DOC_RELPATH).is_file()


# ─── AC8: missing VERDICT marker blocks ──────────────────────────────────────


def test_missing_marker_blocks(tmp_path):
    """AC8: LLM emits text without any recognized VERDICT: prefix → E_FIX_INTEGRITY_NO_MARKER,
    recoverable=False. Bare keywords like 'GAMING' without the VERDICT: prefix don't count.
    """
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_with_fix_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_text_stub("I think this is GAMING but I forgot to write the verdict line.\n")
    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            fix_integrity_model=_STUB_MODEL,
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_FIX_INTEGRITY_NO_MARKER"
    assert result.recoverable is False


# ─── AC9: missing boundary blocks ─────────────────────────────────────────────


def test_missing_boundary_blocks(tmp_path, monkeypatch):
    """AC9: no SHA in org_config, no scratchpad file, git rev-parse HEAD fails →
    step1 returns error E_MISSING_FIX_BOUNDARY, recoverable=False.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True)

    # Point git_cwd at a non-git directory so rev-parse HEAD returns non-zero.
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()

    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(non_git_dir),
            # No pre_fix_sha, no fix_commit_sha — LLM never invoked on error path.
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_MISSING_FIX_BOUNDARY"
    assert result.recoverable is False


def test_missing_boundary_blocks_via_monkeypatch(tmp_path, monkeypatch):
    """AC9 variant: monkeypatch subprocess.run to simulate git rev-parse failure."""
    import subprocess as _sp  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True)

    original_run = _sp.run

    def patched_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and "rev-parse" in cmd:
            raise _sp.CalledProcessError(128, cmd, stderr="not a git repository")
        return original_run(cmd, *args, **kwargs)

    import phase_6_fix_integrity as _p6fi  # noqa: PLC0415
    monkeypatch.setattr(_p6fi.subprocess, "run", patched_run)

    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            # no pre_fix_sha, no fix_commit_sha — LLM never invoked on error path
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_MISSING_FIX_BOUNDARY"


# ─── AC10: test patterns filter applied ───────────────────────────────────────


def test_test_patterns_filter(tmp_path):
    """AC10: diff command always includes '-- *test* *spec* *.test.*' patterns.
    A fixture commit that modifies only src/foo.py (no test files) yields an
    empty diff → NO_CHANGES end-to-end, verified via WorkflowEngine.
    """
    repo = tmp_path / "repo"
    pre_sha, fix_sha = seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = make_engine_with_wf()
    result, _ = eng.execute(
        "phase_6_fix_integrity",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_fix_sha=pre_sha,
            fix_commit_sha=fix_sha,
            # LLM must not be reached on NO_CHANGES path; BURN-GUARD enforces.
        ),
    )

    assert result.status == "ok"
    assert result.data["verdict"] == "NO_CHANGES"
    # Patch file exists (written empty) — confirms step1 ran and filtered properly.
    patch_path = scratchpad / DIFF_PATCH_RELPATH
    assert patch_path.is_file()
    assert patch_path.read_text() == ""
    # verdict_override set in step1 data confirms the short-circuit path (not LLM).
    assert result.data.get("skipped") is True


def test_test_patterns_filter_default_excludes_src(tmp_path):
    """AC10 supplement: verify the assembled diff command explicitly includes
    the pattern segment so non-test paths are excluded by git itself.
    """
    from phase_6_fix_integrity import _resolve_fix_diff_command  # noqa: PLC0415

    cfg = {"pre_fix_sha": "aaa", "fix_commit_sha": "bbb"}
    cmd = _resolve_fix_diff_command(cfg, scratchpad=None)
    # Must include '--' separator followed by the default test patterns.
    assert "--" in cmd
    sep_idx = cmd.index("--")
    patterns = cmd[sep_idx + 1:]
    assert "*test*" in patterns
    assert "*spec*" in patterns
    assert "*.test.*" in patterns


# ─── registry: phase_6_fix_integrity must be registered after GREEN ships ─────


def test_registry_includes_phase_6_fix_integrity():
    """Verify that workflows.register_all will include phase_6_fix_integrity after GREEN."""
    import workflows  # noqa: PLC0415
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_6_fix_integrity" in eng.registered()
