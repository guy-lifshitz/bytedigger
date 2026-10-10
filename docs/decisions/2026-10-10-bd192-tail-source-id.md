---
red_tests:
  - engine_py/tests/test_bd192_tail_source_id.py
  - engine_py/tests/test_bd150_class_i_inventory.py
sibling_tests:
  - engine_py/tests/test_green_test_feedback_AEC7E800.py
  - engine_py/tests/test_gh542_collect_probe.py
  - engine_py/tests/test_bd_flip_red_collect_probe.py
  - engine_py/tests/test_gh602_red_preflight_delta_retry.py
  - engine_py/tests/test_gh1018_orphan_green_cycle_resolution.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_5_implement.py
  - engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md
  - engine_py/bytedigger_engine/conformance/class_i_inventory.json
  - engine_py/bytedigger_engine/flags_catalog.py
  - docs/events.md
  - CHANGELOG.md
---

# bd#192: declare the two carried test-output tails

**Status: r1, TIER3.** Follow-up to #150 (class-I inventory). The schema machinery it shares with #152 has landed (#207 per-invocation id and output digest, #206 class-M carry sites), so nothing here waits on another issue; this lot adds no new event field to `model_invocation_attested`, it only feeds the existing `injections` channel.

| | |
|---|---|
| **Tier** | TIER3. Two builders and two producers in `phase_5_implement.py`, one spec section, two inventory entries, one switch. |
| **Class** | SYSTEMATIC. Chokepoint: the class-I declaration channel (`injected_blocks` bound to the prompt, read by `_declared_injections`, verified by the dispatch chokepoint), fed from the two carry sites of subprocess output. |
| **LLM calls added** | None. Pure code on strings already in memory. |

## Problem (on `0777ae9`)

Two slices of subprocess output reach a prompt and are classified class I in the inventory but are still `I-deferred` to this issue:

- **T1** `_verify_green_passing`: each failing group's pytest/bun stdout is read back from the capture file (`_read_text_or_empty`), the tails are joined with `" "`, left-truncated to `FINDINGS_MAX_CHARS`, carried as `green_test_findings == [joined]` and rendered by `_build_green_prompt` in the `## TEST FAILURES` block. The path of the file each part came from is dropped, and the truncation step has no rule for what a part is once the cut lands inside it.
- **T2** `_red_collect_probe`: the last 400 characters of the probe's stdout plus stderr go into a violation string, then into a batch finding's `evidence`, then (delta-retry) into the `findings` string rendered by `_build_red_prompt`. The finding record has no source identifier.

## Design

### Switch

`HAL_TAIL_SOURCE_ID`, read through `get_config().gate_enabled` (unset or anything but `"0"` is ON; `BD_` and `BYTEDIGGER_` aliases through the existing lookup). Catalog entry: `kind: "gate"`, default `"1"`, module `workflows/phase_5_implement.py`, `owner: guy-lifshitz`, `provenance` starting `introduced: #192`.

Off means: producers add no new key to any record, and the producer's text output (`green_test_findings`, the finding `evidence`, the retry `findings` string) is byte-identical to the on state and to the code before this lot; builders declare nothing from tail records even if a record is present.

Default ON is the issue's stated requirement and is not an enforcement step: this lot adds attribution, it never adds a refusal. A chunk that does not occur in the final prompt is dropped with an event and never reaches the dispatch chokepoint (which would refuse it); so a failing declaration degrades to today's behaviour.

### Records

**T1.** With the switch on, each entry of `failing_group_tails` gains `source_id = str(stdout_path)` when the group has a stdout path. After the existing join and left-truncation (unchanged, same string), a pure helper computes the chunks of the **rstripped** joined string: for each entry in order, the part of its tail that survived. The data returned by `_verify_green_passing` (both the retry and the terminal result) gains `green_test_chunks: [{"source_id": str, "content": str}, ...]`. `green_test_findings` stays `[joined]`.

**T2.** With the switch on, a collect-probe batch finding that has probe output gains `source_id = "collect-probe:" + ", ".join(py_paths)` (the identifier the site already shows the model before the tail) and `source_chunk = tail`. A timeout finding (`"collect-probe timeout"`) has no output and gains neither. `_red_collect_probe`'s return shape is unchanged; the tail travels through an optional trailing parameter that existing callers do not pass. In the delta-retry result of `_verify_red_lint_rules`, `forwarded_data` gains `findings_chunks: [{"source_id", "content"}]` built from the batch findings that carry both keys. The legacy sequential collect-probe branch returns an error without `findings`, so its tail is not carried to a prompt (the engine retry carries `data["findings"]` only) and is left as is; the inventory note says so.

### Chunk rule (written into `AUTHORSHIP_SPEC.md` §4)

A part's chunk is the longest suffix of that part's text that survived the left-truncation of the joined string, taken from the rstripped joined string, so it is exactly the text of the part that reaches the prompt. Parts are joined with one space; the separator belongs to no block. Chunks are declared in prompt order, one block per part, all with that part's `source_id`. A part whose text was cut away entirely, or whose surviving text has no non-whitespace character, is not declared. A single-source tail (T2) is one chunk with the same rule.

### Consumption

`_build_green_prompt` (retry, cycle >= 2, where the `## TEST FAILURES` block is rendered) and `_build_red_prompt` (both the delta-retry branch and the full branch, where `findings` is rendered) read the chunk list from `prev`, pass it through one helper that keeps a chunk only if it is a well-formed mapping with a non-empty `str` `source_id`, a non-empty `str` `content`, and `content` occurs in the final prompt; each dropped chunk emits `tail_chunk_not_declared` `{phase: 5, step, source_id, reason}` with `reason` in `malformed` or `absent_from_prompt`. The kept chunks are appended after the blocks the builder already declares and go into the existing `_injected_blocks_record(prompt, blocks)`; in the RED delta branch that record replaces today's `None`. The engine's retry forwarding already carries non-control keys, so the two new keys reach the builders without an engine change.

### Inventory and spec text

The two entries (`_verify_green_passing::_read_text_or_empty#0`, `_red_collect_probe::bounded_run#0`) become `I-declared` with no `issue` and notes saying where each is declared. `AUTHORSHIP_SPEC.md` §4: both items move into the **Declared** list, the "Not yet declared" bullet and the "Deferred class-I segments" paragraph are removed, and the chunk rule above is added. Two limits are stated: (a) the T2 slice is the last 400 characters of stdout followed by stderr, a contiguous slice of their concatenation; (b) containment, not position, is verified, as for every other class-I block.

## Acceptance (RED, real path)

Tests drive the real functions. The only replaced boundary is `run_test_command` (a callable that writes real stdout files and returns the result object `_verify_green_passing` already consumes); the collect probe runs real `python -m pytest --co` on a real broken test file in a temp git repo; the dispatch chokepoint is exercised with a recording adapter registered through `register_backend`; events are captured with `_emit_safe`.

- **AC1 (T1 record).** Two failing groups (`py`, `bun`) with distinct stdout files, total below the cap. `green_test_chunks == [{"source_id": str(py_path), "content": the py tail}, {"source_id": str(bun_path), "content": ...}]` in group order; `green_test_findings == [" ".join(tails)]` (the test recomputes it); asserted on the retry result and on the terminal (cycle-cap) result.
- **AC2 (chunk rule).** Over the cap, with the cut inside the first tail: the first chunk is the surviving suffix, still `source_id` of the first group, and `" ".join(c["content"] for c in chunks) == green_test_findings[0].rstrip()`. Cut past the whole first tail: one chunk, the second group's. A tail of only whitespace: no chunk for it. Both orderings (cut in first, cut in last-surviving) are in the matrix.
- **AC3 (switch, T1).** `HAL_TAIL_SOURCE_ID=0`: no `green_test_chunks` key; `failing_group_tails` entries without `source_id`; `green_test_findings` equal to the value with the switch on and to `" ".join(tails)[-FINDINGS_MAX_CHARS:]` computed independently. Unset equals `"1"`. `BD_HAL...` alias `=0` is off; a set `HAL_` value wins over the alias.
- **AC4 (GREEN prompt to chokepoint).** The AC1 retry data fed to `_build_green_prompt` at cycle 2: the prompt contains `## TEST FAILURES`; `data["injected_blocks"]["blocks"]` contains the two chunks after the existing blocks, in prompt order (indexes of the contents in the prompt are increasing); `prompt_sha256` equals `hash_text(prompt)`. Dispatched through the real chokepoint with the recording adapter: called once, status ok, and the `model_invocation_attested` event's `injections` carries `{source_id, sha256 == hash_text(content)}` for each chunk.
- **AC5 (degrade, not refuse).** A chunk record whose content is absent from the prompt, and one that is malformed (`source_id` empty, `content` not a `str`): neither is declared, one `tail_chunk_not_declared` event per dropped chunk with the right `reason`, the adapter is still called once and the status is ok (no `E_INJECT_UNATTRIBUTED`). With the switch off and valid chunks present in `prev`, nothing is declared and no event is emitted.
- **AC6 (T2 record and carry).** A real RED file that fails collection (syntax error). With the switch on, the batch finding carries `source_id == "collect-probe:" + path` and `source_chunk` equal to the last 400 characters of the real combined output; the delta-retry result's `data["findings_chunks"] == [{"source_id": ..., "content": source_chunk}]` and `source_chunk in data["findings"]`. A probe timeout finding carries neither key and adds no chunk.
- **AC7 (RED prompt to chokepoint, both branches).** The AC6 retry data fed to `_build_red_prompt` at cycle 2 with `HAL_IMPL_DELTA_RETRY` on (delta branch) and off (full branch): `injected_blocks` declares the chunk, bound to the prompt hash; the chokepoint attests `{source_id, sha256}`; adapter called once. Chunk absent from the prompt (engine-truncated `findings`): dropped with the event, dispatch still ok.
- **AC8 (switch, T2).** `HAL_TAIL_SOURCE_ID=0`: no `source_id`/`source_chunk` on the finding, no `findings_chunks`, the `findings` string byte-identical to the switch-on string, and the delta-branch `injected_blocks` is `None` as before this lot.
- **AC9 (auth modes).** AC1, AC4 and AC7 are run once with `ANTHROPIC_API_KEY` set and `CLAUDE_CODE_OAUTH_TOKEN` unset, and once the other way round; chunks, blocks and attested `injections` are identical.
- **AC10 (inventory and spec).** The two keys are `I-declared` without `issue`; no entry has `issue == "192"`; `class_i_lint.check` on the real tree returns `[]`. The existing AC12 and AC14 of `test_bd150_class_i_inventory.py` are rewritten for the new state in the RED commit (T1/T2 are declared; `bd#192` appears in no "not yet declared" list; the pairing test still holds for whatever deferrals remain). `AUTHORSHIP_SPEC.md` §4 names both sites under **Declared**, contains the chunk rule, and has no "Deferred class-I segments" paragraph naming `bd#192`.
- **AC11 (registry).** `FLAGS["HAL_TAIL_SOURCE_ID"]` exists with `kind == "gate"`, default `"1"`, module `workflows/phase_5_implement.py`, non-blank `owner`, `provenance` starting `introduced:`; `python3 scripts/flag_owner_lint.py` exits 0; `docs/events.md` has a `tail_chunk_not_declared` row.
- **AC12 (stay green, unedited).** The five sibling test files above, and the rest of `test_bd150_class_i_inventory.py`.

## Not touched

`engine.py` retry forwarding; the event schema of `model_invocation_attested`; the directed-repair prompt and the lint/typecheck retry blocks; the legacy sequential collect-probe branch; class-M carries; how `FINDINGS_MAX_CHARS` is chosen.
