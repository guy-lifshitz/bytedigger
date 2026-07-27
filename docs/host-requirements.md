# Host tool requirements (bd#102)

The engine and its test suite assume a small set of external binaries on
`PATH`. This document is the prose counterpart to the canonical,
machine-readable source: **`engine_py/tests/helpers/host_tools.py::HOST_TOOLS`**.
If the two ever disagree, `HOST_TOOLS` wins.

| tool | verdict | why |
|---|---|---|
| `git` | **required by the product** | the engine shells out to git for fixture repos, disk-truth diffs, and its own `git commit` during RED/GREEN cycles (`lib/bounded_spawn.py`, `phase_5`/`phase_6`/`phase_7`/`phase_8`). Without it, ~500 tests raise `FileNotFoundError('git')` and the `doctor` self-check correctly reports `git-runtime: fail`. |
| git **identity** | **required by the product** | git commits made by the product (`commit_red_tests`) run in a separate process that inherits none of a test's per-invocation `-c user.name=...` flags. A configured identity — `git config --local user.email` / `user.name` / `commit.gpgsign false` written by `helpers/git_repo.py::init_repo` — must exist in the repo itself, not only in the caller's ambient global config. `git init -q -b main` (used by `init_repo`) requires **git >= 2.28** for the `-b` flag; on an older git every `init_repo` call errors. |
| `zsh` | **not required** | measured at zero suite failures with or without zsh installed. `phase_6_smoke.py` already degrades gracefully (`skipped="zsh_unavailable"`) when zsh is absent. `zsh` is deliberately excluded from `HOST_TOOLS`: declaring it would let the skip-conversion mechanism silently swallow any future regression of that graceful-degradation path. |
| `bun` | **test-only requirement** | 2 tests (`test_phase_5_step2_verify_red.py`, `test_phase_5_step3_verify_green.py`) assert that a `ts`/bun test group actually runs. |
| `semgrep` | **test-only requirement** | the red-lint preflight refuses to run without it (fail-closed, "build-critical" — see `pyproject.toml`); 1 test exercises that path. `semgrep` ships only in the optional `[security]` extra, which is the mismatch this doc records. |

## Tree shape (bd#2)

Host tools are one axis of what the suite needs. The other is the **shape of
the tree it runs in**, and it has its own canonical source:
**`engine_py/tests/helpers/live_repo.py`**.

| requirement | verdict | why |
|---|---|---|
| a **git checkout** of this repository | **test-only requirement** | three tests resolve the project by climbing from the test file up to an entry named `.git`, then compare a cheap file-based read against a real `git rev-parse` / `git rev-list` on that repository. `git archive`, an installed wheel and a downloaded release tarball all ship tracked files and no `.git`, so the subject simply is not there. The tests are `test_gh1220_ambient_cwd_commit_refusal.py::test_ac16_…`, `::test_ac17_…` and `::test_ac31_…`; without a checkout they report `skipped`, not `failed`. |

The property those three pin — that the sentinel's cheap HEAD read agrees with
git — is genuinely unobservable without a repository, which is what makes a
skip honest here. It is not a general licence: a check that *could* be shipped
and run should be shipped and run, not skipped.

This is deliberately **not** modelled as a `HOST_TOOLS` entry named `.git`.
`host_tool_skip_reason` renders "requires host tool '<name>'", and reporting a
missing binary for a missing checkout would be exactly the misleading message
the declaration exists to remove.

## Mechanism

Availability is probed once via `shutil.which`, at `pytest_configure` time,
into `helpers.host_tools._HOST_TOOL_AVAILABLE`. A `pytest_runtest_makereport`
hookwrapper converts a test's `FileNotFoundError` for a declared, genuinely
absent tool into an honest `skip` (rather than a `FAIL`), and
`helpers.host_tools.skip_without(tool)` is called explicitly by the handful
of tests that fail by assertion instead of by spawn error.

The checkout requirement is frozen the same way and in the same place —
`helpers.live_repo.freeze_git_checkout_availability()`, called from that same
`pytest_configure` — and read by `helpers.live_repo.skip_without_git_checkout()`,
which the three tests above call as their first statement. There is no
automatic conversion for this axis: absence of a checkout surfaces as a plain
`assert`, not as an exception a report hook could recognise.

## Canonical source (§1g)

Once bd#104 lands, `scripts/clean-room/bd102-tools.manifest` becomes a
*consumer* of this same inventory rather than a third independent
declaration — its own stated exit criterion is to be emptied when this
document and `helpers.host_tools.HOST_TOOLS` exist. Until then there are
two declarations (this doc + `HOST_TOOLS`); `HOST_TOOLS` is canonical.
