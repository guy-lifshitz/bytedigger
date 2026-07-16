"""RED tests for 32ED59E2: Sonnet 5 effort=medium lever.

Spec: SHARED/memory/Decisions/2026-07-04_SONNET5EFFORT_hal_delegation_effort_medium_spec.md

§1q / D1CF5FDF compliance: `_load_effort` and `_apply_effort` do NOT exist yet in
llm_subprocess.py. All imports of these not-yet-existing symbols are DEFERRED inside
each test body via `getattr(llm_subprocess, "<name>", None)` — this file COLLECTS
cleanly and tests FAIL at assert-time, NEVER at collection time.

§1l (stub-passability): every test calls the real UUT. No mocking of _load_effort,
_apply_effort, or _invoke_subprocess themselves. config_provider.models_config_path
is monkeypatched (a non-UUT dependency), which is the correct seam per §2 design.

§1i: no singleton-resource or timing races. config_provider.reset_default_config_provider_factory()
is called in finally blocks for tests that override the config seam, to prevent
provider-state leakage between tests.

Expected FAIL today (pre-GREEN):
  AC1 — FAIL: _load_effort does not exist → getattr returns None → assert callable fails
  AC2 — FAIL: _load_effort does not exist → getattr returns None → assert callable fails
  AC3 — FAIL: _load_effort does not exist → getattr returns None → assert callable fails
  AC4 — FAIL: _apply_effort does not exist → getattr returns None → assert callable fails
  AC5 — FAIL: _apply_effort does not exist → getattr returns None → assert callable fails
  AC6 — FAIL: _invoke_subprocess body contains neither "_apply_effort(" nor "_load_effort()"
              nor "not hard_gate" guard wiring → string-presence assertions fail
"""
from __future__ import annotations

import inspect
import json

import pytest

import config_provider
import llm_subprocess


# ─── AC1: _load_effort() returns "medium" when config has claude.effort:"medium" ──

def test_ac1_load_effort_reads_medium(monkeypatch, tmp_path):
    """AC1: _load_effort() returns "medium" when models.json has {"claude":{"effort":"medium"}}.

    Config injected by monkeypatching config_provider.models_config_path → tmp_path file.
    _load_effort deferred via getattr (§1q / D1CF5FDF — MUST collect, fail at assert-time).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"effort": "medium"}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_effort", None)
    assert callable(fn), (
        "_load_effort does not exist in llm_subprocess yet — "
        "GREEN must add it (AC1 FAILS RED)"
    )

    try:
        result = fn()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "medium", (
        f"Expected _load_effort() == 'medium' when claude.effort='medium' in models.json, "
        f"got {result!r}"
    )


# ─── AC2: _load_effort() returns None when claude.effort key is absent ─────────

def test_ac2_load_effort_none_when_absent(monkeypatch, tmp_path):
    """AC2: _load_effort() returns None when models.json has {"claude":{}} (no effort key).

    Inert-by-default guarantee: absence of the config key must produce None (no-op).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_effort", None)
    assert callable(fn), (
        "_load_effort does not exist in llm_subprocess yet — "
        "GREEN must add it (AC2 FAILS RED)"
    )

    try:
        result = fn()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result is None, (
        f"Expected _load_effort() == None when claude.effort absent from models.json, "
        f"got {result!r}"
    )


# ─── AC3: _load_effort() returns None on bad/empty values ──────────────────────

def test_ac3_load_effort_none_on_bad_value(monkeypatch, tmp_path):
    """AC3: _load_effort() returns None for empty-string AND non-str values.

    Sub-case A: {"claude":{"effort":""}} → None (empty string rejected).
    Sub-case B: {"claude":{"effort":123}} → None (non-str rejected; best-effort).
    """
    fn = getattr(llm_subprocess, "_load_effort", None)
    assert callable(fn), (
        "_load_effort does not exist in llm_subprocess yet — "
        "GREEN must add it (AC3 FAILS RED)"
    )

    # Sub-case A: empty string
    models_json = tmp_path / "models_empty.json"
    models_json.write_text(json.dumps({"claude": {"effort": ""}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)
    try:
        result_empty = fn()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result_empty is None, (
        f"Expected _load_effort() == None when effort='', got {result_empty!r}"
    )

    # Sub-case B: non-str integer
    models_json2 = tmp_path / "models_int.json"
    models_json2.write_text(json.dumps({"claude": {"effort": 123}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json2)
    try:
        result_int = fn()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result_int is None, (
        f"Expected _load_effort() == None when effort=123 (non-str), got {result_int!r}"
    )


# ─── AC4: _apply_effort appends --effort flag when effort is truthy str ─────────

def test_ac4_apply_effort_appends_flag():
    """AC4: _apply_effort(["claude","-p","--model","sonnet"], "medium")
    == ["claude","-p","--model","sonnet","--effort","medium"].

    Pure helper, no config seam needed.
    """
    fn = getattr(llm_subprocess, "_apply_effort", None)
    assert callable(fn), (
        "_apply_effort does not exist in llm_subprocess yet — "
        "GREEN must add it (AC4 FAILS RED)"
    )

    base = ["claude", "-p", "--model", "sonnet"]
    result = fn(base, "medium")

    assert result == ["claude", "-p", "--model", "sonnet", "--effort", "medium"], (
        f"Expected _apply_effort to append ['--effort','medium'], got {result!r}"
    )


# ─── AC5: _apply_effort returns command unchanged when effort is None ───────────

def test_ac5_apply_effort_none_passthrough():
    """AC5: _apply_effort(["claude","-p"], None) returns ["claude","-p"] unchanged
    and "--effort" is NOT in the result.
    """
    fn = getattr(llm_subprocess, "_apply_effort", None)
    assert callable(fn), (
        "_apply_effort does not exist in llm_subprocess yet — "
        "GREEN must add it (AC5 FAILS RED)"
    )

    base = ["claude", "-p"]
    result = fn(base, None)

    assert result == ["claude", "-p"], (
        f"Expected _apply_effort(cmd, None) == cmd unchanged, got {result!r}"
    )
    assert "--effort" not in result, (
        f"Expected '--effort' NOT in result for None effort, got {result!r}"
    )


# ─── AC6: _invoke_subprocess body is wired with _apply_effort + hard-gate exempt ─

def test_ac6_invoke_subprocess_wires_effort_hardgate_exempt():
    """AC6 (§1l anti-stub WIRING gate, updated for 3F13371D EFFORTHYBRID):
    inspect.getsource(_invoke_subprocess) must contain all four tokens:
      - "_apply_effort("     — helper is called in the real spawn path
      - "_load_effort(model" — config-reader is called with the model + step_name
                               locals (NOT the old zero-arg "_load_effort()" form)
      - "step_name"          — the step_name local must be passed through to the resolver
      - "not hard_gate"      — Opus-gate exemption is present (effort skipped for hard_gate=True)

    _invoke_subprocess EXISTS today so direct getsource is safe (no getattr deferral needed).
    This test forces GREEN to WIRE the per-model/per-phase resolver call (model, step_name)
    into the real spawn function — defining _load_effort/_apply_effort alone is insufficient;
    AC6 will still FAIL until the two-arg wiring lands.

    Fails today: current _invoke_subprocess body calls "_load_effort()" (zero-arg, the
    32ED59E2 global-only form), not "_load_effort(model" — so this assertion FAILS RED.
    """
    src = inspect.getsource(llm_subprocess._invoke_subprocess)

    assert "_apply_effort(" in src, (
        "_invoke_subprocess body does not call _apply_effort() yet — "
        "GREEN must wire it into the effective_command assembly (AC6 FAILS RED)"
    )
    assert "_load_effort(model" in src, (
        "_invoke_subprocess body does not call _load_effort(model, step_name) yet — "
        "GREEN must pass model + step_name into the resolver, not the bare zero-arg "
        "_load_effort() call (AC6 FAILS RED, 3F13371D EFFORTHYBRID)"
    )
    assert "step_name" in src, (
        "_invoke_subprocess body does not reference step_name in the effort-wiring "
        "call — GREEN must thread step_name through to _load_effort (AC6 FAILS RED)"
    )
    assert "not hard_gate" in src, (
        "_invoke_subprocess body does not contain 'not hard_gate' guard yet — "
        "GREEN must preserve Opus-gate exemption (hard_gate=True skips effort injection) "
        "(AC6 FAILS RED)"
    )
