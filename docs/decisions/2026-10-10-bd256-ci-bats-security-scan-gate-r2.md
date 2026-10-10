# bd#256 gate r2: ci bats security-scan (spec + RED audit)
<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd256-ci-bats-security-scan.md sha256:2c951d3cd5a6fde2580a04dfabf0814f102e85e2dea3e354448d10faccfe0589
-->

Audited: spec draft r2, `tests/test_bd256_ci_bats_step.py`, the two suites `tests/security-scan.bats` and `tests/security-scan-scope.bats`, `scripts/security-scan.sh`, and the round-1 report. Base `0777ae9`. RED on base: 0 pass / 6 fail (all fail at assert time, `pytest.fail` on a missing step, before the bats availability check).

## Round-1 findings: status

1. MAJOR, `phases/` missing from the scratch copy: FIXED. `_scratch()` now copies `tests/`, `scripts/` and `phases/`. Every path the suites read was checked:
   - `security-scan.bats` reads only `scripts/security-scan.sh`, resolved from `$BATS_TEST_FILENAME/..`; its `TMPDIR` is a fresh `mktemp -d`.
   - `security-scan-scope.bats` reads `scripts/security-scan.sh`, `phases/phase-05-inject.md` and `phases/phase-45-spec.md` (both exist on base), and works in its own `mktemp -d`.
   - The script itself sources nothing and reads no other repo file. The only `git ls-files` text is grep content inside the phase docs, not a command run against the scratch tree.
   - The `.py` names in the scope suite (`src/p.py`, `src/token_store.py`) are created or passed as strings inside the work dir; no suite reads a `tests/*.py` file. Ignoring `*.py` and `__pycache__` in the `tests/` copy therefore loses no fixture, and there are no non-`.py` fixtures under `tests/` that the suites use.
   So T2 (unchanged copy exits 0) is satisfiable and T3 to T5 no longer pass for the wrong reason.
2. MAJOR, pipefail masking: FIXED. The harness runs the step text with `bash -e -c` (no pipefail), matching a `run:` without `shell:`. The spec drops the pipe: bats output goes to a file, the exit code is kept explicitly, and per-suite receipt greps run on the file. A tee-style implementation would be caught by T3.
3. MINOR, one empty suite: FIXED. Per-suite receipt in the spec; T4b empties each suite in turn (separate scratch trees) with the sibling intact. An empty file yields `1..0`, no `ok` line, so the receipt fails it.
4. MINOR, `apt-get update`: FIXED in spec section 2 (`sudo apt-get update -qq && sudo apt-get install -y -qq bats`).
5. MINOR, file list mismatch: FIXED. Frontmatter `paths:` now lists `ci.yml`, `CHANGELOG.md`, `CONTRIBUTING.md`, matching AC7 (the RED file is in `red_tests`).

## New checks

- Step lookup: `_step_run` picks the first step whose `run` text contains `bats` and both suite paths. The install step names no suite and the pytest step names only the new test file, so neither can be mistaken for the run step. The step text must use paths relative to the repo root (the harness sets `cwd` to the scratch root) and must not rely on `working-directory` or `$GITHUB_WORKSPACE`.
- T4b both orders: covered by the loop over `SUITES`, each with a fresh scratch tree, asserting non-zero.
- T5: both suites replaced by a single skipping cell; `--tap` prints `ok 1 only_cell # skip nope`, so a receipt that rejects `# skip` fails it. A receipt that only looks for a bare `ok` substring would pass this, and T5 would catch that.
- T3: the first `[ "$status" -eq 0 ]` sits in `T01_classifies_auth_as_high`; the test asserts non-zero, `not ok` and the cell name in the combined output.

## Findings

1. MINOR: under plain `bash -e`, a bare `bats ... > file` that fails aborts the step before the file is printed, so the `not ok <cell>` line would never reach the log. The spec says the exit code is kept and the files are printed; GREEN must capture with `|| rc=$?` (or equivalent) and print before exiting. T3 asserts the cell name in the output, so it enforces this.
2. MINOR: the receipt for "has an `ok` line" must anchor on a line start (`^ok `); `not ok` and `# skip` lines contain `ok`. T4 and T5 catch a loose pattern for the empty and skip cases.
3. MINOR: after GREEN, on a machine without bats T2 to T5 skip rather than run; the job sets `BD_REQUIRE_BATS=1`, so CI cannot silently skip. Accepted.

## Adversarial edges not covered by an AC

- A suite file deleted or renamed: bats exits non-zero on a missing file, so it fails loudly; no extra test needed.
- Locale: the scope suite forces `LANG=en_US.UTF-8`, which may be absent on the runner; AC1 (green on the real runner) is the proof, and any failure must be listed in the PR rather than skipped.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS

No MAJOR findings.

VERDICT: APPROVE
