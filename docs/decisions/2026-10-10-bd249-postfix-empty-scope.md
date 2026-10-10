---
red_tests:
  - engine_py/tests/test_bd249_postfix_empty_scope.py
sibling_tests:
  - engine_py/tests/test_bd90_postfix_test_scope.py
  - engine_py/tests/test_phase_6_post_fix_pytest_gate_7A940850.py
  - engine_py/tests/test_4961254A_commit_manifest_inversion.py
  - engine_py/tests/test_bd85_retry_budgets.py
  - engine_py/tests/test_bd249_postfix_scope_guards.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_6_review.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - docs/events.md
  - engine_py/bytedigger_engine/error_codes.py
  - engine_py/bytedigger_engine/ERROR_CODES.md
  - engine_py/ERROR_CODES.md
red_pins:
  - engine_py/tests/test_bd249_postfix_scope_guards.py
---

# bd#249: an empty post-fix test scope is an error, not a pass

**Status: r2 (gate r1 REJECT F1-F3 majors and F4-F7 minors folded), TIER2.** Follow-up to bd#90 (#253), which made the scope builder pull in the persisted RED tests, manifest tests and sibling tests. What is still open is the other half of #249: when that builder returns nothing, `_run_pytest_post_fix` skips and the step returns ok, so a fix lands without any test run. The fix-and-recheck loop is #250 and is not touched here.

| | |
|---|---|
| **Tier** | TIER2. One branch in `workflows/phase_6_review.py`, one catalog entry, one error-code registration, one docs row, two test files. |
| **Class** | SYSTEMATIC. Chokepoint: the post-fix test-scope builder's consumer, `_run_pytest_post_fix` (the `if not py_test_paths` branch after `_post_fix_test_scope`). |
| **LLM calls added** | None. Pure code on a list of paths; no provider, no auth mode is consulted. |

## Problem (on `4231030`)

`_post_fix_test_scope` can return `([], counts)` when there is no persisted RED file, no test file in the manifest and no sibling of a changed source. `_run_pytest_post_fix` then emits `post_fix_pytest_skipped(no_test_scope)` and returns `status="ok"`. The empty scope is treated as green.

## Deviation from the issue, stated plainly

The issue says an empty scope is an error. This lot ships that behaviour behind a flag that is OFF by default (Guy's rule: shadow first, then enforce, every new mechanism with a switch). Until the flag owner flips the default, an empty scope still passes, but it is now recorded by an event. The flip is a separate decision by the owner `guy-lifshitz`, taken from shadow data once the `post_fix_empty_scope` events show no false positives (see the docs-only exemption below). The issue's kill switch is met by the flag itself: `"0"` restores the pre-lot behaviour at any time, including after the default is flipped.

## Design

Shadow first, then enforce, with a switch.

Flag `HAL_POST_FIX_EMPTY_SCOPE_ENFORCE` (aliases `BD_...`, `BYTEDIGGER_...` through the existing provider lookup), read with `get_config()` in the empty-scope branch only:

| value | mode | behaviour on empty scope |
|---|---|---|
| unset | shadow | exactly today's behaviour (skip event, `status="ok"`), plus one `post_fix_empty_scope` event with `mode="shadow"` |
| `"0"` | shadow | same as unset. This is the kill switch: it restores the current behaviour with no revert |
| anything else (`"true"`, `"yes"`, empty) | shadow | same as unset; only the exact string `"1"` enforces (the provider's `flag()` rule) |
| `"1"` | enforce | `post_fix_empty_scope` event with `mode="enforce"`, no `post_fix_pytest_skipped`, return `status="error"`, `error_code="E_POST_FIX_EMPTY_SCOPE"`, `recoverable=False`, `data=None`; pytest is not invoked |

Event payload: `{phase: 6, step: "run_pytest_post_fix", mode, n_red, n_manifest, n_sibling, n_total, n_manifest_files, n_manifest_py}` (the leg counts that `post_fix_pytest_scope` carries, plus the number of manifest files and of `.py` files among them, so shadow data tells "fix touched code and nothing tests it" from "fix touched no code").

**Docs-only exemption.** An empty scope is a failure only if there was something testable to cover. If the manifest is non-empty and contains no `.py` file at all (a docs-only or config-only fix), the step skips as today (`status="ok"`, `post_fix_pytest_skipped(no_test_scope)`) and the `post_fix_empty_scope` event carries `mode="exempt"`, in either flag state. An empty manifest, or a manifest with any `.py` file, is not exempt.

The enforce decision (flip the default) is a separate, later call by the flag owner, made from shadow data. This lot adds no date-based auto-flip and no `flip-by:` token.

Nothing changes when the scope is non-empty. The two earlier skips in the step (`no_git_repo`, `no_sha_boundary`) happen before the scope is built and are a known limit, not part of this lot. The scope builder itself is unchanged. Known limit: a path given in `prev.data["red_test_paths"]` passes through unchecked for existence (bd#90 AC4), so a missing path there makes a non-empty scope and pytest reports it as an infra error; only the persisted RED file is existence-filtered. The frozen RED tests enter through the persisted RED file or `prev.data["red_test_paths"]` (bd#90 AC1/AC5), and AC4 below pins that on a real commit.

Catalog: one `flags_catalog.FLAGS` entry (`kind: "flag"`, default `"0"`, module `workflows/phase_6_review.py`, `owner`, `provenance` starting `introduced: #249 - ...`), so `scripts/flag_owner_lint.py` stays green. Docs: one row for `post_fix_empty_scope` in `docs/events.md` next to the other `post_fix_pytest_*` events, and `E_POST_FIX_EMPTY_SCOPE` registered in `engine_py/bytedigger_engine/error_codes.py` and listed in both `ERROR_CODES.md` files (`engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md`), as `python3 -m bytedigger_engine.error_codes --check` (run from `engine_py/`) requires.

## Acceptance (RED, real path)

Tests drive `_run_pytest_post_fix` itself on a real tiny git repo (`git init`, a real commit for the "fix commit"), with a real scratchpad. `run_test_command` is replaced by a sentinel that records calls (it is the process boundary; the empty-scope ACs assert it is never reached). `_emit_safe` captures events. Flag values are set through the environment.

- AC1 (enforce): flag `"1"`; the manifest lists only `pkg/module.py` (committed in a real fix commit); no persisted RED file, no manifest tests, no sibling tests tracked. Result: `status="error"`, `error_code="E_POST_FIX_EMPTY_SCOPE"`, `recoverable=False`; one `post_fix_empty_scope` event with `mode="enforce"` and `n_total == 0`; no `post_fix_pytest_skipped`; sentinel not called.
- AC2 (shadow, unset): same repo and input, flag unset. Result: `status="ok"`, exactly one `post_fix_pytest_skipped` with `reason="no_test_scope"`, exactly one `post_fix_empty_scope` with `mode="shadow"`; sentinel not called.
- AC3 (kill switch and alias): flag `"0"` behaves as AC2. `BD_POST_FIX_EMPTY_SCOPE_ENFORCE=1` (with the `HAL_` name unset) behaves as AC1. A set `HAL_` value wins over the alias.
- AC4 (reference case): flag `"1"`; the manifest lists `pkg/module.py` (real fix commit); the persisted RED file lists `tests/test_frozen.py` (tracked, on disk). Result: scope is non-empty and its pytest argv contains `tests/test_frozen.py`; the sentinel is called once; no `post_fix_empty_scope` event; `status="ok"`.
- AC5 (frozen test missing, persisted-file path only): flag `"1"`; the persisted RED file lists only `tests/test_gone.py`, which is not on disk; no other leg. Result: the same error as AC1 (a frozen test that cannot be found does not count as scope).
- AC5b (docs-only exemption): flag `"1"`; manifest `["docs/notes.md"]`, no other leg: `status="ok"`, one `post_fix_pytest_skipped(no_test_scope)`, one `post_fix_empty_scope` with `mode="exempt"`, `n_manifest_py == 0`, `n_manifest_files == 1`; pytest not called. Same with the flag unset. An empty manifest with the flag `"1"` is NOT exempt (error as AC1, `n_manifest_files == 0`).
- AC5c (flag values): flag `"true"` behaves as unset (shadow); `BYTEDIGGER_POST_FIX_EMPTY_SCOPE_ENFORCE=1` with `HAL_`/`BD_` unset enforces.
- AC6 (neighbours, not empty): flag `"1"`; no RED file, a tracked `tests/test_module_extra.py` that is a stem sibling of the changed `pkg/module.py`. Result: the sibling is in argv, no error.
- AC7 (auth mode): AC1, AC2 and AC4 are run once with `ANTHROPIC_API_KEY` set and `CLAUDE_CODE_OAUTH_TOKEN` unset, and once the other way round; outcome, error code and argv are identical in both (the builder consults no provider).
- AC8 (registry): `FLAGS["HAL_POST_FIX_EMPTY_SCOPE_ENFORCE"]` exists with `kind == "flag"`, module `workflows/phase_6_review.py`, default `"0"`, a non-blank `owner` and a `provenance` starting `introduced:`; `python3 scripts/flag_owner_lint.py` exits 0; `docs/events.md` contains a `post_fix_empty_scope` row; `E_POST_FIX_EMPTY_SCOPE` is a key of `error_codes` and appears in both `ERROR_CODES.md` files; `python3 -m bytedigger_engine.error_codes --check` (run from `engine_py/`) exits 0. Shadow/exempt/enforce payloads carry `phase == 6`, `step == "run_pytest_post_fix"` and the count fields; AC1's `result.error` is non-empty.
- AC9 (stay green, unedited): `test_bd90_postfix_test_scope.py`, `test_phase_6_post_fix_pytest_gate_7A940850.py`, `test_4961254A_commit_manifest_inversion.py`, `test_bd85_retry_budgets.py`, plus the flags-catalog consistency tests that already run in the full suite.

## Not touched

The scope builder's legs, ordering and cap; pytest timeout and baseline-delta gate; the fix-and-recheck loop (#250); review aggregation; prompts.
