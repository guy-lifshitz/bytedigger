"""RED tests for GH682 — port cite-grounding pre-lint (Layer 2) to
phase_45_spec_lite.

#680 (GH675A) added `_verify_spec_cite_prelint` to the FULL `phase_45_spec.py`
composite. This ship reuses that same step function (unchanged) and registers
it in the LITE review-cycle loop body (`build_review_loop_contract()`),
between `verify_spec_completeness` and `verify_spec_lint`.

`phase_45_spec_lite` and `build_review_loop_contract` already exist and are
imported at module top level. `_verify_spec_cite_prelint` is NOT yet imported
into `phase_45_spec_lite`'s namespace pre-GREEN, so it is looked up via
`getattr`/loop-body introspection INSIDE each test body — the file COLLECTS
cleanly regardless of GREEN state (§1q ext / D1CF5FDF).

conftest.py registers the engine_py root + workflows dir on `sys.path` at
import time (the singleton pattern) — no module-level `sys.path` mutation
here, no `from conftest import`.

Do NOT implement any production change here — RED-only file.
"""
from __future__ import annotations

from types import SimpleNamespace

import phase_45_spec
import phase_45_spec_lite
from contracts import StepResult


def _ctx(org_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(org_config=org_config or {})


def _find_prelint_step():
    """Look up the registered StepContract by name in the live loop body.

    Pre-GREEN: the step is absent -> returns None -> callers assert cleanly.
    Post-GREEN: returns the StepContract whose execute is the reused fn.
    """
    loop = phase_45_spec_lite.build_review_loop_contract()
    for step in loop.body:
        if step.name == "verify_spec_cite_prelint":
            return step
    return None


def _prelint_fn():
    step = _find_prelint_step()
    assert step is not None, (
        "verify_spec_cite_prelint step not yet registered in "
        "build_review_loop_contract() body (GREEN pending)"
    )
    return step.execute


def _dirty_fixture(tmp_path):
    """Repo root + spec file with one resolvable symbol and one bogus symbol."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    code_file = tmp_path / "helpers.py"
    code_file.write_text("def real_helper():\n    pass\n")
    spec_text = (
        "The function `real_helper` in `helpers.py` performs validation.\n"
        "The function `Zzz_nonexistent_symbol` in `helpers.py` performs the lookup.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)
    return spec_path, tmp_path


def _clean_fixture(tmp_path):
    """Repo root + spec file with zero unresolved citations."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    code_file = tmp_path / "helpers.py"
    code_file.write_text("def real_helper():\n    pass\n")
    spec_text = "The function `real_helper` in `helpers.py` performs validation.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)
    return spec_path, tmp_path


# ── AC1: symbol importable from the phase_45_spec_lite module namespace ────


def test_ac1_symbol_importable_in_lite_namespace():
    assert hasattr(phase_45_spec_lite, "_verify_spec_cite_prelint"), (
        "expected phase_45_spec_lite to import _verify_spec_cite_prelint "
        "(GH682 §2.1a import extension pending)"
    )


# ── AC2: loop body positions the step between completeness and lint ────────


def test_ac2_step_registered_between_completeness_and_lint():
    loop = phase_45_spec_lite.build_review_loop_contract()
    names = [s.name for s in loop.body]

    assert "verify_spec_cite_prelint" in names, (
        f"expected 'verify_spec_cite_prelint' registered in loop body, got: {names!r}"
    )
    idx_completeness = names.index("verify_spec_completeness")
    idx_new = names.index("verify_spec_cite_prelint")
    idx_lint = names.index("verify_spec_lint")

    assert idx_new == idx_completeness + 1, (
        f"expected verify_spec_cite_prelint immediately after "
        f"verify_spec_completeness({idx_completeness}); got index {idx_new}; names={names!r}"
    )
    assert idx_new < idx_lint, (
        f"expected verify_spec_cite_prelint({idx_new}) before verify_spec_lint({idx_lint}); "
        f"names={names!r}"
    )


# ── AC3: unresolved fixture emits result event with correct count/symbols ──


def test_ac3_emits_result_event_with_unresolved_count(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)), raising=False
    )

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="verify_spec_completeness")

    fn(ctx, prev)

    events = [p for et, p in captured if et == "spec_cite_prelint_result"]
    assert len(events) == 1, f"expected 1 spec_cite_prelint_result, got: {captured!r}"
    payload = events[0]
    assert payload.get("unresolved_count") == 1, (
        f"expected unresolved_count==1, got {payload.get('unresolved_count')!r}; payload={payload!r}"
    )
    assert "Zzz_nonexistent_symbol" in payload.get("symbols", []), (
        f"expected bogus symbol in symbols payload, got {payload.get('symbols')!r}"
    )


# ── AC4: also emits _warn, non-blocking status='ok', data key set ──────────


def test_ac4_emits_warn_nonblocking_ok_and_data_key(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)), raising=False
    )

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="verify_spec_completeness")

    result = fn(ctx, prev)

    warn_events = [p for et, p in captured if et == "spec_cite_prelint_warn"]
    assert len(warn_events) == 1, f"expected 1 spec_cite_prelint_warn, got: {captured!r}"
    assert result.status == "ok", f"expected non-blocking status='ok', got {result.status!r}"
    unresolved = result.data.get("spec_cite_prelint_unresolved")
    assert unresolved == ["Zzz_nonexistent_symbol"], (
        f"expected data['spec_cite_prelint_unresolved']==['Zzz_nonexistent_symbol'], got {unresolved!r}"
    )


# ── AC5: frozen fast-path — skip, no prelint events emitted ────────────────


def test_ac5_frozen_fast_path_skip_no_emit(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)), raising=False
    )

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec_path), "is_frozen": True},
        duration_ms=0,
        step_name="verify_spec_completeness",
    )

    result = fn(ctx, prev)

    assert result.status == "skip", f"expected status='skip' on frozen spec, got {result.status!r}"
    prelint_events = [et for et, p in captured if et in ("spec_cite_prelint_result", "spec_cite_prelint_warn")]
    assert prelint_events == [], (
        f"frozen fast-path must NOT emit prelint events; captured: {captured!r}"
    )


# ── AC6: kill-switch — status='ok', config_skip marker, no emit ────────────


def test_ac6_killswitch_skip_no_emit(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)), raising=False
    )

    ctx = _ctx({"hal_root": str(repo_root), "spec_cite_prelint_skip": True})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="verify_spec_completeness")

    result = fn(ctx, prev)

    assert result.status == "ok", f"expected status='ok' on kill-switch, got {result.status!r}"
    assert result.data.get("spec_cite_prelint_skipped") == "config_skip", (
        f"expected data['spec_cite_prelint_skipped']=='config_skip', got "
        f"{result.data.get('spec_cite_prelint_skipped')!r}"
    )
    assert captured == [], f"kill-switch must NOT lint/emit; captured events: {captured!r}"


# ── AC7: clean spec → clean=True, unresolved_count==0 ───────────────────────


def test_ac7_clean_spec_marks_clean_true(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _clean_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)), raising=False
    )

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="verify_spec_completeness")

    result = fn(ctx, prev)

    events = [p for et, p in captured if et == "spec_cite_prelint_result"]
    assert len(events) == 1, f"expected 1 spec_cite_prelint_result, got: {captured!r}"
    assert events[0].get("unresolved_count") == 0, (
        f"expected unresolved_count==0 for clean spec, got {events[0].get('unresolved_count')!r}"
    )
    assert result.status == "ok"
    assert result.data.get("spec_cite_prelint_clean") is True, (
        f"expected data['spec_cite_prelint_clean'] is True, got {result.data.get('spec_cite_prelint_clean')!r}"
    )


# ── AC8 (§1ab loop re-entry): double-call idempotent, both emit result ──────


def test_ac8_double_call_idempotent_lite_loop_reentry(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)), raising=False
    )

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="verify_spec_completeness")

    result1 = fn(ctx, prev)
    result2 = fn(ctx, prev)

    assert result1.status == result2.status, (
        f"expected identical status across re-entry, got {result1.status!r} vs {result2.status!r}"
    )
    assert result1.data.get("unresolved_count", result1.data.get("spec_cite_prelint_unresolved")) == \
        result2.data.get("unresolved_count", result2.data.get("spec_cite_prelint_unresolved")), (
        "expected identical unresolved data across re-entry calls"
    )

    result_events = [p for et, p in captured if et == "spec_cite_prelint_result"]
    assert len(result_events) == 2, (
        f"expected spec_cite_prelint_result emitted on BOTH calls (2 total), got: {captured!r}"
    )
    assert result1.status not in ("error", "escalate")
    assert result2.status not in ("error", "escalate")


# ── AC9 (§2.3 GH641): status never 'escalate'/'error' across all branches ──


def test_ac9_status_never_escalate_or_error_across_branches(tmp_path, monkeypatch):
    fn = _prelint_fn()
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, p: None, raising=False
    )

    dirty_spec, dirty_root = _dirty_fixture(tmp_path / "dirty")
    clean_spec, clean_root = _clean_fixture(tmp_path / "clean")

    branches = [
        ("frozen", _ctx({"hal_root": str(dirty_root)}),
         StepResult(status="ok", data={"spec_path": str(dirty_spec), "is_frozen": True}, duration_ms=0, step_name="x")),
        ("killswitch", _ctx({"hal_root": str(dirty_root), "spec_cite_prelint_skip": True}),
         StepResult(status="ok", data={"spec_path": str(dirty_spec)}, duration_ms=0, step_name="x")),
        ("clean", _ctx({"hal_root": str(clean_root)}),
         StepResult(status="ok", data={"spec_path": str(clean_spec)}, duration_ms=0, step_name="x")),
        ("unresolved", _ctx({"hal_root": str(dirty_root)}),
         StepResult(status="ok", data={"spec_path": str(dirty_spec)}, duration_ms=0, step_name="x")),
    ]

    for label, ctx, prev in branches:
        result = fn(ctx, prev)
        assert result.status in ("ok", "skip"), (
            f"branch={label}: expected status in {{'ok','skip'}}, got {result.status!r} "
            "(would perturb consume_recoverable_retry cycle-counting)"
        )
