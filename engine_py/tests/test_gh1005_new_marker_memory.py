"""RED tests for GH1005 — spec_cite symbol-level new-marker memory (kills
self-introduced-symbol FP).

Spec: SHARED/memory/Decisions/2026-07-18_6E8A14A3_gh1005_spec_cite_new_marker_memory_spec.md
§3 AC1-AC10 (AC11 is the orchestrator's regression net, out of scope here).

conftest.py (engine_py/tests/conftest.py) already inserts engine_py/ onto
sys.path at collection time, so plain top-level `import spec_cite` /
`from spec_cite import lint_spec` are safe (§1q — spec_cite.py already
exists today). The NEW symbols under test (`new_marked_symbols`,
`_iter_scannable_lines`) do NOT exist yet, so they are imported LAZILY
inside each test function body (never top-level) — the file COLLECTS
cleanly and FAILS at assert/import time, never at collection time
(§1q / D1CF5FDF).

Expected pre-GREEN result:
  - AC1, AC2, AC5, AC6, AC9 (first half), AC10 FAIL — the current
    lint_spec has no new-marker-memory downgrade disjunct, so these
    exit 1 / 'unresolved_symbol' where the spec expects exit 0 /
    'new_symbol'.
  - AC3 is a true-positive regression pin (already exit 1 today, and
    expected to stay so post-GREEN) — expected to PASS already.
  - AC4 is a regression pin over EXISTING fence-skip behavior — expected
    to PASS already (marker-in-fence must not leak either before or
    after GREEN).
  - AC7 FAILS at collection-deferred import time — `new_marked_symbols`
    does not exist yet.
  - AC8 (re-entry) FAILS today for the same reason as AC1 (both calls
    return exit 1 today; spec expects both calls to return exit 0
    identically post-GREEN).
"""
from __future__ import annotations

from pathlib import Path

from spec_cite import lint_spec

_EXISTING_FN_SRC = "export function existingFn() {}\n"


def _make_root(tmp_path: Path) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "target.ts").write_text(_EXISTING_FN_SRC)
    return tmp_path


# ── AC1: marker above citation (signature form, no file-token) ─────────────


def test_ac1_marker_above_citation_downgrades_to_new_symbol(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "- NEW `_myGuardHelper(opts: { candidatePath: string; cwd: string }): boolean` "
        "— exported from `target.ts`. Pure predicate.\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 0, f"AC1: expected exit_code=0, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "new_symbol", (
        f"AC1: expected '_myGuardHelper' finding status='new_symbol', got "
        f"{statuses.get('_myGuardHelper')!r} — full findings: {findings}"
    )


# ── AC2: marker below citation — symbol-level memory, not line-order ───────


def test_ac2_marker_below_citation_still_downgrades(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
        "- NEW `_myGuardHelper(opts: { candidatePath: string; cwd: string }): boolean` "
        "— exported from `target.ts`. Pure predicate.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 0, f"AC2: expected exit_code=0, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "new_symbol", (
        f"AC2: expected '_myGuardHelper' finding status='new_symbol' regardless of "
        f"marker-below-citation line order, got {statuses.get('_myGuardHelper')!r} "
        f"— full findings: {findings}"
    )


# ── AC3: no marker anywhere — true-positive preserved ───────────────────────


def test_ac3_no_marker_stays_unresolved_symbol(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 1, f"AC3: expected exit_code=1, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "unresolved_symbol", (
        f"AC3: expected '_myGuardHelper' to stay 'unresolved_symbol' with no marker "
        f"anywhere in the spec, got {statuses.get('_myGuardHelper')!r} — full "
        f"findings: {findings}"
    )


# ── AC4: marker inside a non-python fence must NOT leak (fence-skip) ───────


def test_ac4_marker_inside_fence_does_not_leak(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "```json\n"
        "- NEW `_myGuardHelper(opts: { candidatePath: string; cwd: string }): boolean`\n"
        "```\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 1, (
        f"AC4: expected exit_code=1 (fence-skipped marker must not register), "
        f"got {exit_code}; findings={findings}"
    )
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "unresolved_symbol", (
        f"AC4: expected '_myGuardHelper' to stay 'unresolved_symbol' (fenced marker "
        f"must not leak into memory), got {statuses.get('_myGuardHelper')!r} — full "
        f"findings: {findings}"
    )


# ── AC5: bare-form marker (no file, no signature) ───────────────────────────


def test_ac5_bare_form_marker_downgrades(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "This ship adds NEW `_myGuardHelper`, a pure predicate.\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 0, f"AC5: expected exit_code=0, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "new_symbol", (
        f"AC5: expected '_myGuardHelper' finding status='new_symbol' via bare-form "
        f"marker, got {statuses.get('_myGuardHelper')!r} — full findings: {findings}"
    )


# ── AC6: trailing-() normalization on both sides ────────────────────────────


def test_ac6_trailing_parens_normalization_both_sides(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "- NEW `_myGuardHelper(opts: X): boolean` — exported from `target.ts`.\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper()` before remove "
        "in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 0, f"AC6: expected exit_code=0, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper()") == "new_symbol" or statuses.get(
        "_myGuardHelper"
    ) == "new_symbol", (
        f"AC6: expected the '_myGuardHelper()' citation finding to be 'new_symbol' "
        f"after trailing-() normalization on both marker and citation, got "
        f"statuses={statuses} — full findings: {findings}"
    )


# ── AC7: new_marked_symbols direct helper assertion (§1aa declare-f analog) ─


def test_ac7_new_marked_symbols_extracts_bare_and_signature_forms(tmp_path: Path) -> None:
    from spec_cite import new_marked_symbols, _iter_scannable_lines  # noqa: PLC0415

    assert callable(new_marked_symbols), "AC7: new_marked_symbols must be callable"
    assert callable(_iter_scannable_lines), "AC7: _iter_scannable_lines must be callable"

    result = new_marked_symbols("adds NEW `foo(a: int): str` and `bar`")
    assert result == {"foo", "bar"}, (
        f"AC7: expected new_marked_symbols(...) == {{'foo', 'bar'}}, got {result!r}"
    )


# ── AC8: re-entry — two consecutive calls in one process are identical ─────


def test_ac8_lint_spec_reentry_identical_across_two_calls(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "- NEW `_myGuardHelper(opts: { candidatePath: string; cwd: string }): boolean` "
        "— exported from `target.ts`. Pure predicate.\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
    )
    exit_code_1, findings_1 = lint_spec(spec_path, root)
    exit_code_2, findings_2 = lint_spec(spec_path, root)

    assert exit_code_1 == 0, (
        f"AC8: expected first call exit_code=0, got {exit_code_1}; findings={findings_1}"
    )
    assert exit_code_1 == exit_code_2, (
        f"AC8: expected identical exit codes across two consecutive lint_spec calls, "
        f"got {exit_code_1} then {exit_code_2}"
    )
    statuses_1 = {(f.file, f.symbol): f.status for f in findings_1}
    statuses_2 = {(f.file, f.symbol): f.status for f in findings_2}
    assert statuses_1 == statuses_2, (
        f"AC8: expected identical finding statuses across two consecutive lint_spec "
        f"calls, got {statuses_1} then {statuses_2}"
    )


# ── AC9: two symbols cited, marker for only the first ───────────────────────


def test_ac9_only_marked_symbol_downgrades_other_stays_unresolved(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "- NEW `_myGuardHelper(opts: X): boolean` — exported from `target.ts`.\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` and "
        "`_otherHelper` before remove in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 1, f"AC9: expected exit_code=1, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "new_symbol", (
        f"AC9: expected '_myGuardHelper' status='new_symbol' (marked), got "
        f"{statuses.get('_myGuardHelper')!r} — full findings: {findings}"
    )
    assert statuses.get("_otherHelper") == "unresolved_symbol", (
        f"AC9: expected '_otherHelper' status='unresolved_symbol' (unmarked), got "
        f"{statuses.get('_otherHelper')!r} — full findings: {findings}"
    )


# ── AC10: wrong_file interaction — symbol exists in a DIFFERENT repo file ──


def test_ac10_marker_downgrades_wrong_file_branch_too(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    other_dir = root / "other"
    other_dir.mkdir()
    (other_dir / "place.ts").write_text(
        "export function _myGuardHelper(opts) { return true; }\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "- NEW `_myGuardHelper(opts: { candidatePath: string; cwd: string }): boolean` "
        "— exported from `target.ts`. Pure predicate.\n"
        "7. **AC7 — guard.** Cleanup routes through `_myGuardHelper` before remove "
        "in lib/target.ts.\n"
    )
    exit_code, findings = lint_spec(spec_path, root)
    assert exit_code == 0, f"AC10: expected exit_code=0, got {exit_code}; findings={findings}"
    statuses = {f.symbol: f.status for f in findings}
    assert statuses.get("_myGuardHelper") == "new_symbol", (
        f"AC10: expected '_myGuardHelper' status='new_symbol' even though the symbol "
        f"resolves elsewhere in the repo (wrong_file branch must also be covered by "
        f"the new-marker disjunct), got {statuses.get('_myGuardHelper')!r} — full "
        f"findings: {findings}"
    )
