# bd#249 gate r2: post-fix empty scope is an error

Audited: spec r2 `docs/decisions/2026-10-10-bd249-postfix-empty-scope.md`; RED `engine_py/tests/test_bd249_postfix_empty_scope.py` (19 tests, all failing per preflight receipt) and pinned guards `engine_py/tests/test_bd249_postfix_scope_guards.py` (5 passing); UUT `workflows/phase_6_review.py` `_post_fix_test_scope` (L5674) and `_run_pytest_post_fix` (L5785); `llm_subprocess.manifest_from_result` / `_validate_manifest_or_raise`; `error_codes.py` and both `ERROR_CODES.md`. I have no Bash: failure reasons come from code reading plus the preflight receipt (19 fail, 18+12+29+44+5 pass).

## Round-1 findings: resolution check

| Finding | Status | Evidence |
|---|---|---|
| F1 MAJOR error-code registry | RESOLVED | `paths:` lists `error_codes.py` and both `ERROR_CODES.md`; spec Design names the three registrations and `error_codes --check`; AC8 states them; RED `test_ac8_error_code_registered_and_documented` asserts dict key, both md files, and runs `--check` from `engine_py/` (all three files exist and carry the neighbouring `E_POST_FIX_PYTEST_INFRA` row, so the target is real). |
| F2 MAJOR deviation from issue | RESOLVED | "Deviation from the issue, stated plainly" section: default OFF, owner `guy-lifshitz`, flip criterion (shadow events show no false positives), `"0"` is the kill switch after flip, no date auto-flip. |
| F3 MAJOR docs-only false positive | RESOLVED | Docs-only exemption (non-empty manifest, no `.py` -> skip, ok, `mode="exempt"`) plus `n_manifest_files`/`n_manifest_py` in payload; AC5b and RED `test_ac5b_*` (3 tests, 4 cases) pin exempt in both flag states and "empty manifest is not exempt" in both states. Empty-manifest fixture reaches the branch: `_validate_manifest_or_raise` accepts an empty list (it says so in its docstring), so no `E_LLM_MANIFEST_MISSING_AT_CONSUMER` pre-empts it. |
| F4 MINOR red_test_paths unchecked | RESOLVED (documented) | Known-limit sentence in Design cites bd#90 AC4; AC5 narrowed to "persisted-file path only". Not pinned by a test, acceptable since stated as a limit. |
| F5 MINOR other flag values | RESOLVED | Table row "anything else -> shadow"; AC5c; RED `test_ac5c_true_is_shadow_not_enforcing`, `test_ac5c_bytedigger_alias_enforces`. |
| F6 MINOR RED tightness | RESOLVED | `_assert_payload` pins phase, step, all counts, n_manifest_files/py for every mode; `_assert_enforced` pins `data is None`, non-empty `error`, `recoverable is False`; AC8 pins `kind`, `module`, `default`, owner, provenance; `BYTEDIGGER_` alias covered. |
| F7 MINOR cosmetics | RESOLVED | Header says TIER2; "manifest lists" wording; redundant re-run section gone; RED docstring points AC4/AC6 to the guards file. |

## 4-step audit

### Step 1 - spec-internal consistency
Tokens consistent across spec, RED and guards: `HAL_POST_FIX_EMPTY_SCOPE_ENFORCE`, `post_fix_empty_scope`, `E_POST_FIX_EMPTY_SCOPE`, `no_test_scope`, `run_pytest_post_fix`, modes `shadow|enforce|exempt`. Payload field list in Design equals the keys `_assert_payload` reads. Frontmatter `red_tests`/`sibling_tests`/`red_pins` match the files on disk and the AC9 list. No drift found.

### Step 1.5 - rule-overlap simulation
Dispatch order in the step: manifest-corruption (L5793) -> synthetic env (L5805) -> sha boundary (L5820) -> manifest error unless `red_test_paths` (L5840) -> scope (L5853) -> empty branch (L5856) -> pytest. Fixtures (real `git init`, real commits, valid `pre_fix_sha`, `manifest_source="harness_tool_record"`) pass the first four gates; the passing guards on the same fixture prove it. Inside the new empty branch the spec order is: exempt check (non-empty manifest, zero `.py`) -> flag mode. Simulated:
- docs manifest, flag "1": exempt first -> ok + skip, mode exempt (AC5b). Correct, exemption must win over enforce.
- empty manifest, flag "1": not exempt -> enforce (AC5b).
- manifest `pkg/module.py`, flag unset/"0"/"true": shadow (AC2, AC3, AC5c).
- persisted RED lists only a missing file: n_red 0, manifest `.py` present -> not exempt -> enforce (AC5).
No branch is shadowed by an earlier one.

### Step 2 - section 2 vs section 3
Producing paths: enforce error (AC1, AC3 alias, AC5, AC5b-empty, AC5c alias, AC7), shadow (AC2, AC3, AC5c, AC5b-empty), exempt (AC5b), registry (AC8). Every new terminal/cleanup branch has an AC and every error-path AC has a producing branch. Non-empty path untouched, guarded by AC4/AC6/AC7-guards and AC9. PASS.

### Step 3 - RED adequacy
All 19 reach assertions (imports are of existing modules only; no collect-time failure). Failure reasons: enforce cases fail on `status == "error"`; shadow/exempt cases fail on the missing `post_fix_empty_scope` event (status/skip assertions already hold today); the registry trio fails on missing flag, missing doc text, missing error code. Right reasons. Only `run_test_command` (process boundary) and `_emit_safe` (capture) are replaced; scope builder, step and config provider are real; real git repo; so no stub-passability. The `_assert_enforced` check `env.run_calls == []` and `post_fix_pytest_skipped == []` stop a "log both" implementation. AC7 runs both auth modes for empty paths (RED) and non-empty paths (guards).

### Step 4 - reachability
| AC | Point (prod) | Host | Test path |
|---|---|---|---|
| AC1/3/5/5c/7 enforce | new branch after L5856 | `_run_pytest_post_fix` | `env.run()` calls the real function |
| AC2/3/5c/7 shadow | same, shadow arm | same | same |
| AC5b exempt | same, exempt arm | same | same |
| AC4/6 | argv build L5866 | same | guards, sentinel argv |
| AC8 | `FLAGS`, `ERROR_CODES`, md docs | module data | direct read + `--check` subprocess + lint subprocess |
All reachable. Caller-side handling of the non-recoverable error (does the phase loop stop) remains outside this lot, consistent with "Not touched" (#250).

## Adversarial edges (not covered by an AC)
- A1 (MINOR, advisory): manifest with only a non-test `.py` helper that is not a "test path" yet is not a source, e.g. `conftest.py` or `setup.py`. Not exempt (it is `.py`) and the sibling leg may find nothing -> enforce error. Defensible (code changed, nothing tests it) but is a probable shadow-data false positive; the owner should read `n_manifest_py` in shadow data before flipping.
- A2 (MINOR): `n_manifest_files` is computed from the raw or the normalised manifest? Entries that `_norm` drops (blank, `..`-escaping, `.`) would make the two differ. The spec does not say; both RED fixtures use clean paths so either passes. GREEN should use one consistently; no test change required.
- A3 (informational): `prev.data["red_test_paths"] == []` (key present, empty) skips the persisted-file and manifest-test legs, so scope depends on siblings only; with flag "1" this is enforce, covered by the stated known limit family. No test needed.
- A4 (informational): manifest mixing docs and a `.py` file is not exempt; covered implicitly by the AC1 fixture (single `.py`) and the rule text.
- A5 (informational): second post-fix cycle after #250 re-entry emits a second event per cycle; no dedup wanted.

## Checklist
- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC5b: PASS
- AC5c: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS

## Findings
No MAJOR findings. All of F1-F7 are resolved in spec r2 and RED.
1. N1 (MINOR): A1 above, shadow-data reading guidance for the flip decision.
2. N2 (MINOR): A2 above, GREEN should count `n_manifest_files` from one consistent list (suggest the raw `manifest_from_result` list) and the spec could say so.
3. N3 (MINOR): no RED pins the F4 known limit (missing path via `prev.data["red_test_paths"]`); it is documented only. Acceptable.

Orchestrator: stamp the anchor with `python3 SYSTEM/cli/build/verdict-anchor.py <this doc> --spec <spec> --red <red-file>`; I wrote no hash.

VERDICT: APPROVED
VERDICT: APPROVE

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd249-postfix-empty-scope.md sha256:af7fcdfe731a541803749a9b2590673aa2dc6943ed5f2737b5dd22fcda5f37f5
red: engine_py/tests/test_bd249_postfix_empty_scope.py sha256:ec4211a5e422c2995a13ca979c122728291b173ecff3adc3df0c07dda8713ce4
-->
