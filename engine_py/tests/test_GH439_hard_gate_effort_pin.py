"""RED tests for GH439: hard_gate effort pin.

Spec: SHARED/memory/Decisions/2026-07-09_GH439_hard_gate_effort_pin_spec.md

§1q / D1CF5FDF compliance: `_load_effort_gate` does NOT exist yet in
llm_subprocess.py. All references to it are DEFERRED inside each test body
via `getattr(llm_subprocess, "_load_effort_gate", None)` — this file COLLECTS
cleanly and tests FAIL at assert-time, never at collection time.

§1l (stub-passability): every test calls the real UUT (`_load_effort_gate`,
`_apply_effort`). No mocking of the UUT itself. Only
config_provider.models_config_path is monkeypatched (a non-UUT dependency),
mirroring the seam used by the sibling 32ED59E2 test.

§1i: no singleton-resource or timing races.
config_provider.reset_default_config_provider_factory() is called in
finally blocks for tests that override the config seam.

Expected FAIL today (pre-GREEN):
  AC1 — FAIL: _load_effort_gate does not exist → getattr returns None → assert callable fails
  AC2 — FAIL: same
  AC3 — FAIL: same
  AC4 — FAIL: same
  AC5 — FAIL: same
  AC6 — FAIL: _invoke_subprocess source has no else-branch calling
              _load_effort_gate(model) yet
  AC7 — FAIL: _load_effort_gate does not exist → getattr returns None → assert callable fails
  AC8 — FAIL: source has "_load_effort(model" under "if not hard_gate:" but
              no immediately-following else-branch wiring _load_effort_gate(model)
"""
from __future__ import annotations

import inspect
import json

import config_provider
import llm_subprocess


# ─── AC1: _load_effort_gate("fable") == "low" with by_model pin fixture ────────

def test_ac1_load_effort_gate_reads_by_model_pin(monkeypatch, tmp_path):
    """AC1: fixture {"claude":{"effort":{"by_model":{"fable":"low"}}}} →
    _load_effort_gate("fable") == "low".
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps({"claude": {"effort": {"by_model": {"fable": "low"}}}})
    )
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_effort_gate", None)
    assert callable(fn), (
        "_load_effort_gate does not exist in llm_subprocess yet — "
        "GREEN must add it (AC1 FAILS RED)"
    )

    try:
        result = fn("fable")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "low", (
        f"Expected _load_effort_gate('fable') == 'low' for by_model pin, got {result!r}"
    )


# ─── AC2: legacy global-str shape must NOT degrade the gate ────────────────────

def test_ac2_load_effort_gate_ignores_legacy_global_str(monkeypatch, tmp_path):
    """AC2: {"claude":{"effort":"medium"}} (legacy global shape) → None.

    A global default must NOT degrade gates; only an explicit per-model pin
    under by_model overrides the hard_gate exemption.
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"effort": "medium"}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_effort_gate", None)
    assert callable(fn), (
        "_load_effort_gate does not exist in llm_subprocess yet — "
        "GREEN must add it (AC2 FAILS RED)"
    )

    try:
        result = fn("fable")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result is None, (
        f"Expected _load_effort_gate() == None for legacy global-str effort shape, "
        f"got {result!r}"
    )


# ─── AC3: by_phase must be IGNORED entirely by the gate resolver ──────────────

def test_ac3_load_effort_gate_ignores_by_phase(monkeypatch, tmp_path):
    """AC3: fixture with ONLY by_phase populated → None (only by_model consulted)."""
    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps({"claude": {"effort": {"by_phase": {"validate_tests": "high"}}}})
    )
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_effort_gate", None)
    assert callable(fn), (
        "_load_effort_gate does not exist in llm_subprocess yet — "
        "GREEN must add it (AC3 FAILS RED)"
    )

    try:
        result = fn("fable")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result is None, (
        f"Expected _load_effort_gate() == None when only by_phase is populated "
        f"(by_phase must be ignored entirely), got {result!r}"
    )


# ─── AC4: unknown model family → None ──────────────────────────────────────────

def test_ac4_load_effort_gate_unknown_model_family(monkeypatch, tmp_path):
    """AC4: unknown/gibberish model string → None, even with a valid by_model fixture."""
    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps({"claude": {"effort": {"by_model": {"fable": "low"}}}})
    )
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_effort_gate", None)
    assert callable(fn), (
        "_load_effort_gate does not exist in llm_subprocess yet — "
        "GREEN must add it (AC4 FAILS RED)"
    )

    try:
        result = fn("gpt-9-gibberish-unknown-family")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result is None, (
        f"Expected _load_effort_gate() == None for unrecognized model family, "
        f"got {result!r}"
    )


# ─── AC5: broken JSON / missing file → None, never raises ─────────────────────

def test_ac5_load_effort_gate_none_on_broken_or_missing_config(monkeypatch, tmp_path):
    """AC5 sub-case A: broken/malformed JSON → None, no exception.
    AC5 sub-case B: nonexistent path → None, no exception.
    """
    fn = getattr(llm_subprocess, "_load_effort_gate", None)
    assert callable(fn), (
        "_load_effort_gate does not exist in llm_subprocess yet — "
        "GREEN must add it (AC5 FAILS RED)"
    )

    # Sub-case A: broken JSON
    broken_json = tmp_path / "broken_models.json"
    broken_json.write_text("{not valid json::::")
    monkeypatch.setattr(config_provider, "models_config_path", lambda: broken_json)
    try:
        result_broken = fn("fable")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result_broken is None, (
        f"Expected _load_effort_gate() == None on broken JSON (never raise), "
        f"got {result_broken!r}"
    )

    # Sub-case B: nonexistent path
    missing_path = tmp_path / "does_not_exist" / "models.json"
    monkeypatch.setattr(config_provider, "models_config_path", lambda: missing_path)
    try:
        result_missing = fn("fable")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result_missing is None, (
        f"Expected _load_effort_gate() == None on missing models.json (never raise), "
        f"got {result_missing!r}"
    )


# ─── AC6: _invoke_subprocess wires the hard_gate else-branch to _load_effort_gate ──

def test_ac6_invoke_subprocess_wires_hard_gate_else_branch():
    """AC6 (§1l anti-stub forcing function): inspect.getsource(_invoke_subprocess)
    must contain an else-branch (paired with the existing "if not hard_gate:"
    guard) that calls _apply_effort(..., _load_effort_gate(model)).

    _invoke_subprocess EXISTS today so direct getsource is safe.

    Fails today: the current body only has the `if not hard_gate:` branch
    calling _load_effort(model, step_name); there is no else-branch wiring
    _load_effort_gate(model) — so this assertion FAILS RED.
    """
    src = inspect.getsource(llm_subprocess._invoke_subprocess)

    assert "not hard_gate" in src, (
        "_invoke_subprocess body does not contain 'not hard_gate' guard — "
        "unexpected regression, this should already be present (AC6 FAILS RED)"
    )
    assert "else:" in src, (
        "_invoke_subprocess body does not contain an else-branch yet — "
        "GREEN must add the hard_gate else-branch (AC6 FAILS RED)"
    )
    assert "_load_effort_gate(model)" in src, (
        "_invoke_subprocess body does not call _load_effort_gate(model) yet — "
        "GREEN must wire the hard_gate else-branch to the new gate resolver "
        "(AC6 FAILS RED)"
    )
    assert "_apply_effort(effective_command, _load_effort_gate(model))" in src, (
        "_invoke_subprocess body does not call "
        "_apply_effort(effective_command, _load_effort_gate(model)) yet — "
        "GREEN must wire the else-branch exactly per spec §2 (AC6 FAILS RED)"
    )


# ─── AC7: hard_gate=True WITHOUT a pin → no --effort flag (old behavior kept) ──

def test_ac7_hard_gate_without_pin_appends_no_effort_flag(monkeypatch, tmp_path):
    """AC7: with an empty/absent by_model fixture, calling
    _apply_effort(cmd, _load_effort_gate("fable")) leaves argv unchanged —
    no --effort flag is appended (old hard_gate-exempt behavior preserved
    when there is no explicit per-model pin).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"effort": {"by_model": {}}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    gate_fn = getattr(llm_subprocess, "_load_effort_gate", None)
    assert callable(gate_fn), (
        "_load_effort_gate does not exist in llm_subprocess yet — "
        "GREEN must add it (AC7 FAILS RED)"
    )

    base = ["claude", "-p", "--model", "fable"]
    try:
        effort = gate_fn("fable")
    finally:
        config_provider.reset_default_config_provider_factory()

    result = llm_subprocess._apply_effort(base, effort)

    assert result == base, (
        f"Expected argv unchanged when hard_gate has no explicit pin, got {result!r}"
    )
    assert "--effort" not in result, (
        f"Expected no '--effort' flag in argv without an explicit pin, got {result!r}"
    )


# ─── AC8: non-gate path (if not hard_gate:) stays wired to the old _load_effort ──

def test_ac8_non_gate_path_still_uses_load_effort_and_gains_else_branch():
    """AC8: source contains _load_effort(model, step_name) under
    "if not hard_gate:", AND (per §2 design) an else: branch immediately
    wiring _load_effort_gate(model) follows it.

    This combined assertion is deliberately RED today (no else-branch exists
    yet) and pins the non-gate call unchanged post-GREEN.
    """
    src = inspect.getsource(llm_subprocess._invoke_subprocess)

    assert "if not hard_gate:" in src, (
        "_invoke_subprocess is missing the 'if not hard_gate:' guard — "
        "unexpected regression (AC8 FAILS RED)"
    )
    assert "_load_effort(model, step_name)" in src, (
        "_invoke_subprocess does not call _load_effort(model, step_name) under "
        "the non-gate branch — unexpected regression (AC8 FAILS RED)"
    )

    if_idx = src.find("if not hard_gate:")
    load_effort_idx = src.find("_load_effort(model, step_name)", if_idx)
    else_idx = src.find("else:", load_effort_idx)
    gate_idx = src.find("_load_effort_gate(model)", else_idx)

    assert if_idx != -1 and load_effort_idx != -1 and else_idx != -1 and gate_idx != -1, (
        "Expected an else-branch immediately following the "
        "'if not hard_gate: ... _load_effort(model, step_name)' block, wiring "
        "_load_effort_gate(model) — GREEN must add it per spec §2 "
        "(AC8 FAILS RED: else-branch does not exist yet)"
    )
