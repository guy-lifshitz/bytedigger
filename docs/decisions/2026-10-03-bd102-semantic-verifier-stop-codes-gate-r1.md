# bd#102 gate r1: semantic verifier stop codes (spec + RED audit)
<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd102-semantic-verifier-stop-codes.md sha256:9a0d286689540ad2deadc14f5b5f3ea7411a1f06eb7ab91cad1ca08f4069a9e4
-->

Audited: spec `docs/decisions/2026-10-03-bd102-semantic-verifier-stop-codes.md` (FROZEN r1), RED `engine_py/tests/test_bd102_semantic_verifier_stop_codes.py` (17 tests, all failing pre-GREEN per lot-preflight). Base `43d3b49`. Read-only. No tests run.

## Step 1: spec-internal consistency (literal-token drift)

- Flag `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`, `_VerifierStop`, `_STOP_ERROR_CODES` and event `phase_6_semantic_verify_stopped` are spelled the same in op1-op5, AC1-AC14 and the RED.
- **Drift:** op3 names the tally field in the stopped event `unverified_reasons`. op4 names it `semantic_unverified_reasons` in the ok-return `data` and in the `phase_6_semantic_verify_complete` event. That gives one concept two keys across the two terminal events. No AC pins the stopped-event key, so GREEN will copy whichever literal it reads (finding 2).
- Code claims checked against the tree: `semantic_verifier.py:209-218` (error mapping), `:470-516` (loop), `:604` (ok return), `env_limit.py:31` (`PAUSE_LANE_ERROR_CODES = {"E_LLM_SPEND_LIMIT"}`), `engine.py:392` and `run.py:433` (both `gate_enabled("HAL_PAUSE_LANE") and is_paused_result(...)`), `llm_subprocess.py:1419/2004/787` (the three fatal codes), `phase_6_review.py:6008` (plain `StepContract`, no `skip_on_error`), `:2396` (pass-through wrapper). All are accurate. `grep -i semantic flags_catalog.py` has no match, so §1 is accurate there too.

## Step 1.5: rule-overlap simulation

- `_invoke_verifier_agent` error branch. GREEN inserts the stop check after `status != "ok"`. The stop set and the timeout set `{E_LLM_TIMEOUT, E_LLM_API_TIMEOUT}` do not overlap, so either order gives the same result. SPEND gives a raise (switch on) or the `agent_error` string (switch off). TIMEOUT gives `agent_timeout`. E_LLM_EXIT gives `agent_error`. A thrown OSError is caught before the status check and returns a string. This matches AC10/AC6/AC7.
- Tally classifier. In order: overflow (counted at the `:474` site), then a reason starting with `agent_error ` (key = first token), then the parsed reason, then `unknown`. Simulated for AC6 (`agent_error E_LLM_EXIT boom` gives `E_LLM_EXIT`), AC7 (`agent_error E_LLM_SPEND_LIMIT spend limit reached weekly cap` gives `E_LLM_SPEND_LIMIT`, because colons are stripped by `_sanitise_for_unverified_block`) and AC8 (`agent_timeout`, `empty_response`, `no_verdict_marker`, `overflow`). The first matching branch gives the expected key in every case. `unknown` cannot be reached, because `parse_verdict` always sets a reason on UNVERIFIED. That is harmless.
- AC8 escalation. 21 findings > MAX, so `escalation_allowed=False` and exactly 20 calls are made. AC5 uses 3 findings, so escalation is allowed and the opus call is call 2. Both are consistent.

## Step 2: §2 vs §3 cross-check

| §2 path | AC |
|---|---|
| op2 raise (switch on, stop code) | AC10, AC1, AC2 |
| op2 unchanged paths (timeout, other codes, empty, OSError) | AC10 (timeout), AC6 (other code); empty/OSError already covered by `test_bd82_semantic_verifier_chokepoint.py` v5c/v6 |
| op3 catch at haiku call | AC1-AC4 |
| op3 catch at opus escalation | AC5 |
| op3 no doc rewrite | AC3, AC4, AC5 |
| op3 stopped event | AC3 (code, findings_total), AC4 (model_haiku_calls) |
| op3 returns `exc.result` unchanged | AC1, AC2 (status, error_code, data, recoverable, error) |
| op4 tally in data + complete event | AC6, AC8, AC9 |
| op4 FAIL-skip / no-section returns untouched | AC13 |
| op5 switch off | AC7, AC10 |
| op5 catalog entry | AC12 |
| op6 CHANGELOG | AC14 |

Every terminal or cleanup path has a producing op, and every op has an AC. Partial gap: op3 promises stopped-event fields `step_name`, `model_opus_calls`, `unverified_reasons` and `duration_ms`, and no AC pins any of them (finding 2). This does not block.

## Step 3: RED adequacy

- Collection: the RED imports only existing modules. New names are reached only after an `assert hasattr(...)` (AC10, AC11, AC13) or are string literals. The 17/17 failures happen at assert time, which matches lot-preflight.
- No self-mocking: `_invoke_verifier_agent` is never patched. The seam is the registered `claude-subprocess` backend, the same pattern as `test_bd82_semantic_verifier_chokepoint.py::_spy`. I checked `_dispatch_backend` (`llm_subprocess.py:1734-1784`). With no current run, `_emit_attestation` returns False, so `data` is copied with `invocation_id` popped and equals the backend's dict. `_capability_escape_refusal` and `_pin_mismatch_refusal` stay inactive because there is no `observed_tools`/`observed_model`. So the `data`/`error`/`recoverable` equality in AC1/AC2 can be reached by a faithful GREEN.
- Stub-resistance. A GREEN that raises on every error fails AC6 and AC10. One that ignores the switch fails AC7 and AC10. One that catches only around the haiku call fails AC5. One that rewrites the doc on stop fails AC3/AC4/AC5. One that rebuilds the result fails AC1/AC2 data/error/recoverable equality. One that hardcodes the tally fails AC6/AC7/AC8 (three different dicts) and AC9. There is no vacuous RED.
- **Defect (MAJOR, finding 1):** AC14 at RED line 330 is `start = text.index("## [Unreleased]")`. The line contains both `Unreleased` and `.index(`. bd#212's repo-wide lint `engine_py/tests/test_bd212_changelog_helper.py::test_ac9_no_unreleased_or_top_section_pins_in_other_tests` (`_PIN_TOKENS` includes `.index(`; it scans every `*.py` under `engine_py/tests` and `tests`) will list this file as an offender. Adding the RED therefore turns an existing green test red, and AC14 would also break at the next release cut, which is exactly what bd#212 (HEAD~1) exists to prevent. The spec's AC14 wording ("CHANGELOG `[Unreleased]` mentions ...") invites the pin.

## Step 4: reachability

- Raise: Point = new branch after `semantic_verifier.py:212`. Host = `_invoke_verifier_agent`. Test paths = AC10 (direct), and AC1/AC2 through `verify_findings_semantic` → `llm_subprocess.invoke_llm_subprocess` → `_dispatch_backend` → registered backend.
- Catch / stop-return / stopped emit: Point = around `:482` and `:491`. Host = `verify_findings_semantic`. Test paths = AC1-AC4 (haiku) and AC5 (opus).
- Tally: Points = `:474` (overflow) and `:510` (else branch). Host = `verify_findings_semantic`. Test paths = AC6, AC7, AC8. Emit at `:587-602` and return at `:604` are reached by AC8/AC9.
- Flag: `flags_catalog.FLAGS` is checked by AC12, which also runs the `scripts/flag_owner_lint.py` subprocess with cwd = repo root. With `owner`/`provenance` present, `is_rollout` is True and `_entry_violations` requires a non-blank owner and `introduced:` plus text. op5's `"introduced: bd#102 - ..."` passes.
- Pause lane: the returned `exc.result` keeps `status="error"` and `error_code="E_LLM_SPEND_LIMIT"`. The step is not `skip_on_error`, and the wrapper passes the result through, so `final_result` reaches `engine.py:392` / `run.py:433` and `is_paused_result` holds. The production spend result (`llm_subprocess.py:2481-2497`) has `recoverable=True` but no `retry_from_step`, so the Design-A retry at `engine.py:730-734` does not fire. Returning the result unchanged is consistent with the lane. Fatal codes with `recoverable=False` reach the terminal stuck-report path at `engine.py:421`, which is intended. `_on_phase_6_abort` only writes the satisfaction stub, which is a side effect that does not touch the review doc.
- Can the raise be swallowed? No. `verify_findings_semantic` is the only production caller (`phase_6_review.py:134/2396`). The loop has no broad `except`. `_VerifierStop(Exception)` is not an `OSError`, so the existing `except OSError` cannot catch it even if GREEN places the raise inside that try. Existing direct callers and patchers (`test_bd82_semantic_verifier_chokepoint.py`, `test_3C533CD8_*`, `test_bd82_role_backend_effort.py::test_r7`, `test_bd206_class_m_sites.py`, `test_gh499_*`, `test_semantic_verifier_W15.py:633`, and the W15/F60FED11 tests that patch `_invoke_verifier_agent` to return strings) use ok results or non-stop codes (`E_LLM_EXIT`, `E_LLM_TIMEOUT`, `E_LLM_API_TIMEOUT`, OSError). None of them would see the raise.

## Adversarial edges (not covered by §3)

1. **Repo-lint regression shield:** the bd#212 CHANGELOG-pin lint over all test files (finding 1). Not covered: AC13 lists only `test_semantic_verifier_*`, bd82 chokepoint and 5F9817F6 as guards.
2. **Unbounded / model-authored tally keys:** op4 uses the parsed `reason` value verbatim as a key, so a model reply `UNVERIFIED:\nreason: <any sentence>` creates a free-text key. A model reply `reason: agent_error E_LLM_SPEND_LIMIT` is tallied as a spend-limit code. That is a telemetry spoof only, with no control-flow effect, because the stop decision reads the chokepoint `StepResult`. The OSError path (`agent_error <exception text>`) and an error with `error_code=None` also key on the first word of free text. §5 edge 4 ("cannot create unbounded keys") does not hold for these paths.
3. **Empty scope:** an `## Aggregated Findings` section with zero findings, or only already-low-trust findings (all skipped, zero calls), must still return `semantic_unverified_reasons == {}`. AC9 covers {} only through REPRODUCED calls.
4. **CHANGELOG duplicate-block lint:** `test_bd212_changelog_helper.py::test_ac8_real_changelog_has_no_duplicate_blocks`. GREEN must add its bullet to the existing `### Fixed` block at CHANGELOG.md:102, not create a second `### Fixed` under `[Unreleased]`.
5. **Env alias leak:** `_aliased_env_get` honours `BD_`/`BYTEDIGGER_SEMANTIC_VERIFY_STOP_ON_FATAL` when the `HAL_` name is unset. The autouse fixture deletes only the `HAL_` name, so a developer shell exporting the `BD_` alias set to 0 flips AC1/AC2/AC10. This also applies to `HAL_RUNNER_BACKEND[_JUDGE]`, an inherited hazard shared with the bd82 tests.
6. **`HAL_PAUSE_LANE=0`:** the verifier still stops on `E_LLM_SPEND_LIMIT`, and the engine then reports a raw error. This is probably intended, since the remaining calls are doomed, but the spec does not say so.
7. **`findings_total` semantics:** it is unclear whether the value is `len(findings)` including overflow or the capped count. AC3 uses exactly 20 findings, so both readings pass and the value is unpinned.

## Findings

1. **MAJOR: the RED turns a green test red (bd#212 CHANGELOG-pin lint).** `test_bd102_semantic_verifier_stop_codes.py:330` `start = text.index("## [Unreleased]")` matches `test_bd212_changelog_helper.py::test_ac9_no_unreleased_or_top_section_pins_in_other_tests` (`"Unreleased" in line and ".index(" in line`). Fix:
   - RED: rewrite AC14 as `from helpers.changelog import read_changelog, require_entry` plus `require_entry(read_changelog(), KILL, block="Fixed")`, following `test_bd101_fresh_default.py:290-293`.
   - Spec: reword AC14 to "a CHANGELOG entry (helpers.changelog.require_entry, `Fixed` block) mentions `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`", and add `test_bd212_changelog_helper.py` to AC13's guard list.
   - Since AC14 changes, the spec revision changes and needs a new sha in the anchor.
2. **MINOR: stopped-event key drift and unpinned fields.** op3 says `unverified_reasons` while op4 says `semantic_unverified_reasons`. Pick one, preferably `semantic_unverified_reasons` so the stopped and complete events share it. Optionally pin `model_opus_calls == 1` in AC5's stopped row and the reasons key in AC3/AC4.
3. **MINOR: §5 edge 4 overclaims.** Tally keys are bounded only for chokepoint errors that carry an error code. Either cap or normalise (for example `agent_error` with no code goes to `agent_error`, and any non-catalogued reason goes to `other`), or correct the claim (adversarial edge 2).
4. **MINOR: empty-scope tally (edge 3) and `findings_total` definition (edge 7) are unpinned.** Pinning them is advisory.
5. **MINOR (advisory for GREEN):** add the CHANGELOG bullet to the existing `[Unreleased]` `### Fixed` block (edge 4). Keep the new emit's `except` logging rather than `pass`, because `security/except-pass-allowlist.txt` counts one for this file. Add no `SHARED/` strings (`test_gh499_oss_string_residue.py` reads this source).

VERDICT: REJECTED
