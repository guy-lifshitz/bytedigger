# Clean-room CI

*Spec and rationale for `.github/workflows/clean-room.yml` (bd#100).*

## Why

`ci.yml` checks the repository out and runs it **in place**, on a preconfigured
self-hosted runner. Anything the runner happens to have — a tool on PATH, an
untracked file, a stale artefact — is silently part of the test environment.
That is not hypothetical: bd#97 made the test suite uncollectable from a clean
clone and survived, because no check we ran ever started from a
committed-tree-only copy.

The clean room removes both assumptions:

- **`git archive`** — committed files only. Untracked local state cannot
  participate. The container asserts this: no `.git`, and a file count equal to
  `git ls-tree -r --name-only` computed outside.
- **a stock `python:3.11-slim` container** — not the runner. Whatever the image
  does not ship, the project must bring itself.

It catches host coupling, undeclared dependencies, README drift and
untracked-file reliance on the day they are introduced, rather than at a
contributor's first contact. It is a job for a *class* of defect, not a bug.

## The three jobs

| job | image | host tools | asserts |
|---|---|---|---|
| `quickstart` | `python:3.11-slim` | **none** | README Quickstart verbatim, `bytedigger-engine doctor`, the keyless demo, and that the suite **collects** with zero errors |
| `suite` | `python:3.11-slim` | git, zsh, bun, semgrep | the **full suite** passes; the closure set is on PATH and documented |
| `debt-watch` | `python:3.11-slim`, `python:3.9-slim` | none | two known defects are **still** present — red the day either is fixed |

All three are blocking. None sets `continue-on-error`.

## Decision: how this is green on day one

A job that simply demanded a green `pytest` in a stock image would have been red
from its first run for a reason it does not fix: the suite needs host tools the
image does not ship (bd#102). Three ways out were considered.

**Rejected — collection-only.** Honest and green, but it never proves the suite
runs at all, so the tool debt stays invisible and unbounded.

**Rejected — allowed-to-fail with a deadline.** A yellow job is read by nobody,
and a date in a comment is enforced by nothing.

**Rejected — one job that installs the tools.** It works, and it quietly
destroys the thing the job exists for: the guarantee that an outsider's
`pip install` + demo path runs on a bare image.

**Taken — split the claims across jobs, and make the debt self-closing.**
`quickstart` keeps the bare-image guarantee with zero tools installed. `suite`
carries the tools, sourced from one machine-readable manifest, and requires the
suite green. Nothing is hidden, because the split is what states the gap.

That much still leaves a comment saying "delete this when bd#102 lands", which
nothing enforces. So `debt-watch` runs the bare-image suite and **requires it to
fail**. The moment it passes, the job goes red and says that the closure set is
now dead weight and names the files to delete. The exit criterion is a check,
not a reminder — and there is no date to miss.

The same mechanism carries the Python matrix (below).

### bd#102's closure set is larger than its title

Measured on this tree, `python:3.11-slim`, `git archive HEAD`:

| host tools present | suite result |
|---|---|
| none (stock image) | 468 failed, 36 errors, 3360 passed |
| `git`, `zsh` | 3 failed, 3899 passed |
| `git`, `zsh`, `bun` | 1 failed, 3901 passed |
| `git`, `zsh`, `bun`, `semgrep` | **0 failed, 3902 passed, 14 skipped** |

bd#102's title names `git` and `zsh`. Two more are required:

- **`bun`** — `test_phase_5_step2/3` assert a `ts` test group is executed, which
  only happens when `bun` is on PATH. (The clean room does not otherwise test
  the TS layer; it is pulled in transitively by these Python tests.)
- **`semgrep`** — the red-lint preflight refuses to run without it
  (`semgrep (SAST linter) not on PATH — build-critical`). It ships only in the
  **optional** `[security]` extra, yet the suite treats it as mandatory.

A git identity (`user.email`/`user.name`) is also needed by tests that commit;
the clean room sets a throwaway one. `curl`, `unzip` and `ca-certificates` are
installed too, but only to fetch bun — Debian does not package it — so they are
not part of the closure set.

The set lives in `scripts/clean-room/bd102-tools.manifest`, one tool per line
with its reason. `in-container.sh` asserts every entry is on PATH after install
**and** named in this document, so the set cannot grow silently.

## Python versions

`quickstart` and `suite` run **3.11**. `pyproject.toml` declares
`requires-python = ">=3.9"` and CONTRIBUTING says "3.9+", and the issue asks for
a matrix against the oldest supported Python — but 3.9 does not work today:

- `tier_gate.py:33` — `parents[4] if len(parents) > 4 else parents[-1]`.
  Negative indexing on `Path.parents` only exists from 3.10. It fires only when
  there is no enclosing `.git`, i.e. exactly the `git archive` case, which is
  why a clean *clone* never hits it. Result: `bytedigger-engine --derive-state`
  exits 1 and 74 test modules fail to collect.
- PEP-604 (`X | Y`) annotations evaluated at runtime raise `TypeError` in
  `test_engine.py` / `test_engine_W9.py`.

A normal 3.9 leg would therefore be a permanently red required check. Instead
`debt-watch` runs 3.9 **expect-fail**: the matrix leg exists, it is green today,
and it turns red the day 3.9 starts collecting — at which point it is promoted
to a real leg and the expect-fail mode is deleted. Until then,
`requires-python = ">=3.9"` remains a claim the project does not honour; that
metadata is fixed either by making 3.9 work or by raising the floor, and this
job makes sure the discrepancy stays visible.

## Not covered, and why

- **`tests/*.bats` and `bun test scripts/ts/`** — the Claude Code plugin layer.
  Needs bats and bun, and is on no outsider's README path.
- **The npm pointer package** (`npm/`) — a separate publish surface with no
  bearing on the Python clean room.
- **`pip install -e ".[agentic-pydantic]"`** — an optional extra that pulls
  `pydantic-ai` from the network and asserts no README promise the job can check
  offline. Recorded as a deliberate skip in the manifest, not silently dropped.
- **`ci.yml` is not replaced.** It still gates packaging, import health without
  the `dbos` extra, manifest parity and the wheel-installed suite — none of which
  this workflow covers. `suite` overlaps its `pytest` job, but from a
  committed-tree-only stock container rather than a runner checkout; that
  difference is the entire point, so both stay.

## Running it yourself

```bash
bash scripts/clean-room/run.sh                        # quickstart, python 3.11
bash scripts/clean-room/run.sh suite                  # full suite
bash scripts/clean-room/run.sh expect-fail:bd102      # debt watch
bash scripts/clean-room/run.sh expect-fail:py39 3.9   # debt watch, oldest Python
bash scripts/clean-room/run.sh quickstart 3.12        # any interpreter
CLEAN_ROOM_REF=origin/main bash scripts/clean-room/run.sh
```

Identical to what CI runs; CI adds only a docker preflight.

**It tests your last commit, never your working copy.** `git archive` is the
mechanism, so uncommitted changes are invisible to it. The driver warns when the
tree is dirty — commit first, or you will debug a run that does not contain your
fix.

## README parity guard

A clean-room job is worth nothing if it runs commands the README no longer
contains. `scripts/clean-room/readme_parity.py` (run as the first step of
`quickstart`) extracts the commands from every shell fence under README's
`## Quickstart` and compares them against
`scripts/clean-room/readme-quickstart.manifest`, failing on any difference.

Each manifest line declares which mode runs the command, or carries a mandatory
skip reason. Three are skipped today: `git clone` (the clean room ships an
archive, and the image has no git), `cd bytedigger/engine_py` (a working
directory change the script performs rather than executes), and the
`agentic-pydantic` extra. A command claimed as covered must actually appear in
`in-container.sh`, so bumping the manifest alone cannot silence the guard.

Change the Quickstart, and the job stays red until the manifest and the
clean-room script are updated with it.

## Operational notes

- **Runner.** `[self-hosted, bytedigger]`, used only as a docker host — the repo
  is private, so GitHub-hosted minutes are billed and every existing job is
  self-hosted. The host coupling this workflow exists to catch is removed by the
  *container*, not by the runner: nothing is bind-mounted, no docker socket is
  passed in, and the container receives no host paths. What the host can still
  influence is the docker layer cache and the base-image tag it resolved.
- **Fork pull requests.** This workflow gives PR-authored code access to the
  runner's docker daemon. Keep GitHub's approval requirement for outside
  collaborators enabled; do not add `pull_request_target`.
- **Requirements on the runner:** `docker` on PATH and a reachable daemon.
  `run.sh` preflights both and fails with a clear message otherwise. It also
  **resolves the daemon socket itself**: a runner started as a launchd/systemd
  service does not inherit the interactive shell's `HOME` or `docker context`,
  so the CLI falls back to the `default` context (`/var/run/docker.sock`) and
  declares the daemon down while it is in fact running under colima or Docker
  Desktop. The driver tries the ambient config first, then the known socket
  paths, and exports `DOCKER_HOST` for the run. Set `DOCKER_HOST` yourself to
  override. This bit the first CI run of this very workflow.
- **Network.** The container reaches Docker Hub (image pull), PyPI (all jobs) and
  `bun.sh` (`suite` only). Unauthenticated Docker Hub pulls are rate-limited per
  IP; a busy day can 429. These are accepted flake sources — a clean room that
  never touches the network would not be installing anything.
- **Base image drift.** `python:3.11-slim` is a moving tag, so a run can flip
  green→red with no repo change. Accepted deliberately: pinning a digest would
  freeze the very environment drift this job exists to notice, and the driver
  takes the version as an argument precisely so legs can be added cheaply.
- **On `pull_request`,** `actions/checkout` provides the merge commit, so
  `git archive HEAD` ships the tree as it would land on `main`.
- **Cost.** Roughly 3 minutes for `quickstart`, 6–8 for `suite`, 4 for
  `debt-watch`, dominated by the suite runs themselves.
