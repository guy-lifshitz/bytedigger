"""RED tests for 0015E696 — git_read injectable Protocol seam (OSS seam #5).

Spec: SHARED/memory/Decisions/2026-06-19_0015E696_git_read_port_spec.md

Current production code (lib/git_port.py) is a FLAT module: GitResult + git_read
+ 4 Layer-2 funcs.  It has NO Protocol class, NO registry, NO resolver, NO injectors.

All 12 tests FAIL today because the new symbols (GitReadPort, _git_read_subprocess,
default_git_read, _DEFAULT_FACTORY, _ORIGINAL_FACTORY, get_git_read,
set_default_git_read_factory, reset_default_git_read_factory) do not yet exist,
and git_read is not yet a thin delegator.

Pre-GREEN PASS/FAIL classification (§3):
  AC1  → FAIL  (GitReadPort class absent)
  AC2  → FAIL  (_git_read_subprocess absent)
  AC3  → FAIL  (default_git_read absent)
  AC4  → FAIL  (_DEFAULT_FACTORY / _ORIGINAL_FACTORY module globals absent)
  AC5  → FAIL  (get_git_read absent)
  AC6  → FAIL  (git_read not a delegator; _git_read_subprocess absent for direct call)
  AC7  → FAIL  (set_default_git_read_factory absent; git_read not a delegator)
  AC8  → FAIL  (set_default_git_read_factory absent; git_read not a delegator)
  AC9  → FAIL  (reset_default_git_read_factory absent; get_git_read absent)
  AC10 → FAIL  (git_read is direct subprocess impl, not patchable-and-routeable seam)
  AC11 → FAIL  (set_default_git_read_factory absent; git_read not a delegator)
  AC12 → PASS  (all 4 Layer-2 fns already exist — regression guard)

§1i (workflows.md §1i: Singleton-resource tests pre-stage state, never race):
  AC7/AC8/AC9/AC11 pre-stage global state via set_default_git_read_factory and
  restore via reset_default_git_read_factory() in finally blocks.

D1CF5FDF: not-yet-existing symbols accessed via getattr(gp, "name") INSIDE test
bodies or via deferred import-inside-body, NEVER at module top level.
Module-level `import lib.git_port as gp` is safe because the module already exists.

§1q: no spec_from_file_location / exec_module — normal import used.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

import lib.git_port as gp  # module already exists; safe at module level


# ─────────────────────────────────────────────────────────────────────────────
# Shared real-repo fixture  (§1j macOS-safe realpath, §1u real subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _init_repo(repo: Path) -> None:
    """Initialise a minimal real git repo with one HEAD commit."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "init.txt").write_text("hello\n")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Real macOS-safe git repo with one commit (§1j realpathSync equivalent)."""
    repo = Path(os.path.realpath(tempfile.mkdtemp(dir=tmp_path)))
    _init_repo(repo)
    return repo


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel fake for injection tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_sentinel_fake():
    """Return a fake GitReadPort callable that returns a distinctive sentinel GitResult."""
    def fake(args, *, cwd=None, timeout=None, dir_=None):
        return gp.GitResult(returncode=77, stdout="SENTINEL", stderr="", timed_out=False)
    return fake


# ═════════════════════════════════════════════════════════════════════════════
# AC1 — GitReadPort Protocol exists and is @runtime_checkable
# ═════════════════════════════════════════════════════════════════════════════

def test_ac1_gitreadport_protocol_exists_and_is_runtime_checkable() -> None:
    """AC1: GitReadPort exists, is a @runtime_checkable Protocol with correct __call__ signature."""
    GitReadPort = getattr(gp, "GitReadPort", None)
    assert GitReadPort is not None, (
        "GitReadPort not found in lib.git_port — symbol does not exist yet (expected RED)"
    )
    # @runtime_checkable sets _is_runtime_protocol on the Protocol class
    assert getattr(GitReadPort, "_is_runtime_protocol", False) is True, (
        "GitReadPort is not @runtime_checkable"
    )
    # Verify __call__ exists and declares the correct return type
    assert hasattr(GitReadPort, "__call__"), "GitReadPort has no __call__"
    hints = getattr(GitReadPort.__call__, "__annotations__", {})
    assert "return" in hints, "GitReadPort.__call__ does not declare a return annotation"
    # Return annotation must reference GitResult
    return_hint = hints["return"]
    assert "GitResult" in str(return_hint), (
        f"GitReadPort.__call__ return annotation {return_hint!r} does not mention GitResult"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC2 — _git_read_subprocess exists and isinstance(_, GitReadPort) is True
# ═════════════════════════════════════════════════════════════════════════════

def test_ac2_git_read_subprocess_exists_and_is_protocol_instance() -> None:
    """AC2: _git_read_subprocess exists and isinstance(_git_read_subprocess, GitReadPort) is True."""
    GitReadPort = getattr(gp, "GitReadPort", None)
    assert GitReadPort is not None, "GitReadPort absent"

    _git_read_subprocess = getattr(gp, "_git_read_subprocess", None)
    assert _git_read_subprocess is not None, (
        "_git_read_subprocess not found in lib.git_port — symbol absent (expected RED)"
    )
    assert isinstance(_git_read_subprocess, GitReadPort), (
        f"_git_read_subprocess ({_git_read_subprocess!r}) is not an instance of GitReadPort"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — default_git_read() returns _git_read_subprocess (identity)
# ═════════════════════════════════════════════════════════════════════════════

def test_ac3_default_git_read_returns_subprocess_impl() -> None:
    """AC3: default_git_read() returns _git_read_subprocess (identity, not equality)."""
    default_git_read = getattr(gp, "default_git_read", None)
    assert default_git_read is not None, (
        "default_git_read absent in lib.git_port (expected RED)"
    )
    _git_read_subprocess = getattr(gp, "_git_read_subprocess", None)
    assert _git_read_subprocess is not None, "_git_read_subprocess absent"

    result = default_git_read()
    assert result is _git_read_subprocess, (
        f"default_git_read() returned {result!r}, expected _git_read_subprocess identity"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — _DEFAULT_FACTORY is default_git_read AND _ORIGINAL_FACTORY is default_git_read at import
# ═════════════════════════════════════════════════════════════════════════════

def test_ac4_factory_globals_are_default_git_read_at_import() -> None:
    """AC4: _DEFAULT_FACTORY is default_git_read AND _ORIGINAL_FACTORY is default_git_read at module load."""
    _DEFAULT_FACTORY = getattr(gp, "_DEFAULT_FACTORY", None)
    assert _DEFAULT_FACTORY is not None, "_DEFAULT_FACTORY module global absent (expected RED)"

    _ORIGINAL_FACTORY = getattr(gp, "_ORIGINAL_FACTORY", None)
    assert _ORIGINAL_FACTORY is not None, "_ORIGINAL_FACTORY module global absent (expected RED)"

    default_git_read = getattr(gp, "default_git_read", None)
    assert default_git_read is not None, "default_git_read absent"

    assert _DEFAULT_FACTORY is _ORIGINAL_FACTORY, (
        "_DEFAULT_FACTORY is not _ORIGINAL_FACTORY at import (§1g single-source broken)"
    )
    assert _DEFAULT_FACTORY is default_git_read, (
        "_DEFAULT_FACTORY is not default_git_read at import"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — get_git_read() with no override returns _git_read_subprocess
# ═════════════════════════════════════════════════════════════════════════════

def test_ac5_get_git_read_no_override_returns_subprocess_impl() -> None:
    """AC5: get_git_read() with no factory override returns _git_read_subprocess."""
    get_git_read = getattr(gp, "get_git_read", None)
    assert get_git_read is not None, (
        "get_git_read absent in lib.git_port (expected RED)"
    )
    _git_read_subprocess = getattr(gp, "_git_read_subprocess", None)
    assert _git_read_subprocess is not None, "_git_read_subprocess absent"

    resolved = get_git_read()
    assert resolved is _git_read_subprocess, (
        f"get_git_read() returned {resolved!r}, expected _git_read_subprocess"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC6 — Behavior-preserving (real git subprocess): git_read identical to _git_read_subprocess
# ═════════════════════════════════════════════════════════════════════════════

def test_ac6_behavior_preserving_real_git_repo(git_repo: Path) -> None:
    """AC6: git_read result is byte-identical to _git_read_subprocess called directly (real git repo)."""
    _git_read_subprocess = getattr(gp, "_git_read_subprocess", None)
    assert _git_read_subprocess is not None, (
        "_git_read_subprocess absent (expected RED)"
    )

    args = ["rev-parse", "--abbrev-ref", "HEAD"]
    repo_str = str(git_repo)

    result_via_delegator = gp.git_read(args, cwd=repo_str)
    result_via_impl = _git_read_subprocess(args, cwd=repo_str)

    assert result_via_delegator.returncode == result_via_impl.returncode, (
        f"returncode mismatch: {result_via_delegator.returncode} vs {result_via_impl.returncode}"
    )
    assert result_via_delegator.stdout == result_via_impl.stdout, (
        f"stdout mismatch: {result_via_delegator.stdout!r} vs {result_via_impl.stdout!r}"
    )
    assert result_via_delegator.stderr == result_via_impl.stderr, (
        f"stderr mismatch: {result_via_delegator.stderr!r} vs {result_via_impl.stderr!r}"
    )
    assert result_via_delegator.timed_out == result_via_impl.timed_out, (
        f"timed_out mismatch: {result_via_delegator.timed_out} vs {result_via_impl.timed_out}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC7 — Delegator routes to injection (sentinel through real delegator)
# ═════════════════════════════════════════════════════════════════════════════

def test_ac7_delegator_routes_to_injected_factory(tmp_path: Path) -> None:
    """AC7: after set_default_git_read_factory(lambda: fake), git_read returns sentinel (rc==77, stdout=='SENTINEL').

    §1i: reset_default_git_read_factory() called in finally to prevent global-state leak.
    """
    set_factory = getattr(gp, "set_default_git_read_factory", None)
    assert set_factory is not None, (
        "set_default_git_read_factory absent in lib.git_port (expected RED)"
    )
    reset_factory = getattr(gp, "reset_default_git_read_factory", None)
    assert reset_factory is not None, (
        "reset_default_git_read_factory absent (expected RED)"
    )

    fake = _make_sentinel_fake()
    try:
        set_factory(lambda: fake)
        result = gp.git_read(["status"], cwd=str(tmp_path))
        assert result.returncode == 77, (
            f"Expected returncode==77 (sentinel), got {result.returncode}"
        )
        assert result.stdout == "SENTINEL", (
            f"Expected stdout=='SENTINEL', got {result.stdout!r}"
        )
    finally:
        reset_factory()


# ═════════════════════════════════════════════════════════════════════════════
# AC8 — Injection reaches module-attr form (gp.git_read) used by phase_5/6/8
# ═════════════════════════════════════════════════════════════════════════════

def test_ac8_injection_reaches_module_attr_call_site(tmp_path: Path) -> None:
    """AC8: gp.git_read(...) (module-attr form) after set_default_git_read_factory also returns sentinel.

    Proves phase_5/6/8 'git_port.git_read(...)' sites route through injection.
    §1i: global state restored in finally block.
    """
    set_factory = getattr(gp, "set_default_git_read_factory", None)
    assert set_factory is not None, "set_default_git_read_factory absent"

    reset_factory = getattr(gp, "reset_default_git_read_factory", None)
    assert reset_factory is not None, "reset_default_git_read_factory absent"

    fake = _make_sentinel_fake()
    try:
        set_factory(lambda: fake)
        # Access via module attribute (same form as 'git_port.git_read(...)' in prod)
        import lib.git_port as gp2
        result = gp2.git_read(["status"], cwd=str(tmp_path))
        assert result.returncode == 77, (
            f"Module-attr reach: expected returncode==77 (sentinel), got {result.returncode}"
        )
        assert result.stdout == "SENTINEL", (
            f"Module-attr reach: expected stdout=='SENTINEL', got {result.stdout!r}"
        )
    finally:
        reset_factory()


# ═════════════════════════════════════════════════════════════════════════════
# AC9 — reset_default_git_read_factory() restores original after set
# ═════════════════════════════════════════════════════════════════════════════

def test_ac9_reset_restores_original_factory_and_real_git_works(git_repo: Path) -> None:
    """AC9: after set→reset, get_git_read() is _git_read_subprocess AND git_read returns real GitResult.

    §1i: global state restored in finally block.
    """
    set_factory = getattr(gp, "set_default_git_read_factory", None)
    assert set_factory is not None, "set_default_git_read_factory absent"

    reset_factory = getattr(gp, "reset_default_git_read_factory", None)
    assert reset_factory is not None, "reset_default_git_read_factory absent"

    get_git_read = getattr(gp, "get_git_read", None)
    assert get_git_read is not None, "get_git_read absent"

    _git_read_subprocess = getattr(gp, "_git_read_subprocess", None)
    assert _git_read_subprocess is not None, "_git_read_subprocess absent"

    _ORIGINAL_FACTORY = getattr(gp, "_ORIGINAL_FACTORY", None)
    assert _ORIGINAL_FACTORY is not None, "_ORIGINAL_FACTORY absent"

    fake = _make_sentinel_fake()
    try:
        set_factory(lambda: fake)
        # Confirm injection took effect
        assert get_git_read() is fake, "Injection did not take effect"
    finally:
        reset_factory()

    # After reset: resolver returns subprocess impl
    assert get_git_read() is _git_read_subprocess, (
        "After reset, get_git_read() is not _git_read_subprocess"
    )
    # After reset: _DEFAULT_FACTORY is back to _ORIGINAL_FACTORY
    assert gp._DEFAULT_FACTORY is _ORIGINAL_FACTORY, (  # type: ignore[attr-defined]
        "After reset, _DEFAULT_FACTORY is not _ORIGINAL_FACTORY"
    )
    # After reset: real git works (non-sentinel result)
    result = gp.git_read(["rev-parse", "HEAD"], cwd=str(git_repo))
    assert result.returncode == 0, f"Real git after reset returned rc={result.returncode}"
    assert result.stdout.strip() != "SENTINEL", "Real git returned SENTINEL after reset"
    assert len(result.stdout.strip()) == 40, (
        f"Expected 40-char SHA, got {result.stdout.strip()!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC10 — Monkeypatch-compat: setattr(gp, "git_read", fake) rebinds the name
# ═════════════════════════════════════════════════════════════════════════════

def test_ac10_monkeypatch_compat_setattr_rebinds_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC10: monkeypatch.setattr(gp, 'git_read', fake) makes gp.git_read() see fake — preserves ~6 existing patch sites."""
    fake = _make_sentinel_fake()
    monkeypatch.setattr(gp, "git_read", fake)

    result = gp.git_read(["status"], cwd=str(tmp_path))
    assert result.returncode == 77, (
        f"After monkeypatch.setattr, expected returncode==77, got {result.returncode}"
    )
    assert result.stdout == "SENTINEL", (
        f"After monkeypatch.setattr, expected stdout=='SENTINEL', got {result.stdout!r}"
    )
    # monkeypatch fixture auto-restores on teardown — no manual reset needed here


# ═════════════════════════════════════════════════════════════════════════════
# AC11 — Layer-2 status_porcelain() routes through the delegator
# ═════════════════════════════════════════════════════════════════════════════

def test_ac11_layer2_status_porcelain_routes_through_delegator(tmp_path: Path) -> None:
    """AC11: with set_default_git_read_factory(lambda: fake returning stdout='X\\nY\\n'), status_porcelain returns 'X\\nY\\n'.

    §1i: global state restored in finally block.
    """
    set_factory = getattr(gp, "set_default_git_read_factory", None)
    assert set_factory is not None, "set_default_git_read_factory absent"

    reset_factory = getattr(gp, "reset_default_git_read_factory", None)
    assert reset_factory is not None, "reset_default_git_read_factory absent"

    expected_stdout = "X\nY\n"

    def fake_with_custom_stdout(args, *, cwd=None, timeout=None, dir_=None):
        return gp.GitResult(returncode=0, stdout=expected_stdout, stderr="", timed_out=False)

    try:
        set_factory(lambda: fake_with_custom_stdout)
        result = gp.status_porcelain(cwd=str(tmp_path))
        assert result == expected_stdout, (
            f"status_porcelain returned {result!r}, expected {expected_stdout!r} — "
            "Layer-2 fn is not routing through the delegator"
        )
    finally:
        reset_factory()


# ═════════════════════════════════════════════════════════════════════════════
# AC12 — All four Layer-2 funcs exist with correct signatures (no-surface-loss)
# ═════════════════════════════════════════════════════════════════════════════

def test_ac12_all_layer2_funcs_present_with_correct_signatures() -> None:
    """AC12: rev_parse/status_porcelain/ls_files_others/worktree_list_porcelain all exist with identical signatures."""
    import inspect

    # rev_parse(ref='HEAD', *, cwd=None, flag=None, timeout=None) -> str
    assert hasattr(gp, "rev_parse") and callable(gp.rev_parse), "rev_parse absent"
    sig_rev = inspect.signature(gp.rev_parse)
    assert "ref" in sig_rev.parameters, "rev_parse missing 'ref' param"
    assert "cwd" in sig_rev.parameters, "rev_parse missing 'cwd' param"
    assert "flag" in sig_rev.parameters, "rev_parse missing 'flag' param"
    assert "timeout" in sig_rev.parameters, "rev_parse missing 'timeout' param"

    # status_porcelain(*, cwd=None, timeout=None) -> str
    assert hasattr(gp, "status_porcelain") and callable(gp.status_porcelain), "status_porcelain absent"
    sig_sp = inspect.signature(gp.status_porcelain)
    assert "cwd" in sig_sp.parameters, "status_porcelain missing 'cwd' param"
    assert "timeout" in sig_sp.parameters, "status_porcelain missing 'timeout' param"

    # ls_files_others(*, cwd=None, timeout=None) -> list[str]
    assert hasattr(gp, "ls_files_others") and callable(gp.ls_files_others), "ls_files_others absent"
    sig_lfo = inspect.signature(gp.ls_files_others)
    assert "cwd" in sig_lfo.parameters, "ls_files_others missing 'cwd' param"
    assert "timeout" in sig_lfo.parameters, "ls_files_others missing 'timeout' param"

    # worktree_list_porcelain(*, cwd=None, timeout=None) -> str
    assert hasattr(gp, "worktree_list_porcelain") and callable(gp.worktree_list_porcelain), "worktree_list_porcelain absent"
    sig_wlp = inspect.signature(gp.worktree_list_porcelain)
    assert "cwd" in sig_wlp.parameters, "worktree_list_porcelain missing 'cwd' param"
    assert "timeout" in sig_wlp.parameters, "worktree_list_porcelain missing 'timeout' param"
