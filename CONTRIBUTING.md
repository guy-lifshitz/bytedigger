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

Every PR runs two jobs, and both must pass:

- **engine** -- packaging sanity: `pip install -e ".[test]"` works, every shipped module compiles and imports with no `dbos` extra installed, the packaged module list matches `core_manifest.json`, and `run.py --help` runs.
- **pytest** -- builds a wheel, installs it, and runs the full engine test suite against the installed wheel (not the source tree).

The "imports clean without dbos" check matters most: the core is meant to run on a bare Python install, and CI is what keeps it that way. If your change needs a new dependency, put it behind an optional extra and bring it up in the PR.

## Style

- New behavior comes with a test. Bug fixes come with a test that fails before the fix.
- Match the surrounding code; don't reformat things you didn't change.
- Keep the core free of dependencies (see above).
- Small, focused PRs get reviewed and merged much faster than big ones.

## Questions

Open an issue. No template, just say what you're trying to do.
