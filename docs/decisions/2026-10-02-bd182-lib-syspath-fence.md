# bd#182 — no test may put anything inside `bytedigger_engine/` on sys.path

Follow-up to bd#180 / PR #181 (MINOR findings deferred by the #181 gate).

## Problem

Since bd#44 the engine is one package. `engine_py/tests/conftest.py` exposes
only `engine_py/` (the package parent) and `engine_py/tests/`. PR #181 removed
every test-suite insert of `bytedigger_engine/workflows` and added a runtime
fence for it. The same leak remains for `bytedigger_engine/lib` (≈20 modules)
and `bytedigger_engine/scripts/lib` (gh962, gh891). A leaked directory makes
flat names such as `model_config` or `ground_truth_verifier` importable and
lets a test pass only because some other module ran first.

The runtime fence sees sys.path only at the moment it runs, so an insert in a
test body that runs later (BC45C403, the in-body `_ensure_lib_on_path`
helpers) slips past it.

## Change

1. **Remove** every test-suite sys.path insert whose target is inside
   `engine_py/bytedigger_engine/` (any subdirectory, any spelling: direct
   `Path / "bytedigger_engine" / ...`, a `str(...)` of it, a module constant,
   a `for p in (...)` tuple, `os.path.join`). Inserting `engine_py/` itself is
   allowed. No test currently imports a lib module by flat name, so imports do
   not change; if one turns up, convert it to the package path.
2. **Runtime fence** (`test_bd182_*`): at run time no `sys.path` entry resolves
   to `bytedigger_engine/` or any path below it, and the flat names
   `model_config`, `verdict_parse`, `authored_boundary`,
   `ground_truth_verifier` are not importable (`importlib.util.find_spec`
   returns None).
3. **Static fence** (`test_bd182_*`): an AST scan of every `*.py` under
   `engine_py/tests/` (excluding `__pycache__`) finds every sys.path mutation
   and fails, naming `file:line`, when its target is inside
   `bytedigger_engine/`.
   - Mutations recognised: `sys.path.insert/append/extend(...)`,
     `sys.path[...] = ...`, `sys.path = ...`, `sys.path += ...`,
     `monkeypatch.syspath_prepend(...)`; `sys` may be imported under an alias
     (`import sys as _sys`).
   - Target "inside the package": the string constants reachable from the
     argument contain the path component `bytedigger_engine`. Reachable means
     the argument expression itself plus, for each bare name in it, the value
     of every assignment / for-loop iterable binding that name anywhere in the
     same module, followed transitively (cycle-safe).
   - No allowlist.
   - The scanner is itself tested on inline snippets: each recognised spelling
     is flagged; an `engine_py/` root insert, a `tmp_path` insert, and a
     docstring that merely mentions `bytedigger_engine/lib` are not.
4. **Minor (no behaviour change):**
   - `test_F3A8F4FC_phase12_sonnet_downgrade.py` (~259, ~315): the guard tests
     the flat key `"phase_1_discovery"` / `"phase_2_explore"` but deletes the
     package key, so it never fires. `_default_llm_command()` resolves the model
     at call time, so the re-import is unneeded: drop the guard and the
     "drop from sys.modules" comment; keep the lazy package import.
   - Stale comments that still describe the pre-bd#44 top-level module copy:
     `test_phase_6_fuzzy_citation_match_A37D4F04.py:45`,
     `test_906e37dc_review_findings_audit.py:365-368`, the "Module trap" and
     `from config_provider import` passages in the
     `test_GH1471_inject_path_and_visibility.py` docstring. Rewrite them to the
     single-package truth.

## Out of scope

`engine_py/bytedigger_engine/engine.py`, `phases/` (bd#89 P2a is in flight).
Production code does not mutate sys.path today and is not scanned.

## Acceptance

- New `test_bd182_*` file is RED on origin/main (static scan lists the
  offenders; runtime fence fails in a full-suite run) and GREEN after.
- `pytest engine_py/tests` full run: no new failures vs origin/main.
- `test_bd180_suite_flat_workflow_names_fence.py` still green.
