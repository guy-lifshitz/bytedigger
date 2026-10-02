"""bd#94 RED: engine-owned paths are excluded from tree/manifest gates by construction.

Spec: docs/decisions/2026-10-02-bd94-engine-owned-paths.md (r1, frozen).

Today no shared predicate exists (`bytedigger_engine.lib.util.engine_owned` is absent),
the producers hand-roll (or omit) the engine-state exclusion, and no lint forces a new
tree-scan site to filter (`bytedigger_engine.conformance.tree_scan_lint` is absent).

AC -> test map
--------------
AC1  test_ac1_r1_state_dir_forms_owned / test_ac1_non_matches_are_not_owned /
     test_ac1_r2_escaping_symlinks_owned / test_ac1_empty_path_not_owned /
     test_ac1_host_override_honoured / test_ac1_provider_failure_degrades_never_raises
AC2  test_ac2_drop_preserves_order_and_emits_one_event / test_ac2_event_paths_sorted_and_capped /
     test_ac2_no_event_when_nothing_dropped / test_ac2_emitter_failure_does_not_propagate
AC3  test_ac3_engine_owned_pathspecs_exact / test_ac3_pathspecs_honour_host_override /
     test_ac3_prune_engine_owned_dirs_in_place
AC4  test_ac4_derive_green_paths_excludes_engine_state /
     test_ac4_derive_security_lint_paths_excludes_engine_state_and_escaping_symlink /
     test_ac4_filter_gitignored_paths_drops_engine_state /
     test_ac4_checkpoint_green_worktree_uses_shared_pathspecs (AST) /
     test_ac4_shared_pathspecs_keep_engine_state_unstaged (behavioural, real git)
AC5  test_ac5_harvest_codes_skips_engine_state / test_ac5_harvest_exclude_dirs_has_no_literal_state_dir /
     test_ac5_harvest_codes_honours_host_override / test_ac5_cyrillic_tracked_files_walk_skips_engine_state /
     test_ac5_cyrillic_tracked_files_walk_honours_host_override
AC6  test_ac6_real_tree_passes_tree_scan_lint / test_ac6_producers_are_classified_filters
AC7  test_ac7_unkeyed_sites_redden / test_ac7_filters_without_filter_call_redden /
     test_ac7_filter_call_in_function_is_clean / test_ac7_stale_key_reddens /
     test_ac7_malformed_entries_redden
AC8  test_ac8_filter_gitignored_paths_calls_drop_engine_owned
AC9  test_ac9_class_i_lint_unchanged_on_real_tree (MAY ALREADY PASS: guard that the new module adds
     no class-I read site; not a RED by itself)

Harness: real tmp git repos via subprocess (no git mocks); events captured through the
telemetry slot (`telemetry_ctx.set_current_run(event_log=...)`) that `_emit_safe` writes to, as
test_bd150_class_i_inventory.py does. Host override = a config-provider factory swap, restored
by an autouse fixture (workflows.md 1i: pre-staged baseline, no timing). The new modules are
imported lazily inside the tests so one ImportError reddens each test individually.
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine import config_provider, telemetry_ctx

EVENT_TYPE = "engine_owned_paths_dropped"
CYR = "Привет"  # escape-written so this file stays ASCII

_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _eo():
    return importlib.import_module("bytedigger_engine.lib.util.engine_owned")


def _lint():
    return importlib.import_module("bytedigger_engine.conformance.tree_scan_lint")


def _engine_root() -> Path:
    import bytedigger_engine
    return Path(bytedigger_engine.__file__).parent


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True, env=_GIT_ENV,
    )
    return out.stdout.strip()


class _FakeEventLog:
    def __init__(self, raises: bool = False) -> None:
        self.events: list = []
        self.raises = raises

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        if self.raises:
            raise RuntimeError("event log down")
        self.events.append((event_type, dict(payload), run_id))

    def of(self, event_type: str) -> list:
        return [p for (t, p, _) in self.events if t == event_type]


def _install_log(raises: bool = False) -> _FakeEventLog:
    log = _FakeEventLog(raises=raises)
    telemetry_ctx.set_current_run(event_log=log, run_id="bd94", step_name="bd94")
    return log


class _DirnameProvider(config_provider._DefaultConfigProvider):
    """Overrides only foreign_state_dirname; `value` may be a str, a non-str, or an exception class."""

    def __init__(self, value) -> None:
        super().__init__()
        self._value = value

    def foreign_state_dirname(self):
        if isinstance(self._value, type) and issubclass(self._value, BaseException):
            raise self._value("provider down")
        return self._value


def _set_dirname(value) -> None:
    config_provider.set_default_config_provider_factory(lambda: _DirnameProvider(value))


@pytest.fixture(autouse=True)
def _isolation():
    """Telemetry slot and config provider are process-wide singletons: pre-stage a known
    baseline before the body and restore after (workflows.md 1i)."""
    config_provider.reset_default_config_provider_factory()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    config_provider.reset_default_config_provider_factory()


def _outside(tmp_path: Path) -> Path:
    out = tmp_path / "outside"
    out.mkdir()
    (out / "secret.txt").write_text("x\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# AC1 - predicate
# ---------------------------------------------------------------------------

def test_ac1_r1_state_dir_forms_owned(tmp_path) -> None:
    """AC1 R1: the state dir at any depth, in ./ and backslash forms, as Path, and absolute under root."""
    eo = _eo()
    repo = tmp_path / "repo"
    repo.mkdir()
    root = str(repo)
    owned = [
        ".bytedigger", ".bytedigger/events.jsonl", "sub/.bytedigger/x", "a/b/.bytedigger/c/d",
        "./.bytedigger/x", ".bytedigger\\x", "sub\\.bytedigger\\x",
        str(repo / ".bytedigger" / "x"), str(repo / "sub" / ".bytedigger" / "x"),
    ]
    for p in owned:
        assert eo.is_engine_owned_path(p, root) is True, p
    assert eo.is_engine_owned_path(Path(".bytedigger") / "x", repo) is True
    # positive control on the other side: user content is not owned
    assert eo.is_engine_owned_path("src/a.py", root) is False
    assert eo.is_engine_owned_path(str(repo / "src" / "a.py"), root) is False


def test_ac1_non_matches_are_not_owned(tmp_path) -> None:
    """AC1: explicit non-matches of spec 2.1 (segment equality, not substring/prefix)."""
    eo = _eo()
    repo = tmp_path / "repo"
    (repo / "real").mkdir(parents=True)
    (repo / "real" / "f.py").write_text("x = 1\n", encoding="utf-8")
    os.symlink(str(repo / "real" / "f.py"), str(repo / "inside_link.py"))
    os.symlink(str(repo / "real"), str(repo / "inside_dir"))
    root = str(repo)
    for p in (".bytedigger-sessions.json", "src/bytedigger/x.py", "my.bytedigger/x",
              "bytedigger/x", "src/a.py", "inside_link.py", "inside_dir/f.py"):
        assert eo.is_engine_owned_path(p, root) is False, p
    # positive control: the same call does flag real engine state
    assert eo.is_engine_owned_path(".bytedigger/x", root) is True


def test_ac1_r2_escaping_symlinks_owned(tmp_path) -> None:
    """AC1 R2: a symlink (itself, or a parent component) resolving outside root is owned; a dangling
    symlink is owned; an in-root symlink is not."""
    eo = _eo()
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = _outside(tmp_path)
    (outside / "realvenv").mkdir()
    (outside / "realvenv" / "lib.py").write_text("y = 2\n", encoding="utf-8")
    os.symlink(str(outside / "secret.txt"), str(repo / "link_file"))
    os.symlink(str(outside / "realvenv"), str(repo / "venv"))
    os.symlink(str(outside / "does-not-exist"), str(repo / "dangling_out"))
    os.symlink(str(repo / "does-not-exist"), str(repo / "dangling_in"))
    (repo / "ok.py").write_text("z = 3\n", encoding="utf-8")
    root = str(repo)
    for p in ("link_file", "venv", "venv/lib.py", "dangling_out", "dangling_in"):
        assert eo.is_engine_owned_path(p, root) is True, p
    # positive control: an ordinary file and a non-existent non-symlink are judged by R1 only
    assert eo.is_engine_owned_path("ok.py", root) is False
    assert eo.is_engine_owned_path("src/not-yet-written.py", root) is False


def test_ac1_empty_path_not_owned(tmp_path) -> None:
    """AC1: empty path -> False (and never raises); control: a real state path is True."""
    eo = _eo()
    assert eo.is_engine_owned_path("", str(tmp_path)) is False
    assert eo.is_engine_owned_path(".bytedigger/x", str(tmp_path)) is True


def test_ac1_host_override_honoured(tmp_path) -> None:
    """AC1: provider dirname '.zzstate' -> '.zzstate/x' owned and '.bytedigger/x' not; engine_state_dirname
    follows at call time (no import-time capture)."""
    eo = _eo()
    root = str(tmp_path)
    assert eo.engine_state_dirname() == ".bytedigger"
    _set_dirname(".zzstate")
    assert eo.engine_state_dirname() == ".zzstate"
    assert eo.is_engine_owned_path(".zzstate/x", root) is True
    assert eo.is_engine_owned_path("sub/.zzstate/x", root) is True
    assert eo.is_engine_owned_path(".bytedigger/x", root) is False
    assert eo.is_engine_owned_path("src/a.py", root) is False


@pytest.mark.parametrize("bad", [RuntimeError, ValueError, "", 123, None], ids=lambda b: repr(b))
def test_ac1_provider_failure_degrades_never_raises(bad, tmp_path) -> None:
    """AC1/s4: provider raising or returning a non-str / empty value -> neutral '.bytedigger', no raise."""
    eo = _eo()
    _set_dirname(bad)
    assert eo.engine_state_dirname() == ".bytedigger"
    assert eo.is_engine_owned_path(".bytedigger/x", str(tmp_path)) is True
    assert eo.is_engine_owned_path("src/a.py", str(tmp_path)) is False
    assert eo.engine_owned_pathspecs() == [":(exclude).bytedigger", ":(exclude)**/.bytedigger/**"]


# ---------------------------------------------------------------------------
# AC2 - drop_engine_owned
# ---------------------------------------------------------------------------

def test_ac2_drop_preserves_order_and_emits_one_event(tmp_path) -> None:
    """AC2: order-preserving filter; exactly one engine_owned_paths_dropped event {step, n_dropped, paths}."""
    eo = _eo()
    log = _install_log()
    paths = ["src/b.py", ".bytedigger/z", "src/a.py", ".bytedigger/a", "docs/c.md"]
    out = eo.drop_engine_owned(paths, str(tmp_path), step="commit_manifest")
    assert out == ["src/b.py", "src/a.py", "docs/c.md"]
    events = log.of(EVENT_TYPE)
    assert len(events) == 1, log.events
    ev = events[0]
    assert ev["step"] == "commit_manifest"
    assert ev["n_dropped"] == 2
    assert ev["paths"] == [".bytedigger/a", ".bytedigger/z"]


def test_ac2_event_paths_sorted_and_capped(tmp_path) -> None:
    """AC2: payload paths = sorted(dropped)[:20] while n_dropped counts all; kept paths are returned."""
    eo = _eo()
    log = _install_log()
    dropped = [".bytedigger/f%02d" % i for i in range(25)]
    out = eo.drop_engine_owned(list(reversed(dropped)) + ["keep.py"], str(tmp_path), step="s")
    assert out == ["keep.py"]
    ev = log.of(EVENT_TYPE)[0]
    assert ev["n_dropped"] == 25
    assert ev["paths"] == sorted(dropped)[:20]
    assert len(ev["paths"]) == 20


def test_ac2_no_event_when_nothing_dropped(tmp_path) -> None:
    """AC2: nothing dropped -> no event; control: dropping something on the same log emits one."""
    eo = _eo()
    log = _install_log()
    assert eo.drop_engine_owned(["src/a.py", "src/b.py"], str(tmp_path), step="s") == ["src/a.py", "src/b.py"]
    assert eo.drop_engine_owned([], str(tmp_path), step="s") == []
    assert log.of(EVENT_TYPE) == []
    assert eo.drop_engine_owned([".bytedigger/x"], str(tmp_path), step="s") == []
    assert len(log.of(EVENT_TYPE)) == 1


def test_ac2_emitter_failure_does_not_propagate(tmp_path) -> None:
    """AC2/s4: an event log that raises is swallowed; the filtered list is still returned."""
    eo = _eo()
    _install_log(raises=True)
    out = eo.drop_engine_owned([".bytedigger/x", "src/a.py"], str(tmp_path), step="s")
    assert out == ["src/a.py"]


# ---------------------------------------------------------------------------
# AC3 - pathspecs and prune
# ---------------------------------------------------------------------------

def test_ac3_engine_owned_pathspecs_exact() -> None:
    """AC3: exact list for the default dirname."""
    assert _eo().engine_owned_pathspecs() == [":(exclude).bytedigger", ":(exclude)**/.bytedigger/**"]


def test_ac3_pathspecs_honour_host_override() -> None:
    """AC3: host override flows into both pathspecs at call time."""
    eo = _eo()
    _set_dirname(".zzstate")
    assert eo.engine_owned_pathspecs() == [":(exclude).zzstate", ":(exclude)**/.zzstate/**"]


def test_ac3_prune_engine_owned_dirs_in_place() -> None:
    """Spec 2.1: prune_engine_owned_dirs removes the state dirname from the SAME list (os.walk contract)."""
    eo = _eo()
    names = ["src", ".bytedigger", "docs", ".zzstate"]
    same = names
    assert eo.prune_engine_owned_dirs(names) is None
    assert names is same and names == ["src", "docs", ".zzstate"]
    _set_dirname(".zzstate")
    eo.prune_engine_owned_dirs(names)
    assert names == ["src", "docs"]


# ---------------------------------------------------------------------------
# AC4 - producers on a real tmp git repo
# ---------------------------------------------------------------------------

_KEY_SHAPED = "sk-ant-api03-" + "A" * 40


def _fresh_run_repo(tmp_path: Path):
    """Fresh-run shape: committed src/a.py; then untracked engine state, modified src/a.py,
    new src/b.py, and an escaping venv symlink. No .gitignore."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    _git(repo, "add", "src/a.py")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / ".bytedigger").mkdir()
    (repo / ".bytedigger" / "events.jsonl").write_text(_KEY_SHAPED + "\n", encoding="utf-8")
    (repo / ".bytedigger" / "state.py").write_text("S = 1\n", encoding="utf-8")
    (repo / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")
    realvenv = tmp_path / "outside" / "realvenv"
    realvenv.mkdir(parents=True)
    (realvenv / "lib.py").write_text("L = 1\n", encoding="utf-8")
    os.symlink(str(realvenv), str(repo / "venv"))
    return repo, base_sha


def _phase5():
    return importlib.import_module("bytedigger_engine.workflows.phase_5_implement")


def _common():
    return importlib.import_module("bytedigger_engine.workflows.phase_workflows_common")


def _has_seg(p: str, seg: str) -> bool:
    return seg in p.replace("\\", "/").split("/")


def test_ac4_derive_green_paths_excludes_engine_state(tmp_path) -> None:
    """AC4: _derive_green_paths_from_git == [src/a.py, src/b.py]; today `.bytedigger/state.py` leaks
    (untracked dir is rglob-expanded and .bytedigger/events.jsonl is not .py but state.py is)."""
    eo = _eo()  # the shared predicate must exist (GREEN) - ties this to the new chokepoint
    assert callable(eo.is_engine_owned_path)
    repo, _ = _fresh_run_repo(tmp_path)
    assert _phase5()._derive_green_paths_from_git(str(repo)) == ["src/a.py", "src/b.py"]


def test_ac4_derive_security_lint_paths_excludes_engine_state_and_escaping_symlink(tmp_path) -> None:
    """AC4: the red_sha branch scan set holds src/a.py + src/b.py and neither .bytedigger/... (gitleaks
    would read the engine's own log) nor the escaping venv symlink."""
    _eo()
    repo, base_sha = _fresh_run_repo(tmp_path)
    paths, _flag = _phase5()._derive_security_lint_paths({"red_commit_sha": base_sha}, {}, str(repo))
    assert "src/a.py" in paths and "src/b.py" in paths, paths
    assert not any(_has_seg(p, ".bytedigger") for p in paths), paths
    assert not any(_has_seg(p, "venv") for p in paths), paths


def test_ac4_filter_gitignored_paths_drops_engine_state(tmp_path) -> None:
    """AC4: commit-manifest filter drops engine state even though it is not gitignored; one event."""
    _eo()
    repo, _ = _fresh_run_repo(tmp_path)
    log = _install_log()
    out = _common()._filter_gitignored_paths([".bytedigger/events.jsonl", "src/a.py"], str(repo))
    assert out == ["src/a.py"]
    ev = log.of(EVENT_TYPE)
    assert len(ev) == 1 and ev[0]["step"] == "commit_manifest" and ev[0]["paths"] == [".bytedigger/events.jsonl"]
    # control: nothing engine-owned -> unchanged
    assert _common()._filter_gitignored_paths(["src/a.py", "src/b.py"], str(repo)) == ["src/a.py", "src/b.py"]


def _function_node(module, name: str) -> ast.FunctionDef:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {module.__file__}")


def _called_names(fn: ast.AST) -> set:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_ac4_checkpoint_green_worktree_uses_shared_pathspecs() -> None:
    """AC4 (AST): `_checkpoint_green_worktree` passes engine_owned_pathspecs() to `git add -A` and holds
    no inline ':(exclude' literal any more. The two halves are coupled: the literal must be gone AND
    the shared call present, so deleting the literal alone does not satisfy it."""
    _eo()
    fn = _function_node(_phase5(), "_checkpoint_green_worktree")
    assert "engine_owned_pathspecs" in _called_names(fn)
    literals = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any(":(exclude" in s for s in literals), literals
    assert "add" in literals and "-A" in literals  # the add -A argv is still built here


def test_ac4_shared_pathspecs_keep_engine_state_unstaged(tmp_path) -> None:
    """AC4 (behavioural, real git): `git add -A -- . <engine_owned_pathspecs()>` stages user changes and
    leaves top-level and nested .bytedigger/ unstaged."""
    eo = _eo()
    repo, _ = _fresh_run_repo(tmp_path)
    (repo / "sub" / ".bytedigger").mkdir(parents=True)
    (repo / "sub" / ".bytedigger" / "n.txt").write_text("n\n", encoding="utf-8")
    (repo / "sub" / "keep.txt").write_text("k\n", encoding="utf-8")
    _git(repo, "add", "-A", "--", ".", *eo.engine_owned_pathspecs())
    staged = _git(repo, "diff", "--cached", "--name-only").splitlines()
    assert "src/a.py" in staged and "src/b.py" in staged and "sub/keep.txt" in staged, staged
    assert not any(_has_seg(p, ".bytedigger") for p in staged), staged


# ---------------------------------------------------------------------------
# AC5 - walkers
# ---------------------------------------------------------------------------

def test_ac5_harvest_codes_skips_engine_state(tmp_path) -> None:
    """AC5: E_BD94_PROBE only under .bytedigger/ (top-level and nested) is not harvested; control: a code
    in src/ is."""
    from bytedigger_engine import error_codes
    _eo()
    root = tmp_path / "tree"
    (root / ".bytedigger").mkdir(parents=True)
    (root / "sub" / ".bytedigger").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".bytedigger" / "p.py").write_text('X = "E_BD94_PROBE"\n', encoding="utf-8")
    (root / "sub" / ".bytedigger" / "q.py").write_text('X = "E_BD94_PROBE"\n', encoding="utf-8")
    (root / "src" / "ok.py").write_text('X = "E_BD94_OK"\n', encoding="utf-8")
    found = error_codes.harvest_codes(root)
    assert "E_BD94_OK" in found
    assert "E_BD94_PROBE" not in found


def test_ac5_harvest_exclude_dirs_has_no_literal_state_dir() -> None:
    """AC5: the literal '.bytedigger' left HARVEST_EXCLUDE_DIRS (the shared prune replaces it); '.hal-build'
    stays (HAL host dir, declared)."""
    from bytedigger_engine import error_codes
    _eo()
    assert ".bytedigger" not in error_codes.HARVEST_EXCLUDE_DIRS
    assert ".hal-build" in error_codes.HARVEST_EXCLUDE_DIRS


def test_ac5_harvest_codes_honours_host_override(tmp_path) -> None:
    """AC5: with the host dirname '.zzstate', a code only under .zzstate/ is not harvested (the old
    static literal could never do this); control: src/ code is."""
    from bytedigger_engine import error_codes
    _eo()
    root = tmp_path / "tree"
    (root / ".zzstate").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".zzstate" / "p.py").write_text('X = "E_BD94_ZZPROBE"\n', encoding="utf-8")
    (root / "src" / "ok.py").write_text('X = "E_BD94_OK"\n', encoding="utf-8")
    _set_dirname(".zzstate")
    found = error_codes.harvest_codes(root)
    assert "E_BD94_OK" in found
    assert "E_BD94_ZZPROBE" not in found


def _walk_tree(tmp_path: Path, state: str) -> Path:
    root = tmp_path / "plain"  # not a git repo: forces the walk branch
    (root / state).mkdir(parents=True)
    (root / "sub" / state).mkdir(parents=True)
    (root / "src").mkdir()
    (root / state / "events.jsonl").write_text(CYR + "\n", encoding="utf-8")
    (root / "sub" / state / "n.jsonl").write_text(CYR + "\n", encoding="utf-8")
    (root / "src" / "x.txt").write_text("ok\n", encoding="utf-8")
    return root


def test_ac5_cyrillic_tracked_files_walk_skips_engine_state(tmp_path) -> None:
    """AC5: the non-git walk fallback of cyrillic_scan.tracked_files does not list .bytedigger/ files
    (the engine's own log is not source); control: src/x.txt is listed."""
    from bytedigger_engine import cyrillic_scan
    _eo()
    root = _walk_tree(tmp_path, ".bytedigger")
    listed = cyrillic_scan.tracked_files(str(root))
    assert "src/x.txt" in listed
    assert not any(_has_seg(p, ".bytedigger") for p in listed), listed


def test_ac5_cyrillic_tracked_files_walk_honours_host_override(tmp_path) -> None:
    """AC5: host dirname '.zzstate' is pruned by the walk; control: src/x.txt is listed."""
    from bytedigger_engine import cyrillic_scan
    _eo()
    root = _walk_tree(tmp_path, ".zzstate")
    _set_dirname(".zzstate")
    listed = cyrillic_scan.tracked_files(str(root))
    assert "src/x.txt" in listed
    assert not any(_has_seg(p, ".zzstate") for p in listed), listed


# ---------------------------------------------------------------------------
# AC6 - lint on the real tree
# ---------------------------------------------------------------------------

def test_ac6_real_tree_passes_tree_scan_lint() -> None:
    """AC6: check(<real bytedigger_engine>, load_inventory()) == [] (every tree-scan site classified, no
    stale key, every 'filters' entry references a filter name)."""
    lint = _lint()
    assert lint.check(_engine_root(), lint.load_inventory()) == []


def test_ac6_producers_are_classified_filters() -> None:
    """AC6: the producers of spec 2.2 are 'filters' in the shipped inventory. For each (file, scope, kind)
    at least one inventory key exists and every such key is class 'filters'. Coupled to the real tree:
    the keys must also be live call sites (lint.call_sites), so a hand-written inventory entry for a
    site that does not exist cannot satisfy this."""
    lint = _lint()
    sites = lint.load_inventory()["sites"]
    live = set(lint.call_sites(_engine_root()))
    required = [
        ("workflows/phase_5_implement.py", "_derive_green_paths_from_git", "git-status"),
        ("workflows/phase_5_implement.py", "_derive_green_paths_from_git", "rglob"),
        ("workflows/phase_5_implement.py", "_derive_security_lint_paths", "git_diff_files"),
        ("workflows/phase_5_implement.py", "_checkpoint_green_worktree", "git-add"),
        ("error_codes.py", "harvest_codes", "os.walk"),
        ("cyrillic_scan.py", "tracked_files", "os.walk"),
    ]
    for path, qual, kind in required:
        prefix = f"{path}::{qual}::{kind}#"
        keys = [k for k in sites if k.startswith(prefix)]
        assert keys, f"no inventory key with prefix {prefix}"
        for k in keys:
            assert k in live, f"{k} is not a live call site"
            assert sites[k].get("class") == "filters", (k, sites[k])


# ---------------------------------------------------------------------------
# AC7 - lint catches the omission (synthetic package dir)
# ---------------------------------------------------------------------------
# Expected phrasing (loose): a site with no key -> problem containing the key plus one of
# NO_KEY_WORDS; a stale key -> key plus "stale"; a malformed entry -> key plus one of
# MALFORMED_WORDS; a 'filters' entry without filter call -> key plus "filter".

NO_KEY_WORDS = ("no key", "missing", "unkeyed", "not in inventory")
MALFORMED_WORDS = ("malformed", "note", "class", "invalid")

_K_WALK = "workflows/new_gate.py::scan::os.walk#0"
_K_STATUS = "workflows/new_gate.py::scan::git-status#0"

_SRC_UNFILTERED = (
    "import os\n"
    "from bytedigger_engine.lib.git_port import git_read\n\n"
    "def scan(root):\n"
    "    for _dp, _dn, _fn in os.walk(root):\n"
    "        pass\n"
    "    return git_read([\"status\", \"--porcelain\"], cwd=root)\n"
)
_SRC_FILTERED = (
    "import os\n"
    "from bytedigger_engine.lib.git_port import git_read\n"
    "from bytedigger_engine.lib.util.engine_owned import drop_engine_owned\n\n"
    "def scan(root):\n"
    "    for _dp, _dn, _fn in os.walk(root):\n"
    "        pass\n"
    "    out = git_read([\"status\", \"--porcelain\"], cwd=root)\n"
    "    return drop_engine_owned([], root, step=\"x\"), out\n"
)


def _syn_tree(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "bytedigger_engine"
    (root / "workflows").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "workflows" / "new_gate.py").write_text(source, encoding="utf-8")
    return root


def _inv(sites: dict) -> dict:
    return {"version": 1, "sites": sites}


def _entry(cls: str, note: str = "reviewed: reason") -> dict:
    return {"class": cls, "note": note}


def _problems_for(problems: list, key: str) -> list:
    return [p for p in problems if key in p]


def test_ac7_unkeyed_sites_redden(tmp_path) -> None:
    """AC7: new os.walk and git status sites with no inventory entry -> two problems naming the keys;
    control: the same tree with 'not-a-gate' entries is clean."""
    lint = _lint()
    root = _syn_tree(tmp_path, _SRC_UNFILTERED)
    assert lint.call_sites(root) == sorted([_K_WALK, _K_STATUS])
    problems = lint.check(root, _inv({}))
    assert len(problems) == 2, problems
    for key in (_K_WALK, _K_STATUS):
        hits = _problems_for(problems, key)
        assert hits and any(w in hits[0].lower() for w in NO_KEY_WORDS), (key, problems)
    assert lint.check(root, _inv({_K_WALK: _entry("not-a-gate"), _K_STATUS: _entry("not-a-gate")})) == []


def test_ac7_filters_without_filter_call_redden(tmp_path) -> None:
    """AC7: 'filters' entries whose function never references a FILTER_NAMES member -> two problems, each
    naming its key and 'filter'."""
    lint = _lint()
    root = _syn_tree(tmp_path, _SRC_UNFILTERED)
    problems = lint.check(root, _inv({_K_WALK: _entry("filters"), _K_STATUS: _entry("filters")}))
    assert len(problems) == 2, problems
    for key in (_K_WALK, _K_STATUS):
        hits = _problems_for(problems, key)
        assert hits and "filter" in hits[0].lower(), (key, problems)


def test_ac7_filter_call_in_function_is_clean() -> None:
    """AC7: FILTER_NAMES is the spec's set (pinned), and a drop_engine_owned call in the function makes
    'filters' entries clean."""
    lint = _lint()
    assert set(lint.FILTER_NAMES) == {
        "is_engine_owned_path", "drop_engine_owned", "engine_owned_pathspecs",
        "prune_engine_owned_dirs", "_filter_gitignored_paths",
    }


def test_ac7_filter_call_in_function_makes_filters_entries_clean(tmp_path) -> None:
    """AC7: with a drop_engine_owned call in the enclosing function, 'filters' entries -> []; the same
    entries on the unfiltered source are reported (two-sided)."""
    lint = _lint()
    inv = _inv({_K_WALK: _entry("filters"), _K_STATUS: _entry("filters")})
    assert lint.check(_syn_tree(tmp_path / "ok", _SRC_FILTERED), inv) == []
    assert lint.check(_syn_tree(tmp_path / "bad", _SRC_UNFILTERED), inv) != []


def test_ac7_stale_key_reddens(tmp_path) -> None:
    """AC7: an inventory key with no call site is reported with 'stale'; live keys are not."""
    lint = _lint()
    root = _syn_tree(tmp_path, _SRC_UNFILTERED)
    stale = "workflows/new_gate.py::gone::os.walk#0"
    inv = _inv({_K_WALK: _entry("not-a-gate"), _K_STATUS: _entry("not-a-gate"), stale: _entry("not-a-gate")})
    problems = lint.check(root, inv)
    assert len(problems) == 1, problems
    assert stale in problems[0] and "stale" in problems[0].lower()


def test_ac7_malformed_entries_redden(tmp_path) -> None:
    """AC7: 'not-a-gate' with an empty note, and an unknown class, are malformed; the well-formed sibling
    entry is not reported."""
    lint = _lint()
    root = _syn_tree(tmp_path, _SRC_UNFILTERED)
    p1 = lint.check(root, _inv({_K_WALK: _entry("not-a-gate", note=""), _K_STATUS: _entry("not-a-gate")}))
    assert len(p1) == 1 and _K_WALK in p1[0] and any(w in p1[0].lower() for w in MALFORMED_WORDS), p1
    p2 = lint.check(root, _inv({_K_WALK: _entry("bogus"), _K_STATUS: _entry("not-a-gate")}))
    assert len(p2) == 1 and _K_WALK in p2[0] and any(w in p2[0].lower() for w in MALFORMED_WORDS), p2
    assert not any(_K_STATUS in p for p in p1 + p2)


# ---------------------------------------------------------------------------
# AC8 / AC9
# ---------------------------------------------------------------------------

def test_ac8_filter_gitignored_paths_calls_drop_engine_owned() -> None:
    """AC8 (AST pin for its FILTER_NAMES membership): the body of `_filter_gitignored_paths` calls
    drop_engine_owned, and the call precedes the git check-ignore read (spec 2.2-3: first, then
    check-ignore on the remainder)."""
    _eo()
    fn = _function_node(_common(), "_filter_gitignored_paths")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    def _name(c: ast.Call) -> str:
        f = c.func
        return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")

    drop = [c for c in calls if _name(c) == "drop_engine_owned"]
    reads = [c for c in calls if _name(c) == "git_read"]
    assert drop, "drop_engine_owned is not called in _filter_gitignored_paths"
    assert reads, "fixture precondition: the check-ignore git_read call is still there"
    assert min(c.lineno for c in drop) < min(c.lineno for c in reads)


def test_ac9_class_i_lint_unchanged_on_real_tree() -> None:
    """AC9 guard (MAY ALREADY PASS before GREEN): class_i_lint.check(real tree) == []. It protects the
    post-GREEN invariant that engine_owned.py adds no file-read site needing a class-I key."""
    lint = importlib.import_module("bytedigger_engine.conformance.class_i_lint")
    assert lint.check(_engine_root(), lint.load_inventory()) == []
