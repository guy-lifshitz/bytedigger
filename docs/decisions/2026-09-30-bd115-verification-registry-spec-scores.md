# bd#115 — a registry of verifying skills, and a scored spec review in `review.json`

**Status: FROZEN** · **Class:** SYSTEMATIC · **Chokepoints:**
`verification_registry.run_registry` (`engine_py/bytedigger_engine/verification_registry.py`),
the one place anything decides which skills verify a build and runs them; and
`_apply_review_scores` (`workflows/phase_45_spec.py`), the one place a phase 4.5 review
verdict meets its axis scores.

Source: harvest of Warp OSS / Oz (`oz-for-oss` `docs/platform.md`, `.agents/skills/`), findings
#2 and #3. One lot: an extension of phases 4.5 and 5, small and bounded.

## §1 Problem (measured on `aa87816`)

1. **Phase 5 verification is hard-wired.** `phase_5_implement_workflow()`
   (`workflows/phase_5_implement.py:8315`) lists four fixed checks between GREEN and the commit:
   `verify_green_lint_rules`, `verify_security_lint`, `verify_green_passing`,
   `verify_green_typecheck`. A host project cannot add a check of its own without editing the
   engine. Nothing in `engine_py` reads skill frontmatter (`grep -rn frontmatter
   engine_py/bytedigger_engine` finds no parser), and there is no command that runs "every
   check this repo declares" and only reports.
2. **The phase 4.5 review verdict is free text.** `_review_output_schema()`
   (`workflows/phase_45_spec.py:722`) asks for `## Verdict SHIP | REVISE`, findings and prose.
   `_parse_verdict` (`:4200`) reads one token. There is no per-axis judgement a later run, a
   dashboard or a human can compare across builds, and no machine-readable review file: the
   only artifact is `specs/build-plan-review.md`.

## §2 Design

### op1 — `verification_registry.py` (core module, stdlib only)

- **Discovery roots**, in this order, de-duplicated by resolved path:
  `<repo>/skills/*/SKILL.md`, `<repo>/.claude/skills/*/SKILL.md`, then each entry of
  `org_config["verification_skill_dirs"]` (repo-relative directory; `<dir>/*/SKILL.md`). One
  level deep. A root that does not exist is skipped silently; an entry that resolves outside
  `<repo>` is an `errors[]` row (`reason: "outside_repo"`, `path` = the entry exactly as
  configured, e.g. `"../x"`) and is not scanned. A skill directory under any root whose
  resolved path (symlinks followed) is outside `<repo>` is an `errors[]` row
  `{"path": <repo-relative SKILL.md as found>, "reason": "outside_repo"}` and is not read
  (gate edge 7). An `extra_dirs` entry equal to a default root lists each skill once.
- **`parse_frontmatter(text) -> dict | None`**. Deterministic stdlib subset of YAML: the block
  between a first line `---` and the next line `---`. Top-level `key: value` scalars, and one
  level of nesting under `metadata:` (indented `key: value` lines). Quotes (`'`/`"`) around a
  scalar are stripped. `None` when there is no frontmatter block. Any other top-level
  construct (lists such as `allowed-tools:` + `- item`, folded `>`/`|` scalars, nested maps
  other than `metadata`) is skipped, not an error: ordinary skills must never fail the build
  (DesignReview F1). Only a `metadata` value the parser cannot read (flow style `{...}`,
  lists, deeper nesting) AND whose raw text contains the token `verification` raises
  `FrontmatterError`, recorded as an `errors[]` row (`reason: "unsupported_frontmatter"`) —
  never a silent skip of a declaration that may be a verifier; unreadable `metadata` without
  that token is ignored. pyyaml is not used: the result must not depend on what is installed.
- **Selection.** A skill verifies iff `metadata.verification` is exactly `true` (`true`, `True`,
  `TRUE`). Any other value, or no `metadata`, means not registered. A registered skill has:
  `name` (frontmatter `name`, else the directory name), `path` (repo-relative `SKILL.md`),
  `kind` — `"command"` when `metadata.verify_command` is a non-empty string, else `"agent"`.
- **`discover(repo_root, extra_dirs=()) -> Registry`** — frozen dataclass `skills` (sorted by
  `(name, path)`) and `errors` (sorted by `path`). Reads files only.
- **`run_registry(repo_root, *, extra_dirs=(), timeout_sec=300, execute=True) -> dict`** —
  the chokepoint. For each `command` skill (when `execute`): `shlex.split(verify_command)`,
  `subprocess.Popen(argv, cwd=repo_root, stdin=DEVNULL, stdout=<tempfile>, stderr=STDOUT,
  start_new_session=True)`, never `shell=True`; output goes to a temp file, not a pipe, so a
  grandchild holding the pipe open cannot hang the wait; on timeout the whole process group
  gets `os.killpg(SIGKILL)` (DesignReview F2). Output is decoded `utf-8, errors="replace"`.
  Around every command it takes a snapshot = the tree id of the whole working tree:
  `GIT_INDEX_FILE=<tmp copy of .git/index> git add -A && git write-tree` (in `repo_root`,
  tracked + untracked, `.gitignore` respected, real index untouched); a different tree id
  after the command means the check changed the tree. Porcelain status is not enough: at phase
  5 the GREEN edits are still uncommitted, and a check that rewrites an already-modified file
  leaves `git status --porcelain` identical (DesignReview F3). If `repo_root` is not a git work
  tree (the snapshot command fails), no command runs: one `errors[]` row
  `{"path": ".", "reason": "not_a_git_repo"}` and every command skill is `error`. The same
  happens when `repo_root` is inside a work tree but is not its top level
  (`git rev-parse --show-toplevel` differs from the resolved `repo_root`; gate edge 8). Both
  checks (work tree, top level) and the snapshots run ONLY when at least one `command` skill
  will execute (`execute=True` and a command skill exists): an empty registry, an agent-only
  registry or `--list` never calls them, so a phase 5 `git_cwd` inside a repo subdirectory
  (supported, `phase_5_implement.py:4717`) with no command skills stays `ok` (gate R2-1). Every
  git call made by the registry and by the op2 step (including the tamper guard's
  `git show`) runs with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and
  `GIT_OBJECT_DIRECTORY` removed from its env (only the temp index is set explicitly), so an
  inherited git-hook env cannot redirect the snapshot (gate edge 10). On timeout the whole
  process group is killed (`os.killpg(pgid, SIGKILL)`), so no grandchild survives to mutate
  the tree after the post-snapshot. Per-skill
  `status`:
  `pass` (exit 0, tree unchanged) · `fail` (exit ≠ 0) · `timeout` · `error` (could not start:
  `OSError`, `ValueError` from `shlex.split` on unbalanced quotes, empty argv after split, or
  no snapshot possible) · `mutated` (tree changed; wins over `pass`/`fail`/`timeout`) ·
  `agent` (kind `agent`: not run by the engine, listed for the `verify` command) ·
  `listed` (`execute=False`). The engine never restores a mutated tree; it reports it.
- **Report** (JSON-serialisable, keys sorted on dump):
  `{"schema": 1, "skills": [{"name","path","kind","status","exit_code","duration_ms",
  "output_tail"}], "errors": [{"path","reason"}], "summary": {"total","pass","fail","agent"},
  "ok": bool}`. `output_tail` = last 2000 chars of stdout+stderr (`""` when not run).
  `summary.fail` counts `fail`+`timeout`+`error`+`mutated`. `ok` is `summary.fail == 0 and
  no fatal errors`. Every `errors[]` row carries `"fatal": bool`. Non-fatal (reported, `ok`
  unaffected): a skill directory under a DEFAULT root that is a symlink resolving outside the
  repo (sharing skills by symlink is common; such a dir is simply not read — code review #2).
  Fatal: everything else (`unsupported_frontmatter`, `unreadable`, `not_a_git_repo`, an
  `outside_repo` configured `extra_dirs` entry).

### op1a — code-review amendments (2026-09-30)

- **Inline comments.** A `metadata` or top-level scalar value is cut at the first ` #`
  (whitespace then `#`) that is outside a quoted string, then trimmed, then unquoted. So
  `verify_command: 'python3 scripts/check.py'   # optional` gives `python3 scripts/check.py`
  and `verification: true  # on` registers (code review #1).
- **`unsupported_frontmatter` trigger** is a `verification` KEY token inside the unreadable
  `metadata` block (regex `(^|[\s{,])verification\s*:`), not the substring; `tags:
  [verification, docs]` is not an error (code review #8).
- **Process group is always reaped.** After a command exits (any status), its process group
  is killed, so a background grandchild cannot change the tree after the post-snapshot or
  outlive the step (code review #7).
- **Snapshots are chained.** The work-tree probe snapshot is the first command's `before`;
  an `after` that equals its `before` is reused as the next `before` (N+1 snapshots, not
  2N+1; code review #9).
- **One source of failing statuses.** The step names failing skills using the registry's
  exported `FAIL_STATUSES` (code review #10).
- **Ambient `git_cwd`.** When `_resolve_git_cwd_with_source` reports an ambient source
  (`is_ambient_git_cwd(source)`), the step runs the registry with `execute=False` (no foreign
  code runs, no objects are written), writes the report, emits
  `verification_registry_skipped_ambient`, and returns `ok`; `commit_green_code` refuses the
  ambient cwd next, as today (code review #3).
- **A score downgrade is visible to the writer.** On a downgrade `_apply_review_scores`
  appends to the review doc on disk a section `## Score downgrade` naming each low axis and its
  score and stating that the verdict is REVISE, so the retrying writer reads the reason even
  when the reviewer gave no finding (code review #5).
- Not taken: script tamper (§6); untracked build artefacts count as `mutated` by design
  (fail-closed; a check must not leave non-ignored files — documented in `commands/verify.md`
  and `docs/configuration.md`).
- **`verify_main(argv) -> int`** — `python3 -m bytedigger_engine.run verify [--repo DIR]
  [--extra-dir DIR]... [--list] [--timeout SEC]` (`--extra-dir` repeatable, the CLI twin of
  `verification_skill_dirs`, so the command sees the same registry as phase 5 — DesignReview
  F9); `run.py main()` dispatches `argv[1] == "verify"` the same way it
  dispatches `doctor` (`run.py:239`). Prints the report to stdout, writes no file. Exit `0` when
  `ok`, `1` otherwise, `2` on a usage error (argparse). `--list` = `execute=False`.

### op2 — phase 5 step `verify_registered_skills`

- Inserted in `phase_5_implement_workflow()` right after `verify_green_typecheck`, before
  `commit_green_code`.
- Repo = `_resolve_git_cwd_with_source(ctx, prev)[0]`; `extra_dirs` =
  `org_config.get("verification_skill_dirs", [])`; `timeout_sec` =
  `org_config.get("verification_timeout_sec", 300)`.
- Always writes the report to `$SCRATCHPAD/reviews/verification-report.json` (`atomic_write`)
  when a scratchpad resolves, and emits `verification_registry_report` with `summary`.
- `ok` → `status="ok"`, `data={**prev.data, "verification_report_path": <path or None>}` (the
  commit step still gets every key it reads today).
- Not `ok` → `status="error"`, `error_code="E_VERIFICATION_SKILL_FAILED"`,
  `recoverable=False`, the error names the failing skills; `data` carries the report path.
- Empty registry (every repo today, including this one: `skills/bytedigger/SKILL.md` has no
  `metadata`) → `ok`, a report with `total: 0`. No behaviour change for existing hosts.
- `agent` skills never fail the step (the engine cannot run them); they appear in the report.
- **Registry tamper guard (DesignReview F8, applied; scope per gate finding 2/3).** Before
  running anything, the step computes the *HEAD registry*: every `SKILL.md` under a scanned
  root whose HEAD blob (`git -C <repo> show HEAD:./<path relative to repo>`, so a `git_cwd`
  subdirectory works) is a registered verifying skill (or raises the
  `unsupported_frontmatter` case). If any HEAD-registry path is modified (work-tree bytes differ
  from the HEAD blob) or deleted in the work tree → `status="error"`,
  `error_code="E_VERIFICATION_REGISTRY_TAMPERED"`, `recoverable=False`, the error names the
  paths, and no verify command runs. Everything else is allowed: edits to non-verifying
  `SKILL.md` files (e.g. `skills/bytedigger/SKILL.md`) and newly added verifying skills (a new
  check only adds verification; it is discovered and run normally). GREEN must not switch its
  own checks off, and hosts that never declare `verification` see no behaviour change.
- The step and `_apply_review_scores` emit their events through their own module's
  `_emit_safe` (`phase_5_implement._emit_safe`, `phase_45_spec._emit_safe`); the new core
  modules emit nothing (gate R2-5).
- The workflow entry is exactly
  `StepContract(name="verify_registered_skills", execute=_verify_registered_skills)`.

### op3 — the `verify` command (`commands/verify.md`)

One command, report only: run `python3 -m bytedigger_engine.run verify`, then for every
`agent` skill spawn one read-only agent that follows that skill's `SKILL.md` and returns
findings; write one combined report to `$SCRATCHPAD/reviews/verification-report.md`. It edits
no tracked file. `phases/phase-5-implement.md` points to it after GREEN.

### op4 — scored spec review (`spec_review_score.py`, core, stdlib only)

- **Axes** (fixed order, the Oz set): `completeness`, `clarity`, `feasibility`,
  `issue_alignment`, `consistency`. `SCORE_MIN = 1`, `SCORE_MAX = 5`, `LOW_SCORE = 3`
  (an axis below 3 is low).
- **`parse_scores(raw) -> ScoreResult`** (frozen dataclass `status`, `scores`, `reason`):
  looks only under a `## Scores` heading (up to the next `## `) for the first fenced
  ```` ```json ```` block. `status`:
  `absent` (no heading, or no block under it) · `invalid` (not JSON, not an object, a missing
  or extra key, a value that is not an `int` — `bool` is rejected — or outside 1..5; `reason`
  names the first problem) · `ok` (`scores` = dict in axis order).
- **`build_review_json(*, verdict, verdict_before_scores, cycle, result, review_path) ->
  dict`** — `{"schema": 1, "cycle", "review_path", "verdict", "verdict_before_scores",
  "scores_status", "scores", "reason", "min_score", "low_axes"}` (`min_score`/`low_axes`
  `None`/`[]` unless `ok`).
- **Prompt.** `_review_output_schema()` gains a REQUIRED `## Scores` section after
  `## Verdict`: the fenced JSON object with the five axes, one-line rubric per axis, and the
  rule "any axis below 3 means your verdict MUST be REVISE, and `## Findings (structured)`
  MUST contain at least one `root: \"spec\"` finding naming that axis" (so a REVISE driven by a
  score always hands the writer something actionable — DesignReview F11).
- **Chokepoint `_apply_review_scores(result, raw, cycle, review_path, *, may_downgrade)
  -> StepResult`** in `phase_45_spec.py`, called on BOTH exits of `_write_review_doc` when
  `result.status == "ok"` and `result.data` has `verdict`: the main result with
  `may_downgrade = not result.data.get("is_frozen")` (a frozen spec is hand-reviewed; one
  subjective score must not throw it away — DesignReview F5, applied; the scores are recorded
  and event `spec_review_score_low_frozen {cycle, low_axes}` fires instead), the cycle ≥ 2
  `early_result` with `may_downgrade=False`. The
  `early_result` exit only exists when the restricted (or GH605 delta) reviewer prompt was
  used, and those prompts (`_restricted_reviewer_prompt`, `build_delta_reviewer_prompt`) do
  not carry `## Scores` — and a restricted reviewer only re-judges the previous findings, so a
  score-driven REVISE there would find nothing to resolve and loop to the cap. That exit is
  record-only: it writes `review-cycle-<N>.json` (normally `scores_status: "absent"`) and
  never changes the verdict (DesignReview F4).
  - `ok` scores with a low axis, verdict `SHIP`, `may_downgrade` → verdict becomes `REVISE`, event
    `spec_review_score_downgrade {cycle, low_axes}`. The gate then takes its existing REVISE
    path (`E_VALIDATION_RETRY` below the cap, `E_REVIEW_FAILED` at it). No new gate code.
    Known consequences of reusing the gate (DesignReview F5/F6): a downgraded review whose
    structured findings are all `root: "upstream"` takes `E_SPEC_UPSTREAM_REVISE` (terminal,
    escalate to phase 4); all `already-done` takes `ALREADY_DONE`; every downgrade bumps the
    durable GH443 REVISE counter. (A frozen spec is never downgraded, see `may_downgrade`.)
  - `absent` / `invalid` → verdict unchanged (hosts with a custom reviewer template keep
    working), event `spec_review_scores_missing {cycle, status, reason}`.
  - Always writes `specs/review.json` (cycle 1) or `specs/review-cycle-<N>.json` (N ≥ 2)
    next to the review doc (`atomic_write`), and returns the result with `verdict` (possibly
    downgraded) and `review_json_path` added to `data`; every other key unchanged.

### Registries and docs

`E_VERIFICATION_SKILL_FAILED` and `E_VERIFICATION_REGISTRY_TAMPERED` in `error_codes.py` and both `ERROR_CODES.md`; both new modules
in `core_manifest.json` (not in `mypy-strict-modules.txt`, which does not list `facts_pack.py`
either); `docs/configuration.md` (`verification_skill_dirs`, `verification_timeout_sec`);
`phases/phase-45-spec.md` (`review.json`); `phases/phase-5-implement.md` (new step + command);
`CHANGELOG.md` `[Unreleased]`.

### Files NOT in scope (§1v)

`workflows/phase_45_spec_lite.py`, `phase_6_review.py`, the gate machinery in `_gate_on_review`,
`lib/verdict_parse.py`, the existing four phase 5 verify steps, anything under `npm/` or
`packaging/`. The v0.2.0 publication (#114) is not touched.

## §3 DesignReview

**Verdict: APPROVE-WITH-CHANGES** (reviewed against `aa87816`: `phase_5_implement.py:8315`,
`phase_45_spec.py` `_write_review_doc` :4465 / `_write_review_doc_cycle2_result` :4229 /
`_gate_on_review` :4720 / `_fwd_frozen` :5059, `_review_output_schema` :722, `run.py:239`,
`engine_py/core_manifest.json`, `findings_extractor.py`). Chokepoints are well chosen; the
gate really does read only `prev.data["verdict"]`, so a downgrade needs no gate code; the
`## Scores` JSON block cannot be mistaken for the findings block (`_STRUCTURED_SECTION_RE` is
anchored on `## Findings (structured)`); `_parse_verdict` reads only the first token under
`## Verdict`, so adding a section after it is safe. Changes below.

1. **HIGH — strict frontmatter breaks ordinary skills (applied).** As written, any top-level
   list/folded scalar or unreadable `metadata:` would be an `errors[]` row, `ok` false, and
   phase 5 fails on hosts whose skills carry `allowed-tools:` lists — contradicting "no
   behaviour change". Change: unknown top-level constructs are skipped; `unsupported_frontmatter`
   only when unreadable `metadata` mentions `verification`. AC1 extended.
2. **HIGH — timeout can hang (applied).** `subprocess.run(capture_output=True, timeout=)`
   kills only the child; a grandchild (`npm test`, `sh -c`) keeps running:
   the grandchild outlives the timeout (on CPython >= 3.8 `subprocess.run` does not hang, but
   the orphan keeps running and can mutate the tree after the post-snapshot — gate finding 1
   corrected this rationale). Change: `Popen` + `start_new_session=True` + `killpg`,
   output to a temp file, `errors="replace"` decode. AC6 extended with a grandchild case.
3. **HIGH — porcelain snapshot misses mutations at phase 5 (applied).** The step runs before
   `commit_green_code`, so GREEN edits are uncommitted; a formatter-style check that rewrites an
   already-dirty file leaves `git status --porcelain` unchanged. Change: snapshot = `git
   write-tree` of the full work tree through a temp `GIT_INDEX_FILE`. AC5 extended.
4. **HIGH — cycle-2 downgrade is wrong/unreachable (applied).** `early_result` exists only for
   the restricted/delta reviewer prompts, which do not ask for `## Scores` (so production
   always gets `absent`); and if they did, a score-driven REVISE with every prior finding
   RESOLVED gives the restricted cycle nothing to resolve, so it loops to `E_REVIEW_FAILED`.
   Change: `may_downgrade=False` on that exit (record-only). AC16 rewritten.
5. **MEDIUM — frozen specs (applied: `may_downgrade = not is_frozen`, AC19).** A downgrade on an
   `is_frozen` spec routes to `frozen_spec_fallback_to_full`: one subjective `clarity: 2`
   discards a hand-frozen, already-reviewed spec and hands it to the LLM writer. Recommend
   `may_downgrade = not result.data.get("is_frozen")` (threaded by `_fwd_frozen`), with event
   `spec_review_score_advisory`, plus an AC. [Superseded: §2 names the event
   `spec_review_score_low_frozen`; AC19 covers it.] The behaviour is now documented in §2 either way.
6. **LOW — other gate branches (documented, applied as a note).** A downgraded SHIP whose
   structured findings are all `upstream` becomes a terminal `E_SPEC_UPSTREAM_REVISE`; each
   downgrade bumps the durable REVISE counter. Acceptable, but add one AC if kept.
7. **MEDIUM — missing error paths (applied).** `shlex.split` raises `ValueError` on unbalanced
   quotes; `repo_root` not a git repo made mutation detection undefined. Both now `error` /
   `not_a_git_repo`; covered in AC5/AC6.
8. **MEDIUM — GREEN can weaken the registry (applied: tamper guard, AC18).** Discovery reads the work tree
   after GREEN, so the GREEN LLM can flip `verification: true` off or delete a skill and the
   step passes. Recommend: in op2, a registry `SKILL.md` that is modified or deleted relative
   to `HEAD` is an `errors[]` row `registry_changed_since_head` (added files are allowed). [Superseded by §2 op2 tamper guard: modified/deleted
   HEAD-registry skills are `E_VERIFICATION_REGISTRY_TAMPERED`; added skills are allowed and run.]
9. **LOW — CLI/phase 5 registry mismatch (applied).** The CLI had no way to pass
   `verification_skill_dirs`; added repeatable `--extra-dir`, AC8 extended.
10. **LOW — scope: op3 has no AC (applied: kept, AC20 added).** `commands/verify.md` (agent fan-out) is the only
    consumer of `kind: agent` and nothing tests it. Either add a doc-lint AC (file exists,
    invokes `run verify`, `phase-5-implement.md` links it) or move op3 to a follow-up and keep
    `agent` skills as report-only rows. [Superseded: op3 kept, AC20.]
11. **LOW — score-only REVISE gives the writer nothing (applied).** A SHIP with `[]` findings
    and a low axis becomes REVISE with no actionable finding; the prompt rule now requires a
    `root: "spec"` finding naming each low axis.
12. **NOTE — AC14/AC15 fixtures.** A cycle-1 SHIP only survives reconciliation with
    `## Findings (structured)` `[]` AND a prose `## Verdict SHIP` (GH642 fail-closed flips
    otherwise); fixtures must include both. `core_manifest.json` has `core_modules` with
    `facts_pack.py` precedent, AC17 is fine as written.

## §4 Acceptance (RED: `engine_py/tests/test_bd115_verification_registry_spec_scores.py`)

Fixtures build a real temp git repo (`git init`, one commit) with real `SKILL.md` files and
real commands (`python3 -c ...`); no mock of the unit under test.

| AC | Op | Assertion |
|---|---|---|
| AC1 | op1 | `discover` returns exactly the skills with `metadata.verification: true` across `skills/`, `.claude/skills/` and an `extra_dirs` entry; `false`, `"yes"`, missing `metadata`, and no frontmatter are not registered; order is `(name, path)`. A non-verifying skill with a top-level list (`allowed-tools:` + `- Read`), a folded `description: >` and `metadata: {tags: [a]}` (flow style, no `verification` token) yields no skill and no `errors[]` row. |
| AC2 | op1 | `metadata: {verification: true}` (flow style) → an `errors[]` row `unsupported_frontmatter` for that path, skill not registered; an `extra_dirs` entry `../x` → `errors[]` `outside_repo`. |
| AC3 | op1 | `kind`: `verify_command` set → `command`; absent or empty → `agent`. `name` falls back to the directory name. |
| AC4 | op1 | `run_registry`: exit-0 command → `pass`; exit-3 command → `fail` with `exit_code 3` and its stderr in `output_tail`; `ok` false, `summary.fail == 1`. |
| AC5 | op1 | a command that writes an untracked file in the repo → `mutated`, `ok` false; with a tracked file already modified before the run, a command that rewrites that same file again → `mutated` (the side effect is observed through the real working tree; the real `.git/index` is byte-identical before and after). A `--repo` that is not a git work tree → `errors[]` `not_a_git_repo`, no command runs, `ok` false. |
| AC6 | op1 | `timeout_sec=1` with a sleeping command → `timeout`; `timeout_sec=1` with `sh -c "sleep 30 & sleep 30"` (a grandchild holding stdout) → `timeout` returned in under 10 s; a non-existent binary → `error`; `verify_command: "a 'b"` (unbalanced quote) → `error`; none raises. |
| AC7 | op1 | `agent` skills → status `agent`, not counted in `fail`, `ok` stays true; `execute=False` → every skill `listed`, no command runs (a command that would create a file leaves no file). |
| AC8 | op1 | `python3 -m bytedigger_engine.run verify --repo <tmp>` (real subprocess): stdout parses as the report; exit 0 when ok, 1 when a check fails; `git status` of the repo is identical before and after a passing run; `--extra-dir <d>` registers a skill under `<d>` that the run without it does not list. |
| AC9 | op2 | `phase_5_implement_workflow()` step names contain `verify_registered_skills` immediately after `verify_green_typecheck` and before `commit_green_code`, and that step's `execute is phase_5_implement._verify_registered_skills`. |
| AC10 | op2 | the step on a repo with one failing command skill → `status="error"`, `E_VERIFICATION_SKILL_FAILED`, `recoverable is False`, and `$SCRATCHPAD/reviews/verification-report.json` exists on disk with `ok: false`. |
| AC11 | op2 | the step on a repo with no verifying skills → `status="ok"`, every `prev.data` key forwarded unchanged, `verification_report_path` set, the report file on disk has `summary.total == 0`. |
| AC12 | op4 | `parse_scores`: valid block → `ok` in axis order; no heading → `absent`; missing axis, extra axis, `true` as a score, `0`, `6`, broken JSON → `invalid` with a `reason`. |
| AC13 | op4 | `_review_output_schema()` contains `## Scores` and all five axis keys. |
| AC14 | op4 | `_write_review_doc` with a SHIP review whose scores include a `2` → returned `verdict == "REVISE"`, and `specs/review.json` on disk has `verdict_before_scores: "SHIP"`, `verdict: "REVISE"`, `low_axes` naming that axis. |
| AC15 | op4 | `_write_review_doc` with a SHIP review, all scores ≥ 3 → verdict `SHIP`, `review.json` `scores_status: "ok"`, `min_score` correct; with no `## Scores` section → verdict unchanged, `scores_status: "absent"`. |
| AC16 | op4 | cycle 2 (`early_result` path, review with `FINDING_` per-finding lines all RESOLVED) writes `specs/review-cycle-2.json`; even with a `## Scores` block containing a `2`, the returned verdict stays `SHIP` (record-only), and the json has `verdict_before_scores == verdict == "SHIP"`. Cycle 2 on the main exit (free-form review, no `FINDING_` lines) with a low score → `REVISE`, `review-cycle-2.json` written. |
| AC18 | op2 | a repo with two committed verifying skills A and B (B's command writes a marker outside the repo). (a) A edited in the work tree to `verification: false` → `E_VERIFICATION_REGISTRY_TAMPERED`, `recoverable is False`, A's path in the error, B's marker absent. (b) A deleted (`git rm`) → same code. (c) only an untracked new verifying skill C added → not tampered; C is run (its marker exists). |
| AC21 | op2 | a committed NON-verifying `SKILL.md` with an uncommitted edit, no verifying skills → step `status == "ok"`. |
| AC22 | op1 | edges: a `verification: true` line indented under a non-`metadata` key (list item / folded `description: >`) does not register; `extra_dirs=("skills",)` lists each skill once; a symlinked skill dir pointing outside the repo → `errors[]` `outside_repo`, not registered; whitespace-only `verify_command` → kind `command`, status `error`; a command that mutates the tree and then sleeps past the timeout → `mutated`. |
| AC23 | op1 | timeout kills the process group: `sh -c "(sleep 3; touch <tmp>/gc.marker) & sleep 30"` with `timeout_sec=1` → `timeout`, returns in < 10 s, and after a further 4 s the marker does not exist. `run_registry` on a subdirectory of a git repo → `errors[]` `not_a_git_repo`, no command runs. With `GIT_DIR` pointing at another repo in the env, the snapshot still reflects `repo_root` (a mutating skill is still `mutated`). |
| AC26 | op2 | the step with `git_cwd = <repo>/sub` (a subdirectory of a git repo) and no command skills → `status == "ok"`, report `summary.total == 0`. |
| AC27 | op1a | inline comments: `verification: true  # on` registers; `verify_command: 'python3 -c "..."'  # optional` runs and passes; `metadata:\n  tags: [verification, docs]` gives no error row. |
| AC28 | op1a | a non-verifying skill dir under `skills/` symlinked outside the repo → `errors[]` row `outside_repo` with `fatal: false`, report `ok: true`; the phase 5 step returns `ok`. An `extra_dirs` `../x` row has `fatal: true`. |
| AC29 | op1a | a command that starts `(sleep 2; touch <tmp>/late.marker) &` and exits 0 → `pass`; 4 s later the marker does not exist. |
| AC30 | op1a | the step with an ambient git_cwd source (patch `_resolve_git_cwd_with_source` to return the repo and an ambient source string accepted by `is_ambient_git_cwd`) and a command skill whose command writes a marker → `status == "ok"`, marker absent, report skill status `listed`. |
| AC31 | op1a | `_write_review_doc` downgrade case (AC14 fixture): the review doc on disk contains `## Score downgrade` and the low axis name. |
| AC24 | op1 | CLI: `verify --timeout 1` applies the timeout (a sleeping command → `timeout`); `verify --bogus` exits 2. |
| AC25 | op4 | `_write_review_doc` with a SHIP review and an `invalid` scores block (`clarity: 0`) → verdict `SHIP`, `review.json` `scores_status: "invalid"`. Events are captured (as sibling tests capture `_emit_safe`): `spec_review_score_downgrade` on AC14, `spec_review_scores_missing` on the absent/invalid cases, `spec_review_score_low_frozen` on AC19, `verification_registry_report` on AC11. AC13 additionally asserts the prompt states the below-3 ⇒ REVISE rule and the `root: "spec"` finding requirement. AC16 early exit asserts `scores_status` and `low_axes` recorded in `review-cycle-2.json`. |
| AC19 | op4 | `_write_review_doc` with `is_frozen: True` in `prev.data`, a SHIP review and a `2` score → verdict stays `SHIP`, `review.json` records the low axis. |
| AC20 | op3 | `commands/verify.md` exists, invokes `bytedigger_engine.run verify`, and states that it edits no tracked file; `phases/phase-5-implement.md` references `verify_registered_skills` and `commands/verify.md`. |
| AC17 | reg | `E_VERIFICATION_SKILL_FAILED` and `E_VERIFICATION_REGISTRY_TAMPERED` are in `error_codes.py` and in both `ERROR_CODES.md`; both new modules are in `core_manifest.json`. |

## §5 Scope

New: `engine_py/bytedigger_engine/verification_registry.py`,
`engine_py/bytedigger_engine/spec_review_score.py`, `commands/verify.md`,
`engine_py/tests/test_bd115_verification_registry_spec_scores.py`.
Changed: `engine_py/bytedigger_engine/run.py`, `workflows/phase_5_implement.py`,
`workflows/phase_45_spec.py`, `error_codes.py`, `ERROR_CODES.md` (both), `core_manifest.json`,
`docs/configuration.md`, `phases/phase-45-spec.md`, `phases/phase-5-implement.md`,
`CHANGELOG.md`.
Sibling tests (§1a): `test_phase_45_spec.py`, `test_phase_5_implement_228AB822.py`,
`test_34AEB235_green_typecheck_gate.py`, `test_phase_5_gate_structured_verdict.py`,
`test_contracts.py`, plus every test that asserts the phase 5 step list or the review schema
text (found by grep in RED).

## §6 Open questions

- A GREEN that edits the *script* a `verify_command` invokes (`python3 scripts/check.py`) is
  not caught by the tamper guard, which covers `SKILL.md` only. Follow-up if needed.
- The engine writing files inside a non-ignored in-repo path while a check runs would show as
  `mutated`; the scratchpad and event log live outside the tree or are gitignored today.
- Downgrade + all-`upstream` findings → `E_SPEC_UPSTREAM_REVISE` is the existing gate path,
  covered by the existing gate tests; no separate AC (DesignReview F6 / gate finding 12).

- Should a failing verification skill retry GREEN (like typecheck) instead of stopping? v1
  stops (`recoverable=False`); a retry loop is a follow-up if hosts ask.
