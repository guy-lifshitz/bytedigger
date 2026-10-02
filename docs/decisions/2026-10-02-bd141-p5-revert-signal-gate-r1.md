# Gate r1: bd#141 item 5, the `reverted` signal (spec + RED)

Audited: `docs/decisions/2026-10-02-bd141-p5-revert-signal.md`, `engine_py/tests/test_bd141_p5_revert_signal.py`,
the fixture edit in `engine_py/tests/test_bd117b_companion_tune.py` (REAL_FIELDS l.289-290, `pr_view_500` l.317-318),
and the UUT `engine_py/bytedigger_engine/companion_tune.py` at 67d607b. Tier 2, round 1 of 3. Read-only audit; no
tests were run. The RED counts (28 failed, 1 passed) are the orchestrator's numbers. I checked that they add up:
29 test ids, and only V10 passes today.

## Step 1: is the spec consistent with itself?

- The helper name `_revert_signal`, the synthetic typename `RevertPR`, the kind `reverted`, the id form
  `reverted:<node id>` and the docs literal `Reverts <owner>/<repo>#<N>` are spelled the same way in §0, §2, §3
  and the RED file. No naming drift.
- Line-reference drift: §1.1 cites `_EVENT_KIND` at `companion_tune.py:84`, but it is at l.87. `_PR_FIELDS` at
  l.70 is correct. This is MINOR-1.
- Op-2 d can be read two ways. "The FIRST body line that fully matches … names `<slug>` equal …" can mean
  (i) take the first line that matches the regex, then require the slug, or (ii) take the first line that
  matches the regex AND has our slug. §4 ("first matching line only") points to (i), but the text does not
  pin it. This is MINOR-2.

## Step 1.5: rule-overlap simulation (op-2, check order a, b, c, d, g-bot/null, e, f, maintainer)

I traced every V2 case through the checks in order and recorded the first check that rejects it:

| case | first check that rejects it | does that check own the case? |
|---|---|---|
| a OPEN, b CLOSED | a (mergedAt null). g-null would also reject it, because the fixture nulls mergedBy | partly. A GREEN without the `_is_merged` test still passes (harmless: unmerged rows never have mergedBy on GitHub) |
| c merged 30d, updated 1d | a (window) | yes. A GREEN using updatedAt reddens |
| d ci-bot (has write) | g-bot | yes |
| e eve (read) | maintainer | yes |
| f mergedBy null | g-null | yes |
| g author bd-bot | c | yes |
| h, i, j | f | yes. Each changes one target property |
| k x/y | d | yes. A slug-blind GREEN resolves PR 10 and reddens |
| l1-l4 | d | yes. Unanchored search, an optional slug and `Revert` all redden |
| m id null | b | yes. Without b the run emits `reverted:None` and reddens |

Every V2 world also has the control revert PR 41 → `[reverted:PR_ctl]`. So every negative case has a positive
control and fails at RED for the right reason.

## Step 2: does every §2 path have an AC, and every AC a §2 path?

- The terminal and error branches of op-2 e have ACs: dead target → `None` (V3), any other failure → exit 4
  (V4), cache hit versus one `pr view` (V5).
- The null-`mergedBy` no-crash branch is V2f.
- The `record` change (`action=None` for every kind except relabeled) is pinned by V1 (`action: null`).
- Dedupe is V7. Schema compatibility with `_load_signals` (l.474-487 needs `kind, pr, issue, actor, title, at`)
  and with `_prompt`/`_pr_body` (l.542-547, l.614-616 are generic over kind; `issue=None` gives a pull link) is
  V10 plus V1.
- No orphan branch in either direction.

## Step 3: is the RED adequate?

- Every AC has at least one assertion that fails at assert time. The CLI runs as a subprocess and the UUT is
  never imported or mocked.
- V8 asserts its precondition first: the listing requests `mergedBy`. That keeps V8 from passing vacuously at
  RED.
- V10 is a declared pass-at-RED guard. It reddens if a GREEN filters kinds in `_prompt` or builds the link from
  `issue` without a guard.

Wrong GREENs that every test catches:
- looping only over BD-built rows (V1)
- using the revert PR's author as the actor (V2 d/e/f)
- using the revert PR's title (V1)
- last line wins (V6)
- mapping every `pr view` failure to `None` (V4)
- mapping a dead target to exit 4 (V3)
- skipping `pr view` when the target is not in the listing (V5-outside)
- calling `gh` directly and bypassing the cache (V5-inside)
- bypassing `record`'s dedupe (V7)
- doing the maintainer or `pr view` lookup before the local checks (V8)

Wrong GREENs that pass every test (none is a MAJOR):
- **G1:** a case-sensitive slug compare. Op-2 d says case-insensitive, and no test uses `Reverts O/R#10`.
  See MINOR-3.
- **G2:** a case-sensitive `bot_logins` compare. See MINOR-3.
- **G3:** "first *qualifying* line": keep scanning past a first line whose slug or target fails. See MINOR-2.
- **G4:** doing the maintainer lookup before e/f. This only costs an extra `.../permission` call; nothing
  asserts that a non-BD target costs no permission call. See MINOR-4.
- **G5:** making `_pr_info` itself return `None` on not-found. That changes the issue path (l.388), which
  shares the helper, and no test covers it. See MINOR-5.

## Step 4: reachability (§1y)

- **Point:** a new `record(...)` call in `_collect` (l.348-408), fed by `_revert_signal`.
- **Host:** `_collect`, reached from `main` (l.735).
- **Test path:** `scripts/companion-tune collect --out` → the `signals.json` it writes (`collect_ok`, l.69-73).
- **Docs ACs (V11):** they read the real files.

## GitHub convention realism

- The GitHub "Revert" button creates a PR titled `Revert "<title>"` with the one-line body
  `Reverts <owner>/<repo>#<N>`. Op-2 d matches that.
- `id` and `mergedBy` are real `gh pr list --json` fields.
- On a missing PR, `gh pr view` prints "GraphQL: Could not resolve to a PullRequest with the number of N."
  The fixture message at l.321 matches that, and it fits easily inside the `err[:200]` that `_gh_json` keeps
  (l.177).
- A number that belongs to an issue gives the same not-found message, so it correctly maps to `None`.

## Scope / §1v / siblings

- `core_manifest.json` lists file names only (l.16), so no update is needed.
- The b2 world in `test_bd117b` has no `Reverts` bodies and no `id`/`mergedBy` keys, so check b rejects those
  rows before any `gh` call. AC-B3's call-shape assertions (l.1144-1163) still hold: still two `updated:>=`
  lists, and no `comments`/`reviews` fields.
- The fixture edit only adds things: a field and a switch.
- The sibling docs tests (bd117b l.1804-1815, bd116, bd117a) only assert that certain strings are present, so
  the docs edits cannot break them.

## Adversarial edges (not covered by any §3 AC)

1. **Out-of-range target number.** `Reverts o/r#99999999999` (or `#0`) passes d. `gh pr view` then fails with
   a GraphQL invalid-Int error, which is neither the not-found text nor 404, so the run exits 4. Every weekly
   collect stays down until that PR leaves the window. That contradicts e's own intent ("a human typed a dead
   number; never UNAVAILABLE"). See MINOR-6.
2. **Decoy `\b404\b`.** Target `#404` plus a transient failure whose stderr echoes the number would be read as
   not-found and silently become `None`. Low likelihood, and `_is_maintainer` (l.303) already has the same
   behaviour. Advisory only.
3. **First line has a foreign or unqualified target, second line ours.** For example
   `Reverts x/y#3\nReverts o/r#10`, or `Reverts o/r#12` (not BD-built) followed by `Reverts o/r#10`. Under
   reading (i) this must give `None`. See G3 / MINOR-2.
4. **Slug case mismatch.** The push URL is `O/R` or the body says `O/R`. See G1.
5. **Window boundary.** mergedAt exactly at `frm` is included (`[frm, to]`, the same as `_signal_for`
   l.338). Not tested; advisory.
6. **Transferred or renamed repo.** The body names the old slug, so the revert is silently missed. Not listed
   in §4; advisory (add one line to §4).

## Findings

1. **MINOR-1 (spec accuracy).** §1.1 cites `companion_tune.py:84`; the line is 87.
   **Fix:** change to `:87`.
2. **MINOR-2 (spec ambiguity + test gap).** Op-2 d's "first line" rule can be read two ways, and V6 cannot
   tell the readings apart.
   **Fix:** say "the first line that matches the regex decides; if its slug ≠ `ctx.slug` or its target fails
   e/f ⇒ `None`, later lines are never consulted". Add a V6b case with body `Reverts x/y#3\nReverts o/r#10`
   (target 10 BD-built merged) ⇒ no `reverted` signal, plus the control.
3. **MINOR-3 (test gap).** Two case-insensitivity rules have no test: the slug in d and `bot_logins` in g.
   **Fix:** add a positive case with body `Reverts O/R#10` ⇒ the V1 signal, and a V2 case with mergedBy
   `CI-Bot` ⇒ no signal.
4. **MINOR-4 (test gap).** Nothing tests that the maintainer lookup comes after e/f.
   **Fix:** a world with a revert of a non-BD-built PR merged by `bob` (has write), and no other revert, should
   produce no `.../permission` call for `bob`.
5. **MINOR-5 (spec under-specified).** The design says "via `_pr_info`", but `_pr_info` raises UNAVAILABLE on
   any failure (l.236 → `_gh_json` l.175-177).
   **Fix:** pin that the dead-target test lives inside `_revert_signal`: catch the failure from `_pr_info`
   there and match it against the not-found patterns. `_pr_info`'s contract for the issue path (l.388) stays
   unchanged.
6. **MINOR-6 (robustness).** An out-of-range `N` makes the run exit 4 (edge 1).
   **Fix:** in op-2 d, require `1 <= N <= 2147483647`, else `None`, with no `gh` call. Add it as an
   l-variant: `Reverts o/r#99999999999` gives no `pr view` and no signal.

No MAJOR and no BLOCKER findings. The MINORs are advisory: fold them in before freeze, or record them as
known residue.

VERDICT: APPROVED
