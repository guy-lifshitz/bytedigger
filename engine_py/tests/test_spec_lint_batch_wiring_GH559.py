"""RED tests for GH559 — wire GH540 spec-lint scanners (token_consistency,
presence_triad, format_conversion_cases) into phase_45_spec.py as a single
new warn-only step `verify_spec_lint_batch`.

AC1-AC12 each map to one test. All FAIL pre-GREEN because:
  - _verify_spec_lint_batch does not exist in phase_45_spec.py
  - phase_45_spec_workflow().steps has no "verify_spec_lint_batch" entry
  - flags_catalog.FLAGS lacks HAL_SPEC_LINT_BATCH_GATE / _ENFORCE

Deferred-import discipline (§1q-extension D1CF5FDF): new symbols are imported
INSIDE each test function body so the file COLLECTS cleanly and fails at
assert/call time rather than hanging red_runtime with an ImportError at
collection.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Engine_py root on sys.path — mirrors test_phase_45_spec_EECB919C.py pattern.
# Must be module-level (not inside functions) to reach contracts/phase_45_spec.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── fixture text constants ────────────────────────────────────────────────

# Clean spec: no token drift, no side-effect AC missing the presence triad,
# no format-conversion trigger phrase.
_CLEAN_SPEC = """\
# Test Spec

## §2 Design
- **frobnicate** — does the thing

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | returns integer count |
"""

# Token-drift spec: `emit_foo_bar` vs `emit.foo.bar` — trips token_consistency.
_TOKEN_DRIFT_SPEC = """\
# Test Spec

## §2 Design
- **frobnicate** — does the thing

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | emits event `emit_foo_bar` and `emit.foo.bar` |
"""

# Side-effect AC line missing the §1y presence triad entirely.
_PRESENCE_TRIAD_MISSING_SPEC = """\
# Test Spec

## §2 Design
- **frobnicate** — does the thing

## Files NOT in scope
- SYSTEM/cli/build/engine_py/bar.py

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | emits emit_build_done event |

## §4 Next
"""

# format-conversion trigger phrase present, but no §2.3 writer-behavior section.
_FORMAT_CONVERSION_MISSING_SECTION_SPEC = """\
# Test Spec

This spec covers a format-conversion writer.

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | returns integer count |
"""


# ─── helpers ────────────────────────────────────────────────────────────────


def _make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": "SIMPLE"},
        question="wire GH540 spec lints",
        session_id="test-GH559",
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


# ─── AC1 — step registered between verify_spec_coverage and build_review_prompt


def test_ac1_verify_spec_lint_batch_in_step_list_between_coverage_and_review() -> None:
    """AC1: phase_45_spec_workflow().steps has 'verify_spec_lint_batch',
    index > 'verify_spec_coverage' and < 'build_review_prompt'.
    FAILS pre-GREEN: step absent from workflow list."""
    from bytedigger_engine.workflows.phase_45_spec import phase_45_spec_workflow  # deferred

    names = [s.name for s in phase_45_spec_workflow().steps]

    assert "verify_spec_lint_batch" in names, (
        f"'verify_spec_lint_batch' missing from step list; got: {names}"
    )
    assert "verify_spec_coverage" in names, f"'verify_spec_coverage' missing: {names}"
    assert "build_review_prompt" in names, f"'build_review_prompt' missing: {names}"

    i_cov = names.index("verify_spec_coverage")
    i_batch = names.index("verify_spec_lint_batch")
    i_review = names.index("build_review_prompt")

    assert i_batch > i_cov, (
        f"verify_spec_lint_batch ({i_batch}) must come after verify_spec_coverage "
        f"({i_cov})"
    )
    assert i_batch < i_review, (
        f"verify_spec_lint_batch ({i_batch}) must come before build_review_prompt "
        f"({i_review})"
    )


# ─── AC2 — clean spec → ok, empty findings ─────────────────────────────────


def test_ac2_clean_spec_returns_ok_empty_findings(tmp_path) -> None:
    """AC2: on a clean spec, status='ok' and data['spec_lint_batch_findings']==[].
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    f = _write(tmp_path, "spec_clean.md", _CLEAN_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "ok", f"expected 'ok', got {r.status!r} (error_code={r.error_code!r})"
    assert (r.data or {}).get("spec_lint_batch_findings") == [], (
        f"expected empty findings, got {(r.data or {}).get('spec_lint_batch_findings')!r}"
    )


# ─── AC3 — token-drift spec, defaults → warn-only ok, findings non-empty ───


def test_ac3_token_drift_spec_warn_only_ok_with_findings(tmp_path) -> None:
    """AC3: token-drift spec, defaults (enforce off) → status='ok' (warn-only)
    AND findings non-empty AND some finding rule_id=='token-inconsistency'
    AND data['spec_lint_batch_warn_count']==len(findings).
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    f = _write(tmp_path, "spec_drift.md", _TOKEN_DRIFT_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r} (error_code={r.error_code!r})"
    data = r.data or {}
    findings = data.get("spec_lint_batch_findings")
    assert findings, f"expected non-empty findings, got {findings!r}"
    assert any(f_["rule_id"] == "token-inconsistency" for f_ in findings), (
        f"expected a token-inconsistency finding, got {findings!r}"
    )
    assert data.get("spec_lint_batch_warn_count") == len(findings), (
        f"expected warn_count == len(findings) ({len(findings)}), "
        f"got {data.get('spec_lint_batch_warn_count')!r}"
    )


# ─── AC4 — same drifting spec, ENFORCE=1 → error / E_SPEC_LINT_BATCH ──────


def test_ac4_token_drift_spec_enforce_on_returns_error(tmp_path, monkeypatch) -> None:
    """AC4: same drifting spec with HAL_SPEC_LINT_BATCH_ENFORCE=1 →
    status='error', error_code=='E_SPEC_LINT_BATCH'.
    FAILS pre-GREEN: function does not exist (and enforce path unimplemented)."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    monkeypatch.setenv("HAL_SPEC_LINT_BATCH_ENFORCE", "1")

    f = _write(tmp_path, "spec_drift_enforce.md", _TOKEN_DRIFT_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_LINT_BATCH", (
        f"expected 'E_SPEC_LINT_BATCH', got {r.error_code!r}"
    )


# ─── AC5 — HAL_SPEC_LINT_BATCH_GATE=0 → ok, skipped=env_skip ──────────────


def test_ac5_env_kill_switch_gate_off_skips(tmp_path, monkeypatch) -> None:
    """AC5: HAL_SPEC_LINT_BATCH_GATE=0 with drifting spec → status='ok' AND
    data['spec_lint_batch_skipped']=='env_skip'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    monkeypatch.setenv("HAL_SPEC_LINT_BATCH_GATE", "0")

    f = _write(tmp_path, "spec_drift_gate_off.md", _TOKEN_DRIFT_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "ok", f"expected 'ok', got {r.status!r}"
    assert (r.data or {}).get("spec_lint_batch_skipped") == "env_skip", (
        f"expected 'env_skip', got {(r.data or {}).get('spec_lint_batch_skipped')!r}"
    )


# ─── AC6 — is_frozen=True → skip ───────────────────────────────────────────


def test_ac6_frozen_spec_returns_skip(tmp_path) -> None:
    """AC6: prev.data['is_frozen']=True → status='skip'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    f = _write(tmp_path, "spec_frozen.md", _TOKEN_DRIFT_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1, is_frozen=True)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "skip", f"expected 'skip', got {r.status!r}"


# ─── AC7 — presence-triad-missing spec → warn-only ok with finding ────────


def test_ac7_presence_triad_missing_spec_warn_only_ok(tmp_path) -> None:
    """AC7: side-effect AC missing §1y presence triad → warn-only ok AND
    some finding rule_id=='presence-triad-missing'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    f = _write(tmp_path, "spec_presence_missing.md", _PRESENCE_TRIAD_MISSING_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r} (error_code={r.error_code!r})"
    findings = (r.data or {}).get("spec_lint_batch_findings") or []
    assert any(f_["rule_id"] == "presence-triad-missing" for f_ in findings), (
        f"expected a presence-triad-missing finding, got {findings!r}"
    )


# ─── AC8 — format-conversion trigger, no writer-behavior section ─────────


def test_ac8_format_conversion_missing_section_warn_only_ok(tmp_path) -> None:
    """AC8: format-conversion trigger phrase present, no §2.3 writer-behavior
    section → warn-only ok AND some finding rule_id startswith
    'format-conversion-'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    f = _write(
        tmp_path, "spec_format_missing.md", _FORMAT_CONVERSION_MISSING_SECTION_SPEC
    )
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r} (error_code={r.error_code!r})"
    findings = (r.data or {}).get("spec_lint_batch_findings") or []
    assert any(
        f_["rule_id"].startswith("format-conversion-") for f_ in findings
    ), f"expected a format-conversion-* finding, got {findings!r}"


# ─── AC9 — flags_catalog registration ──────────────────────────────────────


def test_ac9_flags_catalog_has_new_gate_and_enforce_flags() -> None:
    """AC9: flags_catalog.FLAGS contains HAL_SPEC_LINT_BATCH_GATE
    (kind='gate', default='1') and HAL_SPEC_LINT_BATCH_ENFORCE
    (kind='flag', default='0').
    FAILS pre-GREEN: keys absent."""
    from bytedigger_engine import flags_catalog  # deferred

    flags = flags_catalog.FLAGS

    assert "HAL_SPEC_LINT_BATCH_GATE" in flags, (
        f"HAL_SPEC_LINT_BATCH_GATE missing from FLAGS; got keys: "
        f"{sorted(flags.keys())[:5]}..."
    )
    gate_entry = flags["HAL_SPEC_LINT_BATCH_GATE"]
    assert gate_entry.get("kind") == "gate", f"expected kind='gate', got {gate_entry!r}"
    assert gate_entry.get("default") == "1", f"expected default='1', got {gate_entry!r}"

    assert "HAL_SPEC_LINT_BATCH_ENFORCE" in flags, (
        f"HAL_SPEC_LINT_BATCH_ENFORCE missing from FLAGS; got keys: "
        f"{sorted(flags.keys())[:5]}..."
    )
    enforce_entry = flags["HAL_SPEC_LINT_BATCH_ENFORCE"]
    assert enforce_entry.get("kind") == "flag", f"expected kind='flag', got {enforce_entry!r}"
    assert enforce_entry.get("default") == "0", f"expected default='0', got {enforce_entry!r}"


# ─── AC10 — prev=None → error / E_MISSING_PREV_DATA ────────────────────────


def test_ac10_prev_none_returns_error_missing_prev_data() -> None:
    """AC10: prev=None → status='error', error_code=='E_MISSING_PREV_DATA'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    ctx = _make_ctx()

    r = _verify_spec_lint_batch(ctx, None)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_MISSING_PREV_DATA", (
        f"expected 'E_MISSING_PREV_DATA', got {r.error_code!r}"
    )


# ─── AC11 — spec_path nonexistent → ok, skipped=spec_file_missing ────────


def test_ac11_missing_spec_file_returns_ok_skipped(tmp_path) -> None:
    """AC11: prev.data['spec_path'] points at a nonexistent file →
    status='ok' AND data['spec_lint_batch_skipped']=='spec_file_missing'.
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    missing = tmp_path / "does_not_exist_spec.md"
    ctx = _make_ctx()
    prev = _make_prev(missing, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    assert r.status == "ok", f"expected 'ok', got {r.status!r} (error_code={r.error_code!r})"
    assert (r.data or {}).get("spec_lint_batch_skipped") == "spec_file_missing", (
        f"expected 'spec_file_missing', got "
        f"{(r.data or {}).get('spec_lint_batch_skipped')!r}"
    )


# ─── AC12 — every finding carries a 'lint' key from the three GH540 names ──


def test_ac12_findings_carry_lint_key_from_known_names(tmp_path) -> None:
    """AC12: on the AC3 token-drift spec, every finding dict carries a
    'lint' key whose value is one of the three GH540 lint names
    (consumer-shape anchor).
    FAILS pre-GREEN: function does not exist."""
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint_batch  # deferred

    known_lint_names = {
        "token-consistency-lint",
        "presence-triad-lint",
        "format-conversion-lint",
    }

    f = _write(tmp_path, "spec_drift_lint_key.md", _TOKEN_DRIFT_SPEC)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = _verify_spec_lint_batch(ctx, prev)

    findings = (r.data or {}).get("spec_lint_batch_findings") or []
    assert findings, f"expected non-empty findings to check 'lint' key, got {findings!r}"
    for finding in findings:
        assert "lint" in finding, f"finding missing 'lint' key: {finding!r}"
        assert finding["lint"] in known_lint_names, (
            f"finding['lint']={finding['lint']!r} not in {known_lint_names}"
        )
