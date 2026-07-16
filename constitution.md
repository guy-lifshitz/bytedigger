# Project constitution

Rules the factory injects into every build. Edit them for your project;
`bytedigger.json` points here via `constitution_path`. Markdown principles
use `## <title>` sections; SpecKit JSON works too (see
`engine_py/lib/constitution_loader.py`).

## Small, verifiable changes

Prefer the smallest change that satisfies the spec. One concern per
commit; no drive-by refactoring outside the spec's file allowlist.

Examples:
- A bugfix ships without renaming neighboring functions.
- New behavior lands with the test that forced it.

## Errors fail loud

No silent fallbacks. A caught exception either recovers with a stated
reason or propagates; an empty catch block never ships.

## Match the surrounding code

New code reads like the file it lives in: same naming, same comment
density, same idioms. Cleverness loses to consistency.
