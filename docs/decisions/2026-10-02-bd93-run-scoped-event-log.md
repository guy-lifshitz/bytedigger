# bd#93 — the event log lives at its run-scoped home from the first event

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
   - **Degrade, never fail.** If (b) or (c) raises (`ValueError` for a bad run
     id or root, `OSError` from mkdir), write one stderr line that starts with
     `event log disabled:` and the reason, keep `args.event_log = None`, and run
     the workflow exactly as today. Same exit code, same stdout JSON.
   - The resolved path (from any of a/b/c) is put into
     `ctx.org_config["events_log_path"]` unless the caller's ctx already sets
     that key. Phase 8's existing override branch then preserves this run's own
     log, not the cwd-relative shared one. Phase 8 code is not changed.
4. **Behaviour that follows, stated on purpose.** With no `--event-log`, a run
   now has a log, so the restart governor and the oracle gate are active for it.
   Their state lives in `<root>/<run_id>/`, scoped to the run. A caller that
   passes `--event-log` sees no change, except that `events_log_path` is now
   injected into ctx when absent.
5. **Test isolation.** `engine_py/tests/conftest.py` `pytest_configure` sets
   `os.environ["HAL_RUN_LOG_ROOT"]` to a fresh temp dir when it is unset, so
   subprocesses inherit it and no test writes under the real `~/.bytedigger`.
   `pytest_unconfigure` restores the previous state.
6. **Docs.** A CHANGELOG `Unreleased` entry (where logs now go, how to override
   them, the governor/oracle note). Add `HAL_RUN_LOG_ROOT` to
   `docs/configuration.md`.

## Out of scope

- `phases/` (bd#89 lot 1570 P3b2 is in flight).
- `phase_8_post_deploy.py` code.
- `--status`/`--task-*` path defaults.
- `is_engine_owned_path` (#94) and per-cycle invalidation (#92).
- `drive-engine.ts` in the HAL host, which already passes `--event-log`.

## Acceptance (`engine_py/tests/test_bd93_run_scoped_event_log.py`)

- **AC1 crash survives.** A workflow run through `run.main()` with no
  `--event-log` and `--run-id R1`, whose step raises after the engine emitted at
  least `workflow_started`, leaves `<root>/R1/events.jsonl` readable afterwards
  (`EventLog.read_all()`). It holds ≥1 event, and every event has `run_id == "R1"`.
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
- **AC8 isolation.** Inside the suite, `HAL_RUN_LOG_ROOT` is set and is not
  under `Path.home()`.

RED on origin/main, GREEN after. No new file read, subprocess or git_read call
under `bytedigger_engine` (mkdir only), so the bd#150 inventory needs no new key.
Re-check after rebasing onto #201.
