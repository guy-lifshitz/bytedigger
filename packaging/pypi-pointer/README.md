# bytedigger

Pointer package for [ByteDigger](https://github.com/guy-lifshitz/bytedigger) — a verified
agentic TDD engine. Installing this installs `bytedigger-engine`, which holds the code.

```bash
pipx install bytedigger
python3 -m pip install bytedigger
```

The two names version together: `bytedigger X.Y.Z` always requires `bytedigger-engine==X.Y.Z`.
That equality is checked by `scripts/version_parity.py` in the source repo, so the friendly
name cannot drift behind the engine.

Documentation, quickstart and the engine itself live in the repository above.
