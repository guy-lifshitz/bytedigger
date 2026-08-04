"""RED tests for 16ED0B52 — audit_gate lib (AC1–AC9, AC13–AC14).

Agreement: 16ED0B52-073E-4591-B64B-6106D2C872F8
Class: SYSTEMATIC — universal pre-commit teeth for the engine_py→Opus rule.

All tests MUST FAIL until GREEN implements:
  - SYSTEM/cli/build/engine_py/audit_gate.py
  - SYSTEM/cli/build/engine-py-audit-gate.py

Fail mechanism: every import of audit_gate symbols is DEFERRED to inside the
test function body (D1CF5FDF / §1q extension) so the file COLLECTS cleanly and
FAILS at assert/call time with ImportError — NEVER at collection time.

§1i: no singleton/time-dependent resources — N/A.
sys.path wiring is provided by conftest.py at collection time (§1q / 81F97F3D
gate). NO module-level sys.path.insert in this file.

NO top-level import of audit_gate. See D1CF5FDF / §1q.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# ─── paths (computed once, no hardcoded home dir) ────────────────────────────

_THIS = Path(__file__).resolve()
_ENGINE_ROOT = _THIS.parents[1]          # …/engine_py/
_BUILD_DIR = _THIS.parents[2]            # …/SYSTEM/cli/build/
_CLI = _BUILD_DIR / "engine-py-audit-gate.py"


# ─── shared fixture helpers ───────────────────────────────────────────────────


def _engine_py_prod_file(tmp_path: Path, name: str = "prod_module.py") -> Path:
    """Return a touched file whose abspath contains /SYSTEM/cli/build/engine_py/."""
    p = tmp_path / "SYSTEM" / "cli" / "build" / "engine_py" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# prod\n")
    return p


def _build_tests_doc(tmp_path: Path, name: str, content: str) -> Path:
    """Return a *_build_tests.md file in the engine_py subtree with given content."""
    p = tmp_path / "SYSTEM" / "cli" / "build" / "engine_py" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ─── AC1: modified engine_py prod, no doc, no pragma, no kill-switch → BLOCK ─


def test_ac1_block_when_no_audit_doc(tmp_path: Path) -> None:
    """AC1: modified engine_py prod in staged, no build_tests doc, no pragma,
    no kill-switch → blocked=True, changed contains the path, escape=None."""
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)
    staged = [str(prod)]
    msg = "refactor: clean up module"

    result = scan_audit_violation(
        staged, msg,
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is True, (
        f"Expected blocked=True when no audit doc present, got blocked={result.blocked!r}"
    )
    assert str(prod) in result.changed, (
        f"Expected {str(prod)!r} in result.changed={result.changed!r}"
    )
    assert result.escape is None, (
        f"Expected escape=None, got escape={result.escape!r}"
    )


# ─── AC2: co-staged APPROVED build_tests doc → ALLOW ─────────────────────────


def test_ac2_allow_when_approved_doc_co_staged(tmp_path: Path) -> None:
    """AC2: modified engine_py prod + co-staged foo_build_tests.md containing
    'VERDICT: APPROVED' → blocked=False, escape='approved_doc'."""
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)
    doc = _build_tests_doc(
        tmp_path,
        "2026-06-13_16ED0B52_engine_py_audit_gate_build_tests.md",
        "# Audit\nVERDICT: APPROVED\n",
    )
    staged = [str(prod), str(doc)]
    msg = "feat: add audit gate"

    result = scan_audit_violation(
        staged, msg,
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is False, (
        f"Expected blocked=False when APPROVED doc co-staged, "
        f"got blocked={result.blocked!r}, reason={result.reason!r}"
    )
    assert result.escape == "approved_doc", (
        f"Expected escape='approved_doc', got escape={result.escape!r}"
    )


# ─── AC3: co-staged REJECTED doc → still BLOCK (presence ≠ approval) ─────────


def test_ac3_block_when_doc_not_approved(tmp_path: Path) -> None:
    """AC3 (anti-vacuous): co-staged *_build_tests.md containing 'VERDICT: REJECTED'
    (no APPROVAL token) → blocked=True.

    A naive presence-only implementation would pass AC2 but FAIL this test.
    """
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)
    doc = _build_tests_doc(
        tmp_path,
        "2026-06-13_bad_build_tests.md",
        "# Audit\nVERDICT: REJECTED\nOpus says no.\n",
    )
    staged = [str(prod), str(doc)]
    msg = "refactor: rename"

    result = scan_audit_violation(
        staged, msg,
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is True, (
        f"Expected blocked=True for REJECTED doc, got blocked={result.blocked!r}. "
        f"A VERDICT: REJECTED doc must NOT satisfy the gate — "
        f"only 'VERDICT: APPROVED' passes. escape={result.escape!r}"
    )


# ─── AC4: only non-engine files staged → ALLOW, changed=[] ───────────────────


def test_ac4_allow_when_no_engine_py_prod_staged(tmp_path: Path) -> None:
    """AC4: only non-engine files staged (README.md, non-engine .py) →
    blocked=False, changed=[]."""
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    readme = tmp_path / "README.md"
    readme.write_text("# HAL\n")
    other_py = tmp_path / "scripts" / "helper.py"
    other_py.parent.mkdir(parents=True, exist_ok=True)
    other_py.write_text("# not engine_py\n")

    staged = [str(readme), str(other_py)]
    msg = "docs: update readme"

    result = scan_audit_violation(
        staged, msg,
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is False, (
        f"Expected blocked=False when no engine_py prod files staged, "
        f"got blocked={result.blocked!r}"
    )
    assert result.changed == [], (
        f"Expected changed=[], got changed={result.changed!r}"
    )


# ─── AC5: _git_staged_modified_paths builds correct git arg list ──────────────


def test_ac5_git_staged_modified_paths_arg_list() -> None:
    """AC5 (repointed 8F4F8458): _git_staged_modified_paths() routes through the
    central git_port seam — injected via set_default_git_read_factory, args must
    contain '--diff-filter=MR' and '--cached' and must NOT include 'ACMR' or
    '--diff-filter=A' (Added files not gated — design #1).

    Pre-GREEN FAIL: fn still uses runner=subprocess.run default; factory ignored;
    captured stays empty → assertion on --diff-filter=MR fails.
    Repointed from runner=fake_runner to set_default_git_read_factory (8F4F8458 §1.3).
    """
    from bytedigger_engine.audit_gate import _git_staged_modified_paths

    from bytedigger_engine.lib.git_port import (
        GitResult,
        reset_default_git_read_factory,
        set_default_git_read_factory,
    )

    captured: list = []

    def fake_reader(args, *, cwd=None, timeout=None, dir_=None):
        captured.extend(args)
        return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: fake_reader)
    try:
        _git_staged_modified_paths()
    finally:
        reset_default_git_read_factory()

    arg_str = " ".join(str(a) for a in captured)

    assert "--diff-filter=MR" in arg_str or (
        "--diff-filter" in arg_str and "MR" in arg_str
    ), (
        f"Expected '--diff-filter=MR' in git args (Modified+Renamed only). "
        f"Got args: {captured!r}"
    )
    assert "--cached" in arg_str, (
        f"Expected '--cached' in git args. Got args: {captured!r}"
    )
    # Must NOT include a diff-filter that admits Added files.
    assert "--diff-filter=A" not in arg_str, (
        f"Must NOT include '--diff-filter=A' (new files must not be gated). "
        f"Got args: {captured!r}"
    )
    assert "ACMR" not in arg_str, (
        f"Must NOT include 'ACMR' diff-filter (admits Added). "
        f"Got args: {captured!r}"
    )


# ─── AC6: kill-switch env → ALLOW ─────────────────────────────────────────────


def test_ac6_allow_when_kill_switch_env(tmp_path: Path) -> None:
    """AC6: HAL_ENGINE_PY_AUDIT_GATE=0 + modified engine_py prod + no doc
    → blocked=False, escape='kill_switch'."""
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)
    staged = [str(prod)]
    msg = "chore: update"

    result = scan_audit_violation(
        staged, msg,
        env={"HAL_ENGINE_PY_AUDIT_GATE": "0"},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is False, (
        f"Expected blocked=False when kill-switch env set to '0', "
        f"got blocked={result.blocked!r}"
    )
    assert result.escape == "kill_switch", (
        f"Expected escape='kill_switch', got escape={result.escape!r}"
    )


# ─── AC7: pragma with reason → ALLOW ──────────────────────────────────────────


def test_ac7_allow_when_pragma_with_reason(tmp_path: Path) -> None:
    """AC7: commit_message 'engine-py-audit: skip mechanical rename' +
    modified engine_py prod + no doc → blocked=False, escape='pragma',
    reason contains 'mechanical rename'."""
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)
    staged = [str(prod)]
    msg = "refactor: engine-py-audit: skip mechanical rename\n\nBody text."

    result = scan_audit_violation(
        staged, msg,
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is False, (
        f"Expected blocked=False with valid pragma+reason, "
        f"got blocked={result.blocked!r}"
    )
    assert result.escape == "pragma", (
        f"Expected escape='pragma', got escape={result.escape!r}"
    )
    assert "mechanical rename" in result.reason, (
        f"Expected 'mechanical rename' in result.reason={result.reason!r}"
    )


# ─── AC8: pragma without reason → still BLOCK (reason mandatory) ──────────────


def test_ac8_block_when_pragma_has_no_reason(tmp_path: Path) -> None:
    """AC8 (anti-vacuous): pragma 'engine-py-audit: skip' with no reason
    (bare or whitespace-only) → blocked=True.

    A naive substring-presence check ('engine-py-audit: skip' anywhere) would
    incorrectly allow this. Must enforce non-empty reason group.
    """
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)
    staged = [str(prod)]

    for bare_msg in [
        "engine-py-audit: skip",
        "engine-py-audit: skip   ",
        "feat: engine-py-audit: skip\n",
    ]:
        result = scan_audit_violation(
            staged, bare_msg,
            env={},
            exists=os.path.exists,
            read_text=lambda p: Path(p).read_text(encoding="utf-8"),
        )
        assert result.blocked is True, (
            f"Expected blocked=True for bare/whitespace-only pragma {bare_msg!r}, "
            f"got blocked={result.blocked!r}, escape={result.escape!r}. "
            "Reason MUST be non-empty — 'engine-py-audit: skip' alone is not enough."
        )


# ─── AC9: un-staged APPROVED doc → BLOCK; wrong suffixes don't count ──────────


def test_ac9_block_when_approved_doc_not_staged_and_wrong_suffixes(tmp_path: Path) -> None:
    """AC9: APPROVED *_build_tests.md present on disk but NOT in staged_paths
    → blocked=True (co-staged required).
    AND files 'x_build_tests.mdx' / 'x_build_tests.md.bak' must NOT count as
    build_tests docs (exact suffix required).
    """
    from bytedigger_engine.audit_gate import scan_audit_violation, is_build_tests_doc  # ImportError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)

    # Write an APPROVED doc on disk but do NOT include it in staged_paths.
    approved_doc = _build_tests_doc(
        tmp_path,
        "foo_build_tests.md",
        "VERDICT: APPROVED\n",
    )
    assert approved_doc.exists(), "Sanity: approved doc must be on disk"

    # Staged only has the prod file — no doc.
    staged = [str(prod)]
    msg = "chore: update module"

    result = scan_audit_violation(
        staged, msg,
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is True, (
        f"Expected blocked=True when APPROVED doc is on disk but NOT staged. "
        f"Co-staged requirement must be enforced. "
        f"got blocked={result.blocked!r}, escape={result.escape!r}"
    )

    # Verify wrong-suffix files are NOT recognized as build_tests docs.
    assert is_build_tests_doc("x_build_tests.mdx") is False, (
        "x_build_tests.mdx must NOT be recognized as a build_tests doc "
        "(exact '_build_tests.md' suffix required)"
    )
    assert is_build_tests_doc("x_build_tests.md.bak") is False, (
        "x_build_tests.md.bak must NOT be recognized as a build_tests doc"
    )
    assert is_build_tests_doc("foo_build_tests.md") is True, (
        "foo_build_tests.md SHOULD be recognized as a build_tests doc"
    )


# ─── AC13: install_commit_msg_hook → creates executable hook with sentinels ───


def test_ac13_install_hook_creates_executable_hook(tmp_path: Path) -> None:
    """AC13: install_commit_msg_hook(tmp_hooks_dir, gate_path) into empty dir
    → 'commit-msg' exists, mode & 0o111 (executable), contains sentinel OPEN+CLOSE
    delimiters AND '--message-file \"$1\"' AND the literal gate_path string."""
    from bytedigger_engine.audit_gate import install_commit_msg_hook  # ImportError pre-GREEN → FAIL

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    gate_path = "/some/path/to/engine-py-audit-gate.py"

    install_commit_msg_hook(str(hooks_dir), gate_path)

    hook_file = hooks_dir / "commit-msg"
    assert hook_file.exists(), (
        f"Expected commit-msg hook file to be created at {hook_file}"
    )

    # Check executable bit.
    mode = hook_file.stat().st_mode
    assert mode & 0o111, (
        f"Expected commit-msg hook to be executable (mode & 0o111), "
        f"got mode={oct(mode)}"
    )

    content = hook_file.read_text()

    # Sentinel OPEN delimiter.
    assert "# >>> hal engine-py-audit-gate >>>" in content, (
        f"Expected sentinel OPEN '# >>> hal engine-py-audit-gate >>>' in hook content. "
        f"content={content!r}"
    )
    # Sentinel CLOSE delimiter.
    assert "# <<< hal engine-py-audit-gate <<<" in content, (
        f"Expected sentinel CLOSE '# <<< hal engine-py-audit-gate <<<' in hook content. "
        f"content={content!r}"
    )
    # Message file arg.
    assert '--message-file "$1"' in content, (
        f"Expected '--message-file \"$1\"' in hook content. content={content!r}"
    )
    # Literal gate path.
    assert gate_path in content, (
        f"Expected literal gate_path {gate_path!r} in hook content. "
        f"content={content!r}"
    )


# ─── AC14: install_commit_msg_hook is idempotent; preserves other content ─────


def test_ac14_install_hook_idempotent_preserves_content(tmp_path: Path) -> None:
    """AC14: calling install_commit_msg_hook twice → exactly ONE occurrence of the
    sentinel OPEN delimiter (idempotent); a pre-seeded unrelated line is preserved."""
    from bytedigger_engine.audit_gate import install_commit_msg_hook  # ImportError pre-GREEN → FAIL

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    gate_path = "/path/to/gate.py"

    # Pre-seed an existing commit-msg hook with a custom guard.
    hook_file = hooks_dir / "commit-msg"
    hook_file.write_text("#!/usr/bin/env bash\n# custom guard\necho 'pre-existing'\n")
    hook_file.chmod(0o755)

    # Install twice.
    install_commit_msg_hook(str(hooks_dir), gate_path)
    install_commit_msg_hook(str(hooks_dir), gate_path)

    content = hook_file.read_text()

    sentinel_open = "# >>> hal engine-py-audit-gate >>>"
    count = content.count(sentinel_open)
    assert count == 1, (
        f"Expected exactly 1 occurrence of sentinel OPEN after two installs "
        f"(idempotent), got {count}. content={content!r}"
    )

    # Pre-existing content must be preserved.
    assert "# custom guard" in content, (
        f"Expected pre-existing '# custom guard' line to be preserved after install. "
        f"content={content!r}"
    )


# ─── AC15: APPROVED doc added (not modified) still allows — core regression ───


def test_ac15_approved_doc_added_not_modified_still_allows(tmp_path: Path) -> None:
    """AC15 (core regression): the approved doc is a NEW (added) file, not modified.
    scan_audit_violation must accept `modified_paths` kwarg and use it for
    engine-prod detection while using all of staged_paths for doc scanning.

    FAILs today: current scan_audit_violation has no `modified_paths` kwarg → TypeError.
    """
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError / TypeError pre-GREEN → FAIL

    prod = _engine_py_prod_file(tmp_path)  # existing engine_py prod file (modified)
    doc = _build_tests_doc(
        tmp_path,
        "x_build_tests.md",
        "VERDICT: APPROVED\n",
    )

    # staged_paths includes both; modified_paths contains only the prod file.
    # The doc is ADDED (new), so it would be absent from a --diff-filter=MR list.
    result = scan_audit_violation(
        [str(prod), str(doc)],
        "",
        modified_paths=[str(prod)],
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is False, (
        f"Expected blocked=False when APPROVED doc is staged (added, not modified). "
        f"The doc must be found via staged_paths, not modified_paths. "
        f"blocked={result.blocked!r}, escape={result.escape!r}, reason={result.reason!r}"
    )
    assert result.escape == "approved_doc", (
        f"Expected escape='approved_doc', got escape={result.escape!r}"
    )


# ─── AC15b: _git_all_staged_paths includes added files (--diff-filter=ACMR) ───


def test_ac15b_git_all_staged_paths_includes_added() -> None:
    """AC15b (repointed 8F4F8458): _git_all_staged_paths() routes through the
    central git_port seam — injected via set_default_git_read_factory, args must
    contain '--diff-filter=ACMR' and '--cached' so added files are included.

    Pre-GREEN FAIL: fn still uses runner=subprocess.run default; factory ignored;
    captured stays empty → assertion on ACMR fails.
    Repointed from runner=fake_runner to set_default_git_read_factory (8F4F8458 §1.3).
    """
    from bytedigger_engine.audit_gate import _git_all_staged_paths

    from bytedigger_engine.lib.git_port import (
        GitResult,
        reset_default_git_read_factory,
        set_default_git_read_factory,
    )

    captured: list = []

    def fake_reader(args, *, cwd=None, timeout=None, dir_=None):
        captured.extend(args)
        return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: fake_reader)
    try:
        _git_all_staged_paths()
    finally:
        reset_default_git_read_factory()

    arg_str = " ".join(str(a) for a in captured)

    assert "--cached" in arg_str, (
        f"Expected '--cached' in git args for _git_all_staged_paths. "
        f"Got args: {captured!r}"
    )
    assert "ACMR" in arg_str or "--diff-filter=ACMR" in arg_str, (
        f"Expected '--diff-filter=ACMR' (includes Added) in git args. "
        f"Got args: {captured!r}. "
        "_git_all_staged_paths must NOT use --diff-filter=MR."
    )


# ─── AC16: new engine_py file in staged_paths but NOT in modified_paths → ALLOW


def test_ac16_new_engine_py_file_not_gated(tmp_path: Path) -> None:
    """AC16: a NEW engine_py prod file is in staged_paths but NOT in modified_paths.
    With no doc and no pragma, blocked must still be False — new files aren't gated
    (design-decision #1).

    FAILs today: scan_audit_violation has no `modified_paths` kwarg → TypeError.
    """
    from bytedigger_engine.audit_gate import scan_audit_violation  # ImportError / TypeError pre-GREEN → FAIL

    newfoo = _engine_py_prod_file(tmp_path, name="brand_new_module.py")

    result = scan_audit_violation(
        [str(newfoo)],
        "",
        modified_paths=[],  # new file — not in the modified set
        env={},
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )

    assert result.blocked is False, (
        f"Expected blocked=False for a new engine_py file (not in modified_paths). "
        f"New files must not be gated — only modifications trigger the audit gate. "
        f"blocked={result.blocked!r}, changed={result.changed!r}"
    )
    assert result.changed == [], (
        f"Expected changed=[] (no modified engine_py files), got {result.changed!r}"
    )
