# Clean-room CI

*Spec and rationale for `.github/workflows/clean-room.yml` (bd#100).*

## Why

`ci.yml` checks the repository out and runs it **in place**, on a preconfigured
self-hosted runner. Anything the runner happens to have — a tool on PATH, an
untracked file, a stale build artefact — is silently part of the test
environment. That is not a hypothetical: bd#97 made the test suite
uncollectable from a clean clone and survived, because no check we ran ever
started from a committed-tree-only copy.

The clean room removes both assumptions:

- **`git archive HEAD`** — committed files only. Untracked local state cannot
  participate.
- **a stock `python:3.11-slim` container** — not the runner. Whatever the image
  does not ship, the project must bring itself.

The runner is used only as a docker host.

## What it catches

Host coupling, undeclared dependencies, README drift, and untracked-file
reliance — on the day they are introduced rather than at a contributor's first
contact. It is a job for a *class* of defect, not a bug.

## The two jobs

| job | image | host tools | asserts |
|---|---|---|---|
| `quickstart` | `python:3.11-slim` | **none** | README Quickstart verbatim, `bytedigger-engine doctor`, keyless demo, and that the test suite **collects** with zero errors |
| `suite` | `python:3.11-slim` | git, zsh, bun, semgrep | the **full test suite** passes |

Both are blocking. Neither sets `continue-on-error`.

## Decision: how this is green on day one

A job that simply demanded a green `pytest` in a stock image would have been red
from its first run, for a reason it does not fix: the suite requires host tools
the image does not ship (bd#102). Three ways out were considered and one was
taken.

**Rejected — collection-only.** Checking only quickstart, demo and collection is
honest and green, but it never proves the suite runs at all, so the tool debt
stays invisible and unbounded.

**Rejected — allowed-to-fail with a deadline.** A yellow job is read by nobody,
and a date in a comment is enforced by nothing.

**Rejected — one job that installs the tools.** It would work, and it would
quietly destroy the thing the job exists for: the guarantee that an outsider's
`pip install` + demo path runs on a bare image.

**Taken — split the two claims into two jobs.** `quickstart` keeps the bare-image
guarantee with zero tools installed. `suite` carries the tools, in one
commented block, and requires the suite green. Nothing is hidden, because the
split is precisely what states the gap: the contents of that block *are* bd#102,
in machine-readable form.

**bd#102 is closed by deleting one block.** The "install the suite's undeclared
host dependencies" step in `scripts/clean-room/in-container.sh`, plus the
`[security]` extra on the install line, is the whole of the debt. When the suite
no longer needs them, that block goes away and CI proves the closure. No date,
no reminder, no yellow check — the exit criterion is a diff.

### bd#102's closure set is larger than its title

Measured on this tree, `python:3.11-slim`, `git archive HEAD`:

| host tools present | suite result |
|---|---|
| none (stock image) | 468 failed, 36 errors, 3360 passed |
| `git`, `zsh` | 3 failed, 3899 passed |
| `git`, `zsh`, `bun` | 1 failed, 3901 passed |
| `git`, `zsh`, `bun`, `semgrep` | **0 failed, 3902 passed, 14 skipped** |

bd#102's title names `git` and `zsh`. Two more are needed:

- **`bun`** — `test_phase_5_step2/3` assert a `ts` test group is executed, which
  only happens when `bun` is on PATH.
- **`semgrep`** — the red-lint preflight refuses to run without it
  (`semgrep (SAST linter) not on PATH — build-critical`). It lives behind the
  **optional** `[security]` extra, but the suite treats it as mandatory.

A git identity (`user.email` / `user.name`) is also required by the tests that
commit; the clean room sets a throwaway one.

## Python versions

The job runs **3.11** only. `pyproject.toml` declares
`requires-python = ">=3.9"` and CONTRIBUTING says "3.9+", and the issue asks for
a matrix against the oldest supported Python — but 3.9 does not work today, so a
3.9 leg would ship a permanently-red required check:

- `tier_gate.py:33` — `parents[4] if len(parents) > 4 else parents[-1]`. Negative
  indexing on `Path.parents` only exists from 3.10. It fires only when there is
  no enclosing `.git`, i.e. exactly the `git archive` case, which is why a clean
  *clone* never hits it. Result: `bytedigger-engine --derive-state` exits 1 and
  74 test modules fail to collect.
- PEP-604 (`X | Y`) annotations evaluated at runtime raise `TypeError` in
  `test_engine.py` / `test_engine_W9.py`.

Adding the leg is the exit criterion of that issue, not of this one. The driver
already takes the version as an argument, so the leg is one line:

```yaml
- run: bash scripts/clean-room/run.sh quickstart 3.9
```

## Not covered, and why

- **`tests/*.bats` and `bun test scripts/ts/`** — the Claude Code plugin layer.
  Needs bats and bun, and is on no outsider's README path.
- **The npm pointer package** (`npm/`) — a separate publish surface with no
  bearing on the Python clean room.
- **`pip install -e ".[agentic-pydantic]"`** — an optional extra that pulls
  `pydantic-ai` from the network and asserts no README promise the job can check.
  Recorded as a deliberate skip in the manifest, not silently dropped.

## Running it yourself

```bash
bash scripts/clean-room/run.sh                  # quickstart, python 3.11
bash scripts/clean-room/run.sh suite            # full suite
bash scripts/clean-room/run.sh quickstart 3.12  # any interpreter
CLEAN_ROOM_REF=origin/main bash scripts/clean-room/run.sh   # any committed ref
```

Identical to what CI runs — CI adds only a docker preflight.

## README parity guard

A clean-room job is worth nothing if it runs commands the README no longer
contains. `scripts/clean-room/readme_parity.py` compares the commands in
README's `## Quickstart` section against
`scripts/clean-room/readme-quickstart.manifest`, and fails the job on any
difference. Every manifest line declares which mode runs it, or carries an
explicit skip reason. A command claimed as covered must actually appear in
`in-container.sh`, so bumping the manifest alone cannot silence the guard.

Change the Quickstart, and the job goes red until the manifest and the
clean-room script are updated with it.

## Operational notes

- **Requirements on the runner:** `docker` on PATH and a reachable daemon. Both
  jobs preflight this and fail with a clear message otherwise.
- **Network:** the container reaches PyPI (both jobs) and `bun.sh` (suite only).
  A `bun.sh` outage fails `suite`; it does not affect `quickstart`.
- **On `pull_request`,** `actions/checkout` provides the merge commit, so
  `git archive HEAD` ships the tree as it would land on `main` — which is the
  tree we want to test.
- **Cost:** roughly 3 minutes for `quickstart` and 6-8 for `suite`, dominated by
  the suite run itself.
