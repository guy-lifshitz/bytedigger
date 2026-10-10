# bd#90 gate r2: review citation fallback (spec r2 + revised RED audit)
<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd90-review-citation-fallback.md sha256:f6372e3707aebd39314d3098fc289645c9c09675cf0e796c4f6aecb145e0733e
-->

Audited: spec `docs/decisions/2026-10-10-bd90-review-citation-fallback.md` (DRAFT r2), RED `engine_py/tests/test_bd90_review_citation_fallback.py` (20 test ids; lot preflight reports 0 pass / 20 fail, assert-time). Base `f7e7aa7`. Product code re-read: `workflows/phase_6_review.py` (`_verify_finding_quote` :1424-1572, `_aggregate_review_findings` :1575-1951, `_review_all_findings_suspect` :1957-1972, `_render_fix_doc` :1975-2006, `_persist_fix_feed` :2050-2104, write-step call :2194-2197, stdout fallback call :2346), `lib/plugins/anti_hallucination/helper.py` (`_EVIDENCE_QUOTE_RE` :42, `check_citation` :192-236, `verify_findings` :239-384), `semantic_verifier.py` (:36), `lib/worktree_root.py`, `llm_subprocess.py` (`_BACKENDS` :2741, `reset_backends` :2853), `phase_7_synthesize.py:356-357`, and the guard suites `test_gh1591_fix_gate_boundary.py`, `test_phase_6_1F39FB1A_soft_tag.py`, `test_phase_6_FEB64BA8_backtick_strip.py`, `test_phase_6_review_21792EE7.py`, `test_phase_6_reviewer_suspect_rate_D3492E45.py`, `test_phase_6_CA50885D_suspect_fail_open.py`. Read-only. No tests run.

## Closure of r1 findings

| r1 # | Severity | Status | Evidence |
|---|---|---|---|
| 1 op3 mixed population | MAJOR | CLOSED | Spec :31 picks option (a): unresolved no-quote findings are unrated and always fed; no-match/file-not-found keep the rate rule and are withheld together. The pre/post-dedup question disappears for the rate because no-quote findings no longer enter it. AC8 (:50) pins 3 mismatch + 1 resolved + 1 unresolvable, rate 0.75, withhold True, 3 withheld, the resolved and unresolvable blocks fed; RED :271-292 asserts the notice text, `withheld_count == 3`, absence of the mismatch titles and presence of `no-path` in the suspect section. This choice opens a new gap on the composite marker (r2 finding 1). |
| 2 downstream bypass | MAJOR | CLOSED | Spec :30 appends a real `> path:line: <verbatim line>` evidence line, so `verify_findings` finds it with `_EVIDENCE_QUOTE_RE` (helper.py:42, :353) and `check_citation` passes on the verbatim text; the semantic verifier no longer sees `[UNCITED]`. §5 edge 1 (:67) is rewritten accordingly and is now accurate. RED pins the appended line in AC1 (:157-163), AC2 (:177), AC16 (:418/:420), AC17 (:434), and its absence in AC7 (:263) and AC9 (:307). Residual edge: a blank target line (r2 finding 5). |
| 3 absolute-path containment | MAJOR | CLOSED | Spec :29 now says an absolute token is never a candidate; AC6 (:48) and RED :237-252 cover an absolute path outside both roots and an absolute path inside the worktree, plus the `../` escape. No more contradiction with `_verify_finding_quote`. |
| 4 untested op1 clauses | MINOR | PARTIAL | Fallback and base-first precedence: CLOSED (AC16, RED :402-420, discriminating). Cap 5: CLOSED (RED :437-444). Extension rule: CLOSED (RED :447-459, the files exist so only the rule rejects them). URL and version skipping, and "first resolving of several": OPEN in effect. `https://example.com/a.py:3` and `1.2.3:4` do not exist on disk, so an implementation that treats them as ordinary candidates passes RED :425-434; and no block has a non-URL, non-version missing candidate before a resolving one, so a resolver that checks only the first candidate passes every test (r2 finding 3). |
| 5 AC11 weak spy | MINOR | CLOSED | Spec :53; RED :350-352 replaces `invoke_llm_subprocess` and every entry of `_BACKENDS` (`claude-subprocess`, `claude-in-session`, plus any runtime registration), teardown via `reset_backends`. |
| 6 dedup laundering | MINOR | CLOSED | Accepted in §5 edge 6 (:72). |
| 7 literal drift | MINOR | CLOSED | AC5 no longer states a threshold comparison; AC13 names `test_gh1591_fix_gate_boundary.py`; AC10 states `phase == "phase_6_review"`; AC11 text matches the RED. |
| 8 bd#84 overlap | MINOR | CLOSED | Out-of-scope note at :37. |
| 9 GREEN advisories | MINOR | CLOSED | op5 (:33) names the existing `### Fixed` block and no `SHARED/` string; the autouse fixture deletes the `BD_`/`BYTEDIGGER_` aliases (RED :35, :43-48). |

## Step 1: spec-internal consistency (literal-token drift)

- `_resolve_unquoted_citation`, `verified-resolved`, `OK-RESOLVED-BY-READ`, `review_citation_resolved`, `HAL_REVIEW_CITATION_FALLBACK`, `_CITATION_FALLBACK_MAX_CANDIDATES` are spelled identically in op1-op5, AC1-AC17 and the RED (constants RED :34-39).
- Event payload: op2 lists `phase, role, severity, title, reason, path, line`; AC10 lists the same; RED :322-328 checks all of them (`role == "composite"` matches `_slug_from_role_filename`, :1167-1170).
- The resolver return tuple carries `path_text` as written, so `path == "src/mod.py"` in AC10 is consistent with op1.
- One sentence is now misleading (MINOR, r2 finding 4): op3 says the rule for every other suspect "is unchanged". The rule is unchanged, but its inputs shrink, because unresolved no-quote findings leave both the rate and the min-N sample. See Step 1.5.
- op3 enumerates the counts that change ("the `fix_feed_suspect_withheld` count and the `## Suspect Findings Withheld` notice count only the withheld ones") and omits the composite-review marker. See r2 finding 1.

## Step 1.5: rule-overlap simulation against the real code

Wiring: the resolver runs in the verify loop (:1680-1689) before `match_kinds`, the per-finding emit (:1695-1704), the rate (:1710-1748), `suspect_withhold` (:1755-1757), dedup (:1762-1772), partition (:1783-1784), counts and verdict (:1787-1812) and both renderers, so op2 reaches every consumer, as in r1.

1. Rate and min-N with unrated no-quote findings. With V verified, Sm rated suspects and Sq unresolved no-quote findings, f7e7aa7 rates (Sm+Sq)/(V+Sm+Sq) over N = V+Sm+Sq; r2 rates Sm/(V+Sm) over N = V+Sm. The new rate exceeds the old one only if Sm·Sq > Sq·(V+Sm), that is 0 > V·Sq, which cannot hold; and N never grows. So r2 can only un-withhold, never newly withhold: the fail-open direction. It does change `suspect-no-match` outcomes, which the spec lists as out of scope: 2 fabricated quotes + 2 prose-only findings are withheld in full on f7e7aa7 (rate 1.0, N 4) and fed in full under r2 (N 2 < 3); 1 fabricated + 1 verified + 2 prose-only likewise (r2 rate 0.5 but N 2). This is defensible (min-N over judgeable citations), but it should be stated in §5 rather than described as "unchanged" (r2 finding 4).
2. `_review_all_findings_suspect` (:1957-1972). AC5 (3 unresolvable): verified 0, suspect 3 post-dedup, verdict `SUSPECT` (:1806-1810), suspect header present, no bd#84 marker, so the 4B9DF7D3 override precondition holds exactly as today. An all-resolvable review that used to be `SUSPECT` (override-eligible) is now `FAIL`/`PARTIAL` (override off): stricter, and the stated purpose. The only new combination is that an override-eligible review now also feeds all its unresolved findings to the fix worker; the override and the fix feed are independent today, so the override path is unchanged. No conflict.
3. Audit counts. `filtered_count = len(suspect_findings)` (:1792) still includes unresolved no-quote findings, so `filtered`, `consistent` and `high_filter_rate` keep the meaning "suspect, not verified". Fine. `match_kinds` (:1689) is unspecified: if GREEN implements the exclusion by not counting `suspect-no-quote` in `match_kinds`, the audit loses that status; if it keeps the count and filters only in the rate sum, the audit is unchanged (r2 finding 6). `reviewer_grep_accuracy_warning.finding_ids` (:1738-1747) lists every `suspect*` finding, so it would include unrated findings next to a `suspect_count` that excludes them (r2 finding 6). phase 7 (:356-357) prints "review findings were withheld as suspect" from `audit.suspect_withhold`; still true when some suspects were withheld.
4. Composite marker (:1916-1926). The line `SUSPECT FINDINGS WITHHELD FROM FIX WORKER: rate=... threshold=... withheld={len(suspect_findings)}` counts every post-dedup suspect. For the AC8 input that is 4 (3 mismatch + `no-path`), while `_persist_fix_feed` withholds 3 and emits `withheld_count == 3`. `test_gh1591_fix_gate_boundary.py:1228-1326` exists to pin that these two numbers agree ("two views of the same withholding decision"), and the code comment at :1919-1922 says `withheld` is "what actually gets left out of the fix feed". The GH1591 test stays green only because its fixture has no no-quote finding. Spec r2 does not mention the marker, and AC8 / RED :271-292 do not read `aggregated_content`, so a GREEN that follows the spec literally leaves the composite review (read by a human and by the satisfaction evaluator) overstating what was withheld (r2 finding 1).
5. `_persist_fix_feed` and the switch. op3 makes `_persist_fix_feed` render `suspect-no-quote` suspects even when `suspect_withhold` is True, but only "with the switch on"; AC9 (RED :297-312) requires that with `=0` the same findings are withheld and the notice appears. So `_persist_fix_feed` (a different step from the aggregator, :2194) must know the switch state. Reading the flag a second time in the write step satisfies the RED, but a flag flip between the two steps (resume with a changed environment) would let the aggregator's `suspect_withhold` and the feed partition disagree. Recording the decision in the aggregator output (a per-finding marker or a data key) avoids the second read (r2 finding 7, advisory).
6. `_persist_fix_feed` notice rate (:2082-2083). `_rate = withheld_count / (len(verified) + len(suspect))`; for AC8 this prints 0.60 while the composite says 0.75. The pre-dedup/post-dedup divergence already exists; r2 widens it slightly. Not pinned; advisory (r2 finding 7).
7. Doc-derived path (:2070-2075). Reached only when both structured lists are empty. A run that withholds always has at least 3 rated findings, so `suspect_withhold=True` with empty lists does not arise from the aggregator; the stdout fallback call at :2346 passes no `suspect_withhold` (False). "Treated as before" for suspects without `verify_status` is therefore harmless, and it keeps the GH1591 unit tests (:914-938, :956-969), whose dicts carry no `verify_status`, green.
8. Appended evidence line vs existing tests. Only resolved findings change. The no-quote fixtures in 1F39FB1A (:425-427, :491-493, :627-629), FEB64BA8 (:489-492) and 21792EE7 (:308-313) contain no `relpath.ext:N` token, so nothing resolves and no block bytes change. GH1591 and D3492E45 always write a `> path:N:` line (`suspect-no-match`, not no-quote), so their rates and `withheld=` pins are unaffected. 1F39FB1A AC11's six-status set applies only to its own verified-exact fixture. CA50885D and 65695203 call `_render_fix_doc` directly. No existing pin breaks.
9. Appended line vs `verify_findings`. `_EVIDENCE_QUOTE_RE` takes the first matching `>` line. For AC2 the range line `> src/mod.py:10-14: some text` matches neither `_QUOTE_LINE_RE` nor `_EVIDENCE_QUOTE_RE`, so the appended line is the one checked. Two residual edges: a blank or whitespace-only target line yields `> p:n: `, which `_EVIDENCE_QUOTE_RE` (`:\s+(.+)$`) does not match, so the finding is verified in phase 6 and `[UNCITED]` downstream (r2 finding 5); and `check_citation` resolves relative paths against the process cwd (helper.py:204-205), not the worktree or scratchpad, so a fallback-resolved cite (AC16 `reviews/spec.md`) is demoted downstream unless the cwd matches. The second already holds for every relative verified-exact quote today; not new.
10. Dedup order with op3. The same (severity, title) written once with a fabricated quote and once as unresolvable prose: between two suspects first-seen wins (:1766-1771), so with `suspect_withhold` True the finding is withheld or fed depending on order (r2 finding 2).

## Step 2: §2 vs §3 cross-check

| §2 path | AC |
|---|---|
| op1 relative `path:line` in any line shape | AC1, AC10, AC11 |
| op1 range first line | AC2 |
| op1 no line, not a candidate | AC3 |
| op1 line out of range | AC4, AC5 |
| op1 missing file / no path | AC5, AC10 |
| op1 `../` escape, absolute never a candidate | AC6 |
| op1 size cap | AC15 |
| op1 fallback root, base first | AC16 |
| op1 URL / version skip, first resolving wins | AC17 (not discriminated, finding 3) |
| op1 cap 5 | AC17 |
| op1 extension rule | AC17 |
| op2 status, evidence line, counted as verified | AC1, AC2, AC8 |
| op2 matching quote untouched | AC7 |
| op2 event, final status in `composite_finding_quote_verified` | AC10, AC7 |
| op3 unrated (rate, event) | AC5, AC8 |
| op3 always fed | AC3, AC4, AC5, AC8 |
| op3 rated suspects withheld; notice and event count only the withheld | AC8 |
| op3 composite marker `withheld=` (terminal artifact of the withhold decision) | none; spec silent (finding 1) |
| op3 doc-derived path treated as before | GH1591 AC7/AC8 via AC13 |
| op4 switch off, including the feed partition | AC9 |
| op4 catalog + owner lint | AC12 |
| op5 CHANGELOG | AC14 |
| guard | AC13 |

The withhold decision has three outputs: the fix-doc notice, the `fix_feed_suspect_withheld` event, and the composite-review marker. r2 specifies and pins the first two for the new partition and leaves the third at a value that contradicts them. That is a terminal post-condition with no producing spec text and no AC: REJECT (finding 1).

## Step 3: RED adequacy

- Collection: imports only existing names (`llm_subprocess`, `reset_backends`, `StepResult`, `phase_6_review`, `helpers.changelog`); the new private symbol is reached only after `assert hasattr` (RED :300). The preflight's 20/20 failures are at assert time.
- No self-mocking: the aggregator and `_persist_fix_feed` run for real on real files. `_emit_safe` is replaced for capture only; in AC9 the resolver is replaced to prove it is not called; in AC11 the LLM entry points are replaced by spies. None of these is the unit under test.
- Fixtures: `current_worktree_path` makes `src/mod.py` resolve under `wt` (`worktree_root.py:14-16`). The last block retains `VERDICT: PARTIAL` and the count comment (the :1189 strip needs end-of-block); neither carries a candidate token, and `_last_line` still sees the appended line.
- Discrimination of r2 behaviour: an implementation that keeps no-quote findings in the rate fails AC3, AC5 (rate 0.0, no `reviewer_suspect_rate`) and AC8 (rate 0.75); one that withholds unresolved findings with the rest fails AC8 (`no-path` in the suspect section); one that feeds every suspect when any no-quote remains fails AC8 (mismatch titles absent, count 3); one that ignores the switch in `_persist_fix_feed` fails AC9; one that forgets the evidence line fails AC1, AC2, AC16, AC17; one that appends to verified-exact findings fails AC7.
- Not discriminated (MINOR): URL and version skipping, and checking candidates beyond the first (finding 3); the composite `withheld=` count (finding 1); backtick- or parenthesis-wrapped tokens such as ``**File:** `src/mod.py:12` `` (finding 8).

## Step 4: reachability

- Resolver call, status replace, block append: Point = new branch in the verify loop :1684-1689; Host = `_aggregate_review_findings`; Test path = `world.run` (AC1-AC8, AC10, AC11, AC15-AC17).
- `review_citation_resolved`: same Point and Host, through module `_emit_safe` captured by the fixture (AC7, AC10).
- Rate exclusion: Point = :1710-1712; Host = `_aggregate_review_findings`; Test path = `result.data["suspect_rate"]`, `rows("reviewer_suspect_rate")` (AC5, AC8).
- `suspect_withhold`: Point = :1755-1757; Test path = `result.data["suspect_withhold"]` (AC3-AC5, AC8, AC9).
- Feed partition: Point = `_persist_fix_feed` :2079-2094; Host = `_persist_fix_feed`; Test path = `world.fix_doc` (AC1, AC3-AC5, AC8, AC9).
- Composite marker: Point = :1916-1926; Host = `_aggregate_review_findings`; Test path = none (finding 1).
- Flag and lint: `flags_catalog.FLAGS`, `scripts/flag_owner_lint.py` subprocess (AC12). CHANGELOG via `helpers.changelog` (AC14).

## Product principles

- Deterministic first: yes, a local stat and read through the shared cache.
- Provider-agnostic degrade, no model call: yes; AC11 now spies the dispatch entry and every registered backend, with the API key set and unset.
- Subscription and API-token modes: unaffected by design (no provider read), pinned by AC11's parametrisation.
- Kill switch: `HAL_REVIEW_CITATION_FALLBACK`, gate, default `"1"` (AC9, AC12).

## Scope

Paths stay `phase_6_review.py`, `flags_catalog.py`, `CHANGELOG.md`. No change to post-fix test scope (#249) or the fix-and-recheck loop (#250); the prompt is untouched and not asserted. The fix for finding 1 stays inside `phase_6_review.py`.

## Adversarial edges (not covered by §3)

1. **Mixed population, composite view:** the AC8 input renders `withheld=4` in the composite review while the fix feed withholds 3 (finding 1).
2. **Min-N shrink:** 2 fabricated quotes + 2 prose-only findings are withheld on f7e7aa7 and fed under r2 (finding 4).
3. **Missing first candidate:** `**File:** src/gone.py:3` then `**File:** src/mod.py:12` in one block; a first-candidate-only resolver leaves it unresolved and passes the RED (finding 3).
4. **Locally existing URL or version path:** create `wt/example.com/a.py` or `wt/1.2.3`; a resolver without the URL/version rule promotes it and passes the RED (finding 3).
5. **Blank target line:** `**File:** src/mod.py:N` where line N is empty; verified in phase 6, `[UNCITED]` downstream (finding 5).
6. **Wrapped tokens:** a citation in backticks or parentheses, the most common reviewer shape; token boundaries are unspecified (finding 8).
7. **Dedup order:** fabricated-quote copy and prose-only copy of the same finding; fed or withheld depending on order (finding 2).
8. **Flag flip between steps:** aggregator with the switch on, write step with it off (or the reverse); the feed partition disagrees with `suspect_withhold` (finding 7).

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: FAIL
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC17: PASS

## Findings

1. **MAJOR: the composite-review withheld marker contradicts the fix feed for the population AC8 introduces (op3, AC8).** `_aggregate_review_findings` writes `SUSPECT FINDINGS WITHHELD FROM FIX WORKER: rate=... threshold=... withheld={len(suspect_findings)}` (:1923-1926), and the code comment says the number is what gets left out of the fix feed. Under op3 the unresolved no-quote suspects are in `suspect_findings` but are fed, so for the AC8 input the composite says 4 and `fix_feed_suspect_withheld.withheld_count` says 3. GH1591 pinned exactly this agreement after a prior gate finding (`test_gh1591_fix_gate_boundary.py:1228-1326`); it stays green only because its fixture has no no-quote finding. op3 lists the counts that change and omits this one; AC8 and RED :271-292 do not read `aggregated_content`. Fix: in op3, state that the marker's `withheld=` counts only the withheld (rated, post-dedup) suspects, and that the marker is absent when that number is 0; add to AC8 an exact-equality assertion on the marker line (`rate=0.75 ... withheld=3`) and that it equals `withheld_count`.
2. **MINOR: dedup order decides feed vs withhold.** A fabricated-quote copy and an unresolvable prose copy of the same (severity, title): first-seen wins between two suspects (:1766-1771), so the surviving copy, and with it fed vs withheld, depends on order. Accept in §5, or prefer the rated copy deterministically.
3. **MINOR: two op1 clauses remain undiscriminated (r1 finding 4 partial).** The URL and version tokens in RED :427 do not exist on disk, so skipping them is not tested; and no block has a missing ordinary candidate before a resolving one, so "first resolving of several" is not tested (the cap test :437-444 also passes for a first-candidate-only resolver). Add a block `**File:** src/gone.py:3` / `**File:** src/mod.py:12` that must resolve to line 12, and create `wt/example.com/a.py` (3+ lines) and a `wt/1.2.3` file so that only the skip rule keeps them out.
4. **MINOR: op3's "rule unchanged" understates the min-N effect.** Removing unresolved no-quote findings from the sample can only lower the rate and N, so r2 never withholds more than f7e7aa7 but can feed fabricated-quote findings that f7e7aa7 withheld (2 mismatches + 2 prose-only). State this in §5 as accepted, or pin it with an AC.
5. **MINOR: blank target line.** A cite to an empty or whitespace-only line resolves and appends `> p:n: `, which `_EVIDENCE_QUOTE_RE` does not match, so the finding counts toward the verdict in phase 6 and is `[UNCITED]` downstream. Either require a non-blank target line in op1 or accept it in §5.
6. **MINOR: telemetry under op3 is unspecified.** Say whether `match_kinds` still counts `suspect-no-quote` (recommended: yes, audit unchanged) and whether `reviewer_grep_accuracy_warning.finding_ids` lists only rated suspects (so it matches its `suspect_count`).
7. **MINOR (advisory for GREEN): the switch is needed in two steps.** `_persist_fix_feed` runs in the write step; rather than reading the flag a second time, carry the aggregator's decision (a per-finding marker or a data key) so the feed partition and `suspect_withhold` cannot disagree after a resume with a changed environment. The `_persist_fix_feed` notice rate (:2083) is a post-dedup ratio over all suspects and will print 0.60 where the composite prints 0.75; consider aligning it while touching that code.
8. **MINOR: token boundaries.** op1 says "a relative path token" without saying how tokens are delimited. Reviewers commonly write ``**File:** `src/mod.py:12` `` or `(src/mod.py:12)`. State that surrounding backticks, parentheses and trailing punctuation are not part of the token, and add one such case to AC1 or AC17.

Finding 1 changes op3 and AC8, so the spec revision and its sha change, and the RED needs the added assertion. Findings 2-8 can be folded into the same revision.

VERDICT: REJECTED
