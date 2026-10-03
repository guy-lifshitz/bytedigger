# bd#102 gate r2: semantic verifier stop codes (spec r2 + revised RED re-audit)
<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd102-semantic-verifier-stop-codes.md sha256:5eb469183017ad1784c9e61110fc541d0f219470cec21a6cd02a7f75e52ca485
-->

Audited: spec `docs/decisions/2026-10-03-bd102-semantic-verifier-stop-codes.md` (FROZEN r2, sha as anchored above, taken from the orchestrator; I did not recompute it) and RED `engine_py/tests/test_bd102_semantic_verifier_stop_codes.py`. The RED has 20 test ids: AC1 x2, AC2 x3, AC3-AC14 x1 each, AC15 x3. All 20 fail pre-GREEN per lot-preflight `facts.md`, which matches. Base `43d3b49`. Read-only. No tests run. This is round 2 of 2.

## r1 finding closure

| r1 # | Severity | Status | Evidence |
|---|---|---|---|
| 1 | MAJOR | CLOSED | RED AC14 (`:328-331`) is now `from helpers.changelog import read_changelog, require_entry` plus `require_entry(read_changelog(), KILL, block="Fixed")`. `require_entry(text, needle, *, block=None)` exists at `engine_py/tests/helpers/changelog.py:126`. No line in the RED contains `Unreleased`. The `_TOP_PIN_TOKENS` (`heads[1].start()`, `split("\n## ")`) are also absent, so `test_bd212_changelog_helper.py::test_ac9_no_unreleased_or_top_section_pins_in_other_tests` has no offender here. Spec AC14 has been reworded and AC13 now lists `test_bd212_changelog_helper.py`. The spec sha changed, and the anchor uses the new sha. |
| 2 | MINOR | CLOSED | op3 now names the stopped-event key `semantic_unverified_reasons`, the same key op4 uses. AC15 pins it, plus `model_opus_calls`, `model_haiku_calls` and `duration_ms`. The RED also asserts that the drifted key `unverified_reasons` is absent (`:352`). |
| 3 | MINOR | PARTIAL | op4 now says that keys come from the parsed reason, which a model can influence, and that the tally is telemetry only. §5 edge 4 still claims "a long or injected message cannot create unbounded keys". That is still not strictly true for the `agent_error` paths whose first token is free text: OSError text, and `error_code=None`. See finding 1 below. |
| 4 | MINOR | CLOSED | op3 defines `findings_total = len(findings)` including overflow. AC15 pins 21 with 21 findings. op4 and AC15 pin `{}` for zero findings. |
| 5 | MINOR (advisory) | CLOSED | op6 now names the existing `### Fixed` block (`CHANGELOG.md:102` under `[Unreleased]` at `:14`), requires logging in `except`, and forbids `SHARED/` strings. |

## Step 1: spec-internal consistency (literal-token drift)

- `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`, `_VerifierStop`, `_STOP_ERROR_CODES`, `semantic_unverified_reasons`, `phase_6_semantic_verify_stopped` and `phase_6_semantic_verify_complete` are now spelled identically across op1-op6, AC1-AC15, the §4 map and the RED. The r1 drift between `unverified_reasons` and `semantic_unverified_reasons` is gone.
- §4 maps every op to at least one AC, and AC15 covers the op3/op4 event fields.
- Residual cosmetic drift: the RED module docstring (`:3`) still says "(AC1-AC14, r1)", while the spec is r2 with AC15. This is MINOR (finding 3).
- The op5 catalog entry in the spec lists kind, default, module, owner and provenance but not `description`. Every existing catalog entry has a `description` (for example `flags_catalog.py:327-334`). This is advisory for GREEN (finding 4).

## Step 1.5: rule-overlap simulation

I re-simulated the loop at `semantic_verifier.py:470-516` with the GREEN catch around `:482` and `:491`.

- **AC15a** (21 findings, `[E_LLM_EXIT, SPEND]`):
  - idx0: the haiku call returns `agent_error E_LLM_EXIT boom`. Since 21 > 20, `escalation_allowed=False`. The finding is tallied as `{"E_LLM_EXIT": 1}`.
  - idx1: the haiku call raises.
  - The overflow finding at idx20 is never reached, so the tally stays `{"E_LLM_EXIT": 1}` and `findings_total` is 21.
  - Two calls are made, which matches `len(calls) == 2`.
- **AC15b** (3 findings, `[CANNOT_DECIDE, SPEND]`): the haiku call parses to UNVERIFIED with reason `cannot_decide`. Escalation is allowed, and the opus call raises. Nothing was tallied, because the tally site comes after the verdict, so the tally is `{}`.
- **AC15c** (zero findings, `## Aggregated Findings` present): `findings == []`, the loop is skipped and `needs_rewrite` is False. The complete event is emitted and the ok-return at `:604` is reached. No backend call is made. This is reachable.
- **Call counters:** AC4 expects `model_haiku_calls == 3` with 3 backend calls, the third of which raises. AC15a expects haiku == 2 and AC15b expects opus == 1. In all three the raising call is counted. The current increments (`:483`, `:492`) sit after the call, so GREEN must count the call in the `except` path or move the increment ahead of the call. AC4 already forced this rule in r1, and the opus case is the consistent analogue. The spec prose does not state it (finding 2).
- **AC8:**
  - Call 1 is E_LLM_TIMEOUT and gives `agent_timeout`.
  - Call 2 is `_ok("")` and gives `empty_response` (`:220`).
  - Call 3 is prose with no marker and gives `no_verdict_marker` (`parse_verdict :70-71`).
  - The rest are REPRODUCED. Overflow adds 1.
  - 20 calls are made. The first matching branch gives the expected key each time.
- **AC7 / AC10 switch-off:** the SPEND string becomes `agent_error E_LLM_SPEND_LIMIT spend limit reached weekly cap` (colons stripped). The key and the `startswith` assertion both hold.
- **Switch read:** `_DefaultConfigProvider.gate_enabled` reads `_aliased_env_get` per call, and `get_config()` calls the factory each time. Nothing is cached, so `monkeypatch.setenv` inside a test takes effect.

## Step 2: §2 vs §3 cross-check

| §2 path | AC |
|---|---|
| op2 raise (switch on, stop code) | AC10, AC1, AC2 |
| op2 unchanged paths | AC10 (timeout), AC6 (other code); empty response and OSError are covered by the existing bd82 chokepoint tests |
| op3 catch at haiku | AC1-AC4, AC15a |
| op3 catch at opus | AC5, AC15b |
| op3 no doc rewrite | AC3, AC4, AC5, AC15a |
| op3 stopped event, all fields | AC3, AC4, AC15a/b (`step_name` is not pinned; it is a constant, so this is harmless) |
| op3 returns `exc.result` unchanged | AC1, AC2 |
| op4 tally in data and complete event | AC6, AC7, AC8, AC9, AC15c |
| op4 FAIL-skip / no-section untouched | AC13 |
| op5 switch | AC7, AC10, AC1 (unset/1), AC12 |
| op6 CHANGELOG | AC14 |

Every terminal path (stop-return, ok-return, FAIL-skip, no-section) has a producing op and an AC, and every op has an AC. There is no mismatch.

## Step 3: RED adequacy

- **Collection:** the imports are all existing modules (`helpers.changelog` is imported lazily inside AC14 and exists). New names are reached only after `hasattr` assertions (AC10, AC11, AC13), through `.get(...)`, or as string literals. The 20/20 failures are assert-time, which matches the preflight.
- **No self-mocking:** `_invoke_verifier_agent` is never patched. The seam is the registered `claude-subprocess` backend. `E_LLM_RUN_ID_MISSING` is produced only on the `claude-in-session` path (`llm_subprocess.py:780-789`), which the RED never selects, so it does not leak into the scripted results.
- **Stub resistance:** the r1 analysis still holds. The new AC15 tests close the remaining gaps:
  - A GREEN that emits a hardcoded or empty tally on stop fails AC15a.
  - A GREEN that forgets to count the stopping opus call fails AC15b.
  - A GREEN that adds the key only when findings exist fails AC15c.
  - A GREEN that uses the capped count for `findings_total` fails AC15a.
- **AC13** guards against a GREEN that adds the key to the FAIL-skip or no-section returns. Its `hasattr` preconditions keep it red pre-GREEN, so it is not vacuous.
- **AC14 after a release cut:** the helper searches the sections it resolves through `_searched` with `block="Fixed"`. Once the bullet lands in `[Unreleased]` `### Fixed` (the existing block at `CHANGELOG.md:102`), the test does not depend on the top-section position.
- **Repo-lint interactions:**
  - `test_bd212_changelog_helper.py` AC9: no offender (see the closure table).
  - The `flags_catalog.unregistered_tokens_in_files` scan: the RED reads the flag only through the variable `KILL` (`monkeypatch.setenv(KILL, ...)`), not a literal accessor call, so `_TOKEN_PATTERN` does not match. Pre-GREEN there is no unregistered-token pollution.
  - `scripts/flag_owner_lint.py`: run by AC12, and op5 supplies owner and provenance in the required form.

## Step 4: reachability

This is unchanged from r1 and re-confirmed:
- Raise: Point = new branch after `:212`, Host = `_invoke_verifier_agent`. Test paths = AC10 directly, and AC1/AC2 through `verify_findings_semantic` → `invoke_llm_subprocess` → `_dispatch_backend` → registered backend.
- Catch, stopped emit and stop-return: Points = around `:482` and `:491`, Host = `verify_findings_semantic`. Test paths = AC1-AC5 and AC15a/b.
- Tally: Points = `:474` and `:510`, emit `:587-602`, return `:604`. Test paths = AC6-AC9 and AC15c.
- Flag: `flags_catalog.FLAGS`, Test path = AC12.
- Pause lane: the returned `exc.result` carries the SPEND code through `phase_6_review.py:2396` to `engine.py:392` / `run.py:433`.

## Adversarial edges (not covered by §3)

1. **Free-text tally keys on the `agent_error` paths:** the OSError text and `error_code=None` paths. A model reply of `UNVERIFIED:\nreason: <sentence>` is also keyed verbatim. This carries over from r1. It is acknowledged in op4 but still contradicted by §5 edge 4. Telemetry only: it has no control-flow effect.
2. **Only-already-low-trust findings:** all findings are skipped and zero calls are made. op4 promises `{}`, but AC15c pins only the zero-findings case. The code path is the same, since the loop records no tally on the skip branch, so the risk is low.
3. **Env alias leak:** `_aliased_env_get` honours `BD_`/`BYTEDIGGER_` aliases of the kill switch, but the autouse fixture deletes only the `HAL_` name. A developer shell that exports the alias set to 0 flips AC1, AC2 and AC10. The same applies to `HAL_RUNNER_BACKEND` being set to `claude-in-session`. These are inherited hazards that only affect the test environment.
4. **`HAL_PAUSE_LANE=0`:** the verifier still stops on SPEND, and the engine then reports a raw error. This is probably intended, but the spec does not state it.
5. **Flag-read discoverability:** if GREEN reads the switch through a constant (`gate_enabled(_KILL)`) instead of a literal, `flags_catalog.discover_flag_reads` will not attribute the catalog entry to the module. No current test pins that parity, so this is advisory only.

## Findings

1. **MINOR:** §5 edge 4 still overclaims that tally keys are bounded (r1 finding 3 is only partly closed). The fix can wait for a follow-up: correct the sentence, or normalise non-catalogued keys. It is telemetry only and does not block.
2. **MINOR:** the spec prose does not say that `model_haiku_calls` / `model_opus_calls` in the stopped row include the call that raised. AC4 (spec) and AC15a/b (RED) pin that semantics, so GREEN is fully determined. Spec AC15 lists `model_opus_calls` without a value, and the RED pins it to 1. The RED is stricter than the spec text but consistent with it.
3. **MINOR:** the RED module docstring (`:3`) is stale and reads "(AC1-AC14, r1)". It should read AC1-AC15, r2.
4. **MINOR (advisory for GREEN):** give the catalog entry a `description` like every other entry, and read the switch with a literal `gate_enabled("HAL_SEMANTIC_VERIFY_STOP_ON_FATAL")` so flag discovery attributes it. Add the bullet to the existing `### Fixed` block at `CHANGELOG.md:102` and do not create a new block (`test_ac8_real_changelog_has_no_duplicate_blocks`).

No MAJOR findings. The r1 MAJOR is closed. The spec and RED are consistent, and every AC has at least one non-vacuous, assert-time failing test with a traced production path.

VERDICT: APPROVED
