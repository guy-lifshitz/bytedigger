# bd#256 gate r1: ci bats security-scan (spec + RED audit)
<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd256-ci-bats-security-scan.md sha256:2c951d3cd5a6fde2580a04dfabf0814f102e85e2dea3e354448d10faccfe0589
-->

Audited: spec draft r1, `tests/test_bd256_ci_bats_step.py`, `.github/workflows/ci.yml`, `tests/security-scan.bats` (8 cells), `tests/security-scan-scope.bats` (72 cells), `scripts/security-scan.sh`. Base `0777ae9`. RED reported 0 pass / 5 fail on base.

## Section 1 claims

- 8 + 72 = 80 cells: confirmed by counting `@test` lines.
- No other workflow mentions bats: consistent with ci.yml (single workflow, four jobs `engine`, `pytest`, `language`, `manifests`).
- Twelve `.bats` files exist under `tests/`; the widening rule covers them. Not verifiable here whether the other ten pass on a runner; AC6 handles it.

## Findings

### 1. MAJOR: T2 can never pass; the scratch copy omits `phases/`

`tests/security-scan-scope.bats` reads `$REPO_ROOT/phases/phase-05-inject.md` and `phase-45-spec.md` (cells AC9 x3 and AC16 x2 do `[ -f "$PHASE05" ]` and grep the docs). `_scratch()` copies only `tests/` and `scripts/`. In the scratch tree those five cells fail, so the step exits non-zero on an unmodified copy. T2 (unchanged copy exits 0) is unsatisfiable for any correct GREEN. Because T3 to T5 only assert non-zero, they would pass for the wrong reason (the five phase cells already fail), so T3/T4/T5 also lose discriminating power for the exit code. Fix: copy `phases/` into the scratch tree (and any other file the suites read; verify by running the real step against the scratch copy), and keep a T2 that asserts exit 0.

### 2. MAJOR: the test runs the step under `bash -eo pipefail`, but GitHub's default `run` shell is `bash -e {0}`

The design says the step tees the output. On a runner, a `run:` with no `shell:` key runs as `bash -e {0}` with no pipefail, so `bats ... | tee out.txt` returns tee's status and a failing cell would NOT turn the check red unless the receipt grep happens to catch it (a `not ok` line is not in the receipt as specified: only "no `ok` line" or "`# skip`"). The RED executes the text with `-o pipefail`, which hides exactly this bug, so T3 would pass in the harness while the real check stays green. Fix (both): the spec requires either `shell: bash` or an explicit `set -o pipefail`/`PIPESTATUS` check inside the step text, and the test invokes the text with `bash -e` (the no-`shell:` default), or asserts the step declares its own pipefail. Prefer also adding `grep -q '^not ok'` to the receipt as defense in depth.

### 3. MINOR: an empty single suite is not caught by the receipt

With one suite emptied and the other populated, `bats` exits 0, there are `ok` lines and no skip, so the step passes while 8 or 72 cells vanished. T4 empties both and therefore does not cover this. It matters (a truncated/renamed suite is the most plausible regression) and is cheap to close: run the suites in separate invocations, or require at least one `ok` per named suite file (for example loop over the two files, each with its own receipt). Add a T4b that empties one suite. Not a hard-coded count (bd#248 changes counts), only "each named suite has at least one cell".

### 4. MINOR: `apt-get install bats` without `apt-get update`

On a fresh runner image the package index can be stale; the existing `manifests` job uses `apt-get update -qq` first. Specify `sudo apt-get update -qq && sudo apt-get install -y -qq bats`. Ubuntu's bats package is bats-core 1.x, and `--tap` plus `ok N name # skip reason` output is supported, so the "no `ok` / `# skip`" receipt is technically feasible (`skip` cells print `ok <n> <name> # skip <reason>`). Suite load failures and cells erroring in `setup` make bats print `not ok` and exit non-zero, so those are covered by the exit code.

### 5. MINOR: locale/TMPDIR notes (risk, not defect)

Both suites replace or ignore the runner `TMPDIR` (the first sets `TMPDIR=$(mktemp -d)`, the second uses `WORK=$(mktemp -d)`), so no runner-specific TMPDIR handling is needed. The AC20 cell forces `LANG=en_US.UTF-8`, a locale that may not exist on the runner; the script uses `LC_ALL=C` and `grep -a`, so likely fine, but this is exactly what AC1/AC4 (green on the real runner) must prove. If it fails, the PR must list it rather than skip it.

### 6. MINOR: scope drift in the spec text

The issue asks for one step that installs bats and runs the two suites. The spec adds a pytest step (`pip install pytest pyyaml`) and a new test file inside the job, plus `CONTRIBUTING.md` edits. AC7 lists `CONTRIBUTING.md`, while the frontmatter `paths:` lists only `ci.yml` and `CHANGELOG.md`; section 2 says "`docs/`/`CONTRIBUTING.md`" ambiguously. Align the frontmatter with AC7 and name one file. The extra test is defensible as a standing guard but should be called out as an addition beyond the issue.

### 7. Checks that passed

- Root `tests/*.py` are not collected by the `pytest` job (it runs inside `engine_py/` on `tests/` there); root tests run only by explicit file in `manifests`. The new test file is therefore not picked up elsewhere, and the new job must name it explicitly (spec does).
- Fork PRs: the job needs no secrets, so it runs the same on forks. Required-check name: job id `bats` with no `name:` yields a check called `bats`; not required by repository settings (out of scope, stated).
- T1 fails at assert time (`pytest.fail`), T2 to T5 look up the step before requiring bats, so on a base without bats they fail rather than skip; no doubles anywhere; RED uses real bats and the step's own text. T3: the first `[ "$status" -eq 0 ]` is in cell `T01_classifies_auth_as_high`, which the assertion then finds in the `not ok` line.
- T5 skip: both suites are replaced, `# skip` appears in `--tap`; adequate.
- ACs are checkable by CI run links; AC1 to AC6 are manual-evidence ACs (run links in the PR) but each is observable, no AC depends on unobservable behavior.
- Adversarial edges not covered by an AC: (a) one suite empty or deleted while the other has cells (finding 3); (b) default-shell pipefail (finding 2); (c) scratch tree missing files the suites read (finding 1); (d) a `.bats` file renamed so the named path no longer exists: `bats` exits non-zero on a missing file, so this fails loudly, fine.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: FAIL
- AC4: PASS
- AC5: FAIL
- AC6: PASS
- AC7: FAIL

(AC3 fails on finding 2: as specified a failing cell may not turn the check red. AC5 fails on finding 3: the receipt does not catch an empty single suite. AC7 fails on finding 6: frontmatter and AC list disagree on touched files.)

VERDICT: REJECT
