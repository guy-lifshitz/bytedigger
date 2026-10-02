# bd#94 — engine-owned paths excluded from tree/manifest gates by construction

**Status: r1 (FROZEN for RED)** · **Tier:** 2 (one new engine module, one conformance lint +
inventory, wiring in 4 producer helpers; Option D) ·
**Class:** SYSTEMATIC ·
**Chokepoint:** `lib/util/engine_owned.is_engine_owned_path` — the one predicate that decides
"this path is engine state, not the user's/agent's change". Every path-list producer feeding a
gate (security-lint / gitleaks scan set, GREEN lint set, commit manifest, `git add -A` pathspec,
tree walkers) consumes it. `conformance/tree_scan_lint.py` makes forgetting it a test failure.
**Side of the seam:** engine. **Not touched:** `phases/` (lot 1570 P3b2 pending),
`engine.py`, `event_log.py`, `run.py`, `config_provider.py` (bd#93 PR #204 owns those),
`scripts/ts/**` and `hooks/**` (bash/TS lane — follow-up, §5).
**Source:** bd#94; HAL engine_py @456d46e97 incident family (venv symlink in path-escape lint;
secret scanner on own event log; commit step adding own state dir; own log in own manifest).

## §1 Problem (measured on `9ae7b7a`)

No shared predicate exists (`git grep -n "engine_owned"` → 0). Each producer hand-rolls:

| Producer | File:line | Engine state handling today |
|---|---|---|
| `_derive_green_paths_from_git` (GREEN lint set; security-lint fallback set) | `workflows/phase_5_implement.py:892` | none for state dir: untracked `.bytedigger/` shows as `?? .bytedigger/` and is rglob-expanded; symlink resolving outside root silently `continue`d (one-off) |
| `_derive_security_lint_paths` (semgrep+gitleaks scan set) | `workflows/phase_5_implement.py:5900` | none: `git_diff_files(..., untracked=True)` returns `.bytedigger/events.jsonl` → gitleaks reads the engine's own log |
| `_filter_gitignored_paths` (commit manifest filter used by every `git add -- <paths>` commit step, phase 5 and 6) | `workflows/phase_workflows_common.py:819` | only gitignore; a non-ignored `.bytedigger/…` in the manifest is staged |
| `_checkpoint_green_worktree` (`git add -A`) | `workflows/phase_5_implement.py:5089-5092` | inline `f":(exclude){foreign_state_dirname()}"` (one-off) |
| `error_codes.harvest_codes` walker | `error_codes.py:20-24,297-300` | literal `".bytedigger"`, `".hal-build"` in `HARVEST_EXCLUDE_DIRS` (one-off) |
| `cyrillic_scan.tracked_files` walk fallback | `cyrillic_scan.py:85,131-133` | none |

~100 tree/manifest-scanning call sites exist in `workflows/`, `lib/`, top-level modules
(`os.walk`/`rglob` 31, `status --porcelain`/`ls-files` 38, `git_diff_files` 17, `git add` 13);
nothing forces a new one to exclude engine state.

## §2 Design

### §2.1 New module `engine_py/bytedigger_engine/lib/util/engine_owned.py`

stdlib + `from bytedigger_engine.config_provider import foreign_state_dirname`. No file
content reads, no subprocess, no git (so no class-I inventory key; `class_i_lint` must stay rc0).

```python
def engine_state_dirname() -> str
def is_engine_owned_path(path: str | os.PathLike, repo_root: str | os.PathLike) -> bool
def drop_engine_owned(paths: Iterable[str], repo_root, *, step: str) -> list[str]
def engine_owned_pathspecs() -> list[str]
def prune_engine_owned_dirs(dirnames: list[str]) -> None   # in-place, for os.walk
```

- `engine_state_dirname()`: `foreign_state_dirname()` at call time (host override honoured).
  If it raises or returns a non-`str`/empty value → degrade to `".bytedigger"` (neutral
  default) and never raise.
- `is_engine_owned_path(path, repo_root)` → `True` iff ANY of:
  - **R1 state dir:** after normalising (`\` → `/`, strip leading `./`; an absolute path is made
    relative to `repo_root` lexically when it is under it), ANY path segment equals
    `engine_state_dirname()` (state dir at any depth: the engine writes under its cwd, which may be
    a subdir of `git_cwd`).
  - **R2 escaping symlink:** the path itself or any parent component between `repo_root` and the
    path is a symlink (`os.path.islink`) whose target, resolved, is not inside
    `Path(repo_root).resolve()`. A dangling symlink, or `OSError`/`RuntimeError` while resolving
    a symlink, also counts as R2 (degrade: not user content). Non-existent non-symlink paths are
    judged by R1 only.
  - Otherwise `False`. Empty path → `False`. Never raises (any unexpected exception → `False`
    for R1-negative paths; the predicate is a filter, not a gate of its own).
  - Explicit non-matches (B-tests pin): `.bytedigger-sessions.json`, `src/bytedigger/x.py`,
    `my.bytedigger/x`, a symlink resolving INSIDE `repo_root`.
- `drop_engine_owned(paths, repo_root, *, step)`: order-preserving filter by
  `is_engine_owned_path`. When ≥1 path is dropped, emits ONE event
  `engine_owned_paths_dropped` `{step, n_dropped, paths: sorted(dropped)[:20]}` through the
  engine's normal event emission (`_emit_safe` contract: emission failure never raises). No event
  when nothing is dropped. Never silent, never raises.
- `engine_owned_pathspecs()`: `[f":(exclude){d}", f":(exclude)**/{d}/**"]` with
  `d = engine_state_dirname()`.
- `prune_engine_owned_dirs(dirnames)`: removes entries equal to `engine_state_dirname()` from the
  list in place (for `os.walk`'s `dirnames`).

### §2.2 Wiring (AC1 — behaviour, measured on a real tmp git repo)

1. `_derive_green_paths_from_git`: the final list passes through
   `drop_engine_owned(..., git_cwd, step="derive_green_paths")`. The existing rglob-symlink
   `continue` block (`phase_5_implement.py:943-951`) is replaced by the shared predicate (R2) —
   no second hand-rolled escape check remains in that function.
2. `_derive_security_lint_paths`: the `red_sha` branch's `filtered` passes through
   `drop_engine_owned(..., git_cwd, step="security_lint_paths")` (the fallback branch is covered
   by 1).
3. `_filter_gitignored_paths(paths, git_cwd)`: first `drop_engine_owned(paths, git_cwd,
   step="commit_manifest")`, then the existing check-ignore logic on the remainder. Docstring
   states both. All commit steps that route through it inherit the exclusion.
4. `_checkpoint_green_worktree`: `["git", "add", "-A", "--", ".", *engine_owned_pathspecs()]`;
   the inline `:(exclude)` literal is deleted.
5. `error_codes.HARVEST_EXCLUDE_DIRS` loses `".bytedigger"`; `harvest_codes` calls
   `prune_engine_owned_dirs(dirnames)` (`.hal-build` stays — HAL host dir, not this engine's;
   declared).
6. `cyrillic_scan.tracked_files` walk branch calls `prune_engine_owned_dirs(dirnames)`.

### §2.3 Construction lint `engine_py/bytedigger_engine/conformance/tree_scan_lint.py` (AC2)

Mirror of `class_i_lint.py` (bd#150): AST scan of `workflows/**`, `lib/**` and top-level
`bytedigger_engine/*.py` (not `conformance/`, not `tests/`). A **tree-scan site** is a call to:
`os.walk`, any `.rglob(`, `git_diff_files`, `status_porcelain`, `ls_files_others`, or a
call whose first positional argument is a list/tuple literal (or `[...] + x` with a literal left
side) containing, as its git verb, `"status"`, `"ls-files"` or `"add"` (verb = first element,
or second when the first is `"git"`). Key: `<relpath>::<qualname or <module>>::<kind>#<n>`
(`kind` ∈ `os.walk|rglob|git_diff_files|status_porcelain|ls_files_others|git-status|git-ls-files|git-add`,
`n` = ordinal of that kind in that scope).

`conformance/tree_scan_inventory.json`: `{"version": 1, "sites": {key: {"class", "note"}}}`,
`class` ∈ `{"filters", "not-a-gate"}`, `note` non-empty string.

`check(root, inventory) -> list[str]` reports: a site with no key; a key with no site (stale);
a malformed entry; a `"filters"` entry whose enclosing function body does not reference any name
in `FILTER_NAMES = {"is_engine_owned_path", "drop_engine_owned", "engine_owned_pathspecs",
"prune_engine_owned_dirs", "_filter_gitignored_paths"}`. `call_sites(root) -> list[str]`.
`_filter_gitignored_paths` is in `FILTER_NAMES` only because of §2.2-3; a test pins that its
body calls `drop_engine_owned`.

Every site on the post-GREEN tree is classified; the six producers of §2.2 are `"filters"`.

## §3 Acceptance (RED file: `engine_py/tests/test_bd94_engine_owned_paths.py`)

- **AC1** predicate unit: R1 (top-level, nested `sub/.bytedigger/x`, absolute-under-root,
  `./` and `\` forms), R2 (symlink file → outside; symlinked dir parent → outside; dangling;
  symlink inside root → False), non-matches of §2.1, empty → False, host override (monkeypatched
  `foreign_state_dirname` → `".zzstate"` makes `.zzstate/x` owned and `.bytedigger/x` not),
  provider raising → degrades to `.bytedigger`, never raises.
- **AC2** `drop_engine_owned`: order preserved; exactly one `engine_owned_paths_dropped` event
  with `step`/`n_dropped`/sorted capped `paths`; none when nothing dropped; emitter raising does
  not propagate.
- **AC3** `engine_owned_pathspecs()` exact list, host override honoured.
- **AC4** real tmp git repo (fresh run shape: committed `src/a.py`, then untracked
  `.bytedigger/events.jsonl` containing `sk-ant-api03-` + 40 chars and `.bytedigger/state.py`,
  modified `src/a.py`, new `src/b.py`, symlink `venv -> <tmp outside repo>/realvenv` holding
  `lib.py`; no `.gitignore`):
  - `_derive_green_paths_from_git(repo)` == `["src/a.py", "src/b.py"]`;
  - `_derive_security_lint_paths({"red_commit_sha": <sha of first commit>}, {}, repo)[0]`
    contains neither `.bytedigger/…` nor `venv/…`, contains `src/a.py` and `src/b.py`;
  - `_filter_gitignored_paths([".bytedigger/events.jsonl", "src/a.py"], repo)` == `["src/a.py"]`;
  - `_checkpoint_green_worktree` source (AST) passes `engine_owned_pathspecs()` to `git add -A`
    and holds no `":(exclude)` string literal; behaviourally, running the `git add -A` argv it
    builds in the tmp repo leaves `.bytedigger/` unstaged (`git diff --cached --name-only`).
- **AC5** walkers: `error_codes.harvest_codes` on a tmp tree with `E_BD94_PROBE` only under
  `.bytedigger/` → not harvested; `".bytedigger"` not in `HARVEST_EXCLUDE_DIRS`;
  `cyrillic_scan.tracked_files` on a non-git tmp dir with Cyrillic in
  `.bytedigger/events.jsonl` → that path not listed.
- **AC6** lint on the real tree: `tree_scan_lint.check(<bytedigger_engine>, load_inventory())`
  == `[]`; the six §2.2 producers' keys are class `"filters"`.
- **AC7** lint catches the omission: on a synthetic package dir with a new
  `workflows/new_gate.py` doing `os.walk(root)` and `git_read(["status", "--porcelain"], ...)`
  without inventory entries → two "no key" problems naming the keys; with `"filters"` entries but
  no `FILTER_NAMES` reference → two "filters without filter call" problems; with a
  `drop_engine_owned` call in the function → `[]`; a stale key is reported; `"not-a-gate"`
  with empty note is malformed.
- **AC8** `_filter_gitignored_paths` body calls `drop_engine_owned` (AST pin for its
  `FILTER_NAMES` membership).
- **AC9** class-I lint unchanged: `class_i_lint.check(...)` on the real tree == `[]`.

## §4 Degrade-not-fail

Provider failure → neutral dirname. Symlink resolution failure → treated as owned (dropped, with
event). Event emission failure → swallowed. The predicate never raises; producers keep their
existing failure modes unchanged.

## §5 Out of scope / follow-ups

- bash/TS lane (`scripts/ts/build-phase-gate.ts`, `hooks/`): root-level legacy artifacts
  (`build-*.md`, `build-*-output.log`, `build-state.yaml`) — separate issue.
- bd#93 (PR #204) run-scoped log under `~/.bytedigger/runs` is outside every repo root, so no
  tree scan sees it; no coupling to this PR beyond R1's dirname.
- `phases/*.md` prose about these gates — after lot 1570 P3b2 lands.
