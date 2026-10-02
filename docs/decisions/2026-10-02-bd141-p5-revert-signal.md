# bd#141 item 5 (residue) — `reverted` signal for companion tuning

**Status: r1 DRAFT** · **Tier:** 2 (one engine prod `.py` edit, `companion_tune.py`, + docs; Option D) ·
**Class:** SYSTEMATIC ·
**Chokepoint:** `companion_tune._collect` — the one place anything turns maintainer actions after a
BD-built PR shipped into signals (bd#117 Part B). The new code is one named helper,
`companion_tune._revert_signal(ctx, row, window)`, called from `_collect` once per listed PR row; it
returns `(target_pr_row, actor) | None` and emits nothing itself (`record` stays the one writer).
**Side of the seam (decision 2026-07-26 §7.1):** engine. HAL keeps nothing.
**Source:** bd#141 item 5 (Warp №4: "a weekly tune-up driven by human corrections (fixes **and reverts**
after merge)"); bd#117 spec `2026-09-30-bd117-companion-tune-readiness-gate.md` §1v ("`pr_closed_unmerged`,
revert and review-comment signals" out of scope); start-gate spec `2026-10-02-bd141-start-gate.md` §4
("item 5's revert signals … next lots").

## §1 Problem (measured on `68a442c`)

1. `collect` knows two kinds: `_EVENT_KIND = {"ReopenedEvent": "reopened", "LabeledEvent"/"UnlabeledEvent":
   "relabeled"}` (`companion_tune.py:84`). `grep -n -i revert engine_py/bytedigger_engine/companion_tune.py`
   → 0 hits.
2. The strongest human correction — a maintainer reverting a merged BD-built PR — reaches nothing. GitHub's
   "Revert" button opens a PR titled `Revert "<title>"` whose body is the single line
   `Reverts <owner>/<repo>#<N>`; it is merged like any PR. The PR listing `collect` already makes
   (`_PR_FIELDS`, `companion_tune.py:70`, window `updated:>=<from>`) returns that PR, but nothing reads it.

## §2 Design

op-1 `_PR_FIELDS` gains `id` and `mergedBy` (both real `gh pr list --json` fields).

op-2 `_revert_signal(ctx, row, (frm, to))` returns `(target, actor)` iff ALL hold, else `None`:
  a. `row` is merged (`_is_merged`) and `mergedAt` parses (`_parse_time`) to a time inside `[frm, to]`;
  b. `row["id"]` is a non-empty str;
  c. `row`'s author is NOT a BD login (`_is_bd`) — BD reverting itself is not a human correction;
  d. the FIRST body line that, stripped, fully matches `^Reverts ([^\s/#]+/[^\s/#]+)#(\d+)$` names
     `<slug>` equal case-insensitively to `ctx.slug` (other lines are ignored; no match ⇒ `None`);
  e. the target `N` resolves: from `ctx.pr_cache` (window listing) else ONE `gh pr view N` via `_pr_info`.
     A `gh pr view` failure whose stderr matches `Could not resolve to a PullRequest` or `\b404\b` ⇒ `None`
     (a human typed a dead number; never UNAVAILABLE). Any other failure ⇒ UNAVAILABLE (exit 4), as today;
  f. the target is BD-built (`_is_built`) AND merged (`_is_merged`);
  g. `actor = mergedBy.login` exists (null `mergedBy` ⇒ `None`, no crash), is not in `tuning.bot_logins`
     (case-insensitive), and `_is_maintainer(ctx, actor)`.
  Check order: a, b, c, d, then g's bot/null test, then e, f, then the maintainer lookup — so a revert PR
  that fails a local check costs no `gh` call.

op-3 `_collect` loops over `pr_rows` (all of them, not only BD-built) and for each hit calls `record` with a
synthetic event `{"__typename": "RevertPR", "id": row["id"], "actor": {"login": actor}, "createdAt":
row["mergedAt"]}`, kind `reverted`, label `None`, `pr = target number`, `issue = None`,
`title = target["title"]`. `record`'s `action` becomes `None` for every kind except `relabeled` (today it
is `None` only for `reopened`). Signal shape is unchanged: `{id, kind, pr, issue, label, action, actor,
title, at}`, `id = "reverted:<revert PR node id>"`, `at = revert mergedAt` verbatim. Dedupe against tuner
PRs' `bd:tune` ids works unchanged.

op-4 `propose` is unchanged: `_load_signals` accepts any `kind`; `_prompt` and the PR body render the
generic `kind`, `#<pr>` and the PR URL (issue is `None`).

op-5 Docs: module docstring (`collect` paragraph), `docs/configuration.md` `collect` bullet (three kinds;
the exact `Reverts <owner>/<repo>#<N>` rule; direct-push reverts are not read), `CHANGELOG.md`
`[Unreleased]` / Added.

## §3 Acceptance criteria (RED file `engine_py/tests/test_bd141_p5_revert_signal.py`)

All through the real CLI (`scripts/companion-tune collect --out`) with the bd#117b fake-`gh` rig; the
written `signals.json` is the production side-effect (§1l). RED may extend the bd#117b fixture's
`REAL_FIELDS` with `mergedBy` and add a `pr view` failure switch (fixture-only, no assertion change).

- **V1** positive: BD-built merged PR 10; maintainer `alice` merges PR 40 (author `alice`, body
  `Reverts o/r#10`, `id` `PR_rev40`, `mergedAt` 2 days ago) ⇒ exit 0 and exactly one `reverted` signal
  `{id: "reverted:PR_rev40", kind: "reverted", pr: 10, issue: null, label: null, action: null,
  actor: "alice", title: "<PR 10 title>", at: <PR 40 mergedAt>}`.
- **V2** negatives (parametrized; each ⇒ exit 0, no `reverted` signal): (a) revert PR OPEN; (b) CLOSED
  unmerged; (c) merged 30 days ago (updated in window); (d) `mergedBy` in `bot_logins`; (e) `mergedBy`
  without write permission; (f) `mergedBy` null; (g) revert PR author is a BD login; (h) target authored by
  a human with a forged marker; (i) target BD-authored without the `bd:built` line; (j) target BD-built but
  CLOSED unmerged; (k) `Reverts x/y#10` (other repo); (l) inexact lines: `Reverts o/r#10 please`,
  `see Reverts o/r#10`, `Revert o/r#10`, `Reverts #10`; (m) revert PR `id` null.
- **V3** dead target: `Reverts o/r#999`, no PR 999 ⇒ exit 0, no `reverted` signal, other signals of the
  run still reported.
- **V4** `gh pr view` of the target fails with HTTP 500 ⇒ exit 4, one stderr line
  `E_COMPANION_TUNE_UNAVAILABLE …`.
- **V5** target outside the window listing ⇒ exactly one `gh pr view 10` and the V1 signal; target inside
  the listing ⇒ zero `gh pr view` calls for it.
- **V6** two `Reverts` lines (`#10` then `#11`, both BD-built merged) ⇒ one signal, `pr: 10`.
- **V7** dedupe: `reverted:PR_rev40` named in a genuine tuner PR's `bd:tune` line ⇒ not reported.
- **V8** no `gh` call for a revert PR that fails a local check (a–d, null/bot actor): with only V2(a)
  and V2(k) rows plus no BD-built PR, the `gh` log has no `pr view` and no `…/permission` call.
- **V9** `gh pr list` (window listing) `--json` carries `id` and `mergedBy`.
- **V10** propose renders it (guard; passes at RED — reddens if `_prompt`/PR body special-case kinds and
  drop unknown ones): a `reverted` signal yields a prompt line with its id, `reverted`, `#10` and the
  title, and the PR body line `- reverted https://github.com/o/r/pull/10 alice <at>`.
- **V11** docs: `docs/configuration.md` names `reverted` and `Reverts <owner>/<repo>#<N>`; `CHANGELOG.md`
  `[Unreleased]` mentions the `reverted` signal; the module docstring mentions `reverted`.

## §4 Declared limits

- Only GitHub's revert-PR convention. A revert pushed straight to the default branch (`This reverts commit
  <sha>.`), a revert folded into another PR, or a hand-written body without the exact line is not a signal.
- First matching line only (GitHub writes one); one signal per revert PR (`id` is the revert PR's node id).
- The revert PR must be updated inside the window to be listed — a merge inside the window always updates it.
- Advisory input to a model proposal; a human reviews the tuner PR. No new error codes.

## §5 Scope

In: `engine_py/bytedigger_engine/companion_tune.py`, `docs/configuration.md`, `CHANGELOG.md`,
`engine_py/tests/test_bd141_p5_revert_signal.py` (new), `engine_py/tests/test_bd117b_companion_tune.py`
(fixture only: `REAL_FIELDS += mergedBy`, a `pr view` failure switch), this spec.

### Files NOT in scope (§1v)
`readiness.py`, `skill_companion.py`, `workflows/*`, `error_codes.py`, `ERROR_CODES.md`,
`core_manifest.json`, `scripts/companion-tune`, prompts, HAL's tree. Review-readiness gate (item 6
residue), review-comment and `pr_closed_unmerged` signals, feeding signals into learnings / phase 7.

### Sibling tests (§1a) — must stay green
`test_bd117b_companion_tune.py` (whole file: b2 world has no `Reverts` bodies, so its exact-signal sets
must not move), `test_bd117a_readiness.py`, `test_bd116_skill_companion.py`. Full suite + delta vs the RED
baseline is the ship gate (§1r).
