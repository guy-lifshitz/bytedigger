# bd#247 gate r1: fatal/transient classification of chokepoint error codes (spec + RED audit)

Audited: spec `docs/decisions/2026-10-10-bd247-fatal-error-classification.md` (DRAFT r1), RED `engine_py/tests/test_bd247_fatal_error_classification.py` (27 test ids, 0 pass / 27 fail per preflight) and the rewritten `test_ac11_stop_code_set` in `engine_py/tests/test_bd102_semantic_verifier_stop_codes.py`. Base `0777ae9`, head `1492dd9`. Code read: `engine_py/bytedigger_engine/llm_subprocess.py` (error-code sites, `invoke_llm_subprocess` :1966-2119, `_invoke_subprocess` Popen :2208-2226), `lib/plugins/anti_hallucination/semantic_verifier.py` (:60-67, `_invoke_verifier_agent` :138-244, `verify_findings_semantic` loop and stop handler :495-543), `lib/env_limit.py`, `engine.py` (pause-lane remap :387-398, terminal-error handling :411-434), `flags_catalog.py` (:381), `config_provider.py` (`gate_enabled`, env aliases), `engine_py/tests/conftest.py`, `engine_py/tests/helpers/changelog.py`, and the existing guard tests that touch the OSError path. Read-only. No tests run.

## Step 1: spec-internal consistency (literal-token drift)

- `FATAL_ERROR_CODES`, `is_fatal_error_code`, `classify_os_error`, `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`, `_VerifierStop`, `phase_6_semantic_verify_stopped`, `phase_6_semantic_verify_complete`, `E_LLM_CMD_MISSING` are spelled the same in op1-op4, AC1-AC11, the op/AC map and the RED.
- Counting is consistent: 1 pause-lane code + 8 listed = 9 (AC1); 4 original + 5 added = 9, and AC3's five parametrized codes are exactly the five added ones.
- Code claims in section 1 checked against the tree: chokepoint sites `llm_subprocess.py:2224` (`E_LLM_CMD_MISSING`), `:584`, `:1383`, `:1074`/`:1088`, `:1158` are accurate. The `Popen` call catches only `FileNotFoundError` (:2216), so a non-executable binary does raise `PermissionError` out of the chokepoint, as claimed. `E_CAPABILITY_ESCAPE` log at `semantic_verifier.py:227` is accurate.
- Drift (MINOR, finding 4): the spec cites `semantic_verifier.py:215-218` for the `except OSError` branch (actual :222-225), `:236-241` for tag-and-continue (actual :235-240), `:64-67` for the list (actual :62-67 with its comment, :65-67 for the constant).
- Wording drift (MINOR, finding 5): op2 says the module "keeps no code literal", but `_invoke_verifier_agent` keeps `"E_LLM_TIMEOUT"` and `"E_LLM_API_TIMEOUT"` literals for the `agent_timeout` tag (:235), and the spec says that branch stays. AC7 correctly narrows the claim to two codes; op2 should say "no fatal-code literal".

## Step 1.5: rule-overlap simulation

Dispatcher under change: `_invoke_verifier_agent` after the call. Branch order in the new design: (1) `except OSError` then classify; (2) `status != "ok"`: log, fatal-and-gate stop, timeout tag, generic tag; (3) empty response; (4) raw text.

- A fatal code is never in `("E_LLM_TIMEOUT", "E_LLM_API_TIMEOUT")`, so the stop check running before the timeout check changes nothing for transient codes. Sequentially simulated for each of the nine: stop (gate on), or generic `agent_error <code> ...` (gate off). Matches AC3/AC6.
- Gate off with a fatal result code `E_LLM_SPEND_LIMIT`: falls through to the generic tag; the tally key is the code. Matches AC6 ("tag-and-continue").
- Gate off on the OSError path: classify returns a code, gate off, so the existing `agent_error <sanitised exception text>` string is kept. The tally key becomes the first word of the sanitised OS message (for example `[Errno`), which is cosmetic and not asserted (finding 6).
- Gate on, OSError that classifies to `None` (EAGAIN, ENOMEM, `TimeoutError`, `ConnectionResetError`, `BrokenPipeError`): tag path, matches AC5. Note `OSError(errno.ENOENT, ...)` and `OSError(errno.EACCES, ...)` construct `FileNotFoundError` / `PermissionError` automatically, so an `isinstance` rule and an errno rule agree for these two; the RED cannot tell them apart, and the spec does not need it to.
- Escalation call: the second `_invoke_verifier_agent` (escalation tier) sits in the same `try` as the first, so a fatal code on it is also turned into a stop. Spec and RED do not mention it (adversarial edge 3).
- Engine side: a stopped step returns `status="error"` with the chokepoint code; `engine.py:391-393` remaps only `E_LLM_SPEND_LIMIT` to `paused`; `:396-397` copies `error_code` into the `workflow_finished` row for `error`. AC8 (`paused`; `error` + code with `HAL_PAUSE_LANE=0`) and AC9 (`error` + `E_LLM_CMD_MISSING`) are therefore reachable. The scripted spend-limit result carries `data={"pausable": True}` with no `retry_from_step`, so the retry hook (`engine.py:723-735`) does not fire and "backend called once" holds.

## Step 2: section 2 vs section 3 cross-check

| Section 2 path | AC |
|---|---|
| op1 `FATAL_ERROR_CODES` membership | AC1 |
| op1 `is_fatal_error_code`, transient default (`None`, `""`, unknown) | AC1 |
| op1 `classify_os_error` fatal pair / narrow `None` | AC2 |
| op2 stop on fatal result code | AC3, AC8, AC9 |
| op2 stop on classified OSError (terminal: built result, `recoverable=False`) | AC4 |
| op2 transient stays tag-and-continue | AC5 |
| op2 delete `_STOP_ERROR_CODES`, no second list, call-time read | AC7 |
| op2 error-level log for any fatal code | none (finding 3) |
| op3 kill switch, result path and OSError path | AC6 |
| op4 catalog description names the classification | none beyond lint exit 0 (finding 7) |
| op4 CHANGELOG bullet | AC11 |
| provider/billing independence | AC8 (parametrized) |
| doc left byte-identical on stop | AC3, AC4, AC7 |

Every terminal/stop branch of the spec has an AC. The two unpinned rows are advisory, not post-conditions the engine depends on.

## Step 3: RED adequacy

- Collection: imports only existing modules (`llm_subprocess`, `telemetry_ctx`, `contracts`, `engine.WorkflowEngine`, `event_log.EventLog`, `lib.env_limit`, `semantic_verifier`, `register_backend`, `reset_backends`). New names are reached only through `_need()` or `hasattr` assertions, so every failure is at assert time, matching 0 pass / 27 fail. `helpers.changelog` is imported inside the test body.
- No self-mocking. The unit under test (`verify_findings_semantic`, the chokepoint, `WorkflowEngine`) runs for real on a real doc on `tmp_path`. The only seams are the registered backend result and, for the missing-binary cases, a real `PATH` that holds no binary or a mode-644 file. The poison-PATH guard in `conftest.py` is bypassed on purpose by replacing `PATH`, so nothing can spawn a real call.
- Fixture soundness: `_doc` emits headers matching `### SEVERITY: HIGH — ...` with a `> path:line: quote` line, so findings are not low-trust and each gets a call. `model_haiku_calls == 1` in AC4 is read from the stop event the code already emits (:534). AC4's `"ermission"` substring matches the OS message on Linux and macOS and is not a generic engine text.
- Tests that pass pre-GREEN except for their `hasattr` guard: the three AC6 tests, the two AC5 groups, AC8 `HAL_PAUSE_LANE=0`. They are regression shields (status quo behaviour must survive GREEN). Each is guarded by an assertion on a new symbol, so they fail now and cannot be satisfied by an empty stub of the new names alone; AC6 additionally discriminates a GREEN that ignores the switch.
- Rewritten bd#102 `test_ac11_stop_code_set`: fails now (`_STOP_ERROR_CODES` still present), asserts the pause-lane subset and the four original codes against `llm_subprocess.FATAL_ERROR_CODES`. No other test in the tree references `_STOP_ERROR_CODES`. The existing generic-OSError test (`test_bd82_semantic_verifier_chokepoint.py::test_v5c_*`, `OSError("disk...")`) classifies to `None`, so it stays green.

### Discrimination against plausible wrong implementations

| Wrong implementation | Caught by |
|---|---|
| `FATAL_ERROR_CODES` missing a code, or carrying an extra one | AC1 (set equality) |
| `is_fatal_error_code` true for a transient code, or false for `None`/`""`/unknown | AC1 |
| Duplicated list kept in the verifier and read instead of the chokepoint set | AC7 call-time test (a copy ignores the monkeypatch); also `hasattr(sv, "_STOP_ERROR_CODES")` and bd#102 AC11 |
| Verifier imports the set by name at import time (`from ... import FATAL_ERROR_CODES`) | AC7 call-time test (patching the module attribute has no effect) |
| `classify_os_error` maps every `OSError` to `E_LLM_CMD_MISSING` ("catch OSError") | AC2 (EAGAIN/ENOMEM/Timeout/ConnectionReset/BrokenPipe), AC5 |
| `classify_os_error` returns `None` for `PermissionError` | AC2, AC4, AC6 |
| OSError mapped but `recoverable=True` | AC4 (`recoverable is False`) |
| OSError mapped but `error` replaced by a generic text | AC4 (`"ermission"`) |
| OSError path stops but gate ignored | AC6 `test_ac6_kill_switch_permission_error_path` |
| Result path ignores the kill switch | AC6 scripted and empty-`PATH` tests |
| Stop returns `ok`, or tags and continues, or calls the backend more than once | AC3 (`status`, `len(calls) == 1`, doc bytes, `complete` row absent) |
| Stop rewrites the doc | AC3, AC4, AC7 (byte equality) |
| Stop swallows or rewrites `data` / `recoverable` / `error` | AC3 (scripted codes) |
| Pause lane lost for spend limit (stop result altered) | AC8 both backend names, and `HAL_PAUSE_LANE=0` branch |
| Missing binary ends the workflow `ok` or `paused` | AC9 |
| Kept `logger.error if code == "E_CAPABILITY_ESCAPE"` literal | AC7 source scan |
| Log branch rebuilt with a literal of another code (for example `"E_LLM_SPEND_LIMIT"`) | NOT caught (finding 1) |
| Verifier keeps a renamed second list that holds only codes other than the two scanned literals, joined by `or` with `is_fatal_error_code` | NOT caught (finding 1) |
| Log level not raised for non-escape fatal codes | NOT caught (finding 3) |

## Step 4: reachability

- Fatal result stop: Point = new branch in `_invoke_verifier_agent` after `invoke_llm_subprocess`; Host = `_invoke_verifier_agent`, driven by `verify_findings_semantic`; Test path = `verify_findings_semantic` direct (AC3, AC5-AC7) and through `WorkflowEngine.execute` (AC8, AC9).
- OSError stop: Point = the `except OSError` branch; Host = `_invoke_verifier_agent`; Test path = real `Popen` `PermissionError` with a mode-644 binary on `PATH` (AC4, AC6), scripted `OSError` raising backends for the transient side (AC5). A positive scripted `PermissionError` is not used, so the classification of the exception coming out of a backend is proven only through the real spawn path; acceptable, since that is the production shape.
- `classify_os_error` / `is_fatal_error_code` / `FATAL_ERROR_CODES`: Point = new definitions in `llm_subprocess.py`; Host = module scope; Test path = direct (AC1, AC2).
- Kill switch: Point = `config_provider.get_config().gate_enabled("HAL_SEMANTIC_VERIFY_STOP_ON_FATAL")`, read at call time, `=0` exactly disables (`config_provider.py:52-54`); Test path = AC6.
- CHANGELOG: AC11 reads `[Unreleased]`/release sections through `helpers.changelog.require_entry(..., block="Fixed")`; no `Unreleased` + `.index(` line, so the duplicate-block lint stays green.

## Design principles

- One chokepoint: yes. The list and both classifiers live in `llm_subprocess.py`; the verifier reads them at call time through the module attribute. AC7 pins the call-time read.
- No duplicated lists: spec requires deleting `_STOP_ERROR_CODES` and the `PAUSE_LANE_ERROR_CODES` import; the RED pins the deletion and the call-time read, but only partly the absence of a second literal list (finding 1).
- No code literals in the verifier: enforced for two codes only (finding 1).
- Fail-safe default: an unlisted code is transient (status quo). Unlisted chokepoint codes today: `E_LLM_RESULT_MALFORMED`, `E_LLM_MANIFEST_MISSING`/`_MALFORMED`/`_INVALID_SOURCE`, `E_LLM_WATCHDOG_UNSUPPORTED`, `E_GATE_EFFORT_UNSUPPORTED`, `E_GATE_EFFORT_CONFIG_INVALID`, `E_HARD_GATE_MODEL_DOWNGRADE`. The last three are produced only for hard-gated calls, which the verifier is not; the watchdog one needs an idle timeout the verifier does not pass. `E_LLM_RESULT_MALFORMED` and the manifest codes can reach the verifier and are classified transient by default without being named (finding 2).
- Provider and billing mode: classification is on codes only; AC8 exercises both backend names. The distinction is nominal in the RED (no key set), which is fine because nothing in the classification reads the backend.
- Kill switch: existing flag, default on, no new flag; `=0` restores today's behaviour on both paths (AC6).

## Scope

The spec `paths:` are `llm_subprocess.py`, `semantic_verifier.py`, `flags_catalog.py`, `CHANGELOG.md`, the bd#102 test. No change to which codes are pause-lane, no retry/resume change, no `EventLog` rework (#203), no change to the `Popen` path. The one engine-level touch is tests only. Clean.

## Adversarial edges (not covered by section 3)

1. **Fatal code after earlier successes.** The first call returns REPRODUCED/REFUTED, the second raises (or returns a fatal code). The doc must stay byte-identical and the earlier verdicts are discarded (the rewrite happens after the loop). AC3/AC4/AC7 only fail the first call. The original bd#102 tests may cover a mid-run stop for the original codes; nothing covers it for the added codes or the OSError path.
2. **`FileNotFoundError` from somewhere other than the spawn.** Any `FileNotFoundError`/`PermissionError` escaping `invoke_llm_subprocess` (a tool, config or temp-file read inside a backend) is relabelled `E_LLM_CMD_MISSING` and stops the step, with the original text in `error`. The spec accepts the narrow set; the label can mislead.
3. **Fatal code on the escalation call.** The tier escalation (`cannot_decide`) call is inside the same `try`; a fatal code there stops the step with `model_opus_calls` incremented and the doc unchanged. No AC.
4. **Non-exception, non-`OSError` raise.** A `ValueError`, `subprocess.SubprocessError` or `RuntimeError` out of the chokepoint is not caught by either branch and crashes the step (as today). Out of scope, but the "never a crash" sentence in the provider paragraph applies only to refusals returned as a result.
5. **Classifier input contract.** `classify_os_error` on a non-`OSError`, `None`, or an `OSError` subclass chain with `__cause__` set (a `FileNotFoundError` wrapped by another `OSError`) is unspecified; only direct instances are pinned.
6. **Environment alias leak.** `gate_enabled` honours `BD_` / `BYTEDIGGER_` spellings (`config_provider.py:21-28`). The autouse fixture deletes only the `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL` name, and `HAL_PAUSE_LANE` is not cleared at all, so a shell exporting an alias set to `0` flips AC3, AC4, AC8.
7. **Empty `PATH` through the whole engine (AC9).** The engine post-step path (dispatcher report, stuck report, rollup) runs with no `PATH`. Any of those helpers that spawns a tool and does not catch `FileNotFoundError` would fail AC9 for a reason unrelated to this lot. Not verifiable without running; flagged so a GREEN failure of AC9 is triaged correctly (restore `PATH` after the verifier call, or stub that helper).

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS

## Findings

1. **MINOR: no-duplicate-list and no-literal checks are narrower than the design principle (AC7).** The source scan forbids only `E_CAPABILITY_ESCAPE` and `E_LLM_BACKEND_UNKNOWN`. A GREEN that keeps a second list under another name and ORs it with `is_fatal_error_code`, or a log branch keyed on another fatal literal, passes every test. Strengthen AC7 to assert that no string constant in the verifier equals any of the nine codes (the timeout literals at :235 are not in the set), that the module does not import `PAUSE_LANE_ERROR_CODES`, and that it has no module-level `set`/`frozenset` of `E_`-prefixed strings.
2. **MINOR: the spec does not state the disposition of every unlisted chokepoint code.** `E_LLM_RESULT_MALFORMED`, `E_LLM_MANIFEST_MISSING`/`_MALFORMED`/`_INVALID_SOURCE`, `E_LLM_WATCHDOG_UNSUPPORTED` and the hard-gate-only codes (`E_GATE_EFFORT_*`, `E_HARD_GATE_MODEL_DOWNGRADE`) become transient by the default rule. Add one sentence saying that is intended (and why the hard-gate ones cannot reach the verifier), so the "list of 20 doomed calls" claim in section 1 is not read as exhaustive.
3. **MINOR: "error-level log for any fatal code" (op2) has no test.** The behaviour is a spec statement with no assertion, so the old single-code `E_CAPABILITY_ESCAPE` branch with an error-level log plus nothing else for other codes would pass. Add a `caplog` assertion for one added code, or drop the claim.
4. **MINOR: line citations in section 1 are stale.** `:215-218` should be `:222-225`, `:236-241` should be `:235-240`. Update when the spec is next revised; no behavioural impact.
5. **MINOR: op2 wording.** "The module keeps no code literal" is false for the timeout tag; say "no fatal-code literal".
6. **MINOR: gate-off OSError tally key.** With the switch off, the `PermissionError` message becomes the tally key (`[Errno ...`). Not asserted and not harmful; consider recording the classified code in the reason when classification returns one.
7. **MINOR: AC11 and AC10 are weaker than their text.** AC11's test passes when `bd#247` is in any `Fixed` block of an `[Unreleased]` or release section and the flag name appears anywhere in the same section body (an older bd#102 bullet in the same section would satisfy it); the `sec in [...]` branch is vacuous. Assert both needles in the same bullet. AC10 is a guard list with no RED of its own beyond the rewritten bd#102 AC11; the catalog description change in op4 is not asserted at all (only `flag_owner_lint` exit 0).
8. **MINOR (advisory for GREEN):** add the CHANGELOG bullet to the existing `### Fixed` block of `[Unreleased]` (the duplicate-block lint); keep the verifier free of any `SHARED/` or tool-path string; consider clearing the `BD_`/`BYTEDIGGER_` aliases of the two switches in the autouse fixture (edge 6).

No MAJOR finding. Every AC has at least one assertion that fails now at assert time and that a plausible wrong implementation of the named behaviour does not satisfy, the spec's two guard-rail principles are enforced in their load-bearing parts (call-time single source, kill switch on both paths, narrow OSError set, non-recoverable stop result), and the remaining gaps are advisory.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd247-fatal-error-classification.md sha256:b2c8be0f105e46e2d4d49d2690ac23e0a69483d935d139e7dae2551843b5c812
red: engine_py/tests/test_bd247_fatal_error_classification.py sha256:6a6e54af34b3775cc75272d9fd0ddb8ffaf894561a4f0c36ea09cc31dae7e33a
-->
