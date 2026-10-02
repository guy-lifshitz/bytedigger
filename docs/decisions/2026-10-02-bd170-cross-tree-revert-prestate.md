# bd#170: cross-tree auto-revert never resets a path that was dirty before the worker ran

**Status:** r2 (gate r2 APPROVED; gate r1 REJECTED: 1 MAJOR + 5 MINOR, see `2026-10-02-bd170-gate-r1.md`) · **Tier:** 2 (engine prod `.py`, Option D) · **Class:** LOCAL ·
**Chokepoint:** `phase_workflows_common._maybe_emit_cross_tree_warning`, the only owner of the
`_revert_cross_tree_modifications` hazard (`mutating_git_lint.MANIFEST_BOUNDED_WRITE_SITES`).
**Source:** bd#170 (residual of hal-v2#1570 / OFI 24d7359f).

## §1 Problem (measured on `24dbd13`) — residual confirmed, not already covered

1. `_maybe_emit_cross_tree_warning(result, worktree_root)` (`:362`) runs after the worker. It lists tracked
   dirty paths in the **main** checkout (`_verify_no_cross_tree_edits`, `git status --porcelain`), keeps the
   ones in the build's own worker manifest (GH1562 `_filter_cross_tree_to_worker_manifest`) and runs
   `git -C <main> checkout -- <path>` on each (`_revert_cross_tree_modifications`, `:298`).
2. Nothing records the main checkout's state **before** the worker ran. If the user had uncommitted edits on
   `src/x.py` and the worker then also writes `src/x.py` cross-tree, `src/x.py` is owned → reset to HEAD →
   the user's edits are lost. gh1562 tests cover "unowned dirty file survives", never "owned file that was
   already dirty".
3. Three callers, all after a worker LLM call: `phase_5_implement._invoke_red_llm` (`:1586`),
   `phase_5_implement._invoke_green_llm` (`:7598`), `phase_6_review._invoke_fix_llm` (`:2638`).

## §2 Design

**New helper** `phase_workflows_common._snapshot_main_checkout_state(worktree_root: Path) -> dict`. Never
raises. Read-only (`git_port.git_read` only; no `hash-object -w`).

- Resolves the main root the same way `_verify_no_cross_tree_edits` does (first `worktree` entry of
  `git worktree list --porcelain`). If `worktree_root` is the main root or under it: returns
  `{"ok": True, "main_repo_root": <str>, "dirty": {}}` (no cross-tree possible).
- Else `git status --porcelain` in the main root; tracked dirty paths (same parse as
  `_verify_no_cross_tree_edits`: skip `??`, path = `line[3:].strip()`), then one
  `git hash-object -- <p1> <p2> ...` for their working-tree blobs.
  Returns `{"ok": True, "main_repo_root": <str>, "dirty": {relpath: blob_sha | None}}`. A `hash-object`
  failure sets every blob to `None` (the path still counts as dirty).
- Any failure of `worktree list` / `status` (exception, timeout rc 124, rc != 0): returns
  `{"ok": False, "reason": <str>}`.

**Wrapper** `_maybe_emit_cross_tree_warning(result, worktree_root, pre_state: dict | None = None)`. After the
GH1562 manifest filter produces `owned`, split it again:

- `pre_state` is not a usable snapshot → every owned path is held back, reason `"pre_state_unavailable"`
  (fail closed: no snapshot, no reset). Usable means all of: a dict, `pre_state.get("ok") is True`,
  `pre_state.get("dirty")` is a dict, and `os.path.realpath(pre_state.get("main_repo_root"))` equals the
  realpath of the `main_repo_root` detection found now (a stale or other-repo snapshot is unusable; gate r1 F2).
  The check never raises.
- Owned path present in `pre_state["dirty"]` → held back, reason `"dirty_at_start"`.
- The rest → `_revert_cross_tree_modifications` exactly as today.

When anything is held back: `result.data["cross_tree_prestate_refused_files"]` and the same key in
`result.metadata` get the list, and one event is emitted:
`cross_tree_revert_prestate_refused` (severity `warning`) with
`{"step", "main_repo_root", "files": [...], "reason": "dirty_at_start" | "pre_state_unavailable",
"changed_since_start": {path: bool | None}}`. `changed_since_start[p]` is `current blob != start blob`,
from one `git hash-object` at revert time; `None` when either blob is unknown or the reason is
`pre_state_unavailable`. Held-back paths are not reverted: the user's edit and the worker's edit both stay
on disk, and the event says so. (Restoring the pre-state bytes would need a write into the main checkout;
refusing is the issue's other allowed option and adds no new mutating site.)

**Callers.** Each of the three callers takes the snapshot immediately before its `invoke_llm_subprocess`
call (resolving `worktree_root` there, as it already does after the call) and passes it as
`pre_state=`. Resolving scratchpad / worktree root before the call can raise `ValueError` on paths that
today only resolve it after an ok result: the caller catches `ValueError` around the pre-call resolution
and uses `pre_state=None` (fail closed), the same guard `_pre_boundary_snapshot` already uses (gate r1 F3). In `_invoke_red_llm` it sits next to `_boundary_before`. The crash-resume path of
`_invoke_green_llm` (no worker call) is untouched.

**Design constraints from the issue.** All of this is git (deterministic, no model step, no provider). A
failed or down provider returns `status != "ok"` and the wrapper already returns before any revert, so
nothing changes there. The manifest the split works on comes from any backend
(`harness_tool_record` for the subscription claude-subprocess backend, `orchestrator_observed` in-session,
backends registered with their own source such as `git_diff` for API-key backends); the split does not look at
the source. A snapshot failure degrades to "hold back", never to a crash or a reset.

**Known limits (gate r1 F6).** (1) Race: a path clean at snapshot time that the user edits *during* the
run and the worker also writes is still reset; closing that needs a lock on the main checkout, out of scope.
(2) Hashing is one batched `git hash-object`; one unhashable path (rename `a -> b`, quoted name, deleted
file) sets every blob to `None`. Membership in `dirty` is unaffected (same parse as detection), so the
hold-back decision is still right; only `changed_since_start` degrades to `None`. (3) The in-function retry LLM calls of `_invoke_green_llm`
(`phase_5_implement.py:7668`) and `_invoke_fix_llm` (`phase_6_review.py:2743`) run after the single
cross-tree check and get none, as today (gate r2 F3). GREEN binds the snapshot with a plain `=` (gate r2 F1).

Not in scope: detection (`_verify_no_cross_tree_edits`), the GH1562 filter, `_revert_cross_tree_modifications`
body, untracked files (`??` are already never reverted), HAL port.

## §3 Acceptance criteria

Real main checkout + secondary worktree pair (as in `tests/test_gh1562_cross_tree_revert_manifest_bounded.py`),
real git. Only `_emit_safe` is captured.

- **AC1 (dirty before run survives)** Main has tracked `src/x.py` committed; user appends `USER\n`
  (uncommitted); snapshot taken; then the "worker" appends `WORKER\n` to main's `src/x.py`; result manifest
  owns `src/x.py`. After the wrapper: file bytes == committed + `USER\n` + `WORKER\n` (nothing reset), one
  `cross_tree_revert_prestate_refused` event with `reason == "dirty_at_start"`, `files == ["src/x.py"]`,
  `changed_since_start == {"src/x.py": True}`; `result.data["cross_tree_prestate_refused_files"] == ["src/x.py"]`.
- **AC2 (clean before run reverted as today)** Same, but no user edit before the snapshot → file back to the
  committed bytes, `cross_tree_edit_reverted` emitted, no `cross_tree_revert_prestate_refused` event.
- **AC3 (mixed)** Owned `a.py` dirty at start + owned `b.py` clean at start → `b.py` reverted, `a.py` kept;
  event lists only `a.py`.
- **AC4 (no snapshot → fail closed)** `pre_state=None` and `pre_state={"ok": False, "reason": "x"}`: owned
  file not reverted; event `reason == "pre_state_unavailable"`, `changed_since_start == {p: None}`.
- **AC5 (snapshot helper)** (a) clean main → `{"ok": True, "dirty": {}}` with `main_repo_root` set.
  (b) dirty tracked `a.py` + untracked `u.py` → `dirty` keys == `{"a.py"}`, blob == `git hash-object a.py`.
  (c) `git_port.git_read` raising, and returning rc 128 for `status` → `ok is False`, no exception.
  (d) `worktree_root` == main root → `ok True`, `dirty == {}`.
- **AC5e (hash failure)** dirty tracked `a.py` plus `git_port.git_read` returning rc 128 for the
  `hash-object` call only → `ok True`, `dirty == {"a.py": None}`.
- **AC1b (unchanged since start)** dirty-at-start owned `src/x.py` that the worker did not change after the
  snapshot → held back, `changed_since_start == {"src/x.py": False}`; `result.metadata` carries the same
  `cross_tree_prestate_refused_files` as `result.data`.
- **AC4b (unusable snapshot shapes)** `pre_state` = `{"ok": True, "main_repo_root": <other dir>, "dirty": {}}`,
  `{"ok": True, "main_repo_root": <main>}` (no `dirty`), `{"ok": True, "main_repo_root": <main>, "dirty": []}`,
  and the string `"x"` → owned file not reverted, `reason == "pre_state_unavailable"`, no exception.
- **AC6 (backend independence)** AC1 run twice with `manifest_source` `"harness_tool_record"` and
  `"git_diff"` (registered via the test's own backend registration or whatever `manifest_from_result`
  accepts — if `"git_diff"` is not accepted, use `"orchestrator_observed"`) → same outcome both times.
  (This pins that the split ignores the source; it cannot tell backends apart and is not meant to.)
- **AC7 (callers pass the snapshot)** AST check over `phase_5_implement._invoke_red_llm`,
  `_invoke_green_llm`, `phase_6_review._invoke_fix_llm`: each calls `_snapshot_main_checkout_state` before its
  `invoke_llm_subprocess` call, and its `_maybe_emit_cross_tree_warning` call passes a `pre_state` keyword.
  Also: the value passed as `pre_state=` is a Name bound by an assignment whose value is the
  `_snapshot_main_checkout_state(...)` call (or a conditional that is that call or `None`), in the same
  function body, not inside a nested def (gate r1 F4).
- **AC8 (regression; the only allowed edits to existing tests)**
  (a) `test_gh1562_cross_tree_revert_manifest_bounded.py`: calls that expect a revert pass
  `pre_state={"ok": True, "main_repo_root": str(main), "dirty": {}}`; assertions unchanged.
  (b) Two-argument stubs of `_maybe_emit_cross_tree_warning` that the new `pre_state=` keyword would
  break gain `**_kw` and nothing else: `test_gh1157_invoke_red_llm_failed_event.py:111`,
  `test_green_complete_resume_gh483.py:399` and `:452`, `test_subagent_return_discipline_fix_DACC8E2B.py:366-367`
  (gate r1 F1). These files and `test_phase5_bounded_run_BFEC3E71.py`, `test_phase6_bounded_run_7D2BAD22.py`,
  `test_261_PH56SPLIT_stage0_common.py` stay green. `find_unclassified_sites` on the real tree stays `[]`:
  the `_revert_cross_tree_modifications` call stays in `_maybe_emit_cross_tree_warning`'s own body.
