# bd#195 — host-adapter seam for `run_adversaries` (levels §9 step 1)

Frozen levels spec: `2026-07-26_bytedigger_conformance_levels.md` (HAL), §4, §6, §8, §9 step 1.
Builds on bd#58 (`conformance/harness.py`) and bd#38 (`oracle.Oracle`, `evaluate_guarded`).
Unblocks hal-v2#2226.

## Problem

`harness.run_adversaries(only=...)` takes no adapter. ADV-1..ADV-10 feed synthetic inputs to bd's
own primitives, so a run attests the **engine** and never a host. `oracle.Oracle` and
`evaluate_guarded` exist, but nothing in the harness calls them for an adapter the caller supplies.
`adapter_identity` is a free string that the host asserts and nothing measures. As a result HAL
publishes `adapter_identity: "HAL host NOT measured"`.

Class: an attestation field that the subject asserts about itself. Chokepoint: `harness.py`. It is
the only place where adversaries run and the only place that builds attestations.

## Scope decision: only ADV-1 and ADV-2 run through an adapter

The adapter interface (levels §6, §9.1) has two operations: `freeze(paths, *, root) -> str` and
`evaluate(state) -> OracleOutcome`. Only the L1 adversaries are a freeze-then-evaluate question:
ADV-1 rewrites a member and ADV-2 adds one. ADV-3..ADV-10 concern lint, gates, the known-reds
ledger, the model seam and capabilities, and none of them goes through an oracle's `evaluate`.
There are two other ways to "run" them through the adapter, and both are rejected:

- Pass a synthetic scenario dict to `evaluate`. The adapter would then be re-implementing bd's
  checkers.
- Run bd's own probes and file the results under the adapter. That is the very defect §9 forbids
  ("publishing a level we have not measured").

So when an adapter is given, ADV-3..ADV-10 are recorded as `not_executed`. That is honest under §8,
and it caps an adapter attestation at BD-L1 until the interface grows operations for them (follow-up
issue, same pattern as ADV-11 in §8).

## Change (only `harness.py`, plus the new test, a CHANGELOG line and `CONTRACTS_SPEC` §1.5 surface text)

1. **New outcome token** `OUTCOME_INDETERMINATE = "indeterminate"`, exported in `__all__`. The level
   grant is unchanged: only `defended` counts, so `indeterminate` sinks a level the same way
   `errored` does.
2. **Signature** `run_adversaries(only=None, *, adapter=None, timeout_s=30.0)`.
   `adapter=None` keeps today's behaviour exactly: the same probes and the same outcomes, so
   `build_attestation(...)` produces byte-identical JSON for the same inputs.
3. **Adapter path.** `adapter` is not None. For each requested name:
   - **ADV-1 / ADV-2.** These run the adapter protocol below. Engine probes are NOT used.
   - **Other names in `ADVERSARIES`.** These are recorded as `tokens.ADVERSARY_NOT_EXECUTED`. The
     adapter is not called for them.
   - **Unknown names, and ADV-9.** These are recorded as `not_executed`, as they are today, with zero adapter calls.
4. **Adapter protocol for ADV-1 / ADV-2.** The harness owns a fresh temporary directory `root`
   containing `root/specs/a.md`. `members(root)` is the non-recursive listing of regular files
   under `root/specs`, as absolute `Path`s sorted by name, taken with `iterdir`.
   - (a) `token = adapter.freeze(members(root), root=root)`. If the call raises, times out, or does
     not return a non-empty `str`, the outcome is **indeterminate**.
   - (b) **Control.** `evaluate_guarded(adapter, state, timeout_s=timeout_s)` with
     `state = AdapterState(root=root, frozen=token, members=tuple(members(root)))`, evaluated before
     any mutation. A frozen dataclass is used, exported only through the harness. The control must
     be `ACCEPTED`:
     - `INDETERMINATE` gives **indeterminate**.
     - `REJECTED` gives **undefended**. An adapter that rejects everything is not defending
       anything; this keeps the seam from being decorative in both directions.
   - (c) **Mutation.**
     - ADV-1 rewrites `specs/a.md` with different bytes.
     - ADV-2 adds `specs/b.md`.
   - (d) **Attack.** `evaluate_guarded` on a new state with the same `token` and the post-mutation
     `members(root)`. The attack verdict decides the outcome:
     - `REJECTED` gives **defended**.
     - `ACCEPTED` gives **undefended**.
     - `INDETERMINATE` gives **indeterminate**.
   - **Guard on `freeze`.** `freeze` runs under the same daemon-thread guard semantics as
     `evaluate_guarded`, with the same timeout: a daemon worker thread that is abandoned, never joined, when the timeout expires. The run therefore returns after about `timeout_s`, even against a `freeze` or `evaluate` that never returns. An `Exception` gives indeterminate.
     `KeyboardInterrupt` and `SystemExit` are re-raised. `oracle.py` is NOT edited.
   - **No crash.** Any other `Exception` raised in the harness's own adapter path gives
     indeterminate. Under an adapter, a failure is never `errored` and never `defended`, and it
     never crashes the run.
5. **Identity.** `adapter_identity(adapter) -> dict[str, str]` (public) reads `adapter.identity`.
   That attribute must be a `Mapping` (anything else, including `None`, a `str` or a list, raises `ValueError`) whose `backend` and `source` are non-empty `str`s (anything else raises `ValueError`). The
   function returns exactly `{"backend": ..., "source": ...}` (AC-E2b shape), and any other key is
   dropped. A missing or malformed identity raises `ValueError`. The harness knows no provider
   names: the values are copied, never interpreted.
6. **CLI.** `python -m bytedigger_engine.conformance.harness --adapter <module:factory>`
   `--level-claimed L --engine-version V --host-identity H [--timestamp T] [--timeout-s S] [--out PATH]`. `--timeout-s` is optional and defaults to `30.0`.
   - **Loading.** The harness imports `module`, takes the attribute `factory`, and calls it with no
     arguments. It then runs `run_adversaries(adapter=..., timeout_s=S)` and builds the attestation
     with the keyword `adapter_identity=adapter_identity(adapter)`. There is no flag that sets the identity, and an unknown flag such as `--adapter-identity` is a usage error (exit 2).
   - **Output.** The attestation goes to `--out`, or to stdout when `--out` is not given, as one
     JSON object (`sort_keys=True`).
   - **Timestamp.** It defaults to the current UTC time in ISO-8601 with a `Z` suffix.
   - **Exit codes.**
     - `0`: the attestation was written and `validate_attestation` returned `()`.
     - `1`: the attestation was written, but there are complaints. They go to stderr.
     - `2`: nothing was written. This covers bad usage, a malformed `module:factory`, an import or
       attribute error, a factory that raised, and a missing or malformed identity. Provider
       unavailability is NOT a factory error: a conforming adapter builds lazily and fails inside
       `freeze`/`evaluate`, which gives indeterminate.
   - **Import discipline.** The CLI lives in private `_main(argv)`, behind
     `if __name__ == "__main__"`. Module import stays I/O-free, and `importlib` is used inside
     `_main` only.

## Acceptance criteria (test file `engine_py/tests/test_bd195_harness_adapter_seam.py`)

Imports of `conformance.*` go inside test bodies (bd#24). Stub adapters live in the test module.
The CLI tests use a factory in a temporary module on `sys.path`, or `tests/` itself; they never put
anything inside `bytedigger_engine/` on the path (bd#182).

- **AC1 (seam is real).** An adapter that ACCEPTS everything after freeze gives ADV-1 =
  `undefended` and ADV-2 = `undefended`, even though the engine probes would say `defended`.
- **AC2 (conforming adapter).** A digest adapter backed by `oracle.compute_digest` over the given
  members gives ADV-1 = ADV-2 = `defended`. It must also be called: `freeze` at least once and
  `evaluate` at least twice per adversary.
- **AC3 (reject-all is not a defence).** An adapter that rejects everything gives `undefended`
  for both.
- **AC4 (degrade, don't fail).** Each of the following gives `indeterminate` for ADV-1 and ADV-2,
  and `run_adversaries` returns normally:
  - `evaluate` raises
  - `freeze` raises
  - `freeze` returns `""` or a non-`str`
  - `evaluate` returns `True`
  - `evaluate` sleeps past `timeout_s=0.2`
  - `freeze` sleeps past it
  - the backend is unavailable
  The unavailable backend comes in two shapes: a `subscription-session` adapter whose session
  probe raises `ConnectionError`, and an `api-token` adapter whose missing token raises a custom
  `ProviderUnavailable(Exception)`.
  The stub sleep is 5 s against `timeout_s=0.2`, and `run_adversaries` must return in under 2.5 s by
  `time.monotonic`. While a sleeping `freeze` is still asleep, every live non-main thread is a
  daemon.
- **AC4b (interrupts are not ours).** `KeyboardInterrupt` and `SystemExit` raised from `freeze`,
  and the same two raised from `evaluate`, propagate out of `run_adversaries`. That is four cases,
  each checked with `pytest.raises`.
- **AC5 (indeterminate sinks the level).** With `indeterminate` on ADV-1 and everything else
  `defended`, `level_achieved` is `BD-L0`, and a `BD-L1` claim draws a complaint.
- **AC6 (non-oracle adversaries are not borrowed).** When an adapter is given, ADV-3..ADV-8 and
  ADV-10 are `not_executed`, and the adapter is never called for them (call log). This holds for
  `only=("ADV-3",)` and `only=("ADV-9",)` too, and in both cases the adapter receives zero calls. A
  full run under an adapter returns exactly the nine `ADVERSARIES` keys.
- **AC7 (adapter=None unchanged).** Two checks:
  - `run_adversaries()` and `run_adversaries(adapter=None)` both equal the literal "all nine
    `defended`" mapping.
  - `json.dumps(build_attestation(run_adversaries(), <fixed inputs>), sort_keys=True)` equals a
    hard-coded expected string.
- **AC8 (identity from the adapter).** `adapter_identity` returns exactly `{backend, source}` for a
  stub of each shape (`subscription-session`/`env`, `api-token`/`kwarg`), and an extra key is
  dropped. It raises `ValueError` in each of these cases:
  - the attribute is missing
  - `backend` is empty
  - `source` is not a `str`
  - `identity` is `None`, a `str` or a list
  - `backend` is `None`
  A conforming, stub-backed adapter of EACH shape gives ADV-1 = ADV-2 = `defended` (the issue asks
  for both backend shapes).
- **AC9 (CLI end-to-end).** These run in a subprocess, with `cwd` and `PYTHONPATH` set to
  `engine_py/` plus a temporary directory that holds the factory module.
  - Conforming adapter, `--level-claimed BD-L1`: exit 0. The JSON has
    `adapter_identity == {"backend": ..., "source": ...}` from the adapter, `level_achieved ==
    "BD-L1"`, and ADV-3 = `not_executed`.
  - The same adapter with `--level-claimed BD-L2`: exit 1, the JSON is still written, and stderr
    contains `level_claimed`.
  - Accept-all adapter, `--level-claimed BD-L1`: exit 1.
  - Unavailable-backend adapter, `--level-claimed BD-L0`: exit 0, with ADV-1 = `indeterminate`.
  - Exit 2, with nothing on stdout or in `--out`, in each of these cases: `--adapter nocolon`,
    an unimportable module, a missing attribute, a factory that raises, and an adapter with no
    identity, an adapter whose identity is a `str`, and an extra
    `--adapter-identity x` flag.
- **AC10 (surface).** `__all__` gains exactly the following, the bd58 AC10 surface test still
  passes, and `_main` is private:
  - `OUTCOME_INDETERMINATE`
  - `AdapterState`
  - `adapter_identity`
- **AC11 (provider-agnostic).** The source of `harness.py` contains none of these strings
  (case-insensitive): `anthropic`, `claude`, `openai`, `subscription`, `api-token`, `api_key`.

## Not in scope (§1v)

- Changes to `oracle.py`, `bd_l2`, `bd_l3`, `attest` or `tokens`.
- Adapter operations for ADV-3..ADV-10 (follow-up: bd#197).
- The HAL adapter itself (hal-v2#2226).
- Cutting the release tag (`Released under a tag`). That is a post-merge release step, and the PR
  states it.

## Sibling audit (§1a)

`test_bd58_adversary_harness.py`, `test_bd63_r35_enforcement_falsifiable.py` (calls
`run_adversaries()`), `test_bd27_oracle.py` (`evaluate_guarded`), `test_bd22_contracts.py` /
`test_contracts.py` (surface/import-purity), `test_bd182_*` (sys.path fence for the CLI test).
