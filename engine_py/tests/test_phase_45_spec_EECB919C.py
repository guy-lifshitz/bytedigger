"""RED tests for EECB919C — wire scope-inverse (§1v) + spec-coverage (§1w) lints
into phase_45_spec_workflow().

AC1–AC12 each map to one test.  All tests FAIL pre-GREEN because:
  - _verify_spec_scope_inverse does not exist in phase_45_spec.py
  - _verify_spec_coverage does not exist in phase_45_spec.py
  - phase_45_spec_workflow().steps has no "verify_spec_scope_inverse" or
    "verify_spec_coverage" entries

Deferred-import discipline (§1q-extension D1CF5FDF): the two new gate functions
are imported INSIDE each test function body so the file COLLECTS cleanly and
fails at assert/call time rather than hanging the red_runtime with an ImportError
at collection.  `phase_45_spec_workflow` (which already exists) is also imported
inside each body for uniformity.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Engine_py root on sys.path — mirrors test_verify_spec_completeness.py pattern.
# Must be module-level (not inside functions) to reach contracts/phase_45_spec.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── fixture text constants ────────────────────────────────────────────────────
#
# scope_inverse lib: fires when ^#+.*files in scope (case-insensitive) exists but
#   "not in scope" does NOT appear anywhere.
#
# spec_coverage lib: fires when a §2 bold op (bullet+bold in a §2 heading section)
#   is absent from the AC table (first table with "AC" in header row).

_SCOPE_VIOLATING = """\
# Test Spec

## §2 Design
- **frobnicate** — does the thing

## Files in scope
- SYSTEM/cli/build/engine_py/foo.py

## §3 Acceptance Criteria

| AC | Description |
|---|---|
| AC1 | frobnicate works |
"""

_SCOPE_CLEAN = """\
# Test Spec

## §2 Design
- **frobnicate** — does the thing

## Files in scope
- SYSTEM/cli/build/engine_py/foo.py

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

## §3 Acceptance Criteria

| AC | Description |
|---|---|
| AC1 | frobnicate works |
"""

# §2 section heading must match (?i)§?\s*2\b so we use "## §2 Design".
# The bold op "frobnicate" (first ≤6 words) must NOT appear in the AC table.
_COVERAGE_VIOLATING = """\
# Test Spec

## §2 Design

- **frobnicate** — does the special thing

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

## §3 Acceptance Criteria

| AC | op | Description |
|---|---|---|
| AC1 | other_op | something unrelated |
"""

# Every §2 bold op appears in the AC table.
_COVERAGE_CLEAN = """\
# Test Spec

## §2 Design

- **frobnicate** — does the special thing

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

## §3 Acceptance Criteria

| AC | op | Description |
|---|---|---|
| AC1 | frobnicate | covers the op |
"""

# No AC table at all — scan_spec_coverage returns [] → gate must pass (AC7).
_NO_AC_TABLE = """\
# Test Spec

## §2 Design

- **frobnicate** — does the special thing

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

No table here, just prose about frobnicate.
"""


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": "SIMPLE"},
        question="wire spec lints",
        session_id="test-EECB919C",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(spec_path: Path, cycle: int = 1, **extra) -> StepResult:
    data: dict = {"spec_path": str(spec_path), "cycle": cycle, **extra}
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="prev",
    )


def _write(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


# ─── AC1 — verify_spec_scope_inverse registered after verify_spec_lint ────────


def test_ac1_verify_spec_scope_inverse_in_step_list_after_lint() -> None:
    """AC1: phase_45_spec_workflow().steps has 'verify_spec_scope_inverse',
    index > index of 'verify_spec_lint' and < index of 'build_review_prompt'.
    FAILS pre-GREEN: step absent from workflow list."""
    from bytedigger_engine.workflows.phase_45_spec import phase_45_spec_workflow  # deferred — uniformity

    names = [s.name for s in phase_45_spec_workflow().steps]

    assert "verify_spec_scope_inverse" in names, (
        f"'verify_spec_scope_inverse' missing from step list; got: {names}"
    )
    assert "verify_spec_lint" in names, f"'verify_spec_lint' missing: {names}"
    assert "build_review_prompt" in names, f"'build_review_prompt' missing: {names}"

    i_lint = names.index("verify_spec_lint")
    i_scope = names.index("verify_spec_scope_inverse")
    i_review = names.index("build_review_prompt")

    assert i_scope > i_lint, (
        f"verify_spec_scope_inverse ({i_scope}) must come after verify_spec_lint "
        f"({i_lint})"
    )
    assert i_scope < i_review, (
        f"verify_spec_scope_inverse ({i_scope}) must come before build_review_prompt "
        f"({i_review})"
    )


# ─── AC2 — verify_spec_coverage registered after verify_spec_scope_inverse ────


def test_ac2_verify_spec_coverage_in_step_list_after_scope_inverse() -> None:
    """AC2: phase_45_spec_workflow().steps has 'verify_spec_coverage',
    index > 'verify_spec_scope_inverse' and < 'build_review_prompt'.
    FAILS pre-GREEN: step absent."""
    from bytedigger_engine.workflows.phase_45_spec import phase_45_spec_workflow  # deferred

    names = [s.name for s in phase_45_spec_workflow().steps]

    assert "verify_spec_coverage" in names, (
        f"'verify_spec_coverage' missing from step list; got: {names}"
    )
    assert "verify_spec_scope_inverse" in names, (
        f"'verify_spec_scope_inverse' missing (needed for ordering check): {names}"
    )
    assert "build_review_prompt" in names, f"'build_review_prompt' missing: {names}"

    i_scope = names.index("verify_spec_scope_inverse")
    i_cov = names.index("verify_spec_coverage")
    i_review = names.index("build_review_prompt")

    assert i_cov > i_scope, (
        f"verify_spec_coverage ({i_cov}) must come after verify_spec_scope_inverse "
        f"({i_scope})"
    )
    assert i_cov < i_review, (
        f"verify_spec_coverage ({i_cov}) must come before build_review_prompt "
        f"({i_review})"
    )


# ─── AC3 — scope-VIOLATING spec → error / E_SPEC_SCOPE_INVERSE / recoverable ──


def test_ac3_scope_violating_spec_returns_error(tmp_path) -> None:
    """AC3: _verify_spec_scope_inverse on a scope-violating spec (cycle=1)
    → status='error', error_code='E_SPEC_SCOPE_INVERSE', recoverable=True.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_scope_inverse  # deferred

    f = _write(tmp_path, "spec_violating.md", _SCOPE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_scope_inverse(ctx, prev)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_SCOPE_INVERSE", (
        f"expected 'E_SPEC_SCOPE_INVERSE', got {r.error_code!r}"
    )
    assert r.recoverable is True, "cycle=1 must be recoverable"


# ─── AC4 — scope-CLEAN spec → ok ──────────────────────────────────────────────


def test_ac4_scope_clean_spec_returns_ok(tmp_path) -> None:
    """AC4: _verify_spec_scope_inverse on a spec with both 'Files in scope' header
    AND 'Files NOT in scope' line → status='ok'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_scope_inverse  # deferred

    f = _write(tmp_path, "spec_clean.md", _SCOPE_CLEAN)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_scope_inverse(ctx, prev)

    assert r.status == "ok", f"expected 'ok', got {r.status!r} (error_code={r.error_code!r})"


# ─── AC5 — coverage-VIOLATING spec → error / E_SPEC_COVERAGE / recoverable ───


def test_ac5_coverage_violating_spec_returns_error(tmp_path) -> None:
    """AC5: _verify_spec_coverage on a spec with a bold §2 op absent from AC
    table (cycle=1) → status='error', error_code='E_SPEC_COVERAGE', recoverable=True.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_coverage  # deferred

    f = _write(tmp_path, "spec_cov_viol.md", _COVERAGE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_coverage(ctx, prev)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_COVERAGE", (
        f"expected 'E_SPEC_COVERAGE', got {r.error_code!r}"
    )
    assert r.recoverable is True, "cycle=1 must be recoverable"


# ─── AC6 — coverage-CLEAN spec → ok ──────────────────────────────────────────


def test_ac6_coverage_clean_spec_returns_ok(tmp_path) -> None:
    """AC6: _verify_spec_coverage on a spec where every §2 bold op appears in
    the AC table → status='ok'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_coverage  # deferred

    f = _write(tmp_path, "spec_cov_clean.md", _COVERAGE_CLEAN)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_coverage(ctx, prev)

    assert r.status == "ok", f"expected 'ok', got {r.status!r} (error_code={r.error_code!r})"


# ─── AC7 — no-AC-table spec → ok (graceful) ───────────────────────────────────


def test_ac7_no_ac_table_returns_ok(tmp_path) -> None:
    """AC7: _verify_spec_coverage on a spec with NO markdown AC table
    → status='ok' (scan_spec_coverage returns [] → no findings).
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_coverage  # deferred

    f = _write(tmp_path, "spec_no_table.md", _NO_AC_TABLE)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_coverage(ctx, prev)

    assert r.status == "ok", (
        f"spec with no AC table must pass (graceful), got {r.status!r} "
        f"(error_code={r.error_code!r})"
    )


# ─── AC8 — is_frozen=True → both gates return skip ───────────────────────────


def test_ac8_frozen_spec_both_gates_skip(tmp_path) -> None:
    """AC8: with prev.data['is_frozen']=True, BOTH _verify_spec_scope_inverse and
    _verify_spec_coverage return status='skip'.
    FAILS pre-GREEN: functions do not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_scope_inverse, _verify_spec_coverage  # deferred

    # Use the violating fixture so these would error if the frozen fast-path is absent.
    f = _write(tmp_path, "spec_frozen.md", _SCOPE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1, is_frozen=True)

    r_scope = _verify_spec_scope_inverse(ctx, prev)
    assert r_scope.status == "skip", (
        f"frozen spec: _verify_spec_scope_inverse must return 'skip', "
        f"got {r_scope.status!r}"
    )

    r_cov = _verify_spec_coverage(ctx, prev)
    assert r_cov.status == "skip", (
        f"frozen spec: _verify_spec_coverage must return 'skip', "
        f"got {r_cov.status!r}"
    )


# ─── AC9 — HAL_SPEC_SCOPE_GATE=0 → skip (env kill-switch) ────────────────────


def test_ac9_env_kill_switch_scope_gate(tmp_path, monkeypatch) -> None:
    """AC9: with HAL_SPEC_SCOPE_GATE='0', _verify_spec_scope_inverse on a
    scope-VIOLATING spec → status='ok', data['spec_scope_inverse_skipped']=='env_skip'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_scope_inverse  # deferred

    monkeypatch.setenv("HAL_SPEC_SCOPE_GATE", "0")

    f = _write(tmp_path, "spec_viol_skip.md", _SCOPE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_scope_inverse(ctx, prev)

    assert r.status == "ok", (
        f"env kill-switch: expected 'ok', got {r.status!r}"
    )
    assert (r.data or {}).get("spec_scope_inverse_skipped") == "env_skip", (
        f"expected data['spec_scope_inverse_skipped']=='env_skip', "
        f"got {(r.data or {}).get('spec_scope_inverse_skipped')!r}"
    )


# ─── AC10 — HAL_SPEC_COVERAGE_GATE=0 → skip (env kill-switch) ────────────────


def test_ac10_env_kill_switch_coverage_gate(tmp_path, monkeypatch) -> None:
    """AC10: with HAL_SPEC_COVERAGE_GATE='0', _verify_spec_coverage on a
    coverage-VIOLATING spec → status='ok'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_coverage  # deferred

    monkeypatch.setenv("HAL_SPEC_COVERAGE_GATE", "0")

    f = _write(tmp_path, "spec_cov_skip.md", _COVERAGE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_coverage(ctx, prev)

    assert r.status == "ok", (
        f"env kill-switch: expected 'ok', got {r.status!r}"
    )


# ─── AC11 — cycle=2 (cap reached) → fatal / not recoverable ──────────────────


def test_ac11_scope_violating_cycle2_returns_fatal(tmp_path) -> None:
    """AC11: _verify_spec_scope_inverse on scope-violating spec with cycle=2
    → status='error', error_code='E_SPEC_SCOPE_INVERSE_FATAL', recoverable=False.
    FAILS pre-GREEN: function does not exist.

    GH625 §2.5: cap decision now keyed on per-gate gate_attempts, not raw
    cycle; spec_retry cap is now 1 (recoverable_once). Seed 1 prior attempt
    (== cap) — prev.data is spread into forwarded_data at
    phase_45_spec.py:1900 `forwarded_data={**prev.data, ...}`, confirmed."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_scope_inverse  # deferred

    f = _write(tmp_path, "spec_fatal.md", _SCOPE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=2, gate_attempts={"spec_retry": 1})

    r = _verify_spec_scope_inverse(ctx, prev)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_SCOPE_INVERSE_FATAL", (
        f"expected 'E_SPEC_SCOPE_INVERSE_FATAL', got {r.error_code!r}"
    )
    assert r.recoverable is False, (
        "cycle=2 cap-reached must NOT be recoverable"
    )


# ─── AC12 — findings is str, findings_structured is list ──────────────────────


def test_ac12_findings_str_and_findings_structured_list(tmp_path) -> None:
    """AC12: on a scope violation (cycle=1), result.data['findings'] is a str
    (not a list) AND result.data['findings_structured'] is a list.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_scope_inverse  # deferred

    f = _write(tmp_path, "spec_findings.md", _SCOPE_VIOLATING)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_scope_inverse(ctx, prev)

    assert r.status == "error", (
        f"expected error (scope violation), got {r.status!r}"
    )
    data = r.data or {}
    assert isinstance(data.get("findings"), str), (
        f"data['findings'] must be a str (rendered), got {type(data.get('findings'))!r}"
    )
    assert isinstance(data.get("findings_structured"), list), (
        f"data['findings_structured'] must be a list, "
        f"got {type(data.get('findings_structured'))!r}"
    )
