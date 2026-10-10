# bd#247: one fatal/transient classification of chokepoint error codes, read by the semantic verifier

**Status: DRAFT r1** · **Class:** BUG (follow-up of bd#102 / #246)
**Base:** `origin/main` `0777ae9`.
**Chokepoint:** `engine_py/bytedigger_engine/llm_subprocess.py` (the one place model-call errors are produced). The consumer is `verify_findings_semantic` in `engine_py/bytedigger_engine/lib/plugins/anti_hallucination/semantic_verifier.py`, the only caller of `_invoke_verifier_agent`.

## 1. Problem (measured on `0777ae9`)

- `semantic_verifier.py:64-67` keeps its own list, `_STOP_ERROR_CODES`, of four codes. Every other chokepoint error that cannot succeed on a retry still goes to the tag-and-continue branch (`:236-241`), so a list of 20 findings costs 20 doomed calls and the step ends `ok`:
  - `E_LLM_CMD_MISSING` (`llm_subprocess.py:2224`, the `FileNotFoundError` of `Popen`),
  - `E_LLM_BACKEND_NO_MANIFEST` (`:584`), `E_MODEL_PIN_MISMATCH` (`:1383`), `E_INJECT_UNATTRIBUTED` (`:1074`, `:1088`), `E_TOOL_RESTRICTION_UNSUPPORTED` (`:1158`).
- The `except OSError` branch (`semantic_verifier.py:215-218`) tags UNVERIFIED for every `OSError`. Measured: with a `claude` file on `PATH` that is not executable, the chokepoint does not return an error, it raises `PermissionError`. With no `claude` on `PATH` it returns `E_LLM_CMD_MISSING`. So "binary not found" reaches the verifier in two shapes, one as a code and one as an exception.
- No test runs the engine over the step. All tests in `test_bd102_semantic_verifier_stop_codes.py` call `verify_findings_semantic` directly, so "a stopped step ends the phase as paused" is read from code, not proven.

## 2. Design

- **op1, classification at the chokepoint.** In `llm_subprocess.py`:
  - `FATAL_ERROR_CODES: frozenset[str] = PAUSE_LANE_ERROR_CODES | {"E_CAPABILITY_ESCAPE", "E_LLM_BACKEND_UNKNOWN", "E_LLM_RUN_ID_MISSING", "E_LLM_CMD_MISSING", "E_LLM_BACKEND_NO_MANIFEST", "E_MODEL_PIN_MISMATCH", "E_INJECT_UNATTRIBUTED", "E_TOOL_RESTRICTION_UNSUPPORTED"}`. A fatal code is one on which every further call of the same step is doomed or unsafe: spend limit (pause lane), a write-tool escape, an unusable or unconfigured backend, a missing binary, a model or tool contract the backend cannot honour, an unattributed injection.
  - `is_fatal_error_code(code) -> bool`: membership in `FATAL_ERROR_CODES`. Anything else, including `None`, `""` and a code not seen before, is transient. Transient means tag-and-continue: `E_LLM_TIMEOUT`, `E_LLM_API_TIMEOUT`, `E_LLM_NO_PROGRESS`, `E_STEP_TIMEOUT`, `E_LLM_EXIT`, `E_LLM_RESULT_ERROR`, `E_LLM_NO_RESULT_EVENT`.
  - `classify_os_error(exc) -> str | None`: returns `"E_LLM_CMD_MISSING"` when `exc` is a `FileNotFoundError` or `PermissionError` (binary not found, or no right to run it), otherwise `None` (transient: any other `OSError`, including the built-in `TimeoutError`, `ConnectionError`, `BrokenPipeError`, `BlockingIOError`, out of memory or file descriptors). Decision on the open question: file-not-found and permission-denied are fixed by a human, not by the next call; the other `OSError` kinds depend on momentary load or the network and can pass on the next finding. A wrong "fatal" costs one stopped step with a clear code; a wrong "transient" costs up to 20 doomed calls, which is the status quo, so the narrow fatal set is the safe side.
- **op2, the verifier reads it.** `semantic_verifier.py` deletes `_STOP_ERROR_CODES` and its `PAUSE_LANE_ERROR_CODES` import. `_invoke_verifier_agent` raises `_VerifierStop` when the result is an error, `llm_subprocess.is_fatal_error_code(result.error_code)` holds and `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL` is on. In the `except OSError` branch it does the same with `llm_subprocess.classify_os_error(exc)`: a code gives `_VerifierStop(StepResult(status="error", error_code=<code>, error=str(exc), recoverable=False, step_name="verify_findings_semantic", data={}))`, `None` keeps the current tagged `agent_error` string. The existing error-level log for `E_CAPABILITY_ESCAPE` (`:227`) becomes error-level for any fatal code, so the module keeps no code literal. The verifier is read at call time through the module attribute, so there is one list, in one file.
- **op3, kill switch.** The existing `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL` (default `1`) covers every code above; `=0` restores tag-and-continue for all of them, the `OSError` path included. No new flag. The flag has been default-on since #246, so there is no separate shadow stage for the added codes: the switch is the rollback and the per-code tally (`semantic_unverified_reasons`) plus `phase_6_semantic_verify_stopped` already make the effect visible.
- **op4, docs.** The catalog description of the flag names the classification instead of four codes; one bullet in the existing `### Fixed` block of CHANGELOG `[Unreleased]`.
- **Provider and billing mode.** The classification is on error codes and does not look at the backend name, so it holds for the subscription backend (`claude-subprocess`) and for an API backend alike. A provider refusal degrades to a stopped step carrying the original code and data (the pause lane still engages for a spend limit), not to a crash or a silent `ok`.

Out of scope: `EventLog` use in the verifier (issue #203); other callers of the chokepoint; changing which codes are pause-lane; retry or resume logic; the `Popen` path of the chokepoint (it keeps returning `E_LLM_CMD_MISSING` for `FileNotFoundError` and raising `PermissionError`); the unrelated eviction set in `lib/dbos_setup.py`.

## 3. Acceptance criteria

Two files. NEW `engine_py/tests/test_bd247_fatal_error_classification.py`. The old `test_bd102_semantic_verifier_stop_codes.py::test_ac11_*` pinned the removed constant and is rewritten in the RED step to pin the new source (`sv` has no `_STOP_ERROR_CODES`; `llm_subprocess.FATAL_ERROR_CODES` contains `env_limit.PAUSE_LANE_ERROR_CODES` and the four original codes). Its other tests stay as they are.

Real path everywhere: a real review doc on `tmp_path`, the real `verify_findings_semantic`, the real chokepoint. The model boundary is the registered backend returning a scripted `StepResult` (the same seam as the bd#102 tests); for the missing-binary cases no backend is scripted: `HAL_RUNNER_BACKEND=claude-subprocess` with a `PATH` that points at an empty directory, or at a directory holding a non-executable `claude` file.

- **AC1** `FATAL_ERROR_CODES` equals the nine codes of op1 and contains `env_limit.PAUSE_LANE_ERROR_CODES`. `is_fatal_error_code` is `True` for each of them and `False` for `E_LLM_TIMEOUT`, `E_LLM_API_TIMEOUT`, `E_LLM_NO_PROGRESS`, `E_STEP_TIMEOUT`, `E_LLM_EXIT`, `E_LLM_RESULT_ERROR`, `E_LLM_NO_RESULT_EVENT`, `None`, `""` and `"E_SOMETHING_NEW"`.
- **AC2** `classify_os_error` returns `"E_LLM_CMD_MISSING"` for `FileNotFoundError` and `PermissionError`, and `None` for `OSError(errno.EAGAIN, ...)`, `OSError(errno.ENOMEM, ...)`, `TimeoutError`, `ConnectionResetError`, `BrokenPipeError`.
- **AC3** Reference case, parametrized over the five added chokepoint codes: `E_LLM_BACKEND_NO_MANIFEST`, `E_MODEL_PIN_MISMATCH`, `E_INJECT_UNATTRIBUTED`, `E_TOOL_RESTRICTION_UNSUPPORTED` scripted from the backend, and `E_LLM_CMD_MISSING` produced for real by an empty `PATH`. 20 findings, the first call fails. The step returns `status="error"` with that `error_code`; the review doc bytes are unchanged; one `phase_6_semantic_verify_stopped` row carries the code and no `phase_6_semantic_verify_complete` row exists. For the scripted ones `data`, `recoverable` and `error` equal what the backend returned and the backend was called once.
- **AC4** OSError path, real: a non-executable `claude` on `PATH` makes the chokepoint raise `PermissionError`; the step returns `status="error"`, `error_code="E_LLM_CMD_MISSING"`, `recoverable=False`, `error` containing the OS message, after one attempt, and the doc is unchanged.
- **AC5** Transient stays: a backend that raises `OSError(errno.EAGAIN, ...)` on every call with 3 findings gives step `ok`, three attempts, `semantic_unverified == 3`, three `[UNVERIFIED]` tags in the doc; the same with `TimeoutError`. A scripted `E_LLM_TIMEOUT` result on every call gives `semantic_unverified_reasons == {"agent_timeout": 3}`.
- **AC6** Kill switch: with `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL=0`, `E_LLM_CMD_MISSING` (empty `PATH`), the `PermissionError` case and one scripted added code each give step `ok`, one attempt per finding, every finding tagged UNVERIFIED. With the variable unset or `1`, AC3 and AC4 hold.
- **AC7** One source: with `llm_subprocess.FATAL_ERROR_CODES` replaced (monkeypatch) by itself plus `E_LLM_EXIT`, a verifier call that returns `E_LLM_EXIT` stops the step; with the original value it does not (AC5 style). `sv` has no attribute `_STOP_ERROR_CODES`, and the module source holds no literal of the codes `E_CAPABILITY_ESCAPE` or `E_LLM_BACKEND_UNKNOWN`.
- **AC8** Engine level, spend limit: a `WorkflowEngine` runs a one-step workflow whose step calls the real `verify_findings_semantic` on a 20-finding doc; the backend returns `E_LLM_SPEND_LIMIT` on call 1. The `workflow_finished` row has `status == "paused"`; the backend was called once. Parametrized over `HAL_RUNNER_BACKEND` values `claude-subprocess` (subscription) and `openai-compat` (API), the backend being registered under that name with the same scripted seam. With `HAL_PAUSE_LANE=0` the row has `status == "error"` and `error_code == "E_LLM_SPEND_LIMIT"`.
- **AC9** Engine level, missing binary: the same workflow with an empty `PATH` and `HAL_RUNNER_BACKEND=claude-subprocess`: `workflow_finished` has `status == "error"`, `error_code == "E_LLM_CMD_MISSING"` (not `ok`, not `paused`).
- **AC10** (GUARD) Existing `test_bd102_semantic_verifier_stop_codes.py` (with the rewritten AC11), `test_bd82_semantic_verifier_chokepoint.py`, `test_semantic_verifier_*`, `test_phase_6_mass_unverified_5F9817F6.py`, `test_gh576_pause_lane.py` stay green; `scripts/flag_owner_lint.py` exits 0.
- **AC11** (GUARD) CHANGELOG `[Unreleased]` `Fixed` block mentions `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL` and `bd#247`, read through the shared `helpers.changelog` helper (no top-section pin).

## 4. op <-> AC map

op1: AC1, AC2 · op2: AC3, AC4, AC5, AC7, AC8, AC9 · op3: AC6 · op4: AC10, AC11.

## 5. Adversarial edges

1. `PermissionError` and `FileNotFoundError` are `OSError` subclasses; `TimeoutError` and `ConnectionError` are too. The test must pin all of them, so a future "catch OSError" shortcut fails (AC2, AC5).
2. A code the chokepoint adds later is transient until someone lists it; the safe default is the status quo (AC1).
3. A stop code reached through the OSError path has no backend result to return, so the step builds one; it carries `recoverable=False` so the engine does not retry it (AC4).
4. `E_LLM_SPEND_LIMIT` must still reach the engine unchanged so the pause lane engages for both a subscription and an API backend (AC8).
5. A stopped step leaves the review doc byte-identical, so a re-entered run starts clean (AC3, AC4).
