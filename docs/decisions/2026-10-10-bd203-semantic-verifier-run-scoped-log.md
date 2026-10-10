---
red_tests: [engine_py/tests/test_bd203_semantic_verifier_run_log.py]
sibling_tests: [engine_py/tests/test_bd102_semantic_verifier_stop_codes.py, engine_py/tests/test_semantic_verifier_W15.py, engine_py/tests/test_bd82_semantic_verifier_chokepoint.py, engine_py/tests/test_bd93_run_scoped_event_log.py]
paths: [engine_py/bytedigger_engine/lib/plugins/anti_hallucination/semantic_verifier.py, engine_py/bytedigger_engine/flags_catalog.py, CHANGELOG.md]
---
# bd#203: the semantic verifier writes its events to the run's own event log

**Status: DRAFT r1** · **Tier:** 2 (engine `.py`: spec, RED, gate, GREEN) · **Class:** BUG (follow-up of bd#93, #204)
**Base:** `origin/main` `f255024`.
**Chokepoint:** one private helper `_run_event_log(ctx)` in `engine_py/bytedigger_engine/lib/plugins/anti_hallucination/semantic_verifier.py`. It becomes the only place in the module that constructs an `EventLog`; the three emit sites call it.

## 1. Problem (measured on `f255024`)

- `verify_findings_semantic` emits `phase_6_semantic_verify_skipped` (L413), `phase_6_semantic_verify_stopped` (L529) and `phase_6_semantic_verify_complete` (L647) through `EventLog().append(...)`. The constructor takes no path, so it resolves `event_log.default_log_path()`: the host override, else a path under the process cwd.
- Since bd#93 the run's log is `<run_log_root>/<run_id>/events.jsonl` (`event_log.run_scoped_log_path`) or the explicit `--event-log` / host override sink, which the engine hands to steps as the current run's `event_log` (`telemetry_ctx.get_current_run()`). When the process cwd is not the run directory, the three verifier events land under the cwd and the run's own log has no record of the verification step.
- Reference case: run the verifier with a cwd other than the run directory. Actual: the events are under the cwd. Expected: they are in `<root>/<run_id>/events.jsonl` and nothing is written under the cwd.

## 2. Design

- **op1, helper.** `_run_event_log(ctx)` returns the `EventLog` to write to, first match wins:
  1. kill switch off (op3): `EventLog(default_log_path())`, byte-for-byte the old writer;
  2. the active run's sink: `telemetry_ctx.get_current_run().event_log` when a run is set and its `event_log` is not `None`;
  3. a run id (`ctx.session_id`, else `telemetry_ctx.get_invocation_run_id()`): `EventLog(run_scoped_log_path(run_id))`; a `ValueError` for an unsafe id degrades to step 4 (logged at warning, never raised);
  4. no run id: `EventLog(default_log_path())` (today's behaviour, standalone callers are unaffected).
  `default_log_path` and `run_scoped_log_path` are imported from `bytedigger_engine.event_log` next to the existing `EventLog` import and keep the module's `EventLog is None` degrade (the helper is not reached then).
- **op2, call sites.** The three `EventLog().append(...)` become `_run_event_log(ctx).append(...)` inside the existing `try`; payloads, event names and the `run_id=getattr(ctx, "session_id", None)` argument are unchanged, as is the existing `except (OSError, ValueError, TypeError)`. No other line of the module changes (the bd#247 classification is not touched).
- **op3, kill switch.** New gate flag `HAL_SEMANTIC_VERIFY_RUN_LOG`, kind `gate`, default `"1"`, module `lib/plugins/anti_hallucination/semantic_verifier.py`, read at call time with `config_provider.get_config().gate_enabled(...)`. `=0` restores the cwd-relative writer for all three events. Catalog entry carries `owner: "guy-lifshitz"` and `provenance: "introduced: bd#203 - ..."`.
- **op4, docs.** One bullet in the existing `### Fixed` block of CHANGELOG `[Unreleased]` (no second `### Fixed` block).

Out of scope: run directory retention (#260); run id case collision (#261); error classification and the stop/skip logic (bd#247, bd#102); any other module's `EventLog()` use; payload changes.

## 3. Acceptance criteria. RED file: `engine_py/tests/test_bd203_semantic_verifier_run_log.py`

Real path, no doubles of the unit under test: `verify_findings_semantic` runs against a real review doc on `tmp_path`; the model seam is the chokepoint backend registered through `register_backend("claude-subprocess", ...)` returning a scripted `StepResult` (as `test_bd102_semantic_verifier_stop_codes.py`). Each test points `HAL_RUN_LOG_ROOT` at its own `tmp_path` subdir, clears the host event-log override env, clears `telemetry_ctx` current run and invocation id, and `monkeypatch.chdir`s into an empty directory `elsewhere` distinct from the run root. `sv.EventLog` is NOT patched. No network, no sleeps, no real `claude`.

- **AC1** `ctx.session_id="R203"`, FAIL verdict with no CRITICAL/HIGH finding (skip path): the step is ok, `<root>/R203/events.jsonl` holds exactly one row of event type `phase_6_semantic_verify_skipped`, and `elsewhere` is still empty.
- **AC2** Same ctx, one REPRODUCED finding (complete path): the run log holds one `phase_6_semantic_verify_complete` row; `elsewhere` is empty.
- **AC3** Same ctx, the backend returns `E_LLM_SPEND_LIMIT` (stop path): the run log holds one `phase_6_semantic_verify_stopped` row with that `error_code`; `elsewhere` is empty.
- **AC4** An active run is set with `telemetry_ctx.set_current_run(event_log=EventLog(<tmp>/sink.jsonl), run_id="R203", ...)`: the complete row is in `sink.jsonl`; `<root>/R203/` does not exist; `elsewhere` is empty.
- **AC5** `ctx.session_id=None`, no invocation id, no active run (standalone caller): the complete row is in the file `event_log.default_log_path()` resolves to under `elsewhere`; no run directory is created under the root. Same location as before the change.
- **AC6** Unsafe ids (`"../x"`, `"a/b"`, `".h"`, 129 characters), parametrized: the step returns the same ok result as for AC2, raises nothing, the row is in the `default_log_path()` file, and nothing is created at or above the root other than what existed.
- **AC7** Kill switch: `HAL_SEMANTIC_VERIFY_RUN_LOG=0` with `session_id="R203"`: the row is in the `default_log_path()` file under `elsewhere`; `<root>/R203/` does not exist. With the variable unset or `1`, AC2 holds.
- **AC8** (GUARD) Parsing the module source with `ast`: there are no `EventLog` calls without arguments, and every call to `EventLog(...)` sits inside the function `_run_event_log`; at least one such call exists, and the three event names are each emitted through `_run_event_log(...)`.
- **AC9** (GUARD) `flags_catalog.FLAGS["HAL_SEMANTIC_VERIFY_RUN_LOG"]` has kind `gate`, default `"1"`, a non-blank `owner` and a `provenance` starting with `introduced:`; `scripts/flag_owner_lint.py` exits 0 on the live catalog; CHANGELOG `[Unreleased]` mentions the flag in its `Fixed` block (shared helper `from helpers.changelog import read_changelog, require_entry`, as `test_bd101_fresh_default.py` does; no `Unreleased`+`.index(` line, no top-section pin).
- **AC10** (GUARD) Existing `test_bd102_semantic_verifier_stop_codes.py`, `test_semantic_verifier_W15.py`, `test_bd82_semantic_verifier_chokepoint.py` and `test_bd93_run_scoped_event_log.py` stay green unmodified.

## 4. op <-> AC map

op1: AC1-AC7 · op2: AC1-AC3, AC8 · op3: AC7, AC9 · op4: AC9. Existing tests: AC10.

## 5. Adversarial edges

1. Unsafe run id from a caller: degrade to the old writer, never raise, never write outside the root (AC6).
2. The engine's own sink (explicit `--event-log`, host override) must win over a recomputed run-scoped path, otherwise the events split across two files (AC4).
3. Standalone caller with no run id: unchanged location (AC5).
4. `EventLog` construction creates the parent directory; a failure there is an `OSError` inside the existing `try` and only logs a warning (the three sites keep their `except`).
5. Kill switch restores the old writer for all three sites at once, because they share the helper (AC7, AC8).
