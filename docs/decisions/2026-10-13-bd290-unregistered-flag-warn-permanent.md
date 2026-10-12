---
red_tests:
  - engine_py/tests/test_bd290_flag_unregistered_warn.py
red_pins:
  - tests/test_bd89_p3d_flag_owner_lint.py
sibling_tests:
  - engine_py/tests/test_bd_flip_horizon_guard.py
  - tests/test_bd89_p3d_flag_owner_lint.py
  - engine_py/tests/test_gh1179_red_write_boundary.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_5_implement.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - engine_py/bytedigger_engine/error_codes.py
  - engine_py/ERROR_CODES.md
  - engine_py/bytedigger_engine/ERROR_CODES.md
  - scripts/flip_horizon_ledger.json
  - CHANGELOG.md
---
# bd#290: unregistered-flag check becomes a permanent warning; HAL_FLAG_UNREGISTERED_ENFORCE is retired

Slice of #89. Refs #290, #89. Base: origin/main @ 74bed46.

## 1. Problem

`HAL_FLAG_UNREGISTERED_ENFORCE` (default off, `flip-by:2026-07-24`, ledger extension to 2026-11-03) is a
rollout flag nobody turned on. From 2026-11-04 `test_ac7_real_catalog_real_ledger_real_today_clean`
turns CI red for it. The issue proposed to switch the stop on. The open question in the issue
(behaviour on a project that reads its own `HAL_*` variables) was checked in code and the stop is a
false positive:

- `_verify_green_lint_rules` (`workflows/phase_5_implement.py`) scans the lot's `git_cwd`, which is the
  project being built, not bytedigger.
- `flags_catalog.unregistered_tokens_in_files` reads each changed `.py` file whole (not only diff
  lines) and compares every `HAL_*` accessor token with bytedigger's own `FLAGS`.
- Measured on a host tree (1158 `.py` files): 114 `HAL_*` tokens are not in bytedigger's catalog.
  With the stop on, any lot touching one of those files would end in `E_FLAG_UNREGISTERED`.

Decision (MGR, recorded on the issue): take the fallback. Delete the stop branch and the flag; keep a
permanent warning. Narrowing the check (diff-only or own-tree-only) is a new design and is out of
scope.

## 2. Change

1. `workflows/phase_5_implement.py`, `_verify_green_lint_rules`: when `HAL_FLAG_UNREGISTERED_GATE` is
   on and tokens are unregistered, always take the warn path: emit `flag_unregistered_warn`
   (`{"step", "tokens"}`), set `flag_unregistered_warnings`, continue. The `get_config().flag(...)`
   read, the `RecoverableGateMixin.gated_step_result(...)` return and the `_fu_*` locals are removed.
   The `gate_disabled` branch for `HAL_FLAG_UNREGISTERED_GATE=0` is unchanged. The "warn-only rollout
   flip-by" comment is replaced by a comment saying the check is a permanent warning and why.
2. `error_codes.py`: remove `E_FLAG_UNREGISTERED` and `E_FLAG_UNREGISTERED_CAP2` (unreachable after 1).
   Remove the same two lines from `engine_py/ERROR_CODES.md` and
   `engine_py/bytedigger_engine/ERROR_CODES.md`.
3. `flags_catalog.py`: delete the `HAL_FLAG_UNREGISTERED_ENFORCE` entry. Register
   `HAL_SECURITY_RULES_PATH` (kind `path`, default `None`, module `workflows/phase_05_inject.py`) and
   `HAL_STATUS_PROBE_URL` (kind `str`, default `https://status.claude.com/api/v2/status.json`, module
   `lib/reference_backends/agent_sdk.py`), each with a one-sentence description and no `flip-by`
   token. Update the `HAL_FLAG_UNREGISTERED_GATE` description: it now disables the warning.
4. `scripts/flip_horizon_ledger.json`: delete the `HAL_FLAG_UNREGISTERED_ENFORCE` entry.
5. `tests/test_bd89_p3d_flag_owner_lint.py`: delete the `HAL_FLAG_UNREGISTERED_ENFORCE` line of
   `PINNED_DEFAULTS`.
6. `CHANGELOG.md`: one line under Unreleased/Changed (English, no internal ids beyond `bd#290`).

No prompt changes. No new switch. `HAL_FLAG_UNREGISTERED_GATE=0` stays the kill switch; reverting the
PR restores the old behaviour.

Out of scope: the other overdue flags in the ledger, `baseline_delta_gate.py`,
`known_reds_ledger.py`, any narrowing of the check.

## 3. Acceptance criteria

- AC1. `flags_catalog.unregistered_tokens_in_files` over every `.py` under
  `engine_py/bytedigger_engine` returns `[]`.
- AC2. `FLAGS` has no `HAL_FLAG_UNREGISTERED_ENFORCE`, has `HAL_SECURITY_RULES_PATH` and
  `HAL_STATUS_PROBE_URL`, and `HAL_FLAG_UNREGISTERED_GATE` is still `kind=gate`, default `"1"`.
- AC3 (real step, temp git repo with a new `.py` file reading `os.environ.get("HAL_NO_SUCH_FLAG_X")`,
  step `_verify_green_lint_rules`, no `HAL_FLAG_UNREGISTERED_ENFORCE` set): result `status == "ok"`;
  the event log has one `flag_unregistered_warn` event whose `tokens` contain `HAL_NO_SUCH_FLAG_X`;
  `data["flag_unregistered_warnings"]` names it; no `E_FLAG_UNREGISTERED*` code.
- AC4. Same input with `HAL_FLAG_UNREGISTERED_ENFORCE=1` in the environment: identical to AC3 (the
  variable is no longer read).
- AC5. Same input with `HAL_FLAG_UNREGISTERED_GATE=0`: `status == "ok"`, a `gate_disabled` event with
  `gate == "HAL_FLAG_UNREGISTERED_GATE"`, no `flag_unregistered_warn` event.
- AC6. `ERROR_CODES` in `error_codes.py` has neither code, and neither `ERROR_CODES.md` mentions them;
  `error_codes.check(root)` reports no unregistered and no dead codes.
- AC7. `python3 scripts/flip_horizon.py --check --today 2026-11-04` output does not name
  `HAL_FLAG_UNREGISTERED_ENFORCE`; the ledger has no such key.
- AC8. Neither `E_FLAG_UNREGISTERED` nor `HAL_FLAG_UNREGISTERED_ENFORCE` appears in any `.py` under
  `engine_py/bytedigger_engine` (the AC1 scan root).
- AC9. Existing `tests/test_bd89_p3d_flag_owner_lint.py` passes with the pin removed.

## 4. Test plan

One new file `engine_py/tests/test_bd290_flag_unregistered_warn.py` (AC1-AC8, AC3-AC5 through the real
step, no mocks of the unit under test). AC9 is the one-line edit of the existing test. Local runs are
targeted (this file, `test_bd89_p3d_flag_owner_lint.py`, `test_bd_flip_horizon_guard.py`, the
error-code registry tests); the full suite and the base-to-HEAD delta are CI's.
