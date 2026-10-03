# bd#243 gate r4 - spec + RED audit (GREEN-entry gate-verdict guard)

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md sha256:8ff0d905fa7074d63812db27fc6ea4fe8dec277d41293d595da79acd715aff5b
-->

Audited (read-only):
- Spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md (r4 DRAFT)
- RED: engine_py/tests/test_bd243_green_entry_guard.py (68 tests, AC1-AC37, unchanged since r3)
- Previous verdict: ...-gate-r3.md (REJECTED, 1 MAJOR + 6 MINOR + 11 edges)
- Scope of r4 delta: 2.1 exclusion widened to `*-gate-r<digit>*.md`; 2.2 step 2 tie-break now
  "on equal N the LONGEST prefix wins, the stem form being the longest"; section 4 gains bullets for
  unhooked commit paths, merge-conflict commits, shallow clone, multi-`spec:` anchor block.
- Not re-audited: sections unchanged since r3 (r3 Steps 2-4 and sibling-suite analysis stand).

## r3 fold check

| r3 | Sev | Spec r4 | Status |
|---|---|---|---|
| F1 AC34 vs literal exclusion | MAJOR | 2.1: "any name matching `*-gate-r<digit>*.md` (integer revision or not ...; only plain positive-integer ones are gate docs, 2.2)" | CLOSED (re-simulated below) |
| F2 header chokepoint wording | MINOR | section 4 bullet 1 names `--no-verify`, clean merge, cherry-pick, rebase | CLOSED in substance; header line 5-6 still says "every commit of a lot" (cosmetic, finding 2) |
| F3 prefix-vs-prefix tie | MINOR | 2.2 step 2 longest-prefix tie-break | CLOSED in spec; no RED variant (finding 3) |
| F4 2.3 git-call enumeration | MINOR | unchanged | OPEN, advisory |
| F5 cosmetics | MINOR | unchanged (blank line at spec :173 still splits the 3b table; AC23 RED comment "three-segment stem") | OPEN, advisory |
| F6 merge-conflict false refusals | MINOR | section 4 bullet 1: accepted, escape = escalation marker / kill switch | CLOSED (accepted limitation) |
| F7 multi-spec line / exemption-only / spec=- shape | MINOR | multi-`spec:` line pinned in section 4 bullet 2; the other two unchanged | PARTIAL, advisory |
| r3 edge 11 shallow clone | - | section 4 bullet 1: UNREADABLE (fail closed) | CLOSED, consistent with 2.1(b) |

## Step 1 - spec-internal consistency (literal-token drift)

- Codes, env names, bypass kinds: unchanged and identical across 2.1-2.6, both AC tables and the RED.
- Exclusion vs recognition placeholders: 2.1 now uses `<digit>` with a trailing `*` (looser), 2.2
  keeps `<N>` = positive integer (stricter) and both sentences cross-reference each other ("only plain
  positive-integer ones are gate docs, 2.2" / "A revision that is not a plain positive integer ... is
  not a gate doc"). The r3 drift is gone: every name the recognizer accepts is also excluded, and the
  set "excluded but not a gate doc" (`-gate-r3.1.md`, `-gate-r7-delta.md`) is now explicit.
- Tie-break wording: "LONGEST prefix wins, the stem form being the longest" is consistent with the
  earlier "stem form wins" rule (strict superset) and with AC23's equal-N case.
- Section 4 bullet 1 (shallow clone UNREADABLE) agrees with 2.1(b) merge-base fail-closed and AC32.
- Section 4 bullet 2 (multi-`spec:` first block) agrees with 2.2 step 4 ("a verdict-anchor line
  `spec: docs/decisions/<stem>.md ...`") and AC36 (first block only). It is a normative rule filed under
  "Out of scope" (finding 4, MINOR).
- `*-gate-verdict-r<N>.md` is still listed separately; harmless (`-gate-v` is not `-gate-r<digit>`).

No new drift introduced by r4.

## Step 1.5 - rule-overlap simulation (r4 text)

Order: [0 kill switch] -> (a) source -> (b) base probe / merge-base -> (c) lot specs (index, A,
--no-renames, minus `*-gate-r<digit>*.md` and the other exclusions / Gate-exempt) -> per spec:
1 escalation -> 2 gate docs (stem U prefixes >= 4 segments, plain positive integer N, max N, longest
prefix on ties) -> 3 verdict -> 4 first anchor block -> 5 pass.

AC34 (STEM = `2026-10-03-widget`, three segments -> stem form only):

| Part | Added docs on lot | (c) lot specs | Step 2 gate docs | Outcome | RED |
|---|---|---|---|---|---|
| 1 | widget.md, widget-gate-r2.md (REJECTED), widget-gate-r3.1.md (APPROVED) | widget only (`-gate-r2`, `-gate-r3.1` both match `*-gate-r<digit>*.md`) | r2 only (r3.1 not a plain integer) | one REJECTED line naming `-gate-r2.md` | `_assert_single(C_REJECTED, gate_name=...-gate-r2.md)` matches |
| 2 | widget.md, widget-gate-r3.1.md | widget only | none | one MISSING line | `_assert_single(C_MISSING)` matches |

AC23 (KEY_STEM = `2026-10-03-bd243-green-entry-guard`, seven segments; prefixes >= 4 segments:
`2026-10-03-bd243`, `...-bd243-green`, `...-bd243-green-entry`; stem form longest):

| Case | Gate docs | Newest | Outcome | RED |
|---|---|---|---|---|
| key r1 REJECTED | `2026-10-03-bd243-gate-r1` | key r1 | REJECTED naming key r1 | matches |
| key r1 APPROVED + anchor | same | key r1 | [] | matches |
| stem r1 APPROVED, key r2 REJECTED | both | r2 (numeric max) | REJECTED naming key r2 | matches |
| stem r1 APPROVED, key r1 REJECTED | both | tie at N=1 -> longest = stem form | [] | matches |
| `2026-10-03-bd244` r1 | not a prefix | none | MISSING | matches |
| short stem `2026-10-03-short` (4 seg) + `2026-10-03-gate-r1.md` | `2026-10-03` has 3 segments, not counted; the file itself is excluded from (c) by `*-gate-r<digit>*.md` | none | MISSING | matches |
| KEY_STEM + `2026-10-03-gate-r1.md` | 3-segment prefix not counted | none | MISSING | matches |

AC33 re-checked against the new tie-break: case c (s1 r1 APPROVED, bd218 r2 REJECTED) is decided by
N, not by the tie-break -> REJECTED naming `2026-10-03-bd218-gate-r2.md`; s2 sibling is not a prefix
of `2026-10-03-bd218-s1-preflight-rung` -> never binds. All match the RED.

Other ACs whose added docs include gate docs (AC1, AC3, AC8, AC9, AC14, AC36 ...): every plain
`-gate-rN.md` was already excluded under r3 and still is; no AC fixture uses a spec stem containing
`-gate-r<digit>`, so the wider exclusion removes nothing a RED expects to be a spec. AC24's
`-gate-verdict-r1.md` decoy stays excluded by its own rule.

## Step 2 - section 2 vs section 3 cross-check

r4 adds no new terminal in section 2. The two new section-4 rules map to existing paths: shallow
clone -> 2.1(b) merge-base failure (AC32 class); multi-`spec:` line -> 2.2 step 4 (AC36 neighbourhood,
no dedicated AC). The longest-prefix tie-break is a refinement of 2.2 step 2 already exercised by AC23
for stem-vs-prefix; prefix-vs-prefix has no AC (finding 3). No mismatch.

## Step 3 - RED adequacy

RED unchanged since r3 (68 tests); r3's Step 3 analysis holds: imports at collection succeed, the guard
is imported via `_import_guard()` after an `is_file()` assertion, every test fails at assert time
pre-GREEN, no UUT mocking, AC15/AC28 use the real hook. The r3 blocker (AC34 unsatisfiable by a
spec-faithful GREEN) is resolved by the spec change alone; a GREEN implementing the r4 text verbatim
passes both AC34 parts (simulation above). No RED/spec drift introduced by r4.

## Step 4 - reachability

Unchanged from r3 (commit refusal via `precommit_enforce.main` after `repo_root()`, before
`nothing_to_lint`; refusal lines from `green_entry_guard.check`; bypass log under
`<git-common-dir>/bytedigger/bypass.log`; bd66 copy fixture, tree-scan inventory, catalogs). r4 does
not move any point or host.

## Adversarial edges (not covered by the section 3 / 3b AC tables)

1. Fail-open by name (decoy-fence, new with r4): the wider exclusion `*-gate-r<digit>*.md` also drops a
   real spec whose slug happens to contain `-gate-r<digit>`, e.g. `2026-10-04-bd300-gate-r2-redesign.md`
   or `...-close-gate-r1-cleanup.md`. Such a spec is silently not checked (MINOR; naming is under the
   author's control, but it is the guard's only silent-allow by name besides Gate-exempt, which at
   least logs).
2. Prefix-vs-prefix tie (boundary): umbrella `<date>-bdN-gate-r1.md` APPROVED vs sub-lot
   `<date>-bdN-s1-gate-r1.md` REJECTED for spec `<date>-bdN-s1-<slug>.md` -> longest (s1) wins ->
   REJECTED. Now specified, not pinned by RED; a shortest-wins GREEN passes every RED.
3. Multi-`spec:` first block (section 4 bullet 2): a GREEN that reads only the first `spec:` line of the
   first block passes every RED (all fixtures carry one line per block) and false-STALEs the second spec
   of a shared-key lot. Fail closed only.
4. Carried from r3, still advisory: index/disk spec divergence (r3 edge 5), date-prefix cross-lot
   binding through generic 4-segment prefixes (r3 edge 6), exemption-only commit logging (r3 edge 8),
   global refusal-line shape `spec=-` (r3 edge 9), escalation file prefix form and disk-vs-index source
   (r3 edge 10).

## Findings

1. CLOSED - r3 MAJOR (AC34 vs literal exclusion): with r4's `*-gate-r<digit>*.md` exclusion both AC34
   fixtures yield exactly one refusal line (REJECTED naming r2; MISSING) and AC23/AC33 still match under
   the longest-prefix tie-break. No new MAJOR.
2. MINOR - Header (spec lines 5-6) still says "the one place every commit of a lot passes through";
   section 4 now qualifies it, so this is wording only.
3. MINOR - Longest-prefix tie-break between two prefix forms has no RED variant (edge 2); optional
   AC33 case: umbrella r1 APPROVED + s1 r1 REJECTED -> REJECTED.
4. MINOR - Section 4 bullet 2 is a normative lookup rule placed under "Out of scope" and unpinned by RED
   (edge 3); consider moving it into 2.2 step 4.
5. MINOR - New fail-open by name (edge 1): consider requiring the `-gate-r<digit>` token to be followed
   only by digits/dots/a single `-<word>` suffix, or accept and note it.
6. MINOR - Carried r3 advisories F4 (2.3 git-call enumeration) and F5 (3b table split at spec :173,
   AC23 RED comment "three-segment stem" for the four-segment `2026-10-03-short`).

Summary: the single r3 blocker is closed by the spec-only change; AC34 and AC23 re-simulate cleanly
against the r4 text and the unchanged RED, and AC33 is unaffected by the new tie-break. The r4 edits
introduced no contradiction and no RED/spec drift; the new section-4 bullets are consistent with 2.1(b),
2.2 and AC32/AC36. Remaining items are advisory.

VERDICT: APPROVED
