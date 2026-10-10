---
red_tests:
  - tests/test_bd256_ci_bats_step.py
paths:
  - .github/workflows/ci.yml
  - CHANGELOG.md
  - CONTRIBUTING.md
---

# bd#256: CI runs the security-scan .bats suites

**Status: DRAFT r2** (r1 gate: REJECTED, 2 MAJOR + 3 MINOR; addressed below) · **Tier:** 1 (one workflow file, no runtime code) · **Class:** PROCESS (a regression guard that exists only as a local habit: nothing turns red when it fails)
**Base:** `origin/main` `0777ae9`.
**Chokepoint:** `.github/workflows/ci.yml`, the only place that defines which checks a pull request and a push to `main` run. No other workflow mentions bats (`grep -rn bats .github` is empty on the base).
**Provenance:** issue bd#256, split out of bd#248 item 2.
**Principles:** (1) deterministic first: the check is a shell exit code from `bats`; no model is involved. (2) Provider-agnostic: the job needs no `claude`, no API key, no network beyond the runner's package mirror. (3) Subscription and API-token modes: the job reads no provider environment, so both behave the same; AC5 pins that the suites pass with the provider variables unset and set to dummies. (4) Shadow first: the step is additive, runs no code of the product, and has no runtime effect; the kill switch is reverting the workflow change.

## 1. Problem (measured on `0777ae9`)

`tests/security-scan.bats` and `tests/security-scan-scope.bats` (8 + 72 = 80 cells) are run only by hand. A change to `scripts/security-scan.sh` that breaks a cell leaves every check green. Reference case: break one cell, push, all four existing jobs (`engine`, `pytest`, `manifests`, `language`) stay green.

## 2. Design

- One new job `bats` in `ci.yml`, on the file's existing triggers (pull request, push to `main`, manual). `runs-on: ubuntu-latest`, `timeout-minutes: 10`, `actions/checkout@v4` only (the file's rule: no other marketplace action).
- Step 1 installs bats from the runner's package manager (`sudo apt-get update -qq && sudo apt-get install -y -qq bats`) and prints `bats --version`.
- Step 2 runs each suite on its own, `bats --tap <suite>` redirected to a file (no pipe, so the result does not depend on `pipefail`; the test runs the step text under plain `bash -e`, which is what a `run:` without `shell:` gets), and keeps the exit code. The step fails when any bats exit code is non-zero (a failing cell prints `not ok <n> <cell name>` in the log), and, per suite, when its output has no `ok` line or has any `# skip` or any `not ok`; so a suite that is empty, truncated to nothing, or skips itself cannot pass behind a populated sibling (the same receipt idea as the bun receipt in the `pytest` job). It prints each file's output to the log. The cell count is not hard-coded: the sibling lot bd#248 changes it.
- Step 3 runs `tests/test_bd256_ci_bats_step.py` (`pip install pytest pyyaml`), which executes the step-2 command text, read out of `ci.yml`, against a scratch copy of `tests/`, `scripts/` and `phases/` (the scope suite reads two phase files) and checks the exit code: unchanged copy passes; one inverted assertion fails and names the cell; both suites empty fails; one suite empty with the other populated fails; a cell that calls `skip` fails. It needs bats, so it skips when bats is not on `PATH` and the job sets `BD_REQUIRE_BATS=1` to make a missing bats an error instead (the same pattern as `BD_REQUIRE_SQLITE`). This is the standing regression test of the step itself; the throwaway-branch runs below are the one-off proof on the real runner.
- Widening: the job runs the two suites only, unless a probe run of every `tests/*.bats` on the runner is green at the base. If it is green, the step is widened to `tests/*.bats`; if any suite is red, the step stays at two suites and the PR lists each red suite by name with the first failing cell. No red suite is fixed here.
- `CHANGELOG.md` `[Unreleased]`: one bullet. `docs/`/`CONTRIBUTING.md` "What CI checks" gets the new job in the list.

Out of scope: `scripts/security-scan.sh` and every `.bats` file (bd#248 owns them), making the new check a required one in the repository settings (a settings change for the maintainer), pinning bats beyond the runner image's package.

## 3. Acceptance criteria (verified by CI runs, links in the PR)

- AC1 (check exists): a run on the PR head shows a check named `bats` that executes both suites and reports their cells in the log; it is green on the final head.
- AC2 (RED, real path, base state): a throwaway branch from the base with one cell in `tests/security-scan.bats` broken (its assertion inverted) and no workflow change: all existing checks are green. Run link in the PR.
- AC3 (GREEN, same breakage): the same throwaway branch plus the workflow change: the `bats` check is red and the log contains `not ok` followed by the broken cell's name; the other cells are listed `ok`. Run link in the PR.
- AC4 (revert): the breakage reverted, workflow change kept: the `bats` check is green.
- AC5 (receipt): the step fails on a run where one suite has zero cells (the other populated), and on a run where a cell calls `skip` (shown on the throwaway branch, run links in the PR); and both suites pass on the runner with `ANTHROPIC_API_KEY` unset and set to a dummy value.
- AC6 (widening decision): a probe run of all `tests/*.bats` on the runner is linked in the PR, with its per-suite result; the final step matches the rule in section 2.
- AC7 (scope): the final diff touches only `.github/workflows/ci.yml`, `CHANGELOG.md`, `CONTRIBUTING.md`, `tests/test_bd256_ci_bats_step.py` and this spec with its gate reports; it does not touch `scripts/security-scan.sh` or any `.bats` file.

## 4. RED

RED file: `tests/test_bd256_ci_bats_step.py`. It reads the real `ci.yml`, finds the step that runs the two suites, and runs that step's own command text with real bats on a scratch copy of `tests/`, `scripts/` and `phases/` (no stub of bats, no re-implementation of the step). On the base it fails because `ci.yml` has no such step. Tests: T1 the step exists and names both suites; T2 unchanged copy exits 0; T3 one inverted assertion exits non-zero and the output has `not ok` with the cell name; T4 both suites without cells exit non-zero; T4b one suite without cells and the other populated exits non-zero; T5 a suite whose only cell calls `skip` exits non-zero.

Plus the one-off proof on the real runner, AC2 + AC3 on throwaway branches (never merged): the same broken cell gives green checks without the change and a red `bats` check with it.
