# bd#243 gate r3 - spec + RED audit (GREEN-entry gate-verdict guard)

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md sha256:3bd15e0f21efb4cd3365d617f1a27fba5d0a1fd9d547cc71b92e2657d1f9df42
-->

Audited (read-only):
- Spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md (r3 DRAFT)
- RED: engine_py/tests/test_bd243_green_entry_guard.py (68 tests, AC1-AC37)
- Previous verdicts: ...-gate-r1.md (REJECTED), ...-gate-r2.md (REJECTED, 3 MAJOR + 8 MINOR + 10 edges)
- Code read to verify claims: precommit_enforce.py (main :215-246, staged_paths :149-158, repo_root),
  precommit_lints.py (is_test_file :62, is_ts_test_file :66), verdict_verify.py (stdlib-only imports,
  ANCHOR_RE :34, ANCHOR_LINE_RE :35), error_codes.py (CODE_RE :26, harvest excludes `tests`,
  check :318), scripts/flag_owner_lint.py (rollout kinds flag/gate), conformance/tree_scan_lint.py
  (_GIT_KINDS, VALID_CLASSES), githooks/pre-commit, engine_py/core_manifest.json,
  test_bd66_precommit_enforcement.py (_materialize_package_copy :410-439, _init_repo), test_bd117b
  (own hooksPath, unrelated), test_gh1406 AC14 (skipped; gen_flag_catalog.py absent), and the
  live docs/decisions corpus (sub-lot, non-integer and non-spec names).

## r2 fold check

| r2 | Sev | Spec r3 | RED r3 | Status |
|---|---|---|---|---|
| F1 git listing failure -> UNREADABLE | MAJOR | 2.3 enumerates diff/merge-base/ls-files | AC31 corrupt index, live control first | CLOSED. Verified: rev-parse --verify and merge-base do not read the index; `git diff --cached` does and exits non-zero. A rc-ignoring GREEN gets empty output -> [] -> test fails. |
| F2 merge-base failure terminal | MAJOR | 2.1(b) now fail closed (UNREADABLE), consistent with 2.3 | AC32 orphan branch + no-base control | CLOSED |
| F3 sub-lot naming | MAJOR | 2.2 step 2 union over every >=4-segment prefix | AC33 x2 (s1 binds, s2 never binds, lot-level higher N wins) | CLOSED (prefix-vs-prefix tie unspecified, finding 3) |
| F4 error-code siblings | MINOR | 2.4 regen byte-identical + quoted literal | two AC17 tests | CLOSED. harvest_codes excludes `tests`, so the RED's own literals do not count. |
| F5 2.7 class wording | MINOR | `not-a-gate`, precedent cited | AC30 | CLOSED (the lint's own docstring says not-a-gate "never feeds a gate"; accepted on the r2 precedent) |
| F6 gate_exempt kind/write failure | MINOR | 2.6 lists 3 kinds, all refuse on write failure | AC19 gate_exempt test | CLOSED |
| F7 import list | MINOR | section 2 names verdict_verify + precommit_lints | - | CLOSED |
| F8 rev-parse probes vs 2.3 | MINOR | 2.1(b) states probes are not git failures | AC20, AC32 control | CLOSED |
| F9 HEAD == base tip | MINOR | 2.1(b) "no special case" | AC37 | CLOSED |
| F10 RED cosmetics | MINOR | - | docstring now AC1-AC37 | PARTIAL: AC23 comment still says "three-segment stem" for the four-segment `2026-10-03-short` (finding 5) |
| F11 fetch hint | MINOR | 2.2 step 2 `(base=origin/main; run git fetch if stale)` | AC21 asserts it | CLOSED |
| r2 edges 1, 4, 7, 10 | - | AC33, AC34, AC35, AC37 | yes | CLOSED |
| r2 edge 2 (multi-spec anchor) | - | first block only (AC36) | AC36 | PARTIAL, see edge 4 |
| r2 edges 3, 5, 6, 8, 9 | - | not addressed | - | still open, advisory (edges 7, 10 below) |

## Step 1 - spec-internal consistency (literal-token drift)

- Codes: the five `E_GREEN_GATE_*` literals are identical across 2.2-2.6, both AC tables and the RED. No drift.
- Env names `HAL_GREEN_GATE_GUARD` / `HAL_GREEN_GATE_BYPASS_REASON`: identical in 2.5, AC12/13/27/35 and RED.
- Bypass kinds `escalation|kill_switch|gate_exempt`: now identical in 2.1, 2.6 and RED.
- DRIFT (MAJOR, finding 1): the placeholder `<N>` is used in the 2.1 exclusion list (`*-gate-r<N>.md`)
  and defined in 2.2 step 2 as "a positive integer"; 2.2 adds "a revision that is not a plain positive
  integer (e.g. `r3.1`) is not a gate doc". Read literally, `<stem>-gate-r3.1.md` is then neither a gate
  doc nor excluded, so when added on a lot it IS a lot spec. AC34's RED assumes the opposite (see Step 1.5).
- Drift (MINOR, finding 4): 2.3 enumerates the git calls whose non-zero exit is UNREADABLE as
  `diff --cached --name-status`, `merge-base`, `ls-files`; 2.7 also lists gate docs via
  `git diff --cached --name-only`, and 2.1 reads the `Gate-exempt:` line from INDEX content (needs
  `git show :<path>` or `cat-file`), and 2.6 needs `rev-parse --git-common-dir`. None of these is in
  the enumeration; a non-zero exit is not an exception, so their failure mode is unstated.
- Cosmetic (MINOR, finding 5): a blank line (spec line 172) splits the 3b table, so AC31-AC37 render as a
  headerless table; section 2.7 sits after section 3.

## Step 1.5 - rule-overlap simulation

Order: [0 kill switch] -> (a) source -> (b) base probe / merge-base -> (c) lot specs (index, A,
--no-renames, minus exclusions / Gate-exempt) -> per spec: 1 escalation -> 2 gate docs (stem U
prefixes>=4 seg, integer N, max N, stem wins ties) -> 3 verdict -> 4 first anchor block -> 5 pass.

| AC | First matching branch | Outcome | Matches RED |
|---|---|---|---|
| AC1-AC30 | unchanged from r2 simulation | as r2 | yes |
| AC31 | (c): `git diff --cached` exits 128 on garbage index | UNREADABLE (1 line) | yes |
| AC32 | (b): main resolves, merge-base exit 1 | UNREADABLE; control (main deleted) -> [] | yes |
| AC33 | step 2: prefixes `...-s1-preflight`, `...-s1`, `...-bd218`; s2 is no prefix | [] / REJECTED / bd218 r2 beats s1 r1 / s2 never binds | yes |
| AC34 part 1 | (c): added = `widget.md`, `widget-gate-r2.md` (excluded), `widget-gate-r3.1.md` (NOT excluded under the literal 2.1/2.2 reading -> lot spec `2026-10-03-widget-gate-r3.1`) | spec widget -> r2 REJECTED; pseudo-spec -> prefix `2026-10-03-widget` -> r2 REJECTED (step 3 before 4) = TWO lines | NO: RED `_assert_single` demands exactly one |
| AC34 part 2 | same; only r3.1 present | widget MISSING + pseudo-spec MISSING = TWO lines | NO: RED demands one MISSING |
| AC35 | 0: value != "0" exactly | guard on -> REJECTED, no log | yes |
| AC36 | step 4: ANCHOR_RE.search returns first block only | STALE; reversed control -> [] | yes |
| AC37 | (c): spec staged with status A vs HEAD == base tip | MISSING | yes |

Overlap not decided by the spec:
- `-gate-r<non-integer>.md` sits between two classifiers (2.1 exclusion, 2.2 gate-doc recognition) that
  the spec defines with the same `<N>`; AC34's RED is only satisfiable if the exclusion is looser than
  the recognition (finding 1). The same gap hits the corpus file `-gate-r7-delta.md` (bd89-p3c).
- Equal N between two PREFIX forms (neither is the stem form), e.g. umbrella `<date>-bdN-gate-r1.md`
  and sub-lot `<date>-bdN-s1-gate-r1.md`: "on equal N the stem form wins" does not decide it (finding 3).

## Step 2 - section 2 vs section 3 cross-check

Every r3-introduced section 2 terminal now has an AC and RED: 2.1(b) merge-base fail-closed (AC32),
first commit / no HEAD shortcut (AC37), 2.2 prefix union (AC33), non-integer revision (AC34), first
anchor block only (AC36), fetch hint (AC21), 2.3 listing failure (AC31), 2.4 byte identity and quoted
literal (AC17 x2), 2.5 exact `0` (AC35), 2.6 gate_exempt write failure (AC19).
Inverse: every AC1-AC37 has a producing section 2 / 2.7 path.
Accepted as before: 2.3 "any exception" is not reachable without mocking; AC18 covers the realistic part.

No Step-2 mismatch. The blocking defect is spec-vs-RED (Step 1 / 1.5 / 3), not a missing terminal.

## Step 3 - RED adequacy

- 68 tests; module-level imports (`error_codes`, `precommit_lints`) exist; the guard is imported inside
  `_import_guard()` after an `is_file()` assertion, so nothing fails at collection.
- Pre-GREEN failure is at assert time for every test: guard-importing tests via `_import_guard`;
  AC15/AC28 rejected via `returncode != 0` (commit succeeds today); AC15/AC28 approved and AC30 via
  `GUARD_MODULE.is_file()`; AC17 tests via missing catalog entries / missing codes in
  `render_markdown()` / module absence.
- No test mocks the UUT. AC15/AC28 go through the real installer, `githooks/pre-commit`
  (`exec python3 scripts/precommit_enforce_cli.py`) and the real package via symlinks.
- New r3 tests are non-vacuous and carry live controls: AC31 (REJECTED before corruption), AC32
  (no-base -> []), AC33 (s1/s2/lot-level variants), AC35 (exact `0` control), AC36 (reversed blocks),
  AC19 gate_exempt (blocker removed -> exactly one gate_exempt line).
- DEFECT (MAJOR, finding 1): AC34 (both parts) asserts exactly one refusal line, which a GREEN that
  implements 2.1/2.2 literally (one `-gate-r(\d+)\.md$` predicate for both exclusion and recognition)
  cannot produce: the `-gate-r3.1.md` file becomes a second lot spec and yields a second line. A spec-
  faithful GREEN fails the frozen RED; a passing GREEN must invent an exclusion rule the spec does not
  state. Either outcome is the contract ambiguity this gate exists to stop.
- RED looser than spec (carried, harmless): AC17 accepts `kind in ("flag","gate")`, spec says `gate`.
- Cosmetic (finding 5): AC23 comment "A three-segment stem" describes `2026-10-03-short`, which has four
  segments; the assertion itself (3-segment file prefix does not bind) is valid.

## Step 4 - reachability

| Side effect | Point (prod) | Host | Test path |
|---|---|---|---|
| Commit refusal | new `green_entry_guard.check(root, staged_paths(root), os.environ)`; non-empty -> print, return 1 | `precommit_enforce.main`, after `repo_root()` (:223-225), before `nothing_to_lint` (:228) | AC15, AC28: `git commit` -> githooks/pre-commit -> scripts/precommit_enforce_cli.py -> main |
| Refusal lines | `check` return | `green_entry_guard.check` | AC1-AC14, AC16, AC18-AC27, AC29, AC31-AC37 in-process |
| Bypass log append | writer under `<git-common-dir>/bytedigger/bypass.log` | `check` | AC5, AC12, AC19 x3, AC24, AC27, AC29, AC35 via `_bypass_lines` |
| bd66 copy | `_materialize_package_copy` (bd66 :410-439) must add green_entry_guard.py + verdict_verify.py | fixture | AC30 subprocess |
| tree-scan inventory | keys for the guard's git-ls-files / git-diff-names sites | `tree_scan_inventory.json` | AC30 subprocess (bd94 test_ac6) |
| Catalogs | error_codes.py, 2x ERROR_CODES.md (byte-identical), flags_catalog.py | data | AC17 x5 |

Sibling suites verified by reading:
- bd66: every repo is `git init -b main` with no `docs/decisions` paths (0 grep hits) and no orphan or
  origin refs, so the guard returns [] at (b) (unborn main) or (c) (no added spec). Stays green once the
  copy fixture carries the two modules (AC30 runs it).
- bd81 calls only `registry_prepass`; bd166 AC27 stages only `x.test.ts` ((a) false); bd117b installs
  its own tracked hook, not ours.
- gh1591 AC13 / bd166 AC25 (dead / unregistered codes): covered by the quoted-literal and
  byte-identity AC17 tests; `harvest_codes` excludes `tests`, so the RED's literals neither help nor hurt.
- flag_owner_lint: `HAL_GREEN_GATE_GUARD` kind gate + owner + provenance is a rollout entry and passes;
  no sibling test pins the catalog size. gen_flag_catalog.py does not exist and its only test is skipped.
- core_manifest.json lists precommit_lints.py and verdict_verify.py, not precommit_enforce.py; the guard
  need not be added (no all-modules test found).
- Self-application: once wired, this lot's own GREEN commit is checked against
  `docs/decisions/2026-10-03-bd243-green-entry-guard.md`; the newest gate doc is this r3 file (stem
  form), carrying the anchor block above. A REJECTED r3 refuses GREEN by construction, as intended.

## Adversarial edges (not covered by the section 3 / 3b AC tables)

1. Merge-conflict commit (regression-shield): after `git merge origin/main` into a lot branch with
   conflicts, `git commit` runs pre-commit; merge-base(HEAD, origin/main) is still the old fork point,
   so every spec other lots landed on main since then counts as "added". Pre-bd243 gate docs carry no
   anchor -> STALE refusals for other lots' specs on a FRESH origin/main (AC21 only covers a stale one).
   Cheap remedy: drop added paths that already exist in the base tip tree (`git cat-file -e <base>:<path>`).
2. Unhooked entry paths (decoy-fence): a clean `git merge`, `git cherry-pick`, `git rebase` and
   `git commit --no-verify` do not run pre-commit (no pre-merge-commit hook is installed), so GREEN
   source can enter history without the guard. The header's "the one place every commit of a lot passes
   through" overstates the chokepoint; say "every `git commit` that runs pre-commit" and leave the rest to
   the out-of-scope CI re-check.
3. Prefix-vs-prefix tie at equal N (finding 3): umbrella `<date>-bdN-gate-r1.md` vs
   `<date>-bdN-s1-gate-r1.md`. Shortest-wins and longest-wins give different verdicts; neither is pinned.
4. Multi-spec anchor block: one first block with `spec:` lines for two specs of a shared-key lot.
   ANCHOR_LINE_RE.finditer supports it, but a GREEN that reads only the first `spec:` line passes every
   RED (all fixtures have one line per block) and false-STALEs the second spec.
5. Index/disk spec divergence: stage a spec edit, then restore the working copy to the approved bytes;
   the disk hash matches the anchor and the un-gated staged revision is committed with source. Needs a
   deliberate step; 2.3 chose disk knowingly. Noted, not blocking.
6. Date-prefix cross-lot binding: generic 4-segment prefixes such as `2026-10-03-bd` (all `bd-flip-*`
   specs) or `2026-10-03-s4` bind any same-dated `<prefix>-gate-rN.md` from another lot -> false
   STALE/REJECTED (fail closed only).
7. Corpus non-spec docs still not excluded: `-gate-r7-delta.md`, `-red-gate.md`, `-verdicts.md`,
   `-coordinator-accept.md`, `-*-receipt.md`. Each needs a `Gate-exempt:` line; the MISSING detail does
   not name that remedy (r2 edge 3, carried).
8. Exemption-only commit: a lot whose only added docs are `Gate-exempt:` notes makes (c) false; whether
   the gate_exempt line is still logged (and a log failure refuses) is unspecified.
9. Global refusal line shape: UNREADABLE from a git listing failure, BYPASS_NO_REASON and a kill-switch
   log failure have no spec; the `<CODE>: spec=<S> gate=...` format does not say `spec=-`.
10. Escalation file source and form (r2 edge 5, carried): disk vs index existence is unstated, and the
    prefix form (`<date>-bdN-s1-escalation.md`) is not recognised while gate docs are.
11. Shallow clone: `origin/main` resolves but merge-base fails -> every source commit UNREADABLE
    (AC32 class, different cause). Kill switch is the remedy; worth one sentence.

## Findings

1. MAJOR - Spec/RED contradiction on AC34 (Steps 1, 1.5, 3). 2.1 excludes `*-gate-r<N>.md` from lot specs
   and 2.2 defines `N` as a positive integer and says `r3.1` "is not a gate doc"; read literally,
   `<stem>-gate-r3.1.md` is an added lot spec, so both AC34 fixtures produce TWO refusal lines
   (part 1: REJECTED for the spec and REJECTED for pseudo-spec `2026-10-03-widget-gate-r3.1` via prefix
   `2026-10-03-widget` r2; part 2: two MISSING), while the RED asserts exactly one. A spec-faithful GREEN
   fails the frozen RED. Fix in the spec (no RED change needed): "Excluded: any name whose stem contains
   `-gate-r` followed by a digit (`*-gate-r<anything>.md`), integer or not; only plain positive-integer
   ones are gate docs." This also covers the corpus `2026-10-02-bd94-gate-r3.1.md` and
   `2026-10-03-bd89-p3c-gate-r7-delta.md`. Optionally add a RED line asserting the r3.1 file is not
   reported as a spec.
2. MINOR - Header chokepoint wording overstated (edge 2).
3. MINOR - Tie-break between two prefix forms at equal N is unspecified (edge 3). State "on equal N the
   longest prefix (stem form first) wins" and, if desired, add one AC33 variant.
4. MINOR - 2.3 git-call enumeration omits `diff --cached --name-only`, the index read for `Gate-exempt:`
   and `rev-parse --git-common-dir`; say "any non-zero git exit after a base resolves".
5. MINOR - Cosmetics: blank line splitting the 3b table (spec :172); 2.7 placed after section 3; AC23 RED
   comment "three-segment stem" for a four-segment stem.
6. MINOR - Edge 1 (merge-conflict commit false refusals) deserves either the base-tree exclusion or an
   explicit acceptance sentence next to the AC21 one.
7. MINOR - Edges 4, 8, 9: pin multi-`spec:`-line lookup, exemption-only logging, and the global refusal
   line shape (`spec=-`) in prose; RED optional.

Summary: all three r2 blockers are closed with live, non-vacuous RED (corrupt index, orphan branch,
sub-lot prefix union), and all eight r2 advisories except one cosmetic are folded. Sibling suites
(bd66, bd94, bd81, bd166, gh1591, flag_owner_lint) are compatible with the specified design, verified
against the code. One new blocking defect remains: AC34's RED cannot be met by a GREEN that follows the
spec's exclusion rule literally, because a `-gate-r3.1.md` file is then itself a lot spec. It is a
one-sentence spec fix; no RED change is required.

VERDICT: REJECTED
