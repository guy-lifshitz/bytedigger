---
id: BD44
title: bytedigger-engine moves into the package bytedigger_engine/ — 51 flat modules stop occupying generic names in site-packages
date: 2026-08-03
issue: guy-lifshitz/bytedigger#44
class: cost/public-surface — a public package poisons other people's environments; superclass
  "the install succeeds, but the namespace is broken"
chokepoint: `engine_py/pyproject.toml` — the only place where it is decided WHAT lands
  in site-packages. Acceptance is NOT by a green CI but by a real installation of the built wheel.
tier: full cycle (spec → RED → Opus gate → GREEN → verify → PR)
status: FROZEN 2026-08-03 (rev6 — reconciled with RED; rev2-rev5 were rejected by the gate)
---

# BD44 — the move into `bytedigger_engine/`

## §0. The premise — verified by a BUILD, not by reading

Built from a clean clone of `385b1ed`: `python3 -m pip wheel . --no-deps` in `engine_py/`.
The contents of the wheel `bytedigger_engine-0.1.2-py3-none-any.whl`, top level:

```
top-level entries in total:   56
  flat .py modules:           51
  package directories:         5   (lib, scripts, security, workflows, conformance)
a bytedigger_engine package:  NONE
```

The generic names occupied (measured, not listed from memory): `config_provider.py`,
`contracts.py`, `ctx_floor.py`, `doctor.py`, `engine.py`, `event_log.py`, `io_utils.py`,
`run.py` — and, worse, four directories named `lib`, `scripts`, `security`,
`workflows`. A `lib` directory in site-packages is no longer a collision risk but very nearly
a guarantee.

The three numbers are reconciled (rev6): **56** — entries in the WHEEL; **57** — the issue's measurement in
site-packages; **58** — my own measurement in the same place. The difference between the wheel and site-packages is
the installer's own directories (`dist-info`, `__pycache__`), and between 57 and 58 — the composition of the
venv on different machines. The subject depends on none of the three: the package has no namespace
under any count. The base of AC3 is NOT a number but the claim "exactly one
entry" — precisely because the count is unstable.

**The issue's third point is confirmed constructively:** `[project.scripts]` declares
`bytedigger-engine = "run:main"`, i.e. the entry point hangs off a top-level `run`.
Neither `bytedigger` nor `bytedigger_engine` exists in the wheel, so whoever installs
the package has not a single guessable name to import.

## §1. §1b — the cost of the move was measured BEFORE the freeze

A naive `git mv` will break everything: the modules import each other by top-level names.

```
files with internal absolute imports: 172
such imports in total:                758
example: run.py — 20, llm_subprocess.py — 14
```

That is the real volume of work, and it must be named by number before the freeze rather than
discovered in GREEN. The mechanical part is rewriting 758 imports; the risk is
dynamic imports (`importlib`, `__import__`, string names in configs), which
an AST walk does NOT see and which must be enumerated separately in §2.3.

## §2. The contract

### §2.1 The form (set by the requester, not invented here)

- One package `bytedigger_engine/`; everything that is flat today moves under it.
- The console script `bytedigger-engine` IS PRESERVED (we do not break the user interface),
  only its target changes: `bytedigger_engine.run:main`.
- `import bytedigger_engine` — the public entry point, it must exist.
- A breaking change to imports ⇒ version **0.2.0**.

### §2.2 What counts as done

Acceptance is by a **real installation**, not by a green CI:

1. build the wheel from the repository;
2. `python3 -m venv` — a CLEAN venv, with no inheritance of site-packages;
3. `pip install <wheel>` — exit 0;
4. `python -c "import bytedigger_engine"` — exit 0;
5. `bytedigger-engine --list` — exit 0 and **21 workflows**;
6. in the venv's site-packages there is **not one** of the measured top-level generic names
   (neither `contracts.py` nor the directories `lib`/`scripts`/`security`/`workflows`/`conformance` —
   five directories, not four).

Point 6 is the subject of the lot; points 4-5 are what must not be broken along the way.

### §2.3 The edges that must be enumerated (§1n)

- **Dynamic imports.** An AST walk counts only static ones; string module
  names (`importlib.import_module("contracts")`, configs, `--module` flags) must
  be found by a separate grep and enumerated by name in GREEN. A missed
  dynamic import is a runtime failure for the user, not a red test.
- **package-data.** `[tool.setuptools.package-data]` addresses `lib.plugins.*`,
  `security`, `scripts.red_lint` — every path must move together with the packages,
  otherwise the wheel will build and the YAML/MD will not arrive.
- **The repository's tests** import the same top-level names; they move
  together, but editing them must NOT mask a broken production import.
- **`conftest.py`** at the repository root — verified, it is **0 bytes** and takes no part in path
  resolution. What matters is `engine_py/tests/conftest.py:37-49` — see §5.

## §1c. Second-round measurements (made after the first revision)

1. **The 21 workflows were RE-MEASURED, not taken from the issue.** Installing the current wheel into a clean
   venv (`python3.14`), then `bytedigger-engine --list` → a JSON array, `len == 21`,
   rc=0. The number is fit for use as a numeric AC: there is a live base.
   In the same venv, top-level site-packages entries (excluding pip/setuptools/dist-info):
   **58** — agreeing with the issue's 57 up to the installer's own directories.
2. **Dynamic imports were found, and one of them is a real trap.**
   `doctor.py:90` holds module names as STRINGS:
   `["stub_passability", "red_lint_checks", "verdict_gate", "spec_cite", "tier_gate"]`,
   followed by `__import__(mod_name)`. The §1 AST walk does NOT see them. After the move they must
   become `bytedigger_engine.*`, otherwise `doctor` will report "missing module" — a failure for
   the user in a diagnostic tool, not a red test.
   `forbidden_import.py` contains the same tokens as DATA (lint patterns) rather than as
   calls — it need not be touched; distinguishing these two cases within the edit scope is mandatory.
3. **The good news, which removes a risk: workflow registration is EXPLICIT.**
   `workflows/__init__.py` calls `engine.register("echo", …)` by name; there is no discovery by
   file name. So the move cannot silently change the composition of `--list` — it can
   only break the import outright, which is visible at once.
4. **The entry point extends the namespace AT RUNTIME — and it is not alone.**
   rev1 named two insertions in `run.py:24-27`. That is an understatement by a factor of thirty.
   Re-measured by an AST walk (excluding `tests/` and the build junk in `build/`):

   ```
   CALLS to sys.path.insert/append in the code: 63  in 42 files
   including lib/cost_rollup.py, lib/dispatcher_report.py, doctor.py, event_log.py,
             workflows/*, error_codes.py, classify_incident.py …
   ```

   The move must remove ALL 63, otherwise the package will keep working through a path hack and the
   class of collisions will outlive the fix (AC7).

5. **The data trap rev1 fell into itself.** The same symbols occur as
   STRING LITERALS — 7 occurrences in three files: `silent_success.py`,
   `suite_safety.py`, `workflows/phase_5_implement.py` (the last being the text of a product prompt).
   A substring check over the installed files WOULD REQUIRE editing the product's
   text, i.e. it is unimplementable. That is why AC7 counts CALLS by AST rather than substrings —
   exactly the distinction §1c.2 warns about and which rev1 failed to apply to itself.

6. **The version is declared in several places** (there are six in `version_parity.DECLARATIONS`; I counted four files, because two declarations live in the same pointer file), and parity is watched by
   `scripts/version_parity.py`: `engine_py/pyproject.toml`, `package.json`,
   `packaging/pypi-pointer/pyproject.toml` (its own version AND the pin
   `bytedigger-engine==…`). Raising only the first means leaving
   `pip install bytedigger` serving 0.1.2 (a relapse of the documented incident
   0.1.0→0.1.1). Covered by AC6.

7. **The public surface besides the CLI:** `examples/library/*.py` show
   `from contracts import …` as the documented way to use the library.
   The move breaks them silently — they are in scope (AC10).

## §3. Acceptance criteria

Every AC reads the ARTEFACT the user will get: the wheel built from the
repository, and the clean venv it was installed into. No AC reads the source
tree — except AC10, where the subject is the source of the examples.

- **AC1 (§1l).** `import bytedigger_engine` in a clean venv — exit 0. Today it is
  `ModuleNotFoundError`.
- **AC2 (the shield, we do not break the interface).** `bytedigger-engine --list` — exit 0 and exactly
  **21** workflows. The number is a live base, measured on the PRE-move wheel.
  ONLY the JSON is parsed, in isolation from any extraneous output lines.
- **AC3 (the subject, rev2 — rewritten).** The distribution owns **EXACTLY ONE**
  top-level entry in site-packages, and it is `bytedigger_engine`. The source is
  the wheel's own `RECORD`/`top_level.txt`, not a list of names. rev1 checked a white
  list of 13 measured names: an implementation that left 43 other generic names behind
  would have passed straight through it. "None of the listed ones" ≠ "does not occupy the top
  level".
- **AC3b.** The package is NOT empty and is NOT a namespace stub: it contains an `__init__.py` and no fewer than
  forty modules — otherwise an empty directory would satisfy AC3.
- **AC4 (a non-empty corpus).** The number of the distribution's top-level entries and
  the number of modules inside the package are printed; both are asserted to be > 0. The values are taken from the artefact,
  not from the test's constants (rev1 compared two of its own constants — a tautology).
- **AC6 (the version, rev2 — extended).** The wheel declares **0.2.x**, AND
  `scripts/version_parity.py` passes, AND the pointer package pins
  `bytedigger-engine==0.2.x`. Without the latter two, `pip install bytedigger` will keep
  serving 0.1.2.
- **AC7 (the path hack removed, rev2 — by AST).** In the installed package there are **zero CALLS** to
  `sys.path.insert/append`. Counted by parsing the AST, not by substring: the same symbols
  live as string literals in the text of a product prompt (§1c.5), and a substring
  check would require editing the product for the sake of a test.
- **AC7b (behaviourally, rev2 — the right side).** After `import bytedigger_engine`,
  a bare `import contracts` and `import config_provider` must fail with
  `ModuleNotFoundError`. rev1 probed `phase_2_explore` — a module from `workflows/`, i.e.
  it checked the wrong class: an implementation that hid only `workflows/` while
  leaving the flat generic names behind would have passed it.
- **AC8 (string names).** `doctor.check_gates_importable` and `check_engine_imports`
  in a clean venv do not report "missing module".
- **AC9 (package-data, rev2 — new).** The assets declared in
  `[tool.setuptools.package-data]` (the semgrep rules, the red-lint ruleset, the plugin YAML/MD)
  are present in the wheel UNDER the package. Otherwise the wheel will build green and the rules will not
  reach the user.
- **AC11 (rev3 — DECISIVE).** No module INSIDE the package imports a neighbour
  by a bare top-level name. The set of neighbours is derived FROM THE ARTEFACT. Without this AC
  the rest of the table describes only the SHAPE of the tree, and a `package_dir` remap with
  a differently-written path hack passes it with ZERO of the 758 imports rewritten —
  that is, no move happens at all.
- **AC11b (rev3).** After a run of the ENTRY POINT (`run.main`), not merely an import of
  the package, `sys.path` has not grown and the flat names do not resolve. The hacks live on the path of
  the console script, so the moment of measurement decides.
- **AC10 (the public examples, rev3 — the set of names is DERIVED, the corpus extended).** `examples/library/*.py` import
  through `bytedigger_engine`, not through top-level names. This is the documented
  way to use the library; breaking it silently is the same as breaking the CLI.

## §5. Scope and §1a — the sibling audit (rev3, MAJOR-D)

**In scope:** `engine_py/pyproject.toml`, the whole contents of `engine_py/` (the move),
`packaging/pypi-pointer/pyproject.toml` and `package.json` (the version), `examples/**`,
`engine_py/tests/**` (imports), this spec, RED.

**The named casualties — to be checked by name, not "by delta":**
- `engine_py/tests/test_engine_path_closure.py:63-70` — asserts the closure of paths;
  the move affects it directly. The edit must be DELIBERATE, not "an adjustment
  to make it go green".
- `engine_py/core_manifest.json` — today 1:1 with `py-modules`. After the move the
  correspondence must be preserved or deliberately redefined.
- `engine_py/tests/conftest.py:37-49` — participates in path resolution for the tests.
  **Fence:** an edit to conftest that makes the tests green by returning the flat names to
  `sys.path` devalues the §1r delta entirely. If conftest is edited, that is
  declared separately and justified.
- The repository's root `conftest.py` — **0 bytes**; rev1/rev2 claimed it
  resolved paths. The claim is withdrawn.

### §3a. The terminal branch of the entry point (rev6, MINOR-26)

The entry point MAY terminate via `os._exit` (`hard_exit`, the token
`HAL_RUNPY_HARD_EXIT`). This is neither a defect nor an edge — it is declared product behaviour,
and any AC measuring a run of the entry point must SURVIVE it. The practical
conclusion, paid for by four vacuums in a row: post-hoc state snapshots
are unusable; only a streaming channel flushed line by line will do, and the absence
of evidence must be read as a FAILURE of the measurement, not as a clean result.

The ACs' supports are named explicitly so that §3 does not diverge from RED:
- AC9 rests on a MEASUREMENT of the source tree (`lib/plugins/anti_hallucination`: 3,
  `security`: 5, `scripts/red_lint`: 1 — 9 assets in total), and NOT on the
  `[tool.setuptools.package-data]` block, which GREEN rewrites itself.
- AC11b rests on the audit hook `sys.addaudithook` rather than on `run.main(...)`:
  `main()` is declared without parameters.

**Mutation** (not an AC, a separate destructive run after GREEN): return one module to
the top level ⇒ AC3 must go red; return one `sys.path.insert` ⇒ AC7.
rev1 declared this as AC5 and did not implement it — a declared and unenforced check
is worse than a missing one.

## §4. Status

**FROZEN.** The §0, §1 and §1c measurements are made and reproducible; both
gaps of the first revision (the workflow count and the dynamic imports) are closed. Next:
freeze, RED → Opus gate → GREEN → acceptance by a real installation → PR (do not merge).

The 5h pool budget was nearly exhausted, so only the spec and the measurements were done in this window;
nothing was built in CI and nothing was pushed.
