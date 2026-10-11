---
red_tests:
  - engine_py/tests/test_bd285_pytest_headroom.py
red_pins: []
sibling_tests: []
paths:
  - .github/workflows/ci.yml
  - scripts/ci_pytest_headroom.py
  - CHANGELOG.md
---
# bd#285: the pytest job ceiling has headroom, and a signal before it is gone

**Status: r2, TIER2** (r1 gate: REJECTED, F1-F4 MAJOR folded in below). Issue #285.
**Class:** SYSTEMATIC: the `pytest` job in `.github/workflows/ci.yml` had `timeout-minutes: 20` while the suite took 19:38 (run 38156738352, 9613 passed in 1178.42s), so every PR and main got CANCELLED with zero failed tests and the merge gate blocked. The suite grew with no review of the ceiling and no signal on approach.
**Chokepoint:** the job's one `timeout-minutes` number in `ci.yml`, and one new step after the suite that compares elapsed time with that same number.
**Side of the seam:** CI config. **Owner:** bd.

| | |
|---|---|
| **LLM calls added** | None. Arithmetic over two numbers. |

## Principles

1. **Deterministic first.** The warning is a pure function of (elapsed seconds, ceiling read from `ci.yml`, one env switch).
2. **No second copy of the ceiling.** The check parses `timeout-minutes` of the `pytest` job out of `ci.yml` itself; neither the script nor the test holds the number.
3. **Never red.** The check always exits 0; the step is `continue-on-error: true`. It only prints a GitHub `::warning::` annotation.
4. **Fail loud, not silent, but not red.** If the ceiling cannot be read, it prints a warning that the check could not run, and exits 0.
5. **Not in scope:** speeding the suite up (shards, xdist), providers, prompts.

## Design

**Ceiling.** `pytest` job `timeout-minutes: 20` becomes `30` (1800s = 1.53x the 1178s run; the issue asks for at least 1.5x). Rollback is editing that one number.

**Check.** `scripts/ci_pytest_headroom.py --duration-sec N [--workflow PATH] [--job NAME]` (defaults: `<repo>/.github/workflows/ci.yml` resolved from the script's own location, NOT the cwd, because the CI step runs under `working-directory: engine_py`; `pytest`). Stock python3, stdlib only.
- ceiling_sec = job-level `timeout-minutes` of the named job x 60, by a line scan: the job block starts at the line `  <job>:` (exactly 2-space indent) and ends at the next line with a 2-space-indent key; the ceiling is the first line in it with exactly 4-space indent `timeout-minutes: <value>`. `<value>` may be quoted and may carry a trailing `# comment`; it must be a positive integer, otherwise the ceiling is unreadable. A step-level `timeout-minutes` (deeper indent) and other jobs' values are never taken.
- duration: `--duration-sec` is parsed by the script itself; a non-integer or negative value is "could not run" (warning, exit 0), never an argparse exit 2.
- Warn iff `N * 10 > ceiling_sec * 7` (integer arithmetic; strictly above 70%). Warning text: `::warning::pytest job used <N>s of its <C>s ceiling (<P>%, warn above 70%): raise timeout-minutes in .github/workflows/ci.yml or speed the suite`. Otherwise one plain `ok:` line.
- Switch: env `BD_CI_PYTEST_HEADROOM_WARN`. Unset, or any value other than `0`/`false`/`off`/`no` (case-insensitive, trimmed): enabled. Disabled: one plain `skipped:` line and exit 0, checked FIRST (before the ceiling is read, so a disabled check is silent even if the ceiling is unreadable).
- Exit code is 0 in every case (missing file, missing job, bad ceiling, bad duration): those print a `::warning::` that the check could not run.

**Wiring in `ci.yml`, `pytest` job.** A step right after checkout records `date +%s` into `GITHUB_ENV` as `PYTEST_JOB_T0`; a last step `pytest headroom (warn above 70% of the job ceiling)`, `continue-on-error: true`, runs `python3 ../scripts/ci_pytest_headroom.py --duration-sec "$(( $(date +%s) - ${PYTEST_JOB_T0:-$(date +%s)} ))"` (an unset T0 gives 0, never a false warning). Step order in the job: T0 step before the suite step, headroom step after it. Elapsed is measured from the first step so it covers what the ceiling covers (setup + suite). On a cancelled job the step never runs; that case is the ceiling itself.

## Citations

- `.github/workflows/ci.yml` job `pytest`: `timeout-minutes` and the final suite step `pytest — engine test suite (hermetic, no dbos, no network)`.
- `scripts/ci_main_heartbeat.py` and `scripts/commit_subject_lint.py`: precedent for a stdlib script invoked from a workflow step.
- `.github/workflows/clean-room.yml` `debt-watch`: kept for expect-fail debt only; a headroom warning is not a defect-still-present assertion, so it is a step of the pytest job instead.

## Acceptance criteria

Real paths only: the test runs the real script in a subprocess against the real `.github/workflows/ci.yml`; the ceiling is read from that file in the test, never written as a number.

| AC | Case | Expectation |
|---|---|---|
| AC1 | duration 1178 s, switch unset | exit 0, output has no `::warning::` |
| AC2 | duration = 70% of the ceiling + 1 s | exit 0, one `::warning::` naming the percentage and the ceiling in seconds |
| AC2b | duration exactly 70% of the ceiling | no `::warning::` (strictly greater) |
| AC3 | switch `0`, `False`, `off`, ` NO ` with duration above 70% | exit 0, no `::warning::` |
| AC3b | switch `1`, empty string | treated as enabled: warns above 70% |
| AC3c | switch `0` and an unreadable workflow | silent (no `::warning::`) |
| AC4 | `--workflow` a file with no `pytest` job | exit 0, a `::warning::` that the ceiling could not be read |
| AC4b | unreadable ceiling: missing file, `timeout-minutes: ${{ vars.X }}`, `0`, empty, non-integer; and non-numeric / negative `--duration-sec` | exit 0, a `::warning::` each time |
| AC5 | a tmp workflow whose `pytest` job has a ceiling different from the real one, a decoy job with another ceiling placed first, a step-level `timeout-minutes`, a quoted value and a trailing comment | the warning/ok threshold follows that file's pytest job job-level value (no copy of the real number) |
| AC6 | cwd = `engine_py`, no `--workflow` | the default resolves the real `ci.yml` (no "could not run" warning) |
| AC7 | the real `ci.yml`, parsed with a YAML loader | ceiling >= 1.5 x 1178 s; in the `pytest` job a step writing `PYTEST_JOB_T0` to `GITHUB_ENV` precedes the step running `pytest tests/`, and a later step has `continue-on-error: true`, runs `../scripts/ci_pytest_headroom.py` with `--duration-sec` and `PYTEST_JOB_T0` |

## Docs

`CHANGELOG.md`, inside the existing `[Unreleased]` section.

## Known limits

- Elapsed is wall-clock of the job's steps, not runner-queue time; a job that is queued long is not penalized, as for the ceiling itself.
- A job that is cancelled prints no warning (no step runs after the cancel); the warning exists to fire before that.

## Kill switch

- The ceiling: one number in `ci.yml`.
- The warning: env `BD_CI_PYTEST_HEADROOM_WARN=0` on the step (default on); deleting the step removes it entirely.
