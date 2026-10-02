# bd#168: Phase 5 typecheck baseline sees untracked files

**Status:** r1 · **Tier:** 2 (one engine prod `.py`, Option D) · **Class:** LOCAL ·
**Chokepoint:** `phase_5_implement._tree_identical_to_head_for_paths`, the only clean-tree check in front of
the typecheck baseline. **Source:** bd#168 (parity with hal-v2#2090).

## §1 Problem (measured on `24dbd13`)

1. `_verify_green_typecheck` collects GREEN paths with `git_diff_files(red_sha, ..., untracked=True)`, so a
   brand-new untracked module **is** typechecked (`:6125`).
2. On findings it asks `_compute_baseline_typecheck_count` for the HEAD count. That helper first calls
   `_tree_identical_to_head_for_paths` (`:4763`), which runs only `git diff --quiet HEAD -- <paths>`.
   `git diff` does not see untracked files: rc 0 → "identical" → baseline `None`.
3. `delta_verdict(None, n)` is `baseline_unavailable` → `would_block` False → the type errors become warnings.
   A GREEN that only **adds** a module with a type error is never blocked.
4. The helper also returns False on an exception without any event, and treats every non-zero rc
   (including git's 128 failure) the same as "differs" without logging.
5. Existing AC4 test (`test_gh1612b...::test_ac4`) stages the new file (`git add`), which `git diff HEAD`
   does see, so it never covered the untracked case.

## §2 Design

`_tree_identical_to_head_for_paths(paths, git_cwd)` returns True only when **both** reads succeed and show
nothing:

1. `git diff --quiet HEAD -- <paths>` (unchanged). rc 0 → go on. rc 1 → return False. Any other rc → emit
   the event below, return False.
2. `git ls-files --others --exclude-standard -- <paths>` through the same `git_port.git_read` seam
   (timeout 30). rc != 0 → emit event, return False. Non-empty stripped stdout → return False.
   Empty → return True.
3. Any exception from either read → emit event, return False (never raises).

Event (via `_emit_safe`, severity `warning`): `baseline_tree_identity_check_failed`, payload
`{"phase": 5, "step": "tree_identical_to_head_for_paths", "reason": <str>}` where `reason` is
`"diff rc=<rc>"`, `"ls-files rc=<rc>"`, or the exception's `str()`.

"Not identical" on failure is the safe direction: the caller then pays for a real baseline worktree, which
has its own degrade path (`baseline_tree_unavailable` → `None`). Ignored files (`--exclude-standard`) stay
out of scope, as in the issue.

Not in scope: `_compute_baseline_typecheck_count` body, `delta_verdict`, the worktree provider, HAL port
(done later via the drift tool, label `needs-hal-sync` on hal-v2#2090).

**Design constraints from the issue.** The check is plain git (deterministic, no model step). No provider,
LLM or Jev call is on this path, so "provider down" and "subscription vs API backend" do not change its
behaviour; AC6 pins that the check does not touch the LLM dispatch seam.

## §3 Acceptance criteria

Real temp git repos, real `git` (only mypy is canned where a count is needed, as in the existing gh1612b
tests).

- **AC1 (untracked → not identical)** Repo with one commit; write untracked `new.py`;
  `_tree_identical_to_head_for_paths([abs new.py], repo)` is False.
- **AC2 (tracked unchanged → identical)** Committed `a.py`, untouched: result True. No event emitted.
- **AC3 (tracked modified → not identical)** Committed `a.py` edited: False (pin today's behaviour).
- **AC4 (git failure → not identical, logged)** (a) `git_port.git_read` patched to raise → False and exactly one
  `baseline_tree_identity_check_failed` event with `reason` == the exception text. (b) `git_read` returns
  rc 128 for the `diff` call → False and an event whose `reason` starts with `"diff rc=128"`.
  (c) `diff` rc 0 but `ls-files` rc 128 → False, event `reason` starts with `"ls-files rc=128"`.
- **AC5 (end to end through the gate)** Real repo with a RED commit; GREEN adds untracked `new.py` holding
  `x: int = "s"`; mypy canned to report one error on `new.py` for the live tree.
  (a) `_compute_baseline_typecheck_count([new.py], repo, "cfg_git_cwd")` returns `0` (not `None`).
  (b) `delta_verdict(0, 1, enforce=True).would_block` is True.
  (c) Control: untracked `ok.py` with no error and mypy canned clean → `_verify_green_typecheck` (or the
  baseline helper, whichever the test fixture reaches) does not block.
- **AC6 (no provider on the path)** During AC1/AC2 calls, `llm_subprocess.invoke_llm_subprocess` is patched
  to raise; the calls still return the expected values (the check never reaches a provider).
- **AC7 (regression)** All of `tests/test_gh1612b_typecheck_baseline_no_tree_mutation.py` and
  `tests/test_green_typecheck_baseline_5C14EF32.py` stay green.
