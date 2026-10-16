---
red_tests:
  - engine_py/tests/test_bd323_spec_cite_driver.py
sibling_tests:
  - engine_py/tests/test_bd305_spec_lint_feed_fences.py
  - engine_py/tests/test_bd304_spec_lint_drivers_fences.py
  - engine_py/tests/test_bd302_test_lint_drivers_fences.py
  - engine_py/tests/test_bd81_registry_driver_resolution.py
  - engine_py/tests/test_phase_45_spec_cite_fallback_3F5599A6.py
  - engine_py/tests/test_spec_cite_gh366_367.py
paths:
  - engine_py/bytedigger_engine/spec_cite.py
  - engine_py/bytedigger_engine/precommit_lints.py
  - spec-cite-lint.py
  - engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md
  - engine_py/bytedigger_engine/conformance/CONTRACTS_SPEC.md
  - engine_py/bytedigger_engine/conformance/EMISSIONS_SPEC.md
  - engine_py/bytedigger_engine/conformance/ORACLE_SPEC.md
  - CHANGELOG.md
---
# bd#323: spec-cite becomes a blocking lint (driver + layer) on a clean spec set

Refs #323 (slice of #315 / #66). Base: origin/main @ 27ae4d6. Tier 2. Spec edits to the four conformance specs are citation paths only; no phase code (`workflows/`) is touched.

## 0. Step 0 (reproduction on fresh origin/main)

Reproduces. `spec_cite.lint_spec` over the five `conformance/*_SPEC.md`, with the union rule (a finding is blocking only if the same `(file, symbol)` is blocking under repo-root resolution AND under package-root resolution; package root = top of the `__init__.py` chain above the spec, as `_package_root` in `test_bd305_spec_lint_feed.py`): AUTHORSHIP 3, CONTRACTS 1, EMISSIONS 1, ORACLE 13, QUANT 0 = 18 findings. There is no `spec-cite-lint.py` at the repo root, `spec-cite-lint` is the only name left in `DECLARED_ABSENT`, and `phase_45_spec._verify_spec_cite_lint` (l.2255) computes `<repo>/spec-cite-lint.py`, finds it missing and returns `E_SPEC_CITE_LINT_UNAVAILABLE`, so no spec is ever checked by it.

After the citation-path edits of §2.A the union measure is 0 for all five specs (re-measured).

## 1. Class and chokepoint (§1-PREFLIGHT)

Class: a registered lint with a live consumer but no driver file (held in `DECLARED_ABSENT` because the cleanliness of the spec set was never established), i.e. a gate that is declared and never runs.
Chokepoint: ONE rule carrier, `spec_cite.py` (new `lint_spec_union`); ONE driver, root `spec-cite-lint.py`, which both consumers (phase_45 and the pre-commit layer) execute; ONE registry, `precommit_lints.py` (`DECLARED_ABSENT` / `LINT_DRIVER_DIRS`). No copy of the union rule lives in a driver or a test.

## 2. Change

A. Spec edits (citation paths only, plus two disclosed non-path edits). File exists elsewhere: the path is corrected (`lib/git_port.py`, `workflows/phase_45_spec.py`, `conformance/attest.py`, `conformance/oracle.py`, `engine_py/tests/test_gh1067...`, `engine_py/tests/test_phase_6_FEB64BA8...`). Target gone or third-party: the path token is dropped (`workflows/phase_2_explore.py`, `graphify-shim.sh`, `_pytest/cacheprovider.py`, `run.py`, `run-phase.ts`, `workflows/__init__.py:34-36`, `run_oss_driver.py`, one `llm_subprocess.py:609` table-row cell). Non-path edit: the backticks around `PytestCollectionWarning` on ORACLE_SPEC l.1309 are removed (it is a warning class of a third-party package, not a repo symbol).

B. `spec_cite.py`: `lint_spec_union(spec_path, repo_root) -> tuple[int, list[Finding]]`. If the spec's directory holds an `__init__.py`: lint under `repo_root` and under the package root; keep a repo-root finding with a blocking status only if the same `(file, symbol)` is blocking in the package-root lint; downgrade the others to `resolved`-equivalent advisory by dropping them. Otherwise it equals `lint_spec(spec_path, repo_root)`. `exit_code` is recomputed from the kept findings. `lint_spec` itself is unchanged.

C. New root `spec-cite-lint.py` (shape of `token-consistency-lint.py`, imports `bytedigger_engine.spec_cite`): `--spec PATH [--repo-root R] [--json]`; `--repo-root` defaults to the directory of the driver itself. Exit 0 clean, 1 a blocking finding, 2 unreadable spec or usage. JSON `{"findings": [{"file", "symbol", "status"}]}` (what `phase_45_spec._parse_cite_blocking` reads); text: one line per blocking finding `SPEC-CITE <status> <file> <symbol>`.

D. `precommit_lints.py`: `spec-cite-lint` leaves `DECLARED_ABSENT` (the list is now empty; the comment is rewritten) and enters `LINT_DRIVER_DIRS` with `_REPO_ROOT`. The sibling tests that pin `DECLARED_ABSENT == ["spec-cite-lint"]` are edited in the RED to pin `== []` (not relaxed). The issue text says "== {spec-coverage-lint} or whatever remains": the actual remaining set is empty.

E. CHANGELOG line.

## 3. Acceptance criteria

- AC1: the real driver (`spec-cite-lint.py --spec P`, no `--repo-root`) exits 0 on each of the five existing conformance specs.
- AC2: `lint_spec_union` on a copy of a conformance spec with one added backticked citation `engine_py/bytedigger_engine/no_such_module.py` `no_symbol` returns a nonzero exit and a blocking finding for it; on the unmodified copy it returns exit 0.
- AC3: `lint_spec_union` on a spec outside any `__init__.py` package equals `lint_spec` (same exit code and same findings) for a clean and for a blocking fixture.
- AC4: the phase-45 check (`_verify_spec_cite_lint`, driver located by its real path, run via the engine or via subprocess with `--spec P --repo-root R --json`): fails (exit 1, a blocking finding parsed by `_parse_cite_blocking`) on a fixture spec citing a missing path, passes on a clean fixture; JSON has the `findings[].file/symbol/status` keys.
- AC5: exit 2 for a missing spec file and for no `--spec` argument; never exit 0.
- AC6: `DECLARED_ABSENT == []`; `"spec-cite-lint"` is in `LINT_DRIVER_DIRS`; the registry/driver invariant of bd#66 holds (every name in `SPEC_LINTS` has a driver on disk).
- AC7: the real pre-commit CLI layer (`precommit_enforce_cli.py`) refuses a staged `x_spec.md` that cites a missing path (names `spec-cite-lint`) and accepts its clean twin. That the five fixed conformance specs are clean is AC1's claim (the driver run in place, where the package-root union applies); a flat clone has no `__init__.py` chain, so it cannot reproduce it (gate r1 finding 1).
- AC8 (kill-switch): re-adding `spec-cite-lint` to `DECLARED_ABSENT` in a clone while the driver exists does not suppress the driver (a declaration licenses an absence, it does not hide a present driver; bd#66 §2.2); removing the driver file from a clone while the name is absent from `DECLARED_ABSENT` makes the layer refuse (undeclared absence).
- AC9: the sibling tests in the front matter are green, and the pin tests edited by the RED (bd302, bd304, bd305, bd308, bd315 flip and guards, bd66; they hold passing and failing tests, so they sit in neither front-matter list: lot-preflight wants an all-failing red list and an all-green sibling list) are green after GREEN, as are `test_bd85`, `test_bd87` and `test_gh747` (they mention `spec-cite-lint.py`, which phase 45 now runs as a blocking gate); the ones that pinned the held state are edited to the new state without loosening an assertion.

## Known limitations

The 37 frozen lot specs under `docs/decisions/` (carry `red_tests:` front matter) are not fed to the layer: their names do not end in `_spec.md`. Adding the root driver turns phase-45 into a blocking gate for NEW specs (an unresolved citation now stops the build at the spec step): that is the intent of the lot. `spec-coverage-lint` feed rules are unchanged. The union rule loosens a repo-root finding when the package-root resolution finds it; it never adds findings. Not covered by an AC (gate r1, minor): a relative `--spec` path from the layer (the package-root walk must resolve it, else it falls to plain repo-root); an unknown flag or a nonexistent `--repo-root` (exit 2, never 0); an empty/blank spec (exit 0); a killed AC2 run can leak a `_bd323_probe_*.md` in the package dir (does not match `*_SPEC.md`).

## 4. Out of scope

Phase code (`workflows/`), lots 317/318 files, other lints, the frozen-spec feed, CI job wiring (bd#311 covers the range mode).
