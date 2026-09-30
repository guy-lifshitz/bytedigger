# bd#131 — `ship.sh` ships commits ahead of base, and the PR carries the review evidence

**Status: FROZEN (r4, after gate r2 REVISE + code-review r1)** · **Class:** SHIP path · **Chokepoint:** `scripts/ship.sh`, the one place the
plugin commits, pushes and opens the PR; its title and body come from one new helper,
`scripts/ship_pr_text.py`.

Source: first end-to-end `/bytedigger:build` pilot (bd#119 → PR #126, lot-360; evidence
`SHARED/notes/2026-09-30_bd-pilot/`). The pilot shipped by hand.

## §1 Problem (measured on `e26bc3e`)

1. **Only `files_modified` is staged.** `scripts/ship.sh:93` reads the list from
   `build-state.yaml`. No phase keeps that list up to date; the pilot's final state had
   `files_modified: []`.
2. **Atomic commits leave nothing to stage, and ship exits before the push.** COMPLEX runs make
   RED/GREEN commits by default. With nothing staged and readiness not `required`,
   `ship.sh:167-176` prints "No files to commit (all excluded as sensitive)" and exits 0 before
   `git push`. No branch, no PR, and Phase 7 treats SHIP as best-effort success. The bd#117 resume
   rule (`_ahead_of_base`) pushes past this only under `required: true`.
3. **PR text is the raw task string.** `ship.sh:183` commits with `-m "$TASK"` and `:203` opens
   the PR with `--title "$TASK"` and the fixed body "Built via ByteDigger /build pipeline.". In the
   pilot `task` was a 250-character paragraph. None of the review evidence (plan review, Opus
   test validation, reviewer verdicts, satisfaction, scope, follow-ups) reaches the PR.
4. **Leftovers are not ignored.** `.gitignore` lists `build-plan-review.md` and
   `build-opus-validation.md` but not the per-cycle copies (`build-plan-review-cycle1.md`,
   `build-opus-validation-cycle1.md`), nor `.bytedigger-sessions.json` written by
   `scripts/pre-build-gate.sh` (Phase 0.5). Phase 7 State Cleanup deletes neither, so the
   session file survives every build.

## §2 Design

### op1 — what gets staged

**Detached HEAD / unmerged paths.** Right after the `--pr` gate and **before** the readiness check
(so a refusal does not consume an approval — gate r2 m2), with no git mutation:
- `git branch --show-current` empty → stderr `ERROR: detached HEAD — nothing shipped`, exit 1;
- `git ls-files -u` non-empty (merge/rebase conflict) → stderr
  `ERROR: unmerged paths — nothing shipped`, exit 1 (gate r2 m1: discovery's `git add -A` would
  otherwise mark conflicted files resolved, markers included).

After the readiness gate and branch creation (both unchanged), ship.sh stages:

- every path in `files_modified` (unchanged behaviour), plus
- every tracked path with an unstaged change in the working tree (modified or deleted), read
  NUL-delimited and unquoted: `git -c core.quotePath=false diff --name-only -z` (gate r1 M1 — the
  default output quotes non-ASCII paths and splits on newlines). Each is staged with
  `git add -A -- <path>` so a deletion is staged too. Discovered paths print one stdout line each:
  `STAGE (tracked change): <path>` (gate r1 M2, option a).

Every candidate goes through `_is_sensitive` first (unchanged patterns, applied to the unquoted
path); a sensitive path prints `SKIP (sensitive): <path>` and is not staged. Untracked files are
staged **only** when listed in `files_modified` — never by discovery, so build leftovers and stray
secrets in a target project (where `build-*.md` is not gitignored) stay out. The index is not
reset: a path staged before ship.sh ran stays staged, and a staged path modified again is
restaged by discovery like any other tracked change.

**Untracked-not-shipped warning** (gate r1 M3). After staging, when
`git ls-files --others --exclude-standard -z` lists paths that are not staged — ignoring BD's own
leftovers, basenames `build-*` and anything under or named `.bytedigger*` (gate r2 m7) — ship.sh prints one
stderr line `WARNING: untracked files not shipped: <p1>, <p2>, …` (at most 20 paths, then
`… (+N more)`). It stages nothing and does not change the exit code — a build that created files
without committing them or listing them in `files_modified` is visible, not silently partial.

### op2 — when to push

`_ahead_of_base` becomes the push rule for every readiness mode (the `READINESS_REQUIRED`
condition on it is removed; `READINESS_REQUIRED` is no longer read by ship.sh).

Base resolution, first that resolves (`git rev-parse --verify -q`):
1. `@{upstream}`
2. `refs/bd/policy` (force-fetched by every readiness check that reads a policy)

(The `origin/HEAD` / `origin/main` fallbacks of the r0 draft are dropped: unreachable once
readiness has run — gate r1 m1.)

Flow after staging:
- something staged → commit (op3), then push.
- nothing staged, a base resolved and `git rev-list --count <base>..HEAD` > 0 → push, no commit.
- otherwise → stderr `WARNING: nothing to ship — no changes to commit and HEAD is not ahead of
  <base>` (`<base>` is the resolved ref text, `@{upstream}` or `refs/bd/policy`, or
  `any base (none found)`), exit 0, no push, no PR, `build-state.yaml` not touched (Phase 7
  step 2 then sees no `ship_complete: true`).

Push, PR creation (best-effort), and the `ship_complete` / `ship_pr_url` write are unchanged.

### op3 — title and commit subject

`python3 scripts/ship_pr_text.py title --state <state> [--repo <dir>] [--base <ref>]` prints one
line. ship.sh calls it once, after staging and before committing, passing the base from op2 when
one resolved, and uses the result as **both** the commit subject and the PR title. When ship.sh
commits and `task` differs from the title, the commit gets `task` as its body
(`git commit -m "$TITLE" -m "$TASK"`).

Title rules, in order:
1. **Source.** The first line of the spec, outside fenced code blocks (lines between ```` ``` ````
   or `~~~` fences are skipped), that starts with `# ` (H1), text after `# `. The spec is the
   state's `spec_path` (quotes stripped), resolved against the state file's directory when
   relative; default `build-spec.md` next to the state file. A spec path whose basename matches
   ship.sh's sensitive patterns (`.env`, `.env.*`, `*.env`, `*.env.*`, `*.pem`, `*.key`,
   `*.credentials*`), or that is not a regular file, is treated as **no spec** for title and body
   (gate r1 m4). No spec, no H1, or an H1 empty after normalising → `task` (quotes stripped, as
   ship.sh reads it today; empty → `unnamed-build`).
2. **Normalise.** Control characters → space, whitespace runs → one space, trimmed.
3. **Project convention.** If `--base` is given and **every** commit subject in `<base>..HEAD` has
   the form `<P>: …` with the **same** `<P>` (`<P>` = one or more non-space, non-colon characters,
   e.g. `bd#119`, `feat`, `fix(api)`), and there is at least one such commit: when the candidate
   starts with `<P>` followed by `:`, space, `—`, `–` or `-`, that prefix and the separator run
   (`[\s:—–-]+`) are removed; then the title is `<P>: <rest>`. No commits, mixed prefixes, or no
   `--base` → no prefix change.
4. **Length.** Longer than 72 characters → cut to at most 72 at the last space, trailing
   punctuation `,;:—–-` and spaces trimmed. A single word longer than 72 → hard cut at 72.

Pilot example: H1 `bd#119 — One bounded, fail-closed reader for …`, commits `bd#119: …` →
`bd#119: One bounded, fail-closed reader for …` (cut at 72).

### op4 — PR body

`python3 scripts/ship_pr_text.py body --state <state>` prints the body. Sections appear in this
order, each **only** when it has at least one line; a line appears only when its field is present
and non-empty:

```
## Scope

<verbatim spec sections whose heading text matches /\bscope\b/i>

## Review

- Plan review: <plan_review> (<plan_review_cycles> cycles)
  - <each plan_review_concerns item>
- Test validation (Opus): <opus_validation> (<opus_validation_cycles> cycles)
- Reviewers: <phase_6_reviewer_verdicts>
- Satisfaction: <review_satisfaction> (<phase_6_satisfaction>)

## Follow-ups

- <each follow_ups item>
- <each pre_existing_findings item>
<verbatim spec sections whose heading text matches /follow-?ups?/i>

Built via ByteDigger /build pipeline.
<!-- bd:built -->
```

- **Parenthetical parts** (`(<n> cycles)`, `(<phase_6_satisfaction>)`) are dropped when that field
  is absent; the line then ends at the verdict.
- **Spec sections.** A section is the heading line (any level, `#` to `######`) plus everything up
  to the next heading of the same or a higher level (fewer or equal `#`). Lines inside fenced code
  blocks are never headings (they stay part of the section they are in). The H1 itself is never a
  section. Copied verbatim, in spec order, separated by one blank line.
- **Children of an absent parent.** `plan_review_concerns` bullets appear only under a
  `Plan review` line; without `plan_review` they are dropped. `phase_6_satisfaction` appears only
  as the parenthetical of `Satisfaction`; without `review_satisfaction` it is dropped.
- **State parsing.** Line-based over top-level keys (`^key:` at column 0); the **last** occurrence of
  a key wins (the pilot state repeats `gate_bypass`). A scalar value has one level of matching
  surrounding quotes stripped. A value starting `[` is a list: parsed as JSON when valid, otherwise
  the text inside the brackets split on `,` and each item stripped of whitespace and quotes. A key
  with an empty value followed by `  - item` lines is a list of those items. Maps
  (`{a: PASS, b: PASS}`) are rendered as the text inside the braces. An empty list is absent.
- **Marker safety.** Any line of copied content (spec sections and state values) that contains
  `<!-- bd:` is dropped whole (not stripped of the marker), so the provenance marker (`readiness.BUILT_MARKER`) and the bd#117
  record / consumption markers can only appear where BD writes them: the marker is the last line
  and appears exactly once.
- **Size** (gate r1 m2). The body is at most 60 000 **UTF-8 bytes** (below GitHub's 65 536-char
  limit and far below the per-argument argv limit). When over, content is cut in this order, whole
  lines from the end of each: spec Follow-ups sections, then Scope sections, then state follow-up
  items (`follow_ups`, `pre_existing_findings`), then `plan_review_concerns` bullets; one line
  `(truncated; see build-spec.md)` marks each cut section. The other Review lines, the fixed line
  and the marker are never cut; each state value rendered on one line is itself capped at 2 000
  bytes (cut, ` …` appended).
- **No evidence at all** (no spec sections, no review fields, no follow-ups) → exactly today's two
  lines (bd#117 AC-B1 stays green unchanged).

### op5 — failure of the helper

`ship_pr_text.py` never fails the ship. Each of the two calls is checked on its own (gate r1 m3):
- `title`: exit ≠ 0, or empty output → title/subject = `task`; otherwise only the first line of
  the output is used.
- `body`: exit ≠ 0, empty output, or a last line that is not exactly `<!-- bd:built -->` → the
  two-line body.
- Any fallback prints stderr `WARNING: ship_pr_text failed — using the task string` once per
  run (at most one line, however many calls fall back — gate r2 M1). The helper's own stderr is discarded (never forwarded: no traceback text).

The helper needs Python ≥ 3.9 (same floor as `readiness`). The helper
is stdlib-only Python 3, reads files as UTF-8 with `errors="replace"`, runs git with `cwd=<repo>`
(default `.`), and treats a failing `git log` as "no commits" (no prefix change).

### op6 — leftovers

- `.gitignore`: add `build-*-cycle*.md` and `.bytedigger-sessions.json`.
- `phases/phase-7-synthesize.md` State Cleanup: add "Delete `build-*-cycle*.md` from CWD" and
  "Delete `.bytedigger-sessions.json` from CWD" (both "if it exists"; both kept when the pipeline
  FAILED or stopped awaiting approval, like the rest of State Cleanup).
- `phases/phase-7-synthesize.md` §7.5 and `commands/build.md` 7.1c: one sentence each — ship.sh
  ships every commit ahead of the base plus tracked changes, and writes the PR title/body from the
  spec and the review fields (no hand-maintained file list needed).

### op7 — docs

`docs/plugin.md` scripts table: row for `scripts/ship_pr_text.py`, plus a short "What ship.sh ships" paragraph with the §3 accepted risks (tracked changes whatever made them, `STAGE` lines, upstream-first re-run). `CHANGELOG.md` Unreleased:
one `### Fixed` entry.

## §3 Scope

**IN:** `scripts/ship.sh`, new `scripts/ship_pr_text.py`, `.gitignore`,
`phases/phase-7-synthesize.md`, `commands/build.md`, `docs/plugin.md`, `CHANGELOG.md`, tests.

**OUT:** engine `phase_8_post_deploy._ship_to_pr` (its own ship path, body derived from commits
already); keeping `files_modified` up to date in the phases; unstaging sensitive paths that were
staged before ship.sh ran, or sensitive files inside commits already made; the legacy
`tests/ship-protocol.bats` (not run in CI, mocks git without `rev-parse`/`rev-list`; left as is);
an existing open PR for the branch (`gh pr create` failing stays best-effort); merge / rebase in
progress without conflicts (the commit proceeds as git decides).

**Accepted risks, documented in `docs/plugin.md` next to the helper row** (gate r1 M2, m11):
- Tracked changes are shipped **whatever made them**: edits to tracked files that were in the
  checkout before the build (a pasted key in `settings.py`, a token in `.npmrc`) are committed and
  pushed unless `_is_sensitive` matches. Each is announced by a `STAGE (tracked change):` line.
  FEATURE/COMPLEX are forced into a worktree by the pre-build gate; SIMPLE/TRIVIAL on a dirty
  checkout are the exposure. Recording the dirty set at Phase 0 is a follow-up.
- `@{upstream}` is tried first: after a kill between push and `pr create`, a re-run sees nothing
  ahead and opens no PR (open it by hand); the title prefix only sees unpushed commits.
- Copied spec sections are Markdown on GitHub: closing keywords (`Closes #N`) and `@mentions` in
  them take effect.
- Submodule pointer changes count as tracked changes; a directory listed in `files_modified` is
  staged recursively (as today).
- Of the body's state fields, the phases write `plan_review`, `plan_review_concerns`,
  `opus_validation` and `review_satisfaction` today; the others (`*_cycles`,
  `phase_6_reviewer_verdicts`, `phase_6_satisfaction`, `follow_ups`, `pre_existing_findings`)
  appear when the orchestrator records them (the pilot did). Their lines are simply absent
  otherwise.

**Supersedes:** bd#117 AC-A12a row 4 ("`required: false`, nothing staged, 1 ahead → exit 0, no
PUSH") — now PUSH and one `pr create`. Test
`test_ac_a12a_required_false_keeps_todays_exit_0_without_push` is inverted (same file, renamed
`…_required_false_now_pushes_when_ahead`).

## §5 Code-review amendments (r4)

Found by `/code-review high` on the GREEN diff; each gets a RED row.

- **CR1 — repo root.** Right after the `--pr` gate ship.sh resolves `--state` to an absolute
  path, then `cd "$(git rev-parse --show-toplevel)"`. Every git path read (`diff`, `ls-files -u`,
  `ls-files --others`) and every `git add` is then relative to the root. Not inside a work tree →
  stderr `ERROR: not inside a git work tree — nothing shipped`, exit 1.
- **CR2 — all read-only refusals before readiness.** Missing state file, invalid `task`
  (newline/backtick), detached HEAD, unmerged paths and CR3 all run before the readiness check, so
  none of them consumes an approval.
- **CR3 — half-shipped moves.** Before readiness (read-only): when discovery would stage at
  least one **deletion** of a tracked path and the untracked-not-shipped set (op1 warning set, BD
  leftovers excluded) is non-empty, stderr `ERROR: tracked deletions with untracked files not
  shipped: <paths> — commit them or list them in files_modified`, exit 1. A deletion with no
  untracked files, or untracked files with no deletion, keeps the op1 behaviour (warning only).
- **CR4 — marker filter after normalising.** The `<!-- bd:` line filter is applied to the rendered
  one-line text, and additionally matches `<!--` followed by any whitespace then `bd:`
  (regex `<!--\s*bd:`), on spec lines and state values alike.
- **CR5 — block YAML in state.** A key with an empty value followed by indented lines: `  - x`
  lines → list (as today); `  k: v` lines → map, rendered `k: v, k2: v2`; a value of `|` or `>`
  followed by indented lines → scalar, the lines joined with one space.
- **CR6 — fences per CommonMark.** A fence opens with ≥ 3 backticks or tildes (optional info
  string) and closes only on a line of the same character, at least as long, with nothing but
  whitespace after it.
- **CR7 — commit body only when the title differs from the normalised task.** ship.sh strips one
  level of matching single or double quotes from `task` portably (no `\x27` in sed) and compares
  after collapsing whitespace; equal → no commit body.
- **CR8 — one `git add`.** Discovered paths are filtered in bash (`${f##*/}` for the basename) and
  staged in a single `git add -A --pathspec-from-file=- --pathspec-file-nul` call (literal
  pathspecs).
- **CR9 — one sensitive-pattern set.** The helper's spec-path check includes ship.sh's directory
  rules (`node_modules/`, `.bytedigger/` anywhere in the path); both files carry a comment naming
  the other as the twin to keep in sync, and a RED row checks the two lists agree.
- **CR10** — the unused readiness JSON capture is removed (`--json` output discarded).

## §4 Acceptance (RED targets)

Fixture: the bd#117 rig (real temp repo, bare remote behind the ssh shim, pre-receive `PUSH` log,
fixture `gh` via `HAL_GH_BIN`), copied into `engine_py/tests/test_bd131_ship.py` the way
`test_bd117b_companion_tune.py` copies it. Readiness `required: false` unless a row says
otherwise.

| AC | op | Given → Then |
|---|---|---|
| AC1 | 1,2 | **Pilot shape:** branch with 2 commits ahead of `origin/main`, no upstream, nothing modified, `files_modified: []` → exit 0, `PUSH refs/heads/<branch>`, one `pr create`, `ship_complete: true`, no new commit made by ship.sh (HEAD unchanged) |
| AC2 | 1 | tracked file modified, `files_modified: []` → ship.sh commits it (the new commit's tree has the change), PUSH, one `pr create` |
| AC3 | 1 | tracked file deleted, not listed → the ship commit records the deletion |
| AC4 | 1 | untracked `new.txt` not listed + untracked `build-plan-review-cycle1.md` → neither is in any commit; `new.txt` listed in `files_modified` → committed |
| AC5 | 1 | tracked `.env` and `config/app.env` modified (committed in the seed) → not staged, `SKIP (sensitive): .env` on stdout; a non-sensitive modified file in the same run is committed |
| AC6 | 2 | nothing staged, 0 ahead → exit 0, stderr has `WARNING: nothing to ship`, no PUSH, no `pr create`, `build-state.yaml` byte-identical |
| AC7 | 2 | base order: upstream set to the already-pushed same-name branch with HEAD equal to it, while `origin/main` is behind → nothing to ship (upstream wins over `origin/main`); one more commit → PUSH |
| AC8 | 2 | `required: true`, approved: AC1 shape still pushes (bd#117 AC-A12a rows 1–3 stay green) |
| AC9 | 3 | spec H1 `bd#7 — Make the widget fast`, commits ahead `bd#7: RED …`, `bd#7: GREEN …` → PR title `bd#7: Make the widget fast` |
| AC10 | 3 | mixed prefixes (`feat: x`, `fix: y`) → title is the H1 text unchanged; H1 `Add a widget` with commits all `feat: …` → `feat: Add a widget` |
| AC11 | 3 | no spec file → title = `task`; `spec_path` pointing at a relative file next to the state (state outside the repo) is honoured |
| AC12 | 3 | 250-char H1 → title ≤ 72 chars, ends on a word boundary, no trailing `,;:—–-` or space |
| AC13 | 3 | ship.sh commits (AC2 shape) with a spec present → commit subject == PR title, and `task` is in the commit body when it differs |
| AC14 | 4 | pilot-like state (the op4 fields: `plan_review`, `plan_review_cycles`, `plan_review_concerns` list, `opus_validation`, `opus_validation_cycles`, `phase_6_reviewer_verdicts` map, `review_satisfaction`, `phase_6_satisfaction` map, `pre_existing_findings` list, `follow_ups` block list) + spec with `### Scope IN`, `### Scope OUT` and `### Follow-ups` → body has `## Scope` with both sections verbatim, `## Review` with the four lines as specified, `## Follow-ups` with state items then the spec section; last two lines are the fixed line and the marker |
| AC15 | 4 | duplicate key (`plan_review: fail` then later `plan_review: pass`) → `Plan review: pass`; field without its cycles key → no parenthetical |
| AC16 | 4 | a spec section and a state value each containing `<!-- bd:built -->` / `<!-- bd:consumed … -->` → those lines absent; marker appears exactly once, as the last line |
| AC17 | 4 | no evidence → body lines exactly `["Built via ByteDigger /build pipeline.", "<!-- bd:built -->"]` (bd#117 AC-B1 green unchanged) |
| AC18 | 4 | a 200 000-char Scope section → body ≤ 60 000 UTF-8 bytes, contains `(truncated; see build-spec.md)`, `## Review` lines intact, marker last |
| AC19 | 5 | helper made to fail (spec path is a directory / helper exits ≠ 0 via a broken `python3` shim that fails only for `ship_pr_text.py`) → ship still pushes, title = `task`, body = the two lines, one `WARNING: ship_pr_text failed` line, no `Traceback` in stderr |
| AC20 | 5 | helper unit (subprocess on `scripts/ship_pr_text.py`): `title` and `body` exit 0 on a state with only `task:`; stdlib-only (AST: imports ⊂ stdlib) |
| AC21 | 6 | `.gitignore` lines `build-*-cycle*.md` and `.bytedigger-sessions.json` (with `git check-ignore` on `build-plan-review-cycle1.md`, `build-opus-validation-cycle2.md`, `.bytedigger-sessions.json` in this repo) |
| AC22 | 6 | `phases/phase-7-synthesize.md` State Cleanup names `build-*-cycle*.md` and `.bytedigger-sessions.json`; §7.5 and `commands/build.md` 7.1c no longer imply a file list must be maintained (mention "ahead") |
| AC23 | 7 | `docs/plugin.md` lists `scripts/ship_pr_text.py`; `CHANGELOG.md` Unreleased mentions bd#131 |
| AC24 | — | inverted bd#117 row: `required: false`, nothing staged, 1 ahead → PUSH, one `pr create` |
| AC25 | 1 | tracked files `docs/ünï.txt` and `a b.txt` (committed in the seed) modified, not listed → both in the ship commit, exit 0, `STAGE (tracked change): docs/ünï.txt` on stdout (unquoted) |
| AC26 | 1 | tracked `x.txt` modified + untracked `new.txt` not listed → ship commits `x.txt`, `new.txt` in no commit, stderr `WARNING: untracked files not shipped: new.txt` |
| AC27 | 1 | detached HEAD with a modified tracked file → exit 1, stderr `ERROR: detached HEAD`, no PUSH, no commit, nothing staged |
| AC28 | 3,4 | `spec_path: .env` holding `# Env title` + `## Scope` + `SECRET=abc` → title = `task` (not `Env title`), body has no `SECRET` and no `## Scope` (gate r2 M2); H1 inside a ```` ``` ```` fence before the real H1 → the real H1 is the title; a `## Scope` line inside a fence is not a section |
| AC29 | 5 | helper `body` output missing the marker (shim prints a body without it) → two-line body + warning; `title` output of two lines → only the first is used |
| AC30 | 1 | a merge conflict left in the index (`git ls-files -u` non-empty) → exit 1, stderr `ERROR: unmerged paths`, no PUSH, no commit, **no readiness gh call** (under `required: true`); AC27 detached HEAD likewise makes no readiness gh call |

**Test tightening (gate r1 M4, m10):** AC19's shim prints `Traceback (most recent call last):` to
stderr before exiting 1, and the test asserts `Traceback` is absent from ship.sh's stderr. AC16
asserts the whole line (its other text) is gone. AC6/AC7 assert the base text in the warning
(`refs/bd/policy` / `@{upstream}`). AC13 adds a case task == title (no body). AC12 adds an
exactly-72-char H1 (unchanged) and a single 80-char word (hard cut at 72).
| AC31 | CR1 | ship.sh run from `<repo>/sub` with `sub/x.txt` modified → committed and pushed; a conflict outside `sub/` → `ERROR: unmerged paths` |
| AC32 | CR2 | `required: true`, approved: state file missing → exit 1, no readiness gh call (label not consumed); `task` with a backtick → same |
| AC33 | CR3 | tracked `a.py` deleted + untracked `b.py` unlisted → exit 1, `ERROR: tracked deletions with untracked files not shipped: b.py`, no push, no readiness gh call under `required: true`; `b.py` listed in `files_modified` → ships both |
| AC34 | CR4 | state values `<!--\tbd:built -->` and `<!--  bd:built -->` → absent; marker exactly once, last |
| AC35 | CR5 | block map `phase_6_reviewer_verdicts:` + `  code: PASS` + `  security: PASS` → `- Reviewers: code: PASS, security: PASS`; `plan_review: \|` + indented `pass` → `- Plan review: pass` |
| AC36 | CR6 | a ```` ```` ```` fence containing ```` ```bash ```` then ```` ``` ```` before a real `## Scope` → Scope section present; a heading inside that fence is not a section |
| AC37 | CR7 | no spec, `task: 'Fix  login'` → commit subject `Fix login`, no commit body |
| AC38 | CR9 | helper treats `spec_path: .bytedigger/s/spec.md` as no spec; the basename patterns in ship.sh `_is_sensitive` and in the helper are the same set (parsed from both files) |
