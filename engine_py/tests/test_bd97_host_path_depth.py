"""RED tests for bd#97 — import-time fixed-depth ancestor indexing.

`engine_py/workflows/phase_6_smoke.py:25` binds

    HAL_DIR = Path(__file__).resolve().parents[5]

at import time. Six ancestors exist only in the upstream HAL build tree
(`~/.claude/SYSTEM/cli/build/engine_py/workflows/`, 9 ancestors). On a clean
bytedigger clone the package sits shallower, `parents[5]` raises IndexError, the
import of `workflows/__init__.py` dies at L20, and 15 of the 16 test modules
fail at collection. Two further sites of the same class:
`tests/test_F3A8F4FC_phase12_sonnet_downgrade.py:44` (module level — the 16th
collection error) and `tests/test_GH375_tier_model_dispatch.py:663` (in a
function, so it raises when the test runs, before that test's own portability
`pytest.skip` guard can fire).

The fix may not tune the ancestor count: the identical module ships in both
trees, so any fixed N is wrong in one of them. Resolution must be anchored on
what the tree actually contains — see engine_py/lib/tree_root.py in GREEN.

Expected pre-GREEN failures (deep tree, e.g. the dev worktree at 8 ancestors):
  AC1  FAIL — parents[5] still evaluated at module level in phase_6_smoke.py
  AC2  FAIL — phase_6_smoke has no `_resolve_hal_dir`
  AC3  FAIL — marker anchoring not implemented
  AC3b FAIL — marker-outranks-.git precedence not implemented
  AC3c FAIL — .git branch not implemented
  AC3d FAIL — clamped package-root branch not implemented
  AC4  PASS — regression guard for the upstream HAL_DIR/SMOKE_SCRIPT contract,
              in any tree deep enough to import the module at all
  AC6  FAIL — HAL_DIR is not produced by a `_resolve_hal_dir` call
  AC7  FAIL — HAL_DIR still varies with the tree's depth
  AC8  FAIL — site 2 still computes its own fixed-depth index
  AC9  FAIL — site 3 still computes its own fixed-depth index
  AC10 FAIL — no `_resolve_hal_dir` to delegate to lib/tree_root.py

That is 11F/1P here in a deep tree. In a SHALLOW tree the ACs that import the
module (AC2, AC3*, AC4, AC6, AC10) fail via an explicit `pytest.fail` on the
IndexError instead, giving 12F/0P. Either way: assertion failures, never a
collection error — no test here imports `phase_6_smoke` at module scope.

AC5a/AC5b/AC5c (the tree-wide lint rules that close the boundary-lint gap) live
in tests/test_engine_path_closure.py, the file already designated as the lint
for this class: 2F/5P pre-GREEN there, so 13F/6P across both files.
"""
from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_PY = HERE.parent
sys.path.insert(0, str(ENGINE_PY))

_P6S_SRC = ENGINE_PY / "bytedigger_engine" / "workflows" / "phase_6_smoke.py"
_SITE2_SRC = HERE / "test_F3A8F4FC_phase12_sonnet_downgrade.py"
_SITE3_SRC = HERE / "test_GH375_tier_model_dispatch.py"

_SMOKE_REL = "USER/skills/HALForge/tests/phase-6-signal-smoke.sh"

# Ancestor indexes that can walk out of the package root. parents[0]/[1] stay
# inside engine_py/<subdir>/ for every layout and are not part of this class.
_UNSAFE_INDEX = 3

# Guards against AC5a being satisfied by deleting a line or a whole file.
_SITE2_TEST_FUNCS = 10
_SITE3_TEST_FUNCS = 19
# The sole consumer of each site's resolved path. Pinning the count alone lets a
# GREEN delete the consumer and add a dummy to keep the floor.
_SITE2_GUARDED_TEST = "test_ac1_models_json_has_discovery_and_explore_keys"
_SITE3_GUARDED_TEST = "test_ac12_hal_models_json_has_model_by_tier_simple_sonnet"

_MODELS_REL = ("SHARED", "config", "models.json")


# ─── AST helpers ─────────────────────────────────────────────────────────────


class _ParentsIndexes(ast.NodeVisitor):
    """Collect `<expr>.parents[<int>]` subscripts, tagged import-time or not.

    Function and lambda bodies are deferred, so only depth == 0 is import-time.
    Class bodies and `try:` bodies DO execute at import, hence no special case
    for them — that is what blocks a `try/except IndexError` cheat.
    """

    def __init__(self) -> None:
        self.deferred = 0
        self.hits: list[tuple[int, int, bool]] = []

    def _visit_deferred(self, node: ast.AST) -> None:
        self.deferred += 1
        self.generic_visit(node)
        self.deferred -= 1

    visit_FunctionDef = _visit_deferred
    visit_AsyncFunctionDef = _visit_deferred
    visit_Lambda = _visit_deferred

    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            self.hits.append((node.lineno, node.slice.value, self.deferred == 0))
        self.generic_visit(node)


def _parents_indexes(path: Path) -> list[tuple[int, int, bool]]:
    visitor = _ParentsIndexes()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.hits


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _toplevel_assignment(tree: ast.Module, name: str) -> ast.expr | None:
    """The module-level value expression assigned to `name`, if any."""
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    return None


def _test_func_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _func_node(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _called_func_names(node: ast.AST) -> set[str]:
    """Names of every function called anywhere inside `node`."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# ─── module loading (never at import scope) ──────────────────────────────────


def _load_phase_6_smoke():
    """Import the module, converting the bd#97 crash into an assertion failure."""
    try:
        return importlib.import_module("bytedigger_engine.workflows.phase_6_smoke")
    except IndexError as exc:  # the bd#97 symptom itself
        pytest.fail(
            "bd#97: importing phase_6_smoke raised "
            f"IndexError({exc}) — the module still assumes a fixed nesting "
            f"depth. Source: {_P6S_SRC}"
        )


def _resolver():
    mod = _load_phase_6_smoke()
    resolver = getattr(mod, "_resolve_hal_dir", None)
    assert callable(resolver), (
        "phase_6_smoke must expose a callable `_resolve_hal_dir(start: Path)` "
        "that resolves the install root from tree content, not ancestor "
        f"arithmetic. Found: {resolver!r}"
    )
    return mod, resolver


def _synthetic_start(extra_dirs: int) -> Path:
    """Absolute phase_6_smoke.py path with `extra_dirs` levels above engine_py."""
    prefix = "".join(f"/lvl{i}" for i in range(extra_dirs))
    return Path(f"{prefix}/engine_py/workflows/phase_6_smoke.py")


def _plant_marker(root: Path) -> Path:
    script = root / _SMOKE_REL
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env zsh\nSTATUS=PASS\n")
    return script


def _require_no_git_above(tmp_path: Path) -> None:
    """The .git and clamp branches are only observable outside a checkout.

    Under `--basetemp` (or PYTEST_DEBUG_TEMPROOT) pointing inside a repo, the
    .git branch would win from an ancestor we did not plant and these tests
    would false-fail. Assert the precondition instead of inheriting it.
    """
    intruders = [str(p) for p in tmp_path.parents if (p / ".git").exists()]
    assert not intruders, (
        "test precondition: tmp_path must not sit inside a git checkout, else "
        f"the .git branch resolves to an ancestor we did not plant: {intruders}"
    )


def _plant_models_json(root: Path) -> Path:
    """A synthetic checkout root carrying SHARED/config/models.json."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    models = root.joinpath(*_MODELS_REL)
    models.parent.mkdir(parents=True, exist_ok=True)
    models.write_text("{}")
    return models


# ─── AC1: no import-time fixed-depth ancestor index in phase_6_smoke ─────────


def test_ac1_phase_6_smoke_has_no_import_time_depth_assumption():
    """AC1: phase_6_smoke.py evaluates no parents[N>=3] at module level.

    PRE-GREEN FAILURE: `HAL_DIR = Path(__file__).resolve().parents[5]` at L25.
    """
    offenders = [
        (lineno, idx)
        for lineno, idx, import_time in _parents_indexes(_P6S_SRC)
        if import_time and idx >= _UNSAFE_INDEX
    ]
    assert not offenders, (
        "import-time fixed-depth ancestor index in "
        f"{_P6S_SRC.relative_to(ENGINE_PY)}: "
        + ", ".join(f"L{ln}: parents[{idx}]" for ln, idx in offenders)
        + ". IndexErrors on any checkout shallower than the upstream HAL build "
        "tree (bd#97). Resolve from tree content, not by counting ancestors."
    )


# ─── AC2: the resolver survives every depth, clamp included ──────────────────


def test_ac2_resolver_never_raises_at_any_depth():
    """AC2: `_resolve_hal_dir(start)` returns an absolute Path for start paths
    with 0…9 ancestors.

    The 0-, 1- and 2-ancestor inputs are the only ones where the
    empty-ancestors guard and the clamp are what stand between the resolver and
    a fresh IndexError, so they are exercised explicitly rather than assumed
    unreachable.

    PRE-GREEN FAILURE: phase_6_smoke exposes no `_resolve_hal_dir`.
    """
    _, resolver = _resolver()

    starts = [
        Path("/"),  # no ancestors at all
        Path("/phase_6_smoke.py"),
        Path("/lvl0/phase_6_smoke.py"),
    ]
    starts += [_synthetic_start(extra) for extra in range(0, 7)]

    for start in starts:
        got = resolver(start)
        assert isinstance(got, Path), (
            f"_resolve_hal_dir({start}) returned {got!r}, expected a Path "
            f"(start has {len(start.parents)} ancestors)"
        )
        assert got.is_absolute(), (
            f"_resolve_hal_dir({start}) returned non-absolute {got}"
        )


# ─── AC3: marker anchoring — depth-independent, preserves the HAL answer ─────


def test_ac3_resolver_anchors_on_the_smoke_script_at_any_depth(tmp_path):
    """AC3: when an ancestor actually carries the smoke script, that ancestor is
    returned — whatever the nesting depth between it and the module.

    This is the HAL-parity guarantee: upstream `~/.claude` carries
    USER/skills/HALForge/tests/, so HAL_DIR keeps resolving to `~/.claude` and
    upstream's `test_smoke_script_path_resolves_to_existing_file` — which
    asserts SMOKE_SCRIPT.exists() and cannot be mirrored locally, since
    bytedigger ships no USER/ tree — keeps passing.

    PRE-GREEN FAILURE: phase_6_smoke exposes no `_resolve_hal_dir`.
    """
    _, resolver = _resolver()

    root = tmp_path / "install_root"
    _plant_marker(root)

    # Upstream HAL nesting and a shallower one must give the same answer.
    for nesting in ("SYSTEM/cli/build/engine_py/workflows", "engine_py/workflows"):
        start = root / nesting / "phase_6_smoke.py"
        assert resolver(start) == root, (
            f"_resolve_hal_dir({start}) must anchor on the ancestor carrying "
            f"{_SMOKE_REL} ({root}), got {resolver(start)}"
        )


def test_ac3b_marker_outranks_an_intermediate_git(tmp_path):
    """AC3b: a `.git` BELOW the marker root must not win.

    A submodule or nested checkout at `…/build/` would otherwise make the
    resolver return a directory that does not carry the smoke script, breaking
    the upstream `SMOKE_SCRIPT.exists()` contract. This pins the branch order.

    PRE-GREEN FAILURE: phase_6_smoke exposes no `_resolve_hal_dir`.
    """
    _, resolver = _resolver()

    root = tmp_path / "install_root"
    _plant_marker(root)
    nested = root / "SYSTEM" / "cli" / "build"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / ".git").mkdir()

    start = nested / "engine_py" / "workflows" / "phase_6_smoke.py"
    got = resolver(start)
    assert got == root, (
        f"marker root {root} must outrank the nested .git at {nested}; "
        f"got {got}. With .git first, SMOKE_SCRIPT would not exist."
    )


def test_ac3c_resolver_falls_back_to_the_checkout_root(tmp_path):
    """AC3c: no marker anywhere → the first ancestor carrying `.git`.

    This is the bytedigger clone / worktree case, where today's parents[5]
    silently returns an unrelated directory well above the repo.

    PRE-GREEN FAILURE: phase_6_smoke exposes no `_resolve_hal_dir`.
    """
    _require_no_git_above(tmp_path)
    _, resolver = _resolver()

    root = tmp_path / "clone_root"
    (root / "engine_py" / "workflows").mkdir(parents=True)
    (root / ".git").write_text("gitdir: /elsewhere\n")  # worktree-style .git FILE

    start = root / "engine_py" / "workflows" / "phase_6_smoke.py"
    got = resolver(start)
    assert got == root, (
        f"with no marker, the checkout root {root} must be returned (a "
        f"worktree .git is a FILE, not a dir); got {got}"
    )


def test_ac3d_resolver_falls_back_to_the_clamped_package_root(tmp_path):
    """AC3d: neither marker nor `.git` → the engine_py package root.

    This is the docker `git archive` tarball from the issue: no .git at all.
    A degenerate resolver that returns Path("/") would pass AC2 and AC3; this
    pins the actual value.

    PRE-GREEN FAILURE: phase_6_smoke exposes no `_resolve_hal_dir`.
    """
    _require_no_git_above(tmp_path)
    _, resolver = _resolver()

    root = tmp_path / "tarball_root"
    pkg = root / "engine_py"
    (pkg / "workflows").mkdir(parents=True)

    start = pkg / "workflows" / "phase_6_smoke.py"
    got = resolver(start)
    assert got == pkg, (
        f"with neither marker nor .git, the package root {pkg} must be "
        f"returned; got {got}"
    )


# ─── AC4: regression guard — upstream import contract is preserved ──────────


def test_ac4_hal_dir_and_smoke_script_contract_preserved():
    """AC4 REGRESSION GUARD: HAL_DIR/SMOKE_SCRIPT stay module attributes and
    HAL_DIR stays a real directory. Upstream
    `tests/test_phase_6_smoke.py::test_smoke_script_path_resolves_to_existing_file`
    does `from bytedigger_engine.workflows.phase_6_smoke import HAL_DIR, SMOKE_SCRIPT` and asserts
    `HAL_DIR.is_dir()`.

    The `.exists()` half of that contract cannot be asserted here — bytedigger
    ships no USER/ tree — so AC3/AC3b carry it instead.

    Expected to PASS pre- and post-GREEN in any tree deep enough to import.
    """
    mod = _load_phase_6_smoke()
    hal_dir = getattr(mod, "HAL_DIR", None)
    smoke_script = getattr(mod, "SMOKE_SCRIPT", None)
    assert isinstance(hal_dir, Path), f"HAL_DIR must stay a Path, got {hal_dir!r}"
    assert isinstance(smoke_script, Path), (
        f"SMOKE_SCRIPT must stay a Path, got {smoke_script!r}"
    )
    assert hal_dir.is_dir(), f"HAL_DIR should be a directory: {hal_dir}"
    assert smoke_script == hal_dir / mod._SMOKE_REL, (
        f"SMOKE_SCRIPT ({smoke_script}) must stay HAL_DIR / _SMOKE_REL "
        f"({hal_dir / mod._SMOKE_REL})"
    )


# ─── AC6: HAL_DIR is actually PRODUCED by the resolver ──────────────────────


def test_ac6_hal_dir_is_wired_to_the_resolver():
    """AC6: the module-level HAL_DIR binding is a call to `_resolve_hal_dir`,
    and its value equals what that resolver returns for this module's own path.

    Without this, the likeliest accidental GREEN passes everything else: add a
    correct `_resolve_hal_dir`, then leave HAL_DIR bound through a deferred
    legacy helper (`def _legacy(): return ….parents[5]`). AC1 sees no
    module-level subscript, AC2/AC3 exercise the new resolver, AC4 accepts the
    legacy value in any deep tree — and the clean clone stays broken.

    Both halves are required: the equality alone can hold by coincidence in a
    marker-carrying tree, and the AST check alone does not prove the value is
    used.

    PRE-GREEN FAILURE: HAL_DIR is bound from `Path(__file__).resolve().parents[5]`.
    """
    mod, resolver = _resolver()

    expected = resolver(Path(mod.__file__).resolve())
    assert mod.HAL_DIR == expected, (
        f"HAL_DIR ({mod.HAL_DIR}) must be what _resolve_hal_dir returns for "
        f"this module's own path ({expected}) — the resolver exists but is not "
        "the source of the bound value."
    )

    binding = _toplevel_assignment(_module_tree(_P6S_SRC), "HAL_DIR")
    assert binding is not None, "phase_6_smoke must bind HAL_DIR at module level"
    assert isinstance(binding, ast.Call) and isinstance(binding.func, ast.Name) and (
        binding.func.id == "_resolve_hal_dir"
    ), (
        "the module-level HAL_DIR binding must be a direct call to "
        f"_resolve_hal_dir; AST shows {ast.dump(binding)[:160]}"
    )


# ─── AC7: behavioural depth-invariance — the issue's own acceptance test ────


# The module set phase_6_smoke needs to import standalone.
_IMPORT_CLOSURE = (
    "contracts.py",
    "lib/worktree_root.py",
    "lib/bounded_spawn.py",
    "workflows/phase_6_smoke.py",
)


def _materialise(engine_root: Path) -> None:
    """Copy phase_6_smoke's import closure into a fresh engine_py root.

    bd#44: the closure lands under `<engine_root>/bytedigger_engine/`, mirroring
    the real layout. Package `__init__.py` files are planted EMPTY on purpose —
    the real `workflows/__init__.py` registers all 21 phases and would drag in
    the whole engine, defeating the point of a four-file closure.
    """
    members = list(_IMPORT_CLOSURE)
    # tree_root.py only exists post-GREEN; copy it when present so the same
    # helper works either side of the fix.
    if (ENGINE_PY / "bytedigger_engine" / "lib" / "tree_root.py").is_file():
        members.append("lib/tree_root.py")
    pkg = engine_root / "bytedigger_engine"
    for rel in members:
        src = ENGINE_PY / "bytedigger_engine" / rel
        dst = pkg / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    for init in (pkg, pkg / "lib", pkg / "workflows"):
        init.mkdir(parents=True, exist_ok=True)
        (init / "__init__.py").write_text("")


def _import_hal_dir(engine_root: Path) -> subprocess.CompletedProcess:
    # bd#44: only the package's PARENT goes on the path. Adding the moved
    # subdirs back would make `workflows`/`lib` resolve as top-level names —
    # the very layout this release removes.
    code = (
        "import json, sys\n"
        f"sys.path[:0] = [{str(engine_root)!r}]\n"
        "import bytedigger_engine.workflows.phase_6_smoke as m\n"
        "print(json.dumps({'hal_dir': str(m.HAL_DIR)}))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(engine_root),
    )


def test_ac7_import_is_depth_invariant_in_a_real_subprocess(tmp_path):
    """AC7: importing phase_6_smoke must succeed AND resolve HAL_DIR to its own
    package root, from two trees planted at DIFFERENT depths.

    This is the issue's acceptance criterion executed rather than reasoned
    about. Neither tmp tree carries a marker or a `.git`, so the correct answer
    is each copy's own engine_py root; today's fixed index instead walks out of
    the copy entirely and returns a different directory for each depth.

    PRE-GREEN FAILURE: HAL_DIR is a tmp-path ancestor, not <copy>/engine_py,
    and differs between the two depths.
    """
    _require_no_git_above(tmp_path)
    roots = {
        "shallow": tmp_path / "a" / "engine_py",
        "deep": tmp_path / "a" / "b" / "c" / "d" / "engine_py",
    }
    for label, engine_root in roots.items():
        _materialise(engine_root)
        proc = _import_hal_dir(engine_root)
        assert proc.returncode == 0, (
            f"[{label}] importing phase_6_smoke from {engine_root} exited "
            f"{proc.returncode}\nSTDERR:\n{proc.stderr[-2000:]}"
        )
        got = Path(json.loads(proc.stdout.strip())["hal_dir"])
        # bd#44: "the copy's own package root" is now the package directory
        # itself. The AC is depth-INVARIANCE, and it is still exactly that:
        # both depths must answer with their own root, whatever the depth.
        expected = engine_root / "bytedigger_engine"
        assert got == expected, (
            f"[{label}] HAL_DIR resolved to {got}, expected the copy's own "
            f"package root {expected}. The value still depends on how deep "
            "the tree happens to sit."
        )


# ─── AC8 / AC9: the other two sites keep their semantics ───────────────────


def test_ac8_site2_resolves_repo_root_without_depth_arithmetic(tmp_path):
    """AC8: tests/test_F3A8F4FC_phase12_sonnet_downgrade.py:44 — REAL_CONFIG_PATH
    must come from a `resolve_tree_root` call, and that resolution must land on
    <repo root>/SHARED/config/models.json for a checkout root.

    Also pins the file's test inventory AND the name of REAL_CONFIG_PATH's only
    consumer, so AC5a cannot be satisfied by deleting the offending line (which
    would turn 10 upstream assertions into permanent skips), the whole file, or
    the one test that reads the path while padding the count with a dummy.

    PRE-GREEN FAILURE: the value is still `Path(__file__).resolve().parents[5] / …`.
    """
    _require_no_git_above(tmp_path)
    tree = _module_tree(_SITE2_SRC)

    names = _test_func_names(tree)
    assert len(names) >= _SITE2_TEST_FUNCS, (
        f"{_SITE2_SRC.name} must keep its {_SITE2_TEST_FUNCS} test functions; "
        f"found {len(names)}: {sorted(names)}"
    )
    assert _SITE2_GUARDED_TEST in names, (
        f"{_SITE2_SRC.name} must keep {_SITE2_GUARDED_TEST} — the only reader "
        "of REAL_CONFIG_PATH; without it the resolution is dead code and the "
        "inventory floor can be met with a dummy"
    )

    binding = _toplevel_assignment(tree, "REAL_CONFIG_PATH")
    assert binding is not None, "REAL_CONFIG_PATH must stay a module-level name"
    assert "resolve_tree_root" in _called_func_names(binding), (
        "REAL_CONFIG_PATH must be built from a resolve_tree_root(...) call "
        f"instead of ancestor arithmetic; AST shows {ast.dump(binding)[:200]}"
    )

    offenders = [
        (lineno, idx)
        for lineno, idx, _ in _parents_indexes(_SITE2_SRC)
        if idx >= _UNSAFE_INDEX
    ]
    assert not offenders, (
        f"{_SITE2_SRC.name} still indexes a fixed ancestor depth: "
        + ", ".join(f"L{ln}: parents[{idx}]" for ln, idx in offenders)
    )

    # The resolution itself, on a synthetic checkout root.
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415 — post-GREEN module

    root = tmp_path / "repo"
    (root / "engine_py" / "tests").mkdir(parents=True)
    models = _plant_models_json(root)

    start = root / "engine_py" / "tests" / _SITE2_SRC.name
    got = resolve_tree_root(start).joinpath(*_MODELS_REL)
    assert got == models, f"expected {models}, got {got}"


def test_ac9_site3_guarded_test_no_longer_raises_before_its_skip(tmp_path):
    """AC9: tests/test_GH375_tier_model_dispatch.py:663 — the in-function
    parents[5] runs BEFORE that test's own `pytest.skip` guard, so on the clean
    clone this issue targets it raises IndexError instead of skipping. Collection
    is green there but the run is not.

    Removing the index is necessary but NOT sufficient: `parents[1]`, `Path.cwd()`
    or any other non-raising expression also satisfies "no fixed depth" while
    pointing `models_json` somewhere that does not exist — which converts a live
    upstream assertion on `claude.model_by_tier.SIMPLE` into a permanent silent
    skip. So the replacement is pinned positively, exactly as AC8 pins site 2:
    the guarded function must call `resolve_tree_root`, and that resolution must
    land on <root>/SHARED/config/models.json for a real checkout root.

    PRE-GREEN FAILURE: `repo_root = Path(__file__).resolve().parents[5]` at L663.
    """
    _require_no_git_above(tmp_path)
    tree = _module_tree(_SITE3_SRC)

    names = _test_func_names(tree)
    assert _SITE3_GUARDED_TEST in names, (
        f"{_SITE3_SRC.name} must keep {_SITE3_GUARDED_TEST}"
    )
    assert len(names) >= _SITE3_TEST_FUNCS, (
        f"{_SITE3_SRC.name} must keep its {_SITE3_TEST_FUNCS} test functions; "
        f"found {len(names)}: {sorted(names)}"
    )

    offenders = [
        (lineno, idx)
        for lineno, idx, _ in _parents_indexes(_SITE3_SRC)
        if idx >= _UNSAFE_INDEX
    ]
    assert not offenders, (
        f"{_SITE3_SRC.name} still indexes a fixed ancestor depth (raises before "
        "its own portability skip guard on a shallow clone): "
        + ", ".join(f"L{ln}: parents[{idx}]" for ln, idx in offenders)
    )

    guarded = _func_node(tree, _SITE3_GUARDED_TEST)
    assert guarded is not None, f"{_SITE3_GUARDED_TEST} must stay a top-level test"
    assert "resolve_tree_root" in _called_func_names(guarded), (
        f"{_SITE3_GUARDED_TEST} must derive its repo root from a "
        "resolve_tree_root(...) call. Any other non-raising expression "
        "(parents[1], Path.cwd(), a literal) silently turns this test into a "
        "permanent skip instead of restoring the assertion it guards."
    )

    # The resolution itself, on a synthetic checkout root.
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415 — post-GREEN module

    root = tmp_path / "repo"
    (root / "engine_py" / "tests").mkdir(parents=True)
    models = _plant_models_json(root)

    start = root / "engine_py" / "tests" / _SITE3_SRC.name
    got = resolve_tree_root(start).joinpath(*_MODELS_REL)
    assert got == models, f"expected {models}, got {got}"


# ─── AC10: the wrapper delegates instead of duplicating the branch logic ────


def test_ac10_resolve_hal_dir_delegates_to_the_shared_resolver():
    """AC10: `_resolve_hal_dir` must call `resolve_tree_root`.

    Reimplementing the marker/.git/clamp branches inside phase_6_smoke while
    also shipping lib/tree_root.py for sites 2 and 3 passes every other AC and
    leaves two copies of the same logic free to drift — the failure mode that
    produced this bug's neighbours in the first place.

    PRE-GREEN FAILURE: phase_6_smoke declares no `_resolve_hal_dir`.
    """
    tree = _module_tree(_P6S_SRC)
    node = _func_node(tree, "_resolve_hal_dir")
    assert node is not None, (
        "phase_6_smoke must declare `_resolve_hal_dir` at module level"
    )
    assert "resolve_tree_root" in _called_func_names(node), (
        "_resolve_hal_dir must delegate to lib/tree_root.resolve_tree_root "
        "rather than reimplementing the branch logic; called names: "
        f"{sorted(_called_func_names(node))}"
    )
