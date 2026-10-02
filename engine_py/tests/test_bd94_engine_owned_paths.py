"""bd#94 RED (spec r2): engine-owned paths are excluded from tree/manifest gates by construction.

Spec: docs/decisions/2026-10-02-bd94-engine-owned-paths.md (r2); gate r1 findings F1-F6, M1-M8 in
docs/decisions/2026-10-02-bd94-gate-r1.md.

AC -> test map
--------------
AC1  predicate: test_ac1_r1_* (state dir forms, `..`, outside root, root under .bytedigger ancestor,
     root alias), test_ac1_is_engine_state_path_is_r1_only, test_ac1_r2_* (content_scan True/False),
     test_ac1_content_scan_is_required_keyword, test_ac1_host_override_bdstate,
     test_ac1_invalid_dirname_degrades
AC2  drop_engine_owned: order, one event {step,n_dropped,content_scan,paths}, cap, no event, emitter raises
AC3  pathspecs + prune (bdstate override)
AC4  producers on a real tmp git repo (derive_green_paths with pkg/ links, security lint, manifest filter,
     checkpoint called for real, clean-only-state repo, _dirty_worktree_guard, AST pins for
     _verify_green_lint_rules / _verify_green_typecheck / _commit_fix_tests)
AC5  walkers (harvest_codes with non-dot "bdstate", HARVEST_EXCLUDE_DIRS, cyrillic walk, preflight AST)
AC6  tree_scan_lint on the real tree, producers "filters", every git-add key "filters"
AC7  one synthetic case per kind/form + controls, filters/no-filter-call, git-add must filter, stale,
     malformed, security/ scanned and conformance/ not
AC8  _filter_gitignored_paths calls drop_engine_owned(content_scan=False) (AST)
AC9  class_i_lint on the real tree (guard: MAY ALREADY PASS before GREEN)

Harness: real tmp git repos (no git mocks); events captured through the telemetry slot that the engine's
emission writes to; host override = config-provider factory swap restored by an autouse fixture
(workflows.md 1i, no timing). New modules are imported lazily inside tests; where a producer test calls
_eo() first, its behavioural assertion ALSO fails today with the module merely added (comment per test).
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

    def steps(self) -> set:
        return {p.get("step") for p in self.of(EVENT_TYPE)}


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


def _has_seg(p: str, seg: str) -> bool:
    return seg in p.replace("\\", "/").split("/")


def _outside(tmp_path: Path) -> Path:
    out = tmp_path / "outside"
    out.mkdir(exist_ok=True)
    (out / "secret.txt").write_text("x\n", encoding="utf-8")
    (out / "x.py").write_text("OUT = 1\n", encoding="utf-8")
    (out / "realvenv").mkdir(exist_ok=True)
    (out / "realvenv" / "lib.py").write_text("L = 1\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# AC1 - predicate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scan", [False, True], ids=["content_scan_off", "content_scan_on"])
def test_ac1_r1_state_dir_forms_owned(scan, tmp_path) -> None:
    """AC1 R1: state dir at any depth, ./ and backslash forms, `src/../.bytedigger/x` (normpath), Path
    objects, absolute under root; user paths are not owned (positive and negative in one test)."""
    eo = _eo()
    repo = tmp_path / "repo"
    repo.mkdir()
    root = str(repo)
    owned = [
        ".bytedigger", ".bytedigger/events.jsonl", "sub/.bytedigger/x", "a/b/.bytedigger/c/d",
        "./.bytedigger/x", ".bytedigger\\x", "sub\\.bytedigger\\x", "src/../.bytedigger/x",
        str(repo / ".bytedigger" / "x"), str(repo / "sub" / ".bytedigger" / "x"),
    ]
    for p in owned:
        assert eo.is_engine_owned_path(p, root, content_scan=scan) is True, p
    assert eo.is_engine_owned_path(Path(".bytedigger") / "x", repo, content_scan=scan) is True
    for p in ("src/a.py", str(repo / "src" / "a.py")):
        assert eo.is_engine_owned_path(p, root, content_scan=scan) is False, p


def test_ac1_non_matches_are_not_owned(tmp_path) -> None:
    """AC1: spec non-matches, incl. `.bytedigger/../src/a.py` (normpath -> src/a.py) and `..` escapes."""
    eo = _eo()
    repo = tmp_path / "repo"
    (repo / "real").mkdir(parents=True)
    (repo / "real" / "f.py").write_text("x = 1\n", encoding="utf-8")
    os.symlink(str(repo / "real" / "f.py"), str(repo / "inside_link.py"))
    os.symlink(str(repo / "real"), str(repo / "inside_dir"))
    root = str(repo)
    for scan in (False, True):
        for p in (".bytedigger-sessions.json", "src/bytedigger/x.py", "my.bytedigger/x", "bytedigger/x",
                  "src/a.py", ".bytedigger/../src/a.py", "../.bytedigger/x", "..", "../x",
                  "inside_link.py", "inside_dir/f.py"):
            assert eo.is_engine_owned_path(p, root, content_scan=scan) is False, (p, scan)
        assert eo.is_engine_owned_path(".bytedigger/x", root, content_scan=scan) is True


def test_ac1_r1_absolute_path_outside_root_is_not_owned(tmp_path) -> None:
    """AC1/M3: an absolute path under neither root is False even with a .bytedigger segment above;
    control: the same name under the root is True."""
    eo = _eo()
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other" / ".bytedigger" / "x"
    for scan in (False, True):
        assert eo.is_engine_owned_path(str(other), str(repo), content_scan=scan) is False
        assert eo.is_engine_owned_path(str(repo / ".bytedigger" / "x"), str(repo), content_scan=scan) is True


def test_ac1_r1_repo_under_state_dir_ancestor(tmp_path) -> None:
    """AC1/M3: a repo living under a `.bytedigger` ancestor (bd#93 run worktrees): R1 is root-relative,
    so ordinary paths are NOT owned (relative and absolute); state inside the repo still is."""
    eo = _eo()
    repo = tmp_path / ".bytedigger" / "runs" / "wt"
    repo.mkdir(parents=True)
    root = str(repo)
    for scan in (False, True):
        assert eo.is_engine_owned_path("src/a.py", root, content_scan=scan) is False
        assert eo.is_engine_owned_path(str(repo / "src" / "a.py"), root, content_scan=scan) is False
        assert eo.is_engine_owned_path(".bytedigger/x", root, content_scan=scan) is True
        assert eo.is_engine_owned_path(str(repo / ".bytedigger" / "x"), root, content_scan=scan) is True


def test_ac1_r1_root_alias_resolves(tmp_path) -> None:
    """AC1: an absolute path is made relative lexically OR against Path(root).resolve() (/var vs /private/var)."""
    eo = _eo()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    os.symlink(str(real), str(alias))
    assert eo.is_engine_owned_path(str(real / ".bytedigger" / "x"), str(alias), content_scan=False) is True
    assert eo.is_engine_owned_path(str(alias / ".bytedigger" / "x"), str(real), content_scan=False) is True
    assert eo.is_engine_owned_path(str(real / "src" / "a.py"), str(alias), content_scan=False) is False


def test_ac1_is_engine_state_path_is_r1_only(tmp_path) -> None:
    """AC1: is_engine_state_path is R1 only (no symlink logic, no content_scan keyword)."""
    eo = _eo()
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = _outside(tmp_path)
    os.symlink(str(outside / "secret.txt"), str(repo / "link_out"))
    root = str(repo)
    assert eo.is_engine_state_path(".bytedigger/x", root) is True
    assert eo.is_engine_state_path("src/../.bytedigger/x", root) is True
    assert eo.is_engine_state_path("link_out", root) is False
    assert eo.is_engine_state_path("", root) is False
    assert eo.is_engine_state_path("src/a.py", root) is False
    # the R2 half exists only behind content_scan=True
    assert eo.is_engine_owned_path("link_out", root, content_scan=True) is True


def _r2_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = _outside(tmp_path)
    os.symlink(str(outside / "secret.txt"), str(repo / "link_file"))
    os.symlink(str(outside / "realvenv"), str(repo / "venv"))
    os.symlink(str(outside / "does-not-exist"), str(repo / "dangling_out"))
    os.symlink(str(repo / "does-not-exist"), str(repo / "dangling_in"))
    os.symlink(str(repo / "loop_b"), str(repo / "loop_a"))
    os.symlink(str(repo / "loop_a"), str(repo / "loop_b"))
    (repo / "real").mkdir()
    (repo / "real" / "f.py").write_text("z = 1\n", encoding="utf-8")
    os.symlink(str(repo / "real" / "f.py"), str(repo / "inside_link"))
    (repo / "ok.py").write_text("z = 3\n", encoding="utf-8")
    return repo


_R2_CASES = ("link_file", "venv", "venv/lib.py", "dangling_out", "dangling_in", "loop_a", "loop_b")


def test_ac1_r2_escaping_symlinks_owned_with_content_scan(tmp_path) -> None:
    """AC1 R2 (content_scan=True): file link -> outside, dir-link parent, dangling, loop: owned; in-root
    link, ordinary file and a not-yet-written path: not owned."""
    eo = _eo()
    root = str(_r2_fixture(tmp_path))
    for p in _R2_CASES:
        assert eo.is_engine_owned_path(p, root, content_scan=True) is True, p
    for p in ("inside_link", "ok.py", "real/f.py", "src/not-yet-written.py"):
        assert eo.is_engine_owned_path(p, root, content_scan=True) is False, p


def test_ac1_r2_cases_are_not_owned_without_content_scan(tmp_path) -> None:
    """AC1/F3: the same R2-only cases with content_scan=False are NOT owned (a user's escaping or dangling
    link stays committable); control: R1 still owns state."""
    eo = _eo()
    root = str(_r2_fixture(tmp_path))
    for p in _R2_CASES:
        assert eo.is_engine_owned_path(p, root, content_scan=False) is False, p
    assert eo.is_engine_owned_path(".bytedigger/x", root, content_scan=False) is True


def test_ac1_content_scan_is_required_keyword(tmp_path) -> None:
    """AC1: content_scan omitted -> TypeError (every caller decides explicitly); positional is rejected too.
    Control: with the keyword the same call works."""
    eo = _eo()
    root = str(tmp_path)
    with pytest.raises(TypeError):
        eo.is_engine_owned_path("src/a.py", root)
    with pytest.raises(TypeError):
        eo.is_engine_owned_path("src/a.py", root, False)
    with pytest.raises(TypeError):
        eo.drop_engine_owned(["src/a.py"], root, step="s")
    assert eo.is_engine_owned_path("src/a.py", root, content_scan=False) is False


def test_ac1_empty_path_not_owned(tmp_path) -> None:
    """AC1: empty path -> False; control: a state path is True."""
    eo = _eo()
    for scan in (False, True):
        assert eo.is_engine_owned_path("", str(tmp_path), content_scan=scan) is False
        assert eo.is_engine_owned_path(".bytedigger/x", str(tmp_path), content_scan=scan) is True


def test_ac1_host_override_bdstate(tmp_path) -> None:
    """AC1/F5: provider dirname 'bdstate' (NOT dot-prefixed) -> 'bdstate/x' owned, '.bytedigger/x' not;
    engine_state_dirname follows at call time."""
    eo = _eo()
    root = str(tmp_path)
    assert eo.engine_state_dirname() == ".bytedigger"
    _set_dirname("bdstate")
    assert eo.engine_state_dirname() == "bdstate"
    for scan in (False, True):
        assert eo.is_engine_owned_path("bdstate/x", root, content_scan=scan) is True
        assert eo.is_engine_owned_path("sub/bdstate/x", root, content_scan=scan) is True
        assert eo.is_engine_owned_path(".bytedigger/x", root, content_scan=scan) is False
        assert eo.is_engine_owned_path("src/a.py", root, content_scan=scan) is False
    assert eo.is_engine_state_path("bdstate/x", root) is True


@pytest.mark.parametrize("bad", ["a/b", "a\\b", "", None, 123, "..", ".", RuntimeError],
                         ids=lambda b: repr(b))
def test_ac1_invalid_dirname_degrades(bad, tmp_path) -> None:
    """AC1/s4: provider raising or returning an invalid value (multi-segment, empty, non-str, '.', '..')
    degrades to '.bytedigger'; nothing raises."""
    eo = _eo()
    _set_dirname(bad)
    assert eo.engine_state_dirname() == ".bytedigger"
    assert eo.is_engine_owned_path(".bytedigger/x", str(tmp_path), content_scan=False) is True
    assert eo.is_engine_owned_path("src/a.py", str(tmp_path), content_scan=True) is False
    assert eo.engine_owned_pathspecs() == [":(exclude).bytedigger", ":(exclude)**/.bytedigger/**"]


# ---------------------------------------------------------------------------
# AC2 - drop_engine_owned
# ---------------------------------------------------------------------------

def test_ac2_drop_preserves_order_and_emits_one_event(tmp_path) -> None:
    """AC2: order-preserving; exactly one event {step, n_dropped, content_scan, paths}."""
    eo = _eo()
    log = _install_log()
    paths = ["src/b.py", ".bytedigger/z", "src/a.py", ".bytedigger/a", "docs/c.md"]
    out = eo.drop_engine_owned(paths, str(tmp_path), step="commit_manifest", content_scan=False)
    assert out == ["src/b.py", "src/a.py", "docs/c.md"]
    events = log.of(EVENT_TYPE)
    assert len(events) == 1, log.events
    ev = events[0]
    assert ev["step"] == "commit_manifest" and ev["n_dropped"] == 2 and ev["content_scan"] is False
    assert ev["paths"] == [".bytedigger/a", ".bytedigger/z"]


def test_ac2_content_scan_flag_drives_symlink_drop_and_payload(tmp_path) -> None:
    """AC2: content_scan=True also drops an escaping link and the event says so; =False keeps it."""
    eo = _eo()
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink(str(_outside(tmp_path) / "secret.txt"), str(repo / "link_out"))
    log = _install_log()
    assert eo.drop_engine_owned(["link_out", "a.py"], str(repo), step="s1", content_scan=False) == ["link_out", "a.py"]
    assert log.of(EVENT_TYPE) == []
    assert eo.drop_engine_owned(["link_out", "a.py"], str(repo), step="s2", content_scan=True) == ["a.py"]
    ev = log.of(EVENT_TYPE)
    assert len(ev) == 1 and ev[0]["content_scan"] is True and ev[0]["paths"] == ["link_out"]


def test_ac2_event_paths_sorted_and_capped(tmp_path) -> None:
    """AC2: payload paths = sorted(dropped)[:20] while n_dropped counts all."""
    eo = _eo()
    log = _install_log()
    dropped = [".bytedigger/f%02d" % i for i in range(25)]
    out = eo.drop_engine_owned(list(reversed(dropped)) + ["keep.py"], str(tmp_path), step="s", content_scan=False)
    assert out == ["keep.py"]
    ev = log.of(EVENT_TYPE)[0]
    assert ev["n_dropped"] == 25 and ev["paths"] == sorted(dropped)[:20] and len(ev["paths"]) == 20


def test_ac2_no_event_when_nothing_dropped(tmp_path) -> None:
    """AC2: nothing dropped -> no event; control: dropping on the same log emits one."""
    eo = _eo()
    log = _install_log()
    assert eo.drop_engine_owned(["src/a.py"], str(tmp_path), step="s", content_scan=True) == ["src/a.py"]
    assert eo.drop_engine_owned([], str(tmp_path), step="s", content_scan=False) == []
    assert log.of(EVENT_TYPE) == []
    assert eo.drop_engine_owned([".bytedigger/x"], str(tmp_path), step="s", content_scan=False) == []
    assert len(log.of(EVENT_TYPE)) == 1


def test_ac2_emitter_failure_does_not_propagate(tmp_path) -> None:
    """AC2/s4: an event log that raises is swallowed; the filtered list is still returned."""
    eo = _eo()
    _install_log(raises=True)
    assert eo.drop_engine_owned([".bytedigger/x", "src/a.py"], str(tmp_path), step="s", content_scan=False) == ["src/a.py"]


# ---------------------------------------------------------------------------
# AC3 - pathspecs and prune
# ---------------------------------------------------------------------------

def test_ac3_engine_owned_pathspecs_exact_and_override() -> None:
    """AC3: exact list for the default dirname; honours the 'bdstate' override."""
    eo = _eo()
    assert eo.engine_owned_pathspecs() == [":(exclude).bytedigger", ":(exclude)**/.bytedigger/**"]
    _set_dirname("bdstate")
    assert eo.engine_owned_pathspecs() == [":(exclude)bdstate", ":(exclude)**/bdstate/**"]


def test_ac3_prune_engine_owned_dirs_in_place() -> None:
    """AC3: prune_engine_owned_dirs removes the state dirname from the SAME list (os.walk contract)."""
    eo = _eo()
    names = ["src", ".bytedigger", "docs", "bdstate"]
    same = names
    assert eo.prune_engine_owned_dirs(names) is None
    assert names is same and names == ["src", "docs", "bdstate"]
    _set_dirname("bdstate")
    eo.prune_engine_owned_dirs(names)
    assert names == ["src", "docs"]


# ---------------------------------------------------------------------------
# AC4 - producers on a real tmp git repo
# ---------------------------------------------------------------------------

_KEY_SHAPED = "sk-ant-api03-" + "A" * 40


def _fresh_run_repo(tmp_path: Path):
    """Committed src/a.py; then untracked .bytedigger/{events.jsonl,state.py}, modified src/a.py, new src/b.py,
    escaping symlink venv, untracked pkg/ {m.py, link.py -> file outside, vend -> dir outside}. No .gitignore."""
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
    outside = _outside(tmp_path)
    os.symlink(str(outside / "realvenv"), str(repo / "venv"))
    (repo / "pkg").mkdir()
    (repo / "pkg" / "m.py").write_text("M = 1\n", encoding="utf-8")
    os.symlink(str(outside / "x.py"), str(repo / "pkg" / "link.py"))
    os.symlink(str(outside / "realvenv"), str(repo / "pkg" / "vend"))
    return repo, base_sha


def _only_state_repo(tmp_path: Path) -> Path:
    """Second repo whose ONLY dirt is untracked engine state."""
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / ".bytedigger").mkdir()
    (repo / ".bytedigger" / "events.jsonl").write_text(_KEY_SHAPED + "\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain") == "?? .bytedigger/"
    return repo


def _phase5():
    return importlib.import_module("bytedigger_engine.workflows.phase_5_implement")


def _common():
    return importlib.import_module("bytedigger_engine.workflows.phase_workflows_common")


def _fixint():
    return importlib.import_module("bytedigger_engine.workflows.phase_6_fix_integrity")


def _review6():
    return importlib.import_module("bytedigger_engine.workflows.phase_6_review")


def test_ac4_derive_green_paths_excludes_engine_state_and_escaping_links(tmp_path) -> None:
    """AC4/F6: == [pkg/m.py, src/a.py, src/b.py]; step 'derive_green_paths', content_scan True in the event.
    Behavioural RED today even with the module added: `.bytedigger/state.py` and `pkg/vend/lib.py` leak
    from the rglob expansion."""
    _eo()
    repo, _ = _fresh_run_repo(tmp_path)
    log = _install_log()
    assert _phase5()._derive_green_paths_from_git(str(repo)) == ["pkg/m.py", "src/a.py", "src/b.py"]
    evs = [e for e in log.of(EVENT_TYPE) if e.get("step") == "derive_green_paths"]
    assert evs and all(e["content_scan"] is True for e in evs), log.events


def test_ac4_derive_security_lint_paths_excludes_engine_state_and_escaping_links(tmp_path) -> None:
    """AC4: scan set holds src/a.py, src/b.py, pkg/m.py and no .bytedigger / venv segment, nothing under
    pkg/vend, not pkg/link.py; step 'security_lint_paths'. Behavioural RED today (ls-files --others
    returns .bytedigger/* and the links)."""
    _eo()
    repo, base_sha = _fresh_run_repo(tmp_path)
    log = _install_log()
    paths, _flag = _phase5()._derive_security_lint_paths({"red_commit_sha": base_sha}, {}, str(repo))
    for want in ("src/a.py", "src/b.py", "pkg/m.py"):
        assert want in paths, paths
    assert not any(_has_seg(p, ".bytedigger") or _has_seg(p, "venv") or _has_seg(p, "vend") for p in paths), paths
    assert "pkg/link.py" not in paths, paths
    evs = [e for e in log.of(EVENT_TYPE) if e.get("step") == "security_lint_paths"]
    assert evs and all(e["content_scan"] is True for e in evs), log.events


def test_ac4_filter_gitignored_paths_drops_state_keeps_escaping_user_link(tmp_path) -> None:
    """AC4/F3: commit manifest drops engine state but KEEPS an escaping user link (content_scan=False);
    step 'commit_manifest'. Behavioural RED today (state kept)."""
    _eo()
    repo, _ = _fresh_run_repo(tmp_path)
    log = _install_log()
    out = _common()._filter_gitignored_paths([".bytedigger/events.jsonl", "src/a.py", "pkg/link.py"], str(repo))
    assert out == ["src/a.py", "pkg/link.py"]
    evs = log.of(EVENT_TYPE)
    assert len(evs) == 1 and evs[0]["step"] == "commit_manifest" and evs[0]["content_scan"] is False
    assert evs[0]["paths"] == [".bytedigger/events.jsonl"]
    assert _common()._filter_gitignored_paths(["src/a.py", "src/b.py"], str(repo)) == ["src/a.py", "src/b.py"]


def test_ac4_checkpoint_green_worktree_real_call_excludes_engine_state(tmp_path, monkeypatch) -> None:
    """AC4/F4: _checkpoint_green_worktree called for real on the fresh-run repo (+ nested sub/.bytedigger).
    HEAD lists the user files, nothing under .bytedigger is in HEAD or the index, n_files excludes state
    (5: M src/a.py, ?? src/b.py, ?? venv, ?? pkg/, ?? sub/); step 'checkpoint_dirty'. Behavioural RED
    today: the inline exclude misses nested state and n_files counts `?? .bytedigger/` (6)."""
    eo = _eo()
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.com"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(k, v)
    repo, _ = _fresh_run_repo(tmp_path)
    (repo / "sub" / ".bytedigger").mkdir(parents=True)
    (repo / "sub" / ".bytedigger" / "n.txt").write_text("n\n", encoding="utf-8")
    (repo / "sub" / "keep.txt").write_text("k\n", encoding="utf-8")
    log = _install_log()
    res = _phase5()._checkpoint_green_worktree(str(repo), None, 1, "bd94", "cfg_git_cwd")
    assert res["outcome"] == "committed", res
    head_files = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    staged = _git(repo, "diff", "--cached", "--name-only").splitlines()
    for want in ("src/a.py", "src/b.py", "sub/keep.txt"):
        assert want in head_files, head_files
    assert not any(_has_seg(p, ".bytedigger") for p in head_files + staged), (head_files, staged)
    assert res["n_files"] == 5, res
    assert "checkpoint_dirty" in log.steps(), log.events
    assert eo.engine_owned_pathspecs()  # the shared pathspecs exist


def test_ac4_checkpoint_green_worktree_only_state_is_clean_no_commit(tmp_path, monkeypatch) -> None:
    """AC4/F2: a repo whose only dirt is `?? .bytedigger/` -> outcome 'clean', no new commit.
    Behavioural RED today: the dirty check counts state, so it 'commits' or errors instead of 'clean'."""
    _eo()
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.com"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(k, v)
    repo = _only_state_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    res = _phase5()._checkpoint_green_worktree(str(repo), None, 1, "bd94", "cfg_git_cwd")
    assert res["outcome"] == "clean", res
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "rev-list", "--count", "HEAD") == "1"


def test_ac4_checkpoint_source_holds_no_inline_exclude_literal() -> None:
    """AC4: no ':(exclude' string literal in _checkpoint_green_worktree and engine_owned_pathspecs is called."""
    _eo()
    fn = _function_node(_phase5(), "_checkpoint_green_worktree")
    literals = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any(":(exclude" in s for s in literals), literals
    assert "engine_owned_pathspecs" in _called_names(fn)


def test_ac4_dirty_worktree_guard_only_state_is_clean(tmp_path) -> None:
    """AC4/F2: _dirty_worktree_guard(git_cwd, cfg, scratchpad, pre_fix_sha, git_cwd_source) returns None
    (clean) when the only dirt is `?? .bytedigger/`; step 'dirty_guard'. Two-sided: real dirt still gives
    E_FIX_UNCOMMITTED_CHANGES, naming the real path and not the state. Behavioural RED today: the guard
    returns E_FIX_UNCOMMITTED_CHANGES for state-only dirt."""
    _eo()
    repo = _only_state_repo(tmp_path)
    log = _install_log()
    assert _fixint()._dirty_worktree_guard(repo) is None
    assert "dirty_guard" in log.steps(), log.events
    (repo / "c.py").write_text("C = 1\n", encoding="utf-8")
    res = _fixint()._dirty_worktree_guard(repo)
    assert res is not None and res.error_code == "E_FIX_UNCOMMITTED_CHANGES"
    assert "c.py" in res.error and ".bytedigger" not in res.error


def _call_kw_const(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return "<absent>"


def test_ac4_verify_green_lint_and_typecheck_filter_with_content_scan_true() -> None:
    """AC4 (AST, M5): _verify_green_lint_rules and _verify_green_typecheck call drop_engine_owned with
    content_scan=True and the pinned step names; _detect_green_complete_resume with content_scan=False."""
    _eo()
    mod = _phase5()
    want = {
        "_verify_green_lint_rules": ("green_lint_paths", True),
        "_verify_green_typecheck": ("green_typecheck_paths", True),
        "_detect_green_complete_resume": ("green_resume_paths", False),
    }
    for fname, (step, scan) in want.items():
        fn = _function_node(mod, fname)
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and _call_name(c) == "drop_engine_owned"]
        assert calls, f"{fname} does not call drop_engine_owned"
        assert any(_call_kw_const(c, "content_scan") is scan and _call_kw_const(c, "step") == step
                   for c in calls), (fname, [ast.dump(c) for c in calls])


def test_ac4_commit_fix_tests_routes_through_filter_gitignored_paths() -> None:
    """AC4 (AST, M1): _commit_fix_tests passes test_paths through _filter_gitignored_paths before git add."""
    _eo()
    fn = _function_node(_review6(), "_commit_fix_tests")
    assert "_filter_gitignored_paths" in _called_names(fn)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _function_node(module, name: str):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {module.__file__}")


def _call_name(c: ast.Call) -> str:
    f = c.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _called_names(fn: ast.AST) -> set:
    return {_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}


# ---------------------------------------------------------------------------
# AC5 - walkers
# ---------------------------------------------------------------------------

def test_ac5_harvest_codes_skips_non_dot_host_state_dir(tmp_path) -> None:
    """AC5/F5: with the host dirname 'bdstate' (not dot-prefixed, so scan_roots.is_pruned_dir does NOT
    prune it), E_BD94_PROBE only under bdstate/ is not harvested; control E_BD94_OK in src/x.py is.
    Behavioural RED today even with the module added: bdstate is walked."""
    from bytedigger_engine import error_codes
    _eo()
    root = tmp_path / "tree"
    (root / "bdstate").mkdir(parents=True)
    (root / "sub" / "bdstate").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "bdstate" / "p.py").write_text('X = "E_BD94_PROBE"\n', encoding="utf-8")
    (root / "sub" / "bdstate" / "q.py").write_text('X = "E_BD94_PROBE2"\n', encoding="utf-8")
    (root / "src" / "x.py").write_text('X = "E_BD94_OK"\n', encoding="utf-8")
    _set_dirname("bdstate")
    found = error_codes.harvest_codes(root)
    assert "E_BD94_OK" in found
    assert "E_BD94_PROBE" not in found and "E_BD94_PROBE2" not in found


def test_ac5_harvest_exclude_dirs_has_no_literal_state_dir() -> None:
    """AC5: '.bytedigger' is not in HARVEST_EXCLUDE_DIRS (the shared prune replaces it); '.hal-build' stays.
    Behavioural RED today: the literal is there."""
    from bytedigger_engine import error_codes
    _eo()
    assert ".bytedigger" not in error_codes.HARVEST_EXCLUDE_DIRS
    assert ".hal-build" in error_codes.HARVEST_EXCLUDE_DIRS


def _walk_tree(tmp_path: Path, state: str) -> Path:
    root = tmp_path / "plain"  # not a git repo: forces the walk branch
    (root / state).mkdir(parents=True)
    (root / "sub" / state).mkdir(parents=True)
    (root / "src").mkdir()
    (root / state / "events.jsonl").write_text(CYR + "\n", encoding="utf-8")
    (root / "sub" / state / "n.jsonl").write_text(CYR + "\n", encoding="utf-8")
    (root / "src" / "x.md").write_text("ok\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("state,override", [(".bytedigger", None), ("bdstate", "bdstate")],
                         ids=["default", "bdstate_override"])
def test_ac5_cyrillic_tracked_files_walk_skips_engine_state(state, override, tmp_path) -> None:
    """AC5: the non-git walk fallback does not list state-dir files (also nested); control src/x.md listed.
    Behavioural RED today: _WALK_SKIP_DIRS has no state-dir rule."""
    from bytedigger_engine import cyrillic_scan
    _eo()
    root = _walk_tree(tmp_path, state)
    if override:
        _set_dirname(override)
    listed = cyrillic_scan.tracked_files(str(root))
    assert "src/x.md" in listed
    assert not any(_has_seg(p, state) for p in listed), listed


def test_ac5_preflight_untracked_producers_call_drop_engine_owned() -> None:
    """AC5 (AST): every function in preflight.py that reads `ls-files ... --others` calls drop_engine_owned
    (step 'preflight_untracked', content_scan False); at least two such producers exist (:216, :355)."""
    _eo()
    path = _engine_root() / "preflight.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    producers = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        has_others = False
        for c in ast.walk(fn):
            if isinstance(c, ast.Call):
                toks = [a.value for a in c.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if "ls-files" in toks and "--others" in toks:
                    has_others = True
        if has_others:
            producers.append(fn)
    assert len(producers) >= 2, [p.name for p in producers]
    for fn in producers:
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and _call_name(c) == "drop_engine_owned"]
        assert calls, f"{fn.name} lists untracked files without drop_engine_owned"
        assert any(_call_kw_const(c, "step") == "preflight_untracked"
                   and _call_kw_const(c, "content_scan") is False for c in calls), fn.name


# ---------------------------------------------------------------------------
# AC6 - lint on the real tree
# ---------------------------------------------------------------------------

def test_ac6_real_tree_passes_tree_scan_lint() -> None:
    """AC6: check(<real bytedigger_engine>, load_inventory()) == []."""
    lint = _lint()
    assert lint.check(_engine_root(), lint.load_inventory()) == []


def test_ac6_producers_are_classified_filters() -> None:
    """AC6: the producers of spec 2.2 (1-8, 11) are 'filters' and are live call sites; for each
    (file, scope, kind) at least one key exists and all such keys are 'filters'."""
    lint = _lint()
    sites = lint.load_inventory()["sites"]
    live = set(lint.call_sites(_engine_root()))
    p5 = "workflows/phase_5_implement.py"
    required = [
        (p5, "_derive_green_paths_from_git", "git-status"),
        (p5, "_derive_green_paths_from_git", "rglob"),
        (p5, "_derive_security_lint_paths", "git_diff_files"),
        (p5, "_verify_green_lint_rules", "git_diff_files"),
        (p5, "_verify_green_typecheck", "git_diff_files"),
        (p5, "_detect_green_complete_resume", "git_diff_files"),
        (p5, "_checkpoint_green_worktree", "git-status"),
        (p5, "_checkpoint_green_worktree", "git-add"),
        ("workflows/phase_6_fix_integrity.py", "_dirty_worktree_guard", "git-status"),
        ("error_codes.py", "harvest_codes", "os.walk"),
        ("cyrillic_scan.py", "tracked_files", "os.walk"),
        ("preflight.py", "compute_state_hash", "git-ls-files"),
    ]
    for path, qual, kind in required:
        prefix = f"{path}::{qual}::{kind}#"
        keys = [k for k in sites if k.startswith(prefix)]
        assert keys, f"no inventory key with prefix {prefix}"
        for k in keys:
            assert k in live, f"{k} is not a live call site"
            assert sites[k].get("class") == "filters", (k, sites[k])
    pre = [k for k in sites if k.startswith("preflight.py::") and "::git-ls-files#" in k]
    assert len(pre) >= 2 and all(sites[k].get("class") == "filters" for k in pre), pre


def test_ac6_every_git_add_key_is_filters() -> None:
    """AC6/M1: every git-add key in the shipped inventory is 'filters'; call_sites sees them (non-vacuous)."""
    lint = _lint()
    sites = lint.load_inventory()["sites"]
    live_adds = [k for k in lint.call_sites(_engine_root()) if "::git-add#" in k]
    assert len(live_adds) >= 3, live_adds
    for k in live_adds:
        assert k in sites and sites[k].get("class") == "filters", (k, sites.get(k))


# ---------------------------------------------------------------------------
# AC7 - lint catches the omission (synthetic package dir)
# ---------------------------------------------------------------------------
# Expected phrasing: "no key" + key; "stale" + key; "malformed" + key;
# "filters without filter call" + key; "git-add must filter" + key.

# (function name, body, expected key kind)
_FORMS = [
    ("f_walk", "for _ in os.walk(r):\n        pass", "os.walk"),
    ("f_scandir", "return os.scandir(r)", "os.scandir"),
    ("f_listdir", "return os.listdir(r)", "os.listdir"),
    ("f_glob", "return glob.glob(r)", "glob.glob"),
    ("f_rglob", "return Path(r).rglob(\"*\")", "rglob"),
    ("f_iterdir", "return Path(r).iterdir()", "iterdir"),
    ("f_gdf", "return git_diff_files(sha, r)", "git_diff_files"),
    ("f_sp", "return x.status_porcelain()", "status_porcelain"),
    ("f_gstatus", "return git_read([\"status\", \"--porcelain\"], cwd=r)", "git-status"),
    ("f_dashC", "return run([\"git\", \"-C\", r, \"status\"])", "git-status"),
    ("f_nolocks", "return run([\"git\", \"--no-optional-locks\", \"ls-files\"])", "git-ls-files"),
    ("f_varargs", "return _git(r, \"ls-files\", \"--others\")", "git-ls-files"),
    ("f_add", "return run([\"git\", \"add\", \"--\"] + r)", "git-add"),
    ("f_names", "return run([\"git\", \"diff\", \"--name-only\", sha])", "git-diff-names"),
    ("f_ctl_diff", "return run([\"git\", \"diff\", sha])", None),
    ("f_ctl_revparse", "return run([\"git\", \"rev-parse\", \"HEAD\"])", None),
]
_EXPECTED_KEYS = sorted(f"workflows/new_gate.py::{fn}::{kind}#0" for fn, _b, kind in _FORMS if kind)
_K_ADD = "workflows/new_gate.py::f_add::git-add#0"


def _forms_source(filtered: bool) -> str:
    out = ["import glob", "import os", "from pathlib import Path", ""]
    for fn, body, _kind in _FORMS:
        out.append(f"def {fn}(r, sha=None, x=None):")
        if filtered:
            out.append("    drop_engine_owned([], r, step=\"x\", content_scan=False)")
        out.append("    " + body)
        out.append("")
    return "\n".join(out)


def _syn_tree(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "bytedigger_engine"
    (root / "workflows").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "workflows" / "new_gate.py").write_text(source, encoding="utf-8")
    return root


def _inv(sites: dict) -> dict:
    return {"version": 1, "sites": sites}


def _ent(cls: str, note: str = "reviewed: reason") -> dict:
    return {"class": cls, "note": note}


def _all(cls: str) -> dict:
    return {k: _ent(cls) for k in _EXPECTED_KEYS}


def test_ac7_call_sites_one_per_kind_and_form_and_controls_are_not_sites(tmp_path) -> None:
    """AC7/F1: one synthetic function per kind/form is a site with its exact key; the two controls
    (content `git diff`, `git rev-parse`) are not sites."""
    lint = _lint()
    root = _syn_tree(tmp_path, _forms_source(False))
    assert lint.call_sites(root) == _EXPECTED_KEYS
    assert not any("f_ctl_" in k for k in lint.call_sites(root))


def test_ac7_unkeyed_sites_redden_one_problem_each(tmp_path) -> None:
    """AC7: no inventory -> one 'no key' problem per site naming its exact key, none for the controls;
    control: with every site classified 'not-a-gate' the tree is clean except the git-add rule."""
    lint = _lint()
    root = _syn_tree(tmp_path, _forms_source(False))
    problems = lint.check(root, _inv({}))
    assert len(problems) == len(_EXPECTED_KEYS), problems
    for key in _EXPECTED_KEYS:
        hits = [p for p in problems if key in p]
        assert len(hits) == 1 and "no key" in hits[0].lower(), (key, problems)
    assert not any("f_ctl_" in p for p in problems)
    inv = _all("not-a-gate")
    inv[_K_ADD] = _ent("filters")
    # filters on f_add without a filter call is still reported; everything else is clean
    only = lint.check(root, _inv(inv))
    assert len(only) == 1 and _K_ADD in only[0], only


def test_ac7_filters_without_filter_call_redden(tmp_path) -> None:
    """AC7: 'filters' entries whose function never references a FILTER_NAMES member -> 'filters without
    filter call' naming each key."""
    lint = _lint()
    root = _syn_tree(tmp_path, _forms_source(False))
    problems = lint.check(root, _inv(_all("filters")))
    assert len(problems) == len(_EXPECTED_KEYS), problems
    for key in _EXPECTED_KEYS:
        hits = [p for p in problems if key in p]
        assert hits and "filters without filter call" in hits[0].lower(), (key, problems)


def test_ac7_filter_call_in_function_makes_filters_entries_clean(tmp_path) -> None:
    """AC7: with a drop_engine_owned call in each function, 'filters' entries -> []; FILTER_NAMES is the
    spec's set (pinned); the same entries on the unfiltered source are reported (two-sided)."""
    lint = _lint()
    assert set(lint.FILTER_NAMES) == {
        "drop_engine_owned", "is_engine_owned_path", "is_engine_state_path",
        "engine_owned_pathspecs", "prune_engine_owned_dirs", "_filter_gitignored_paths",
    }
    assert lint.check(_syn_tree(tmp_path / "ok", _forms_source(True)), _inv(_all("filters"))) == []
    assert lint.check(_syn_tree(tmp_path / "bad", _forms_source(False)), _inv(_all("filters"))) != []


def test_ac7_git_add_must_filter(tmp_path) -> None:
    """AC7/M1: a git-add site marked 'not-a-gate' -> 'git-add must filter' naming the key (even with a
    filter call present); the other not-a-gate sites are fine."""
    lint = _lint()
    root = _syn_tree(tmp_path, _forms_source(True))
    inv = _all("not-a-gate")
    problems = lint.check(root, _inv(inv))
    assert len(problems) == 1, problems
    assert _K_ADD in problems[0] and "git-add must filter" in problems[0].lower()
    inv[_K_ADD] = _ent("filters")
    assert lint.check(root, _inv(inv)) == []


def test_ac7_stale_key_reddens(tmp_path) -> None:
    """AC7: an inventory key with no call site is reported with 'stale'; live keys are not."""
    lint = _lint()
    root = _syn_tree(tmp_path, _forms_source(True))
    inv = _all("filters")
    stale = "workflows/new_gate.py::gone::os.walk#0"
    inv[stale] = _ent("not-a-gate")
    problems = lint.check(root, _inv(inv))
    assert len(problems) == 1, problems
    assert stale in problems[0] and "stale" in problems[0].lower()


def test_ac7_malformed_entries_redden(tmp_path) -> None:
    """AC7: 'not-a-gate' with an empty note, and an unknown class, are malformed; siblings are not reported."""
    lint = _lint()
    root = _syn_tree(tmp_path, _forms_source(True))
    k_walk = "workflows/new_gate.py::f_walk::os.walk#0"
    inv = _all("filters")
    inv[k_walk] = _ent("not-a-gate", note="")
    p1 = lint.check(root, _inv(inv))
    assert len(p1) == 1 and k_walk in p1[0] and "malformed" in p1[0].lower(), p1
    inv[k_walk] = _ent("bogus")
    p2 = lint.check(root, _inv(inv))
    assert len(p2) == 1 and k_walk in p2[0] and "malformed" in p2[0].lower(), p2


def test_ac7_security_is_scanned_conformance_is_not(tmp_path) -> None:
    """AC7/F1: a module under security/ is scanned; one under conformance/ (and tests/) is not."""
    lint = _lint()
    root = tmp_path / "bytedigger_engine"
    src = "import os\n\ndef g(r):\n    return list(os.walk(r))\n"
    for sub in ("security", "conformance", "tests", "workflows"):
        (root / sub).mkdir(parents=True)
        (root / sub / "m.py").write_text(src, encoding="utf-8")
    (root / "top.py").write_text(src, encoding="utf-8")
    assert lint.call_sites(root) == sorted([
        "security/m.py::g::os.walk#0", "workflows/m.py::g::os.walk#0", "top.py::g::os.walk#0",
    ])


# ---------------------------------------------------------------------------
# AC8 / AC9
# ---------------------------------------------------------------------------

def test_ac8_filter_gitignored_paths_calls_drop_engine_owned_content_scan_false() -> None:
    """AC8 (AST): the body of `_filter_gitignored_paths` calls drop_engine_owned with content_scan=False,
    and the call precedes the git check-ignore read (spec 2.2-5)."""
    _eo()
    fn = _function_node(_common(), "_filter_gitignored_paths")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    drop = [c for c in calls if _call_name(c) == "drop_engine_owned"]
    reads = [c for c in calls if _call_name(c) == "git_read"]
    assert drop, "drop_engine_owned is not called in _filter_gitignored_paths"
    assert all(_call_kw_const(c, "content_scan") is False for c in drop), [ast.dump(c) for c in drop]
    assert reads, "fixture precondition: the check-ignore git_read call is still there"
    assert min(c.lineno for c in drop) < min(c.lineno for c in reads)


def test_ac9_class_i_lint_unchanged_on_real_tree() -> None:
    """AC9 guard (MAY ALREADY PASS before GREEN): class_i_lint.check(real tree) == []; GREEN must keep it
    green (engine_owned.py adds no file-read site needing a class-I key)."""
    lint = importlib.import_module("bytedigger_engine.conformance.class_i_lint")
    assert lint.check(_engine_root(), lint.load_inventory()) == []
