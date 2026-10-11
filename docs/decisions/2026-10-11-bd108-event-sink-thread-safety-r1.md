# bd#108 validation gate, round 1

Spec: docs/decisions/2026-10-11-bd108-event-sink-thread-safety.md. RED: engine_py/tests/test_bd108_event_sink_thread_safety.py (18 tests, 0 pass pre-GREEN, assert-time fails; facts.md confirms siblings green).

## 4-step audit

**Step 1, spec-internal consistency.** Tokens `HAL_EVENT_SINK_SERIALIZE`, `event_sink_serialize_enabled`, `SerializedEventSink`, `_APPEND_LOCK` are used identically in spec and RED. The `gate_enabled` semantics (on unless exactly `0`) match `config_provider.py:52-54`. The free-function pattern matches `run_id_case_guard_enabled` (`config_provider.py:330-334`). Retire-by 2026-11-10 is 30 days from 2026-10-11, inside the 45-day bound. `helpers.changelog.require_entry/parse_sections/is_unreleased/read_changelog` all exist. PASS.

**Step 1.5, dispatcher simulation.** `get_event_sink` has three pass-through branches (original factory, already wrapped, switch off) and one wrap branch. Each branch is exercised: original (AC3 stock, `type is EventLog`), already wrapped (AC4), off (AC2), wrap (AC1/AC3/AC2-other). Order is not ambiguous. One note: "original factory" is tested by identity of `_DEFAULT_FACTORY`, which the RED reaches only via reset. OK.

**Step 2, §2 vs §3.** Every spec op has an AC: op1 AC1/AC4, op2 AC1-AC4, op3 AC5, op4 AC2/AC6, op5 AC6. The lock-release path is covered by AC4. The module-level shared lock (op1's stated rationale) has no AC. See finding 1.

**Step 3, RED adequacy.**
- (a) AC1: without a lock the first caller reads `count`, waits for a second caller, and a second caller always arrives (the other threads are released by the Barrier), so `max_inside >= 2` and duplicate `seq` values. AC1 fails without the lock and passes with a correct one. It cannot hang: the Barrier, `join(60)`, and the 2 s event wait are all bounded. With the lock the single wait times out once (a 2 s cost per serialized run).
- Only the first caller ever waits (`_first_taken` stays set), so the race window is one event wide. That is sufficient to fail an unserialized implementation.
- AC3 file sink: without a lock the header half is written, then another thread's header lands in between. At least one line is corrupt or the line count is wrong. With the lock the file is whole. It fails without the lock and passes with it.
- (c) The 2 s bound is not asserted anywhere (no elapsed-time or `wait()` return check). OK. No sleeps. No mock of the unit under test: `get_event_sink` is real, and only the injected sink is a fake, which is the intended seam.
- Loaded-runner flake: `test_ac2_switch_off` asserts `max_inside >= 2`. If the second thread were delayed more than 2 s the overlap would not occur. This is very unlikely and is MINOR.
- (b) Wrapping the stock EventLog is caught by `type(sink) is EventLog`. **A per-wrapper lock is NOT caught.** `_run_seam` and `_drive` share ONE wrapper across all threads, so a per-instance lock passes AC1 and AC3. See finding 1.

**Step 4, reachability.** Point: `event_sink.get_event_sink`. Hosts: `run.py:78,123,222`, `phase_sentinel.py:168,210`, `restart_governor.py:221,274,349`, `stuck_report.py:112,149`, `emit_resolver.py:36`. Test path: the injected factory goes through the real `get_event_sink`. Reachable. Note that most production sites call `get_event_sink(path).append(...)` per emit, so each call gets a NEW wrapper over the factory's (often singleton) sink.

## Adversarial edges (not covered by the AC table)

1. Cross-wrapper serialization: threads that each call `get_event_sink()` themselves (the production pattern), over one shared sink object. Not tested. This is the whole reason the spec chose a module-level lock.
2. Re-entrancy: a sink whose `append` re-enters another wrapper's `append` on the same thread. A plain `Lock` would deadlock; the spec chose `RLock` but nothing pins it.
3. `__getattr__` delegation when `_inner` is absent (copy, pickle). An infinite-recursion risk. The spec does not mention it. Advisory.
4. The factory returns a stock `EventLog` while injected. The spec says it is wrapped; the RED does not pin this.

## Checklist

- AC1: FAIL
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS

## Findings

1. MAJOR (AC1, adversarial edge 1). The RED cannot distinguish the spec's required module-level `_APPEND_LOCK` from a per-wrapper lock. Both AC1 and AC3 drive a single wrapper shared by all threads. Production resolves per call (`run.py:222`, `phase_sentinel.py:210`, `restart_governor.py`), so with a per-wrapper lock the seam would give no protection in production while every RED test passes. Required fix: add a test in which each thread calls `event_sink.get_event_sink()` itself (a fresh wrapper per call, or per thread) over the one `_UnsafeSink` returned by the factory. Assert `max_inside == 1` and `seq == 0..59`. Without the module-level lock this fails.
2. MINOR. Add a re-entrancy test (wrapper A's inner `append` calls wrapper B's `append` over the same inner sink, in one thread, with a bounded join) to pin the `RLock`. Without it a plain `Lock` passes the RED.
3. MINOR. Each serialized run costs a 2 s wait: AC1, 4 serialized runs in AC2, and AC3, about 14 s in total. Consider a smaller window, or arming the rendezvous with `wait(WINDOW)` only for the unserialized check. Bounded and not asserted, so advisory.
4. MINOR. `test_ac2_switch_off` `max_inside >= 2` depends on the second thread arriving within 2 s. Document it as an accepted residual, or raise the window for that test only.

VERDICT: REJECTED

VERDICT: REJECT
