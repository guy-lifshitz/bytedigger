# bd#249 gate r1: post-fix empty scope is an error

Audited: spec `docs/decisions/2026-10-10-bd249-postfix-empty-scope.md`; RED `engine_py/tests/test_bd249_postfix_empty_scope.py` (12 failing: AC1, AC2, AC3 x3, AC5, AC7 x4, AC8 x2) and `engine_py/tests/test_bd249_postfix_scope_guards.py` (5 pinned guards); UUT `workflows/phase_6_review.py` `_post_fix_test_scope` (L5674) and `_run_pytest_post_fix` (L5785); `config_provider.py`, `flags_catalog.py`, `scripts/flag_owner_lint.py`, `docs/events.md`, `error_codes.py`. Preflight receipt read (12 fail, 18+12+29+44+5 sibling/guard pass). Not run by me (no Bash); conclusions on RED failure reasons are from code reading.

## 4-step audit

### Step 1 - spec-internal consistency
- Tokens consistent: `HAL_POST_FIX_EMPTY_SCOPE_ENFORCE`, `post_fix_empty_scope`, `E_POST_FIX_EMPTY_SCOPE`, `no_test_scope`, `run_pytest_post_fix`.
- Drift (MINOR): header says "MICRO", the lot and the preflight receipt say TIER2. The tier check in preflight passed, but the doc should state one tier.
- Drift (MINOR): the spec says "the fix commit touches only pkg/module.py". The builder never reads the commit. It uses `prev.data["worker_written_paths"]` (manifest) and `pre_fix_sha` only as the boundary. The RED fixture does both, so it is fine, but the AC wording should say "manifest".
- The last section "Post-fix scope the lot re-runs" is a test-run list, not behaviour. Not an AC; confusing next to AC9 (MINOR).
- Spec claims about code verified true: empty `([], counts)` is reachable (L5856); skip event plus `status="ok"` today (L5857-5863); `get_config().flag()` is True iff exactly `"1"` (config_provider L56-58); HAL_ wins over BD_/BYTEDIGGER_ (L21-33); `flag_owner_lint` includes a `kind: flag` entry whose name ends `_ENFORCE`, and requires non-blank `owner` and `introduced:` provenance (lint L74-107); `docs/events.md` L26 has `post_fix_pytest_scope`. `get_config` is already imported in phase_6_review (L4392), so no new import is needed.
- Not true / missing: the spec says the error code needs a docs mention "if such a list exists". It exists, in three places, and is machine-checked (see F1).

### Step 1.5 - rule-overlap simulation
Dispatch inside `_run_pytest_post_fix`, in order: (1) manifest corruption error, (2) synthetic env skip, (3) no SHA boundary skip, (4) manifest error unless `red_test_paths` present, (5) scope empty, (6) pytest.
- AC1/AC2/AC5 fixtures: real git repo, valid `pre_fix_sha`, valid manifest -> pass through 1-4 and reach 5. The guards (non-empty scope, same fixture) pass in preflight, which proves 1-4 do not intercept this fixture. Good.
- Flag branch inside 5: unset/"0"/other -> shadow, "1" -> enforce. Values such as "true", "2", " 1" fall to shadow via `flag()`. Spec table lists only unset/"0"/"1"; the behaviour is deterministic but unstated (MINOR, F5).
- First-match issue: `prev.data["red_test_paths"] == []` (key present, empty list) takes the `prev_red is not None` branch, skips the persisted file and the manifest-test leg, and has no exists-check. Scope then depends on siblings only. Not an AC. See edge E2.

### Step 2 - section 2 vs section 3
- New terminal branch (enforce error): AC1, AC5, AC7. New shadow branch: AC2, AC3. Kill switch: AC3. Registry: AC8. Every produced path has an AC.
- Inverse mismatch: the spec section 3 asserts an error code (`E_POST_FIX_EMPTY_SCOPE`), and the codebase has a registry for error codes (`engine_py/bytedigger_engine/error_codes.py` L147-148 dict, `engine_py/ERROR_CODES.md` L231-232, `engine_py/bytedigger_engine/ERROR_CODES.md` L231-232, `error_codes.py` has `--check` drift detection). Section 2 has no deliverable for it, `paths:` frontmatter omits all three, and no AC pins it. -> F1 (MAJOR).
- Frontmatter `paths:` lists three files; `scripts/flag_owner_lint.py` is read-only. Good.

### Step 3 - RED adequacy
- Collect-time vs assert-time: all 12 reach assertions. Failure reasons from code: AC1/AC5/AC7-enforce fail on `result.status == "error"` (today "ok"); AC2/AC3-zero/AC3-HAL-wins/AC7-shadow fail on `len(env.of(_EVENT)) == 1` (today 0), after `status == "ok"` and the skip event already hold; AC3-alias fails on status; AC8 x2 fail on missing flag / missing doc text. Right reasons.
- No stub-passability: `run_test_command` is replaced only as the pytest process boundary; `_emit_safe` is capture only; the scope builder, the step and `get_config()` are real. Real git repo with real commits. No sys.path mutation. Not vacuous.
- Pinned guards (AC4, AC6, AC7-nonempty, AC8 lint) pass pre-GREEN by design. They protect the non-empty path (the issue's "includes the originally frozen tests": the `tests/test_frozen.py` argv assertion is real). The lint guard is trivially true until the flag exists, but then it is the only lint pin; acceptable.
- Weaknesses (MINOR): AC2/AC3 shadow tests do not assert the event payload counts/`phase`/`step` (only AC1 does); `BYTEDIGGER_` alias is untested (only `BD_`); AC1 does not assert `result.error` is a non-empty message; AC4 enforce-with-non-empty is asserted, but there is no AC4 case with flag "0" (cheap).
- AC8 catalog test does not assert `kind == "flag"` or `module`, which the spec lists; lint would still pass for a `kind: "str"` entry only if name ends `_ENFORCE`... it would not be rollout-checked, since `is_rollout` requires kind flag/gate. Pin `kind == "flag"` so lint actually covers it (MINOR, F6).

### Step 4 - reachability (section 1y)
| AC | Point (prod) | Host | Test path |
|---|---|---|---|
| AC1/AC5/AC7 | new enforce branch at L5856 `if not py_test_paths` | `_run_pytest_post_fix` | `env.run()` calls the real function on a real repo |
| AC2/AC3 | same branch, shadow arm | `_run_pytest_post_fix` | same |
| AC4/AC6 | L5866 argv build | `_run_pytest_post_fix` | same, sentinel records argv |
| AC8 | `FLAGS` dict, `docs/events.md` | module data | direct read plus lint subprocess |
All reachable on the real path. Caller-side reachability of the `error` result (does the phase-6 loop stop or retry on `recoverable=False`) is not tested; see E1.

## Adversarial edges (not covered by an AC)
- E1: Non-Python or docs-only fix (manifest has only `.md`, `.yaml`, `.ts`). `sources` is empty, scope is empty, and enforce returns a non-recoverable error although nothing testable changed. This is the most likely production false positive when the default flips. Shadow data would show it, but the spec should say whether enforce exempts manifests with no `.py` source, or accepts the false positive. Decide and pin one way; at minimum add a shadow-payload field for the manifest size so the owner can tell "no tests exist" from "nothing to test".
- E2: `prev.data["red_test_paths"] == []` (present, empty): enforce errors, with no existence check; vs `red_test_paths=["tests/gone.py"]` (present, missing on disk): scope is NON-empty (`red_check_exists=False`), pytest is invoked on a missing file and returns an infra error. AC5 only covers the persisted-file path. The "frozen test missing" claim in AC5 is therefore true for one source of RED paths and false for the other. Either state the limit or add a pin.
- E3: Empty manifest plus no RED file (`worker_written_paths == []`). Same result as E1; confirm intended.
- E4: `get_config()` host-provided provider that does not read env: flag cannot be set; fails closed to shadow. Fine, mention only.
- E5: Concurrent/re-entry: a second post-fix cycle after #250 re-entry emits a second `post_fix_empty_scope`; the events are per cycle, no dedup needed. Informational.
- E6: Both an empty scope and an earlier skip (`no_git_repo`/`no_sha_boundary`) leave a fix landing untested; the spec records this as a known limit. It is part of the issue's chokepoint, so the shadow/enforce story should at least emit nothing misleading; accept, but record in the "Not touched" section as a numbered follow-up.

## Shadow / kill-switch vs the issue
The issue requires (a) empty scope is an error, (b) one config setting restores current behaviour. The spec ships enforce as OFF by default and the kill switch `"0"` is therefore identical to the default. This follows Guy's "shadow before enforce with a switch", but it means issue acceptance (a) is met only when the flag is set to "1", and (b) is a no-op at default. That should be stated explicitly in the spec as a deliberate deviation, with the flip owner and a flip criterion (e.g. a shadow count threshold) named, or the issue acceptance must be amended. Without it the lot can be closed while the live default still treats empty scope as a pass (F2).

## Existing tests that encode empty-scope-ok
`test_bd90_postfix_test_scope.py` L181 and `test_phase_6_post_fix_pytest_gate_7A940850.py` L455-492 assert the skip event plus ok, flag unset. In shadow they keep passing as long as no assertion counts all events; the preflight siblings pass pre-change, and the new `post_fix_empty_scope` event is additive. Risk is low, but GREEN must run both unedited (AC9 says so). PASS.

## Findings
1. F1 (MAJOR): Error-code registry omitted. `E_POST_FIX_EMPTY_SCOPE` must be added to `engine_py/bytedigger_engine/error_codes.py` (dict near L147), and to both `engine_py/ERROR_CODES.md` and `engine_py/bytedigger_engine/ERROR_CODES.md`; `error_codes.py --check` detects drift. The spec only says "if such a list exists", `paths:` omits them, and no AC pins them. Fix: add the three files to `paths:`; add to AC8 "the code is registered in `error_codes.py` and both `ERROR_CODES.md` copies" and a RED test (read the dict, grep both md files; if a drift check is exposed as a function, call it). Without this, GREEN either ships drift or touches unlisted files.
2. F2 (MAJOR): Issue-acceptance gap not declared. Default-off enforce means "empty scope is an error" is not true by default. Fix: add a "Deviation from the issue" paragraph naming the owner and flip criterion (no date auto-flip is fine), and state that kill-switch "0" equals the default until flip, or add a second kill value semantics. A short statement is enough; no code change.
3. F3 (MAJOR): E1 / E3 false-positive decision missing. Choose: enforce exempts manifests with no `.py` source (and add AC plus RED), or accept and say so. Also add `n_manifest_files` (manifest length) to the event payload so shadow data distinguishes the cases; add it to the spec payload list and to the AC1/AC2 payload pin.
4. F4 (MINOR): E2, `red_test_paths` list present: AC5 holds only for the persisted-file leg. Document the limit or add a pin.
5. F5 (MINOR): State behaviour for flag values other than "0"/"1" (shadow) and pin one case.
6. F6 (MINOR): Tighten RED: AC8 catalog test assert `kind == "flag"` and `module` ends with `phase_6_review.py`; shadow tests assert payload `phase`, `step`, counts; add a `BYTEDIGGER_` alias case; AC1 assert non-empty `result.error`.
7. F7 (MINOR): Spec cosmetics: MICRO vs TIER2; "fix commit touches" should read "manifest lists"; section "Post-fix scope the lot re-runs" is redundant with AC9; RED docstring says AC1-AC8 but AC4/AC6 live in the guards file.

## Checklist
- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: FAIL
- AC9: PASS

VERDICT: REJECTED
(The requester asked for the literal `VERDICT: REJECT`; the gate's machine-checked literal is `REJECTED`. Same decision.)
