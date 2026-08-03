"""bytedigger_engine — the verified agentic TDD engine, as ONE package.

Everything the distribution ships lives under this namespace: the engine core
(`engine`, `contracts`, `event_log`, `run`, …), the support library (`lib`),
the workflow phases (`workflows`), the security ruleset (`security`), the lint
scripts (`scripts`) and the conformance suite (`conformance`).

Before 0.2.0 these were 51 flat modules and 5 directories laid straight into
site-packages, taking generic top-level names (`contracts`, `engine`, `run`,
and the directories `lib`, `scripts`, `security`, `workflows`, `conformance`).
0.2.0 moves the whole corpus under this single name; the import paths change
accordingly, which is why the minor version is bumped.

Deliberately kept import-light: this module pulls in nothing, so that importing
any single leaf (e.g. `bytedigger_engine.package_meta`, used by the doctor to
diagnose broken environments) never drags in the rest of the engine.

The console script `bytedigger-engine` is unchanged and now points at
`bytedigger_engine.run:main`.
"""
from __future__ import annotations

__all__: list[str] = []
