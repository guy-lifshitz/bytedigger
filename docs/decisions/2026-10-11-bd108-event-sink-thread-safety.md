---
red_tests: [engine_py/tests/test_bd108_event_sink_thread_safety.py]
sibling_tests: [engine_py/tests/test_event_sink.py, engine_py/tests/test_bd93_run_scoped_event_log.py, engine_py/tests/test_gh795_resolver_emit_seam.py, engine_py/tests/test_9AB32375_config_provider.py]
paths: [engine_py/bytedigger_engine/event_sink.py, engine_py/bytedigger_engine/config_provider.py, engine_py/bytedigger_engine/flags_catalog.py, docs/configuration.md, CHANGELOG.md]
---
# bd#108: the EventSink seam serializes appends and states the thread-safety requirement

**Status: DRAFT r2 (gate r1: REJECT, MAJOR fixed by AC1b, minor AC4b)** · **Tier:** 2 (engine `.py`: spec, RED, gate, GREEN) · **Class:** BUG/contract (follow-up of #82 part 2, #97)
**Base:** `origin/main` `8237283`.
**Chokepoint:** `get_event_sink` in `engine_py/bytedigger_engine/event_sink.py`, the single resolver every construction site already calls (`run.py`, `lib/phase_sentinel.py`, `lib/restart_governor.py`). The switch is read in one place, `config_provider.event_sink_serialize_enabled()`.

## 1. Problem

Since #97 three pool threads emit into one run's event sink at once. The default `EventLog` survives that (one `O_APPEND` `write()` per event, `event_log.py:276-278`). An injected sink (`set_default_event_sink_factory`) gets no promise either way: a buffered list, a sequence counter or a sqlite connection can interleave, corrupt or raise, and `_emit_safe` swallows the error, so events vanish silently. The `EventSink` docstring says nothing about threads.

## 2. Decision: serialize at the seam, not in the sinks

A sink author should not have to know the engine runs a pool. `get_event_sink` returns, for an injected factory, a thin wrapper that takes one lock around every `append`. The stock `EventLog` is already safe, so it is returned **unwrapped** (zero added cost, same object as today). The contract states both halves: the seam serializes appends for any sink it hands out; a sink obtained any other way (constructed directly) must be thread-safe itself.

Refused: a lock inside `_emit_safe`/`engine._emit` (several emit paths, `llm_subprocess` has its own; the seam is the one place all of them share); making `_emit_safe` louder (separate lot, does not fix interleaving).

## 3. Design

- **op1, the wrapper.** `class SerializedEventSink` in `event_sink.py`: holds `_inner`; `append(event_type, payload, run_id=None)` does `with _APPEND_LOCK: return self._inner.append(...)`. `_APPEND_LOCK` is one module-level `threading.RLock` (a per-wrapper lock would not protect two wrappers over one object that a singleton factory hands out; contention is negligible, appends are short). Any other attribute is delegated to `_inner` through `__getattr__` (`path`, `read_all`, ...). `isinstance(w, EventSink)` holds. An exception from the inner `append` propagates unchanged and releases the lock.
- **op2, the seam.** `get_event_sink(path)`: `sink = _DEFAULT_FACTORY(path)`. Return `sink` unchanged when `_DEFAULT_FACTORY is _ORIGINAL_FACTORY` (the stock EventLog), when it is already a `SerializedEventSink`, or when the switch is off. Otherwise return `SerializedEventSink(sink)`.
- **op3, the contract.** The `EventSink` docstring gains a "Thread safety" paragraph: `append` may be called concurrently from several threads of one run; the seam (`get_event_sink`) serializes calls to a sink it returns when the factory is injected; a sink used outside the seam must be safe itself; the stock `EventLog` is safe by one `O_APPEND` write per event. The module docstring and `set_default_event_sink_factory` docstring point at it. No provider-specific wording (provider-agnostic).
- **op4, the switch.** `config_provider`: provider method `event_sink_serialize_enabled()` (default provider: `self.gate_enabled("HAL_EVENT_SINK_SERIALIZE")`, on unless exactly `0`; `BD_`/`BYTEDIGGER_` aliases) plus a free function of the same name tolerating a provider without the method (neutral default: on). With `HAL_EVENT_SINK_SERIALIZE=0`, `get_event_sink` returns exactly what the factory returned (`is` the same object): today's direct emit.
- **op5, catalog and docs.** `flags_catalog.FLAGS["HAL_EVENT_SINK_SERIALIZE"]`: kind `gate`, default `"1"`, module `event_sink.py`, `owner: "guy-lifshitz"`, `provenance: "introduced: bd#108 - an injected sink was called from several pool threads with no thread-safety promise - serialization on by default, =0 restores the direct emit"`, description with `retire-by:2026-11-10`. One row in `docs/configuration.md`, one bullet in the `### Added` block of the EXISTING CHANGELOG `[Unreleased]` (no second block).

Out of scope: `_emit_safe` error visibility, `EventLog` itself, sqlite sinks, prompts, any provider-specific code.

## 4. Acceptance criteria. RED file: `engine_py/tests/test_bd108_event_sink_thread_safety.py`

Determinism: no sleeps in assertions, no time thresholds. The race is provoked by a **rendezvous inside the unsafe sink**: the first caller to enter `append` sets `entered` and waits on `second_arrived` (bounded at 2 s only so an unserialized-free path cannot hang; the bound is a provocation window, never asserted; AC2's `max_inside >= 2` relies on the second thread arriving inside it, accepted residual: it can only fail on a runner stalled for 2 s); the next caller to enter sets `second_arrived`. Without a lock the second caller arrives while the first is inside, so overlap is certain; with the lock the second cannot enter, the wait times out once and the run continues. Threads are released together through a `threading.Barrier`. All tests restore the factory (autouse reset).

- **AC1** (RED). Unsafe sink: `append` reads a counter, hits the rendezvous, writes counter+1, then appends `{seq, event_type, payload}` to a list. 3 threads x 20 events through `set_default_event_sink_factory` + `get_event_sink()`. Assert: the sink never saw two threads inside `append` (`max_inside == 1`), `count == 60`, the recorded events are 60, each `(thread, i)` payload exactly once and equal to what was sent, `seq` values are `0..59` with no gap or duplicate.
- **AC1b** (RED, gate r1 MAJOR). The production pattern: every emit calls `get_event_sink()` itself and gets a fresh wrapper over the one unsafe sink the factory returns (a singleton). 3 threads x 20 events, each emit `get_event_sink().append(...)`. Same assertions as AC1 (`max_inside == 1`, `count == 60`, `seq` `0..59`). A per-wrapper lock fails this; the module-level `_APPEND_LOCK` passes.
- **AC2** (RED). Switch: with `HAL_EVENT_SINK_SERIALIZE=0` (and `BD_` alias) `get_event_sink()` returns the very object the factory built (`is`), and the same driver observes `max_inside >= 2` (the old direct behaviour). Other values (`1`, ``, `off`) keep serialization on (`max_inside == 1`).
- **AC3** (RED, real side effect). A file-backed unsafe sink writes each event to a real `events.jsonl` under `tmp_path` in two `write()` calls (a header half, the rendezvous, the rest). 3 threads x 20 events through the seam. Assert: the file has exactly 60 lines, every line parses as JSON, each `(thread, i)` once. Also, stock path: `get_event_sink(path)` with no override from 3 pool threads (`ThreadPoolExecutor`) x 20 events: `EventLog(path).read_all()` returns 60 events, and `type(sink) is EventLog` (not wrapped, so no added cost; structural, not a timing check).
- **AC4** (wrapper). `isinstance(wrapped, EventSink)`; `append` returns the inner's return value; `wrapped.<other attr>` is the inner's; an exception raised by the inner `append` reaches the caller and a following `append` from another thread still works (lock released); a sink already wrapped is not wrapped twice.
- **AC4b** (re-entrancy, gate r1 minor). Wrapper A over an inner whose `append` calls wrapper B's `append` (B over a second inner) on the same thread completes (bounded `join`, no deadlock): the lock is re-entrant.
- **AC5** (contract). `EventSink.__doc__` contains `thread` and states that the seam serializes; `set_default_event_sink_factory.__doc__` mentions thread safety.
- **AC6** (switch plumbing). `config_provider.event_sink_serialize_enabled` is `True` unset and `False` for `0`; a provider lacking the method gives `True` from the free function; `flags_catalog.FLAGS["HAL_EVENT_SINK_SERIALIZE"]` has kind `gate`, a non-blank `owner`, `provenance` starting `introduced:`, a `retire-by:` date within 45 days of 2026-10-11; `scripts/flag_owner_lint.py` and `scripts/flip_horizon.py --check --today 2026-10-11` exit 0; `docs/configuration.md` mentions the variable; CHANGELOG mentions it in `Added` (helper `helpers.changelog.require_entry`), and the file has exactly one `[Unreleased]` block.
- **AC7** (siblings). The sibling suites listed above stay green (run, not a test here).
