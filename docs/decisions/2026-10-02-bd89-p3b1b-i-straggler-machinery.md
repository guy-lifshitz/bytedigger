# bd#89 P3b1b-i: remove the straggler-abort machinery from `llm_subprocess.py`

**Status: FROZEN r1** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** PROCESS (dead-code removal after bd#89 P3b1; completes the single-reviewer cut) ·
**Chokepoint:** `invoke_llm_subprocess` (`engine_py/bytedigger_engine/llm_subprocess.py:1888`). It is the single public entry that turns a caller's `straggler_cfg` into a watchdog arm. It feeds the probe `_assert_backend_supports_watchdog` (`:590`), the dispatch `_resolve_backend_and_invoke` (`:2117`) and the `claude-subprocess` backend body `_invoke_subprocess` (`:2156`, which arms at `:2277`). Neutralising the value at this entry and deleting the arm in the one backend that consumes it removes the feature everywhere.
**Side of the seam (decision 2026-07-26 §7.1):** engine, plus CHANGELOG and `docs/backends.md`. No orchestrator-flow md, no gate script, no TS.
**Base:** `origin/main` `a1cbda1` (P3b1 #198 merged as `abed94a`). **Source:** bd#89 row 2 split (comment 5955412867). Scope was decided on #89 (BLOCKED-DECISION comment 5956226542, answer: option A). P3b1b is split into P3b1b-i (this, straggler machinery) and P3b1b-ii (aggregation step, role glob, `E_NO_ROLE_FILES`, banner, 21 -> 20 steps). Template: P3b1 spec `2026-10-02-bd89-p3b1-single-reviewer.md`.

## §0 Scope size

- 2 prod files: `llm_subprocess.py` and `workflows/phase_6_review.py` (one line).
- 2 docs: CHANGELOG and `docs/backends.md`.
- 1 RED file: `engine_py/tests/test_bd89_p3b1b_i_straggler_removed.py`.
- 1 test file deleted: `test_ccbb65dc_straggler_watchdog.py`.
- 6 sibling test files edited (§5).
- Phase 6 stays at 21 steps. The step count is P3b1b-ii's job.

## §1 Problem (measured on `a1cbda1`)

Since P3b1, nothing arms the watchdog. Phase 6 passes the literal `straggler_cfg=None` (`phase_6_review.py:1058`), and no other caller in `bytedigger_engine` passes the kwarg; this was grepped across `workflows/*.py` and `lib/**`. The following code is still live but unreachable from any workflow:

- the `_StragglerWatchdog` class plus `STRAGGLER_PATIENCE_SEC` and `STRAGGLER_POLL_INTERVAL_SEC` (`:661-752`);
- the arm, stop and synthetic-ok branch in `_invoke_subprocess` (`:2277-2293`, `:2352-2354`, `:2376-2402`), which emits the `straggler_abort` event and the `straggler_aborted` data field;
- the `straggler_enabled` conjunct in `_assert_backend_supports_watchdog` (`:590-605`) and in `_fallback_hang_attempts` (`:1830-1865`);
- the `straggler_enabled` key in the `runner_capability_probe_failed` payload (`:2060-2080`).

`straggler_cfg` is also part of the **public** backend seam:

- `LLMBackend` (`:2801`) declares it as a required keyword.
- `examples/library/custom_backend.py:49` declares it without a default.
- `docs/backends.md:130` names `register_backend` and `LLMBackend` as the public seam.
- About 20 test files call `invoke_llm_subprocess(..., straggler_cfg=None)`.

If the kwarg disappeared from the dispatcher, every third-party backend written from the example would raise `TypeError`.

## §2 Design: delete the machinery, keep the kwarg as an ignored deprecation

Precedent: P3b1 ignored a stale `review_fanout: parallel` key with an event instead of failing (constraint 1, degrade not fail).

- **op1** Delete `_StragglerWatchdog`, `STRAGGLER_PATIENCE_SEC` and `STRAGGLER_POLL_INTERVAL_SEC`, along with their comment block.
- **op2** In `_invoke_subprocess`, delete the arm (`:2277-2293`), the stop (`:2352-2354`) and the synthetic-ok branch (`:2376-2402`). The `straggler_cfg` parameter stays in the signature because it is a registered backend that the protocol calls, but it is unused. Its docstring line reads "deprecated, ignored (#202)".
- **op3** `_assert_backend_supports_watchdog(backend, *, idle_enabled)`:
  - drop the `straggler_enabled` parameter and the `"abort"` branch;
  - `_fallback_hang_attempts(resolved_backend, result, *, idle_enabled)` drops the parameter too;
  - the `runner_capability_probe_failed` payload becomes exactly `{backend, reason, idle_enabled}`.
- **op4** In `invoke_llm_subprocess`, `straggler_cfg` keeps its default `None`.
  - When the value is not `None`, emit `straggler_cfg_ignored` once, with the payload `{"step_name": <step_name>}`. Emit it through `_emit_safe` on the active run context resolved at `:2007`; with no run context, emit nothing.
  - The value is then dropped. The dispatcher passes the literal `straggler_cfg=None` to every backend (`:2117`, `:2146`), so backends that require the keyword keep working.
  - The value never reaches a probe or a watchdog.
- **op5** `phase_6_review.py:1058`: delete the `straggler_cfg=None,` line. Phase 6 then contains no `straggler` token.
- **op6** Docs:
  - CHANGELOG: the watchdog, `straggler_abort` and `straggler_aborted` are removed. A non-None `straggler_cfg` is ignored with `straggler_cfg_ignored`, and the kwarg is deprecated (#202, kill-by 2027-01-31).
  - `docs/backends.md`: one sentence under "Bring your own" saying that `straggler_cfg` is still passed as `None` and is deprecated.

Kept on purpose:
- `LLMBackend.straggler_cfg`, the reference-backend parameters, the example signature, and the `"abort"` capability token. The token stays in `claude-subprocess`'s built-in set and is still accepted by `register_backend`, which has no vocabulary, but it has no consumer.
- All of these go in #202 (kill-by 2027-01-31). A known divergence until then: the token `abort` is declared but nothing reads it.

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd89_p3b1b_i_straggler_removed.py`

- **AC1** (op1, op2) The module no longer has `_StragglerWatchdog`, `STRAGGLER_PATIENCE_SEC` or `STRAGGLER_POLL_INTERVAL_SEC` (`hasattr` is False). The AST-collected string constants of `llm_subprocess.py` contain none of `"llm-straggler-watchdog"`, `"straggler_abort"` or `"straggler_aborted"`.
- **AC2** (op3) `inspect.signature(_assert_backend_supports_watchdog)` has parameters exactly `["backend", "idle_enabled"]`.
  - `idle_enabled=False` returns `None` for a backend registered with no capabilities.
  - `idle_enabled=True` on that backend returns `E_LLM_WATCHDOG_UNSUPPORTED`, and the error text contains `['progress_since']` but not `abort`.
- **AC3** (op3) The parameters of `_fallback_hang_attempts` are exactly `["resolved_backend", "result", "idle_enabled"]`.
- **AC4** (op3, side effect, §1l) Set up a real `EventLog` on disk under a `tmp_path` run context, a stub backend registered with no capabilities, and `idle_timeout_sec=5`. The written jsonl holds one `runner_capability_probe_failed` record, and its payload keys are exactly `{"backend", "reason", "idle_enabled"}`.
- **AC5** (op4, side effect, §1l) Same real on-disk `EventLog`, with a spy backend registered with `{"progress_since"}`. Calling `invoke_llm_subprocess(..., straggler_cfg={"reviews_dir": str(tmp_path), "expected_n": 2})` must:
  - return the spy's `StepResult` unchanged (status `ok`, no `straggler_aborted` key);
  - write exactly one `straggler_cfg_ignored` record with the payload `{"step_name": <step>}` to the jsonl;
  - pass `straggler_cfg=None` to the spy;
  - start no thread named `llm-straggler-watchdog` (checked with `threading.enumerate()` snapshot diff).
- **AC6** (op4) With `straggler_cfg=None` or the kwarg omitted (parametrized), no `straggler_cfg_ignored` record is written and the spy still receives `straggler_cfg=None`. With a non-None cfg and no active run context, the call returns `ok` and raises nothing.
- **AC7** (op2) `claude-subprocess`, the real `_invoke_subprocess` body, is called with `straggler_cfg={"reviews_dir": ..., "expected_n": 2}`. `_stream_read_events` is mocked to the ok tuple, the same seam G2-AC2 in `test_4C03CCED` uses. The result is `status == "ok"` and `"straggler_aborted" not in data`. No watchdog thread is started.
- **AC8** (op5) `phase_6_review.py` source contains no `straggler` token (case-insensitive). With an `invoke_llm_subprocess` spy, `_invoke_review_llm` call kwargs do not contain `straggler_cfg`.
- **AC9** (GUARD, kept on purpose)
  - `invoke_llm_subprocess`'s signature still has `straggler_cfg` with default `None`.
  - `LLMBackend.__call__`'s signature still has `straggler_cfg`.
  - `"abort" in _DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]`.
- **AC10** (op6) Content checks:
  - `CHANGELOG.md`'s first `## ` section mentions `straggler_cfg_ignored` and `#202`.
  - `docs/backends.md` mentions `straggler_cfg` and `deprecated`.
- **AC11** (GUARD, §5) `test_ccbb65dc_straggler_watchdog.py` no longer exists. None of the edited siblings, read as text, still references `_StragglerWatchdog`, `straggler_enabled=` or `straggler_aborted`.

### §1w op <-> AC map

op1 -> AC1, AC11 · op2 -> AC1, AC7 · op3 -> AC2, AC3, AC4 · op4 -> AC5, AC6, AC9 · op5 -> AC8 · op6 -> AC10.

### §3 expected-red summary

Before GREEN:
- AC9 passes (premise GUARD).
- AC6's None branch passes, because there is no event today.
- AC1-AC5, AC7, AC8 and AC10, plus AC6's no-run-context leg, fail for the right reason:
  - attributes still present;
  - extra parameters;
  - `straggler_enabled` key in the payload;
  - no `straggler_cfg_ignored`;
  - a watchdog thread started, or the probe raising `E_LLM_WATCHDOG_UNSUPPORTED` on the `{"progress_since"}` spy, because `abort` is missing;
  - the `straggler` token in phase 6;
  - the docs text.
- AC11 fails until RED deletes the file (RED commits its sibling edits in the same commit, so after RED, AC11 is green).

## §4 Out of scope (§1v: files and behaviours NOT in this PR)

- The aggregation step, the `role-*.md` glob, `E_NO_ROLE_FILES`, the fanout banner and the 21 -> 20 step count all go to P3b1b-ii.
- The `LLMBackend` / reference-backend / example `straggler_cfg` parameter and the `abort` token go to #202.
- `conformance/ORACLE_SPEC.md:767` is a historical thread table pinned to an older base. It is not edited.
- The org keys `straggler_abort*` were already unread since P3b1, and there is no config schema entry.
- The orchestrator-flow md, gate scripts and `bytedigger.json` counts go to P3b1c.

## §5 Scope list (§1a sibling-test audit). RED edits these; GREEN treats tests as read-only (§1s)

- **Delete** `test_ccbb65dc_straggler_watchdog.py`. All of its tests cover the deleted watchdog (AC1-AC5 machinery, AC6/AC8/AC10/AC11 arm and synthetic-ok).
- `test_4C03CCED_ship1d_watchdog_capability.py`:
  - retire `test_g2_ac1b_in_session_straggler_fail_closed`, `test_g2_ac3_straggler_abort_step_result_shape_byte_identical_baseline` and `test_g2_ac5_straggler_abort_no_deadlock_wall_bound`;
  - drop `straggler_enabled=` from any remaining direct call of `_assert_backend_supports_watchdog`;
  - drop the `straggler_enabled` key from any payload equality assertion;
  - remove the §1i header line about pre-staging `_StragglerWatchdog`.
- `test_bd145_reserved_observation_fields.py`: retire `_drive_straggler` together with `test_ac4_straggler_synthetic_ok_drops_all_reserved` and `test_ac5_straggler_forged_tools_not_capability_escape`.
- `test_register_backend_A60F1FE3.py`: `test_ac4_capabilities_stored_and_watchdog_gate_accepts` drops `straggler_enabled=True` from the probe call and docstring. The `capabilities={"progress_since","abort"}` storage assertion stays.
- `test_bd139_single_reviewer.py::test_ac6_single_mode_passes_straggler_cfg_none` and `test_bd82_role_backend_effort.py::test_r8_phase6_straggler_check_resolves_the_reviewers_backend`: the assertion `kwargs["straggler_cfg"] is None` becomes `"straggler_cfg" not in kwargs`.
- `test_bd89_p3b1_single_reviewer_only.py` AC15:
  - `test_ac15_phase6_never_arms_straggler` becomes `"straggler_cfg" not in kwargs`;
  - `test_ac15_phase6_only_literal_straggler_cfg_none` becomes zero occurrences of `straggler_cfg` in phase 6;
  - `test_ac15_guard_invoke_llm_subprocess_keeps_straggler_param` is unchanged; it still holds under AC9.

Untouched:
- The roughly 20 files that pass `straggler_cfg=None` into `invoke_llm_subprocess` or accept it in a stub backend; the kwarg is kept, so they still run.
- The comment-only mentions in `test_CF2EE8ED_in_session_cutover.py`, `test_e8433b4e_aggregator_partial_floor.py`, `test_gh1194_mcp_server_losses.py`, `test_GH1399_advisory_format_terminal.py` and `test_llm_subprocess_23680DDA.py`.

Verify scope (§1r):
- the RED file;
- every file above;
- every `tests/*llm_subprocess*`, `*backend*`, `*watchdog*`, `*agent_sdk*` and `*phase_6*` file;
- `test_25e75663`, `test_bd10_l3_authorship`, `test_bd141_p4e`, `test_bd71`, `test_bd82_*`, `test_gh11*`, `test_gh514`, `test_GH901`, `test_gh933`, `test_gh956`, `test_prompt_cache_seam_GH334` and `test_subprocess_telemetry`.

The full suite is CI only.

### GAP list (not ported)

- GAP-1: no N-1 early abort for a slow reviewer. Since P3b1 there is one reviewer, so there is no N-1.

## §6 Resolved (orchestrator, 2026-10-02, AUTO-DECISION)

- Keep `straggler_cfg` as an ignored, deprecated kwarg rather than a hard removal. **Why:** it is a public seam, and the example declares it required. P3b1 set the "ignore with an event" precedent. Removal is tracked in #202 with a kill-by date.
- Keep the `abort` capability token. **Why:** removing it changes the built-in capability set that several siblings pin, and `register_backend` accepts arbitrary tokens. It goes in #202.
