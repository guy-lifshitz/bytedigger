# bd#117 — weekly companion tuning from human corrections; a `plan-approved` readiness gate

**Status: FROZEN** (spec only; implementation decided after review) · **Class:** SYSTEMATIC ·
**Chokepoints:** `readiness.verdict` (`engine_py/bytedigger_engine/readiness.py`) — the one place
anything decides whether an issue-bound build may start implementing or send work off the
machine; `companion_tune.propose` (`engine_py/bytedigger_engine/companion_tune.py`) — the one
place anything turns human corrections into a proposed text change.

Source: harvest of Warp OSS / Oz, rows 4 and 5 (`oz-for-oss` `update-*-local` cron;
`ready-to-spec` / `ready-to-implement` / `plan-approved` labels); HAL note
`SHARED/notes/2026-09-30_harvest-warp-oz.md` (HAL `b09cf8509`, L14-16, L19, L29-30);
`.bytedigger/learnings/skills.md`. **Part B depends on bd#116** (frozen spec, PR #120:
`bytedigger/companions/<core-id>.md`, `metadata.overridable`, `scripts/skill-companion check`,
`lib/frontmatter.py`). This spec changes no #116 rule: Part B is a new *writer* of the file
#116 reads and uses #116's `check` as its output gate. #116 §2 "Out of scope" names this work
("self-tuning of companions from human corrections, harvest finding #4"). **Part A does not
depend on #116** and ships first. bd#115 (lot-1874) is not touched.

## §1 Problem (measured on `aa87816`)

1. **BD learns only from its own build.** Phase 7's synthesizer reads the build's spec and
   review files (`phase_7_synthesize.py:109-112`, `:441-456`) and writes one report (`:709`);
   `phases/phase-7-synthesize.md:60-62` persists `reviews/learnings-raw.md` to
   `.bytedigger/learnings/`. What a human does *after* the build — reopens the issue the PR
   closed, relabels it `regression` — reaches nothing. BD has no GitHub timeline/event reader;
   the only label reader is `phase_8_post_deploy.py:435-449` (`_issue_type_prefix`,
   fix/feat prefix, best-effort).
2. **Nothing marks a PR as BD-built.** `scripts/ship.sh:144` sends body
   `"Built via ByteDigger /build pipeline."`; `phase_8_post_deploy.py:488-494` sends the
   synthesizer report or `org_config["ship_pr_body"]`. No machine-readable marker.
3. **BD has no intake and no approval gate that binds.** `/build` takes a free-text `task`
   (`commands/build.md:7`). Approval exists only as a SUPERVISED pause
   (`phase-4-architect.md:83,87`, `phase-45-spec.md:125`); AUTONOMOUS never pauses
   (`build.md:27-29`), and FEATURE is AUTONOMOUS by default. `plan_review: pass` /
   `opus_validation: pass`, required by `gate_phase_5` (`build-gate.sh:260-266`), are model
   verdicts in agent-writable `build-state.yaml`.
4. **The spec is not one stable file.** The prompt pipeline writes `./build-spec.md`
   (`build.md:47,79`; `phase-45-spec.md:85,93`); the engine writes
   `$SCRATCHPAD/specs/build-spec.md` (`phase_45_spec.py:57`, `phase_45_spec_lite.py:213`), and
   7.2 deletes scratchpad `specs/` before Phase 8 (`build.md:158`). Phase 5 "Plan-Sync"
   rewrites the spec to match reality (`phase-5-implement.md:348`). TRIVIAL writes none
   (`phase-0-classify.md:110,176`). A gate that hashes a local spec file at ship time would
   either refuse every drifting build or bless text nobody approved — so the approved text must
   live somewhere the build cannot rewrite (op-A2).
5. **Issue binding today is loose.** `_parse_issue_from_branch` is an unanchored `re.search`
   (`phase_8_post_deploy.py:429`: `high5-x` → 5); Phase 0 worktrees are `build/<slug>`
   (`phase-0-classify.md:205`); `ship.sh:97-101` creates `feat/<slug>` when on main.
6. **A ship failure is swallowed.** `phase-7-synthesize.md:117` ("log warning, continue"), then
   7.2 deletes `build-state.yaml` (`build.md:158`) — nothing is left to resume.

7. **Not measured (§1b, open — Lot A measures before its RED freeze and records here):**
   (a) whether GitHub stores a comment body posted via `gh api` byte-for-byte or rewrites line
   endings / trailing whitespace (the normalisation in op-A1 is designed to survive either; the
   probe confirms it); (b) the wall-clock cost Phase 0 adds (`ls-remote` + `fetch` of one ref,
   30 s timeout ceiling) on a real host, as the baseline for any latency claim. Live probes
   against GitHub were not run from this spec session.
   **Measured by Lot A (2026-09-30, before the RED freeze):** (a) a body with CRLF line ends,
   trailing spaces/tabs and trailing blank lines, posted with `gh api …/issues/117/comments
   --input`, came back **byte-identical** from both REST and GraphQL (`lastEditedAt` null);
   the comment was deleted right after. The op-A1 normalisation is therefore a safety margin,
   not a workaround. (b) `ls-remote --symref` + `fetch --no-tags +main:refs/bd/…` against
   `git@github.com:guy-lifshitz/bytedigger` over SSH, 3 runs: 1.46–1.56 s + 1.52–1.61 s,
   **3.0–3.2 s total** per Phase 0 / ship check.

What Oz does and why it is not enough for us: a weekly `update-*-local` job opens a PR against
the local skill only; `plan-approved`, set by a human, gates the implementation run. Both limits
are skill prose; the note names no deterministic check (Principle C). We take the shape and name
an enforcement layer for each limit.

## §2 Design

### Part A — readiness gate

#### Terms

- **Policy source** — the repository BD **pushes to**: `P` = the output of
  `git remote get-url --push --all origin` (so a `pushurl` or `pushInsteadOf` cannot point the
  read at a decoy while the push goes elsewhere — gate r1 M3). That call exits non-zero or
  prints nothing ⇒ **no origin**; more than one line ⇒ unavailable. Default branch `D` from
  `git ls-remote --symref P HEAD`; then `git fetch --no-tags P +D:refs/bd/policy` (forced, so a
  force-pushed default branch does not wedge it) and `git show refs/bd/policy:bytedigger.json`.
  Every git call runs with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`,
  `GIT_OBJECT_DIRECTORY`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT` scrubbed,
  `GIT_TERMINAL_PROMPT=0`, and a 30 s timeout (`GIT_SSH_COMMAND` is kept). **Repository for GitHub calls** — `<owner>/<name>`
  parsed from `P` (`https://github.com/<o>/<n>[.git]` or `git@github.com:<o>/<n>[.git]`;
  anything else, GitHub Enterprise hosts included, ⇒ unavailable — v1 supports github.com
  only, stated in `docs/configuration.md`) and passed explicitly
  to every `gh` call (`-R` / GraphQL `owner`, `name`), never `gh`'s own default repo (gate r2
  m2). The only local ref written is
  `refs/bd/policy`. Never the working tree or the build branch: a build that commits
  `required: false` on its own branch changes nothing (DesignReview F1). Part A owns
  `lib/git_blob.py`: `read_blob(repo, rev, path) -> ("ok", bytes) | ("absent", None) |
  ("error", detail)` (tri-state; #116's implementation lot may import it — no #116 rule
  changes).
- **Readiness policy** — key `readiness` of that blob. Schema:
  `{"required": bool, "label": str, "approvers": [login…], "distinct_actor": bool}`; defaults
  `false`, `"plan-approved"`, `[]`, `false`. Decision table (gate r1 M3):

  | case | stage `ship` | stage `start` / Phase 0 |
  |---|---|---|
  | no origin (`get-url` non-zero or empty) | off (0) | off (0) |
  | `ls-remote` / `fetch` fails or times out, or `ls-remote` prints a `HEAD` line without `ref:` | unavailable (4) | warn `W_READINESS_UNAVAILABLE`, continue |
  | `ls-remote` exits 0 with empty output (empty repo / unborn `HEAD` — `--symref` does not request `unborn`), or `bytedigger.json` absent on `D` | off (0) | off (0) |
  | file present, not valid JSON, or `readiness` not an object / wrong field types | unavailable (4) | warn, continue |
  | `readiness` absent or `required: false` | off (0) | off (0) |
  | `required: true` | evaluate | evaluate |

  The push URL is parsed into `<owner>/<name>` only **after** the table says `required: true`;
  a local-path, GitLab or other non-GitHub push target with no policy is therefore off (0), and
  with `required: true` it is unavailable (4) (gate r4 m2).

  **Declared §1x change (gate r4 M1):** a push target that cannot be reached at ship time was a
  push failure (`E_SHIP_PUSH_FAILED`, recoverable) and is now a readiness failure
  (`E_READINESS_UNAVAILABLE`), because no policy can be read; the gate fails closed before any
  push. Phase 8 marks `E_READINESS_UNAVAILABLE` `recoverable=True` (transient) and
  `E_READINESS_NOT_APPROVED` `recoverable=False`. The two siblings that pin the old code on an
  unreachable origin are amended by the RED lot (§5).

  At `start` an unreadable policy never blocks (offline builds keep working; the start gate is
  prompt-level anyway); at `ship` it never lets work out. "off" = today's behaviour (§1x): no
  `gh` call at all.
- **`gh` binary** — one name everywhere: `get_config().binary("HAL_GH_BIN", "gh")`, which the
  alias layer also reads as `BD_GH_BIN` / `BYTEDIGGER_GH_BIN` (`docs/configuration.md:81-89`,
  `phase_8_post_deploy.py:217`); `ship.sh` resolves the same precedence in shell
  (`HAL_GH_BIN` > `BD_GH_BIN` > `BYTEDIGGER_GH_BIN` > `gh`) instead of bare `gh` (`:143`)
  (gate r1 M5).
- **Bound issue** — from the branch name only, anchored, case-insensitive (as today):
  `(?i)^(?:gh|batch/)(\d+)(?:-|$)`.
  `_parse_issue_from_branch` moves to `readiness.py` with this anchored regex and a re-export
  in `phase_8_post_deploy.py` (§1g; its title-prefix caller sees fewer, never more, matches —
  a deliberate behaviour change, sibling `test_gh1124_ship_pr_title.py`). `/build --issue <N>`
  makes Phase 0 put the build on branch `gh<N>-<slug>` for **every** tier (worktree or not);
  with `--issue <N>` and a branch that parses to another number, Phase 0 STOPs
  (`issue_mismatch`).
- **Issue read** — three pinned GraphQL queries on `repository(owner,name).issue(number)`, one
  connection each, each paged by `readiness.py`'s own cursor loop (`gh api graphql -F
  after=<cursor>` until `pageInfo.hasNextPage` is false — not `--paginate`, which follows only
  one connection per query; gate r2 N3):
  `labels(first:100, after:$after){nodes{name} pageInfo{hasNextPage endCursor}}`;
  `comments(first:100, after:$after){nodes{id databaseId author{login} body createdAt
  lastEditedAt} pageInfo{…}}`;
  `timelineItems(itemTypes:[LABELED_EVENT], first:100, after:$after){nodes{... on LabeledEvent{id
  actor{login} createdAt label{name}}} pageInfo{…}}`. Every page is read (gate r1 M7); any page
  error ⇒ unavailable. Comment order is `databaseId` (monotonic; `createdAt` has one-second
  resolution — gate r2 m4). Logins compare case-insensitively.
- **BD user** — `gh api user` → `login`.
- **Spec record** — a comment **by the BD user** whose body is: line 1
  `<!-- bd:spec sha256=<hex> -->`, then the spec text; `<hex>` = sha256 of the text after line 1
  normalised (CRLF → LF, trailing whitespace at the end stripped, one final `\n`). Comments by
  anyone else are ignored whatever they contain (gate r1 m1). A record is **valid** iff
  `sha256(normalised text) == <hex>` and `lastEditedAt` is null. The **current record** is the
  latest valid record by `databaseId`; a BD-user record newer than it that is invalid is itself
  the reason (`spec_record_edited` / `spec_record_bad`), so editing a comment cannot fall back
  to an older approval (F5).
- **Consumption record** — a BD-user comment, never edited (`lastEditedAt` null; edited ones
  are ignored), whose body is `<!-- bd:consumed approval=<LabeledEvent id> branch=<branch> -->`.
  The **owner** of an approval event is the branch named by its earliest (lowest `databaseId`)
  consumption record.
- **Approval event** — the latest `LabeledEvent` for `label` (by `createdAt`, then position in
  the timeline). It **counts** iff its `createdAt` is strictly later than the current record's,
  its actor is in `approvers` (when non-empty), and, when `distinct_actor: true`, its actor is
  not the BD user.
- **Verdicts.** Evaluated in this order; the first match decides:
  1. `no_issue`, `no_spec_record`, `spec_record_edited`, `spec_record_bad` ⇒ NOT_APPROVED;
  2. **retry** — at stage `ship`, the approval event counts and its owner is **this** branch ⇒
     `APPROVED`, whether or not the label is still on the issue (the consumption removed it —
     gate r2 N1);
  3. `approval_consumed` — the approval event has an owner that is another branch;
  4. `label_absent`, `label_predates_spec` (also: label present but no `LabeledEvent` for it),
     `approver_not_allowed`, `self_approved` ⇒ NOT_APPROVED;
  5. at stage `start` only: `spec_changed` — the current record's normalised text ≠ the
     normalised `--spec` file;
  6. otherwise `APPROVED`.

  `gh`/git/network failure or unparseable JSON ⇒ unavailable (4), never `APPROVED`.
- **Consumption (gate r1 M1, r2 N1).** At stage `ship`, a verdict of `APPROVED` from rule 6 is
  taken **before any push**: BD posts a consumption record for the approval event's `id` and
  this branch, re-reads the comments, and proceeds only if the owner is this branch (two racing
  builds: exactly one wins; the loser gets `approval_consumed`, nothing pushed). Posting fails,
  or BD's own record is still not visible after 3 re-reads 2 s apart (GitHub read-after-write
  lag — gate r3 m7) ⇒ unavailable (4), nothing pushed. Then —
  still before the push, the **one** removal point — BD removes `label` (UI only; failure is a
  warning). A verdict from rule 2 (retry: push failed, `pr create` failed, phase 8's
  already-open-PR path, a DBOS replay) posts nothing and removes nothing. Any other branch
  needs a new record and a new approval (F6). A second build's `post` on the same issue makes
  a newer current record and removes the label, so an in-flight build of the first that has
  not consumed yet is refused `label_absent` — intended: the issue has one live plan at a time (gate r2
  m10).
- **BD never adds `label`.** It only removes it (op-A2, consumption).

#### op-A1 — `readiness.py` + `scripts/readiness` (core module, stdlib + `gh` subprocess)

`verdict(repo, stage, spec_path=None) -> {"required", "issue" | None, "label", "verdict",
"reason" | None, "record_sha256" | None}`. Thin wrapper (§1f, the #116 op2 shape):
`scripts/readiness check --stage {start|ship} [--spec <path>] [--repo .] [--json]` and
`scripts/readiness post --spec <path> [--repo .]`. `check --stage ship` performs the
consumption itself — one call, so no ship site can check without consuming.

| exit | `check` | `post` |
|---|---|---|
| 0 | off, or `APPROVED` (at `ship`: consumed for this branch) | done (or off — nothing done) |
| 2 | usage | usage |
| 3 | `NOT_APPROVED`; stderr `E_READINESS_NOT_APPROVED <reason> #<N>` | `E_READINESS_NOT_APPROVED spec_too_large #<N>` (record > 65 536 chars) |
| 4 | unavailable; stderr `W_READINESS_UNAVAILABLE <detail>` | same |
| other | crash, `python3` missing — callers treat like 4 | same |

#### op-A2 — `post`: the approved text lives in the issue

Reads `--spec`, posts one spec record on the bound issue, then removes `label` if it is on the
issue (so a human can add it again; the timestamp rule already voids an older approval, the
removal keeps the UI honest). If the current record already has the same sha, no comment is
posted but the label is still removed when the verdict reason is `label_predates_spec` (F13).
The record is the frozen approved spec: Plan-Sync may rewrite the local file afterwards; the
ship stage does not read it (F2, F3).

#### op-A3 — pipeline wiring (prompt layer) and the pause

- **Phase 0** (`phases/phase-0-classify.md`, `commands/build.md`, `skills/bytedigger/SKILL.md`),
  after #116's `skill-companion render` step when #116 is merged (render first — gate r1 m8):
  parse `--issue <N>`; run `readiness check --stage start --json` only to read `required`
  (4 ⇒ warn and continue, per the decision table); when `required: true` and no issue ⇒ STOP
  (`no_issue`) before `build-state.yaml` is written; `--issue <N>` on a branch that parses to
  another number ⇒ STOP (`issue_mismatch`, Phase-0 text only, not a verdict reason); otherwise
  put the build on `gh<N>-<slug>` for every tier.
- **Start gate** — before the first write outside the scratchpad: FEATURE/COMPLEX after Phase
  4.5, SIMPLE after its Phase 1 spec, TRIVIAL after writing a minimal `build-spec.md`
  (`Task | Files | Change`, only when `required: true`) and before its direct edit — run
  `readiness check --stage start --spec ./build-spec.md`:
  - **0** ⇒ continue;
  - **3** ⇒ `readiness post --spec ./build-spec.md`; set `current_phase: awaiting_approval`
    and `awaiting_stage: start`; print `Waiting for "<label>" on #<N>`; STOP — **in every
    mode, AUTONOMOUS included**. The PAUSE-POINT HARD RULE (`build.md:29`) gains this one named
    exception, keyed on `readiness.required`, not on mode;
  - **anything else** ⇒ warn with the stderr line and continue (the ship gate still holds; it
    will refuse `no_spec_record` if no record was posted).
- **`/build continue` from `awaiting_approval`** (the `**Resumable:**` paragraph): `awaiting_stage:
  start` ⇒ re-run the start gate, 0 ⇒ next phase; `awaiting_stage: ship` ⇒ re-run **only**
  SHIP (no re-implementation — gate r1 M2), then the rest of Phase 7.
- **Every readiness STOP message** prints the verdict line and, for `no_spec_record` /
  `label_predates_spec`, the recovery: `scripts/readiness post --spec ./build-spec.md`, then a
  human adds `<label>`, then `/build continue` (gate r2 m6).
- **Engine-driven builds** (`engine_py` phases 4.5 → 5) get only the ship gate in v1. Under
  `required: true` their Phase 8 fails `E_READINESS_NOT_APPROVED` until a record exists;
  recovery (documented in `docs/configuration.md`): `scripts/readiness post --spec
  $SCRATCHPAD/specs/build-spec.md`, a human adds the label, resume Phase 8. A start gate inside
  `phase_5_implement.py` is v2 (§1v).

#### op-A4 — ship chokepoint (the deterministic layer)

Both scripted paths by which BD sends work off the machine run `readiness check --stage ship`
(verdict + consumption) **before** anything leaves. `scripts/ship.sh`: **one** place — right
after its `--pr` gate (`:37-39`), before any git mutation (the `feat/<slug>` creation `:97-101`,
staging, commit, `git push` `:134`); it runs `readiness check --stage ship --json` and keeps
`required` from the output. `phase_8_post_deploy.py` `ship_to_pr`: right after its zero-ahead skip
(`:574`) — after the ship-disabled / on-main / zero-ahead
skips (`:522-574`, so a build that ships nothing makes no call — gate r2 m1), before the
dirty-tree guard (`:576`), the rebase (`:595-599`), the push (`:603-608`) and the already-open-PR lookup (`:626-647`) (gate r3 m4).
**Resume rule in `ship.sh`, only when `required: true`** (gate r2 m5, r3 N1): "nothing staged"
(`:119-122`) no longer exits 0 if the branch has commits ahead of `@{upstream}`, or, with no
upstream, of `refs/bd/policy` (the push target's default branch that readiness just fetched);
it goes on to the push. A count that is not a number (e.g. a mock git) is 0. `required: false`
⇒ `:119-122` unchanged (§1x). Not 0 ⇒ nothing is pushed:
- `ship.sh` exits with the readiness code (3 refused, 4 unavailable; a crash or a missing
  `python3` is mapped to 4 — so `ship.sh --pr` now requires `python3`, which every BD install
  already has for `engine_py`; stated in `docs/configuration.md`, gate r3 m6); its other exits
  keep today's meaning;
- **both** Phase-7 texts get the same order and rule (gate r2 N2): in
  `phases/phase-7-synthesize.md` "7.5 SHIP" moves before "State Cleanup" (today `:110` after
  `:77`); in `commands/build.md` Phase 7 gains the SHIP step (if `--pr`) between 7.1b and 7.2
  State Cleanup (`:149-158`), and `:162` ("Phase 8 runs after SHIP") stays true. When
  `required: true`, **any** non-zero ship.sh exit is STOP: set `current_phase:
  awaiting_approval`, `awaiting_stage: ship`, keep `build-state.yaml` and `build-metadata.json`,
  skip cleanup (gate r1 M2, F8); the Exit Criterion "build-state.yaml cleaned up"
  (`phase-7-synthesize.md:143`) gains "unless stopped awaiting approval". `required: false` ⇒
  today's best-effort rule (`:117`) unchanged;
- Phase 8 returns `status="error"`, `error_code="E_READINESS_NOT_APPROVED"` (3,
  `recoverable=False`) or `"E_READINESS_UNAVAILABLE"` (4, `recoverable=True`).

`required: false` ⇒ both paths unchanged and make no `gh` call.

**Enforcement layers, named (Principle C).** "BD's scripted ship paths do not push or open a
PR for unapproved work" — `readiness.verdict` at both sites, deterministic, reading only the
push target's default-branch policy, the branch name and GitHub. "Implementation does not
*start* without approval" — prompt instruction (op-A3); its failure mode is wasted local work.
**Residual risk, stated (F4, gate r1 M4):** the orchestrator has Bash, `git` and `gh`. It can
add the label itself, or skip both ship sites with a direct `git push` / `gh pr create`. So the
gate is **advisory against the model at every setting**: it makes the approval ritual the path
of least resistance and makes skipping it an explicit act visible in the transcript, not
impossible. `approvers` / `distinct_actor` close only the "model adds the label and then ships
through BD" path. A human deleting the newest record makes an older record current again; the
start gate then reports `spec_changed`, the ship gate cannot see it. A PreToolUse deny on
`git push` / label edits is out of scope. Approval binds the posted spec, not the code;
conformance stays the job of phases 5-6.

### Part B — weekly companion tuning (after #116 merges)

#### Terms

- **Tuning config** — key `tuning` of the same policy blob as Part A (push target's default
  branch): `{"bd_logins": [..], "bot_logins": [..], "watched_labels": [..]}`, all default `[]`.
  Malformed (not an object, a non-list field, a non-string entry) ⇒
  `E_COMPANION_TUNE_UNAVAILABLE malformed_tuning`, exit 4.
- **BD logins** — `tuning.bd_logins` ∪ {`gh api user` login}. Under a workflow `GITHUB_TOKEN`,
  `gh api user` returns 403 (gate r1 M6); then `tuning.bd_logins` must be non-empty, else
  `E_COMPANION_TUNE_UNAVAILABLE bd_logins_required`. Logins compare case-insensitively.
- **Maintainer** — an actor whose `permission` from
  `gh api repos/<o>/<r>/collaborators/<login>/permission` is `admin` or `write` (the API reports
  `maintain` as `write`), and
  who is not in `tuning.bot_logins`. 404 ⇒ not a maintainer; any other failure ⇒ exit 4.

#### op-B1 — provenance markers

`ship.sh` and `phase_8` `ship_to_pr` append one line to every PR body they send, after any
`ship_pr_body` override: `<!-- bd:built -->`. The tuner's own PRs carry
`<!-- bd:tune signals=<id>,<id>… -->` and never `bd:built`. A marker only counts on a PR whose
author is a BD login; tuner PRs additionally need head `bd/companion-tune-` (F11: anyone can
type a marker).

#### op-B2 — `companion_tune.collect` (deterministic; no model)

`scripts/companion-tune collect [--repo .] [--since 7d] [--out signals.json]` →
`{"window": [from, to], "signals": [{id, kind, pr, issue | None, label | None, actor, title, at}]}`.
Two listings, because reopening an issue does not touch the PR that closed it (gate r1 M6):
`gh pr list --state all --search "updated:>=<from>" --limit 1000` (for `relabeled` on PRs) and
`gh issue list --state all --search "updated:>=<from>" --limit 1000` (for `reopened` and
`relabeled` on issues; each issue's closing PRs via GraphQL
`closedByPullRequestsReferences(first:100, includeClosedPrs:true)`, paged like op-A1). Either listing at exactly 1000 rows ⇒ `E_COMPANION_TUNE_TRUNCATED`, exit 4, no
output. Timelines are read with `gh api graphql --paginate`. Signal kinds, exactly two in v1,
both **by a maintainer** inside the window:

- `reopened` — a `reopened` event on an issue that a **merged** BD-built PR closes
  (`closingIssuesReferences`).
- `relabeled` — a `labeled` / `unlabeled` event on a BD-built PR or an issue it closes, for a
  label in `tuning.watched_labels` (empty or absent ⇒ no `relabeled` signals). The readiness
  `label` is never a signal.

`id` = `<kind>:<event node_id>`. Earlier tuner PRs are listed with
`gh pr list --state all --search "head:bd/companion-tune-" --limit 1000` (same truncation rule)
and filtered by the author/head rule; signals named in their `bd:tune signals=` line are
dropped. Comment bodies and replies are
**not** read in v1 (Oz reads replies; free text by anyone is the injection surface). Titles are
read and are untrusted (op-B3).

#### op-B3 — `companion_tune.propose` (the chokepoint)

`scripts/companion-tune propose --core bytedigger --signals signals.json [--repo .]
[--llm-command …] [--dry-run]`. In order; the first failing step ends the run:

1. **No signals** ⇒ exit 0, `no signals`; no model call, no branch.
2. **Open tuner PR exists** ⇒ exit 0, `skipped: open PR #<n>`.
3. **Targets** — the core's overridable set, parsed from `skills/<id>/SKILL.md`
   `metadata.overridable` with #116's `lib/frontmatter.py` (no #116 interface change; F10).
   Empty ⇒ exit 0, `nothing overridable`. Then `skill-companion check --core <id> --repo
   <origin/<default> worktree>`: 3 ⇒ `E_COMPANION_TUNE_REFUSED current_companion_invalid`
   (a human fixes the companion first); other non-zero ⇒ `E_COMPANION_TUNE_UNAVAILABLE`.
4. **Draft** — one model call (default `get_claude_fallback()`, `lib/model_config.py:160`;
   `--llm-command` overrides) with no tools, given: the overridable section titles and bodies,
   the current companion (or none), the signals. Output: the complete new companion file
   between fixed fences on stdout. Empty / unparseable ⇒ `E_COMPANION_TUNE_DRAFT_INVALID`.
5. **Commit in isolation** — a temporary worktree from `origin/<default>` on branch
   `bd/companion-tune-<YYYYMMDD>-<first 8 hex of sha256(sorted signal ids)>` (F12); write only
   `bytedigger/companions/<id>.md`; commit.
6. **Gate** — (a) `git diff --name-only origin/<default>...HEAD` is exactly
   `bytedigger/companions/<id>.md`, else `E_COMPANION_TUNE_REFUSED extra_path`; (b)
   `skill-companion check --core <id> --repo <worktree>`: 0 ⇒ go; 3 ⇒
   `E_COMPANION_TUNE_REFUSED <first #116 reason>`; other ⇒ `E_COMPANION_TUNE_UNAVAILABLE`. Any
   refusal deletes the branch unpushed.
7. **Open PR** — push the branch, `gh pr create` against the default branch (under a
   workflow `GITHUB_TOKEN` this needs the repository setting "Allow GitHub Actions to create
   and approve pull requests"; `docs/configuration.md` says so); body lists each
   signal (kind, link, actor, at) and the `bd:tune signals=` line. Never merges, never pushes
   to the default branch, never adds labels. `--dry-run` stops after 6 and prints the diff.

**Enforcement layers, named.** "Only the local companion changes", "only in sections the core
allows" — step 6, deterministic, before anything is pushed. "A human decides" — the PR; no BD
code path merges it (AC-B8). **Residual risk, stated:** issue/PR titles are written by anyone
who can open an issue; step 6 bounds where the model's text lands, not what it says (#116 §2
residual risk); the human review of the PR is the only content check.

#### op-B4 — the weekly schedule

BD ships `examples/github-actions/companion-tune.yml` (`schedule: cron "0 6 * * 1"` +
`workflow_dispatch`; `permissions: contents: write, pull-requests: write, issues: read`) that
runs `collect --since 7d` then `propose`. BD's own `.github/workflows/` does **not** get it (BD
ships the core and has no companion). A host copies it; HAL wires its own scheduler in a HAL lot.

### Registries and docs

`E_READINESS_NOT_APPROVED`, `E_READINESS_UNAVAILABLE`, `E_COMPANION_TUNE_DRAFT_INVALID`, `E_COMPANION_TUNE_REFUSED`,
`E_COMPANION_TUNE_UNAVAILABLE`, `E_COMPANION_TUNE_TRUNCATED` in `error_codes.py` and both
`ERROR_CODES.md` (regenerated with `error_codes.py --markdown`); `readiness.py`,
`lib/git_blob.py`, `companion_tune.py` in `core_manifest.json`; `docs/configuration.md`:
`readiness`, `tuning`, `--issue`, the marker lines, the shared-account residual risk;
`bytedigger.json` unchanged (defaults live in code); `CHANGELOG.md` `[Unreleased]`.

### Out of scope (§1v)

A start gate inside engine `phase_5_implement.py`; a PreToolUse deny on label edits; reading
comment/reply text; `pr_closed_unmerged`, revert and review-comment signals; feeding signals
into `.bytedigger/learnings/` or phase 7; triage labels (`ready-to-spec`, `repro:*`, `area:*`)
and a review-readiness gate; the #116 files and rules; `build-gate.sh`, `build-phase-gate.ts`,
`gate-dispatcher.sh`, `hooks/`; bd#115's files; HAL's tree.

## §3 DesignReview

**Verdict: REJECT** (Opus, against `aa87816`) → changes applied. All other §1 citations
confirmed; no contradiction with #116 on the companion path, `check` exits 0/3/2, or scope.

1. **HIGH — HEAD policy read is switchable by the build's own commit (applied).** Policy from
   `origin/<default>`; AC-A3.
2. **HIGH — Plan-Sync rewrites the approved spec (`phase-5-implement.md:348`) (applied).** The
   approved text is the issue record; the ship stage reads no local spec; `spec_changed` only at
   the start stage.
3. **HIGH — §1.4 wrong for the engine path (applied).** §1.4 rewritten; the ship stage needs no
   spec file; engine start gate is v2.
4. **HIGH — self-approval in a shared account; prompt enforcement overclaimed (applied).**
   `approvers`, `distinct_actor`; residual risk states the gate is advisory against the model at
   defaults; AC-A12.
5. **HIGH — edited record keeps approval; text ≠ hash (applied).** sha over the text,
   `lastEditedAt`, `spec_record_edited`/`spec_record_bad`, `spec_too_large`; AC-A2, AC-A8.
6. **HIGH — stale approval replay (applied).** consumption at ship; AC-A10.
7. **MED — loose/forgeable branch binding (applied).** Anchored regex, `gh<N>-` for every tier,
   `issue_mismatch`, `ship.sh` checks before `feat/`; AC-A4.
8. **MED — refusal swallowed, state lost (applied).** ship.sh exit 3 ⇒ STOP, skip cleanup; AC-A14.
9. **MED — Part A reused #116 code (applied).** Part A owns `lib/git_blob.py`.
10. **MED — `check --json` has no overridable set; exit handling (applied).** Parse via
    `lib/frontmatter.py`; 3 vs other split; `E_COMPANION_TUNE_UNAVAILABLE`.
11. **MED — forged markers, non-maintainer reopen, 30-row list (applied).** Author/head rule,
    maintainer filter, `--limit 1000` + truncation error, `--paginate`; AC-B2, AC-B3.
12. **LOW — same-day branch collision (applied).** Signals-hash suffix.
13. **LOW — stale label on same-sha `post`; ties (applied).** Removal on
    `label_predates_spec`; strict `>`.
14. **LOW — two citations (applied).**
15. **LOW — no label-add oracle (applied).** AC-A13.

### Gate r1 (Opus) — REJECTED → changes applied

DesignReview F2, F3, F7, F9, F10, F12-F15 confirmed closed; F1, F4, F5, F6, F11 partly; F8 not.

1. **M1 — one approval = one ship not enforced (label removed after `pr create`; races;
   phase 8's open-PR path).** Consumption record posted and re-read **before** any push,
   earliest-wins, same-branch retry allowed; the check and the consumption are one call;
   AC-A10.
2. **M2 — resume after a ship refusal undefined; 7.5 runs after cleanup.** `awaiting_stage:
   start|ship`; continue from `ship` re-runs 7.5 only; 7.5 moves before State Cleanup; any
   non-zero under `required: true` keeps state; AC-A14.
3. **M3 — policy read fails open and is steerable.** Read from the push URL into
   `refs/bd/policy`; decision table (ship fail-closed, start warn); tri-state `read_blob`;
   `GIT_TERMINAL_PROMPT=0`, 30 s timeout; AC-A1, AC-A3.
4. **M4 — F4 claim too strong (direct `git push` / `gh pr create`).** Guarantee narrowed to BD's
   scripted ship paths; advisory against the model at every setting.
5. **M5 — three `gh` resolvers.** One: `HAL_GH_BIN` via the alias layer (`BD_GH_BIN`), also in
   `ship.sh`; fixtures shim both env and `PATH`.
6. **M6 — Part B vs GitHub reality.** Issue listing for reopens; `bd_logins` required when
   `api user` 403s; Actions PR setting documented; AC-B2, AC-B3.
7. **M7 — record read unpinned.** Pinned paginated GraphQL with `lastEditedAt`; AC-A6.
8. **m1-m8 (applied).** Only BD-user comments are records; deleted-record fallback stated as
   residual; normalised hash + §1 item 7 probe; unavailable pinned to exit 4; `issue_mismatch`,
   `GH42-x`, Phase 0 unavailable = warn; engine-build recovery documented; §1b latency
   baseline as a Lot A precondition; `tuning` malformed / 404 / dedupe listing / case rules
   defined; `render` before `readiness` in Phase 0.

### Gate r2 (Opus) — REJECTED → changes applied

r1 M4-M6, m1-m8 confirmed closed; M1, M2, M3, M7 partial. GraphQL fields verified to exist.

1. **N1 — same-branch retry could never pass (label removed ⇒ `label_absent` first).** Verdict
   order rewritten: retry (approval owned by this branch) is rule 2, before the label checks;
   one removal point (after consumption, before push); invisible own record ⇒ 4; stateful
   fixture; AC-A10.
2. **N2 — `commands/build.md` Phase 7 had no SHIP before cleanup.** SHIP step added there too;
   Exit Criterion exemption; AC-A14.
3. **N3 — one GraphQL query cannot be paged over several connections.** Three queries, own
   cursor loop; >100 labeled-events row in AC-A6.
4. **N4 — `ship-protocol.bats` mock git.** "No origin" = `get-url` non-zero **or empty**;
   check placed after the `--pr` gate; AC-A1 row.
5. **m1-m10 (applied).** Phase 8 check after its skips; `<owner>/<name>` from the push URL;
   `+D`, `--all`, config-env scrub; `databaseId` ordering, event `id`, edited consumption
   ignored; `ship.sh` resume past "nothing staged"; recovery text; resolver / crash / F13 /
   non-404 ACs; `pre-receive` hook ordering; `includeClosedPrs:true`, `maintain`→`write`;
   one live plan per issue stated.

### Gate r3 (Opus) — REJECTED → changes applied

N1-N4, m1, m3, m4, m6-m9 confirmed closed; the r2 verdict order and single removal point hold
under adversarial audit (only BD-user comments count; a re-label makes a new unowned event).

1. **N1 — two positions for the `ship.sh` check; ahead rule changed `required: false`.** One
   position (after `--pr`, before any git mutation); ahead rule only under `required: true`;
   base = `@{upstream}` else `refs/bd/policy`; non-numeric ⇒ 0; AC-A12a rewritten.
2. **N2 — AC-A10 expected `label_predates_spec` where the order yields `label_absent`.**
   Row and prose corrected; re-label-then-other-branch row added.
3. **m3-m7 (applied).** github.com push URL + `GIT_SSH_COMMAND` shim in fixtures; GHE ⇒ 4,
   stated; phase 8 check between `:574` and `:576`; `GIT_CONFIG_*` and >100-label rows;
   `python3` dependency stated; 3 bounded re-reads. Duplicate rule text and "and and" fixed.
   The shared-account pre-claim is covered by the stated residual risk.

### Gate r4 (Opus) — REJECTED → changes applied

All r3 findings confirmed closed; no internal contradiction in the r3 changes.

1. **M1 — an unreachable origin now fails closed, turning two siblings red.** Declared as a §1x
   change; `E_READINESS_UNAVAILABLE` is `recoverable=True`; both siblings listed as amended by
   the RED lot; AC-A11 row.
2. **m2-m4 (applied).** URL parsed only under `required: true` (local/GitLab with no policy ⇒
   0); citation: the check precedes the dirty-tree guard `:576` and the rebase `:595-599`;
   `GIT_SSH_VARIANT=simple`. Edges added: unborn default branch ⇒ 0; consumed then
   `E_SHIP_DIRTY_TREE` ⇒ retry passes.

### Gate r5 (Opus) — REJECTED → changes applied

r4 m2, m4 and both edges closed; M1, m3 partial. Sibling audit otherwise clean (no other test
ships against an unreachable origin; `ship-protocol.bats` T11 unaffected; no TS test reaches
the ship paths).

1. **M1' — `test_phase_8_gh1119_ship_rebase.py:409-433` undeclared.** Declared amended (push URL
   set to a real bare, fetch URL stays dead); "three amended rows".
2. **m (applied).** `:576` no longer called the rebase; `ls-remote` empty output ⇒ off, `HEAD`
   without `ref:` ⇒ 4; AC-A1 non-GitHub rows qualified by `required: true`; retry edge after
   phantom deletion / rebase conflict added to AC-A11.

### Gate r6 (Opus) — APPROVED

r5 M1' closed (amendment traced through `_rebase_onto_origin_main` and
`_detect_phantom_deletions`; exactly three dead-origin tests in the repo, all declared), m2-m4
closed. No internal contradiction, no op without an AC, no unwritable AC. Minors applied: AC-A1
names a detached `HEAD` (not an unborn one) for the 4 row; the race-only "`D` named but absent"
row dropped (a failed fetch is 4); AC-A11 keeps an `E_SHIP_PUSH_FAILED` row via a failing
`pre-receive` hook.

## §4 Acceptance

RED: `engine_py/tests/test_bd117a_readiness.py` (Part A), `engine_py/tests/test_bd117b_companion_tune.py`
(Part B, frozen on the post-#116 base). Fixtures: a real temp git repo whose `origin` is a real
bare repo (its default branch carries the fixture `bytedigger.json`); `gh` replaced by a fixture
executable reached **both** through `HAL_GH_BIN` and as `gh` first on `PATH` (so `ship.sh`,
`phase_8` and the new modules all hit it — gate r1 M5). It serves recorded JSON for the pinned
GraphQL query (paged), `api user` (or 403), `pr list`, `issue list`, collaborator permission,
comment create / label remove, and appends every argv to a log — the side-effect oracle. The
fixture is **stateful** (gate r2 N1): a comment create appends to the served comments (with the
next `databaseId`), a label removal drops the label; a switch makes a create "succeed" without
appending (the invisible-record row). The push URL is `git@github.com:o/r.git` and a `GIT_SSH_COMMAND` shim (with `GIT_SSH_VARIANT=simple`) runs
`git-upload-pack` / `git-receive-pack` against a local bare repo, so the GitHub-form parse and
real pushes both work (gate r3 m3). The bare repo has a `pre-receive` hook that appends
`PUSH <ref>` to the same log, so ordering between `gh` calls and pushes is observable (m8).
Modules imported lazily (§1q). No mocks of `readiness` / `companion_tune`.

| AC | op | assertion |
|---|---|---|
| AC-A1 | A1 | decision table, one row each: no `origin`, and `get-url` exiting 0 with empty output (the `ship-protocol.bats` mock git) → 0; `bytedigger.json` absent on `D` → 0; `readiness` absent → 0; `required: false` → 0 (all four: no `gh` call in the log); unreachable remote / a detached `HEAD` on the bare (a `HEAD` line without `ref:`) / invalid JSON / `readiness: "yes"`, two push URLs, and, under `required: true`, a non-GitHub push URL and a GitHub Enterprise push URL → `ship` exit 4, `start` exit 0 with `W_READINESS_UNAVAILABLE` on stderr; a local-path and a GitLab push URL with no policy → 0 (URL parsed only under `required: true`); a bare origin whose `HEAD` names an unborn branch → 0; the policy fetch uses `+D:refs/bd/policy` and succeeds after the default branch is force-pushed; `GIT_CONFIG_PARAMETERS` / `GIT_CONFIG_COUNT` setting `remote.origin.pushurl` to a decoy → ignored (scrubbed) |
| AC-A2 | A1 | one row per reason → exit 3, stderr `^E_READINESS_NOT_APPROVED <reason> #<N>$`: `no_issue` (branch `main`), `no_spec_record`, `spec_record_edited` (`lastEditedAt` set), `spec_record_bad` (marker ≠ text hash), `label_absent`, `label_predates_spec` (incl. equal timestamps; label with no event), `approver_not_allowed`, `self_approved`, `approval_consumed`, `spec_changed` (start only; same fixture at ship → 0); positive row → 0 |
| AC-A3 | A1 | `required: true` on `D`; the build branch commits `required: false`, the working tree says `false`, and `remote.origin.url` points at a decoy bare repo whose policy is off while `pushurl` is the real one → still enforced |
| AC-A4 | A1 | branches `high5-x`, `feat/x`, `build/x` → `no_issue`; `gh42-x`, `GH42-x`, `gh42`, `batch/42` → 42; `phase_8`'s re-exported parser returns the same |
| AC-A5 | A1 | fixture `gh` exits 1 / prints non-JSON / fails on page 2 → exit 4, `W_READINESS_UNAVAILABLE`; `readiness.py` killed by an uncaught exception (fixture that raises inside the module via a malformed-but-parseable page) → `ship.sh` exits 4, nothing pushed; every GitHub call in the log carries the `<owner>/<name>` parsed from the push URL, not `gh`'s default repo |
| AC-A6 | A1 | 150 comments; the newest valid record is on page 2, an older approved record on page 1 → the page-2 record is current (`label_predates_spec` when the label predates it); 150 `LabeledEvent`s, the latest on page 2 → that one is the approval event; two records with equal `createdAt` → the higher `databaseId` is current; 150 labels with `label` on page 2 → label present |
| AC-A7 | A1 | a `bd:spec` comment by a non-BD user, newer and malformed → ignored (verdict unchanged); CRLF spec file vs LF record text → same hash |
| AC-A8 | A2 | `post` → one comment create whose line 1 is `<!-- bd:spec sha256=H -->`, H == sha256(normalised rest); then one label removal; same spec again with the label still on and verdict `label_predates_spec` → no new comment, one label removal (F13); > 65 536 chars → exit 3 `spec_too_large`, no comment |
| AC-A9 | A4 | real `scripts/ship.sh --pr`, fixture repo + bare remote, `required: true`, not approved → exit 3, no `PUSH` line, no new ref, no `pr create`; approved → in the log, in this order: consumption comment create, label removal, `PUSH`, `pr create`; `ship.sh` without `--pr` → no readiness call; `HAL_GH_BIN` pointing at the fixture while `PATH`'s `gh` is a stub that exits 99 → fixture used; `HAL_GH_BIN` unset, `BD_GH_BIN` set → `BD_GH_BIN` used |
| AC-A10 | A4 | consumption, on the stateful fixture: after AC-A9's approved ship (label now removed), the same branch again → 0 via retry, no new comment, no removal; another branch → exit 3 `approval_consumed`, no `PUSH`; two consumption records for one event (equal `createdAt`), the lower `databaseId` naming X → X passes, Y refused; an edited consumption record is ignored; comment create fails, or succeeds but stays invisible over 3 re-reads → exit 4, no `PUSH`; visible on the 2nd re-read → proceeds; a second build's `post` before the first consumed → first refused `label_absent` (gate r3 N2); after a consumed approval by X, a human removes and re-adds the label → a new unowned approval event → branch Y passes and owns it |
| AC-A11 | A4 | `phase_8` `ship_to_pr` not approved → `E_READINESS_NOT_APPROVED`, `recoverable is False`, no `PUSH`, `pr create` or PR lookup in the log; unavailable → `E_READINESS_UNAVAILABLE`, `recoverable is True`; a reachable push target with no policy whose `pre-receive` hook exits 1 → `E_SHIP_PUSH_FAILED`, `recoverable is True` (the push-failure path keeps a test after the two amendments); origin `/nonexistent/path` with no reachable policy → `E_READINESS_UNAVAILABLE` (the declared §1x change); approved and consumed, then `E_SHIP_DIRTY_TREE` / `E_SHIP_PHANTOM_DELETION` / a rebase conflict → nothing pushed, and after fixing the cause a re-run passes via retry; ship disabled / on main / zero ahead → skipped as today with no readiness call; after `E_SHIP_PR_FAILED` on an approved run, a re-run passes via retry |
| AC-A12a | A4 | `ship.sh --pr` resume under `required: true`: nothing staged, branch 1 commit ahead of upstream, approved → `PUSH`, `ship_complete: true`; same with no upstream and 1 ahead of `refs/bd/policy` → `PUSH`; nothing staged, 0 ahead → exit 0, no `PUSH`; under `required: false`, nothing staged and 1 ahead → exit 0, no `PUSH` (today's behaviour) |
| AC-A12 | A1 | label added by the BD user: `distinct_actor: true` → `self_approved`; `approvers: ["alice"]` → `approver_not_allowed`; defaults → 0 (documented residual risk) |
| AC-A13 | A | across AC-A1…A12 the log holds no label **add** (`--add-label`, `addLabelsToLabelable`, `POST …/labels`) from any BD-invoked command |
| AC-A14 | A3/A4 | prompt contract: `phase-0-classify.md`, `commands/build.md`, `skills/bytedigger/SKILL.md` contain `--issue`, `gh<N>-`, `issue_mismatch`, the start gate before the first non-scratchpad write for each tier, `awaiting_stage: start`, the PAUSE-POINT exception naming `readiness.required`; the `**Resumable:**` paragraph maps `awaiting_stage: ship` to re-running SHIP only; in `phase-7-synthesize.md` the "7.5 SHIP" heading precedes "State Cleanup" and in `commands/build.md` the SHIP step precedes "7.2 State Cleanup"; both map any non-zero exit under `required: true` to STOP with `awaiting_stage: ship`; the Exit Criterion carries the "unless stopped awaiting approval" clause; the STOP text names the `readiness post` recovery |
| AC-B1 | B1 | both ship paths → PR body in the log ends with `<!-- bd:built -->`, also with `ship_pr_body` set |
| AC-B2 | B2 | fixture → exactly the expected signals: maintainer reopen of an issue closed by a merged BD PR whose PR was **not** updated in the window (in, via the issue listing); by a human-authored PR carrying a forged `bd:built` (out); reopen by the issue's non-maintainer author (out); permission 404 (out); outside the window (out); maintainer relabel of a watched label (in); unwatched (out); by a `bot_logins` actor (out); the readiness label (out); an id already in an earlier genuine `bd:tune` line (out); an id in a forged `bd:tune` line from a non-BD author (in) |
| AC-B3 | B2 | either listing at exactly 1000 rows → `E_COMPANION_TUNE_TRUNCATED`, exit 4; timeline reads are paged; `closedByPullRequestsReferences` is requested with `includeClosedPrs:true`; no comment-body field requested; `api user` 403 with empty `bd_logins` → `bd_logins_required`; malformed `tuning` → `malformed_tuning`; permission lookup 500 → exit 4 |
| AC-B4 | B3 | no signals → exit 0, no model call, no branch; genuine open tuner PR → skipped; a forged one (wrong author) → not a skip |
| AC-B5 | B3 | fixture model editing a non-overridable section → `E_COMPANION_TUNE_REFUSED section_not_overridable`, no push, branch absent |
| AC-B6 | B3 | fixture model output writing a second path → `E_COMPANION_TUNE_REFUSED extra_path` |
| AC-B7 | B3 | valid draft → one push of `bd/companion-tune-<date>-<8 hex>`, one `pr create` to the default branch whose body holds every signal id in the `bd:tune` line; the pushed branch's real `git diff --name-only` == the companion path |
| AC-B8 | B3 | source scan of `companion_tune.py`: no `pr merge`, no push to the default branch, no label add |
| AC-B9 | B3 | invalid current companion on `D` → `E_COMPANION_TUNE_REFUSED current_companion_invalid`, no model call; `check` crash → `E_COMPANION_TUNE_UNAVAILABLE` |
| AC-B10 | B4 | `examples/github-actions/companion-tune.yml` parses, has the weekly `schedule`, the three permissions, runs `collect` then `propose`; `.github/workflows/` has no such file |
| AC-R | registry | the six codes in `error_codes.py` and both `ERROR_CODES.md`; the three modules in `core_manifest.json`; `docs/configuration.md` names `readiness`, `approvers`, `tuning`, `bd_logins`, `--issue`, the engine-build recovery and the Actions PR setting; `CHANGELOG.md` mentions #117; `core-boundary-lint.py` clean |

§1l production side-effect: AC-A9/AC-A10 run the shipped `ship.sh` against a real bare remote
and assert the ref's presence/absence and the order of consumption → push; AC-B7 asserts the
pushed branch's real diff.

## §5 Files (for the implementation lots)

**Lot A (readiness).** New: `engine_py/bytedigger_engine/readiness.py`,
`engine_py/bytedigger_engine/lib/git_blob.py`, `scripts/readiness`,
`engine_py/tests/test_bd117a_readiness.py`. Changed: `scripts/ship.sh`,
`engine_py/bytedigger_engine/workflows/phase_8_post_deploy.py` (parser → re-export; ship guard;
consume), `commands/build.md`, `phases/phase-0-classify.md`, `phases/phase-45-spec.md`,
`phases/phase-7-synthesize.md`, `skills/bytedigger/SKILL.md`, `error_codes.py`, both
`ERROR_CODES.md`, `core_manifest.json`, `docs/configuration.md`, `CHANGELOG.md`.
`phases/phase-5-implement.md` is **not** changed (Plan-Sync stays; the record is the frozen text).
**Lot B (tuning, after #116).** New: `engine_py/bytedigger_engine/companion_tune.py`,
`scripts/companion-tune`, `examples/github-actions/companion-tune.yml`,
`engine_py/tests/test_bd117b_companion_tune.py`. Changed: `ship.sh`, `phase_8_post_deploy.py`
(marker), the registries above.
**Sibling tests (§1a, `--require-clean`; `grep -rlE "ship\.sh|phase_8_post_deploy|_parse_issue_from_branch|ship_to_pr"`
over `tests/`, `engine_py/tests/`, `scripts/ts/__tests__/` at `aa87816`, 22 files):**
`tests/ship-protocol.bats`; `engine_py/tests/`: `test_phase_8_post_deploy.py`,
`test_phase_8_post_deploy_6CBC19FA.py`, `test_phase_8_post_deploy_W8.py`,
`test_phase_8_ship_to_pr_98364258.py` (**amended**: `:465-485` unreachable origin now expects
`E_READINESS_UNAVAILABLE`, `recoverable=True`), `test_phase_8_gh1119_ship_rebase.py`
(**amended**: `:584-601`, same; and `:409-433` `test_ac6_phantom_deletion_blocks_the_ship` —
keep its dead fetch URL and add `git remote set-url --push origin <real bare>`, so readiness
reads a reachable push target with no policy (0), the rebase fetch still fails and is skipped,
and `E_SHIP_PHANTOM_DELETION` still fires),
`test_phase_8_full_suite_delta.py`, `test_gh1124_ship_pr_title.py` (anchored parser; marker
line), `test_C27F057B_phase8_push_seam.py`, `test_3B410663_phase8_write_seam.py`,
`test_9F74246A_phase8_read_seam.py`, `test_ab117afa_preserve_events_log.py`,
`test_8C9F758C_suite_boyscout.py`, `test_ctx_boundary_hardfail.py`,
`test_gh1086_worktree_sync_race.py`, `test_gh1612b_typecheck_baseline_no_tree_mutation.py`,
`test_gh1626c_worker_interpreter.py`, `test_GH1674_injection_missing.py`,
`test_gh878_seam_rename.py`, `test_r_prime_ship2a_list_runs.py`,
`test_r_prime_ship2b_status_run.py`, `test_run_allowlist_1DA29C33.py`; plus the
`core_manifest` readers and `error_codes.py --check` consumers #116 §5 lists, and the tree-wide
CI steps (import smoke, manifest parity, `cyrillic-prose-lint.py`). Ship fixtures with no
`readiness` key on the push target's default branch, no `origin`, or a mock git whose
`get-url` prints nothing (`tests/ship-protocol.bats:61-92`) must stay green unchanged (§1x), except the three amended rows above;
`ship.sh`'s "nothing staged" change keeps exit 0 when nothing is ahead, which is every bats
mock case.
