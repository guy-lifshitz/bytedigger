"""RED tests for HAL agreement D02C615D — `_autoprefix_bare_citations`.

Adds `_autoprefix_bare_citations(text: str, repo_root: pathlib.Path) -> str`
to `phase_45_spec.py` and calls it inside `_write_spec_doc` BEFORE
`_atomic_write`.

Function detects bare-filename citations like `phase_45_spec.py:596` and
rewrites them to repo-rooted paths like
`SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py:596` — but ONLY
when the basename is UNIQUE in `git ls-files`. If the basename appears 0
or 2+ times, the citation is left untouched.

These tests MUST fail today — `_autoprefix_bare_citations` does not yet
exist. GREEN phase implements it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _has_git() -> bool:
    return shutil.which("git") is not None


def _git_init_repo(root: Path) -> None:
    """Initialise a bare git repo at `root` so git ls-files works."""
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        env={**__import__("os").environ, "GIT_AUTHOR_NAME": "test",
             "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "test",
             "GIT_COMMITTER_EMAIL": "t@t"},
    )


def _add_file(root: Path, rel: str, content: str = "x\n") -> Path:
    """Create a file inside the repo and stage it."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", rel], cwd=root, check=True, capture_output=True)
    return p


def make_ctx(scratchpad: Path, *, git_cwd: str | None = None, **org_extra) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad), **org_extra}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev_with_response(
    doc_path: Path, raw_response: str, *, cycle: int = 1
) -> StepResult:
    """Build a StepResult shaped like the Opus writer's output (pre-_write_spec_doc)."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="call_opus_writer",
    )


# ─── unit tests on _autoprefix_bare_citations ─────────────────────────────────


def test_import_autoprefix():
    """_autoprefix_bare_citations must be importable from phase_45_spec.

    Today: ImportError → RED.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations  # noqa: F401


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_unique_match_is_rewritten(tmp_path):
    """Unique basename → bare citation rewritten to repo-rooted path.

    stage a repo with SYSTEM/foo/bar.py; input "see bar.py:42 for details";
    expect "see SYSTEM/foo/bar.py:42 for details".
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo/bar.py")

    text = "see bar.py:42 for details"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "see SYSTEM/foo/bar.py:42 for details", (
        f"expected bare citation rewritten; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_multiple_matches_left_alone(tmp_path):
    """Ambiguous basename (2+ files with same name) → citation unchanged.

    stage a/foo.py AND b/foo.py; "foo.py:1" must stay "foo.py:1".
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "a/foo.py")
    _add_file(tmp_path, "b/foo.py")

    text = "foo.py:1"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "foo.py:1", (
        f"ambiguous basename must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_zero_matches_left_alone(tmp_path):
    """Basename not in repo → citation unchanged (do not guess).

    stage repo with no unknown.py; "unknown.py:5" → unchanged.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    # no unknown.py staged

    text = "unknown.py:5"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "unknown.py:5", (
        f"absent file must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_already_prefixed_left_alone(tmp_path):
    """Already-rooted citation must not be double-prefixed.

    stage SYSTEM/foo/bar.py; input "see SYSTEM/foo/bar.py:10";
    expect unchanged (no SYSTEM/SYSTEM/... churn).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo/bar.py")

    text = "see SYSTEM/foo/bar.py:10"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "see SYSTEM/foo/bar.py:10", (
        f"prefixed citation must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_mixed_paragraph_only_bare_rewritten(tmp_path):
    """Mixed text: bare citation gets prefixed, already-prefixed citation untouched."""
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo/bar.py")
    _add_file(tmp_path, "SYSTEM/x/y.py")

    text = "see bar.py:1 and also SYSTEM/x/y.py:2 for context"
    result = _autoprefix_bare_citations(text, tmp_path)

    assert "SYSTEM/foo/bar.py:1" in result, f"bare citation should be prefixed; got {result!r}"
    assert "SYSTEM/x/y.py:2" in result, f"prefixed citation should survive; got {result!r}"
    # no double-prefix
    assert "SYSTEM/SYSTEM/" not in result, f"double-prefix must not occur; got {result!r}"


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_non_target_extension_left_alone(tmp_path):
    """Extension not in the citation set (ts/py/md/sh/tsx/js/yml) → unchanged.

    "foo.txt:5" is not a citation target; leave it alone even if foo.txt exists.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "docs/foo.txt")

    text = "foo.txt:5"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "foo.txt:5", (
        f".txt is not in citation extension set; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_inside_code_fence_still_rewritten(tmp_path):
    """Citations inside code fences are still rewritten.

    Current regex matches anywhere in text (not context-aware). This test
    documents that choice: lint behaviour stays consistent regardless of
    markdown context. Change this test if context-aware exclusion is added.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo/bar.py")

    text = "```\nsee bar.py:7\n```"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert "SYSTEM/foo/bar.py:7" in result, (
        f"citation inside code fence should still be rewritten; got {result!r}"
    )


def test_empty_text_returns_empty():
    """Empty input string → empty output, no error."""
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    result = _autoprefix_bare_citations("", Path("/nonexistent/path"))
    assert result == "", f"empty text should return empty; got {result!r}"


def test_git_ls_files_fails_graceful_degradation(tmp_path):
    """If git ls-files fails (not a repo) → return text unchanged, no exception."""
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    # tmp_path is NOT a git repo
    text = "bar.py:42"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "bar.py:42", (
        f"graceful degradation: should return text unchanged; got {result!r}"
    )


# ─── integration tests on _write_spec_doc call site ───────────────────────────


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_write_spec_doc_prefixes_before_atomic_write(tmp_path):
    """End-to-end: _write_spec_doc must run autoprefix before writing to disk.

    Stage a repo with SYSTEM/foo.py; raw_response contains "foo.py:1";
    file on disk must contain "SYSTEM/foo.py:1".
    """
    from bytedigger_engine.workflows.phase_45_spec import _write_spec_doc

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo.py")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_response = "## Context\nSee foo.py:1 for the main entry point.\n"
    ctx = make_ctx(scratchpad, git_cwd=str(tmp_path))
    prev = make_prev_with_response(doc_path, raw_response, cycle=1)

    result = _write_spec_doc(ctx, prev)

    assert result.status == "ok", f"_write_spec_doc failed: {result.error}"
    written_text = Path(result.data["spec_path"]).read_text(encoding="utf-8")
    assert "SYSTEM/foo.py:1" in written_text, (
        f"autoprefix must happen before write; got:\n{written_text}"
    )
    assert "foo.py:1" not in written_text.replace("SYSTEM/foo.py:1", ""), (
        "bare citation must not remain after prefixing"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_write_spec_doc_idempotent(tmp_path):
    """Running _write_spec_doc twice with same input produces byte-identical output.

    No churn from re-rewriting an already-prefixed citation.
    """
    from bytedigger_engine.workflows.phase_45_spec import _write_spec_doc

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo.py")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_response = "## Context\nSee foo.py:1 for details.\n"
    ctx = make_ctx(scratchpad, git_cwd=str(tmp_path))

    # First run
    prev1 = make_prev_with_response(doc_path, raw_response, cycle=1)
    result1 = _write_spec_doc(ctx, prev1)
    assert result1.status == "ok"
    content1 = Path(result1.data["spec_path"]).read_bytes()

    # Second run with same raw_response
    prev2 = make_prev_with_response(doc_path, raw_response, cycle=1)
    result2 = _write_spec_doc(ctx, prev2)
    assert result2.status == "ok"
    content2 = Path(result2.data["spec_path"]).read_bytes()

    assert content1 == content2, (
        "second run must produce byte-identical output (idempotent)"
    )


# ─── Opus gate additional edge-case tests (RED, D02C615D) ─────────────────────


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_partial_path_unique_is_rewritten_no_double_prefix(tmp_path):
    """Partial-path citation with UNIQUE suffix MUST be rewritten (post-DA5330E9 contract).
    Defends against double-prefix bug: SYSTEM/cli/build/engine_py/cli/build/engine_py/foo.py:5.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/cli/build/engine_py/foo.py")

    text = "see engine_py/foo.py:5 for details"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert "SYSTEM/cli/build/engine_py/foo.py:5" in result, (
        f"unique 2-seg suffix must be rewritten; got {result!r}"
    )
    assert "SYSTEM/cli/build/engine_py/cli/build/engine_py/foo.py:5" not in result, (
        f"double-prefix bug must not occur; got {result!r}"
    )
    assert "SYSTEM/SYSTEM/" not in result, (
        f"no SYSTEM/SYSTEM/ accidental concatenation; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
@pytest.mark.parametrize("rel_path,citation,expected", [
    ("SYSTEM/specs/build-spec.md", "build-spec.md:10", "SYSTEM/specs/build-spec.md:10"),
    ("SYSTEM/x/foo.bar.py",        "foo.bar.py:3",     "SYSTEM/x/foo.bar.py:3"),
])
def test_multi_dot_filename_rewritten(tmp_path, rel_path, citation, expected):
    """Basenames with multiple dots are correctly matched and rewritten.

    Ensures the regex handles filenames like `build-spec.md` and `foo.bar.py`
    whose basename contains more than one dot.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, rel_path)

    result = _autoprefix_bare_citations(citation, tmp_path)
    assert result == expected, (
        f"multi-dot filename {citation!r} should rewrite to {expected!r}; got {result!r}"
    )


def test_git_subprocess_timeout_graceful(monkeypatch):
    """TimeoutExpired from subprocess.run → input returned unchanged, no exception.

    Patches subprocess.run as imported inside phase_45_spec to raise
    TimeoutExpired, simulating a slow git invocation.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations  # RED: ImportError today

    from bytedigger_engine.workflows import phase_45_spec as _phase_mod

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "git", timeout=5)

    monkeypatch.setattr(_phase_mod.subprocess, "run", _raise_timeout)

    text = "foo.py:1"
    result = _autoprefix_bare_citations(text, Path("/any/path"))
    assert result == text, (
        f"TimeoutExpired must degrade gracefully and return input unchanged; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_git_ls_files_called_once_per_call(monkeypatch, tmp_path):
    """subprocess.run is called exactly once per _autoprefix_bare_citations call.

    Pins the anti-N+1 contract: the filename→path map must be built in a
    single git ls-files invocation, not once per citation.
    Input has 3 distinct bare citations; subprocess.run must fire only once.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations  # RED: ImportError today

    # Provide a fake git ls-files response covering all 3 basenames
    fake_stdout = "src/a.py\nlib/b.py\nutils/c.py\n"

    call_count = {"n": 0}

    def _fake_run(*args, **kwargs):
        call_count["n"] += 1
        return types.SimpleNamespace(stdout=fake_stdout, returncode=0, stderr="")

    from bytedigger_engine.workflows import phase_45_spec as _phase_mod
    monkeypatch.setattr(_phase_mod.subprocess, "run", _fake_run)

    text = "a.py:1 and b.py:2 and c.py:3"
    _autoprefix_bare_citations(text, tmp_path)

    assert call_count["n"] == 1, (
        f"subprocess.run must be called exactly once per invocation; "
        f"called {call_count['n']} time(s)"
    )


def test_no_citation_tokens_skips_git_subprocess(monkeypatch, tmp_path):
    """6DBB248D F3 (DA5330E9 contract update) — when text contains zero
    citation-shaped tokens (no `<path>.<ext>:<digits>` anywhere), the
    autoprefix must NOT spawn a git ls-files subprocess.

    Pre-DA5330E9 framing was "no bare citations" with a rooted-path probe; under
    DA5330E9 the regex captures both bare AND rooted paths (the `full == key`
    guard short-circuits the rewrite — but the subprocess has already fired by
    then). To keep the early-return optimisation meaningfully tested, the input
    must contain no path tokens at all.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    call_count = {"n": 0}

    def _fake_run(*args, **kwargs):
        call_count["n"] += 1
        return types.SimpleNamespace(stdout="", returncode=0)

    from bytedigger_engine.workflows import phase_45_spec as _phase_mod
    monkeypatch.setattr(_phase_mod.subprocess, "run", _fake_run)

    # Pure prose: no `<path>.<ext>:<digits>` patterns anywhere.
    text = "Pure prose paragraph with no citations at all. Just narrative text here."
    result = _autoprefix_bare_citations(text, tmp_path)

    assert result == text, "early-return must leave text untouched"
    assert call_count["n"] == 0, (
        f"git ls-files must NOT run when no citation tokens are present; "
        f"called {call_count['n']} time(s)"
    )
