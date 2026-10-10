# bd#90 gate r1: review citation fallback (spec + RED audit)
<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd90-review-citation-fallback.md sha256:a150b4cf408b3ecc846668d3bf578e533795c3752a62476415451cbf0f0cee6a
-->

Audited: spec `docs/decisions/2026-10-10-bd90-review-citation-fallback.md` (DRAFT r1), RED `engine_py/tests/test_bd90_review_citation_fallback.py` (16 test ids, 0 pass / 16 fail per lot-preflight, assert-time per the orchestrator). Base `f7e7aa7`. Product code read: `engine_py/bytedigger_engine/workflows/phase_6_review.py` (`_QUOTE_LINE_RE` :1297, constants :1301/:1302/:1307, `_verify_finding_quote` :1424-1572, `_aggregate_review_findings` :1575-1951, `_review_all_findings_suspect` :1957, `_render_fix_doc` :1975, `_persist_fix_feed` :2050-2104), plus the downstream steps `lib/plugins/anti_hallucination/helper.py` (`verify_findings`) and `semantic_verifier.py`, `lib/plugins/review_schema/canonical.py` (bd#84), `flags_catalog.py`, `scripts/flag_owner_lint.py`, `tests/helpers/changelog.py`. Read-only. No tests run.

## Step 1: spec-internal consistency (literal-token drift)

- `_resolve_unquoted_citation`, `verified-resolved`, `OK-RESOLVED-BY-READ`, `review_citation_resolved`, `HAL_REVIEW_CITATION_FALLBACK`, `_CITATION_FALLBACK_MAX_CANDIDATES` are spelled the same in op1-op5, AC1-AC15 and the RED.
- Code claims in §1 checked against the tree and accurate: `_QUOTE_LINE_RE` shape (:1297), threshold 0.4 with a strict `>` (:1720), min-N 3 (:1302/:1756), `_QUOTE_FILE_MAX_BYTES` 4 MiB (:1307), the all-or-nothing withhold in `_persist_fix_feed` (`None if suspect_withhold else suspect`, :2080), the phase 7 line "review findings were withheld as suspect" (`phase_7_synthesize.py:357`), verdict from verified only (:1787-1812), the prompt sentence "WILL BE DROPPED" (:668). No flag controls this today (no `HAL_REVIEW_CITATION*` in the engine).
- Small drift (MINOR, finding 7): AC5 says "rate 1.0 >= threshold" while the code is `>`; AC13 names `test_GH1591*` but the file is `test_gh1591_fix_gate_boundary.py` (lowercase; a case-sensitive glob on Linux matches nothing); AC10 does not state the `phase` value that the RED pins (`phase_6_review`); AC11 says "with no backend registered" while the RED registers a spy backend.
- Internal contradiction (MAJOR, finding 3): op1 says the resolver resolves "exactly as `_verify_finding_quote` resolves a relative or absolute cite ... containment under the root", and §5 edge 3 says a candidate "must not resolve to a file outside the root". `_verify_finding_quote` applies containment to relative cites only (:1497-1510); an absolute cite is used as-is (:1475-1476, then straight to `is_file()` at :1514). The two sentences give different behaviour for an absolute token naming an existing file outside both roots.
- Internal contradiction (MAJOR, finding 1): op3 says `suspect_withhold` is False whenever one `suspect-no-quote` remains, and in the same bullet that `suspect-no-match`/`suspect-file-not-found` "keeps the current rate rule". See Step 1.5.

## Step 1.5: rule-overlap simulation against the real code

- Wiring placement. The verify loop (:1680-1689) runs before every consumer: the per-finding emit (:1695-1704), `match_kinds` totals and rate (:1710-1748), `suspect_withhold` (:1755-1757), dedup (:1762-1772), partition (:1783-1784), severity counts and verdict (:1787-1812), and the composite render. Replacing `verify_status`/`verify_reason` inside the loop, before the `match_kinds` increment, therefore changes all of them, as op2 claims. The `composite_finding_quote_verified` emit reads the finding dict after the loop, so it reports the final status (AC10). `quote_line_present` stays False for resolved findings, which is truthful.
- Unresolved no-quote with `suspect_withhold=False`. `_persist_fix_feed` passes `suspect` to `_render_fix_doc`, which writes the `## Suspect Findings (LOW CONFIDENCE)` section with every suspect block (:1995-2005). So op3 does make AC4/AC5 findings reach the fix doc.
- Mixed population (not in §3). 3 findings with `> src/mod.py:12: not the line` (`suspect-no-match`) plus 1 finding with no path at all. Pre-dedup: suspect 4, verified 0, rate 1.0, N 4, so the rate rule says withhold. op3: one `suspect-no-quote` remains, so `suspect_withhold=False`. `_persist_fix_feed` is all-or-nothing, so all four suspects, including the three fabricated-quote findings, go to the fix worker; no `fix_feed_suspect_withheld`, no withheld marker in the composite. The content-mismatch rule is not kept; it is disabled by any single prose finding, which is a common reviewer output. AC8's fixture avoids this case (its fourth finding resolves). The spec also does not say whether "still `suspect-no-quote`" is counted before or after dedup: a no-quote duplicate that dedup drops in favour of a verified copy (:1770-1771) still disables withholding for the remaining no-match findings under a pre-dedup reading (finding 1).
- Verdict impact of promoted HIGH findings. A resolved HIGH counts in `counts["HIGH"]` and makes the verdict FAIL (AC1 asserts this). The spec accepts prose promotion in §5 edge 1 because "the later semantic verifier and the fix worker still read the file". This does not hold. A promoted finding has, by construction, no `> path:line:` evidence line. The next step `verify_findings` (`helper.py:346-366`) tags every finding without an evidence line `[UNCITED]`. `verify_findings_semantic` treats `[UNCITED]` as already low-trust (`semantic_verifier.py:283`) and skips the agent call (`:503-506`). So promoted findings decide the verdict and drive a fix cycle with no downstream read-based check. Only the fix worker reads them (finding 2).
- bd#84 header canonicalization. `canonicalize_severity_headers` rewrites a non-canonical header only when its block carries a `> path:line:` evidence line (`canonical.py:141-144`, docstring :166-167). A finding with a bold header and only a `**File:** a.py:10` citation is not canonicalized, parses as zero findings, and goes down the bd#84 `review_zero_findings_suspect` / `VERDICT_SUSPECT` path. bd#90 cannot reach it. That is not silent, and it is outside this lot, but the §1 "typical misses" list overlaps (finding 8).
- Dedup rule. A verified copy replaces a quote-suspect duplicate (:1770). `verified-resolved` starts with `verified`, so a finding written twice (once with a fabricated quote, `suspect-no-match`, once as a bare path restatement) is kept as verified and counted in the verdict (finding 6).
- `_review_all_findings_suspect`. It reads only verdict, the suspect header and the bd#84 markers. Fewer reviews end as all-suspect `SUSPECT`; the precondition logic is unchanged. AC5 (all unresolved) still yields `VERDICT_SUSPECT` plus a suspect section, so the 4B9DF7D3 override precondition holds as today. No conflict.
- Kill switch. `get_config().gate_enabled` is "enabled unless exactly `0`" (`config_provider.py:52-54`), so default ON matches the catalog default `"1"`. With `=0` and the resolver not called, the loop and line :1755 are byte-for-byte today's code, so AC9's claim is achievable.

## Step 2: §2 vs §3 cross-check

| §2 path | AC |
|---|---|
| op1 resolve `**File:** path:line` | AC1, AC10, AC11 |
| op1 range `path:a-b` | AC2 |
| op1 path without line | AC3 |
| op1 line out of range (terminal: no resolve) | AC4 |
| op1 missing file / no path (terminal: `None`) | AC5, AC10 (`ev-c`) |
| op1 relative escape | AC6 |
| op1 size cap | AC15 |
| op1 absolute path outside root | none (finding 3) |
| op1 `fallback_dir` resolution, base-first precedence | none (finding 4) |
| op1 first-of-several candidates, cap 5, extension rule | none (finding 4) |
| op2 status replace + counted as verified (rate, partition, verdict) | AC1, AC2, AC8 (rate 0.75) |
| op2 matching quote untouched | AC7 |
| op2 event | AC7, AC10 |
| op3 withhold False with unresolved no-quote | AC4, AC5 |
| op3 rate rule kept for content mismatch | AC8 (only without a remaining no-quote; mixed case unpinned, finding 1) |
| op4 switch off | AC9 |
| op4 catalog entry + owner lint | AC12 |
| op5 CHANGELOG | AC14 |
| guard | AC13 |

Every terminal path of the resolver that the spec names has an AC except the absolute-path and fallback branches. The op3 terminal decision has an AC for the pure cases, not for the mixed case, where the spec's two sentences disagree. That gap blocks (finding 1).

## Step 3: RED adequacy

- Collection: imports only existing modules (`llm_subprocess.register_backend`, `reset_backends`, `emit_resolver_resolved` exist at `llm_subprocess.py:2792/2853/66-68`; `helpers.changelog` exists). The only new symbol touched by name, `_resolve_unquoted_citation`, is reached after `assert hasattr(...)` (AC9). The 16/16 failures are assert-time, which matches the preflight.
- No self-mocking. The unit under test (`_aggregate_review_findings`, `_persist_fix_feed`) runs for real on real files. Only `_emit_safe` (event capture, same seam as `test_gh1591_fix_gate_boundary`) and, in AC9 only, the new resolver are replaced; AC9 replaces it to prove it is not called.
- Fixture soundness. `_resolve_worktree_root` prefers `org_config["current_worktree_path"]` (`lib/worktree_root.py:13-16`), so `src/mod.py` resolves under `wt`. The last finding block keeps the `<!-- role-findings-count -->` line (the VERDICT strip at :1189 needs end-of-block); it carries no file-extension token, so it adds no candidates. A range line `> src/mod.py:10-14: ...` does not match `_QUOTE_LINE_RE` (the `(\d+):` group cannot cross `-`), so AC2 really exercises the resolver. In AC6, `wt/../outside.py` and `scratch/../outside.py` both exist and both escape their roots, so only containment rejects it.
- Discrimination against plausible wrong implementations:
  - never withholds at all: fails AC8 and AC9;
  - promotes any block regardless of file existence or line: fails AC4, AC5, AC6, AC10, AC15;
  - scans only `>` lines: fails AC1, AC3, AC10, AC11;
  - requires a line number: fails AC3;
  - drops the size cap: fails AC15; drops containment for relative cites: fails AC6;
  - ignores the switch: fails AC9; promotes but leaves `suspect_withhold` from the rate rule: fails AC5;
  - forgets the event or reports the pre-resolution status: fails AC10 and AC7.
- Not discriminated (MINOR, finding 4): a resolver that ignores `fallback_dir`, that checks only the first candidate, that has no candidate cap, or that accepts extension-less tokens passes every test. AC11's spy is on the `claude-subprocess` name only; a call routed to `anthropic-api`, `pydantic-anthropic` or `agent-sdk` would not be seen (finding 5).
- AC14 uses `helpers.changelog.require_entry(..., block="Fixed")` and has no `Unreleased` + `.index(` line, so the bd#212 lint stays green.

## Step 4: reachability

- Resolver call and status replace: Point = new branch inside the loop at :1684-1689. Host = `_aggregate_review_findings`. Test path = `world.run` → `_aggregate_review_findings` (AC1-AC8, AC10, AC11, AC15).
- `review_citation_resolved` emit: same Point and Host, through the module `_emit_safe` that the fixture captures (AC7, AC10).
- `suspect_withhold` change: Point = :1755-1757. Host = `_aggregate_review_findings`. Test path = `result.data["suspect_withhold"]` (AC4, AC5, AC8, AC9) and through `world.fix_doc` → `_persist_fix_feed` :2079-2090 (AC1, AC4, AC5, AC9).
- Flag: `flags_catalog.FLAGS` (AC12), lint via subprocess with cwd = repo root. With `owner`/`provenance` present, `is_rollout` is True (`flag_owner_lint.py:87`), and `_entry_violations` needs a non-blank owner and `introduced:` plus text, which op4 supplies.
- CHANGELOG: `[Unreleased]` already has `### Fixed` at CHANGELOG.md:102, so op5 adds a bullet there.

## Product principles

- Deterministic first: yes. The resolver is a local stat and read through the shared cache.
- Provider-agnostic degrade with no model call: yes by design. Nothing in the aggregator imports a backend. AC11's check is weak (finding 5).
- Subscription and API-token modes: covered by argument (no model call) and AC11 parametrises `ANTHROPIC_API_KEY` set/unset; the spy covers only the subscription backend name.
- Kill switch: present (`HAL_REVIEW_CITATION_FALLBACK`, gate, default `"1"`, AC9 and AC12).

## Scope

The spec paths are `phase_6_review.py`, `flags_catalog.py`, `CHANGELOG.md`. Nothing touches post-fix test scope (#249) or the fix-and-recheck loop (#250). The reviewer prompt text is explicitly left alone, and the RED does not assert on any prompt. Clean.

## Adversarial edges (not covered by §3)

1. **Mixed suspect population:** content-mismatch suspects plus one unresolvable no-quote suspect. op3 un-withholds all of them (finding 1). The pre-dedup vs post-dedup reading is also open.
2. **Absolute token outside both roots** (for example a prose mention of an installed library's `.py` file). Whether it resolves depends on which of op1's two sentences GREEN follows (finding 3).
3. **Downstream bypass:** promoted findings are tagged `[UNCITED]` by `verify_findings` and skipped by the semantic verifier, yet they set the verdict (finding 2).
4. **Dedup laundering:** a fabricated-quote finding plus a bare-path restatement of the same severity and title is kept as verified (finding 6).
5. **Fallback-only file:** a cited artifact that exists only under the scratchpad (for example `reviews/x.md` or `spec.md`) should resolve via `fallback_dir`. Untested, and so is base-first precedence when both exist.
6. **Second candidate wins:** first token absent, second present; and a block with more than 5 tokens where the only real file is the sixth (cap boundary, off by one).
7. **Non-canonical header with non-canonical citation:** stays invisible through bd#84 and never reaches the resolver (finding 8).
8. **Env alias leak:** `_aliased_env_get` honours `BD_`/`BYTEDIGGER_REVIEW_CITATION_FALLBACK`. The autouse fixture deletes only the `HAL_` name, so a shell exporting an alias set to `0` flips AC1-AC8, AC10, AC11 and AC15.

## Checklist

- AC1: FAIL
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: FAIL
- AC6: FAIL
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS

## Findings

1. **MAJOR: op3 contradicts itself and disables GH1591 withholding for mixed populations (AC5).** As written, one unresolved `suspect-no-quote` sets `suspect_withhold=False`. Because `_persist_fix_feed` (:2080) withholds all suspects or none, every `suspect-no-match` and `suspect-file-not-found` finding is then fed too. This contradicts the bullet's own "keeps the current rate rule" and AC8's parenthetical. Pick one behaviour and pin it:
   - (a) Keep the rate rule for content suspects: withhold `suspect-no-match`/`suspect-file-not-found` when the rate rule fires, and still feed unresolved no-quote findings. This needs a partitioned feed, for example a `suspect_withhold` that applies only to non-no-quote suspects, specified in op3 together with the withheld count and notice wording.
   - (b) Accept that any unresolved no-quote un-withholds everything. Delete the "keeps the current rate rule" claim and the AC8 parenthetical, and state the risk.
   - Either way, add an AC with 3 `suspect-no-match` plus 1 no-path finding that asserts the chosen `suspect_withhold` and fix-doc content, and state whether the no-quote count is taken before or after dedup.
2. **MAJOR: §5 edge 1 rests on a false claim about downstream checks (verdict impact, AC1).** Promoted findings have no evidence line, so `verify_findings` tags them `[UNCITED]` (`helper.py:361-366`) and `verify_findings_semantic` skips them (`semantic_verifier.py:283`, `:503-506`). A promoted HIGH sets the verdict to FAIL and starts a fix cycle with no read-based check other than the fix worker. Correct the text and decide:
   - (a) Accept this explicitly. Add a guard AC or a note that resolved findings are `[UNCITED]` downstream and skip semantic verification.
   - (b) Count resolved findings as verified for the rate and the feed but not for severity counts and verdict.
   - (c) Make the downstream steps treat resolved findings as cited. That is outside the current `paths:` and needs a scope change.
3. **MAJOR: absolute-path containment is ambiguous (AC6).** op1's "exactly as `_verify_finding_quote`" gives no containment for absolute tokens (:1475-1476). op1's "containment under the root" and §5 edge 3 say there is. State the rule: for example, an absolute token resolves only when it lies under `target_root` or the scratchpad. Add an AC: an absolute path to an existing file outside both roots stays `suspect-no-quote`, and an absolute path inside the worktree resolves.
4. **MINOR: untested op1 clauses.** `fallback_dir` resolution and base-first precedence, first-resolving-of-several candidates, the 5-candidate cap, the file-extension rule, and URL or version tokens (edge 3). A GREEN that skips any of these passes the RED. Add small cases, or drop the clauses that are not pinned.
5. **MINOR: AC11 is weak.** The spy sits on `claude-subprocess` only, and the RED registers a backend while the spec says "with no backend registered". Spy the dispatch entry point (`llm_subprocess.invoke_llm_subprocess`), or register the spy under every reference backend name, and align the AC text.
6. **MINOR: dedup laundering.** A resolved copy replaces a `suspect-no-match` duplicate (:1770). Accept it in §5 or pin it.
7. **MINOR: literal drift.** AC5 says `>=` where the code has `>`. AC13 says `test_GH1591*` where the file is `test_gh1591_fix_gate_boundary.py`. AC10 does not state the `phase` value. AC11 says "no backend registered".
8. **MINOR (advisory): bd#84 overlap.** A bold-header finding with only a `**File:**` citation is not canonicalized (no evidence line), so the resolver never sees it. Note it under out of scope.
9. **MINOR (advisory for GREEN):** add the CHANGELOG bullet to the existing `### Fixed` block at CHANGELOG.md:102 (the bd#212 duplicate-block lint). Add no `SHARED/` string to the module. Consider deleting the `BD_`/`BYTEDIGGER_` aliases in the autouse fixture (edge 8).

Since findings 1-3 change the spec text and add ACs, the spec revision changes and needs a new sha in the anchor, and the RED needs the added cases.

VERDICT: REJECTED
