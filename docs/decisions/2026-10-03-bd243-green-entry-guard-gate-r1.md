# bd#243 gate r1 - spec + RED audit (GREEN-entry gate-verdict guard)

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md sha256:c02416a66efa586cd36e02859c1364eb0e8248edd65821dd3b1c62ec45984b48
-->

Audited (read-only):
- Spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md (r1 DRAFT)
- RED: engine_py/tests/test_bd243_green_entry_guard.py (27 tests)
- Context: engine_py/bytedigger_engine/precommit_enforce.py, precommit_lints.py,
  engine_py/tests/test_bd66_precommit_enforcement.py, githooks/pre-commit,
  scripts/precommit_enforce_cli.py, scripts/install_git_hooks.py, scripts/flag_owner_lint.py,
  engine_py/bytedigger_engine/conformance/tree_scan_lint.py + tree_scan_inventory.json,
  engine_py/tests/test_bd94_engine_owned_paths.py, verdict_verify.py, error_codes.py,
  both ERROR_CODES.md, the docs/decisions/ corpus.

## Step 1 - spec-internal consistency (literal-token drift)

- Env names: `HAL_GREEN_GATE_GUARD`, `HAL_GREEN_GATE_BYPASS_REASON` - spelled identically in
  section 2.5 and AC12/AC13; the RED uses the same literals (KILL/REASON). No drift.
- Codes: the five `E_GREEN_GATE_*` codes are identical in sections 2.2, 2.3, 2.4, 2.5, 2.6 and the
  AC table; the RED uses the same five. No drift.
- Helper names cited: `precommit_lints.is_test_file` / `is_ts_test_file`, `verdict_verify.ANCHOR_RE` /
  `ANCHOR_LINE_RE`, `precommit_enforce.main`, `repo_root()` - all exist with the cited names.
- Soft drift (MINOR, finding 9): section 2 says the module is "stdlib-only" while section 1.3 points at
  `verdict_verify.ANCHOR_RE` as the anchor format. A GREEN that imports `verdict_verify` to reuse the
  regex is no longer stdlib-only and widens the package-copy breakage in finding 1.
- Soft drift (MINOR, finding 6): section 2.6 `"spec": <S or null>` does not say which kind gets null;
  the RED pins `kill_switch -> None`.

## Step 1.5 - rule-overlap simulation (per-spec dispatcher, section 2.2; applicability, 2.1; kill switch, 2.5)

Branch order simulated: [kill switch] -> (a) source path -> (b) lot spec -> per spec: escalation ->
gate docs exist -> verdict line -> REJECT -> anchor -> pass.

| AC | First matching branch | Outcome | Matches table |
|---|---|---|---|
| AC1 | r2 newest, verdict REJECTED (check 3 before 4) | REJECTED naming r2 | yes |
| AC2 | r1 APPROVED + anchor == disk sha | [] ; control edit -> STALE | yes |
| AC3 | APPROVED, anchor sha != disk | STALE | yes |
| AC4 | APPROVED, no anchor | STALE | yes |
| AC5 | escalation marker non-blank | pass + 1 log line; pre-marker control REJECTED | yes |
| AC6 | marker blank -> falls through | REJECTED / MISSING | yes |
| AC7 | no gate doc | MISSING, gate=- | yes |
| AC8 | r10 numeric newest | REJECTED ; mirror r10 APPROVED -> [] | yes |
| AC9 | r2 newest, no VERDICT line | UNREADABLE | yes |
| AC10 | (a) false | [] ; control adds src -> REJECTED | yes |
| AC11 | old spec is M, gate-r1 excluded by name | [] ; control new spec -> MISSING | yes |
| AC12 | kill switch + reason | [] + kill_switch log line | yes |
| AC13 | kill switch, blank reason | single NO_REASON, no checks | yes |
| AC14 | per-spec, no early exit | one/two lines as stated | yes |
| AC16 | `\s*$` absorbs `\r` | [] ; CRLF REJECT control -> REJECTED | yes |

Overlap not decided by the spec (MINOR, finding 7): the kill-switch branch vs the applicability
branch (a)/(b). Every AC12/AC13 fixture has a source path AND a lot spec staged, so both orders pass
the RED, yet they differ in production: with `HAL_GREEN_GATE_GUARD=0` exported in a shell and no
reason, a docs-only commit is refused under kill-first and allowed under applicability-first; with a
reason, kill-first writes a `kill_switch` bypass-log line on every commit, docs-only included.

## Step 2 - section 2 vs section 3 cross-check

Section 2 terminal / error paths with NO producing AC (REJECT per the gate rule):

1. Section 2.3: unreadable spec or gate file -> `E_GREEN_GATE_UNREADABLE`. No AC, no test.
2. Section 2.3: `git` failure while listing -> `E_GREEN_GATE_UNREADABLE`. No AC, no test.
3. Section 2.3: any exception after (a) is true -> `E_GREEN_GATE_UNREADABLE`. No AC, no test.
4. Section 2.6: bypass-log write failure -> `E_GREEN_GATE_UNREADABLE` (a bypass that cannot be
   recorded is refused). No AC, no test. A GREEN that swallows the write error and allows passes
   all 27 tests.
5. Section 2.1(b): "neither `origin/main` nor `main` resolvable -> no lot spec -> []" (a silent-allow
   terminal) and the `origin/main`-before-`main` preference. No AC, no test.

AC9 is the only test that reaches `E_GREEN_GATE_UNREADABLE`, and only through the "no VERDICT line"
sub-branch of 2.2 step 3. The fail-closed rules are what this lot is about (never silently allow);
none of them is pinned.

Inverse direction (every AC has a producing section 2 path): yes for AC1-AC17.

## Step 3 - RED adequacy

- Count: 27 collected tests (AC6 x2, AC13 x3 + control, AC14 x2, AC15 x2, AC16 x2, AC17 x4).
- Collect-time: the guard module is imported inside `_import_guard()` after an `is_file()`
  assertion; module-level imports (`error_codes`, `precommit_lints`) exist today. Every test fails at
  assert time pre-GREEN (AC17 on missing codes / flags; AC15-rejected on rc==0; AC15-approved on its
  final `GUARD_MODULE.is_file()`).
- Stub-passability: no test mocks the unit under test; all fixtures are real temp git repos; AC15
  goes through the real installer, the real `githooks/pre-commit` and the real
  `scripts/precommit_enforce_cli.py` (realpath resolves back to the real engine). Not vacuous.
- Positive controls present on AC2, AC5, AC9, AC10, AC11, AC12, AC16, which keep the allow-side
  assertions from being satisfied by a guard that always returns [].
- Gaps (see Step 2 for the MAJOR ones):
  - AC10 does not discriminate the directory exclusions of 2.1(a): every non-source fixture is
    already excluded by its file name (`test_*.py`, `*.test.ts`, `*.md`), so `docs/`, `tests/` and
    `__tests__/` are never the deciding rule (MINOR, finding 8).
  - AC17 asserts `kind in ("flag","gate")` for `HAL_GREEN_GATE_GUARD`, which the spec does not
    state. It is the right requirement (otherwise `flag_owner_lint.is_rollout` ignores the entry and
    "passes the lint" is vacuous), but the spec must say it (MINOR, finding 5).
  - AC16 with `trailing_newline=False` ends the file with `VERDICT: APPROVE` and no CR at all, so
    only the True case exercises CRLF; anchor lines are always LF (MINOR, edge list).

## Step 4 - reachability

| Side effect | Point (prod) | Host | Test path |
|---|---|---|---|
| Refusal on commit | new `green_entry_guard.check` call | `precommit_enforce.main`, after `repo_root()` (spec 2) | AC15: `git commit` -> githooks/pre-commit -> scripts/precommit_enforce_cli.py -> main |
| Refusal lines | `green_entry_guard.check` return | `check` | AC1-AC14, AC16 in-process |
| Bypass log append | guard log writer | `check` | AC5, AC12 via `_bypass_lines` (`git rev-parse --git-common-dir`) |
| Catalog / docs | error_codes.py, 2x ERROR_CODES.md, flags_catalog.py | module data | AC17 |

The chokepoint is reachable: in the AC15 fixture the staged set is `src/app.py` (text, so the plan is
non-empty), base `main` resolves, merge-base is the base commit, the spec and gate-r1 are listed as
added, gate-r1 REJECTED -> refusal; `cyrillic-prose-lint.py` and `one-sided-predicate-lint.py` exist at
the real repo root, so the pre-pass does not pre-empt the guard.

Reachability hazard (MINOR, finding 12): "before the lint plan" does not say before or after the
`nothing_to_lint` early return in `main`. Placed after it, a commit staging only binary source
(`.so`, `.whl`, images) never reaches the guard. AC15 cannot tell, because `src/app.py` is a text file.

Sibling surfaces that change when `main` gains the call (this is where the lot breaks):

- bd66 `_materialize_package_copy` (test_bd66_precommit_enforcement.py:410-439) copies ONLY
  `__init__.py`, `precommit_lints.py`, `precommit_enforce.py` (+ `scripts/precommit_enforce_cli.py`)
  into a temp package that the CLI puts first on sys.path. Once `precommit_enforce` imports
  `green_entry_guard`, `bytedigger_engine.green_entry_guard` does not exist in the copy:
  - `test_ac11_...` (line ~1189): the pre-pass passes, `repo_root()` resolves, then the guard import
    raises -> traceback rc=1, no `BD66-REFUSE-VIOLATION` -> FAILS (rc==1 holds by accident, token
    assertion fails). A lazy import inside `main` fails the same way, since the guard runs before the
    plan.
  - `test_ac6c_...` (line ~1433): with a module-level import the hook crashes before the pre-pass ->
    no `BD66-REFUSE-MISSING-DRIVER` -> FAILS. With a lazy import after the pre-pass it survives.
- bd66 in-process tests (main([]) on an UNBORN `main`, staged `logo.png`): `logo.png` IS a source
  path under 2.1(a) (not a test, not docs/tests, not *.md). They stay green only because `main` is
  unresolvable on an unborn branch. A GREEN that runs `git merge-base` first and treats its failure as
  "git failure while listing" (2.3) refuses with UNREADABLE and breaks bd66 AC12 (expects rc 0).
- bd94 `test_ac6_real_tree_passes_tree_scan_lint` (test_bd94_engine_owned_paths.py:969):
  `tree_scan_lint` scans every package `*.py`. `git diff --cached --name-status ...` is a
  `git-diff-names` site; any `glob`/`os.listdir`/`iterdir` used to enumerate `<stem>-gate-r*.md` is a
  site too. Each needs a key in `conformance/tree_scan_inventory.json`, otherwise the real-tree lint
  reports "no key for call site". The class is not obvious: `filters` needs a FILTER_NAMES reference
  (non-stdlib import, which the spec forbids), and `not-a-gate` is defined as "never feeds a gate, a
  commit or a hash", which this read set does. Precedent: `precommit_enforce.py::staged_paths` is
  `not-a-gate` ("the user's staging choice").

The spec scopes the lot as "one new module + one call site" (+ catalogs/docs). It authorises
neither the bd66 fixture edit nor the inventory edit. GREEN would have to widen scope without a
spec, or ship red siblings.

## Adversarial edges (not covered by the section 3 AC table)

1. Decoy-fence, naming convention: most recent lots name gate docs `<date>-<lot>-gate-rN.md`
   (bd101, bd103, bd107, bd139, bd145, bd147, bd150, bd152, bd155, bd158, bd164, bd165, bd166,
   bd168-170, bd182, bd195, bd206, bd211, bd218-s1..s5, ...), NOT `<spec-stem>-gate-rN.md`. Only the
   bd141 sub-lots use the spec-stem form. Under 2.2 step 2, a lot that follows the majority convention
   gets `E_GREEN_GATE_MISSING` on its first GREEN commit.
2. Decoy-fence, non-spec docs: lots add non-spec docs to docs/decisions (bd150 class-i-inventory,
   bd206 class-m-sites, bd218-s1 coordinator-accept next to preflight-rung, bd141 close-gate,
   bd139 phase6-single-reviewer, hal1551 gate-verdict-r1/r2). Each is a "lot spec" under 2.1 and
   demands its own gate doc + anchor or escalation file. `*-gate-verdict-rN.md` and
   `*-gate-rN-<suffix>.md` are not excluded either.
3. Regex boundary: `-gate-r0.md`, `-gate-r01.md`, `-gate-r1.md.bak`, `-gate-r1-response.md` (positive
   integer, "exactly") are not tested.
4. Regex multi-line spill: `^ESCALATION:\s*\S` applied to the whole text with re.M lets `\s*` cross
   the newline, so `ESCALATION:` followed by ANY non-blank next line passes as a valid marker. AC6
   fixtures put nothing after the blank marker, so they cannot catch it.
5. Rename detection: `git diff --cached --diff-filter=A` uses rename detection by default
   (`diff.renames`), so a new spec created by `git mv` or by copying an old doc that the lot also
   deletes shows as `R`, is not a lot spec, and the guard silently allows. Use `--no-renames`.
6. Disk vs index: lot specs are found from the INDEX, the spec hash is taken from DISK, gate docs are
   read from DISK. An approved working copy with a different staged spec passes; an untracked,
   never-committed gate doc counts as approval.
7. Stale base ref: `origin/main` is preferred over `main`. In a worktree whose `origin/main` was not
   fetched but which was rebased on a fresh local `main`, the merge-base is older and specs merged by
   OTHER lots show up as added. Their gate docs carry no anchor (section 1.3: none do today) ->
   STALE / MISSING for specs this lot never touched.
8. Commit on the base branch itself: on `main` with no `origin/main`, merge-base == HEAD, so a spec
   committed in an earlier local commit is no longer "added" and the guard is off.
9. Merge-base failure vs base unresolvable: unrelated histories, or an unborn HEAD on a non-main
   branch while `main` exists. Is this "no base" (silent allow) or "git failure" (UNREADABLE)?
   Not specified.
10. Repeated bypass: every commit attempt after an escalation adds one more `escalation` log line,
    including attempts a later lint refuses. Probably intended, but not stated.
11. `env` parameter scope: the RED's mapping carries only HOME/GIT_CONFIG_* (no PATH). If GREEN
    passes `env` as the git subprocess env, git lookup depends on the platform default path. The spec
    should say `env` is for flag lookup only.
12. CRLF on the anchor lines (whole-file CRLF gate doc) is untested. `ANCHOR_LINE_RE`'s `\s*$`
    tolerates it; a hand-rolled stdlib regex may not.

## Findings

1. MAJOR - Sibling breakage, bd66 package-copy fixture. `test_bd66_precommit_enforcement.py::
   _materialize_package_copy` copies only precommit_lints.py + precommit_enforce.py. Once
   `precommit_enforce.main` calls the guard, `test_ac11_*` fails (ModuleNotFoundError instead of
   BD66-REFUSE-VIOLATION), and `test_ac6c_*` fails too if the import is at module level. The spec
   neither authorises the fixture edit (copy `green_entry_guard.py`) nor lists bd66 as a sibling that
   must stay green. An `except ImportError: allow` fallback would violate 2.3. Fix: name the bd66
   fixture change in scope (and require the guard to stay single-file stdlib so the copy needs only
   one extra file), or choose another design, and state which.
2. MAJOR - Sibling breakage, bd94 tree-scan inventory. The guard's `git diff --cached --name-status`
   (and any glob/listdir of gate docs) are tree-scan sites. `test_bd94_engine_owned_paths.py::
   test_ac6_real_tree_passes_tree_scan_lint` fails until `conformance/tree_scan_inventory.json` has
   their keys. `filters` is unreachable for a stdlib-only module, and `not-a-gate` contradicts its own
   definition here. The spec must list the inventory edit and the class decision, with the
   `staged_paths` precedent cited if `not-a-gate` is chosen.
3. MAJOR - Step 2: fail-closed terminals without ACs. Section 2.3 (unreadable spec/gate file, git
   failure, exception after (a)) and section 2.6 (bypass-log write failure -> UNREADABLE) have no AC
   and no RED; nor does the 2.1(b) "no resolvable base -> []" silent-allow terminal or the
   origin/main-first preference. Add ACs plus RED, e.g. a gate doc with mode 000 or a directory named
   like a gate doc; `bytedigger/` under the git common dir as a regular file so the log append fails;
   a repo with no `main`/`origin/main`; a repo where `origin/main` and `main` give different merge-bases.
4. MAJOR - Section 1.3 premise vs the live corpus. "`*-gate-r*.md` ... is the real naming" holds for
   the glob but not for the stem binding 2.2 enforces: the majority convention is
   `<date>-<lot>-gate-rN.md`, which never equals `<spec-stem>-gate-rN.md` (adversarial edges 1-2).
   And docs/decisions routinely receives non-spec docs that 2.1 classifies as lot specs. As written,
   the guard will refuse GREEN on most future lots and on every lot that adds an inventory or
   acceptance doc. The spec must decide explicitly: accept the `<date>-<lot>-gate-rN` form (and say
   how a doc maps to its spec when a lot has several), or declare the rename a requirement and
   define what in docs/decisions is a spec (allow-list or marker), with decoy-fence ACs either way.
5. MINOR - AC17 RED requires `kind in ("flag","gate")` for HAL_GREEN_GATE_GUARD and a non-blank
   description for HAL_GREEN_GATE_BYPASS_REASON. Correct, but absent from section 2.5; add it to the
   spec (without the kind, `flag_owner_lint` skips the entry and "passes the lint" is vacuous).
6. MINOR - Section 2.6: state that `kill_switch` lines carry `"spec": null` (the RED pins it).
7. MINOR - Section 2.5 vs 2.1 order unspecified (Step 1.5). Pin it and add a docs-only + kill-switch
   AC.
8. MINOR - AC10 cannot catch a missing or over-broad directory exclusion. Add fixtures that only a
   directory rule excludes (`tests/helpers.py`, `tests/data.json`, `docs/diagram.svg`,
   `web/__tests__/util.ts`), plus one source path that merely contains "test" in a non-test position
   (`src/contest.py`) as a positive control.
9. MINOR - Section 1.3's `verdict_verify.ANCHOR_RE` vs "stdlib-only": say whether GREEN may import
   `verdict_verify` (it would break the bd66 copy fixture further) or must re-declare the regex.
10. MINOR - Adversarial edges 4, 5, 6: line-scoped ESCALATION match, `--no-renames`, and disk vs
    index for the hash and the gate-doc source. Specify, and pin at least edge 4 and edge 5 with RED.
11. MINOR - Adversarial edges 7-9 (base-ref selection): stale `origin/main`, committing on the base
    branch, merge-base failure. Specify the behaviour for each. Edge 9 is load-bearing for the bd66
    in-process tests (finding 1 context).
12. MINOR - Fix where the call sits relative to the `nothing_to_lint` early return in `main` (the
    spec says "before the lint plan"). Say "before `classify_staged`" and add an AC15 variant that
    stages only a binary source path.

Summary: the dispatcher logic in 2.2 is consistent, and the RED is non-vacuous, fails at assert
time and reaches the real commit chokepoint. The lot is rejected for two un-scoped sibling
breakages (bd66 copy fixture, bd94 tree-scan inventory), for fail-closed terminals with no AC, and
for a naming premise that does not match the live docs/decisions corpus.

VERDICT: REJECTED
