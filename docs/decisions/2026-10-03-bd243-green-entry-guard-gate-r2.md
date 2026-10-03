# bd#243 gate r2 - spec + RED audit (GREEN-entry gate-verdict guard)

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md sha256:82f0865f0ba83c8ef5d40a298461efc4422fc362c65ca8d14a41c65f77500d6e
-->

Audited (read-only):
- Spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md (r2 DRAFT)
- RED: engine_py/tests/test_bd243_green_entry_guard.py (51 tests: 28 for AC1-AC17, 23 for AC18-AC30)
- Previous verdict: docs/decisions/2026-10-03-bd243-green-entry-guard-gate-r1.md (REJECTED, 4 MAJOR + 8 MINOR)
- Code read to verify claims: precommit_enforce.py (main, staged_paths, repo_root), precommit_lints.py
  (is_test_file, is_ts_test_file, classify_staged, nothing_to_lint), verdict_verify.py (imports,
  ANCHOR_RE, ANCHOR_LINE_RE), conformance/tree_scan_lint.py + tree_scan_inventory.json,
  error_codes.py (CODE_RE, harvest_codes, check, render_markdown), scripts/flag_owner_lint.py,
  flags_catalog.py, core_manifest.json, tests: bd66 (fixtures, _materialize_package_copy), bd94
  (test_ac6_*), bd81, bd166 (AC25, AC27), gh1591 AC13, gh514 AC6, engine_py/tests/conftest.py,
  and the live docs/decisions corpus (gate-doc and sub-lot naming).

## r1 fold check

| r1 | Sev | Spec r2 | RED r2 | Status |
|---|---|---|---|---|
| F1 bd66 package copy | MAJOR | 2.7 names the fixture edit (copy green_entry_guard.py + verdict_verify.py), module-level unwrapped import | AC30 runs bd66 in a subprocess | CLOSED. verdict_verify.py imports stdlib only (verified), so the copy is self-sufficient. |
| F2 bd94 tree-scan inventory | MAJOR | 2.7 names the inventory edit | AC30 runs bd94 test_ac6_real_tree_passes_tree_scan_lint | CLOSED for scope; the class wording is self-contradictory (finding 5, MINOR). |
| F3 fail-closed terminals | MAJOR | AC18 (unreadable file), AC19 (log write), AC20 (no base), AC21 (origin/main first) | 2+2+1+1 tests | PARTIAL. "non-zero git exit while listing -> UNREADABLE" still has no AC and no RED (finding 1). The new 2.1(b) merge-base-failure terminal has none either (finding 2). |
| F4 naming premise vs corpus | MAJOR | key form `<first-4-segments>-gate-rN.md`, exclusion list, `Gate-exempt:` line | AC23 x3, AC24 x2 | PARTIAL. The sub-lot convention (bd218-s1..s5, bd89-p*, bd141-p4d/p4e) is still unmatched (finding 3). |
| F5 kind for HAL_GREEN_GATE_GUARD | MINOR | 2.5 `kind` `gate` | AC17 | CLOSED |
| F6 kill_switch spec null | MINOR | 2.5 | AC12, AC27 | CLOSED |
| F7 kill switch vs applicability order | MINOR | 2.1 step 0, 2.5 | AC27 x2 | CLOSED |
| F8 directory exclusions | MINOR | 2.1(a) "at any depth" | AC26 (dir-only names + contest/latest/docsy controls) | CLOSED. Verified: is_test_file is basename-only, so every AC26 fixture is decided by the directory rule. |
| F9 verdict_verify import | MINOR | section 2 allows it | - | CLOSED |
| F10 ESCALATION single-line, --no-renames, disk vs index | MINOR | 2.2 step 1 `[ \t]*`, 2.1 `--no-renames`, 2.3 | AC29 x4, AC25 | CLOSED |
| F11 base-ref edges 7-9 | MINOR | 2.1(b) | AC21, AC22 | PARTIAL (edge 9 = finding 2) |
| F12 call site vs nothing_to_lint | MINOR | section 2 / 2.1 "before nothing_to_lint" | AC28 x2 through the real hook | CLOSED |

## Step 1 - spec-internal consistency (literal-token drift)

- Codes: the five `E_GREEN_GATE_*` literals are identical in 2.2, 2.3, 2.4, 2.5, 2.6, 2.7-adjacent ACs and the
  RED. No drift.
- Env names: `HAL_GREEN_GATE_GUARD`, `HAL_GREEN_GATE_BYPASS_REASON` identical in 2.5, AC12/13/27 and the
  RED. No drift.
- Bypass-log kinds (drift, MINOR, finding 6): 2.6 defines `"kind": "escalation"|"kill_switch"` and says
  "every escalation-marker pass and every kill-switch skip appends ONE JSON line"; 2.1 introduces a
  third kind `gate_exempt`, which AC24 and its RED pin. 2.6 neither lists it nor says whether a failed
  `gate_exempt` write refuses (AC19 covers only escalation and kill_switch).
- Import rule (soft drift, MINOR, finding 7): section 2 says "stdlib plus `bytedigger_engine.verdict_verify`",
  but 2.1(a) requires `precommit_lints.is_test_file` / `is_ts_test_file`. Both are in the bd66 copy, so it
  works, but the allowed-import list should name precommit_lints and forbid every other
  `bytedigger_engine` import (error_codes, engine_owned, ...), which would break the bd66 copy.
- 2.3 vs 2.1(b) (drift, MINOR, finding 8): 2.3 makes "a non-zero `git` exit while listing or resolving after
  (a) is true" UNREADABLE, excepting only the merge-base failure. `git rev-parse --verify origin/main`
  exiting non-zero is exactly how 2.1(b) learns "not resolvable -> try main -> none -> []". AC20 pins
  the 2.1(b) reading; 2.3 should except `rev-parse --verify` explicitly.
- 2.1(b) wording (MINOR, finding 9): "HEAD equal to the base tip (committing on the base branch itself)
  yields no added spec, hence []" is stated as a consequence, not a rule. It is false when the spec is
  staged in the same commit (status A against HEAD), and it is also the state of the FIRST commit on a
  fresh lot branch. A GREEN that implements it as a shortcut (`HEAD == base tip -> []`) silently allows
  a first lot commit that stages spec + source together; computing it naturally refuses. Pin which.
- 2.7 (contradiction, MINOR, finding 5): "class `not-a-gate` is wrong for a gate input", then "`filters`
  only if the lint demands it", then "class `not-a-gate` with a note citing this section where the class
  definition allows it". tree_scan_lint.check accepts any non-git-add site as `not-a-gate` with a non-blank
  note, and rejects `filters` unless the enclosing scope references a FILTER_NAMES member (only reachable
  by importing an engine_owned helper, which section 2 forbids and the bd66 copy cannot carry). So the
  only class that can pass AC30 is `not-a-gate`. Precedent exists:
  `verification_registry.py::head_registry_tampered::git-diff-names#0` (a tamper gate, `not-a-gate`,
  "bounded by explicit pathspecs") and `precommit_enforce.py::staged_paths::git-diff-names#0`. The spec
  should say "`not-a-gate`, note cites 2.7 and the head_registry_tampered precedent; pathspec bounded to
  docs/decisions/" and drop the contradicting clauses.
- RED cosmetic (MINOR, finding 10): module docstring still says "AC1-AC17"; `test_ac23_decoys_other_key_and_short_stem_do_not_bind`
  claims to test "a stem with fewer than four segments", but `2026-10-03-short` has exactly four
  segments (its key equals its stem). The "<4 segments" rule is tautological anyway (the first four
  segments of a shorter stem are the stem), so nothing real is missed, but the comment is wrong.

## Step 1.5 - rule-overlap simulation

Branch order: [0 kill switch] -> (a) source path -> (b) base ref / merge-base -> (c) lot spec (index, A,
--no-renames, minus exclusions / Gate-exempt) -> per spec: 1 escalation -> 2 gate docs (stem U key) ->
3 verdict -> 4 anchor -> 5 pass.

| AC | First matching branch | Outcome | Matches |
|---|---|---|---|
| AC1-AC16 | unchanged from r1 simulation | as r1 | yes |
| AC18 spec | step 4: hash of a directory -> exception | UNREADABLE | yes |
| AC18 gate | step 3: read of a directory listed in the index | UNREADABLE | yes |
| AC19 esc | step 1 pass -> log write into a regular file | UNREADABLE | yes |
| AC19 kill | 0: reason present -> log write fails | UNREADABLE | yes |
| AC20 | (b): neither origin/main nor main | [] ; control (main created) -> REJECTED | yes |
| AC21 | (b): origin/main = old_base -> other lot's spec is A, no gate | MISSING(other) ; control (origin/main deleted) -> [] | yes |
| AC22 | (b): merge-base == HEAD -> no A under docs/decisions | [] ; control on lot branch -> REJECTED | yes |
| AC23 | step 2 union; equal N -> stem wins | REJECTED r2 / [] / MISSING for bd244 key | yes |
| AC24 | (c): every decoy excluded by suffix; exempt doc excluded by its line | [] + 1 gate_exempt ; blank / line-26 exemption -> 2x MISSING | yes |
| AC25 | (c): --no-renames shows D + A | MISSING | yes |
| AC26 | (a) false via directory rule only | [] ; contest/latest/docsy -> REJECTED | yes |
| AC27 | 0 before (a) | [] + kill_switch line ; no reason -> NO_REASON | yes |
| AC28 | guard before nothing_to_lint in main | refused / control commits | yes |
| AC29 | step 1 regex `[ \t]*\S` does not cross the newline | REJECTED, no log ; tab control -> [] + 1 line | yes |

Overlap not decided by the spec: AC22's natural computation vs the shortcut reading (finding 9). No
other first-match ambiguity found.

## Step 2 - section 2 vs section 3 cross-check

Section 2 terminals with NO producing AC / RED:

1. 2.3 "a non-zero `git` exit while listing ... after (a) is true -> UNREADABLE". This was r1 F3 item 2 and
   is still unpinned. A GREEN that does `if rc != 0: return []` around `git diff --cached --name-status`
   or `git ls-files` passes all 51 tests, and that is exactly the silent allow 2.3 forbids. Testable
   without mocks: after arranging an approved or rejected lot, overwrite `<git-dir>/index` with garbage;
   `rev-parse --verify` and `merge-base` still succeed (no index read), `git diff --cached` and
   `git ls-files` fail -> expected `E_GREEN_GATE_UNREADABLE`. (MAJOR, finding 1)
2. 2.1(b) "A `git merge-base HEAD <base>` failure -> `[]`" is a NEW silent-allow terminal in r2 with no AC
   and no RED. Neither direction is pinned: a GREEN that maps it to UNREADABLE (the 2.3 default) also
   passes all 51 tests. Fixture: `git checkout --orphan x` with `main` existing (HEAD unborn ->
   merge-base fails), or a commit with unrelated history fetched as `main`. (MAJOR, finding 2)
3. 2.3 "any exception inside the guard after (a)" -> UNREADABLE: not reachable without mocking; AC18
   covers the realistic part. Accepted.

Inverse direction: every AC1-AC30 has a producing section 2 / 2.7 path. AC30 maps to 2.7.

## Step 3 - RED adequacy

- 51 tests collected; module-level imports (`error_codes`, `precommit_lints`) exist today; the guard is
  imported inside `_import_guard()` after an `is_file()` assertion. Every test fails at assert time
  pre-GREEN: AC17 on missing catalog entries, AC15/AC28 approved-controls and AC30 on their
  `GUARD_MODULE.is_file()` assertion, all others through `_import_guard()`.
- No test mocks the unit under test. AC15/AC28 go through the real installer, `githooks/pre-commit` and
  `scripts/precommit_enforce_cli.py` (symlinked to the real tree). AC30 runs the real sibling files.
- Positive controls on every allow-side AC added in r2 (AC18 restore, AC19 blocker removal, AC20 main
  created, AC21 origin/main deleted, AC22 lot-branch twin, AC24 blank/late exemption, AC26 contest/latest/
  docsy, AC27 no-reason twin, AC28 approved twin, AC29 same-line tab).
- Stub-passability: AC30 alone is passable by an empty, unwired `green_entry_guard.py`, but AC15/AC28
  force the wiring, so AC30 runs against the wired layer. Acceptable.
- RED is looser than the spec in one place: AC17 accepts `kind in ("flag","gate")`, the spec says `gate`.
  Harmless.
- Gaps: findings 1 and 2 (no RED for two section 2 terminals); finding 4 (error-code siblings).

## Step 4 - reachability

| Side effect | Point (prod) | Host | Test path |
|---|---|---|---|
| Commit refusal | new `green_entry_guard.check(root, staged_paths(root), os.environ)` call | `precommit_enforce.main`, after `repo_root()` (line 223), before `nothing_to_lint` (line 228) | AC15, AC28: `git commit` -> githooks/pre-commit -> scripts/precommit_enforce_cli.py -> main |
| Refusal lines | `check` return value | `green_entry_guard.check` | AC1-AC14, AC16, AC18-AC27, AC29 in-process |
| Bypass log append | log writer under `<git-common-dir>/bytedigger/` | `check` | AC5, AC12, AC19, AC24, AC27, AC29 via `_bypass_lines` |
| bd66 copy | `_materialize_package_copy` (bd66 test file :410-439) | fixture | AC30 subprocess |
| tree-scan inventory | `tree_scan_inventory.json` keys for the guard's git-diff-names / git-ls-files sites | data | AC30 subprocess (bd94 test_ac6_real_tree_passes_tree_scan_lint) |
| Catalogs | error_codes.py, 2x ERROR_CODES.md, flags_catalog.py | data | AC17 |

Verified: `repo_root() is None -> return 0` precedes the guard, so a non-repo commit never reaches it
(correct). bd66 in-process and hook tests run on an UNBORN `main`: `git rev-parse --verify main` fails ->
"none" -> [] before merge-base, so they stay green as long as GREEN follows 2.1(b) and not 2.3 (finding 8).
bd166 AC27 stages only `x.test.ts` (a test file -> (a) false). bd81 calls only `registry_prepass`.
`.git/bytedigger/` is not used by any existing engine code.

Sibling surfaces NOT covered by AC30 (finding 4):
- `error_codes.CODE_RE` harvests only QUOTED literals (`["']E_[A-Z0-9_]+["']`). `test_gh1591_fix_gate_boundary.py::test_ac13`
  asserts `error_codes.check(engine_root)` has no dead codes and `test_bd166_one_sided_predicate.py::test_ac25`
  asserts `error_codes.main(["--check"]) == 0`. If the guard builds its codes dynamically
  (`f"E_GREEN_GATE_{kind}"`) or only via constants defined in error_codes.py (excluded from harvest), the
  five new codes are DEAD and both siblings go red.
- Both ERROR_CODES.md copies must be byte-identical to `error_codes.render_markdown()`
  (test_bd166 AC25, test_bd8_l1_oracle.py:1302, test_bd163, test_GH1674, test_gh1626d). Spec 2.4 says
  "in the existing format", which a hand edit (wrong sort position, `-` instead of the em dash
  render_markdown emits) satisfies for the RED regex `^- \`CODE\` ` but breaks byte-identity. Precedent:
  bd107 gate r1 F2, bd165 gate r2 MINOR-A.

## Adversarial edges (not covered by the section 3 / 3b AC tables)

1. Sub-lot naming (decoy-fence, corpus): `2026-10-03-bd218-s1-preflight-rung.md` with
   `2026-10-03-bd218-s1-gate-r1.md`; same for bd218-s2/s3/s5, bd89-p1/p2a/p2b/p3a/p3b1/p3b1b-i/p3b1b-ii,
   bd141-p4d/p4e. The 4-segment key is `<date>-bd218` / `<date>-bd89` / `<date>-bd141`; neither form
   matches -> `E_GREEN_GATE_MISSING` while an approved gate doc exists. See finding 3.
2. Shared-key multi-spec lot: two specs `<date>-bdN-a.md`, `<date>-bdN-b.md` share `<date>-bdN-gate-rN.md`.
   The newest key doc must anchor BOTH specs, and `verdict_verify.ANCHOR_RE.search` reads only the FIRST
   `<!-- verdict-anchor -->` block; a gate doc with two separate blocks makes the second spec STALE.
   Say "one block, one `spec:` line per spec" or "all blocks".
3. Corpus non-spec docs not in the exclusion list: `*-red-gate.md` (bd91), `*-verdicts.md` (bd73),
   `<lot>-coordinator-accept.md` (bd218-s1). Each becomes a lot spec needing a gate doc or a
   `Gate-exempt:` line. Remedy exists; state it in the refusal detail.
4. Non-integer revision: `2026-10-02-bd94-gate-r3.1.md` is ignored by "N is a positive integer", so a newer
   r3.1 approval is invisible and r3 decides. Fail-closed only if r3 rejected; otherwise silently reads
   an older verdict.
5. Escalation file source: 2.2 step 1 says the file "exists"; disk or index is not stated. An untracked,
   never-committed `<stem>-escalation.md` on disk would bypass if read from disk. Gate docs and the spec
   list come from the index; the escalation marker should too. Key-form `<date>-bdN-escalation.md` is
   also not recognised.
6. Self-service exemption: a `Gate-exempt:` line in the first 20 lines of the lot's REAL spec removes it
   from the check. Logged, like escalation, but no distinction between a non-spec doc and a spec that
   exempts itself.
7. Kill-switch spellings: only `HAL_GREEN_GATE_GUARD=0` is defined; `false`, `off`, `no`, empty are unpinned.
8. `env` scope (r1 edge 11, unfolded): the RED mapping carries no PATH and the RED reads KILL/REASON only
   from the mapping (os.environ is cleared). If GREEN passes `env` as the subprocess env, git resolves via
   `os.defpath`. Say `env` is for flag lookup only.
9. Repeated log lines: `gate_exempt` and `kill_switch` lines are appended on EVERY commit attempt,
   including attempts a later lint refuses. Probably intended; not stated.
10. Fresh lot branch, first commit stages spec + source together (HEAD == base tip): see finding 9.

## Findings

1. MAJOR - Step 2: 2.3 "non-zero git exit while listing -> E_GREEN_GATE_UNREADABLE" has no AC and no RED
   (r1 F3 item 2, not folded). A silent-allow GREEN passes all 51 tests. Add an AC + RED: corrupt
   `<git-dir>/index` after arranging a lot; expect exactly one `E_GREEN_GATE_UNREADABLE`, and a restored
   index as positive control.
2. MAJOR - Step 2: the r2-introduced 2.1(b) terminal "merge-base failure -> []" has no AC and no RED, and
   collides with 2.3's default (UNREADABLE). Add an AC + RED (orphan branch with `main` present, or
   unrelated histories) expecting `[]`, with a control that the same docs on a related branch refuse.
3. MAJOR - r1 F4 partially folded: the 4-segment key does not cover the sub-lot convention used by 13 of
   the recent lots (bd218-s1/s2/s3/s5 dated today, bd89-p1..p3b1b-ii, bd141-p4d/p4e). Each would get
   `E_GREEN_GATE_MISSING` with an approved gate doc on disk. Decide explicitly, e.g. "gate docs = union over
   every dash-prefix of the stem with at least four segments (`<prefix>-gate-rN.md`); on equal N the
   longest prefix wins", and add a RED with `<date>-bdN-s1-<slug>.md` + `<date>-bdN-s1-gate-r1.md`
   (APPROVED + anchor -> [], REJECTED -> refused), plus a decoy `<date>-bdN-s2-gate-r1.md` that must not
   bind the s1 spec. Or state that sub-lots must name gate docs by stem and accept MISSING, citing the
   corpus count.
4. MINOR - Error-code siblings outside AC30: require in 2.4 that both ERROR_CODES.md are regenerated from
   `error_codes.render_markdown()` (byte-identical) and that each code appears as a bare quoted literal in
   green_entry_guard.py (harvest; otherwise DEAD). Optionally extend AC17 RED with
   `error_codes.check(PACKAGE_DIR)` having none of the five in `dead`/`unregistered` and byte-equality to
   `render_markdown()`.
5. MINOR - 2.7 class wording contradicts itself; state `not-a-gate` with the head_registry_tampered
   precedent and a docs/decisions pathspec bound.
6. MINOR - 2.6 does not list the `gate_exempt` kind and its write-failure policy.
7. MINOR - Section 2 import list omits precommit_lints and does not forbid other package imports.
8. MINOR - 2.3 must except `git rev-parse --verify` base probing from "non-zero git exit while resolving",
   or AC20 and the bd66 unborn-main tests contradict it.
9. MINOR - 2.1(b) "HEAD equal to the base tip ... hence []" must be either a rule or removed; pin the
   fresh-branch "spec + source in the first commit" case.
10. MINOR - RED cosmetics: stale "AC1-AC17" docstring; the AC23 short-stem comment describes a branch the
    fixture does not exercise.
11. MINOR - AC21 pins a false refusal (a stale `origin/main` makes other lots' merged specs count as added).
    Accepted as a design choice, but the refusal detail should tell the user to fetch, and the spec should
    say so.

Summary: 9 of 12 r1 findings are closed, including both sibling-breakage MAJORs (bd66 copy, bd94
inventory), and the r2 RED is non-vacuous, fails at assert time and reaches the real hook. The lot is
still rejected: two section 2 terminals (git listing failure -> UNREADABLE, merge-base failure -> [])
have no AC or RED, so a GREEN can implement either direction and stay green; and the gate-doc binding
still refuses the sub-lot naming convention that a third of the recent corpus uses (r1 F4 residual).

VERDICT: REJECTED
