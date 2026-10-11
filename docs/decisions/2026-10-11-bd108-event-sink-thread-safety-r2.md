# bd#108 validation gate, round 2

Spec r2 plus RED (20 tests). Delta over r1: AC1b, AC4b, AC2 residual note.

## Fix verification

- **AC1b closes r1 MAJOR.** `_PerEmit.append` calls `get_event_sink()` on every emit and gets a fresh wrapper over the one singleton `built`. With a per-wrapper lock the three threads hold three different locks. The first caller blocks in the rendezvous inside `built.append`, a second thread enters, `max_inside` reaches 2, and `seq` duplicates appear, so the test fails on `max_inside == 1`. With the module-level `_APPEND_LOCK` it passes. Pre-GREEN it fails at assert time (the factory is not wrapped, so `max_inside >= 2`).
- **AC4b closes r1 minor 2.** Wrapper A's inner calls wrapper B on the same thread. With a module-level plain `Lock` the thread self-deadlocks. The worker is a daemon thread and `join(10)` is bounded, so `assert not th.is_alive()` fails without hanging the test. With an RLock it completes. A per-wrapper plain Lock would pass AC4b but is caught by AC1b, so the pair pins module-level plus re-entrant.
- **AC2 residual** is documented in section 4 (accepted). r1 minor 4 closed. r1 minor 3 (2 s wait per serialized run) is unchanged, advisory.

## 4-step audit on the delta

**Step 1.** Tokens `_APPEND_LOCK`, `RLock`, `SerializedEventSink`, `get_event_sink` match between spec and RED. Status line says DRAFT r2. PASS.

**Step 1.5.** The `get_event_sink` branches are unchanged. AC1b exercises the wrap branch repeatedly. The already-wrapped branch is covered by AC4. PASS.

**Step 2.** op1 (module lock, re-entrant) now has AC1b and AC4b. No new section 2 path lacks an AC. PASS.

**Step 3.** Both new tests are real: they use real `get_event_sink`, with no mock of the UUT. They have no sleeps and fail at assert time. PASS.

**Step 4.** Point `event_sink.get_event_sink`, Host: production per-emit callers (`run.py`, `phase_sentinel.py`, `restart_governor.py`), Test path: `_PerEmit` goes through the same function. Reachable.

## Adversarial edges (not in the AC table)

1. Cascade after a broken GREEN: if GREEN used a module-level plain Lock, the AC4b daemon thread dies holding `_APPEND_LOCK` forever. Later serialized tests then block until their bounded joins (60 s each, non-daemon workers may stall interpreter exit). The failure is still reported correctly but slowly. Advisory.
2. `__getattr__` with `_inner` absent (copy or pickle) recurses infinitely. Advisory, GREEN should guard `_inner`.
3. An injected factory that returns a stock `EventLog` is wrapped. Not pinned. Advisory.

## Checklist

- AC1: PASS
- AC1b: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC4b: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS

## Findings

1. MINOR. Adversarial edge 1 (slow cascade if GREEN uses a non-reentrant module lock). Advisory.
2. MINOR. Adversarial edge 2 (`__getattr__` recursion guard). Advisory.
3. MINOR. About 14 s of rendezvous wait per full run (unchanged from r1). Advisory.

No MAJOR findings. No required fixes.

VERDICT: APPROVED

VERDICT: PASS

<!-- verdict-anchor
spec: docs/decisions/2026-10-11-bd108-event-sink-thread-safety.md sha256:af66ef201d882d9dea5db5e40de40c93557051f5a491d67643b3c4f125a84a0b
red: engine_py/tests/test_bd108_event_sink_thread_safety.py sha256:965d4214b08c8d1350d47dd9c0ef689eedbc5aff9835a4e8e7a2e94b5d67e366
-->
