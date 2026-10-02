# GH1551 gate verdict r1: AC31/AC32 under the bytedigger package layout

Spec: `docs/decisions/2026-10-01-hal1551-ac31-ac32-package-layout-spec.md`
UUT: `engine_py/tests/test_gh1406_red_skeleton_provenance.py`
Prod (read-only reference): `engine_py/bytedigger_engine/lib/red_skeleton.py`
Cross-referenced: `engine_py/tests/conftest.py`, `engine_py/tests/test_bd44_package_namespace.py`, `engine_py/tests/helpers/host_tools.py`, `.github/workflows/ci.yml`, `engine_py/pyproject.toml`

## Step 1: spec-internal consistency (literal tokens)
- The child-command literal, `ENGINE_ROOT`, the AC32b test name, and the A5 grep strings are consistent. `"AC31/AC32 additionally degenerate"` matches the trailing sentence of all 24 skip reasons byte for byte.
- Drift 1: Change 3 bans `from lib import red_skeleton` (ImportFrom module `lib`) but does not ban `import lib.red_skeleton` (Import alias `lib.red_skeleton`). Both register `sys.modules["lib.red_skeleton"]`, which is the same second-instance hazard. See MAJOR-2.
- Drift 2: Change 4 says to replace the "bd#44 PORT NOTE ... Original HAL rationale follows ..." block, but the scope is ambiguous. The rationale tail (L119-133) still says `sys.modules["lib.X"]` (false in the package layout) and "AC32 pins the identity so drift is named" (L133), which would dangle once AC32 is deleted. No acceptance item catches it. See MINOR-2.
- Drift 3: the module docstring (L44-45) says "No AC classifies anything by reading a production file's text or AST". AC32b, when scanning `bytedigger_engine/`, does exactly that, and the spec does not amend that sentence. See MINOR-3.
- Drift 4: Out-of-scope cites "the 30 phase B wiring tests". Measured: 24 (this file) + 9 (`test_gh1338_corpus_parity_gate.py`) = 33 tests carry the `hal#1145 phase-1 declared gap` reason before the change, and 31 after. See MINOR-5.

## Step 1.5: rule-overlap simulation (AC32b import classifier, all branches)
| Input | Branch hit | Spec outcome | Correct? |
|---|---|---|---|
| `import red_skeleton` / `... as rs` | Import alias == red_skeleton | FAIL | yes |
| `import red_skeleton.x` | Import alias startswith `red_skeleton.` | FAIL | yes |
| `from red_skeleton import build_stamp` | ImportFrom L0 module == red_skeleton | FAIL | yes |
| `from lib import red_skeleton` / `from lib.red_skeleton import X` | ImportFrom L0 module lib / lib.red_skeleton | FAIL | yes |
| `import lib.red_skeleton` | no branch | PASS | **no: bypass (MAJOR-2)** |
| `from lib import dirty_tree_guard` | ImportFrom L0 module == lib | FAIL | over-broad but harmless; the message must say "flat lib spelling" (MINOR-7) |
| `from . import red_skeleton` (in lib/) | level 1, skipped | PASS | yes: resolves to `bytedigger_engine.lib.red_skeleton`, the same instance |
| `from .lib import red_skeleton` / `from ..lib import X` | level >= 1, skipped | PASS | yes: legitimate. 93 relative imports exist in the package and none is a false positive, because `level == 0` is checked first |
| `from bytedigger_engine.lib import red_skeleton` / `import bytedigger_engine.lib.red_skeleton` | no branch | PASS | yes |
| AC31 child string `"from bytedigger_engine.lib import red_skeleton; ..."` | string Constant, not an import | PASS | yes |
| `import red_skeleton` inside a function body (the §1q deferred style this file uses) | only if the scan uses `ast.walk` | FAIL | yes only if `ast.walk` is used. A3 does not test this (MINOR-1) |
| `importlib.import_module("red_skeleton")`, `spec_from_file_location`, `importlib.reload` | not static | PASS | out of reach for a static scan. Accepted, recorded as an edge |

The current tree has zero real offenders. The only regex hit, `test_gh612_phase_sentinel_seam.py:340`, is docstring prose. No `.py` fixtures and no build/venv dirs sit under either scan root, so there are no false positives today.

## Step 2: change-vs-acceptance cross-check
- Change 1 (AC31) maps to A1 and A2. Change 2 (AC32 delete) maps to A1 and A5. Change 3 (AC32b) maps to A1, A3 and A4. Change 5 (skip reasons) maps to A5.
- Change 4 (the `_red_skeleton` docstring) has no acceptance item (MINOR-2).
- No acceptance item lacks a producing change.

## Step 3: RED / negative-leg adequacy
- This is a test-only change, so the negative legs stand in for RED.
- A2 (AC31) is sound. `"0"*32` passes the 32-hex shape check, so the test fails at `observed[0] != observed[1]` and not at import. Changing the size of the source invalidates the pyc.
- A3 (AC32b) is decoy-shaped. Its probe goes under `bytedigger_engine/lib/`, a root that `test_bd44_package_namespace.py::test_ac11_installed_modules_do_not_import_siblings_by_bare_name` already covers more strictly. That test does an ast.walk over the installed package, its sibling set includes `lib` and `red_skeleton`, and it checks both Import and ImportFrom with first dotted component. AC32b's only non-redundant surface is `tests/`, and neither A3 nor A4 exercises it (MAJOR-1).
- No stub-passability: neither AC mocks its UUT. `ast` is already imported at L51, so there is no collect-time risk.

## Step 4: reachability (§1y)
- AC31: Point `red_skeleton.py:50` `PROCESS_TOKEN = uuid.uuid4().hex`, Host the module body (import time), Test-path two child interpreters plus the in-process `_red_skeleton()`. Reachable.
- AC32b, `bytedigger_engine/` root: Point is any import node, Host the AST scan, Test-path A3 probe. Reachable, but redundant with bd44 AC11.
- AC32b, `tests/` root: Point is any import node in tests, Host the AST scan, Test-path none. The tests root is never proven to be scanned (MAJOR-1).

## Probes requested
- **(a) AC31, wheel vs source tree.** Both sides resolve the source tree.
  - Parent: `conftest.py:46-49` inserts `engine_py` at `sys.path[0]`, which shadows the CI-installed wheel.
  - Children: `-c` with `cwd=ENGINE_ROOT` puts `''` (that is, engine_py) first, and the `PYTHONPATH` prepend sits ahead of site-packages even under PYTHONSAFEPATH.
  - So the A2 source mutation is observed by the children and AC31 fails for the right reason.
  - The leftover `build/` is not on the child's path ahead of PYTHONPATH. The host-tool hook only converts FileNotFoundError for git, bun or semgrep, so it cannot mask AC31.
  - Advisory: also print and assert `red_skeleton.__file__` under ENGINE_ROOT (MINOR-6).
- **(b) AC32b vacuity and ban list.** Relative imports are correctly legitimate (same instance). The ban list misses `import lib.red_skeleton` (MAJOR-2), and the tests root is unproven (MAJOR-1). The package root duplicates bd44 AC11 and contradicts the file's own "no AST of production" invariant (MINOR-3). There are no false positives on legitimate package imports.
- **(c) Deleting AC32.** As written it is a tautology: `_stamp_for` and `prod` both read `p5.red_skeleton`, and `build_stamp` is deterministic. So deleting that body is justified. A non-tautological phase B subject does remain: `_prod_red_skeleton() is _red_skeleton()`, which checks that the object the unit ACs AC1-8/AC28/AC31 test is the object production stamps with. It would catch a vendored copy, a shim object, or a `spec_from_file_location` load. These are things AC32b cannot see. The skip reason itself says "do not delete". Advisory (MINOR-4).
- **(d) A1 counts are correct.**
  - Baseline: 36 `def test_` in the file, 24 `@pytest.mark.skip` (L609...L1978), no parametrize, no `pytestmark`, no in-body `pytest.skip`/`importorskip`.
  - The 12 unskipped tests are AC1-AC8, S2, AC10, AC18 and AC28.
  - After the change: 36 collected, 22 skipped, 14 passed.

## Adversarial edges (not covered by the spec's acceptance items)
1. **Decoy fence / wrong tests root.** If the `tests/` root is typo'd or empty, A1-A4 stay green, because the anti-vacuum only checks `bytedigger_engine/lib/red_skeleton.py`.
2. **Equivalent spelling `import lib.red_skeleton`.** It bypasses AC32b inside `tests/`, where bd44 AC11 does not look.
3. **Function-scoped bare import** (the exact §1q deferred pattern of this file). A top-level-only scanner still passes A3.
4. **Unparseable or non-UTF-8 `.py` under a scan root.** Behaviour is unspecified (crash vs offender). bd44 AC11 records it as an offender. Use `ast.parse(read_bytes())`.
5. **A3 run during a full-suite or parallel run.** A probe file under `bytedigger_engine/lib/` also reddens bd44/manifest/path-closure tests. Run A3 scoped to the target file only.
6. **Dynamic second instance** (`importlib.import_module`, `spec_from_file_location`, `reload`). A static scan cannot see it. Only the phase B identity check from (c) would.

VERDICT: REJECTED

## Findings
1. **MAJOR-1: the AC32b negative leg and anti-vacuum skip its only non-redundant surface.**
   - Problem: the `bytedigger_engine/` root is already strictly covered by `test_bd44_package_namespace.py::test_ac11_*`, which runs unskipped in CI. AC32b adds value only on `tests/`, yet A3 probes `bytedigger_engine/lib/_probe_bare.py` and A4 asserts only that `red_skeleton.py` was scanned. A broken or empty `tests/` root would pass every acceptance item.
   - Fix:
     - Add an A3 variant with a probe under `tests/` (e.g. `tests/_probe_ac32b.py`, `import red_skeleton` inside a function body) that must FAIL by name.
     - Add an anti-vacuum assert that `Path(__file__).resolve()` is among the scanned files.
     - Preferably also scope AC32b to `tests/` and cite bd44 AC11 for the package in the AC32 removal comment. That also clears MINOR-3.
2. **MAJOR-2: the ban list has an equivalent-spelling hole.**
   - Problem: `import lib.red_skeleton` is not banned while `from lib import red_skeleton` is. Both produce `sys.modules["lib.red_skeleton"]`, the second instance the removal comment says AC32b pins.
   - Fix: ban any Import alias, or level-0 ImportFrom module, whose first dotted component is `red_skeleton` or `lib`. This mirrors bd44 AC11. Add `import lib.red_skeleton` to A3.
3. **MINOR-1:** State `ast.walk` (all nesting levels) explicitly. Put at least one A3 probe import inside a `def`, because this file's own imports are all deferred into function bodies.
4. **MINOR-2:**
   - Change 4: say the whole `_red_skeleton` docstring is replaced. L119-133 carry the false `sys.modules["lib.X"]` claim and a dangling "AC32 pins the identity" (L133).
   - Add to A5: `grep -nE "AC32([^b]|$)"` must hit only the removal comment and the §9d header (consider retitling L1918 to "AC31, AC32b").
5. **MINOR-3:** AC32b scanning production AST contradicts the module docstring at L44-45. Either amend that sentence or scope AC32b to `tests/` (see MAJOR-1).
6. **MINOR-4:** AC32 deletion is acceptable as written (tautology), but a real phase B subject remains: `_prod_red_skeleton() is _red_skeleton()`. Either replace AC32 with that one-line phase B-skipped check (honouring the "do not delete" in the skip reason), or record it as a named phase B obligation in #1551.
7. **MINOR-5:** The "30 phase B wiring tests" count does not match the tree: 33 before and 31 after (24+9 → 22+9). Correct it or cite its source.
8. **MINOR-6:** AC31 child: also print `red_skeleton.__file__` and assert it is under `ENGINE_ROOT/bytedigger_engine`. This turns the source-vs-wheel resolution into an asserted precondition instead of an environment assumption.
9. **MINOR-7:** Specify SyntaxError/encoding handling (record as an offender, like bd44 AC11). The failure message must distinguish "flat `lib` spelling" from "bare red_skeleton". Run A3 scoped to the target file only (edge 5).
