# bd#88 (P7) — baseline once on the RED commit; GREEN delta by test ID

**Status: FROZEN** · **Class:** SYSTEMATIC · **Chokepoint:** `_verify_green_passing`
(`engine_py/bytedigger_engine/workflows/phase_5_implement.py`).

## §1 Problem (measured on `329ffb3`)

1. **`-x` makes counts meaningless.** `_runner_for_path` ran pytest with `-x`, so a group reported
   at most one failure. A delta of "1 before / 1 after" said nothing about which test failed.
2. **The baseline was taken after GREEN, by stash.** `_compute_baseline_failed` stashed GREEN's
   working tree, re-ran the groups and popped. That cost one extra run per verify, touched the tree
   under test, and returned "unavailable" (never blocking) whenever GREEN left no uncommitted change.
3. **Counts mask swaps.** One preexisting sibling failure fixed and one new failure introduced is a
   net delta of zero.
4. **The lane-2 gate never gave a verdict.** `known-reds.md` does not exist at the repo root, so
   `baseline_delta_gate.py` exited 2 ("ledger not found") on every call. `_baseline_delta` reported
   `driver_error` and discarded stderr. Its baseline resolved a cache keyed by `origin/main` that
   nothing writes.
5. The HAL GH1313 release (a RED test still failing "as before" lets GREEN pass) does not exist in
   bytedigger. The invariant is pinned so it cannot be introduced.

## §2 Design (DesignReview: option B, APPROVE WITH CONDITIONS)

- **Runner prefix:** `--tb=no -q -rfE --continue-on-collection-errors` and no `-x`. Every failing
  id is reported, and one import error does not abort the other files.
- **RED paths:** the RED run (`verify_red_fails_mechanically`) is the RED-commit baseline. It uses
  the same cwd, argv and venv as GREEN's run, so the ids agree by construction. It is recorded under
  `prev.data["red_commit_sha"]` only for a real RED:
  - the verdict is ok;
  - no group passed (not the GH483 resume or GH1034 restore paths);
  - every py run completed (`run_fail_ids`);
  - at least one id was parsed.
- **Siblings:** siblings are known only after GREEN touched prod files. Their baseline is taken once
  per `red_sha`, and only when a sibling currently fails:
  - it runs in a detached temp worktree of `red_sha`, never by stash;
  - that run gets an explicit `--rootdir`, cwd mirrors `git_cwd`'s place in the repo, and
    PYTHONPATH puts the worktree first so imports resolve to `red_sha` code;
  - the runner is resolved from `git_cwd`;
  - presence at `red_sha` comes from one `git ls-tree -z`. A git failure is unavailable, never
    "absent".
- **Cache:** `<scratchpad>/baseline/<red_sha>.pytest.json` (covered realpaths and fail ids) plus
  `<red_sha>.pytest.fails`, the file `baseline_delta_gate.py --base-sha/--cache-dir` reads. An
  unavailable result is never cached. A corrupt cache is reported.
- **Verdict:** `net_new_delta.id_delta_verdict`. A baseline id covers a current id when they are
  equal or the baseline id is a `::` prefix of it: a file-level collection error at RED covers that
  file's test failures at GREEN.
  - **Siblings fail closed:** an untrusted baseline or an unavailable current run blocks as the
    existing terminal `sibling_net_new`.
  - **The RED group fails open:** without a baseline the step still errors, as the existing
    recoverable `E_GREEN_NOT_PASSING`. An id the RED commit did not fail is the existing terminal
    `net_new_delta`.
  - **A RED test that is still red is never released**, preexisting or not.
- **Lane 2:**
  - a missing ledger is an empty ledger (`ledger_source: "missing"`); an unreadable one is still a
    driver error;
  - `driver_error` carries the stderr tail;
  - py groups get the RED-commit baseline when it was recorded;
  - the gate and the engine share one id grammar and one coverage rule (`net_new_delta`).
- **Unchanged:** step lists, `recoverable` values, `retry_from_step` (P4, #85).

## §3 Accepted risks

- **The RED-group delta fails open** without a recorded baseline (GH483 resume, GH1034 restore, no
  scratchpad, cfg-only `red_sha`). The step still fails recoverably; only the terminal
  "new id" classification is lost.
- **The worktree is a clean checkout.** A sibling test that depends on untracked or gitignored
  inputs can fail at the baseline for that reason alone and hide a real regression in the same id.
- **Sibling fail-closed is terminal** on an unavailable current run (timeout, rc 2/3/4, a failure
  without ids). A deterministic verdict was preferred over skipping the gate.
- **Longer runs without `-x`.** RED/GREEN groups run to completion, including the five
  reproducibility runs, under the existing 120 s caps.

## §4 Out of scope

Full-suite delta in phase 8 and the phase 6 post-fix test scope (`red_test_paths ∪ siblings`) are
follow-ups.
