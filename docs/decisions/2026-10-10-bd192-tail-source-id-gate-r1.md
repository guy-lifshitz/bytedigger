# bd#192 gate r1: declare the GREEN test-output tail and the collect-probe tail

Audited: spec `docs/decisions/2026-10-10-bd192-tail-source-id.md`; RED `engine_py/tests/test_bd192_tail_source_id.py` (31 tests, all failing today); pins `engine_py/tests/test_bd192_tail_source_id_pins.py` (12, pass today by design: switch-off T1/T2, probe timeout, switch-off declares nothing, two lint pins); `engine_py/tests/test_bd150_class_i_inventory.py` (new AC12 tail test, AC14 and AC14b rewritten, helper `_not_yet_declared_section` changed; 3 failing today, must go green after the change). UUT `workflows/phase_5_implement.py` `_red_collect_probe` (L3653), batch collect-probe (L3893-3912), `_verify_red_lint_rules` delta retry (L4466-4498), legacy branch (L4051-4066), `_verify_green_passing` (L5231, tails L5350-5352, join/truncate L5478-5483, retry L5485-5499, terminal L5501-5510), `_build_red_prompt` (L1261, delta L1294-1324, full L1441-1468), `_build_green_prompt` (L7876, TEST FAILURES L8074-8093, record L8113-8123); `phase_workflows_common.py` `_declared_injections` (L708-734), `_injected_blocks_record` (L737-744); `llm_subprocess.py` `_injection_refusal` (L1045-1091), attest payload (L1312-1331); `engine.py` `_RETRY_CONTROL_KEYS` (L194-201) and forwarding (L836-855); `config_provider.py` (L21-58); `flags_catalog.py`; `scripts/flag_owner_lint.py`; `conformance/AUTHORSHIP_SPEC.md` §4 (L477-551); `conformance/class_i_inventory.json` (L188, L208); `conformance/class_i_lint.py` (L220-229). Preflight receipt read (red file failing; pins 12/0; test_bd150 56/3; five siblings green). The combined run of 34 failures (31 in the RED file plus 3 in test_bd150) at assertion level on the missing keys, flag, events row, inventory state and spec text matches the test count. Not run by me; failure reasons below are from code reading.

## 4-step audit

### Step 1 - spec-internal consistency
- Tokens consistent across spec, RED and pins: `HAL_TAIL_SOURCE_ID`, `tail_chunk_not_declared`, `green_test_chunks`, `findings_chunks`, `source_chunk`, `source_id`, reasons `malformed` / `absent_from_prompt`, inventory keys `..._verify_green_passing::_read_text_or_empty#0` and `..._red_collect_probe::bounded_run#0`.
- Spec claims verified true against code:
  - T1: the tails are read whole with `_read_text_or_empty` (L4801-4807, `errors="replace"`), joined with `" "` (L5479), and left-truncated only when `len > FINDINGS_MAX_CHARS` (L5480-5481; `FINDINGS_MAX_CHARS = 32_000`, L369). `green_test_findings == [joined]` goes into both the retry result (L5490) and the terminal result (L5505). The stdout path is dropped (L5352).
  - T2: `combined[-400:]` (L3677) becomes the violation `"<py_paths>: <tail>"` (L3678), then the batch finding `evidence` (L3906), then `findings_str = "; ".join(...)` with the evidence quoted (L4469-4470, L4485), which is forwarded (L4495). A timeout gives `"collect-probe timeout"` (L3673).
  - Legacy branch: returns `data={**prev.data}` with no `findings` (L4062-4066), and the engine carries only `data["findings"]` (engine L736, L855). The claim holds.
  - Engine forwarding: every key that is not a control key is forwarded (engine L839-842; `green_test_chunks` and `findings_chunks` are not control keys). No engine change is needed.
  - RED delta branch: today it hard-codes `"injected_blocks": None` (L1319). `_injected_blocks_record(prompt, [])` returns `None` (common L739-740), so with no chunks kept the delta record stays `None`. This is what the AC8 pin needs.
  - Switch: `gate_enabled` is `!= "0"` (config_provider L52-54) and a set `HAL_` value wins over the aliases (L25-33). The spec's "unset or anything but 0 is ON" is correct.
- Drift (MINOR, F6): AC3 writes "`BD_HAL...` alias", which should read `BD_TAIL_SOURCE_ID`. AC1 names the groups `py`/`bun`, but the fixture paths give kinds by runner (`py`, `ts`). The Acceptance preamble says the probe runs "in a temp git repo", but `_run_probe` uses a plain temp dir, which is enough because nothing in the batch path needs git. AC6 writes `"collect-probe:" + path`, while Design says `", ".join(py_paths)` (see F5).
- Wording (MINOR, F1): the chunk rule says the chunk is "the longest suffix of that part's text that survived". That is exact for a part the cut lands inside. It is not exact for the last non-blank part, whose chunk also loses trailing whitespace through the rstrip. In the `whitespace_only_last` case the `ts` chunk is `"real tail"`, which is not a suffix of `"real tail\n"`. The RED oracle `_expected_chunks` states the rule precisely: the part's span intersected with `[cut, len(joined.rstrip()))`. The prose should say the same, and AC10 needs to keep the literal "longest suffix".
- Inconsistency (MINOR, F2): "A single-source tail (T2) is one chunk with the same rule" would apply the rstrip to T2. AC6 and the RED expect `content == source_chunk ==` the raw 400-character tail, which usually ends in `"\n"`. This is correct for T2: the findings string quotes the tail whole, `'...<tail>'`, so the raw tail does occur. The spec text and the §4 text it dictates should say that T2's chunk is the tail verbatim, with no rstrip.
- Cross-document tension (MINOR, F3): AUTHORSHIP_SPEC §4 "Binding" says "Inside a bound record, a malformed element is passed to the chokepoint, which refuses it. It is never dropped" (L543-544). It also says "`source_id` is the identifier the site already shows the model" (L533-534) and "An empty chunk is not declared" (L533). The new design drops malformed or absent tail chunks before the record is built, uses the stdout path for T1 (the GREEN prompt shows `[unknown]`, not the path, L8078-8081), and drops whitespace-only chunks. None of this conflicts in code: the filter runs upstream of `_injected_blocks_record`, so a bound record still never holds a dropped element. The §4 edit the spec dictates should still state these three points so that the normative text does not contradict the code.

### Step 1.5 - rule-overlap simulation
- **`_verify_green_passing`** returns early, in order: no RED paths (L5239), plan skipped (L5260), reproducibility (L5269-5302), timeout (L5330), baseline-delta (L5364, off in the fixture via `HAL_BASELINE_DELTA_GATE=0`), sibling delta (L5374, needs `red_sha`, which is None in the fixture), net-new-added (L5419, needs `red_sha`), id delta (L5452, baseline None with `fail_closed=False`, so no block), then retry (L5485) or cycle-cap terminal (L5501).
  - The AC1/AC2/AC3 fixtures reach the retry or terminal arm. The fake runner returns `exit_code=1, n_failed=1` on all five reproducibility calls, so the counts are reproducible.
  - Only the retry and terminal arms carry `green_test_findings`. The other recoverable arms (E_GREEN_NOT_REPRODUCIBLE, E_BASELINE_DELTA) carry neither findings nor chunks, so they render no TEST FAILURES block. Attaching chunks to the two findings arms only is complete.
- **Group order**: `_infer_test_command_for_paths` builds groups in the order each kind first appears in `red_test_paths` (L2776-2795). `failing_group_tails` follows plan order. The RED `_entries` follows `tails` order, which is the same order. The three-group matrix (`py`, `ts`, `sh`) is consistent.
- **Join and truncation arithmetic, checked by hand on the matrix** (cap 32000):
  - `cut_inside_first`: total 35001, cut 3001. The `py` chunk is `py[3001:]` and the `ts` chunk is `ts.rstrip()`. Joining them with `" "` equals `findings[0].rstrip()`.
  - `cut_past_whole_first`: total 36001, cut 4001, past the end of `py` (3000) and the separator (3000). Only `ts[1000:]` remains.
  - `cut_exactly_at_end_of_first`: total 35000, cut 3000, so `py` is empty. The truncated string starts with the separator, which belongs to no block. The test therefore correctly marks this case non-joinable.
  - `whitespace_only_middle` / `whitespace_only_last`: the blank parts drop out. In the `_last` case the rstrip eats the blank third part and the `"\n"` of the second.
  - The oracle's `end_all = len(full.rstrip())` equals the end of the rstripped truncated string whenever the truncated string is not entirely blank. That holds for every case in the matrix.
- **Is chunk content guaranteed to occur in the final prompt?**
  - GREEN: the entry is a `str`, so the renderer emits `"  [unknown]\n" + entry.rstrip()` (L8078-8081). There is no wrapping, escaping or re-truncation. Every chunk is a substring of `joined.rstrip()`, so every chunk occurs.
  - RED: `findings` is inserted verbatim by both the delta prompt (`impl_delta_retry_prompt.py` L51-53) and the full prompt (L1441-1446). The tail sits inside `'...'` unchanged, so it occurs unless something upstream rewrites `findings`. That case is AC7's absent path.
- **Builder filter, in order**: switch off (declare nothing, emit nothing); not a mapping, empty or non-`str` `source_id`, or empty or non-`str` `content` gives `malformed`; content not in prompt gives `absent_from_prompt`. A chunk that is both malformed and absent should be reported as `malformed`, because absence cannot be evaluated for non-`str` content. The spec does not state this order (see E9).
- **Would the chokepoint refuse a declared chunk?** No. `_injection_refusal` refuses only a non-`str` or empty `source_id`, or `content` that is not a `str` or not in the prompt (llm_subprocess L1062-1090). The filter is a strict superset of those checks: it also drops empty content, which the chokepoint would accept. It is evaluated against the same `d["prompt"]` that the dispatch sends: GREEN L8222 and RED L1566 pass `prev.data["prompt"]` unchanged, and refusal is computed before the prefix is hoisted (L1717). `_declared_injections` re-checks the hash binding (common L727). A kept chunk cannot be refused.
- **RED branch dispatch** (L1294): `cycle >= 2 and findings and delta_enabled` selects delta, otherwise full. The fixture pins which branch ran through `data["delta_retry"]`. Both arms are exercised (AC5, AC7, AC9).

### Step 2 - section 2 vs section 3
| Design path | AC |
|---|---|
| T1 record on retry and on cycle-cap terminal | AC1 (both), AC2 |
| T1 truncation and part rule | AC2 matrix |
| T1/T2 switch off: no new key, byte-identical text | AC3 / AC8 (RED and pins) |
| T2 finding keys, `findings_chunks` on delta retry | AC6 |
| T2 timeout carries nothing | AC6 pin |
| GREEN builder declares, chokepoint attests | AC4 |
| RED delta and full builders declare (delta record replaces `None`) | AC7 |
| Malformed or absent chunk dropped with event, dispatch ok | AC5 (GREEN, RED x2), AC7 absent |
| Switch off: builders declare nothing, emit nothing | AC5 pin x3, AC8 pin (delta `None`) |
| Inventory flip, §4 text | AC10 (+ lint pin), test_bd150 AC12/AC14/AC14b |
| Flag registry, events row | AC11 (+ lint pin) |
| Legacy branch untouched | no AC needed (not touched; inventory note only) |

- Every new branch has an AC, and every AC post-condition has a producing path. No terminal, cleanup or finalize branch is introduced; the cycle-cap terminal only carries the extra key.
- Unpinned §2 text (MINOR, F8): the two stated limits, (a) the T2 slice is a contiguous slice and (b) containment is checked rather than position, have no AC10 text check. `CHANGELOG.md` is in `paths:` with no AC, which is acceptable for a changelog.

### Step 3 - RED adequacy
- **Collect vs assert time**: the module imports only existing symbols (`p5.FINDINGS_MAX_CHARS`, `p5.GREEN_TEST_CYCLE_CAP`, `_declared_injections`, `hash_text`, `TestRunResult`). Every test reaches its assertions, and each fixture precondition (`E_GREEN_NOT_PASSING`/recoverable, `E_RED_COLLECT_PROBE` delta retry, one collect-probe finding, an ok builder with a TEST FAILURES block, the requested branch ran) holds on today's code.
- **Failure reasons today** (from code):
  - AC1/AC2/AC3-on/AC4/AC9 fail on the missing `green_test_chunks`, after `green_test_findings` already matched.
  - AC5 GREEN/RED fail on `injected_blocks` lacking the chunks: today the GREEN record holds F2/F3 only and the RED delta record is `None`.
  - AC6/AC7/AC8-on fail on the missing `source_id`/`source_chunk`/`findings_chunks`.
  - AC10 fails on the inventory class and the §4 text. AC11 fails on the missing flag and events row.
  - These are the right reasons.
- **No stub-passability**:
  - Replaced boundaries: `run_test_command` (the process boundary; it writes real files), and `bounded_run`, which a spy wraps around the real probe. The spy hands back the recorded process for replay or a timed-out process.
  - Also replaced: `_emit_safe` (capture only), and `shutil.which("semgrep") -> None`. `p5.shutil` is the global `shutil` module, so this patches `shutil.which` process-wide for the test's duration. It delegates for every other name and is restored by monkeypatch, so the effect is harmless.
  - Real: the producers, builders, `_declared_injections`, `invoke_llm_subprocess` (with a recording adapter through `register_backend`), and `class_i_lint`. No UUT is mocked.
- **Vacuous-pass risk**: low.
  - AC2 compares against an independent oracle and adds structural asserts (source ids per case, partial first chunk, joinability).
  - `_assert_tail_declared` checks the hash binding, that the chunks close the record, that no tail source id appears earlier, and that chunks are in strict prompt order.
  - AC4/AC5/AC7 go through the real refusal logic and check the attested `{source_id, sha256}`.
  - A GREEN cannot pass these by writing keys without rendering-consistent content.
- **Pins**: the switch-off, timeout and lint pins are correctly separated, and each fails if the change regresses the off state. The delta-`None` pin (AC8) protects the "replaces `None` only when something is kept" contract.
- **AC9 auth-mode test validity** (MINOR, F7): it is valid as a regression shield and fails today. Nothing on the exercised path reads the auth variables, though: only `lib/llm_cost.py` L162-164 and the API reference backend do, and the recording adapter replaces the backend. The test therefore cannot detect an auth-dependent divergence unless one is later introduced into the builders or `_declared_injections`. Accept as specified.
- **test_bd150 edits** (MINOR, F4): beyond the AC12 and AC14 rewrites the spec authorises, the RED commit adds an assertion to AC14b and changes the shared helper `_not_yet_declared_section` to return `""` when the heading is absent.
  - The helper change is required. The spec removes the "Not yet declared" bullet, and `text.index` would then raise.
  - It does not weaken AC14. With the section gone, `listed == set()` forces `deferred == set()`, so no `I-deferred` entry may remain, which is strict. Today only the two bd#192 entries are `I-deferred` (inventory L188, L208).
  - It makes the first assert of AC14b vacuous once the section is gone, which is semantically correct.
  - Spec AC10 should name AC14b and the helper, and spec AC12's "the rest ... unedited" should exclude them.

### Step 4 - reachability (section 1y)
| AC | Point (prod) | Host | Test path |
|---|---|---|---|
| AC1/AC2/AC3 | L5352 (source_id), L5478-5483 + new chunk helper, L5486-5499 / L5501-5510 returns | `_verify_green_passing` | `_run_green` calls the real function with real stdout files |
| AC4/AC5-green | L8074-8093 block, L8113-8123 record | `_build_green_prompt` | `_build_green` (dict prev, cycle 2), then `_dispatch` through `invoke_llm_subprocess` |
| AC6/AC8 | L3677-3678 tail, L3903-3908 finding, L4485-4495 forwarded_data | `_red_collect_probe` / `_verify_red_lint_rules` | `_run_probe`: real `pytest --co` on a real broken file |
| AC7/AC5-red | L1294-1324 (delta record, today L1319 `None`), L1441-1468 (full) | `_build_red_prompt` | `_build_red` per branch, then `_dispatch` |
| AC10 | inventory L188/L208, §4 L477-551 | data / doc | `load_inventory`, `class_i_lint.check`, text reads |
| AC11 | `FLAGS`, `docs/events.md` | data / doc | import, lint subprocess, regex row |

All ACs are reachable on the real path. The engine hop (verify step to builder) is not driven end to end. It is covered by reading the code: engine L839-855 forwards both new keys.

## Adversarial edges (not covered by an AC)
- E1, exact cap boundary: `len(joined) == FINDINGS_MAX_CHARS` (no cut; the oracle gives `cut = 0`) and `== cap + 1` (the cut removes exactly the first character of part 1). The matrix covers interior cuts and a cut at a part's end, not these two off-by-one points. Cheap to add as two rows.
- E2, a surviving remnant that is blank: a non-blank first part whose surviving suffix is only its trailing `"\n"`. The rule says it is not declared. The matrix covers only whole blank parts.
- E3, a group with no `stdout_path` (stub runner, or capture disabled): the tail is `""`, so the entry gets no `source_id` and there is no chunk. The spec states this ("when the group has a stdout path"), but nothing pins it. A GREEN emitting `{"source_id": "None", ...}` for a non-empty tail with a `None` path cannot happen today, because the tail is empty when there is no path. Worth a one-line pin.
- E4, T2 decoy (multi-file): with two RED `.py` files, spec Design gives `source_id = "collect-probe:" + "a.py, b.py"`, while `finding["path"]` is `resolved_paths[0]`, which may not even be a `.py` file. AC6 uses one file, so `"collect-probe:" + finding["path"]` cannot tell a correct GREEN from one that uses `finding["path"]`. See F5.
- E5, T2 with non-zero exit and empty output: `tail == ""`, so by "has probe output" the finding gets neither key and no chunk is produced. Unpinned.
- E6, RED StepResult prev shape (LoopRunner, L1283-1286): `findings` is read from `_prev.data`, and the chunks must be read from the same shape. Every RED test passes a dict.
- E7, GREEN chunks present but no TEST FAILURES block rendered (cycle 1 via a StepResult prev, or an empty `green_test_findings`): every chunk would emit `absent_from_prompt`. The spec should say whether the filter runs only when the block is rendered, which is quieter and matches "where the block is rendered", or always.
- E8, identical tails in two groups: two blocks with equal content and different source ids. Containment cannot tell which bytes belong to whom. This is limit (b), so it is informational. The RED helper's strict distinct-position assertion would reject it, but no fixture hits it.
- E9, precedence: a chunk that is both malformed and absent should be reported once as `malformed`. The order is unstated.

## Findings
1. F1 (MINOR): Chunk-rule prose. Change it to "the longest suffix of the part that survived the left-truncation, ending where the rstripped joined string ends", or equivalent span-intersection wording, and keep the literal "longest suffix" that AC10 reads. The RED oracle is already right.
2. F2 (MINOR): T2 chunk. State that the T2 chunk is `source_chunk` verbatim, the raw last 400 characters with no rstrip, because the findings string quotes it whole. Replace "with the same rule" accordingly, in the spec and in the §4 text.
3. F3 (MINOR): §4 reconciliation. The §4 edit must also (a) say the tail filter runs before the record is built, so a bound record still never drops an element (L543-544 stays true); (b) name the T1 `source_id` as the capture-file path, an exception to "the identifier the site already shows the model" (L533-534); and (c) say a whitespace-only tail chunk is not declared (L533 says "empty").
4. F4 (MINOR): Spec AC10 and AC12 wording. List AC14b and `_not_yet_declared_section` as edited in the RED commit, with the reason (the section is removed). The edits themselves are correct and not weakening.
5. F5 (MINOR): Add a two-file T2 case (one broken and one valid `.py`, or both broken) asserting `source_id == "collect-probe:" + ", ".join(py_paths)`, and fix AC6's "`+ path`" wording. Without it a GREEN using `finding["path"]` passes.
6. F6 (MINOR): Cosmetic drift. Fix "`BD_HAL...`" to `BD_TAIL_SOURCE_ID`, "`py`, `bun`" to the actual kinds, and "temp git repo" to a temp dir.
7. F7 (MINOR): AC9 exercises no auth-dependent branch. Keep it as a shield, and note in the spec that declarations are auth-independent by construction.
8. F8 (MINOR): Optionally pin limits (a) and (b) in the §4 text, and add the E1/E2/E3/E5 rows to the matrix and pins. State the E7 and E9 behaviour in one sentence each.

No MAJOR finding. Every concern raised in the review request checks out:
- The join, separator and rstrip arithmetic is right.
- The rendering preserves chunk bytes: rstrip only, no wrapping.
- The RED delta `None` is preserved when nothing is kept.
- The chokepoint cannot refuse a kept chunk.
- The vacuous-pass risk is low.

## Checklist
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
- AC11: PASS
- AC12: PASS

VERDICT: APPROVED
(The requested literal was `VERDICT: APPROVE`; the checked literal is `APPROVED`. Same decision. The MINOR findings are advisory; F1-F3 and F5 are cheap and worth folding into the spec before GREEN.)

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd192-tail-source-id.md sha256:805525e7bc44a8186ccb0ca23b7526d7ce7fb02810313705c7c5526732bd1b84
red: engine_py/tests/test_bd192_tail_source_id.py sha256:1a76b86f4c748acd902b756e452a46a0b54f2060dc9668f9905a84fd14375053
-->
