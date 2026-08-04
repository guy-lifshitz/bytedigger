"""mutating_git_lint — GH1220 Change D: the anti-rot layer.

AST-based deterministic scan of engine prod `.py` (excluding `tests/`) for
every mutating-git call site — walk every `git`/`git_read`/`git_op_capture`/
`git_op_with_lock_retry`/`_git_write` invocation whose argument list begins
with a (verb, subcommand) pair, classify on the PAIR (Amendment 6: a
read-only pair like `worktree list` is never a "site" at all), plus direct
`Path(...).unlink()` / `shutil.rmtree(...)` calls rooted at a `git_cwd`
variable (D1/A8.3). Each hazard's enclosing function must be declared in
exactly one of `GUARDED_WRITE_SITES` (re-verified against an actual
`is_ambient_git_cwd(` call in its body — not rubber-stamped, AC34) or
`DECLARED_NON_GIT_CWD_SITES` (a documented reason).

Also flags a mutating verb mis-routed through the READ port
(`find_read_port_misuse`, AC37/A7.8) — classified, not re-routed.

API frozen by spec Amendment 3.3:
    find_unclassified_sites(root, guarded_sites=None, declared_non_git_cwd_sites=None) -> list
    resolver_source_labels() -> set
    run_lint(root) -> int
    GUARDED_WRITE_SITES: dict
    DECLARED_NON_GIT_CWD_SITES: dict
    find_read_port_misuse(root) -> list
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

# ── D1: mutating git verbs (Amendment 6: classified on (verb, subcommand),
# never verb alone — see READ_ONLY_PAIRS below) ──────────────────────────────
MUTATING_VERBS = frozenset({
    "commit", "add", "stash", "checkout", "reset", "clean", "rm", "apply",
    "push", "restore", "worktree", "update-ref", "cherry-pick", "revert",
    "merge", "rebase", "init", "branch", "tag", "fetch", "mv", "gc", "prune",
})

# Amendment 6: (verb, subcommand/flag) pairs that are pure reads, never a
# "site" needing registry classification at all.
READ_ONLY_PAIRS = frozenset({
    ("worktree", "list"),
    ("stash", "list"),
    ("branch", "--list"),
    ("branch", "-l"),
    ("tag", "-l"),
    ("tag", "--list"),
    ("remote", "-v"),
    ("submodule", "status"),
    ("notes", "list"),
})

# Callables whose first positional argument is treated as a git argv.
_GIT_CALL_NAMES = frozenset({
    "run", "git_read", "git_op_capture", "git_op_with_lock_retry",
    "_git_op_with_lock_retry", "_git_write", "op_capture", "op_with_lock_retry",
})

# Callables specifically on the READ port (AC37/A7.8).
_READ_PORT_CALL_NAMES = frozenset({"git_read"})

GIT_CWD_MARKER_FRAGMENT = "git_cwd"  # heuristic substring for path-rooting

# ── D2/A8.1: the real production registries ──────────────────────────────────
# Ten B1-B10 sites (spec §1 Change B), all guarded by an
# `is_ambient_git_cwd(...)` call (AC34 re-verifies the marker is a real AST
# Call, not merely a comment mentioning the name).
GUARDED_WRITE_SITES: "dict[str, str]" = {
    "_commit_red_tests": "phase_5_implement.py B1 — refuses ambient before `git add`",
    "_commit_red_skeleton": "phase_5_implement.py — refuses ambient before `git add` (GH1406)",
    "_commit_green_code": "phase_5_implement.py B2 — refuses ambient before `git add`",
    "_commit_fix_code": "phase_6_review.py B3 — refuses ambient before Path.unlink()",
    "_commit_fix_tests": "phase_6_review.py B4 — refuses ambient before `git add`",
    "_attempt_red_cycle_restore": "phase_5_implement.py B5 — refuses ambient before `git checkout`",
    "_checkpoint_green_worktree": "phase_5_implement.py B6 — refuses ambient before staging",
    "_autocommit_fix_tail": "phase_6_review.py B7 — refuses ambient before `git add`",
    "_compute_baseline_failed": "phase_5_implement.py B8 — refuses ambient before `git stash push -u`",
    "_compute_baseline_typecheck_count": "phase_5_implement.py B9 — refuses ambient before `git stash push -u`",
    "_verify_fix_typecheck": "phase_6_review.py B10 — refuses ambient before `git worktree add --detach`",
}

# A8.1/§0/A8.6: sites whose `git_cwd` is NOT derived from `lib.git_cwd` at
# all (a parameter passed in from elsewhere, or a different resolver chain
# entirely) — genuinely out of the GH1220 chokepoint, documented here rather
# than silently ignored.
DECLARED_NON_GIT_CWD_SITES: "dict[str, str]" = {
    "_revert_cross_tree_modifications": (
        "phase_workflows_common.py — different resolver chain "
        "(_resolve_worktree_root), not lib.git_cwd; follow-up OFI"
    ),
    "_rebase_onto_origin_main": (
        "phase_8_post_deploy.py — git_cwd is a parameter, never lib.git_cwd "
        "(A8.6: adds the `rebase` verb to §0's phase_8 row)"
    ),
    "_ship_to_pr": "phase_8_post_deploy.py — git_cwd is a parameter, never lib.git_cwd",
    "_cleanup_worktrees": "phase_8_post_deploy.py — git_cwd is a parameter, never lib.git_cwd",
    "_cleanup_gone_branches": "phase_8_post_deploy.py — git_cwd is a parameter, never lib.git_cwd",
    "_compute_full_suite_baseline": "phase_8_post_deploy.py — git_cwd is a parameter, never lib.git_cwd",
    "_verify_no_cross_tree_edits": (
        "phase_6_fix_integrity.py / phase_workflows_common.py — reads the label "
        "for a read-only dirty-tree guard, no mutating op"
    ),
}


def resolver_source_labels() -> "set[str]":
    """AC35: the set of labels `resolve_git_cwd_with_source` can actually
    return, derived from `lib.git_cwd` (both directions)."""
    from bytedigger_engine.lib.git_cwd import AMBIENT_GIT_CWD_SOURCES, EXPLICIT_GIT_CWD_SOURCES  # noqa: PLC0415

    return set(AMBIENT_GIT_CWD_SOURCES) | set(EXPLICIT_GIT_CWD_SOURCES)


# ── AST plumbing ──────────────────────────────────────────────────────────────

# Directory names that never hold this project's production code (bd#3).
# `tests`/`__tests__` are the original spec exclusions; the rest are vendored
# or installed third-party trees.
_PRUNED_DIR_NAMES = frozenset({
    "tests", "__tests__", "__pycache__",
    "site-packages", "dist-packages", "node_modules",
})

# Canonical virtualenv marker. Name-matching alone is not enough: `.venv` is a
# convention, and `python -m venv env311` is just as valid.
_VENV_MARKER = "pyvenv.cfg"


def _iter_prod_py_files(root: "str | Path") -> "Iterator[Path]":
    """Every production `.py` under `root`, excluding tests and dependencies.

    bd#3: the walk used to prune three directory names and nothing else, so a
    virtualenv inside the tree — which is what the README's install flow
    produces — put every installed package under the lint. The clean room hit
    `semgrep/git.py::_remove_worktree_with_check`: a correct match for the
    pattern, and one no edit could ever make pass, since third-party functions
    cannot be registered in `GUARDED_WRITE_SITES`. `run_lint` is fail-closed,
    so that made the anti-rot layer unusable for anyone who installed the
    package.

    Walks with `os.walk` rather than `rglob` so a pruned directory is not
    descended into at all — on a real install, skipping the recursion into
    `site-packages` is also most of the walk.
    """
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Sorted in place so traversal order is deterministic — os.walk yields
        # directory entries in filesystem order, `sorted(rglob(...))` did not.
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _PRUNED_DIR_NAMES and not (here / d / _VENV_MARKER).is_file()
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield here / name


def _callee_name(call: ast.Call) -> "str | None":
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _argv_elements(node: ast.AST) -> "list[str | None] | None":
    """Extract a positional list of (literal-string-or-None) from a list/tuple
    literal, or the LEFT operand of a `[...] + more_paths` BinOp (production's
    `["git", "add", "--"] + trackable_paths` shape)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _argv_elements(node.left)
    if isinstance(node, (ast.List, ast.Tuple)):
        out: "list[str | None]" = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
            else:
                out.append(None)
        return out
    return None


def _verb_subcommand(elements: "list[str | None]") -> "tuple[str | None, str | None]":
    """Locate (verb, subcommand) in an argv-element list, handling both the
    `["git", <verb>, ...]` form and the bare `[<verb>, ...]` form (git_read's
    convention), and skipping a `-C <dir>` pair inserted before the verb."""
    if not elements:
        return None, None
    idx = 0
    if elements[0] == "git":
        idx = 1
        if len(elements) > idx and elements[idx] == "-C":
            idx += 2  # skip "-C" and its (usually non-literal) dir argument
    verb = elements[idx] if idx < len(elements) else None
    subcommand = elements[idx + 1] if idx + 1 < len(elements) else None
    return verb, subcommand


def _is_git_cwd_rooted(expr: ast.AST) -> bool:
    """Heuristic (D1/A8.3): does `expr` reference a `git_cwd`-named variable
    anywhere in its subtree — e.g. `Path(git_cwd) / rel`?"""
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and GIT_CWD_MARKER_FRAGMENT in node.id:
            return True
    return False


class _FuncScopedVisitor(ast.NodeVisitor):
    """Walks a module tracking the nearest enclosing function, collecting
    mutating-git call sites, git-cwd-rooted `.unlink()`/`shutil.rmtree()`
    sites, read-port misuse sites, and (per function) whether an
    `is_ambient_git_cwd(` guard call is genuinely present (AST Call, not a
    text/comment match — AC34's trap)."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self._func_stack: "list[str]" = []
        self._var_stack: "list[set[str]]" = []
        self.sites: "list[dict[str, Any]]" = []
        self.read_port_misuse: "list[dict[str, Any]]" = []
        self.guard_calls: "dict[str, bool]" = {}

    def _enter_func(self, node: "ast.FunctionDef | ast.AsyncFunctionDef") -> None:
        self._func_stack.append(node.name)
        self._var_stack.append(set())
        self.guard_calls.setdefault(node.name, False)
        self.generic_visit(node)
        self._var_stack.pop()
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._enter_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._enter_func(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if (
            self._var_stack
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_git_cwd_rooted(node.value)
        ):
            self._var_stack[-1].add(node.targets[0].id)
        self.generic_visit(node)

    def _current_function(self) -> "str | None":
        return self._func_stack[-1] if self._func_stack else None

    def _record_guard_call(self, name: "str | None") -> None:
        if name == "is_ambient_git_cwd" and self._func_stack:
            self.guard_calls[self._func_stack[-1]] = True

    def _var_is_git_cwd_rooted(self, name: str) -> bool:
        return any(name in scope for scope in self._var_stack)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = self._current_function()
        callee = _callee_name(node)
        self._record_guard_call(callee)

        if func is not None:
            # ── mutating-git argv sites ──────────────────────────────────────
            if callee in _GIT_CALL_NAMES and node.args:
                elements = _argv_elements(node.args[0])
                if elements is not None:
                    verb, subcommand = _verb_subcommand(elements)
                    is_mutating_pair = verb in MUTATING_VERBS and (verb, subcommand) not in READ_ONLY_PAIRS
                    if is_mutating_pair:
                        self.sites.append({
                            "file": self.filename, "line": node.lineno, "function": func,
                        })
                    if callee in _READ_PORT_CALL_NAMES and is_mutating_pair:
                        self.read_port_misuse.append({
                            "file": self.filename, "line": node.lineno, "function": func,
                        })

            # ── Path.unlink() / shutil.rmtree() on a git_cwd-rooted path ─────
            if callee == "unlink" and not node.args and isinstance(node.func, ast.Attribute):
                target = node.func.value
                rooted = (
                    _is_git_cwd_rooted(target)
                    or (isinstance(target, ast.Name) and self._var_is_git_cwd_rooted(target.id))
                )
                if rooted:
                    self.sites.append({
                        "file": self.filename, "line": node.lineno, "function": func,
                    })
            if callee == "rmtree" and node.args:
                if _is_git_cwd_rooted(node.args[0]) or (
                    isinstance(node.args[0], ast.Name)
                    and self._var_is_git_cwd_rooted(node.args[0].id)
                ):
                    self.sites.append({
                        "file": self.filename, "line": node.lineno, "function": func,
                    })

        self.generic_visit(node)


def _scan_file(path: Path) -> "_FuncScopedVisitor | None":
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None
    visitor = _FuncScopedVisitor(str(path))
    visitor.visit(tree)
    return visitor


def find_unclassified_sites(
    root: "str | Path",
    guarded_sites: "Iterable[str] | None" = None,
    declared_non_git_cwd_sites: "Iterable[str] | None" = None,
) -> "list[dict[str, Any]]":
    """D3/Amendment 3.3: every mutating-git (or git_cwd-rooted unlink/rmtree)
    call site whose enclosing function is not classified in EITHER registry
    — or is declared "guarded" but genuinely lacks an `is_ambient_git_cwd(`
    call in its body (AC34) — is reported with file/line/function."""
    guarded = set(guarded_sites) if guarded_sites is not None else set(GUARDED_WRITE_SITES)
    declared_non_git_cwd = (
        set(declared_non_git_cwd_sites)
        if declared_non_git_cwd_sites is not None
        else set(DECLARED_NON_GIT_CWD_SITES)
    )

    unclassified: "list[dict[str, Any]]" = []
    for path in _iter_prod_py_files(root):
        visitor = _scan_file(path)
        if visitor is None:
            continue
        for site in visitor.sites:
            func = site["function"]
            if func in declared_non_git_cwd:
                continue
            if func in guarded:
                if visitor.guard_calls.get(func):
                    continue
                unclassified.append(site)  # AC34: declared guarded, marker absent
                continue
            unclassified.append(site)
    return unclassified


def find_read_port_misuse(root: "str | Path") -> "list[dict[str, Any]]":
    """AC37/A7.8: a mutating git subcommand passed to `git_port.git_read` —
    a mutating op mis-routed through the READ port. Classified (registered
    in GUARDED_WRITE_SITES via its enclosing function), never re-routed."""
    misuse: "list[dict[str, Any]]" = []
    for path in _iter_prod_py_files(root):
        visitor = _scan_file(path)
        if visitor is None:
            continue
        misuse.extend(visitor.read_port_misuse)
    return misuse


def run_lint(root: "str | Path") -> int:
    """D4: enforcing from day one — no falsy-default flag. Escape:
    `HAL_MUTATING_GIT_LINT=0`. Returns 0 clean, non-zero on findings."""
    if os.environ.get("HAL_MUTATING_GIT_LINT") == "0":
        return 0
    findings = find_unclassified_sites(root)
    return len(findings)
