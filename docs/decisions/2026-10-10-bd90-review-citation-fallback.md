---
red_tests:
  - engine_py/tests/test_bd90_review_citation_fallback.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_6_review.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - CHANGELOG.md
---

# bd#90: review aggregation resolves an unparseable citation by reading the file instead of withholding the finding

**Status: DRAFT r3** (r1 gate: REJECTED, 3 MAJOR + 6 MINOR; r2 gate: REJECTED, 1 MAJOR + 7 MINOR; all addressed below) · **Tier:** 2 (engine `.py`, Option D: spec, RED, Opus gate, GREEN) · **Class:** SYSTEMATIC (review findings are filtered by the shape of their citation)
**Base:** `origin/main` `f7e7aa7`.
**Chokepoint:** the review-aggregation step `_aggregate_review_findings` in `engine_py/bytedigger_engine/workflows/phase_6_review.py`: it is the only place that assigns `verify_status` to a finding and the only place that computes `suspect_withhold`. The fix-feed renderer `_persist_fix_feed` is its single consumer of that decision.

Scope: citation-format filtering only. The sibling problems are #249 (test scope after a review fix) and #250 (bounded fix-and-recheck); this lot does not touch them or the code they own.

## 1. Problem (measured on `f7e7aa7`)

- `_verify_finding_quote` finds the citation with `_QUOTE_LINE_RE` (`^>\s+path:line:text$`). Any finding without a line of exactly that shape gets `verify_status="suspect-no-quote"`. Typical misses: a range (`> a.py:10-14: ...`), a citation in a bullet or a bold `**File:** a.py:10` line, a path with no line.
- `_aggregate_review_findings` rates `suspect / (verified + suspect)` over all findings. When the rate is above `_REVIEWER_SUSPECT_RATE_THRESHOLD` (0.4, strict `>`) and at least `_REVIEWER_SUSPECT_WITHHOLD_MIN_N` (3) findings were rated, `suspect_withhold` is True.
- With `suspect_withhold`, `_persist_fix_feed` renders the fix doc without any suspect block (all or none) and emits `fix_feed_suspect_withheld`. The format miss of a real finding is therefore enough to keep it from the fix step.
- Verdict counts come from verified findings only, so a real HIGH with a non-matching citation shape also does not fail the review.
- Downstream, `verify_findings` tags a finding with no evidence line `[UNCITED]` (`lib/plugins/anti_hallucination/helper.py:361-366`) and the semantic verifier skips `[UNCITED]` findings (`semantic_verifier.py:283`, `:503-506`).
- No flag controls this.

## 2. Design

- **op1, resolver.** New module-private `_resolve_unquoted_citation(block, file_cache, base_dir, fallback_dir) -> tuple[str, str, str, int] | None` returning `(verify_status, reason, path_text, line)`. It scans the finding block for at most `_CITATION_FALLBACK_MAX_CANDIDATES` (5) candidates, in order of appearance: a **relative** path token with a file extension followed by `:<line>` or `:<line>-<line>` (a range uses its first line); the token is delimited by whitespace, backticks, quotes, parentheses, brackets, `*` or `,`, and a trailing `.`/`:` is stripped, so `` `src/mod.py:12` `` and `(src/mod.py:12)` are candidates. A token with no line, an absolute token, a URL (contains `://`), and a bare version string (digits and dots only) are not candidates. A candidate resolves when the file exists under `base_dir` (checked first) or else under `fallback_dir`, stays inside that root after symlink resolution, is a regular file, is not larger than `_QUOTE_FILE_MAX_BYTES`, and the line is within the file and not blank (reads go through the shared `file_cache`). The first candidate that resolves returns `("verified-resolved", "OK-RESOLVED-BY-READ", path, line)`; none returns `None`. No LLM or backend call, no network: the resolution is a read of the local file.
- **op2, wiring and evidence line.** In the verify loop, when `_verify_finding_quote` returns `suspect-no-quote` and the switch (op4) is on, the loop calls the resolver. On a result it sets `verify_status`/`verify_reason`, and appends to `f["block"]` one line `> <path>:<line>: <that line of the file, verbatim>`, so the block now carries a real evidence line (downstream `verify_findings` keeps it cited and the semantic verifier checks it; the fix doc shows it). The finding then counts as verified in the rate, the partition, the severity counts and the verdict. It emits `review_citation_resolved` with `phase` (`phase_6_review`), `role`, `severity`, `title`, `reason`, `path`, `line`. `composite_finding_quote_verified` reports the final status and `quote_line_present` of the final block.
- **op3, unresolved no-quote findings are fed and not rated.** With the switch on, a finding still `suspect-no-quote` after op2 is excluded from the suspect rate (neither verified nor suspect: its citation cannot be judged) and from `suspect_withhold`; `_persist_fix_feed` always renders it, in the LOW CONFIDENCE suspect section, even when `suspect_withhold` is True. Every other suspect (`suspect-no-match`, `suspect-file-not-found`) is rated by the same formula and, when `suspect_withhold` is True, withheld all together; what changes is only the population being rated (unresolved no-quote findings leave it, so a mixed population can fall below the threshold or below min-N and then feed its mismatching suspects too, which is intended). Every count of withheld findings means the withheld ones only (rated suspects after dedup): the `fix_feed_suspect_withheld.withheld_count`, the `## Suspect Findings Withheld` notice, and the composite review marker line `SUSPECT FINDINGS WITHHELD FROM FIX WORKER: ... withheld=<n>` (`phase_6_review.py:1923-1926`), which must equal `withheld_count`. `match_kinds`, the `reviewer_suspect_rate` event and the `reviewer_grep_accuracy_warning` `finding_ids` cover rated findings only (switch on); the `composite_finding_quote_verified` event still covers every finding. `_aggregate_review_findings` passes its switch state to `_persist_fix_feed` through `suspect_withhold` and the finding dicts' `verify_status` (the writer does not read the flag itself); with the switch off no finding is kept back on account of its status. When a suspect block has no `verify_status` (doc-derived path of `_persist_fix_feed`) it is treated as before.
- **op4, kill switch.** New gate flag `HAL_REVIEW_CITATION_FALLBACK`, kind `gate`, default `"1"`, module `workflows/phase_6_review.py`, read with `get_config().gate_enabled("HAL_REVIEW_CITATION_FALLBACK")` at call time. `=0` restores the previous behaviour exactly: no resolver call, no block rewrite, no event, unresolved no-quote findings rated and withheld by the rate rule. Catalog entry carries `description`, `owner: "guy-lifshitz"` and `provenance: "introduced: bd#90 - ..."`.
- **op5, docs.** One bullet in the existing `### Fixed` block of CHANGELOG `[Unreleased]` (no second `### Fixed` block). No `SHARED/` string in the module. The reviewer prompt text is not changed.

Provider and billing mode: the change adds no model call and reads no provider setting, so it behaves the same on a subscription and on API tokens, and a provider failure cannot reach it. AC11 pins that no LLM entry point is invoked, with the API key set and unset.

Out of scope: the prompt sentence that says unverifiable findings "WILL BE DROPPED" (prompt change); `suspect-no-match` and `suspect-file-not-found` handling; the withhold threshold and minimum N; bd#84 header canonicalization (a bold-header finding with only a `**File:**` citation is not recanonicalized and is seen by the resolver only if its header already parses); the #249 and #250 problems; `docs/configuration.md`.

## 3. Acceptance criteria. RED file: `engine_py/tests/test_bd90_review_citation_fallback.py`

Fixtures: a real scratchpad with `reviews/role-composite.md` and a worktree root on `tmp_path` holding `src/mod.py` (30 lines), driven through `_aggregate_review_findings` with the existing context/`prev` helpers used by `test_phase_6_*` aggregation tests. Events are captured by patching `_emit_safe`. The autouse fixture removes the flag and its `BD_`/`BYTEDIGGER_` aliases from the environment.

- **AC1** (reference case) Three `### SEVERITY: HIGH` findings, each citing `src/mod.py` only as `**File:** src/mod.py:12` (no `> path:line:` line). Aggregated content has all three under `## Aggregated Findings`, none under `## Suspect Findings`; `verified_findings` has 3, `suspect_withhold` is False, the verdict is FAIL, `_persist_fix_feed` output contains all three blocks and no `## Suspect Findings Withheld`. Each rendered block ends with the evidence line `> src/mod.py:12: <line 12 of the file verbatim>`.
- **AC2** Range citation `> src/mod.py:10-14: some text`: resolved, `verify_reason == "OK-RESOLVED-BY-READ"`, evidence line uses line 10.
- **AC3** A finding citing only a path with no line (`src/mod.py`): not resolved (stays `suspect-no-quote`), reaches the fix doc in the suspect section, never withheld.
- **AC4** A finding citing `src/mod.py:999` (line beyond the file) and nothing else: not resolved, reaches the fix doc suspect section. Paired in the same run with one resolvable finding.
- **AC5** Three findings with unresolvable citations (missing file, no path, line out of range): all stay `suspect-no-quote`; no rated findings, so `suspect_withhold` is False; all three reach the fix doc.
- **AC6** Containment: `../outside.py:1` (existing file outside the root) and an absolute path to an existing file outside both the worktree root and the scratchpad stay unresolved. An absolute path to an existing file inside the worktree root is not a candidate either (absolute tokens are never candidates, op1).
- **AC7** A finding with a normal `> src/mod.py:12: <line>` quote that matches is unchanged (`verified-exact`, block unchanged, no `review_citation_resolved` event).
- **AC8** Mixed population: 3 findings with a mismatching quote (`> src/mod.py:12: not the line`), 1 resolvable, 1 unresolvable no-quote. Rated: 3 suspect of 4, rate 0.75, N >= 3, so `suspect_withhold` is True; `_persist_fix_feed` withholds the 3 mismatching ones (notice and `fix_feed_suspect_withheld.withheld_count == 3`) and still renders the resolved block and the unresolvable block.
- **AC9** Kill switch: `HAL_REVIEW_CITATION_FALLBACK=0` and the AC1 input: findings are `suspect-no-quote`, blocks are unchanged, `suspect_withhold` is True, the fix doc has `## Suspect Findings Withheld`, no `review_citation_resolved` event. With the variable unset or `1`, AC1 holds.
- **AC10** Event: AC1 emits exactly one `review_citation_resolved` row per resolved finding with `phase == "phase_6_review"`, `reason == "OK-RESOLVED-BY-READ"`, `path`, `line`, `role`, `severity`, `title`.
- **AC11** No model call: the aggregation of the AC1 input runs with `ANTHROPIC_API_KEY` set and unset while every LLM dispatch entry (`llm_subprocess.invoke_llm_subprocess` and the registered reference backends) is replaced by a spy; no spy is called.
- **AC12** Flag: `flags_catalog.FLAGS["HAL_REVIEW_CITATION_FALLBACK"]` has kind `gate`, default `"1"`, a non-blank `description`, `owner` and a `provenance` starting with `introduced:`; `scripts/flag_owner_lint.py` exits 0 on the live catalog.
- **AC13** (GUARD) Existing `test_phase_6_*` aggregation tests, `test_gh1591_fix_gate_boundary.py`, `test_bd84*` canonicalization tests and `test_bd212_changelog_helper.py` stay green.
- **AC14** (GUARD) CHANGELOG `[Unreleased]` mentions `HAL_REVIEW_CITATION_FALLBACK` in its `Fixed` block, read through `helpers.changelog` (`read_changelog`, `require_entry`); no `Unreleased`+`.index(` line and no top-section pin in the test.
- **AC15** A cited `.py` file larger than `_QUOTE_FILE_MAX_BYTES` is not resolved (size cap kept).
- **AC16** Roots: a finding citing `reviews/spec.md:2` (file only under the scratchpad) resolves via `fallback_dir`; a finding citing a path that exists in both roots resolves against the worktree root (the evidence line carries the worktree file's text).
- **AC16b** (marker) In the AC8 input, `aggregated_content` contains exactly one line `SUSPECT FINDINGS WITHHELD FROM FIX WORKER: rate=0.75 threshold=0.4 withheld=3`, and its `withheld` equals the `fix_feed_suspect_withheld` `withheld_count` from `_persist_fix_feed` on the same data.
- **AC17** Candidates: a block naming `https://example.com/a.py:3`, `1.2.3:4` and then `src/mod.py:12` resolves to `src/mod.py:12` (URL and version tokens skipped; first resolving candidate wins). A variant with two resolvable tokens `src/mod.py:12` then `src/other.py:3` resolves to the first; a variant where the URL token is `https://example.com/src/mod.py:12` and nothing else is not resolved (the URL's tail is not a candidate); `` `src/mod.py:12` `` in backticks and `(src/mod.py:12)` resolve. A block with 6 candidates where only the 6th resolves is not resolved (cap 5). An extension-less token (`Makefile:3`, `src/mod:3`) is not a candidate.

## 4. op <-> AC map

op1: AC2-AC6, AC15-AC17 · op2: AC1, AC7, AC10 · op3: AC3, AC4, AC5, AC8, AC16b · op4: AC9, AC12 · op5: AC14 · provider/billing: AC11.

## 5. Adversarial edges

1. A finding that names an existing file and line only in prose ("see src/mod.py:12") is promoted. Accepted: the appended evidence line makes it a cited finding, so `verify_findings` and the semantic verifier check it like any other; the alternative is the silent drop this lot removes. The promotion does make a real-looking HIGH count toward the verdict (the stated purpose).
2. A fabricated path stays unresolved (AC5) and is fed as LOW CONFIDENCE, never dropped, never rated.
3. URL and version tokens, absolute paths and escapes are not resolved (AC6, AC17).
4. The candidate scan is capped at 5 and reads go through the shared cache, so a huge block cannot cause unbounded reads.
5. With the switch off, every number and event equals `f7e7aa7` (AC9).
6. Dedup: a resolved copy replaces a `suspect-no-match` duplicate of the same (severity, title), the existing rule "a verified copy replaces a suspect one". Accepted: its evidence line is a real line of the cited file.
