# bd#203 gate review r1: semantic verifier run-scoped event log

Scope: spec `2026-10-10-bd203-semantic-verifier-run-scoped-log.md` (DRAFT r1) and RED file `engine_py/tests/test_bd203_semantic_verifier_run_log.py` (16 failed, 0 passed pre-GREEN; the four sibling suites pass 20/19/20/46 on the base).

## 1. Audit

**Spec facts against the code.** All hold on `f255024`. `semantic_verifier.py` has exactly three `EventLog().append(...)` sites (L413, L529, L647), each inside a `try` with `except (OSError, ValueError, TypeError)` and an `EventLog is None` guard. `EventLog.__init__` creates the parent directory. `default_log_path()` resolves the host override, then HAL-root, then cwd-relative. `run_scoped_log_path()` raises `ValueError` for ids failing `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` or containing `..`; `../x`, `a/b`, `.h` and a 129-char id are all rejected. `telemetry_ctx.get_current_run()` / `get_invocation_run_id()` exist as described. `gate_enabled` is "enabled unless exactly `0`", matching the kill-switch semantics. `HAL_RUN_LOG_ROOT` is the env the provider reads, and the fixture sets it. The flag-catalog neighbour (`HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`) has the same entry shape the spec prescribes. No literal-token drift found (`_run_event_log`, `HAL_SEMANTIC_VERIFY_RUN_LOG`, event names consistent between spec and RED).

**Rule overlap (helper rungs).** Simulated in order: kill switch, active-run sink, run id, default. Each AC maps to the intended rung: AC1-3 and AC7-on reach rung 3, AC4 rung 2, AC5 rung 4, AC6 rung 3 degrading to rung 4, AC7-off rung 1. No AC lands on a different rung than specced.

**Spec section 2 vs section 3.** Every producing path (three emit sites, helper, flag, changelog) has an AC; every AC has a producing op. The op-to-AC map is consistent.

**RED adequacy.** Tests exercise the real `verify_findings_semantic`; the only seam is the chokepoint backend registry, and `sv.EventLog` is not patched, so there is no double of the unit under test. Failures occur at assert time (new names are touched only inside test bodies). AC1-AC4, AC7, AC8, AC9 fail pre-GREEN on real behaviour or catalog absence. AC5 and AC6: see finding 1.

**Reachability.** Each side-effect AC traces emit site (L413/L529/L647) to `verify_findings_semantic` to a test that calls it directly with a real review document.

**Existing tests.** `test_semantic_verifier_W15.py:499` patches `sv.EventLog` with a Mock (`create=True`) and calls with `ctx=None`. After GREEN the helper reaches rung 4 and calls the patched class with `default_log_path()`; `mock.return_value.append` still receives the event, so the test passes provided the helper uses `getattr(ctx, "session_id", None)` (not `ctx.session_id`) and looks `EventLog` up as a module global at call time. Both are implied by the spec but not stated. The other callers of `verify_findings_semantic` in tests do not set `session_id`, so GREEN will not start writing under the default run-log root from the existing suites.

**Active-run sink safety.** Safe in the normal engine flow: the slot is thread-local and cleared after each step, and the sink is exactly the log the engine already treats as the run's. Residual risks are in finding 2 and 3.

**Scope.** One problem (where the verifier's three events are written), one helper, one flag, one changelog bullet. No scope creep.

## 2. Adversarial edges (not covered by an AC)

1. Invocation-id fallback: `ctx.session_id=None` with `telemetry_ctx.set_invocation_run_id("R203")` and no active run should write to `<root>/R203/events.jsonl`. No test pins rung 3's second source.
2. Session id precedence: `ctx.session_id` set and a different invocation id set; spec says session id wins, untested.
3. Active run whose `event_log` is `None`: spec says fall through to rung 3; untested (a GREEN that dereferences `.event_log.append`-style or returns `None` would crash inside the `try`, silently losing events).
4. Kill switch versus active run: `=0` with an active run set must still use the default path (rung 1 precedes rung 2); AC7 runs without an active run.
5. Active-run sink whose `run_id` differs from `ctx.session_id` (stale slot from a previous in-process run): the spec makes the sink win unconditionally.
6. Thread-pool caller without `run_with_current_run`: the slot is empty, so rung 3 applies; behaviour is fine but unpinned.

## 3. Checklist

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

## 4. Findings

1. MINOR, RED `test_ac5_*` and `test_ac6_*` (L169, L184): these are regression shields. Pre-GREEN their behavioural assertions already hold on the old code, so the `hasattr(sv, "_run_event_log")` line is the only reason they fail. The assertion after it still pins real behaviour after GREEN (location equals `default_log_path()`, no run directory created, no raise, only the expected paths created), so the tests are not name-only; the name check is a legitimate chokepoint pin, and AC8 pins the same name structurally. Acceptable. Optional hardening: none required.
2. MINOR, spec section 2 step 2: the active-run sink is chosen without comparing its `run_id` to `ctx.session_id`. In a long-lived process a stale slot could redirect events to another run's log. Engine clears the slot per step, so this is advisory; the choice should at least be stated as intentional (edge 5 above).
3. MINOR, RED coverage: edges 1-4 above (invocation-id rung, session-id precedence, `event_log is None`, kill switch over an active run) have no test; a GREEN that mis-orders rungs 1 and 2 or mishandles a `None` sink would pass the RED. Suggest one added test each if cheap; not blocking because the spec's rung order is explicit and AC4/AC7 cover the main paths.
4. MINOR, spec section 2 op1: state that the helper must tolerate `ctx=None` (`getattr(ctx, "session_id", None)`) and resolve `EventLog` as a module global at call time; the W15 suite depends on both (`ctx=None`, `sv.EventLog` patched).

No MAJOR findings. GREEN as specced makes all 16 RED tests pass and leaves the four sibling suites green.

VERDICT: APPROVE

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd203-semantic-verifier-run-scoped-log.md sha256:7aa7492a19feb0f9306450878c93663dce7e9ec5bb9742b5a88286609e9f399b
red: engine_py/tests/test_bd203_semantic_verifier_run_log.py sha256:0d3d9b82fc7ea891ecf9955a0e57ec1b0535d2dc833ac1d07b514054d66b540b
-->
