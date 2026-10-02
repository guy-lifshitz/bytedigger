# bd#94: engine-owned paths excluded from tree/manifest gates by construction

**Status: r3.** Gate r1 REJECTED the spec (6 MAJOR, 8 MINOR) and gate r2 REJECTED r2 (1 MAJOR N1, 8 MINOR N2-N9, 8 edges). Every finding is accepted or explicitly declared; see `2026-10-02-bd94-gate-r{1,2}.md` and the tables in §6/§7. r1 was frozen at bbf56dd, r2 at 3427031.

| | |
|---|---|
| **Tier** | 2. One new engine module, one conformance lint plus its inventory, and wiring in the producers listed in §2.2. Option D. |
| **Class** | SYSTEMATIC |
| **Chokepoint** | `lib/util/engine_owned.drop_engine_owned`, which sits over the predicate `is_engine_owned_path`. It is the one place that decides "this path is engine state, not the user's or agent's change". Every path-list producer that feeds a gate consumes it: the security-lint / gitleaks scan set, the GREEN lint and typecheck sets, the commit manifest, the dirty-tree checks, the `git add -A` pathspec, and the tree walkers. `conformance/tree_scan_lint.py` turns a forgotten call into a test failure. |
| **Side of the seam** | engine |
| **Not touched** | `phases/` (lot 1570 P3b2 is pending); `engine.py`, `event_log.py`, `run.py`, `config_provider.py` (bd#93 PR #204 owns those); `scripts/ts/**` and `hooks/**` (the bash/TS lane, follow-up in §5) |
| **Source** | bd#94. The HAL engine_py @456d46e97 incident family: a venv symlink tripped the path-escape lint, the secret scanner flagged the engine's own event log, a commit step added the engine's own state dir, and the engine's own log landed in its own manifest. |

## §1 Problem (measured on `9ae7b7a`)

There is no shared predicate (`git grep -n "engine_owned"` → 0). Each producer hand-rolls its own handling:

| Producer | File:line | How it handles engine state today |
|---|---|---|
| `_derive_green_paths_from_git`: GREEN lint set and the security-lint fallback set | `workflows/phase_5_implement.py:892` | The state dir is not handled: an untracked `?? .bytedigger/` is rglob-expanded. A file symlink that resolves outside the root is skipped by a one-off `continue` (`:943-951`). |
| `_derive_security_lint_paths`: semgrep + gitleaks scan set | `workflows/phase_5_implement.py:5900` | Not handled: `git_diff_files(..., untracked=True)` returns `.bytedigger/events.jsonl`, so gitleaks reads the engine's own log. |
| `_verify_green_lint_rules` / `_verify_green_typecheck`: scan sets | `phase_5_implement.py:5617`, `:6233` | Not handled. Same `git_diff_files` call. |
| `_detect_green_complete_resume` | `phase_5_implement.py:1765` | Not handled. |
| `_filter_gitignored_paths`: commit manifest filter | `workflows/phase_workflows_common.py:819` | Gitignore only. A non-ignored `.bytedigger/…` in the manifest gets staged. |
| `_commit_fix_tests` `git add` (step_name `commit_fix_tests`) | `workflows/phase_6_review.py:5042` | Bypasses `_filter_gitignored_paths` entirely. |
| `_dirty_worktree_guard`: porcelain dirty check plus self-heal commit | `workflows/phase_6_fix_integrity.py:301,329,354` | Counts `?? .bytedigger/` as dirt. |
| `_checkpoint_green_worktree`: dirty check plus `git add -A` | `phase_5_implement.py:5078-5092` | The dirty check counts state. The add uses an inline `f":(exclude){foreign_state_dirname()}"` (one-off). |
| `error_codes.harvest_codes` walker | `error_codes.py:20-24,297-300` | Hard-coded `".bytedigger"` in `HARVEST_EXCLUDE_DIRS`. The engine dirname is only excluded today because `scan_roots.is_pruned_dir` prunes every dot-dir. |
| `cyrillic_scan.tracked_files` walk fallback | `cyrillic_scan.py:85,131-133` | Not handled. |
| `preflight.compute_state_hash`: receipt state fingerprint | `preflight.py:208-216` (`status --untracked-files=all`, two `diff --binary`, `ls-files --others`) | Not handled: a new engine state file changes the hash and stales the receipt. |
| `preflight._Run._changed`: changed-file set read by `step_syntax` | `preflight.py:355` | Not handled. |

There are about 280 tree/manifest-scanning call sites across the package, and nothing forces a new one to exclude engine state.

## §2 Design

### §2.1 New module `engine_py/bytedigger_engine/lib/util/engine_owned.py`

- `from __future__ import annotations`, stdlib only, plus `from bytedigger_engine.config_provider import foreign_state_dirname` and `from bytedigger_engine.telemetry_ctx import emit_safe` (or whatever symbol `_emit_safe` writes through; RED pins the observable event).
- Python 3.9: no `Path.walk`, no `os.path.realpath(strict=)`.
- No file-content reads, no subprocess, no git. It therefore needs no class-I key, and `class_i_lint` stays rc0.

```python
def engine_state_dirname() -> str
def is_engine_state_path(path, repo_root) -> bool                          # R1 only
def is_engine_owned_path(path, repo_root, *, content_scan: bool) -> bool   # R1, or R1 or R2
def drop_engine_owned(paths, repo_root, *, step: str, content_scan: bool) -> list[str]
def engine_owned_pathspecs() -> list[str]
def prune_engine_owned_dirs(dirnames: list[str]) -> None                   # in place, for os.walk
def porcelain_path(line: str) -> str                                       # `git status --porcelain` v1 line -> path
def drop_engine_owned_porcelain(lines, repo_root, *, step: str) -> list[str]  # R1 on porcelain_path(line)
```

**`engine_state_dirname()`** returns `foreign_state_dirname()`, read at call time.
- It degrades to `".bytedigger"` when the call raises, or the value is not a `str`, is empty, contains `/` or `\`, or is `.` or `..`.
- It never raises.

**R1, state dir: `is_engine_state_path`.**
1. Normalise: replace `\` with `/`.
2. An absolute path is made relative to `repo_root`. Try it lexically first, then with BOTH `Path(path).resolve()` (non-strict) and `Path(repo_root).resolve()`, so `/private/var` vs `/var` and a path under an alias dir both work (gate r2 N5).
3. An absolute path under neither root → `False`. R1 never looks at segments above the root (gate M3, edge 2).
4. Apply `posixpath.normpath`. A result that is `..` or starts with `../` → `False`.
5. `True` iff ANY remaining segment equals `engine_state_dirname()`. Any depth counts, because the engine writes under its cwd, which may be a subdir of `git_cwd`.
6. Empty path → `False`. It never raises.

**R2, escaping symlink.** The path itself, or any parent component between `repo_root` and the path, is a symlink (`os.path.islink`) whose resolved target is not inside `Path(repo_root).resolve()`.
- A dangling link, a link loop, or an `OSError`/`RuntimeError` while resolving also counts as R2. This is the degrade path: it is not user content to read.
- A non-existent path that is not a symlink fails R2.
- **R2b, decoy link into the state dir (gate r2 edge 4).** The path, or a parent component, is a symlink whose resolved target IS inside the resolved root and R1 holds for that target (relative to the resolved root). Example: `src/log.txt -> ../.bytedigger/events.jsonl`. R2b is part of R2, so it applies only with `content_scan=True`.

**`is_engine_owned_path(path, repo_root, *, content_scan)`**
- `content_scan=False` → R1 only.
- `content_scan=True` → R1 or R2.
- `content_scan` is keyword-only and required, so every caller decides explicitly.
- R2 applies only to sets whose files are READ (scan/lint/typecheck). It never applies to commit manifests or dirty checks: a user's escaping or dangling symlink is a legitimate change and must stay committable (gate F3, edge 8).

**Explicit non-matches**, pinned by B-tests:
- `.bytedigger-sessions.json`
- `src/bytedigger/x.py`
- `my.bytedigger/x`
- `.bytedigger/../src/a.py` (normpath gives `src/a.py`)
- a symlink that resolves INSIDE `repo_root`
- an absolute path outside the root
- every path when `repo_root` itself lives under a `.bytedigger` ancestor

Explicit matches: `src/../.bytedigger/x`, `sub/.bytedigger/x`, `./.bytedigger/x`, `.bytedigger\x`.

**`drop_engine_owned(paths, repo_root, *, step, content_scan)`**
- An order-preserving filter by `is_engine_owned_path`.
- When at least one path is dropped, it emits ONE event `engine_owned_paths_dropped` with payload `{step, n_dropped, content_scan, paths: sorted(dropped)[:20]}`, sent through the engine's `_emit_safe` contract (an emission failure never raises).
- It emits nothing when nothing is dropped. It is never silent and never raises.

**`porcelain_path(line)`** (gate r2 edge 3)
- Drops the 3-char `XY ` status prefix.
- A rename/copy `old -> new` yields `new`.
- A path wrapped in double quotes is C-unquoted: `\\`, `\"`, `\t`, `\n` and 3-digit octal escapes; octal bytes are decoded as UTF-8 (`errors="replace"`). So `?? ".bytedigger/caf\303\251.json"` gives `.bytedigger/café.json`.
- It never raises; a line shorter than 4 chars yields `""`.

**`drop_engine_owned_porcelain(lines, repo_root, *, step)`** keeps the order of `lines` and drops each line where `is_engine_state_path(porcelain_path(line), repo_root)`. Blank lines are dropped too. It emits the same `engine_owned_paths_dropped` event as `drop_engine_owned` (payload `content_scan: False`, `paths` = the dropped `porcelain_path` values) under the same emit rules.

**`engine_owned_pathspecs()`** returns `[f":(exclude){d}", f":(exclude)**/{d}/**"]`, where `d = engine_state_dirname()`.

**`prune_engine_owned_dirs(dirnames)`** removes the entries equal to `engine_state_dirname()` from the list, in place.

### §2.2 Wiring (AC4/AC5 are behavioural, run on a real tmp git repo)

Step names are pinned by tests.

| # | Site | Change | `step` | `content_scan` |
|---|---|---|---|---|
| 1 | `_derive_green_paths_from_git` | The final list passes through `drop_engine_owned`. The rglob-symlink `continue` block (`:943-951`) is deleted, and R2 replaces it, including symlinked parent dirs (edge 3). | `"derive_green_paths"` | True |
| 2 | `_derive_security_lint_paths` | The `red_sha` branch passes through `drop_engine_owned`. The fallback is covered by 1. | `"security_lint_paths"` | True |
| 3 | `_verify_green_lint_rules` (:5617), `_verify_green_typecheck` (:6233) | Their `git_diff_files` result passes through `drop_engine_owned`. | `"green_lint_paths"` / `"green_typecheck_paths"` | True |
| 4 | `_detect_green_complete_resume` (:1765) | Same. | `"green_resume_paths"` | False |
| 5 | `_filter_gitignored_paths(paths, git_cwd)` | First `drop_engine_owned`, then the existing check-ignore on the remainder. The docstring states both. | `"commit_manifest"` | False |
| 6 | `_commit_fix_tests` (`phase_6_review.py:5042`) | `test_paths = _filter_gitignored_paths(test_paths, git_cwd)` (result assigned back) BEFORE the `if not test_paths` early return, so an all-state manifest takes the existing `no_test_paths` branch and never reaches `git add --` with no pathspec (gate r2 N8, edge 5). All seven `git add` sites in phase 5/6 then route through it. | | |
| 7 | `_dirty_worktree_guard` (`phase_6_fix_integrity.py`): BOTH reads, the first (:329) and the post-self-heal re-read (:354, `still_dirty`); and the `_checkpoint_green_worktree` dirty check (:5078-5086) | Each porcelain result passes through `drop_engine_owned_porcelain` before the dirty / self-heal / still-dirty decision. A tree whose only dirt is engine state is CLEAN, and so is a tree whose only remaining dirt after the self-heal commit is engine state (gate F2, gate r2 N1). Tracked, modified state (` M .bytedigger/…`, gate r2 edge 2) is dropped the same way. | `"dirty_guard"` (both reads) / `"checkpoint_dirty"` | False |
| 8 | `_checkpoint_green_worktree` | Runs `["git", "add", "-A", "--", ".", *engine_owned_pathspecs()]`. The inline `:(exclude)` literal is deleted. | | |
| 9 | `error_codes` | `HARVEST_EXCLUDE_DIRS` loses `".bytedigger"`; `harvest_codes` calls `prune_engine_owned_dirs(dirnames)`. `.hal-build` stays: it is the HAL host dir, not this engine's. Declared. The comment at `error_codes.py:17-19` ("import-order dependency") is rewritten: the state dirname is now read at walk time through `prune_engine_owned_dirs` (gate r2 N9e). | | |
| 10 | `cyrillic_scan.tracked_files` | The walk branch calls `prune_engine_owned_dirs(dirnames)`. | | |
| 11a | `preflight.compute_state_hash` (:208-216) | The `status`, both `diff --binary` and the `ls-files --others` calls append `"--", ".", *engine_owned_pathspecs()`; `rev-parse HEAD` is unchanged. A new or changed engine state file therefore leaves the hash unchanged (gate r2 edge 7). The fingerprint keeps user links (no R2). Existing receipts stale once after the upgrade; that is acceptable (they are per-run). | | |
| 11b | `preflight._Run._changed` (:355) | The final set passes through `drop_engine_owned` (sorted input, set output kept). Its files are read and compiled by `step_syntax`, so it is a read set (gate r2 N2). | `"preflight_changed"` | True |

**Walker R2 choice (gate r2 N2, declared).** Rows 9 and 10 prune by dirname only (R1). `os.walk` does not descend into dir links (default `followlinks=False`), but a file link to outside the root is still read by `harvest_codes` / `cyrillic_scan`. Neither is a safety gate (harvest feeds the error-code registry, cyrillic is a style lint), so R2 is not applied there. A follow-up can add it.

**Out of scope here (declared).** `_autocommit_fix_tail` parses porcelain with `ln[3:].strip()` (`phase_6_review.py:4285`), which mishandles quoted and renamed paths. It already routes through `_filter_gitignored_paths`, so unquoted state is dropped; switching it to `porcelain_path` is a follow-up.

### §2.3 Construction lint `engine_py/bytedigger_engine/conformance/tree_scan_lint.py` (AC2)

This mirrors `class_i_lint.py` (bd#150).

**Scope.** An AST scan of every `*.py` under `bytedigger_engine/`, except `conformance/**` and `tests/**`. `security/` and the top-level modules are included (gate F1).

**Public API:**
- `load_inventory()`
- `call_sites(root) -> list[str]`
- `check(root, inventory) -> list[str]`
- `FILTER_NAMES`
- `KINDS`

**A tree-scan site** is any one of:

| Group | What counts | `kind` |
|---|---|---|
| (a) Walk family | A call to `os.walk`, `os.scandir`, `os.listdir`, `glob.glob`, `glob.iglob` (after import-alias resolution, like `class_i_lint._collect_bindings`), or any attribute call `.rglob(`, `.glob(`, `.iterdir(` | the callee: `os.walk`, `os.scandir`, `os.listdir`, `glob.glob`, `glob.iglob`, `rglob`, `glob`, `iterdir` |
| (b) Named helpers | A call to `git_diff_files`, `diff_files` (attribute or name), `status_porcelain`, `git_status_porcelain`, `ls_files_others` | the helper name |
| (c) Git argv | A call whose argument tokens hold a git verb from the table below | `git-status`, `git-ls-files`, `git-add`, `git-diff-names` |

**Argument tokens for (c)** (gate r2 N3). The token list is ALL elements, in order, of either:
- **list form:** the first positional argument, when it is a list/tuple literal or a `BinOp(+)` whose left side is one (left elements flattened; `*starred` elements skipped). A string literal is its value; any other element (a name, an f-string, an attribute) is a **placeholder**. The list form applies only when the first token is the literal `"git"` OR the callee's terminal name contains `git` (case-insensitive; e.g. `git_read`, `git_op_capture`); or
- **varargs form:** the positional arguments after any leading non-literal ones, when the callee's terminal name with leading underscores stripped is exactly `git` (e.g. `_git(top, "ls-files", ...)`). Same literal/placeholder rule. This keeps `x.get("status")` and `readiness.guard_git(lambda: ..., "add", False)` out (gate r2 N4).

**Verb rule.** Walk the tokens. Skip a leading `"git"`. `-C`, `-c`, `--git-dir`, `--work-tree` consume the NEXT token as their value, whatever it is (a placeholder included). The `--opt=value` forms consume nothing. Any other token starting with `-` is skipped. The verb is the first remaining token that is a string literal. A placeholder in verb position means no verb, so no site. Example: `["git", "-C", d, "status"]` → `-C` consumes `d`, the verb is `status` (a site).

**Lambda scope (gate r2 edge 6).** A `lambda` is not its own scope. A site inside a lambda takes the qualname of its nearest enclosing `def`/`class` (e.g. `companion_tune.py::_stage_files::git-add#0`), and the "filters" check looks at that `def`'s body.

**Kind precedence (gate r2 N6b).** Each call yields at most ONE site. A module-function match after import-alias resolution wins over the attribute rule: `glob.glob(p)` and `_glob.glob(p)` (with `import glob as _glob`) are `glob.glob`; only a `.glob(` on anything that does not resolve to the `glob` module is `glob`. The same holds for `os.walk` / `os.scandir` / `os.listdir` under an alias (`import os as _os`).

**`call_sites(root)`** returns the keys SORTED (gate r2 N9a).

| Verb | Kind | Condition |
|---|---|---|
| `status` | `git-status` | always |
| `ls-files` | `git-ls-files` | always |
| `add` | `git-add` | always |
| `diff` | `git-diff-names` | only when `--name-only`, `--name-status`, `--numstat` or `--stat` is among the tokens. A content diff is not a file list. |

**Key.** `<relpath>::<qualname or <module>>::<kind>#<n>`, where `n` is the ordinal of that kind within that scope, in source order.

**Inventory** `conformance/tree_scan_inventory.json`: `{"version": 1, "sites": {key: {"class", "note"}}}`.
- `class` is `"filters"` or `"not-a-gate"`.
- `note` is a non-empty string.

**`check` reports:**
- a site with no key, with the word "no key" plus the key;
- a key with no site, "stale" plus the key;
- a malformed entry, "malformed" plus the key;
- a `"filters"` entry whose enclosing function body references no name in `FILTER_NAMES = {"drop_engine_owned", "drop_engine_owned_porcelain", "is_engine_owned_path", "is_engine_state_path", "engine_owned_pathspecs", "prune_engine_owned_dirs", "_filter_gitignored_paths"}`, reported as "filters without filter call" plus the key;
- a `git-add` site whose class is not `"filters"`, reported as "git-add must filter" plus the key. Every staging site in the engine must route through the shared exclusion (gate M1).

`_filter_gitignored_paths` is in `FILTER_NAMES` only because of §2.2-5. AC8 pins that its body calls `drop_engine_owned`.

**Declared limitation (gate M2).** The `"filters"` check is per enclosing function, not per site. A function holding one filtered and one unfiltered scan passes. Each note names what is filtered, so the GREEN review can see it. No data-flow analysis is attempted. For the one known two-read function (`_dirty_worktree_guard`), AC4 pins both reads behaviourally.

**Declared limitation (gate r2 edge 7).** Argv held in a variable (`for args in (...): _git(top, *args)`) is invisible: starred arguments are skipped. `compute_state_hash` is wired by §2.2-11a and pinned behaviourally by AC5.

**Inventory classification.**
- Every site on the post-GREEN tree is classified.
- The sites of the §2.2 producers 1-8 and 11 are `"filters"`.
- So are all `git-add` sites (including `companion_tune.py:465` and `verification_registry.py:240`). `baseline_tree.py:84` (`worktree add`) has verb `worktree`, so it is not a `git-add` site (gate r2 N9c). Each `git-add` site filters its argv or list through the shared function.

## §3 Acceptance (RED file: `engine_py/tests/test_bd94_engine_owned_paths.py`)

**Hermetic git (gate r2 N7).** An autouse fixture sets `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` to `os.devnull` in the process env (monkeypatch), so the units under test (`git_port.git_read`, `git_op_with_lock_retry`) do not inherit `commit.gpgsign`, `core.hooksPath`, `core.excludesFile` or `status.showUntrackedFiles`. It `delenv`s the `HAL_`, `BD_` and `BYTEDIGGER_` spellings of `GREEN_CHECKPOINT_GATE` and `AUTHORED_BOUNDARY_GATE` unless a test sets them.

**AC1: predicate unit.**
- Every R1 match and non-match in §2.1, including the `..` cases, an absolute path outside the root, and a repo under a `.bytedigger` ancestor.
- R2 with `content_scan=True`:
  - file symlink → outside: owned;
  - symlinked dir parent → outside: owned;
  - dangling link: owned;
  - link loop: owned;
  - link inside the root: not owned;
  - R2b decoy `src/log.txt -> ../.bytedigger/events.jsonl` (and a dir link `src/st -> ../.bytedigger` with path `src/st/events.jsonl`): owned with `content_scan=True`, not owned with `False`.
- R1 with a path under an alias dir of the root (both resolved, N5): owned for `<alias>/.bytedigger/x`.
- The same R2-only cases with `content_scan=False` → not owned.
- `content_scan` omitted → `TypeError`.
- Host override:
  - a monkeypatched `foreign_state_dirname` → `"bdstate"` (NOT dot-prefixed, gate F5) makes `bdstate/x` owned and `.bytedigger/x` not;
  - each of `"a/b"`, `""`, `None`, `123`, `".."`, and a raised `RuntimeError` degrades to `.bytedigger`;
  - nothing raises.

**AC2: `drop_engine_owned`.**
- Order is preserved.
- Exactly one `engine_owned_paths_dropped` event carries `step`, `n_dropped`, `content_scan`, and sorted, 20-capped `paths`.
- No event when nothing is dropped.
- An emitter that raises does not propagate.
- `porcelain_path`: `"?? .bytedigger/"` → `.bytedigger/`; `" M src/a.py"` → `src/a.py`; `"R  old.py -> .bytedigger/x"` → `.bytedigger/x`; `'?? ".bytedigger/caf\303\251.json"'` → `.bytedigger/café.json`; `'?? "a b\\c.py"'` → `a b\c.py`; `""` and `"??"` → `""`.
- `drop_engine_owned_porcelain`: keeps order; drops the state lines above (quoted and rename-target included) and blank lines; keeps `R  .bytedigger/x -> src/y.py`; one event with step, `n_dropped`, `content_scan: False`; no event when nothing is dropped.

**AC3: `engine_owned_pathspecs()` and `prune_engine_owned_dirs`.**
- `engine_owned_pathspecs()` returns the exact list and honours the `bdstate` override.
- `prune_engine_owned_dirs` prunes in place.

**AC4: producers on a real tmp git repo.** The fixture is fresh-run shaped:
- committed: `src/a.py`;
- untracked: `.bytedigger/events.jsonl` containing `sk-ant-api03-` plus 40 chars, and `.bytedigger/state.py`;
- modified: `src/a.py`; new: `src/b.py`;
- the symlink `venv -> <tmp outside>/realvenv/` holding `lib.py`;
- an untracked `pkg/` holding `m.py`, `link.py -> <outside>/x.py`, and the dir link `vend -> <outside>/realvenv`;
- no `.gitignore`.

The expected results:
- `_derive_green_paths_from_git(repo) == ["pkg/m.py", "src/a.py", "src/b.py"]`. This covers gate F6.
- `_derive_security_lint_paths({"red_commit_sha": sha1}, {}, repo)[0]`:
  - contains `src/a.py`, `src/b.py`, `pkg/m.py`;
  - contains no path with a `.bytedigger` or `venv` segment, and nothing under `pkg/vend/`, nor `pkg/link.py`.
- The `step` values `"derive_green_paths"` and `"security_lint_paths"` appear in the captured events.
- `_filter_gitignored_paths([".bytedigger/events.jsonl", "src/a.py", "pkg/link.py"], repo) == ["src/a.py", "pkg/link.py"]`. The escaping user link survives the commit filter (gate F3).
- `_verify_green_lint_rules` and `_verify_green_typecheck` are pinned by AST plus `git_diff_files` sharing: each enclosing function calls `drop_engine_owned` with `content_scan=True`.
- `_checkpoint_green_worktree` is called for real (gate F4), with `git_cwd_source="cfg_git_cwd"` and a commit identity set through `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env. After the call:
  - `git show --name-only HEAD` lists `src/a.py` and `src/b.py`;
  - nothing under `.bytedigger/` is in HEAD or the index.
  - On a second repo whose only dirt is `?? .bytedigger/`, the call reports clean and makes no new commit (gate F2).
  - The source holds no `":(exclude)` string literal.
- `_dirty_worktree_guard` on a repo whose only dirt is `?? .bytedigger/` returns the clean / no-error result, not `E_FIX_UNCOMMITTED_CHANGES` (gate F2). RED reads its signature and asserts its documented clean return.
- **Post-self-heal re-read (gate r2 N1, MAJOR).** On the state-only repo plus an untracked `c.py`, call `_dirty_worktree_guard(repo, cfg={}, scratchpad=tmp_path/"sp", pre_fix_sha=<HEAD sha>, git_cwd_source="cfg_git_cwd")` with the git identity env set and `HAL_AUTHORED_BOUNDARY_GATE=0` (the boundary scan is not the unit under test). Expected:
  - the result is `None`;
  - `git show --name-only --format= HEAD` lists `c.py` and no `.bytedigger` path, and HEAD moved;
  - a `fix_integrity_tail_selfheal` event was emitted.
  - Today (and with only the :329 read filtered) the re-read sees `?? .bytedigger/` and the result is `E_FIX_UNCOMMITTED_CHANGES`.
- **Tracked state (gate r2 edge 2).** On a repo where `.bytedigger/events.jsonl` is COMMITTED and then modified (` M`), with no other dirt: `_dirty_worktree_guard(repo)` is `None`, and `_checkpoint_green_worktree` reports clean with no new commit and `.bytedigger/events.jsonl` left unstaged.
- `_commit_fix_tests` (AST, N8): there is an assignment whose target is `test_paths` and whose value is a `_filter_gitignored_paths(...)` call; its line precedes every `if` whose test references `test_paths` and every `git add` argv in the function.

**AC5: walkers.**
- `error_codes.harvest_codes` on a tmp tree with `E_BD94_PROBE` only under `bdstate/`, with the provider overridden to `"bdstate"`, does not harvest it. Positive control: `E_BD94_OK` in `src/x.py` is harvested.
- `".bytedigger"` is not in `HARVEST_EXCLUDE_DIRS`.
- `cyrillic_scan.tracked_files` on a non-git tmp dir:
  - Cyrillic in `.bytedigger/events.jsonl` → not listed (also `bdstate/` under the override);
  - positive control: `src/x.md` is listed.
- `preflight.compute_state_hash` (behavioural, §2.2-11a): on a real repo, the hash is unchanged after writing an untracked `.bytedigger/events.jsonl` and `sub/.bytedigger/n.json`, and after modifying a committed `.bytedigger/state.json`; it changes after writing an untracked `src/new.py` (positive control).
- `preflight._Run._changed` calls `drop_engine_owned` with `step="preflight_changed"` and `content_scan=True` (AST). The old `"preflight_untracked"` step is not used anywhere.

**AC6: lint on the real tree.**
- `tree_scan_lint.check(<bytedigger_engine>, tree_scan_lint.load_inventory()) == []`.
- The keys of the producers 1-8 and 11 are class `"filters"`.
- Every `git-add` key is `"filters"`.

**AC7: the lint catches the omission.** Run on a synthetic package dir with a new `workflows/new_gate.py` that holds one function per form, each with no inventory entry:
- `os.walk(root)`
- `os.scandir(root)`
- `os.listdir(root)`
- `glob.glob(p)`
- `Path(r).rglob("*")`
- `Path(r).iterdir()`
- `git_diff_files(...)`
- `x.status_porcelain()`
- `git_read(["status", "--porcelain"], ...)`
- `run(["git", "-C", d, "status"])`
- `run(["git", "--no-optional-locks", "ls-files"])`
- `_git(top, "ls-files", "--others")`
- `run(["git", "add", "--"] + paths)`
- `run(["git", "diff", "--name-only", sha])`
- a control `run(["git", "diff", sha])`, which is NOT a site
- a control `run(["git", "rev-parse", "HEAD"])`, which is NOT a site
- r3 additions (gate r2 N6a), each in its own function:
  - `glob.iglob(p)` → `glob.iglob`
  - `import glob as _glob` + `_glob.glob(p)` → `glob.glob` (one site only, N6b)
  - `import os as _os` + `_os.walk(r)` → `os.walk`
  - `Path(r).glob("*.py")` → `glob`
  - `x.diff_files(a, b)` and `diff_files(a, b)` → `diff_files`
  - `git_status_porcelain(r)` → `git_status_porcelain`; `ls_files_others(r)` → `ls_files_others`
  - tuple literal `run(("git", "status"))` → `git-status`
  - `run(["git", "-c", "core.quotePath=false", "status"])` → `git-status`
  - `run(["git", "--git-dir", g, "--work-tree", w, "add", "-A"])` → `git-add`
  - `run(["git", "diff", "--name-status", a])`, `--numstat`, `--stat` → `git-diff-names` each
- r3 non-site controls (N4): `payload.get("status")`, `entry.get("status", "error")`, `readiness.guard_git(fn, "add", False)`, `foo(["status", "ok"])`.
- A function `f_lambda` holding `guard(lambda: run(["git", "add", "--", *ps]))` yields exactly one key `workflows/new_gate.py::f_lambda::git-add#0` (edge 6).

The expected results:
- `call_sites` returns exactly the sorted list of expected keys (N9a).
- One "no key" problem per site, naming its exact key (`workflows/new_gate.py::<fn>::<kind>#0`), and none for the controls.
- With `"filters"` entries but no `FILTER_NAMES` reference → "filters without filter call".
- With a `drop_engine_owned` call in the function → `[]`.
- A `git-add` site marked `"not-a-gate"` → "git-add must filter".
- A stale key is reported.
- `"not-a-gate"` with an empty note, or an unknown class, is malformed.
- A module under `security/` is scanned; one under `conformance/` is not.

**AC8.** The `_filter_gitignored_paths` body calls `drop_engine_owned` with `content_scan=False`. This is an AST pin.

**AC9.** `class_i_lint.check(...)` on the real tree `== []`. A guard: it may pass before GREEN, and GREEN must keep it green.

## §4 Degrade-not-fail

| Failure | Outcome |
|---|---|
| Provider failure or invalid dirname | The neutral `.bytedigger` is used. |
| Symlink resolution failure in a content scan | Treated as owned: the path is dropped, with an event. |
| Event emission failure | Swallowed. |

The predicate never raises, and producers keep their existing failure modes.

## §5 Out of scope / follow-ups

- **The bash/TS lane** (`scripts/ts/build-phase-gate.ts`, `hooks/`): root-level legacy artifacts (`build-*.md`, `build-*-output.log`, `build-state.yaml`). Separate issue.
- **HAL host state outside the dirname** (gate M4): `SHARED/state/*` in HAL's provider. A future provider hook `engine_owned_relpaths()` would need a `config_provider.py` change, which is blocked by #204. Follow-up issue.
- **bd#93 (PR #204)**: its run-scoped log under `~/.bytedigger/runs` is outside every repo root, so no tree scan sees it. A worktree under a `.bytedigger` ancestor is handled by R1's root-relative rule.
- **Inventory ordinals vs PR #204** (gate M7): #204 touches `run.py` and `event_log.py`. Whichever of the two PRs merges second regenerates its `tree_scan_inventory.json` keys for those files. This is the same duty bd#150 declared.
- **`phases/*.md` prose** about these gates: after lot 1570 P3b2 lands.

## §6 r1 → r2

| Gate r1 | Disposition |
|---|---|
| F1 | §2.3 broadened: the walk family, the helper names, the option-skipping verb rule, the varargs form, and `diff` with name flags. Scope is the whole package minus `conformance/` and `tests/`. AC7 has one case per kind and per form, plus non-site controls. |
| F2 | §2.2-7 adds the dirty-guard and checkpoint dirty-check filter. AC4 covers the clean-only-state repos. |
| F3 | The `content_scan` split: R2 applies only to read sets. AC4 checks that the escaping link survives `_filter_gitignored_paths`. |
| F4 | AC4 calls `_checkpoint_green_worktree` for real. |
| F5 | The non-dot `"bdstate"` override, plus a positive control. |
| F6 | `pkg/` with a symlinked file and a symlinked dir in AC4. |
| M1 | §2.2-6. Every `git-add` site must be `"filters"` (lint rule plus AC6/AC7). |
| M2 | Declared limitation in §2.3. |
| M3 | R1 is root-relative, with `normpath` and `..` handling. |
| M4 | Declared follow-up (§5). |
| M5 | §2.2-3 and §2.2-4. |
| M6 | The header is fixed, `load_inventory` is in the API, and the step values are pinned. |
| M7 | Declared (§5). |
| M8 | §2.1 states the Python 3.9 constraints. |

## §7 r2 → r3

| Gate r2 | Disposition |
|---|---|
| N1 MAJOR | AC4 post-self-heal test (state + `c.py`, scratchpad, `pre_fix_sha`, `cfg_git_cwd`, boundary gate off). §2.2-7 names both reads and the new `drop_engine_owned_porcelain`. |
| N2 | §2.2-11b: `_Run._changed` is a read set, `content_scan=True`, step `preflight_changed`. §2.2-11a: the fingerprint uses pathspecs and keeps user links. Walker R2 choice declared. |
| N3 | §2.3 token list keeps placeholders; option values consume the next token. |
| N4 | List form needs a `"git"` first token or a `git` callee; varargs only for a callee named `git`/`_git`. AC7 non-site controls. |
| N5 | R1 step 2 resolves both path and root. AC1 alias case. |
| N6 | AC7 r3 forms; kind precedence stated. |
| N7 | Hermetic git fixture in §3. |
| N8 | §2.2-6 assignment and order; AC4 AST pins both. |
| N9a-e | sorted `call_sites`; `_commit_fix_tests`; `baseline_tree` removed; §1 preflight rows split; error_codes comment rewritten in §2.2-9. |
| Edge 2 tracked state | Porcelain filter + pathspecs; AC4 test. |
| Edge 3 quotePath | `porcelain_path` C-unquotes; AC2 units. `_autocommit_fix_tail` parse: declared follow-up. |
| Edge 4 decoy link | R2b; AC1. |
| Edge 5 all-state manifest | N8 ordering. |
| Edge 6 lambda | §2.3 lambda scope; AC7 `f_lambda`. |
| Edge 7 tuple-argv fingerprint | §2.2-11a + AC5 behavioural; lint limitation declared. |
| Edge 8 glob double count | N6b precedence. |
