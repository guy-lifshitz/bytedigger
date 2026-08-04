"""RED tests for GH681 — enforce-teeth for spec-writer cite pre-lint.

Adds a bounded 1x re-prompt enforce branch to the existing (warn-only,
GH675/#680) `_verify_spec_cite_prelint` step, gated by
`HAL_SPEC_CITE_PRELINT_ENFORCE` (default OFF). New pure helper
`_format_prelint_cite_directive` does NOT exist yet — looked up via
`getattr` INSIDE each test body so the file COLLECTS cleanly
(§1q ext / D1CF5FDF). `_verify_spec_cite_prelint` and
`_inphase_unresolved_symbols` already exist (from #680) and are used
directly / patched as a dependency (never as the UUT itself, per §1l).

Do NOT implement any production change here — RED-only file.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bytedigger_engine.workflows import phase_45_spec
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows._recoverable_policy import resolve_policy
from bytedigger_engine import flags_catalog


def _ctx(org_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(org_config=org_config or {})


def _directive_fn():
    """Deferred lookup — raises AssertionError (not ImportError/NameError)
    until GREEN ships the helper."""
    fn = getattr(phase_45_spec, "_format_prelint_cite_directive", None)
    assert fn is not None, "_format_prelint_cite_directive not yet implemented (GREEN pending)"
    return fn


def _spec_file(tmp_path):
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("The function `foo` in `helpers.py` performs validation.\n")
    return spec_path


# ── AC1: pure helper renders a self-labeling directive block ───────────────


def test_ac1_format_prelint_cite_directive_lists_all_symbols():
    fn = _directive_fn()
    text = fn(["alpha", "beta"])

    assert "- alpha" in text, f"expected '- alpha' on its own line, got:\n{text}"
    assert "- beta" in text, f"expected '- beta' on its own line, got:\n{text}"
    assert "NEW" in text, f"expected literal 'NEW' marker guidance, got:\n{text}"


# ── AC2: enforce OFF -> unchanged warn-advance, no enforce_* events ─────────


def test_ac2_enforce_off_unchanged_warn_advance(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_CITE_PRELINT_ENFORCE", raising=False)
    monkeypatch.setattr(phase_45_spec, "_inphase_unresolved_symbols", lambda *a, **k: ["foo"])

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: events.append((et, p)))

    spec_path = _spec_file(tmp_path)
    ctx = _ctx({})
    prev = {"spec_path": str(spec_path), "cycle": 1}

    sr = phase_45_spec._verify_spec_cite_prelint(ctx, prev)

    assert sr.status == "ok", f"expected status='ok', got {sr.status!r}"
    assert sr.data.get("retry_from_step") is None, (
        f"expected no retry_from_step on enforce-OFF advance, got {sr.data.get('retry_from_step')!r}"
    )
    assert sr.data.get("spec_cite_prelint_unresolved") == ["foo"], (
        f"expected data['spec_cite_prelint_unresolved']==['foo'], got {sr.data.get('spec_cite_prelint_unresolved')!r}"
    )
    enforce_events = [et for et, _ in events if et.startswith("spec_cite_prelint_enforce")]
    assert enforce_events == [], (
        f"enforce OFF must NOT emit any spec_cite_prelint_enforce_* event; got {events!r}"
    )


# ── AC3: enforce ON, fresh attempts -> recoverable retry with findings ─────


def test_ac3_enforce_on_fresh_returns_recoverable_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")
    monkeypatch.setattr(phase_45_spec, "_inphase_unresolved_symbols", lambda *a, **k: ["foo", "bar"])

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: events.append((et, p)))

    spec_path = _spec_file(tmp_path)
    ctx = _ctx({})
    prev = {"spec_path": str(spec_path), "cycle": 1}

    sr = phase_45_spec._verify_spec_cite_prelint(ctx, prev)

    assert sr.status == "error", f"expected status='error' (recoverable retry shape), got {sr.status!r}"
    assert sr.recoverable is True, f"expected recoverable is True, got {sr.recoverable!r}"
    assert sr.data.get("retry_from_step") == 0, (
        f"expected data['retry_from_step']==0, got {sr.data.get('retry_from_step')!r}"
    )
    gate_attempts = sr.data.get("gate_attempts", {})
    assert gate_attempts.get("spec_cite_prelint") == 1, (
        f"expected gate_attempts['spec_cite_prelint']==1, got {gate_attempts!r}"
    )
    assert sr.data.get("cycle_count") == 1, (
        f"expected data['cycle_count']==1, got {sr.data.get('cycle_count')!r}"
    )
    findings = sr.data.get("findings", "")
    assert "foo" in findings and "bar" in findings, (
        f"expected both 'foo' and 'bar' present in data['findings'], got: {findings!r}"
    )


# ── AC4: enforce ON, cap reached -> non-terminal advance, exhausted event ──


def test_ac4_enforce_on_cap_reached_nonterminal_advance(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")
    monkeypatch.setattr(phase_45_spec, "_inphase_unresolved_symbols", lambda *a, **k: ["foo"])

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: events.append((et, p)))

    spec_path = _spec_file(tmp_path)
    ctx = _ctx({})
    prev = {
        "spec_path": str(spec_path),
        "cycle": 2,
        "gate_attempts": {"spec_cite_prelint": 1},
    }

    sr = phase_45_spec._verify_spec_cite_prelint(ctx, prev)

    assert sr.status == "ok", f"expected non-terminal status='ok', got {sr.status!r}"
    assert sr.data.get("retry_from_step") is None, (
        f"expected no retry_from_step on cap-exhausted advance, got {sr.data.get('retry_from_step')!r}"
    )
    assert sr.status != "error", "cap-exhaustion must NOT be a new terminal error (§1n)"

    event_names = [et for et, _ in events]
    assert "spec_cite_prelint_enforce_exhausted" in event_names, (
        f"expected spec_cite_prelint_enforce_exhausted emitted, got: {event_names!r}"
    )
    assert "spec_cite_prelint_enforce_retry" not in event_names, (
        f"cap-exhausted path must NOT emit enforce_retry, got: {event_names!r}"
    )


# ── AC5: retry path emits full event trio with correct payload keys ────────


def test_ac5_retry_path_emits_all_three_events(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")
    monkeypatch.setattr(phase_45_spec, "_inphase_unresolved_symbols", lambda *a, **k: ["foo", "bar"])

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: events.append((et, p)))

    spec_path = _spec_file(tmp_path)
    ctx = _ctx({})
    prev = {"spec_path": str(spec_path), "cycle": 1}

    phase_45_spec._verify_spec_cite_prelint(ctx, prev)

    event_names = [et for et, _ in events]
    for expected in (
        "spec_cite_prelint_result",
        "spec_cite_prelint_warn",
        "spec_cite_prelint_enforce_retry",
    ):
        assert expected in event_names, f"expected {expected!r} emitted, got: {event_names!r}"

    retry_payload = next(p for et, p in events if et == "spec_cite_prelint_enforce_retry")
    for key in ("unresolved_count", "symbols", "cycle"):
        assert key in retry_payload, (
            f"expected key {key!r} in enforce_retry payload, got: {retry_payload!r}"
        )


# ── AC6: policy SSOT locks bounded 1x contract across build classes ────────


@pytest.mark.parametrize("build_class", ["SIMPLE", "FEATURE", "COMPLEX"])
def test_ac6_policy_locks_bounded_once_per_build_class(build_class):
    pol = resolve_policy(build_class, "spec_cite_prelint")
    assert pol.cycle_cap == 1, f"expected cycle_cap==1 for {build_class}, got {pol.cycle_cap!r}"
    assert pol.slot == "recoverable_once", (
        f"expected slot=='recoverable_once' for {build_class}, got {pol.slot!r}"
    )


# ── AC7 (§1ab re-entry idempotency): two sequential exhausted calls ────────


def test_ac7_reentry_exhausted_path_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")
    monkeypatch.setattr(phase_45_spec, "_inphase_unresolved_symbols", lambda *a, **k: ["foo"])

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: events.append((et, p)))

    spec_path = _spec_file(tmp_path)
    ctx = _ctx({})
    prev = {
        "spec_path": str(spec_path),
        "cycle": 2,
        "gate_attempts": {"spec_cite_prelint": 1},
    }

    for call_idx in (1, 2):
        events.clear()
        sr = phase_45_spec._verify_spec_cite_prelint(ctx, prev)

        assert sr.status == "ok", (
            f"call {call_idx}: expected status='ok', got {sr.status!r}"
        )
        assert sr.data.get("retry_from_step") is None, (
            f"call {call_idx}: expected no retry_from_step, got {sr.data.get('retry_from_step')!r}"
        )
        event_names = [et for et, _ in events]
        assert "spec_cite_prelint_enforce_exhausted" in event_names, (
            f"call {call_idx}: expected enforce_exhausted emitted, got: {event_names!r}"
        )
        gate_attempts_after = sr.data.get("gate_attempts", {})
        count = gate_attempts_after.get("spec_cite_prelint", 1)
        assert count <= 1, (
            f"call {call_idx}: exhausted branch must NOT increment counter, "
            f"got gate_attempts['spec_cite_prelint']=={count!r}"
        )


# ── AC8: flag registered in flags_catalog.py with default-OFF + flip-by ────


def test_ac8_flag_registered_in_flags_catalog():
    entry = flags_catalog.FLAGS.get("HAL_SPEC_CITE_PRELINT_ENFORCE")
    assert entry is not None, (
        "expected 'HAL_SPEC_CITE_PRELINT_ENFORCE' registered in flags_catalog.FLAGS"
    )
    assert entry.get("kind") == "flag", f"expected kind=='flag', got {entry.get('kind')!r}"
    assert entry.get("default") == "0", f"expected default=='0', got {entry.get('default')!r}"
    assert "flip-by:" in entry.get("description", ""), (
        f"expected 'flip-by:' token in description, got: {entry.get('description')!r}"
    )
