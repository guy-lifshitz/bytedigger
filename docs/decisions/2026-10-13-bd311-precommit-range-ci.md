---
red_tests:
  - engine_py/tests/test_bd311_precommit_range_ci.py
sibling_tests:
  - engine_py/tests/test_bd66_precommit_enforcement.py
  - engine_py/tests/test_bd308_empty_input_assert_driver.py
  - engine_py/tests/test_bd305_spec_lint_feed.py
paths:
  - engine_py/bytedigger_engine/precommit_enforce.py
  - scripts/precommit_enforce_cli.py
  - .github/workflows/ci.yml
  - CHANGELOG.md
---
# bd#311: server-side mirror of the pre-commit lint layer (range mode + one CI job)

Slice of #66 (after #303, #306, #307, #309, #310). Refs #66, #311. Base: origin/main @ 155fc69. Tier 2.
Structural only: no new lint logic, no second registry. The hook is opt-in per clone and
`git commit --no-verify` bypasses it, so a commit can reach `main` unchecked; CI is the only
enforcement that is live on merge (same precedent as the `language` job, bd#79).

## 1. Measurement (before the spec)

Re-checked on 155fc69: `precommit_enforce.main` reads its file set only from
`git diff --cached` (`staged_paths`); `ci.yml` has no step that calls the registry; no
`BD_PRECOMMIT_CI` anywhere. The task is not done.

Idea run by hand over the last 10 first-parent commits of `origin/main` (each a squash-merged PR):
file set = `git diff --name-only --diff-filter=ACMR C^ C`, the real registry pre-pass, the real
`classify_staged` / `build_lint_commands` / `run_plan`, ratchet baseline = the file at `C^`,
working tree = `C`.

| commit | PR | files | planned lint runs | pre-pass violations | refusals |
|---|---|---|---|---|---|
| 155fc69 | #310 | 1 | 1 | 0 | 0 |
| 762e43b | #309 | 16 | 40 | 0 | 0 |
| fa7e297 | #307 | 10 | 20 | 0 | 0 |
| c5e0b99 | #306 | 18 | 28 | 0 | 0 |
| 6e15ff5 | #303 | 10 | 16 | 0 | 0 |
| 1023487 | #301 | 10 | 18 | 0 | 0 |
| 7e2e531 | #300 | 15 | 29 | 0 | 0 |
| 1c5531c | #298 | 14 | 22 | 0 | 0 |
| 05fe8ca | #295 | 6 | 10 | 0 | 0 |
| cd5d04d | #293 | 10 | 14 | 0 | 0 |

**Would-be refusals: 0 of 10.** The job does not go red on this history. Caveat: the
history was produced under the hook, so 0 is the expected value; the measurement shows the job
does not misfire on honest work (ratchet against BASE, DECLARED_ABSENT skipped), not that the
hook was ever bypassed.

## 2. Change

1. **Range mode** in `precommit_enforce.main(argv=None)`; `scripts/precommit_enforce_cli.py`
   passes `sys.argv[1:]` through. `--range BASE..HEAD` (also `BASE...HEAD`; both mean merge-base):
   - `BASE` and `HEAD` are resolved with `git rev-parse --verify --quiet <ref>^{commit}`. The right side
     must resolve to the same commit as the checked-out `HEAD` (drivers read the working tree; a
     range whose tip is not the tree on disk would lint the wrong bytes).
   - File set = `git diff --name-only --diff-filter=ACMR BASE...HEAD`, replacing `staged_paths`.
     Everything after it (classify, `nothing_to_lint`, `build_lint_commands`, `run_plan`) is the
     existing code path, unchanged; the registry pre-pass runs first, as in the hook.
   - The bd#243 GREEN-entry guard is a commit-time check on staged paths and is NOT run in range mode.
   - No argument: behaviour is byte-for-byte the staged mode of today.
2. **Ratchet baseline.** `_head_findings(command, root)` gains `ref="HEAD"`; in range mode `run_plan`
   is called with `ref` = `git merge-base BASE HEAD`. The ratchet rule is unchanged (refuse only
   when `now > base`; absent at ref -> base 0; baseline run that errors -> driver error).
3. **Kill switch.** `BD_PRECOMMIT_CI=off` (exactly `off`) in range mode prints
   `BD66-CI-OFF` and returns 0, before anything else (no git, no pre-pass). Any other value, unset or
   empty, leaves the job on. It does not affect staged mode. Per-lint switches
   (`BD_SPEC_LINTS_OFF`, `LINT_OFF_ENVS`), NOT-FED and `DECLARED_ABSENT` behave exactly as in the hook.
4. **Fail closed.** A malformed `--range` (missing value, no `..`, empty side), an unknown
   argument, an unresolvable ref, a tip that is not `HEAD`, no merge-base, or a failing `git diff`:
   print `BD66-CI-RANGE-ERROR: <reason>` and return 2. Never 0. An empty range (BASE == HEAD) -> 0.
5. **CI job** `precommit-lints` in `.github/workflows/ci.yml`: `if: github.event_name == 'pull_request'`,
   `runs-on: ubuntu-latest`, `timeout-minutes: 10`, `actions/checkout@v4` with `fetch-depth: 0`
   (a depth-1 checkout has no base to diff; an empty corpus would pass vacuously), no install step
   (stock `python3`; the shim sets its own path). Env: `BASE_SHA: ${{ github.event.pull_request.base.sha }}`,
   `BD_PRECOMMIT_CI: ${{ vars.BD_PRECOMMIT_CI }}`. Run:
   `python3 scripts/precommit_enforce_cli.py --range "$BASE_SHA..HEAD"`. No continue-on-error,
   no an or-true suffix. The same `BD66-REFUSE-*` lines as the hook reach the job log.
6. `CHANGELOG.md` entry. Making the job a required check is a repo setting, out of scope.

## 3. Acceptance criteria (each refusal is paired with an accepted twin)

Real `scripts/precommit_enforce_cli.py` in a temporary git repo; legacy and offending commits made
with `--no-verify`; exit code and stdout are the observations.

- AC1 range with an added test asserting `findings == []` with no live denominator: exit 1,
  stdout has `BD66-REFUSE-VIOLATION` with `lint=empty-input-assert-lint` and the file, plus the driver's
  `file:line` finding line. Twin: the same range with a clean test: exit 0.
- AC2 legacy test with findings at BASE, edited without adding one: exit 0.
- AC3 the baseline is BASE: legacy file with N findings at BASE, branch adds one: exit 1 with `findings=N+1 head=N`
  (the `head=` key is the existing token). Against a HEAD baseline this would have passed.
- AC4 `BD_PRECOMMIT_CI=off` on the AC1 range: `BD66-CI-OFF`, exit 0. `OFF`, `0`, empty: still exit 1.
  Staged mode with the variable set to `off` still refuses.
- AC5 fail closed (exit 2, `BD66-CI-RANGE-ERROR`, never 0): unresolvable BASE, `--range` without value,
  value without `..`, tip not at `HEAD`, unknown flag.
- AC6 empty range: exit 0, nothing run.
- AC7 pre-pass runs in range mode: a registry name with no driver and no declaration (via `BD66_LINT_DIR`)
  -> exit 1 `BD66-REFUSE-MISSING-DRIVER`.
- AC8 per-lint switch honoured: `BD_EMPTY_INPUT_ASSERT_LINT=off` on the AC1 range -> `BD66-LINT-OFF`, exit 0.
- AC9 docs-only range (no lintable file): exit 0.
- AC10 `ci.yml` parsed as YAML: job `precommit-lints` exists, is guarded to `pull_request`, checks out with
  `fetch-depth: 0`, its run step invokes `scripts/precommit_enforce_cli.py --range` on `base.sha`, passes
  `BD_PRECOMMIT_CI`, has no continue-on-error and no an or-true suffix.
- AC11 no-argument invocation is still the staged mode (a staged violation refuses, an unstaged one does not).

## 4. Out of scope

spec-cite-lint and token-consistency-lint (#305), baseline-delta driver (#299), fixing legacy
findings, branch-protection settings, any change to lint logic or the registry.
