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

## Two declarations, and why only one set of tests must make one (bd#49)

The hookwrapper above is **total** over the ordinary path: a test that simply
spawns a host tool and dies with `FileNotFoundError` becomes an honest `skip`
no matter how it spawned it. Measured on `10ca0fc`: **610** test bodies reach a
host tool by argv literal (`subprocess.run(["git", …])`) without ever calling
`shutil.which`, and every one of them is covered by that conversion with zero
lines of declaration.

There is exactly one way out from under it — **probe availability yourself** and
decide before the `FileNotFoundError` can fire. A body that does so is invisible
to the mechanism. Measured: **37** such bodies, which is the *complete* set of
escapes, not a sample.

So the rule applies to those, and only those:

> A test body that probes host-tool availability itself must declare what it
> does with the answer — either `helpers.host_tools.skip_without(tool)`, or an
> explicit `# host-tool-hard-fail: <reason>` comment in its own body.

Enforced by `tests/test_bd49_host_tool_declaration_gate.py` (scanner:
`tests/helpers/host_tool_probes.py`), which **derives** the set by AST rather
than enumerating files — a new probing call site cannot be exempted by being
absent from a list, only by declaring a form.

Both declarations are legitimate. `test_gh1338_corpus_parity_gate.py` hard-fails
on purpose across eight ACs: a silent skip there would certify corpus parity
without ever having run the corpus. What bd#49 changed is that the choice is now
*stated* rather than incidental.

Three hand-rolled variants were converted to `skip_without` in the process
(`test_GH294_…`, `test_BF7890C8_…`, `test_phase_45_spec_D02C615D.py`,
`test_phase_45_spec_DA5330E9_suffix_map.py`, and `test_bd102_host_tool_contract.py`
itself). All of them called `shutil.which` **live**, which the mechanism above
forbids for a documented reason — two tests monkeypatch `shutil.which`
process-wide, so a live lookup can report a tool absent on a machine that has
it. The `@pytest.mark.skipif(not _has_git(), …)` form was worse still: a
decorator is evaluated at import time, before `pytest_configure` freezes the
map, so it could not consult the frozen map even in principle.

**Declared limit of the gate.** It recognises pre-emption performed via
`shutil.which` (under any alias, and via `from shutil import which`). It does
*not* recognise `os.path.exists("/usr/bin/git")` or a hand-written
`except FileNotFoundError: pytest.skip(...)`. Measured at zero occurrences of
both; the first one to appear must move this boundary, and the gate's own
docstring is where that is recorded.

## Canonical source (§1g)

Once bd#104 lands, `scripts/clean-room/bd102-tools.manifest` becomes a
*consumer* of this same inventory rather than a third independent
declaration — its own stated exit criterion is to be emptied when this
document and `helpers.host_tools.HOST_TOOLS` exist. Until then there are
two declarations (this doc + `HOST_TOOLS`); `HOST_TOOLS` is canonical.
