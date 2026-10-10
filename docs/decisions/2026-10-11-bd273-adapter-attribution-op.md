---
red_tests:
  - engine_py/tests/test_bd273_adapter_attribution_op.py
sibling_tests:
  - engine_py/tests/test_bd273_adapter_attribution_op_pins.py
  - engine_py/tests/test_bd271_adapter_attestation_op.py
  - engine_py/tests/test_bd271_adapter_attestation_op_pins.py
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op.py
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op_pins.py
  - engine_py/tests/test_bd267_adapter_failure_handling_ops.py
  - engine_py/tests/test_bd267_adapter_failure_handling_ops_pins.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint_pins.py
  - engine_py/tests/test_bd195_harness_adapter_seam.py
  - engine_py/tests/test_bd58_adversary_harness.py
  - engine_py/tests/test_bd63_r35_enforcement_falsifiable.py
  - engine_py/tests/test_bd10_l3_authorship.py
  - engine_py/tests/test_bd94_engine_owned_paths.py
  - engine_py/tests/test_bd212_changelog_helper.py
red_pins:
  - engine_py/tests/test_bd273_adapter_attribution_op_pins.py
paths:
  - engine_py/bytedigger_engine/conformance/harness.py
  - CHANGELOG.md
---

# bd#273 (group 5): a host attribution operation for ADV-8, in shadow

**Status: r1, TIER2.** Fifth slice of #197 (plan in that issue; g1 = ADV-3 merged in #264, g2 = ADV-4/ADV-5 in #268, g3 = ADV-6 in #270, g4 = ADV-7/ADV-10 in #272). Under an adapter, ADV-8 (a block injected into a prompt must be attributable) is `not_executed` (`run_adversaries`, adapter branch, `harness.py` l.711), so a host has no way to show that a block it injects can be traced back to a source and to its content. This slice adds **one** optional adapter operation, one registry row and one probe, on the dispatcher g1 introduced, in the same shape. The shadow-to-counted flip is g6.

| | |
|---|---|
| **Tier** | TIER2: one module, one new test file (plus a pins file), a CHANGELOG line. |
| **Class** | SYSTEMATIC: one adversary the harness cannot put to the host, so the host's attribution of injected content is never measured. Chokepoint: `conformance/harness.py`, the only place adversaries run under an adapter (`run_adversaries`, adapter branch, via `_ADAPTER_OPS` and `_optional_op`). |
| **LLM calls added** | None. The probe is three calls to one adapter method with fixed string arguments. The verdict is a string comparison against a SHA-256 the harness computes itself. Nothing is judged by a model. |

## Principles (Guy's, for bd)

1. **Deterministic first.** The digest is one fixed function (below), recomputed by the harness. No model, no fuzzy match.
2. **Provider-agnostic: a path degrades, it does not fail.** The harness names no model, vendor or provider (the #195 scan still holds). A host that cannot answer (backend down, no credential, hang) gets `shadow:indeterminate`, never a crash.
3. **Works on a subscription and on API tokens.** The probe does not depend on how the host pays for a call; the host assembles whatever prompt it assembles.
4. **Shadow first, enforce later.** The outcome is `shadow:<outcome>` and counts toward no level. The flip is g6.

## Problem

`run_adversaries(adapter=...)` records ADV-8 as `not_executed`. A host that injects a block with no source id, or records a digest that is not the digest of the content it injected, cannot be told apart from one that is correct. The engine's own probe (`_adv_8`) builds an `InjectedBlock` and checks `attest.assemble` and `attest.hash_text` over hand-made values, which says nothing about the host.

## Adapter interface (levels §6 and §9.1, amended again)

One more optional operation, looked up by name:

`attribute_block(content) -> Mapping`

- `content` is a non-empty `str` chosen by the harness. The host assembles a prompt with **one** injected block carrying exactly that content, and reports the block's attribution. How the host builds the rest of the prompt is its own business.
- It returns a mapping, the host's attribution of the block as raw facts:

| key | value |
|---|---|
| `"source_id"` | the identifier the host gave the block (a non-empty `str` when the host attributes it) |
| `"digest"` | `str`: the host's digest of the block's content, in the form fixed below |

Extra keys are ignored. The names are the harness's own; they map onto the engine's `InjectedBlock.source_id` and the digest `attest.hash_text` produces, and the host does the mapping.

**The host does not say whether the block is attributed; it reports the facts, and the harness judges.** A host defends by reporting truthfully what it recorded: a host that gives the block no source id, or records a digest of something other than the content, shows that to the harness and is `undefended`.

Rules 1 to 6 of g1 apply unchanged (omitted or `None` is `not_executed`; guarded call; control first; shadow; `adapter_ops`; `adapter=None` untouched). "Malformed answer" for this operation is defined below. No new switch: `adapter_ops="off"` / `--adapter-ops off` records ADV-8 as `not_executed` and never calls the method, which restores the BD-L1 ceiling.

## The digest (fixed here, one function)

The digest of a content is `attest.hash_text(content)`, the engine's own form: the text `"sha256:"` followed by the 64 lowercase hex digits of the SHA-256 of `content.encode("utf-8")`. The harness already imports `attest`; no new dependency and no new function. The prefix is part of the value, so a bare hex string, an upper-case hex string, or a digest with any other prefix does not match. Comparison is exact string equality: no stripping, no case folding, no normalisation of the content. The known vector `hash_text("abc") == "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"` is pinned.

## Probe

Three calls, each with a fresh argument. The three contents are fixed strings in the harness: two control contents that differ from each other, and a probe content that differs from both and contains a non-ASCII character and a line break (so the encoding and the exact bytes are part of what is judged). A full run calls the method three times; `only=("ADV-8",)` does the same.

1. **Control: two calls**, `attribute_block(control_a)` then `attribute_block(control_b)`. Each answer must be well formed: a `Mapping` whose `"digest"` key is present and exactly a `str` (a `str` subclass is not exact). A raise, a timeout or a malformed answer on either call is `indeterminate`; the later calls are not made (a failure on the first answer means one call, on the second two). The two digests must differ; **equal digests are `indeterminate`** (the issue fixes this): a host that reports the same digest for different contents has shown nothing about the probe, which a constant would pass or fail by luck. The control judges the digests only: a control answer without a source id, or with a digest that is not the digest of the content, does not stop the run, because the probe is where those are judged.
2. **Probe: one call**, `attribute_block(probe_content)`. A raise, a timeout, or a malformed answer (as above: not a `Mapping`, or `"digest"` missing or not exactly a `str`) is `indeterminate`. Otherwise:
   - **defended** iff the answer carries a source id (key present, value exactly a `str`, and non-empty after `str.strip()`) **and** its digest equals `attest.hash_text(probe_content)`;
   - **undefended** otherwise: a missing, `None`, empty, blank, non-`str` or `str`-subclass source id, or a digest that is not the digest of the content (a digest of other text, of the content plus a newline, of a control content, a bare hex string, an upper-case hex string, an empty string).

The extractor (`_attribution_of(answer)`) reduces an answer to `(has_source_id: bool, digest: str)` or `None` when malformed. It runs inside the guard's worker (the `extract` argument of `_guarded_value_call`), so a hostile mapping cannot hang the main thread past `timeout_s`.

The outcome is then prefixed `shadow:`. The issue's "shadow:pass" and "shadow:fail" are the harness's existing `defended` and `undefended`.

## Shape of the change (`conformance/harness.py`)

- `_attribution_of(answer)` (the extractor above) and `_probe_attribution(method, timeout_s)` (the table above), each call through `_guarded_value_call(method, content, timeout_s, extract)`. The three contents are module-level constants.
- One row added to `_ADAPTER_OPS`: `"ADV-8": ("attribute_block", _probe_attribution)`. `_optional_op`, `run_adversaries`, the CLI and `__all__` are unchanged. `ADV-8` is already in `ADVERSARIES` and in `_LEVEL_OF` (BD-L3); its engine probe `_adv_8` still runs when `adapter=None`.
- The module docstring gains the ADV-8 row, and its rule 2 ("malformed answer") and rule 3 ("control") name this operation's shapes, so they stay true.

## Acceptance criteria

RED file `engine_py/tests/test_bd273_adapter_attribution_op.py` (every test fails before GREEN); pins file `..._pins.py` (passes before and after). `conformance.*` imports go inside test bodies (bd#24). Stub adapters live in the test module and subclass nothing from the harness; they compute digests with `hashlib` themselves, not with the harness. No test reads the wall clock or the date (a monotonic timer bounds the timeout tests).

- **AC1 (the operation is called, not the engine probe).** An adapter that reports a source id and a digest that is the digest of the content plus a trailing newline (different per content, so the control passes) gives ADV-8 `shadow:undefended`, though the engine probe says `defended`.
- **AC2 (conforming).** A host that reports a source id and the exact digest of the content it was handed gives `shadow:defended`. The method is called exactly three times, with three `str` arguments that are non-empty and pairwise distinct; the third is not pure ASCII and holds a line break. `only=("ADV-8",)` gives the same three calls. Extra keys, a `Mapping` that is not a `dict`, and a source id with inner spaces change nothing.
- **AC3 (wrong implementations fail).** After three calls, `shadow:undefended` for: a missing source id key, `None`, `""`, `"   "`, a non-`str` (`7`, `b"x"`, a list), a `str` subclass; a digest that is the digest of different text, of the content plus `"\n"`, of a control content (stale), a bare hex string, an upper-case hex string, an empty string. A host that hashes the content with a lossy encoding (`ascii`, errors ignored) is undefended through the non-ASCII probe content.
- **AC4 (control first).** A host that reports the same digest for every content gives `shadow:indeterminate` after exactly two calls, and so does one that reports an empty digest for every content. A control answered with a wrong-but-different digest proceeds to the probe call and ends `undefended`; a control answered without a source id proceeds and ends `defended` if the probe answer is right (the control judges digests only).
- **AC5 (degrade, don't fail).** `shadow:indeterminate`, and `run_adversaries` returns normally, for: the method raises on the first, the second or the third call; returns `None`, `True`, a string, `[]`, `{}`, a mapping missing `"digest"`, a `digest` that is not a `str` (`7`, `b"x"`, `None`, a list) or is a `str` subclass, on each of the three calls (the earlier calls good); sleeps 5 s against `timeout_s=0.2` (the run returns in under 2.5 s, every live non-main thread is a daemon, and a hang on the first call means exactly one call); the attribute is a non-callable value; a host whose session probe raises `ConnectionError`; a host whose missing credential raises a custom `Exception` subclass; a mapping whose reads block past the timeout. `KeyboardInterrupt` and `SystemExit` from the method propagate (`pytest.raises`). A missing digest together with a missing source id is `indeterminate`, not `undefended`.
- **AC6 (omitted stays `not_executed`; pin).** An adapter with only `freeze`/`evaluate`, and one with `attribute_block = None`, gives ADV-8 `not_executed` with zero calls to the method, and the full run still returns exactly the nine `ADVERSARIES` keys. The operations are independent: an adapter with only `attribute_block` gives ADV-8 a `shadow:` outcome and ADV-3 to ADV-7 and ADV-10 `not_executed`; an adapter with the other five operations but not this one gives ADV-8 `not_executed`.
- **AC7 (shadow is not counted).** A conforming adapter has ADV-1 = ADV-2 = `defended` and ADV-8 = `shadow:defended`. `build_attestation` with `level_claimed="BD-L3"`, ADV-8 taken from that run and every other adversary `defended`, gives `level_achieved == "BD-L2"`, and `validate_attestation` returns a complaint naming `level_claimed`.
- **AC8 (kill switch; pin).** With `adapter_ops="off"` a conforming adapter gets zero calls and ADV-8 = `not_executed`; ADV-1/ADV-2 equal the `"shadow"` run. An unknown value still raises `ValueError`. `adapter=None` with `adapter_ops="off"` equals the all-`defended` mapping.
- **AC9 (digest form; pin).** `attest.hash_text("abc")` is the known vector above, and for a non-ASCII text with a line break equals `"sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()`. The harness source contains no provider word and no model or vendor name (the #195 scan).
- **AC10 (no interference; pin).** For an adapter that has all six operations, ADV-1 to ADV-7 and ADV-10 equal the outcomes of the same adapter without `attribute_block`, for a digest adapter and for an accept-all adapter.
- **AC11 (CLI end to end, subprocess).** A conforming adapter with the operation, `--level-claimed BD-L1`: exit 0, JSON `adversaries["ADV-8"] == "shadow:defended"`, `level_achieved == "BD-L1"`. The same with `--adapter-ops off`: exit 0 and `not_executed`.
- **AC12 (pinned, must still pass).** `__all__` is unchanged; `run_adversaries()` and `run_adversaries(adapter=None)` are byte-identical to today; the g1 to g4 behaviour is unchanged (their test files stay green untouched). The probe function behind the ADV-8 row reads no clock and no file: its source contains none of `datetime`, `date.today`, `time.time`, `time.monotonic`, `open(`, `tempfile`.

## Not in scope

- ADV-11 and the shadow-to-counted flip (g6).
- Any change to `oracle.py`, `bd_l2`, `bd_l3`, `attest`, `tokens`, `llm_provider`, the CLI or `__all__`. The engine's own `_adv_8` and `attest.assemble` / `InjectedBlock` / `hash_text` are untouched; `_adv_8` still runs when `adapter=None`.
- Checking that the block really sits in the prompt the host sends, or that the source id is a registered source. The harness judges only that the host reports a source id and a digest that is the digest of the content; that limit is the one every adapter operation has, and counting the result is g6's decision.
- The frozen levels document kept outside this repository: this file and the module docstring carry the amended interface text.

## Files and checks

Touched: `harness.py`, `CHANGELOG.md`, the two new test files, this spec and its gate report. The probe writes no file and lists no directory, so the `bd#94` tree-scan lint (which skips `conformance/`) has nothing to see; `tree_scan_inventory.json` stays as is (`test_bd94_engine_owned_paths.py` in the sibling run proves it). The change adds no `E_...` code, so `error_codes.py` and both `ERROR_CODES.md` copies stay untouched; preflight runs the error-code drift check (bd#243 lineage) over the diff to confirm. The harness source must not gain a provider word (`anthropic`, `claude`, `openai`, `subscription`, `api-token`, `api_key`), including in comments and docstrings, nor a model or vendor name.

## Sibling audit (§1a)

- g1, g2, g3, g4 files (`test_bd197_*`, `test_bd267_*`, `test_bd269_*`, `test_bd271_*`, each with `_pins`): the dispatcher, `_optional_op` and `_guarded_value_call` are shared; the change adds one row and one probe and edits no shared function, so every AC of theirs is re-run unmodified. Their stubs have no `attribute_block`, so the places that assert ADV-8 is `not_executed` (`test_bd271_adapter_attestation_op.py` l.660, its pins l.173 and l.179) stay true; they are the tests that would break if the dispatcher gave ADV-8 an outcome without the method.
- `test_bd195_harness_adapter_seam.py`: `_NON_ORACLE` (l.214) lists ADV-8 as `not_executed` for an adapter without operations; still true. Its no-adapter attestation JSON (l.343, l.354) is unchanged.
- `test_bd58_adversary_harness.py` (surface equals `__all__`) and `test_bd63_r35_enforcement_falsifiable.py` (calls `run_adversaries()`): no-adapter path untouched.
- `test_bd10_l3_authorship.py` (R3.2 and ADV-8 at `attest`, the engine's `_adv_8`): `attest` is not changed.
- `test_bd212_changelog_helper.py`: the CHANGELOG line must satisfy the entry lint (bold lead, one bullet, in `[Unreleased]`).
- Found by `grep -rl "run_adversaries\|conformance.harness\|ADV-8" engine_py/tests`.
