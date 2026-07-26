# Contributing

Thanks for taking an interest. The project is small enough that there's no process bureaucracy -- open an issue or a PR and we'll figure it out.

Editable installs (`pip install -e`) of this pyproject-only package need Python 3.9+ and pip 21.3+ (PEP 660) -- older pip fails with "Directory cannot be installed in editable mode".

## Running the tests

The engine lives in `engine_py/` and is plain Python (3.9+), no runtime dependencies. Tests use pytest:

```bash
cd engine_py
pip install -e ".[test]"
python -m pytest tests/ -q
```

The suite is hermetic -- no network, no dbos, no API keys needed. If a test wants any of those, that's a bug.

There's also a small TypeScript side (gate scripts). If you touch it:

```bash
bun install
bun test scripts/ts/__tests__
```

## What CI checks

Every PR runs three jobs, and all must pass:

- **engine** -- packaging sanity: `pip install -e ".[test]"` works, every shipped module compiles and imports with no `dbos` extra installed, the packaged module list matches `core_manifest.json`, and `run.py --help` runs.
- **pytest** -- builds a wheel, installs it, and runs the full engine test suite against the installed wheel (not the source tree).
- **manifests** -- version parity: every version declaration in the repo matches the canonical one, and the parity script's own tests pass.

The "imports clean without dbos" check matters most: the core is meant to run on a bare Python install, and CI is what keeps it that way. If your change needs a new dependency, put it behind an optional extra and bring it up in the PR.

## Releasing

The version lives in one place: `engine_py/pyproject.toml` `[project].version`. Everything else
is derived from it -- root `package.json`, `npm/package.json`, `.claude-plugin/plugin.json`, and
`.claude-plugin/marketplace.json` (`plugins[0].version`). Five declarations, one source.

Bump with the script, not by hand:

```bash
python3 scripts/version_parity.py --write 0.2.0   # sets all five
python3 scripts/version_parity.py --check         # what CI runs
```

`--check` is what the `manifests` CI job runs, so a partial bump fails the PR rather than
reaching a registry. This matters: 0.1.1 shipped to PyPI while the plugin manifests stayed at
0.1.0, because the bump was done by hand -- one commit set two of the five files, a follow-up
set a third, and two were still missed.

Note that `CHANGELOG.md` states the versioning invariant over only three of the five
declarations; `scripts/version_parity.py --list-declarations` is the authoritative list.

## Style

- New behavior comes with a test. Bug fixes come with a test that fails before the fix.
- Match the surrounding code; don't reformat things you didn't change.
- Keep the core free of dependencies (see above).
- Small, focused PRs get reviewed and merged much faster than big ones.

## Questions

Open an issue. No template, just say what you're trying to do.
