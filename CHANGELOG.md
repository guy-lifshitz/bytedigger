# Changelog

All notable changes to ByteDigger are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning: the plugin (`.claude-plugin/plugin.json`), the npm pointer package
(`npm/`), and the Python engine (`engine_py/pyproject.toml`) all version together
as `0.x` until the engine API stabilizes. The historical `v1.0.0` tag predates
the Python engine and refers to the original bash plugin (see Pre-history).

## [Unreleased]

### Removed

- **Phases 1-4 dropped (bd#89 P2a).** The pipeline is now 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7 for every tier. The engine no longer registers `phase_1_discovery`, `phase_2_explore`, `phase_3_clarify` and `phase_4_architect` (10 workflows remain), and the `architect` and `explorer` agents, the `graph_source` module, the frozen short-circuit helpers in `skip_logic` and the `HAL_FROZEN_SHORT_CIRCUIT` flag are gone, with the six `E_CLARIFY_*` / `E_EXPLORE_*` error codes and the matching timeout-policy and model-role keys. `build-gate.sh` and `build-phase-gate.ts` no longer check `phase_4_architect` or `build-architecture.md`; `plan_review: pass` is now required for all tiers at 4.5 and 5. A resumed run whose recorded stage names a dropped phase fails with `KeyError` ("not registered"); restart it from `phase_45_spec`.
- **One spec path, one review path (bd#89 P2b).** The engine no longer registers the `phase_45_spec_lite` and `phase_6_review_simple_fastpath` stages; SIMPLE builds use the full `phase_45_spec` and `phase_6_review` workflows. The `spec_lite.writer` and `spec_lite.reviewer` timeout-policy keys and the lite oracle member are gone. No flag or error code is removed. A resumed run whose recorded stage names a dropped stage now fails with `KeyError` ("not registered"); restart such a run from the full stages.
- **Surgical revise and the restricted cycle-2 reviewer dropped (bd#89 P3a).** Cycle 2 of phase 4.5 now always asks the writer for a full revised spec (the delta retry) and the reviewer uses the same full prompt as cycle 1, so a cycle-2 reviewer can raise new findings and the score downgrade applies to it. The `HAL_SURGICAL_REVISE` and `HAL_DELTA_REREVIEW` flags and the `surgical_revise_*` / `delta_rereview_*` events are gone. A resumed run that replays a cached patch-array writer result re-runs the same cycle as a full revise.

### Fixed

- **A malformed oracle freeze row refuses instead of escaping (bd#158).** `find_last_freeze` and `verify_against` now shape-check the freeze/amendment payload (`check_freeze_payload`: object, string `digest`/`scope_digest`, list members of string or `{path, digest}` strings, list-of-string `scope`, no NUL in a path) and raise `OracleMalformedFreeze`, an `OracleRefusal` with `E_ORACLE_INDETERMINATE`. Before this, a hand-written or corrupted row raised `KeyError`/`TypeError`/`AttributeError` past `run.py` and was reported as a non-oracle code. A re-entry amendment over a malformed previous row refuses rather than recording a non-string `previous_digest`; a path-layer `ValueError`/`OSError` during verify is `E_ORACLE_INDETERMINATE`. The `oracle verify` CLI keeps rc 2 for a malformed row. No new error code.

### Added

- **`effective_model` hook on `register_backend`; the hard-gate floor checks the model a backend actually runs (bd#103).** `register_backend(..., effective_model=callable)` declares the model a backend will run for a requested one. `_dispatch_backend` resolves it once and checks the hard-gate floor against it. The attestation (`model_requested`, AC-M3 amended in `AUTHORSHIP_SPEC.md`) and the R3.3 pin check use the same value, and a remap is logged as `effective_model_remapped` so the requested model stays in the log. If the hook raises or returns no usable name, `effective_model_unresolved` is emitted: a hard gate is refused with `E_HARD_GATE_MODEL_DOWNGRADE` and no backend call, and a worker degrades to the requested model. `pydantic-openai` registers `PYDANTIC_BACKEND_DEPLOYMENT` through the hook, and its adapter-local gate refusal is gone, so a deployment that meets the floor now passes a gate. The floor checks the declared name, not the weights served behind it. Effort stays keyed on the requested model. No new error code.

- **Readiness review gate in engine Phase 6 (bd#141 item 6).** New optional policy key `readiness.review_label` and stage `review`: when set under `required: true`, `phase_6_review` refuses to start the review (`E_READINESS_NOT_APPROVED`, non-recoverable) until a human has added that label after the current spec record, before any reviewer spawns. `review` never consumes or posts; an unavailable read, an internal error or an ambient git cwd fails open and emits a `readiness_review_verdict` event. No new step or error code.
- **`reverted` signal for companion tuning (bd#141 item 5).** `companion-tune collect` now reads a third kind of maintainer correction: a non-BD PR merged inside the window by a maintainer whose body has the exact line `Reverts <owner>/<repo>#<N>` for this repository, where PR `<N>` is a merged BD-built PR, becomes one `reverted` signal (`id` `reverted:<revert PR node id>`, `pr` the reverted PR, `action` `null`). A dead target number is skipped; any other `gh pr view` failure is `E_COMPANION_TUNE_UNAVAILABLE`. Direct-push reverts are not read. No new error code.
- **Readiness start gate in engine Phase 5 (bd#141 item 6).** `phase_5_implement` now calls `readiness.verdict(repo, "start", spec)` as the first statement of the validation-cycle composite: under `readiness.required: true` an unapproved build stops with `E_READINESS_NOT_APPROVED` (non-recoverable) before any RED write, instead of at the Phase 8 ship gate. `start` never consumes the approval; an unavailable read, an internal error or an ambient git cwd fails open and emits a `readiness_start_verdict` event. No new step or error code.
- **`check_bd_l2` reads real event logs (bd#155).** `check_bd_l2` now reads the on-disk `event_type` key that `EventLog.append` writes, as well as the harness `type` shape, through one shared resolver (`conformance/_event_type.event_type_of`, `event_type` first, `type` as fallback) that `check_bd_l3` also uses; before this, every R2.x verdict on a production log was `not-checked` and `failed` was unreachable. An event carrying both keys is now one type, not two.
- **`bd_l3` host CLI; the checker reads real event logs (bd#141, item 4 c/d/e).** `python -m bytedigger_engine.conformance.bd_l3 --event-log PATH [--run-id ID]` prints one JSON line (`passed`, `requirements`, `violations`, `labels`, `complaints`, `events_read`, `run_id`) and exits 0 on any report, 2 on usage or input errors (stderr only); read-only. `check_bd_l3` now reads the on-disk `event_type` key that `EventLog.append` writes as well as the harness `type` shape; before this, R3.x could not reach `failed` on a production log. The same defect in `check_bd_l2` is tracked in #155; an oracle verify CLI (item 4 b) in #154.
- **`oracle verify` host CLI; the event-log reader refuses invalid UTF-8 and non-object lines (bd#141, item 4(b), bd#154).** `python -m bytedigger_engine.conformance.oracle verify --event-log PATH --run-id ID --scratchpad-dir DIR` prints one JSON line (`outcome`, `code`, `token`, `message`, `event_type`, `frozen_digest`, `current_digest`, `run_id`) and exits 0 on any verdict (`verified`, `unfrozen`, `mutated`, `indeterminate`), 2 on usage errors, an empty `--run-id` or a malformed freeze event (stderr only); read-only. `read_log_events` now maps invalid UTF-8 and a non-object JSON line to `E_ORACLE_INDETERMINATE` instead of letting `UnicodeDecodeError`/`AttributeError` escape.
- **Role template declared on the `injections` channel in every phase (bd#141, item 4(d)).** All 17 builders that prepend the org role template now read it once (`_role_template`), record `{source_id, content}` under `data["role_template"]` (`_role_template_record`), and every dispatch passes `injections=_declared_injections(<data dict>)`, including the GREEN and fix retries and the COMPLEX satisfaction pool. With `role_template_path` set, R3.2 is now `passed` on those steps instead of `not-checked`; prompt bytes are unchanged. `_maybe_role_template` stays as the string view. Other non-literal prompt segments remain tracked in bd#147.
- **File-sourced prompt segments declared on the `injections` channel (bd#147).** The decision doc in the spec prompt (whole, or head and tail when truncated), the in-scope test files in the fix prompt, and the directed-repair artifact are now declared as `InjectedBlock`s, so R3.2 is `passed` on those dispatches. Builders record `data["injected_blocks"]` (`_injected_blocks_record`), bound to the final prompt's hash; `_declared_injections` honours a record only alongside the prompt it was built with, so forwarded records are inert and malformed elements fail closed at the chokepoint. Prompt bytes are unchanged. Class boundary in AUTHORSHIP_SPEC §4; follow-ups #150, #151, #152.
- **Subprocess observed model (bd#141, item 4(e)).** `_invoke_subprocess` now writes `observed_model` (the last root assistant `message.model`, falling back to the first root `system/init` model, `<`-prefixed placeholders skipped), so the R3.3 pin check is no longer inert on the `claude -p` path: a family mismatch fails with `E_MODEL_PIN_MISMATCH`, an absent model stays not-checked. `bd_l3.AWAITING_PRODUCER` is now empty.
- **Check ladder (bd#141, item 3).** New `bytedigger_engine/check_ladder.py` (`python -m bytedigger_engine.check_ladder prescreen --gate-input PATH [--findings PATH] [--classifier-cmd JSON] [--mode shadow|enforce] [--threshold F] [--timeout-s F] [--log PATH]`) puts a pre-screen rung in front of the validation gate: a MAJOR script finding or (enforce only) a confident classifier reject returns `reject`, everything else `escalate`; it never approves and fails open on classifier error or timeout. Phase 5 runs it in shadow mode when `org_config["prescreen"]["classifier_cmd"]` is set, emits a `prescreen_verdict` event and never skips the gate.
- **Close gate (bd#141, item 7).** New `bytedigger_engine/close_gate.py` (`python -m bytedigger_engine.close_gate --transcript PATH --spec PATH [--threshold N] [--vocab PATH] [--cwd DIR] [--state-dir DIR --run-id ID]`) returns a `fire` / `clear` / `latched` / `mentioned` / `no-claim` / `no-turn` / `no-spec` verdict when a turn claims "done" after N or more main-chain tool calls without a successful edit of the bound spec; optional once-per-run latch.
- **Claim-vs-evidence check and tool-call loop detector (bd#141, items 1-2).** New
  `bytedigger_engine/claim_evidence.py` (`python -m bytedigger_engine.claim_evidence --transcript PATH
  [--vocab PATH]`) returns a `fire` / `clear` / `no-runner` / `no-claim` / `no-turn` verdict when a turn
  claims "done / tests pass" while the same turn's runner output is red; the vocabulary is data, hosts add
  their own tokens. New `bytedigger_engine/loop_detector.py` (`python -m bytedigger_engine.loop_detector
  --state-dir DIR [--log PATH]`) flags repeated, alternating and thrashing tool calls from a hook payload on
  stdin, advisory only. Env: `BD_LOOP_DETECTOR=0`, `BD_LOOP_WINDOW`, `BD_LOOP_REPEAT`, `BD_LOOP_THRASH_SPAN`,
  `BD_LOOP_THRASH_CALLS`, `BD_LOOP_THRASH_FAILS`, `BD_LOOP_COOLDOWN`.
- **Subagent write guard (bd#133).** New PreToolUse hook `hooks/worker-write-guard.sh`
  (logic in `hooks/worker_write_guard.py`) on `Write|Edit|MultiEdit|NotebookEdit`. During an active
  build it blocks subagent writes to `build-state.yaml`, `build-metadata.json`,
  `build-red-output.log`, `build-green-output.log` and `.bytedigger-orchestrator-pid`, and
  confines `synthesizer` to `<scratchpad>/reviews/`. These rules (R5–R7) never apply to the
  main thread, which is blocked only on malformed tool input; unreadable input fails closed. Known limits (Bash writes and
  others) are listed in `docs/security.md`. Hook-less backends and engine workers started as `claude -p` get no guard.
- **Weekly companion tuning (bd#117, Part B).** New `scripts/companion-tune collect|propose`
  (`bytedigger_engine/companion_tune.py`). `collect` reads maintainer corrections (a reopened issue closed
  by a BD-built PR, a watched label added or removed) into `signals.json`; `propose` asks one model call for
  a new `bytedigger/companions/<core>.md`, refuses any diff beyond that file, runs the #116 checker, and
  opens a PR for a human. Config: `tuning.bd_logins`, `bot_logins`, `watched_labels` in `bytedigger.json`.
  Every PR opened by `ship.sh` and `ship_to_pr` now ends its body with `<!-- bd:built -->`. Error codes
  `E_COMPANION_TUNE_REFUSED`, `_DRAFT_INVALID`, `_UNAVAILABLE`, `_TRUNCATED`. New
  `examples/github-actions/companion-tune.yml`.
- **`plan-approved` readiness gate (bd#117, Part A).** A repo opts in with `"readiness": {"required":
  true}` in `bytedigger.json` on the default branch of the repository BD pushes to (optional keys
  `label`, `approvers`, `distinct_actor`). `scripts/ship.sh --pr` and the engine's `ship_to_pr` then
  refuse to push or open a PR unless the bound issue (`gh<N>-...` / `batch/<N>` branch) carries a
  current approval: a spec record posted by BD (`scripts/readiness post --spec`) followed by a human
  adding the label. An approval is consumed by the first branch that ships it and the label is
  removed. New `scripts/readiness check|post`, `lib/git_blob.py`, error codes
  `E_READINESS_NOT_APPROVED` and `E_READINESS_UNAVAILABLE`. `/build --issue <N>` and a start gate in
  Phase 0/4.5 bind the build to the issue. An unreachable push target at ship time is now
  `E_READINESS_UNAVAILABLE` (recoverable) instead of `E_SHIP_PUSH_FAILED`. Repos without the key see
  no change and no `gh` call. `ship.sh --pr` now needs `python3`.
- **Verification skills registry (bd#115).** A `SKILL.md` with `metadata.verification: true`
  under `skills/*/`, `.claude/skills/*/` or an `org_config["verification_skill_dirs"]` entry
  declares a check. Phase 5 runs every such skill that has a `metadata.verify_command` in the new
  step `verify_registered_skills` (after `verify_green_typecheck`, before `commit_green_code`) and
  writes `$SCRATCHPAD/reviews/verification-report.json`. A failing, timed-out or tree-mutating check
  stops the phase with `E_VERIFICATION_SKILL_FAILED`; a verifying `SKILL.md` from HEAD modified or
  deleted by GREEN stops it with `E_VERIFICATION_REGISTRY_TAMPERED`. Repos with no verifying
  skills see no change. New `python3 -m bytedigger_engine.run verify` and `commands/verify.md`
  run the same registry on demand, report only. Timeout: `verification_timeout_sec` (default 300).
- **Scored spec review (bd#115).** The phase 4.5 reviewer now returns a `## Scores` block
  (`completeness`, `clarity`, `feasibility`, `issue_alignment`, `consistency`, 1 to 5). Every review
  is recorded in `specs/review.json` (`specs/review-cycle-<N>.json` for later cycles). A SHIP with an
  axis below 3 becomes REVISE, except for frozen specs and the restricted cycle-2 review, which only
  record it. Missing or invalid scores never change the verdict.
- **Skill companions (#116).** A host can extend a core skill with a committed
  `bytedigger/companions/<core-id>.md` whose frontmatter says `specializes: <core-id>`. Only the
  H2 sections the core lists in `metadata.overridable` can be extended; each is appended inside
  `bd:local` markers. `skills/bytedigger/SKILL.md` declares an empty `## Project conventions`
  section. New `scripts/skill-companion {render|check}`; Phase 0 and `/build continue` run
  `render` first. An invalid companion exits 3 (`E_SKILL_COMPANION_INVALID`) and stops the build;
  an unrunnable checker falls back to the core skill with `W_SKILL_COMPANION_UNAVAILABLE`.

### Changed

- **Dropped the DevOps, canary and smoke stages (bd#89, P1).** Removed the `phase_0_6_artifact_detect`, `phase_5_devops_scan`, `phase_devops_pipeline`, `phase_5_integration_canary` and `phase_6_smoke` workflows and `_standards_context`; none was reachable from the /build flow. `_select_reviewers` and `_review_plan` no longer take `artifact_type` (an `org_config["artifact_type"]` key is ignored), `phase_45_spec` no longer writes `integration/canary-meta.json` or emits `canary_integration_parsed`, and the DevOps steps are gone from the /build docs, templates and `build-gate.sh`. Removed error codes `E_CANARY_*`, `E_DEVOPS_SCAN_*`, `E_SMOKE_*` and the flags `HAL_DEVOPS_SCAN_{GATE,CONFIG,ALLOWLIST}`.
- **Shared frontmatter parser (#116).** `parse_frontmatter` moved to
  `bytedigger_engine.lib.frontmatter` (still importable from `verification_registry`). It now
  ignores a UTF-8 BOM and reads CRLF as LF, so a BOM or CRLF `SKILL.md` with
  `metadata.verification: true` is registered instead of skipped. An unreadable `metadata` block
  that mentions `overridable` is now an `unsupported_frontmatter` error, like one that mentions
  `verification`.

- **Role template hardening (bd#119).** `org_config["role_template_path"]` is now read by one
  bounded, fail-closed reader (`role_template.py`): a regular UTF-8 file of at most 64 KiB
  (65536 bytes), no NUL, non-empty, opened through a single file descriptor. Failures raise the
  new `CodedStepError` subclass and the engine converts it once, in `_execute_step`, to an error
  result with the new code `E_ROLE_TEMPLATE_INVALID` (nine reason tokens, not retried, no file
  content in messages). Phase 2 reads the template once and reuses the block. Documented in
  `docs/configuration.md` and `docs/security.md`.
  Narrowing: the discovery goal "clearly delimited" is narrowed to "bounded": the template is
  bounded, not delimited. No delimiter is added in this release.
  Follow-up (#125): route the template through the bd#116 companion contract (#123), and bound or
  delimit its content.

- **Role template errors fail the step (bd#119).** A configured `role_template_path` that is
  missing, unreadable, oversized, not UTF-8, NUL-bearing, empty or of a non-string type now fails
  the step with `E_ROLE_TEMPLATE_INVALID` instead of being skipped or crashing. On the full phase 6
  the abort handler also runs and writes its `NOT_ASSESSED` stub; the simple fast path halts
  without one.

### Fixed

- **An unreadable or malformed effort config no longer passes silently (bd#107).** Effort lookup now goes through one resolver, `_resolve_effort`. When the models config can't be read or parsed, or a gate pin names a family it can't resolve, `_dispatch_backend` emits an `effort_config_invalid` event (`backend`, `step_name`, `hard_gate`, `problem`, `detail`) and logs one WARNING per distinct problem. A hard gate with an unreadable or malformed config is refused with the new `E_GATE_EFFORT_CONFIG_INVALID` (non-recoverable) and the backend is not called. Every other case still dispatches, at effort `None`. `_load_effort` and `_load_effort_gate` keep their None/no-raise contract.

- **Callers can no longer forge adapter observations (bd#145).** `_dispatch_backend` drops the
  reserved observation fields (`observed_model`, `observed_tools`, `worker_written_paths`,
  `manifest_source`, `mcp_server_losses`) from `extra_data` before any backend sees it, with one
  warning naming them. On the in-session, straggler and reference-backend paths a caller value used
  to override the adapter's report and reach the R3.3 / R3.5 checks.

- **Workers write their own deliverables (bd#127).** The explorer, architect and synthesizer
  agents now get `Write` for their one deliverable path (findings, approach, `learnings-raw.md`),
  and the phase 2/4/7 prompts have the orchestrator verify the file on disk and re-prompt on a
  miss instead of relaying text. Gates 4 and 7 check the deliverables on disk (best-effort
  nudge). Gate 7 no longer crashes when `learning_backend` or `learnings_extracted` is absent.
  `learning-store.sh extract` reports `learnings_parse_errors` and prints a `WARN` when lines do
  not parse, instead of silently storing 0.
- **`ship.sh` ships commits ahead of the base, and the PR carries the review evidence (bd#131).**
  `ship.sh --pr` now pushes every commit ahead of `@{upstream}` (else `refs/bd/policy`) under any
  readiness mode, stages tracked changes (modified or deleted) on top of `files_modified`, and warns
  about untracked files it does not ship. A detached HEAD or unmerged paths are refused before the
  readiness check. The PR title and commit subject come from the spec's H1 (project `<prefix>:`
  convention, at most 72 characters); the PR body carries the spec's Scope and Follow-ups sections and
  the review fields from `build-state.yaml` (new `scripts/ship_pr_text.py`; any helper failure falls
  back to the task string). `build-*-cycle*.md` and `.bytedigger-sessions.json` are now gitignored and
  deleted by Phase 7 State Cleanup.
- **Phase 5 typecheck baseline sees untracked files (bd#168).** The clean-tree check in front of the
  typecheck baseline (`_tree_identical_to_head_for_paths`) only ran `git diff --quiet HEAD`, which
  cannot see untracked files, so a GREEN that only added a module with a type error was judged
  "identical to HEAD", the baseline degraded to `None` and the errors became warnings. It now also
  runs `git ls-files --others --exclude-standard` and reports "not identical" when anything is
  untracked, so the baseline is computed (0 for a new module) and the delta gate can block. A git
  failure or exception on either read also returns "not identical" and emits
  `baseline_tree_identity_check_failed` (severity `warning`) instead of failing silently.
- **D3 prohibition gate reads negations on word boundaries (bd#169).** `_D3_NEGATION_RE` no longer
  matches inside longer words ("Whenever", "casino", "nevertheless", "do nothing"), so a spec that
  modifies a path the task asks for is no longer turned into REVISE. `_D3_MUTATION_VERB_RE` now also
  counts the noun forms `modifications`, `edits` and `changes` ("no changes to `src/z.py`"); known
  limit: "`x` never changes in `src/a.py`" still prohibits `src/a.py`.
- **Cross-tree auto-revert keeps edits that predate the run (bd#170).** The RED, GREEN and fix steps
  now snapshot the main checkout's dirty tracked paths just before the worker runs. After the run, an
  owned cross-tree path that was already dirty is no longer reset to HEAD: it is held back with a
  `cross_tree_revert_prestate_refused` event (`changed_since_start` says whether the worker touched it
  again) and `cross_tree_prestate_refused_files` on the result. A missing or unusable snapshot holds
  back every owned path (fail closed). Clean paths are reverted as before.

## [0.2.0] — 2026-09-30

### Changed — BREAKING

- **`phase_5_implement` now refuses to run unless the acceptance criteria it is about to implement
  were recorded first.** Run it with `--event-log PATH`, where `PATH` is the same log the spec
  phase (`phase_45_spec` or `phase_45_spec_lite`) wrote to earlier in the build. Without that, the
  phase stops before it starts and reports `E_ORACLE_UNFROZEN` with exit code 1.

  **Who this affects.** Only callers that invoke `python3 run.py --workflow phase_5_implement`
  directly *without* `--event-log`. If you drive builds through the supplied driver, nothing
  changes — it already passes `--event-log` on every phase. Measured before shipping: no such
  caller exists in this repository, and no production build path omits the flag.

  **What to do.** Add `--event-log` (and a `--run-id` shared with the spec phase) to the
  invocation, so the run has a log to look the recorded criteria up in:

  ```
  python3 run.py --workflow phase_45_spec   --run-id BUILD1 --event-log .bytedigger/events.jsonl ...
  python3 run.py --workflow phase_5_implement --run-id BUILD1 --event-log .bytedigger/events.jsonl ...
  ```

  **Why it is not optional.** The point of the check is that the criteria cannot be edited by the
  actor being judged: they are hashed when the spec phase ends and re-checked before and after the
  implementation runs, so an implementation that rewrote its own acceptance criteria mid-flight is
  refused rather than accepted. A run with no log has nowhere to have recorded them, so it cannot
  be distinguished from one whose criteria were removed — and treating "nothing was recorded" as
  "nothing changed" would make the check announce a pass it never performed. Other workflows are
  unaffected and still run with or without a log. (#8)

### Added

- **`AC-C5` — the quantifier-completeness lint.** `conformance.quant_lint` gains
  `lint_quantifier_completeness(text) -> list[Finding]` and the frozen two-field `Finding`, replacing the
  import-only placeholder. Three independent checks over a spec document's own declarations: a collection
  level with no non-uniformity row, a row that does not enumerate the reductions its level admits, and an
  interception seam that has not pinned its attribute path, binding time and normalisation. It reports rather
  than raises — on a malformed document as much as a non-conformant one — so "conformant" and "unparseable"
  stay distinguishable to a caller. It checks that a document *documents* its quantifiers; it does not verify
  that the fixtures a document cites exist or discriminate, which does not mechanise. Nothing consumes it yet.
  (#39)
- **A driver seam that resumes a task instead of restarting it.** `run.py --task-begin PHASES --run-id ID
  --event-log PATH` reads the run's event log and answers where the task continues: resume at the failed
  phase, stop (a human is needed: `escalate`, `E_RED_WORKTREE_DIRTY`, `E_SPEC_DEFECT_BUDGET`), reroute to
  `phase_45_spec` (`E_SPEC_DEFECT`), or done. It also admits one run under a per-task cap (default 3 runs /
  $60, `--task-max-runs`, `--task-max-cost-usd`) and refuses with `E_TASK_CAP_REACHED`. `--task-reset REASON`
  is the operator escape. `templates/driver-resume.sh` is a driver built on it that keeps one run id per task,
  so finished phases and steps are served from the engine's caches. `workflow_finished` now carries
  `error_code` on `error` and `escalate`. (#85)

### Changed

- **A gate failure retries its step with the findings instead of ending the phase.** (#85)
  - The deterministic spec gates and the spec reviewer spend separate budgets (`spec_gates`,
    `spec_review`, one retry each), so a lint retry no longer uses up the reviewer's retry.
  - Spec lint, cite-lint and preflight-batch findings now send the writer back with those findings instead
    of stopping the phase. Infrastructure failures (driver missing, timeout, blind cite-lint) stay terminal,
    and `phase_45_spec_lite` keeps lint terminal.
  - A satisfaction FAIL in `phase_6_review` re-runs the fix step with the evaluator's findings, up to two
    times. Evaluator-format failures and a degraded review stay terminal.
  - A terminal exit now invalidates the step sentinels of every cycle, not only the last.

### Fixed

- **The install-platform scan no longer reads local build residue as a shipping platform.**
  `scan_domain` walked the tree without asking git about a file's status, so leftovers from a
  local package build (measured: `packaging/pypi-pointer/bytedigger.egg-info/`, untracked and
  git-ignored) failed the platform-registry guard — green in CI's clean checkout, red on the tree
  of whoever built the package. The domain is now derived from git (`git check-ignore --stdin`)
  rather than from a list of build-artifact directory names, so the next packaging format does
  not reproduce it. In a tree without git (unpacked sdist, exported archive) the scan fails open:
  the full declared domain, exactly as before. (#33)

## [0.1.2] — 2026-07-29

### Fixed

- **`pip install bytedigger` now prints and installs a command that works.** The npm wrapper
  advertised an install form that did not resolve, and the same broken form was repeated in the
  PyPI pointer README and in `package_meta.install_hint`. All install forms now come from one
  canonical dictionary (`scripts/install_forms.py`), and the set of places allowed to state one is
  an enforced registry — a new site that hand-writes a form fails the suite. (#20, #25)
- **Pointer pin follows the canonical version.** `bytedigger` pinned `bytedigger-engine` to a
  literal that could drift from the engine's actual version; the pin is now derived and checked by
  `version_parity.py`. This is the defect that made `bytedigger 0.1.0` hand out a stale engine. (#19)
- **Three phases and two checks no longer report a pass they never earned** — a gate that could not
  reach its subject reported success instead of refusing. (#5)
- **CI on `main` unwedged**, and a silent-skip path made loud. (#14, #15)

### Added

- **Conformance package (`BD-L2`)** — shared contracts and packaging for falsifiable conformance
  checks. (#26)
- **Attested authorship and inputs (`BD-L3`)** — attestation is emitted if and only if a dispatch
  actually happened. (#31)
- **Engine conformance emissions** — `phase`, `run_identity`, `phase_artifacts`. (#23)
- **Event-log path override, worktree in-use veto, and Red-cell shape lint**, ported from upstream. (#16)

### Changed

- Clean-room verification runs on `ubuntu-latest` instead of a self-hosted docker label. (#6)

## [0.1.1] — 2026-07-27

### Added

- **PyPI pointer package built from the repo** (`packaging/pypi-pointer/`). The `bytedigger`
  name was previously published from an untracked working copy, and its pin drifted: `bytedigger
  0.1.0` required `bytedigger-engine==0.1.0` while the engine had moved to `0.1.1`, so
  `pip install bytedigger` handed out a stale engine. The source now lives in the repo and its
  version is a sixth declaration checked by `scripts/version_parity.py`.

### Added

- **Spec-writer rule 9** — NEW-symbol citation-form ban in the spec-writer prompt: a symbol that does not exist yet may not be cited in path:line form (ported from HAL GH934). (#44)
- **Agent-SDK stderr-tail capture** — LLM subprocess failures now carry a stderr tail and are classified as external-outage vs build-fault (ported from HAL GH933). (#45)
- **Starter `constitution.md`** — shipped in the repo root so the `constitution_path` config default resolves out of the box. (#43)
- Digger-1983-style promo card in docs. (#37)

### Changed

- README rewritten around the software-factory thesis: value-first hero, 6-axis comparison table, shift-left security, deterministic-first economics. (#28, #33, #34, #36, #41, #42)
- `docs/article.md` rewritten for the engine era — verified specs, killed review loop, economics. (#27, #35)
- Dependencies bumped across the board (TypeScript 7.0.2, bun-types 1.3.14, DBOS 2.27.0). (#40)

## [0.1.0] — 2026-07-16

First release built around the **Python engine** (`engine_py/`) — a deterministic
state machine that drives the whole pipeline (research → spec → failing tests →
implementation → review) with TDD at the core and LLM agents as replaceable
workers. Matches the `0.1.0` version of `bytedigger-engine` (PyPI /
`engine_py/pyproject.toml`) and the `bytedigger` npm pointer package.

### Added

- **Engine core** — strict manifest-driven extraction of the engine from its upstream host: state machine, event log, verdict gates, deterministic lints (stub-passability, cite-verify, scope allowlist), crash-resume from success sentinels. (#15)
- **Test suite** — 321 hermetic pytest tests imported with the engine (no DBOS dependency in the test lane). (#21)
- **Product wrapper** — engine README, keyless verified-TDD demo (`examples/verified-tdd-run/`), custom-backend example (`examples/library/custom_backend.py`), backend docs. (#22, #23)
- **Security extraction** — OWASP ASVS-derived secure-codegen defaults, semgrep + gitleaks gate assets shipped in the package, security policy docs, path-closure test. (#24)
- **CONTRIBUTING.md** and the npm pointer package. (#25)
- Python 3.9 test-collection compatibility; quickstart pip note. (#32)

### Changed

- The Claude Code plugin (`/build`) now fronts the Python engine; the TS/bash gate scripts remain as the plugin's phase-gate layer (see Pre-history).
- Plugin and marketplace manifests aligned to `0.1.0` (previously `1.0.0`, a leftover from the pre-engine plugin).

---

## Pre-history (before the Python engine)

The project began as a Claude Code plugin with a bash phase-gate pipeline
(tagged `v1.0.0`, 2026-04-10), then grew a TypeScript gate backend. That work
now lives on as the plugin's gate layer under `scripts/`. Condensed timeline:

### Phase 2 — F7 (2026-04-16)

- Observability emit wiring: 12 wire points across `dispatchPhase`, `mainCLI`, and `checkPhase6` calling the `emit.ts` wrappers; 13 new tests (109/109 passing); dead-import and switch-arm cleanups.

### Phase 2 — Sprint B (2026-04-16)

- Post-review gate (F3): semantic-skip enforcement in `checkPhase6`; 18 forbidden phrases in `semantic-skip-phrases.json`.
- Observability events (F7): `emit.ts` JSONL event streaming to stderr.
- Active Work injection (F9): `memory-reader.ts` extracts `## Active Work` from project MEMORY.md (caps: 10 items / 500 chars; flag `activeWorkInjection`).
- Reviewers config (F10): `reviewers.mode` (toolkit/generic/auto).
- 96 tests total, satisfaction 87%.

### Phase 2 — Sprint A (2026-04-16)

- `omitProjectContext` flag (skip CLAUDE.md injection, saves 10–45K tokens/build); TRIVIAL-tier skip path; state-reader hardening (`StateReadError`, TOCTOU guard); config parsing helpers; cross-platform file-freshness fix (`mtimeMs`); 43 tests passing.

### Phase 1 (2026-04-15)

- TypeScript phase-gate backend: `scripts/ts/build-phase-gate.ts` (~824 lines) with `config-reader.ts` / `state-reader.ts`, 30 TS unit tests, 26 bash-parity tests.
- `gate_backend` config flag (`"bash"` default / `"ts"` / `"shadow"` A/B mode with mismatch logging to `.bytedigger/gate-shadow/`), `GATE_BACKEND` env override, fail-closed `scripts/gate-dispatcher.sh`.
- Security: removed a credential leak from phase artifacts; hardened `ship.sh` against command injection.
- Gate repairs: `findings_skipped` / `post_review_gate` hard-block; mandatory worktree enforcement on main/master; AUTONOMOUS pause-regression fix; 116/116 BATS tests green.

### v1.0.0 (2026-04-10)

- Initial release: phased build pipeline for AI code generation as a Claude Code plugin — bash gate enforcement hook, phase transition validation with TDD.
