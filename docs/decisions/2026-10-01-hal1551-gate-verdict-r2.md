# GH1551 gate verdict r2: AC31/AC32 under the bytedigger package layout (delta re-audit)

Spec (r2, same path): `docs/decisions/2026-10-01-hal1551-ac31-ac32-package-layout-spec.md`
UUT: `engine_py/tests/test_gh1406_red_skeleton_provenance.py`
Prior verdict: `2026-10-01-hal1551-gate-verdict-r1.md` (REJECTED: MAJOR-1/2, MINOR-1..7)
Scope: only what changed in r2.

## r1 findings: disposition
| r1 | r2 change | Status |
|---|---|---|
| MAJOR-1: tests/ root unproven, package root redundant with bd44 AC11 | AC32b scoped to `tests/` only. Anti-vacuum: >= 20 files AND `Path(__file__).resolve()` scanned. A3 probe `tests/_probe_gh1551.py`, import inside a function body | Closed. The tree has 529 `.py` files under `tests/`, and the file scans itself. A wrong root now fails the self-present assert. |
| MAJOR-2: `import lib.red_skeleton` bypass | First-dotted-component ban on `red_skeleton` and `lib`, for both Import and level-0 ImportFrom. A3 runs all three spellings | Closed. Matches the bd44 AC11 rule. |
| MINOR-1 ast.walk | stated explicitly. A3 probe is function-scoped | Closed |
| MINOR-2 docstring scope | `_red_skeleton` docstring replaced in full. A5 `grep "AC32\b"` | Closed, but see residual MINOR-A |
| MINOR-3 production AST vs L44-45 invariant | tests-only scope | Closed: the file's tests tree is not production |
| MINOR-4 AC32c | added, skipped with the phase B reason: `_prod_red_skeleton() is _red_skeleton()` | Closed. Non-tautological: two independent resolution paths (`p5` attribute vs `bytedigger_engine.lib` import) |
| MINOR-5 count | 33 on base → 32 after | Verified: 24+9 = 33 now; 23+9 = 32 after |
| MINOR-6 `__file__` | children print `__file__`, asserted under `ENGINE_ROOT/bytedigger_engine/lib` | Closed |
| MINOR-7 unparseable / kind label / target-only | all specified | Closed |

## Step 1: consistency (delta)
- Names (`test_ac32b_no_flat_red_skeleton_import_in_tests`, `test_ac32c_test_helper_binds_production_red_skeleton_object`, probe `_probe_gh1551.py`) are each used consistently.
- The deletion comment's cross-references (bd44 AC11, AC32b, AC32c) all resolve to real or specified tests.
- `ast.Import` has no `level` field, so "at level == 0" is meaningful only for ImportFrom. Import is always absolute, so the rule is semantically right. Wording only.

## Step 1.5: classifier re-simulation (tests/ root, r2 rule)
- `import red_skeleton`, `import lib.red_skeleton`, `from lib import red_skeleton`, `from red_skeleton import X`, `from lib.red_skeleton import X`: all FAIL.
- `from bytedigger_engine.lib import red_skeleton` and `import bytedigger_engine.lib.red_skeleton`: PASS.
- Relative imports: PASS.
- String literals (the AC31 child command): PASS.
- Function-scoped imports are caught via `ast.walk`.
- Current tree: a level-0 `lib|red_skeleton` grep over `engine_py/tests` finds a single hit, `test_gh612_phase_sentinel_seam.py:340`. That line is docstring prose, so it is not a false positive.
- Unparseable files: the only non-collected `.py` files (`_bd24_*.py`, `_live_repo_sentinel.py`) are plain valid Python. No fixture `.py` files exist.

## Step 2: change-vs-acceptance
- Changes 1 through 6 map to A1-A6. Change 4 (AC32c) maps to A1 (collected/skipped counts).
- Change 5 (docstring) maps to A5.
- No orphan on either side.

## Step 3: negative legs
- **A2:** `"0"*32` passes the shape and `__file__` checks and fails at DIFFERENT. Sound.
- **A3:** three function-scoped spellings must fail, plus a legit-import probe that must pass (false-positive guard) and an unparseable probe. Sound, and it now exercises the AC's actual root.
- **A4:** an empty root fails both anti-vacuum asserts.
- **AC32c:** has no negative leg until phase B. Its "export a different object" mutation is named for that phase, which is acceptable for a skipped test.

## Step 4: reachability
- AC31: `red_skeleton.py:50` → module body → child import, with provenance now asserted via `__file__`.
- AC32b: tests-tree import nodes → AST scan → A3 probe under `tests/`.
- AC32c: deferred to phase B (skipped).

## A1 counts (re-derived)
| | Base | r2 | Delta |
|---|---|---|---|
| Collected | 36 | 37 | −AC32, +AC32b, +AC32c |
| Skipped | 24 | 23 | −AC31, −AC32, +AC32c |
| Passed | 12 | 14 | +AC31, +AC32b |

- 14 + 23 = 37. Correct.
- A6 skip delta of −1 is correct.

## Adversarial edges (not covered by r2 acceptance)
1. **Symlinked worktree path.** The `__file__` check must compare resolved paths: `Path(f).resolve().is_relative_to(ENGINE_ROOT.resolve() / "bytedigger_engine" / "lib")`. Python 3.9+ supports this, and `requires-python>=3.9`. A raw string-prefix compare would false-red under a symlinked checkout.
2. **PEP 263 non-UTF-8 coding cookie.** A legitimate latin-1 file would be flagged "unparseable" by a strict UTF-8 read. None exists today. `ast.parse(read_bytes())` honours cookies and still flags genuinely undecodable files.

VERDICT: APPROVED

## Findings (all MINOR, advisory, non-blocking)
1. **MINOR-A:** The §9d section header at L1918 (`# §9d — AC31-AC32, added in r4 ...`) survives and matches A5's `grep -n "AC32\b"`, but A5's allowed set doesn't list it. Either retitle it (e.g. `AC31, AC32b, AC32c`) or add it to A5's allowed hits. Also, `AC32\b` never matches `AC32b` or `AC32c`, because there is no word boundary between `2` and a letter. So A5's "and AC32b/AC32c references" clause is moot. The check still works, but the wording should be fixed. On macOS, prefer `grep -nE "AC32([^bc]|$)"` for portability.
2. **MINOR-B:** Implement the AC31 `__file__` assertion on resolved paths (edge 1).
3. **MINOR-C:** Prefer `ast.parse(path.read_bytes())` so coding cookies are honoured (edge 2). Undecodable or unparseable files still count as offenders.
4. **MINOR-D:** Wording: "at level == 0" applies to ImportFrom only. Implement as `isinstance(node, ast.ImportFrom) and node.level == 0`, and give Import nodes their own branch.
