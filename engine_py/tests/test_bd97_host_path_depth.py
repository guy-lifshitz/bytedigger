"""Tests for bd#97 — import-time fixed-depth ancestor indexing.

The original bug was `HAL_DIR = Path(__file__).resolve().parents[5]` at import time
in `workflows/phase_6_smoke.py`. bd#89 P1 deleted that module, so the ACs anchored on
it (AC1, AC4, AC6, AC7, AC10) were retired with it; AC2, AC3, AC3b, AC3c, AC3d are
ported to call `lib/tree_root.resolve_tree_root` directly.

What stays is the shared resolver `lib/tree_root.resolve_tree_root` and the two other
sites of the same class, exercised directly:
  AC8  `tests/test_F3A8F4FC_phase12_sonnet_downgrade.py:44` (module level)
  AC9  `tests/test_GH375_tier_model_dispatch.py:663` (in a function, so it raises
       when the test runs, before that test's own portability `pytest.skip` guard)

The fix may not tune the ancestor count: the identical module ships in both trees, so
any fixed N is wrong in one of them. Resolution must be anchored on what the tree
actually contains — see engine_py/lib/tree_root.py.

AC5a/AC5b/AC5c (the tree-wide lint rules that close the boundary-lint gap) live
in tests/test_engine_path_closure.py, the file already designated as the lint
for this class.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_PY = HERE.parent
sys.path.insert(0, str(ENGINE_PY))

_SITE2_SRC = HERE / "test_F3A8F4FC_phase12_sonnet_downgrade.py"
_SITE3_SRC = HERE / "test_GH375_tier_model_dispatch.py"

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


# ─── AC2 / AC3*: lib/tree_root.resolve_tree_root semantics, called directly ──
# (ported from the retired phase_6_smoke._resolve_hal_dir cases; guard kept behavior)

_MARKER = "MARKER_BD97.txt"


def _synthetic_start(extra_dirs: int) -> Path:
    prefix = "".join(f"/lvl{i}" for i in range(extra_dirs))
    return Path(f"{prefix}/engine_py/workflows/module.py")


def test_ac2_resolver_never_raises_at_any_depth():
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415

    starts = [Path("/"), Path("/module.py"), Path("/lvl0/module.py")]
    starts += [_synthetic_start(extra) for extra in range(0, 7)]
    for start in starts:
        for marker in (None, _MARKER):
            got = resolve_tree_root(start, marker=marker)
            assert isinstance(got, Path) and got.is_absolute(), (start, marker, got)


def test_ac3_resolver_anchors_on_the_marker_at_any_depth(tmp_path):
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415

    root = tmp_path / "install_root"
    root.mkdir()
    (root / _MARKER).write_text("x")
    for nesting in ("SYSTEM/cli/build/engine_py/workflows", "engine_py/workflows"):
        start = root / nesting / "module.py"
        assert resolve_tree_root(start, marker=_MARKER) == root


def test_ac3b_marker_outranks_an_intermediate_git(tmp_path):
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415

    root = tmp_path / "install_root"
    root.mkdir()
    (root / _MARKER).write_text("x")
    nested = root / "SYSTEM" / "cli" / "build"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    start = nested / "engine_py" / "workflows" / "module.py"
    assert resolve_tree_root(start, marker=_MARKER) == root


def test_ac3c_resolver_falls_back_to_the_checkout_root_with_git_file(tmp_path):
    _require_no_git_above(tmp_path)
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415

    root = tmp_path / "clone_root"
    (root / "engine_py" / "workflows").mkdir(parents=True)
    (root / ".git").write_text("gitdir: /elsewhere\n")  # worktree-style .git FILE
    start = root / "engine_py" / "workflows" / "module.py"
    assert resolve_tree_root(start, marker=_MARKER) == root
    assert resolve_tree_root(start) == root


def test_ac3d_resolver_falls_back_to_the_clamped_package_root(tmp_path):
    _require_no_git_above(tmp_path)
    from bytedigger_engine.lib.tree_root import resolve_tree_root  # noqa: PLC0415

    pkg = tmp_path / "tarball_root" / "engine_py"
    (pkg / "workflows").mkdir(parents=True)
    start = pkg / "workflows" / "module.py"
    assert resolve_tree_root(start, marker=_MARKER) == pkg


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
