# bd#90 gate r3: review citation fallback (spec r3 + revised RED, targeted re-audit)

Anchor note: this document does not carry the verdict-anchor block. The anchor is stamped deterministically after the file is written (`python3 SYSTEM/cli/build/verdict-anchor.py <this doc> --spec docs/decisions/2026-10-10-bd90-review-citation-fallback.md --red engine_py/tests/test_bd90_review_citation_fallback.py`); the audit itself does not write or copy any hash.

Audited: spec `docs/decisions/2026-10-10-bd90-review-citation-fallback.md` (DRAFT r3), RED `engine_py/tests/test_bd90_review_citation_fallback.py` (25 test ids; the lot preflight reports 0 pass / 25 fail, assert time). Base `f7e7aa7`. Product code re-read: `workflows/phase_6_review.py` verify loop :1676-1689, events :1695-1748, `suspect_withhold` :1755-1757, dedup :1762-1772, partition :1783-1784, verdict :1798-1812, audit dict :1872-1891, composite marker :1916-1926, `_render_fix_doc` :1975-2006, `_persist_fix_feed` :2050-2104, write-step call :2190-2198, constants :270, :1301-1307. Guard suites grepped for `match_kinds` and `suspect-no-quote` pins. Read-only, no tests run. Scope: closure of r2 findings and problems introduced by the r3 edits; closed r1/r2 items are not reopened.

## Closure of r2 findings

| r2 # | Severity | Status | Evidence |
|---|---|---|---|
| 1 composite marker vs fix feed | MAJOR | CLOSED | Spec op3 :31 now states that the marker `SUSPECT FINDINGS WITHHELD FROM FIX WORKER: ... withheld=<n>` (`phase_6_review.py:1923-1926`) counts only the withheld (rated, post-dedup) suspects and must equal `fix_feed_suspect_withheld.withheld_count`. AC16b :59 pins the exact line; AC8 :50 pins the count. RED :301-302 (AC8: exactly one marker line ending `withheld=3`) and RED :312-317 (AC16b: marker equals `SUSPECT FINDINGS WITHHELD FROM FIX WORKER: rate=0.75 threshold=0.4 withheld=3` and ends with the captured `withheld_count`). Formatting checked against the code: the marker interpolates `suspect_rate` and `_REVIEWER_SUSPECT_RATE_THRESHOLD` with plain f-string conversion (:1924-1925); `_REVIEWER_SUSPECT_RATE_THRESHOLD = 0.4` (:1301) renders `0.4`; 3/4 is exact in binary and renders `0.75`. Population simulated under op3: rated = 3 `suspect-no-match` + 1 `verified-resolved` (prefix `verified`, counted at :1710), the unresolved `no-path` excluded, so rate 0.75 > 0.4 and N = 4 >= 3 (:1302), `suspect_withhold` True; titles are distinct, so post-dedup rated suspects = 3 = marker = writer count. A GREEN that keeps `len(suspect_findings)` (4) fails both tests. |
| 2 dedup order decides feed vs withhold | MINOR | OPEN | §5 edge 6 :73 covers only resolved vs `suspect-no-match`. A `suspect-no-match` copy and an unresolved `suspect-no-quote` copy of the same (severity, title) still resolve first-seen (:1766-1771). The status line :12 says all findings are addressed; this one is not. Finding 2 below. |
| 3 URL/version and first-resolving undiscriminated | MINOR | CLOSED | RED :450-463 creates `wt/example.com/a.py` and `wt/1.2.3`, so only the version rule keeps `1.2.3:4` out (line 4 exists); RED :466-477 adds `gone-first` (`src/gone.py:3` then `src/mod.py:12`), which a first-candidate-only resolver fails; RED :480-489 adds the URL-tail variant. AC17 :60 lists all three. |
| 4 "rule unchanged" understated min-N | MINOR | CLOSED | op3 :31 now says the rated population shrinks and a mixed population can fall below the threshold or min-N and feed its mismatching suspects, as intended. |
| 5 blank target line | MINOR | CLOSED | op1 :29 requires the line to be "within the file and not blank"; RED :41-42 makes line 20 empty and line 21 whitespace-only, and RED :504-513 asserts both stay `suspect-no-quote` while a normal cite in the same run resolves. Treating whitespace-only as blank is the RED's reading; it is the natural one and matches the r2 rationale. |
| 6 telemetry under op3 unspecified | MINOR | CLOSED (with note) | op3 :31 now says `match_kinds`, `reviewer_suspect_rate` and `reviewer_grep_accuracy_warning.finding_ids` cover rated findings only, and `composite_finding_quote_verified` still covers every finding. RED :297-300 pins `suspect_count == 3` and that `no-path` is absent from `finding_ids`. No guard or consumer pins `match_kinds["suspect-no-quote"]` (only `verified-*` keys are asserted: `test_3F5599A6_a2a3_residue.py:268`, `test_phase_6_fuzzy_citation_match_A37D4F04.py:376-379`; no production reader outside `phase_6_review.py`). The `match_kinds` clause itself is not pinned by the RED (finding 4). |
| 7 switch needed in two steps; notice rate | MINOR | PARTIAL | First half rewritten in op3 :31, but the new wording cannot be satisfied literally (finding 1). Second half open: the `_persist_fix_feed` notice and event rate (:2082-2083) is `withheld / (verified + all suspects)`, which for the AC8 input prints 0.60 next to the marker's 0.75; unpinned and unspecified (finding 3). |
| 8 token boundaries | MINOR | CLOSED | op1 :29 lists the delimiters (whitespace, backticks, quotes, parentheses, brackets, `*`, `,`) and strips a trailing `.`/`:`; RED :492-501 asserts backtick, parenthesised and trailing-period tokens resolve to line 12. With these delimiters `**File:**` yields `File:` (no extension, no line) and a URL stays one token (neither `/` nor `:` is a delimiter), so the `://` rule applies to the whole token as intended. |

## Step 1: spec-internal consistency (r3 edits)

- Literal tokens (`_resolve_unquoted_citation`, `verified-resolved`, `OK-RESOLVED-BY-READ`, `review_citation_resolved`, `HAL_REVIEW_CITATION_FALLBACK`, `_CITATION_FALLBACK_MAX_CANDIDATES`, the marker prefix) are spelled identically in op1-op5, AC1-AC17, AC16b and the RED (RED :34-43).
- §4 map :64 now lists AC16b under op3. The RED module docstring (:3) still says "AC1-AC17, r2"; cosmetic.
- op3 writer channel (new, finding 1): "passes its switch state to `_persist_fix_feed` through `suspect_withhold` and the finding dicts' `verify_status` (the writer does not read the flag itself)". The unresolved finding carries `verify_status == "suspect-no-quote"` with the switch on (AC3-AC5, AC8; RED :191, :208, :226, :283) and with it off (AC9; RED :331), and `suspect_withhold` is True in both AC8 and AC9. The writer receives identical inputs for that finding yet must feed it in AC8 and withhold it in AC9, so those two channels alone cannot carry the switch.
- op1 version rule wording (finding 5): "a bare version string (digits and dots only)" must apply to the path part before `:<line>`; the whole token `1.2.3:4` contains a colon. AC17 and RED :454-463 settle the intent.

## Step 1.5: rule-overlap simulation (r3 inputs)

1. AC8 / AC16b population: done in the closure table, row 1. Writer side: under op3 the write step renders the verified block plus the unresolved `no-path` block in the LOW CONFIDENCE section (header `## Suspect Findings (LOW CONFIDENCE)`, :1996, matching RED :38) and withholds the three mismatches; `withheld_count` must become the count of non-`suspect-no-quote` suspects (3), giving the notice text `3 suspect finding(s) withheld` (:2086, RED :288) and one event with `withheld_count == 3`. `_render_fix_doc` does not include `review_content`, so the mismatch titles are absent from the doc (RED :289-290) once they are excluded from the rendered suspect list.
2. AC9 (switch off, AC1 input): 3 `suspect-no-quote`, rate 1.0, N 3, `suspect_withhold` True, every suspect withheld; matches f7e7aa7. The marker would read `withheld=3`, consistent.
3. AC5: no rated findings, total 0, so no `reviewer_suspect_rate` event (:1718) and `suspect_rate == 0.0`; matches RED :227-230.
4. Candidate scan with the r3 delimiters, block by block: `> src/mod.py:10-14: some text` gives `src/mod.py:10-14:`, trailing colon stripped, range, line 10 (AC2). `(src/mod.py:12).` gives `src/mod.py:12` then `.`. `https://example.com/a.py:3` is one token containing `://`, skipped; `1.2.3:4` skipped by the version rule; then `src/mod.py:12` (AC17 tokens-a). `src/nope1..5.py:1` exhaust the cap of 5 before `src/mod.py:12` (AC17 cap). `Makefile:3` and `src/mod:3` have no extension (AC17). All consistent with op1.
5. Blank rule: line 20 (`""`) and line 21 (`"    "`) exist within the 30-line file, so only the blank rule rejects them. The `MOD_LINES` edit touches no other test's cited lines (10, 12, 999).
6. Withhold with zero post-dedup rated suspects (all rated suspects replaced by verified copies of the same key): marker `withheld=0`, no notice, no event. The equality holds trivially; behaviour is unchanged from f7e7aa7 and not new.
7. Guard impact of the rated-only telemetry: guard suites pin only `verified-*` keys of `match_kinds`, and no guard fixture with a `suspect-no-quote` finding asserts a rate, `suspect_withhold` or a `withheld=` value (grep of the six files containing `suspect-no-quote`). AC13 is not endangered by the r3 edits.

## Step 2: §2 vs §3 cross-check (delta from r2)

| §2 path | AC |
|---|---|
| op3 composite marker `withheld=` equals `withheld_count` | AC16b, AC8 (closed r2 MAJOR) |
| op3 `reviewer_suspect_rate`, `finding_ids` rated only | AC8 (RED :297-300) |
| op3 `match_kinds` rated only | none (finding 4, MINOR: telemetry only) |
| op3 writer receives the switch state without reading the flag | none, and not satisfiable as worded (finding 1, MINOR) |
| op1 delimiters, trailing strip | AC17 (RED :492-501) |
| op1 non-blank line | AC17 (RED :504-513) |
| op1 URL/version skip, first resolving wins | AC17 (RED :450-489) |

Every terminal post-condition of the withhold decision (fix-doc notice, `fix_feed_suspect_withheld` event, composite marker) now has producing spec text and an AC. No REJECT condition remains.

## Step 3: RED adequacy (new and changed tests)

- AC16b and the AC8 additions read only `result.data["aggregated_content"]` and captured events; they run the real aggregator and writer, no self-mocking. Both fail pre-GREEN at assert time (the marker count is 4, and `reviewer_suspect_rate.suspect_count` is 4).
- AC17 variants: each pairs the probe with at least one normally resolvable finding in the same run, so a GREEN with no resolver fails on the positive assertion, not only on the negative one. The local `example.com/a.py` and `1.2.3` files make the version skip discriminating. The URL rule is behaviourally pinned only against tail extraction: a GREEN without the `://` rule would look up `https:/example.com/a.py`, which does not exist, so it also leaves the URL unresolved. The outcome is still correct, so this is not a gap.
- The `"3 suspect finding(s) withheld"` text assertion (RED :288) matches the existing notice format (:2086).
- Not discriminated: the op3 writer-channel clause (a writer that reads the flag passes, because the RED keeps the environment unchanged between `world.run` and `world.fix_doc`), and `match_kinds` rated-only.

## Step 4: reachability (delta)

- Marker count: Point = :1923-1926; Host = `_aggregate_review_findings`; Test path = `result.data["aggregated_content"]` (AC8 RED :301, AC16b RED :312).
- Rated-only rate event and warning: Point = :1710-1748; Host = `_aggregate_review_findings`; Test path = `world.rows("reviewer_suspect_rate")`, `world.rows("reviewer_grep_accuracy_warning")` (AC8 RED :297-300).
- Writer partition and count: Point = :2079-2094; Host = `_persist_fix_feed`; Test path = `world.fix_doc` and `world.rows("fix_feed_suspect_withheld")` (AC8, AC16b, AC9).
- Delimiters, blank rule, URL/version, first-wins: Point = the new resolver called from the verify loop :1684-1689; Test path = `world.run` (AC17 variants).

## Adversarial edges (not covered by §3)

1. **Switch flipped between steps:** aggregate with the switch on, then run the write step with `=0` (resume under a changed environment). If the writer reads the flag, the unresolved `no-path` finding is withheld although the aggregator excluded it from the rate and the marker said `withheld=3`; the marker and `withheld_count` then disagree again (4 vs 3). No test holds the environment apart between the two calls (finding 1).
2. **Dedup order between two suspects:** `suspect-no-match` copy first and `suspect-no-quote` copy second (or the reverse) of the same (severity, title); with `suspect_withhold` True the finding is withheld or fed depending on order (finding 2).
3. **Notice rate vs marker rate:** the AC8 input prints 0.60 in the fix-doc notice and the event while the composite marker prints 0.75 (finding 3).

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
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC16b: PASS
- AC17: PASS

## Findings

1. **MINOR: the op3 writer-channel sentence cannot be met literally.** op3 says `_aggregate_review_findings` passes the switch state to `_persist_fix_feed` "through `suspect_withhold` and the finding dicts' `verify_status` (the writer does not read the flag itself)". The unresolved finding is `suspect-no-quote` in both modes, and `suspect_withhold` is True in both AC8 and AC9, yet the writer must feed it in AC8 and withhold it in AC9. GREEN therefore needs a third channel. The spec's stated intent (the writer does not read the flag) is met by a per-finding key set by the aggregator, for example a boolean marking the finding as unrated, while `verify_status` stays `suspect-no-quote` as AC3-AC5, AC8 and AC9 require. GREEN should do that and must not read the flag in the writer or invent a new status value. No test detects a writer that reads the flag (adversarial edge 1). This is not blocking: every behaviour the RED pins is unambiguous, and r2 classed the underlying risk as advisory. Recommended in a later revision: replace "the finding dicts' `verify_status`" with "a per-finding field set by the aggregator", and add a RED case that aggregates with the switch on, sets `=0` before `world.fix_doc`, and asserts `no-path` is still fed and the marker still equals `withheld_count`.
2. **MINOR (r2 #2, still open): dedup order between a rated and an unrated copy.** First-seen wins between two suspects (:1766-1771). Either accept this in §5 or prefer the rated copy deterministically. The status line :12 should not say all r2 findings are addressed while this one is not.
3. **MINOR (advisory, r2 #7 second half): notice and event rate.** `_persist_fix_feed` computes `_rate = withheld_count / (len(verified) + len(suspect))` (:2083); for the AC8 input that is 3/5 = 0.60, while the marker and `reviewer_suspect_rate` report 0.75. op3 aligns the counts but not the rate. GREEN may compute the writer rate over rated findings only (3/4) while touching this code; nothing pins either value.
4. **MINOR: `match_kinds` rated-only is unpinned.** op3 requires `match_kinds` to exclude unresolved no-quote findings with the switch on, but no RED assertion reads `findings_audit["match_kinds"]` or the `review_findings_audit` event. If the exclusion is implemented by filtering only the rate sum, `match_kinds` keeps `suspect-no-quote` and the RED still passes. The difference is telemetry only (no consumer outside `phase_6_review.py`), but GREEN should follow op3. A one-line assertion on the AC8 audit event would pin it.
5. **MINOR (wording): the version rule applies to the path part.** "a bare version string (digits and dots only)" should read "a token whose path part is digits and dots only", because the token `1.2.3:4` contains a colon. AC17 and RED :454-463 make the intent unambiguous.

No MAJOR finding. The r2 MAJOR is closed by op3 :31, AC16b :59, AC8 :50 and RED :301-302 / :312-317, and the marker text the RED expects matches the code's formatting. The r3 edits introduce no blocking problem.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd90-review-citation-fallback.md sha256:f9a9450669c78c759a89219caeb5f5031e7e7eb173c28e4c102bf596cfe26cb9
red: engine_py/tests/test_bd90_review_citation_fallback.py sha256:a21ba38103a8c3f950cfa43213ed000378cbb835b9f1afc1bfbec55f4d2b1588
-->
