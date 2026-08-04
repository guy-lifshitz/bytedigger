"""RED tests — anthropic-api backend honors reasoning-effort (GH #329).

Spec: SHARED/memory/Decisions/2026-07-04_GH329_anthropic_api_effort_spec.md
UUT: SYSTEM/cli/build/engine_py/lib/reference_backends/anthropic_api.py
     (helpers _build_request_body / _resolve_thinking, constant _EFFORT_TO_BUDGET)

8 tests, one per AC1-AC8 (AC6 parametrized over the 3 effort levels: 10 cases total).

Non-collectable RED guard (D1CF5FDF / §1q): `lib.reference_backends.anthropic_api`
ALREADY EXISTS and imports cleanly today (safe to import at module scope) — but the
new symbols `_build_request_body`, `_resolve_thinking`, and `_EFFORT_TO_BUDGET` do
NOT exist on it yet. We therefore NEVER reference those new symbols at module top
level or in `@pytest.mark.parametrize` collection-time arguments (the three effort
level NAMES are hardcoded string literals — the DICT itself is only looked up
inside each test body). Every access to the new symbols happens via `_load_mod()`
called INSIDE each test function, so a missing symbol raises AttributeError at
call/assert time — a clean FAIL, never a collection-time ImportError/hang.

§1l stub-passability: the UUT is `_build_request_body` / `_resolve_thinking` —
neither is ever patched here. The only monkeypatch target is the collaborator
`_load_effort` (imported into the anthropic_api module namespace per spec §2.5),
which is NOT the UUT.

Pre-GREEN predict: ALL tests FAIL — `_build_request_body` / `_resolve_thinking` /
`_EFFORT_TO_BUDGET` are absent from lib.reference_backends.anthropic_api, so every
test raises AttributeError the moment it accesses one of these symbols.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helper: deferred import of the (existing) module, new symbols accessed lazily
# ---------------------------------------------------------------------------

def _load_mod():
    """Import the anthropic_api module (safe — it exists today).

    The NEW symbols (_build_request_body, _resolve_thinking, _EFFORT_TO_BUDGET)
    are looked up as attributes by the caller, at test-body time — NOT here,
    and NEVER at module/collection scope. Missing attribute => AttributeError
    raised inside the test function => clean FAIL, not a collection error.
    """
    from bytedigger_engine.lib.reference_backends import anthropic_api as m  # noqa: PLC0415
    return m


_PROMPT = "test prompt"
_MODEL_ID = "claude-sonnet-4-6"
_MODEL = "sonnet"
_STEP_NAME = "architect"


def _stub_effort(monkeypatch, m, level):
    """Monkeypatch the collaborator _load_effort (NOT the UUT) on module m."""
    monkeypatch.setattr(
        m, "_load_effort", lambda model=None, step_name=None: level, raising=False
    )


# ---------------------------------------------------------------------------
# AC1 — medium effort → thinking present, max_tokens bumped
# ---------------------------------------------------------------------------

def test_ac1_medium_effort_adds_thinking_and_bumps_max_tokens(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, "medium")

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=False
    )

    assert body["thinking"] == {
        "type": "enabled",
        "budget_tokens": m._EFFORT_TO_BUDGET["medium"],
    }, f"AC1: thinking dict mismatch; got {body.get('thinking')!r}"
    assert body["max_tokens"] == m._EFFORT_TO_BUDGET["medium"] + m._DEFAULT_MAX_TOKENS, (
        f"AC1: max_tokens must be budget+default; got {body.get('max_tokens')!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — high effort → budget_tokens reflects the high map entry
# ---------------------------------------------------------------------------

def test_ac2_high_effort_uses_high_budget(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, "high")

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=False
    )

    assert body["thinking"]["budget_tokens"] == m._EFFORT_TO_BUDGET["high"], (
        f"AC2: budget_tokens must equal _EFFORT_TO_BUDGET['high']; "
        f"got {body.get('thinking')!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — no effort configured (None) → byte-identical baseline body
# ---------------------------------------------------------------------------

def test_ac3_no_effort_configured_yields_baseline_body(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, None)

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=False
    )

    assert "thinking" not in body, "AC3: thinking key must be absent when effort is None"
    assert set(body.keys()) == {"model", "max_tokens", "messages"}, (
        f"AC3: body keys must be exactly model/max_tokens/messages; got {set(body.keys())!r}"
    )
    assert body["max_tokens"] == m._DEFAULT_MAX_TOKENS, (
        f"AC3: max_tokens must equal _DEFAULT_MAX_TOKENS; got {body.get('max_tokens')!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — empty-string effort level → falsy-level guard, no thinking
# ---------------------------------------------------------------------------

def test_ac4_empty_string_effort_yields_no_thinking(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, "")

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=False
    )

    assert "thinking" not in body, "AC4: empty-string effort level must not add thinking"


# ---------------------------------------------------------------------------
# AC5 — hard_gate=True exempts thinking even at high effort
# ---------------------------------------------------------------------------

def test_ac5_hard_gate_true_exempts_thinking(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, "high")

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=True
    )

    assert "thinking" not in body, "AC5: hard_gate=True must exempt thinking regardless of effort"


# ---------------------------------------------------------------------------
# AC6 — parametrized over the 3 effort levels: budget/max_tokens API invariant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_ac6_every_level_satisfies_budget_max_tokens_invariant(monkeypatch, level):
    m = _load_mod()
    _stub_effort(monkeypatch, m, level)

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=False
    )

    assert "thinking" in body, f"AC6[{level}]: thinking must be present"
    assert body["max_tokens"] > body["thinking"]["budget_tokens"], (
        f"AC6[{level}]: max_tokens must exceed budget_tokens; got {body!r}"
    )
    assert body["thinking"]["budget_tokens"] >= 1024, (
        f"AC6[{level}]: budget_tokens must satisfy Anthropic's >=1024 floor; got {body!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — _resolve_thinking is a directly-callable named helper (§1aa)
# ---------------------------------------------------------------------------

def test_ac7_resolve_thinking_returns_exact_dict_for_low(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, "low")

    result = m._resolve_thinking(_MODEL, _STEP_NAME, False)

    assert result == {
        "type": "enabled",
        "budget_tokens": m._EFFORT_TO_BUDGET["low"],
    }, f"AC7: _resolve_thinking('sonnet','architect',False) mismatch; got {result!r}"


# ---------------------------------------------------------------------------
# AC8 — unrecognized effort level ("ultra") → graceful no-thinking
# ---------------------------------------------------------------------------

def test_ac8_unrecognized_effort_level_yields_no_thinking(monkeypatch):
    m = _load_mod()
    _stub_effort(monkeypatch, m, "ultra")

    body = m._build_request_body(
        _PROMPT, _MODEL_ID, model=_MODEL, step_name=_STEP_NAME, hard_gate=False
    )

    assert "thinking" not in body, (
        "AC8: unrecognized effort level ('ultra') must not add thinking"
    )
