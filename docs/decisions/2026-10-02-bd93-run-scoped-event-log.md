# bd#93 — the event log lives at its run-scoped home from the first event

Revision r2 (gate r1 REJECT: M1–M5 + minors addressed).

## Problem

`run.py` attaches an event log only when the caller passes `--event-log`.
Without it there is no log at all. With the engine default
(`default_log_path()`), the log is `<cwd>/.bytedigger/events.jsonl`: it depends
on the caller's cwd and every run from that cwd shares it. Phase 8
(`_preserve_events_log`) then copies that shared, cwd-relative file into
`<scratchpad>/post-deploy/`. A run that dies before phase 8 is never archived.
A run whose cwd differs from what phase 8 expects archives nothing, or
archives another run's events.

Chokepoint: the place in `run.py` where the run id is first known.

## Change

1. **Root, set by the provider** (`config_provider.py`). Add a neutral method
   `run_log_root(self) -> Path` that returns
   `self.path("HAL_RUN_LOG_ROOT", self.home_root() / self.foreign_state_dirname() / "runs")`.
   The env var is env-overridable, with BD_/BYTEDIGGER_ aliases through
   `path()`. Add a free function `run_log_root() -> Path`. When the provider
   lacks the method (minimal Protocol), it falls back to the same neutral
   default, the same way `foreign_state_dirname()` does. Add `HAL_RUN_LOG_ROOT`
   to `flags_catalog.py` (kind `path`, default None, module
   `config_provider.py`).
2. **Path function** (`event_log.py`). Add
   `run_scoped_log_path(run_id: str, root: Path | None = None) -> Path`, which
   returns `<root>/<run_id>/events.jsonl` (`root` defaults to `run_log_root()`,
   with `expanduser()` applied).
   - `run_id` must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` and must not
     contain `..`. Otherwise it raises `ValueError`.
   - A root that is not absolute raises `ValueError`, so the result never
     depends on cwd.
   - The function does no filesystem I/O.
   - `default_log_path()` and `EventLog` do not change.
3. **Driver resolves the log once, before the engine is built** (`run.py`
   `main()`). This applies only to an invocation that executes a workflow:
   `--workflow` is set and none of `--list`, `--derive-state`, `--list-runs`,
   `--status` or the task seam is given.
   - Resolve the run id first: `_resolved_run_id = args.run_id or uuid hex[:12]`.
     This is the same value that is passed to execution today.
   - Then resolve the log path, first match wins:
     a. explicit `--event-log`, unchanged;
     b. a non-empty `event_log_path_override()` (host-forced, GH1309);
     c. `run_scoped_log_path(_resolved_run_id)`.
   - For (c), create the parent directory (`mkdir(parents=True, exist_ok=True)`).
   - The resolved value is assigned to `args.event_log`. Every existing consumer
     in `main()` then sees it: `make_engine`, the governor, the oracle and
     refusal rows.
   - **Degrade, never fail.** If (b) or (c) raises anything (see §5), write one stderr line that starts with
     `event log disabled:` and the reason, keep `args.event_log = None`, and run
     the workflow exactly as today. Same exit code, same stdout JSON.
   - The resolved path (from any of a/b/c) is put into
     `ctx.org_config["events_log_path"]` unless the caller's ctx already sets
     a truthy value for that key (absolute path, §6). Phase 8's existing override branch then preserves this run's own
     log, not the cwd-relative shared one. Phase 8 code is not changed.
4. **Every consumer gated on `args.event_log`, declared (r2, gate M1/M2).** With
   no `--event-log`, the run now has a log, so these activate. All of them keep
   their state in `<root>/<run_id>/`, scoped to the run:
   - **restart governor** (`governor_gate`, `governor_record_result`/`_pause`).
     A repeated invocation with the same `--run-id` counts against the run's
     restart budget. That is intended: it is the same run.
   - **`--restart-reason`**. Without `--event-log` this used to be a
     "ignored" warning. It now resets the run-scoped governor state. Intended.
   - **phase sentinel** (`execute_native_workflow` → `load_cached_success`/
     `store_success`). Re-invoking the same workflow with the same `--run-id`
     and the same ctx is served from cache (resume), exactly as it is today for
     `--event-log` callers. A different or absent `--run-id` executes. The
     default run id is random, so a caller that does not pass `--run-id` never
     hits the cache.
   - **oracle entry verify** (`phase_5_implement`). It used to be refused
     `E_ORACLE_UNFROZEN` with no log. It now looks up the freeze in the
     run-scoped log, so `phase_45_spec` → `phase_5_implement` with the same
     `--run-id` works with no flags.
   - **oracle freeze** (`_oracle_after_execute`). The frozen `[bd8:6b]`
     contract is kept: when the log is **implicit** (source b or c, not an
     explicit `--event-log`) and ctx has no `scratchpad_dir`, the freeze
     returns None (no refusal), exactly as a logless run does today. The
     driver records `_log_implicit: bool` alongside `args.event_log`. Only this
     branch reads it.
   - **`phase_refused` rows, `emit_stuck_report`, cost rollup.** These now
     write into the run-scoped log. Intended.
   - A caller that passes `--event-log` sees no change, except that
     `events_log_path` is injected into ctx when absent (see §3). Phase 8 then
     archives exactly that file. If that file is shared across runs (the
     caller's choice), the archive is the same shared file it archives today
     via the worktree default.
   - `--event-log ""` is falsy and is treated as absent, so it resolves via b/c.
5. **Degrade path covers every failure (r2, gate M3).** The whole resolution
   block (b, c, mkdir) is wrapped in `except Exception`. The host override is
   read with `getattr(get_config(), "event_log_path_override", None)` and
   treated as "" when missing. A non-absolute override (after `expanduser`)
   raises `ValueError` inside the block and degrades. A minimal-Protocol
   provider, an unresolvable HOME (`RuntimeError`) or an unwritable root
   never turn a working run into a crash.
6. **Path details (r2, minors).**
   - The injected `events_log_path` is the resolved path made absolute with
     `Path.absolute()`, not `resolve()`, so symlinks such as `/private/var` vs
     `/var` stay as given.
   - Only a truthy caller value counts as caller-set.
   - In `run_scoped_log_path`, `expanduser()` comes before the absolute check.
   - The minimal-Protocol fallback of `run_log_root()` is
     `get_config().path("HAL_RUN_LOG_ROOT", Path.home().resolve() / ".bytedigger" / "runs")`.
     `path()` is on the minimal Protocol, so the env var is honoured there too.
   - Known and declared: on a case-insensitive filesystem (APFS default),
     run ids `R1` and `r1` share a directory. Run directories are not cleaned
     up; that is a follow-up issue.
7. **Test isolation (r2, gate M5).** `engine_py/tests/conftest.py`:
   - `pytest_configure` sets `os.environ["HAL_RUN_LOG_ROOT"]` unconditionally
     to a fresh temp dir and saves the previous value. `pytest_unconfigure`
     restores it. This covers subprocesses and session-scoped code.
   - An autouse function fixture does
     `monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(tmp_path_factory.mktemp("bd93-runlog")))`,
     so each test gets its own root and no governor or sentinel state leaks
     between tests that reuse literal run ids.
8. **Docs.**
   - A CHANGELOG `Unreleased` entry covering: where logs now go, how to
     override them, and the consumer list from §4.
   - `HAL_RUN_LOG_ROOT` in `docs/configuration.md`.
   - A follow-up issue for the `semantic_verifier.py` `EventLog()` cwd
     writers (L375, L566) and for cleanup of run directories.

## Out of scope

- `phases/` (bd#89 lot 1570 P3b2 is in flight).
- `phase_8_post_deploy.py` code.
- `--status`/`--task-*` path defaults.
- `is_engine_owned_path` (#94) and per-cycle invalidation (#92).
- `drive-engine.ts` in the HAL host, which already passes `--event-log`.
- `semantic_verifier.py` direct `EventLog()` writers (follow-up issue).

## Acceptance (`engine_py/tests/test_bd93_run_scoped_event_log.py`)

- **AC1 crash survives.** A workflow run through `run.main()` with no
  `--event-log` and `--run-id R1`, whose step raises after the engine emitted at
  least `workflow_started`, leaves `<root>/R1/events.jsonl` readable afterwards
  (`EventLog.read_all()`). It holds ≥1 event, and every event has `run_id == "R1"`.
- **AC1b hard kill (r2, M4).** A subprocess registers a workflow whose step
  calls `os._exit(17)`, then runs `run.main()` with no `--event-log` and
  `--run-id R1`. The process exits with 17. Afterwards `<root>/R1/events.jsonl`
  is readable, contains a `workflow_started` event, and every event has
  `run_id == "R1"`.
- **AC2 no cross-contamination.** Two runs, R1 and R2, from the same cwd (no
  `--event-log`, real `echo` workflow, subprocess with `--neutral`) produce two
  different files, `<root>/R1/events.jsonl` and `<root>/R2/events.jsonl`. Each
  contains only its own run id. Neither run creates `<cwd>/.bytedigger/events.jsonl`.
- **AC3 cwd-independent.** The same run id from two different cwds resolves to
  the same path under the root.
- **AC4 precedence.** Explicit `--event-log P` writes to P and creates no
  `<root>/<run_id>`. A host override (provider returning a path) wins over the
  run-scoped default.
- **AC5 degrade.** A bad run id (`../x`, `a/b`, `.h`, a 129-char id) or an
  unusable root (root is a regular file) causes: the workflow still runs, the
  exit code and stdout match a no-log run, stderr has `event log disabled:`,
  and nothing is written outside the root.
- **AC6 ctx injection.** The ctx the workflow receives has
  `org_config["events_log_path"]` equal to the resolved path. A caller-provided
  value is kept.
- **AC7 unit.** `run_scoped_log_path` accepts and rejects the ids above, and
  rejects a relative root. `run_log_root()` honours `HAL_RUN_LOG_ROOT` and
  `BYTEDIGGER_RUN_LOG_ROOT`. The minimal-Protocol fallback works.
- **AC8 isolation.** Inside a test, `HAL_RUN_LOG_ROOT` is set, is under
  pytest's basetemp (not under `Path.home()`), and differs between two tests.
- **AC9 consumers (r2, M1/M2).**
  - (a) Sentinel: two in-process invocations of the same workflow, same
    `--run-id R1`, same ctx and no `--event-log`. The second is served from
    cache: the workflow body runs once and `phase_sentinel_resumed` is in the
    log. Different run ids both execute.
  - (b) `[bd8:6b]` kept: `phase_45_spec` (or an oracle workflow stubbed to
    succeed) with no `--event-log` and no `scratchpad_dir` is not refused. The
    result is not `E_ORACLE_INDETERMINATE`, matching today's logless behaviour.
  - (c) The same oracle case with an explicit `--event-log` and no scratchpad
    is still refused `E_ORACLE_INDETERMINATE`, unchanged.
- **AC5c degrade, minimal provider (r2, M3).** A provider factory returning
  a minimal-Protocol object (only `hal_root`/`path`, no
  `event_log_path_override`), plus `echo` with no `--event-log`, still runs to
  `status ok` with no crash. A relative host override degrades with
  `event log disabled:`.
- **AC10 catalog.** `flags_catalog` has `HAL_RUN_LOG_ROOT` with kind `path`.

RED on origin/main, GREEN after. No new file read, subprocess or git_read call
under `bytedigger_engine` (mkdir only), so the bd#150 inventory needs no new key.
Re-check after rebasing onto #201.
