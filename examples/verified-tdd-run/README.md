# Verified-TDD run on a toy repo

The whole point of this engine is that generated code has to earn a PASS.
This example shows that on the smallest repo that can demonstrate it: one
unimplemented `slugify()` function, a frozen spec with an AC table, and a
loop where a dishonest test gets caught by a deterministic gate before any
implementation is written.

Runs keyless. No API key, no network, no pytest -- stdlib plus the engine.

```bash
cd engine_py && pip install -e . && cd ..
python3 examples/verified-tdd-run/run_demo.py
```

## What happens

Two runs share one append-only event log.

Attempt 1 submits a vacuous RED: a test that patches `slugify` itself and
asserts against the mock. That suite would pass with zero implementation --
the classic reward-hack. The stub-passability lint (`stub_passability`, the
same module the real pipeline runs) catches the import-and-patch pair and
the run dies with `E_RED_STUB_PASSABLE` before anything else executes.

Attempt 2 submits an honest RED against the real import. The loop then runs
to the end: the suite is executed and must FAIL pre-implementation, a
validation verdict is checked, the GREEN write is checked against the spec's
files-in-scope allowlist, and the suite must PASS post-implementation. The
final derived state comes from replaying the event log -- there is no other
state file.

Expected trace, trimmed:

```
attempt 1: the model submits a RED that mocks slugify itself
  lint_red             REJECT  RED patches its own unit under test ('slugify', ...)
  => run 'attempt-1-vacuous-red' finished: error (E_RED_STUB_PASSABLE: ...)

attempt 2: honest RED against the real import
  verify_red_fails     PASS  suite fails pre-implementation (FAILED (errors=6))
  validation_verdict   PASS  VERDICT: APPROVED ...
  write_green          ok    slugify.py written (allowlist-checked)
  verify_green_passes  PASS  (OK)

replaying the event log (derived state, no other source of truth):
  attempt-1-vacuous-red: error  [lint_spec, write_red, lint_red]
  attempt-2-honest-red: ok  [lint_spec, write_red, ..., verify_green_passes]
```

## What is real and what is scripted

Real: the engine (`WorkflowEngine`), the event log and `derive_state.replay`,
the `scope_inverse` spec lint, the `stub_passability` RED lint, and both
unittest subprocess runs. These modules decide the outcome; delete the
`E_RED_STUB_PASSABLE` branch and attempt 1 sails through.

Scripted: the LLM. `ScriptedBackend` returns canned RED/GREEN/verdict text
through `register_backend`, the same public seam a real API client plugs
into (see [../library/custom_backend.py](../library/custom_backend.py)).
The "validation verdict" here is therefore canned APPROVED text -- in the
real pipeline that verdict comes from an adversarial model call and is
itself anchor-checked. This demo is the deterministic skeleton of the loop,
honestly labeled; it is not a 13-phase production build.

## Files

- [spec.md](spec.md) -- the frozen spec: design, AC table, files-in-scope
  allowlist plus its NOT-in-scope inverse (the `scope_inverse` lint fails
  the run if the inverse block is missing).
- [run_demo.py](run_demo.py) -- the loop, one screen of orchestration over
  engine modules.
- [toyrepo/](toyrepo/) -- starting state, copied to a temp dir per attempt;
  the checkout never changes.
