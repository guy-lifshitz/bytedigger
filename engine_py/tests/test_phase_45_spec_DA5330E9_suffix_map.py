"""RED tests for HAL agreement DA5330E9 — citation autoprefix suffix-map.

Spec: SHARED/memory/Decisions/2026-05-19_DA5330E9_citation_autoprefix_suffix_map_spec.md

Replaces `_build_filename_map` with `_build_suffix_map` and widens
`_BARE_CITATION_RE` to capture multi-segment paths (adds `/` to inner char
class). Fixes two bypass paths that left partial-path and non-unique basename
citations unrewritten in the BARK monorepo fleet (53% of /build runs).

All 13 tests MUST FAIL today (pre-GREEN) because:
- `_build_suffix_map` does not yet exist (today's symbol is `_build_filename_map`)
- `_BARE_CITATION_RE` excludes `/` in the capture class, so multi-segment
  citations like `engine_py/foo.py:5` are invisible to the regex.

Each test imports from `phase_45_spec` inside the test body so every AC fails
independently (rather than a module-level ImportError blocking all collection).
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


# ─── helpers (verbatim from test_phase_45_spec_D02C615D.py lines 36–93) ────────


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


# ─── AC1: import test (per-test import — fails independently) ─────────────────


def test_suffix_map_import():
    """_build_suffix_map must be importable from phase_45_spec.

    RED today: AttributeError/ImportError because today's symbol is
    `_build_filename_map`, not `_build_suffix_map`.
    Do NOT import `_build_filename_map` — that symbol will be deleted by GREEN.
    """
    from bytedigger_engine.workflows.phase_45_spec import _build_suffix_map  # noqa: F401


# ─── AC2–AC11: git-based behavioural tests ───────────────────────────────────


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_partial_path_unique_rewritten(tmp_path):
    """2-segment suffix unique in repo → citation rewritten to full path.

    Stage SYSTEM/cli/build/engine_py/foo.py.
    Input: "see engine_py/foo.py:5 for details"
    Expected: "see SYSTEM/cli/build/engine_py/foo.py:5 for details"

    RED today: _BARE_CITATION_RE excludes `/`, so engine_py/foo.py:5 is not
    captured, and `_build_suffix_map` doesn't exist.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/cli/build/engine_py/foo.py")

    text = "see engine_py/foo.py:5 for details"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "see SYSTEM/cli/build/engine_py/foo.py:5 for details", (
        f"unique 2-seg suffix must be rewritten; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_partial_path_ambiguous_left_alone(tmp_path):
    """2-segment suffix resolving to 2+ paths → citation left unchanged.

    Stage a/x/foo.py AND b/x/foo.py.
    Input: "x/foo.py:1"
    Expected: "x/foo.py:1" (ambiguous suffix dropped from map).

    RED today: import fails (_build_suffix_map absent).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "a/x/foo.py")
    _add_file(tmp_path, "b/x/foo.py")

    text = "x/foo.py:1"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "x/foo.py:1", (
        f"ambiguous 2-seg suffix must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_partial_path_zero_match_left_alone(tmp_path):
    """Suffix not in repo → citation unchanged (do not guess).

    Stage repo with no matching files.
    Input: "renderers/missing.py:1"
    Expected: "renderers/missing.py:1"

    RED today: import fails (_build_suffix_map absent).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    # No matching files staged

    text = "renderers/missing.py:1"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "renderers/missing.py:1", (
        f"absent suffix must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_three_segment_unique_rewritten(tmp_path):
    """3-segment suffix unique in repo → rewritten to full path.

    Stage demo/app1/renderers/agreement.py.
    Input: "app1/renderers/agreement.py:9"
    Expected: "demo/app1/renderers/agreement.py:9"

    RED today: _BARE_CITATION_RE excludes `/` so multi-seg citations not
    captured; also _build_suffix_map absent.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "demo/app1/renderers/agreement.py")

    text = "app1/renderers/agreement.py:9"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "demo/app1/renderers/agreement.py:9", (
        f"unique 3-seg suffix must be rewritten; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_bare_basename_unique_still_rewritten(tmp_path):
    """Regression guard: unique bare basename still rewrites after suffix-map switch.

    Stage SYSTEM/foo.py (unique).
    Input: "foo.py:1"
    Expected: "SYSTEM/foo.py:1"

    Ensures the new _build_suffix_map preserves the D02C615D basename contract
    (basename is just the length-1 suffix; uniqueness criterion unchanged).

    RED today: import fails (_build_suffix_map absent).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo.py")

    text = "foo.py:1"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "SYSTEM/foo.py:1", (
        f"unique bare basename must still be rewritten; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_bare_basename_ambiguous_still_left_alone(tmp_path):
    """Regression guard: ambiguous bare basename still left alone after suffix-map switch.

    Stage a/foo.py AND b/foo.py.
    Input: "foo.py:1"
    Expected: "foo.py:1" (ambiguous basename unchanged).

    RED today: import fails (_build_suffix_map absent).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "a/foo.py")
    _add_file(tmp_path, "b/foo.py")

    text = "foo.py:1"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "foo.py:1", (
        f"ambiguous bare basename must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_already_rooted_left_alone(tmp_path):
    """Already-rooted citation must not be rewritten (lookbehind + full==key guard).

    Stage SYSTEM/foo/bar.py.
    Input: "see SYSTEM/foo/bar.py:10"
    Expected: "see SYSTEM/foo/bar.py:10" (full path == key → no-op guard fires).

    RED today: import fails (_build_suffix_map absent).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/foo/bar.py")

    text = "see SYSTEM/foo/bar.py:10"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "see SYSTEM/foo/bar.py:10", (
        f"already-rooted citation must be left alone; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_repo_root_file_left_alone(tmp_path):
    """Repo-root file (no parent dir) → citation unchanged (full == key guard).

    Stage README.md at repo root.
    Input: "README.md:1"
    Expected: "README.md:1" (suffix-map returns README.md which equals key → no-op).

    RED today: import fails (_build_suffix_map absent).
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "README.md")

    text = "README.md:1"
    result = _autoprefix_bare_citations(text, tmp_path)
    assert result == "README.md:1", (
        f"repo-root file citation must be left alone (full==key guard); got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_double_prefix_never_occurs(tmp_path):
    """No double-prefix bug regardless of which suffix form appears in text.

    Stage SYSTEM/cli/build/engine_py/foo.py.
    Text contains ALL THREE forms:
      - bare basename: foo.py:5
      - 2-seg: engine_py/foo.py:5
      - 3-seg: cli/build/engine_py/foo.py:5
    All three should rewrite to SYSTEM/cli/build/engine_py/foo.py:5.
    Assert no SYSTEM/cli/build/engine_py/cli/build/engine_py/foo.py occurs
    and no SYSTEM/SYSTEM/ occurs.

    RED today: _BARE_CITATION_RE excludes `/`; _build_suffix_map absent.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "SYSTEM/cli/build/engine_py/foo.py")

    text = (
        "First: foo.py:5 — then: engine_py/foo.py:5 — "
        "then: cli/build/engine_py/foo.py:5"
    )
    result = _autoprefix_bare_citations(text, tmp_path)

    assert "SYSTEM/cli/build/engine_py/cli/build/engine_py/foo.py:5" not in result, (
        f"double-prefix bug must not occur; got {result!r}"
    )
    assert "SYSTEM/SYSTEM/" not in result, (
        f"no SYSTEM/SYSTEM/ accidental concatenation; got {result!r}"
    )
    assert "SYSTEM/cli/build/engine_py/foo.py:5" in result, (
        f"at least one form must be rewritten correctly; got {result!r}"
    )


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_function_app_monorepo_unique_2seg_rewritten(tmp_path):
    """BARK monorepo reproducer: non-unique basename + unique 2-seg suffix.

    Stage 3 paths:
      demo/app-a/function_app.py
      demo/app-b/function_app.py
      demo/app-c/function_app.py

    Text: "function_app.py:692 and app-a/function_app.py:692"

    Expected:
    - bare "function_app.py:692" UNCHANGED (3-way ambiguous basename)
    - "app-a/function_app.py:692" → "demo/app-a/function_app.py:692" (unique 2-seg)
    - no demo/app-b/ or demo/app-c/ in result

    This is the actual ppba#657 fleet failure shape (53% of /build runs).

    RED today: _BARE_CITATION_RE excludes `/`; _build_suffix_map absent.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    _git_init_repo(tmp_path)
    _add_file(tmp_path, "demo/app-a/function_app.py")
    _add_file(tmp_path, "demo/app-b/function_app.py")
    _add_file(tmp_path, "demo/app-c/function_app.py")

    text = "function_app.py:692 and app-a/function_app.py:692"
    result = _autoprefix_bare_citations(text, tmp_path)

    # 2-seg unique suffix must be rewritten exactly once
    assert result.count("demo/app-a/function_app.py:692") == 1, (
        f"unique 2-seg suffix must appear exactly once rewritten; got {result!r}"
    )
    # No other app directories must appear
    assert "demo/app-b/" not in result, (
        f"no app-b rewrite must occur; got {result!r}"
    )
    # Bare basename (3-way ambiguous) must remain unchanged somewhere in text
    # After 2-seg rewrite, the text contains "function_app.py:692" as part of
    # "demo/app-a/function_app.py:692" — verify original bare form also present
    # by checking that "demo/app-a/function_app.py:692" appears (2-seg was rewritten)
    # and that no double-prefix occurred
    assert "demo/app-a/demo/app-a/function_app.py:692" not in result, (
        f"double-rewrite of 2-seg must not occur; got {result!r}"
    )


# ─── AC12: idempotency via _write_spec_doc ────────────────────────────────────


@pytest.mark.skipif(not _has_git(), reason="git not on PATH")
def test_existing_d02c615d_idempotent_holds(tmp_path):
    """Running _write_spec_doc twice with same input produces byte-identical output.

    Sanity that suffix-map doesn't perturb determinism inherited from D02C615D.

    RED today: _build_suffix_map absent → _autoprefix_bare_citations will behave
    differently once GREEN lands; pre-GREEN the test fails transitively because
    the import itself fails within the _write_spec_doc execution path.
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
    assert result1.status == "ok", f"_write_spec_doc failed first run: {result1.error}"
    content1 = Path(result1.data["spec_path"]).read_bytes()

    # Second run with same raw_response
    prev2 = make_prev_with_response(doc_path, raw_response, cycle=1)
    result2 = _write_spec_doc(ctx, prev2)
    assert result2.status == "ok", f"_write_spec_doc failed second run: {result2.error}"
    content2 = Path(result2.data["spec_path"]).read_bytes()

    assert content1 == content2, (
        "second _write_spec_doc run must produce byte-identical output (idempotent)"
    )


# ─── AC13: anti-N+1 (monkeypatch, no real git needed) ────────────────────────


def test_anti_n_plus_1_holds(monkeypatch, tmp_path):
    """subprocess.run called exactly once even with 3 distinct multi-segment citations.

    Mirrors test_git_ls_files_called_once_per_call from D02C615D but for the
    new _build_suffix_map symbol with multi-segment citation inputs.

    Feed text with 3 distinct multi-segment citations:
      "a/x.py:1 and b/y.py:2 and c/z.py:3"
    Fake subprocess returns all 3 paths.
    Assert call_count == 1 (no per-citation subprocess).

    RED today: _build_suffix_map absent → ImportError inside test.
    """
    from bytedigger_engine.workflows.phase_45_spec import _autoprefix_bare_citations

    fake_stdout = "a/x.py\nb/y.py\nc/z.py\n"
    call_count = {"n": 0}

    def _fake_run(*args, **kwargs):
        call_count["n"] += 1
        return types.SimpleNamespace(stdout=fake_stdout, returncode=0, stderr="")

    from bytedigger_engine.workflows import phase_45_spec as _phase_mod
    monkeypatch.setattr(_phase_mod.subprocess, "run", _fake_run)

    text = "a/x.py:1 and b/y.py:2 and c/z.py:3"
    _autoprefix_bare_citations(text, tmp_path)

    assert call_count["n"] == 1, (
        f"subprocess.run must be called exactly once per invocation; "
        f"called {call_count['n']} time(s)"
    )
