"""RED tests for 3F13371D EFFORTHYBRID: per-model + per-phase effort resolution.

Spec: SHARED/memory/Decisions/2026-07-04_EFFORTHYBRID_per_model_phase_effort_spec.md
(## FROZEN section, §3 AC list).

GREEN will change `_load_effort` from the 32ED59E2 zero-arg, str-only form:
    def _load_effort() -> "str | None":
        ...
        effort = raw.get("claude", {}).get("effort")
        if isinstance(effort, str) and effort:
            return effort
        return None
to a two-arg resolver:
    def _load_effort(model=None, step_name=None) -> "str | None":
        # dict form: by_phase[step_name] -> by_model[_model_family(model)] -> None
        # str form (legacy): global default for all model/step
        # other/absent: None

§1q / D1CF5FDF compliance: `_load_effort` ALREADY EXISTS in llm_subprocess.py today,
so a direct (non-deferred) call is collection-safe — this file collects cleanly.
Every test below FAILS at call-time or assert-time, never at collection time:
  - calls with a `model` positional arg raise TypeError today (current signature
    takes zero arguments), OR
  - calls with a dict-shaped `effort` value return None today (current code only
    accepts `isinstance(effort, str)`, so any dict is silently rejected) where the
    spec requires dict-aware resolution to return a specific string.

§1l (stub-passability): every test calls the real UUT `llm_subprocess._load_effort`.
No mocking of _load_effort/_apply_effort/_invoke_subprocess. Only
`config_provider.models_config_path` (a non-UUT config seam) is monkeypatched, per
the existing 32ED59E2 test file's idiom, which this file copies verbatim.

§1i: no singleton-resource or timing races — each test writes its own tmp_path
config file and resets config_provider's cached factory in a finally block.
"""
from __future__ import annotations

import json

import config_provider
import llm_subprocess


def _write_config(tmp_path, effort_value, name="models.json"):
    models_json = tmp_path / name
    models_json.write_text(json.dumps({"claude": {"effort": effort_value}}))
    return models_json


# ─── AC1: by_model default when by_phase is empty ──────────────────────────────

def test_by_model_sonnet_default_when_by_phase_empty(monkeypatch, tmp_path):
    """AC1: dict {"by_model":{"sonnet":"medium"}, "by_phase":{}} →
    _load_effort("sonnet") == "medium".

    Fails today: current _load_effort() takes zero args — calling it with a
    positional "sonnet" argument raises TypeError (wrong arity).
    """
    effort = {"by_model": {"sonnet": "medium"}, "by_phase": {}}
    models_json = _write_config(tmp_path, effort)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result = llm_subprocess._load_effort("sonnet")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "medium", (
        f"Expected _load_effort('sonnet') == 'medium' via by_model default, "
        f"got {result!r}"
    )


# ─── AC2: by_model null for haiku → None (haiku untouched) ─────────────────────

def test_by_model_haiku_null_returns_none(monkeypatch, tmp_path):
    """AC2: dict {"by_model":{"haiku":null}, "by_phase":{}} →
    _load_effort("haiku") is None (haiku explicitly untouched).

    Fails today: current _load_effort() takes zero args — calling it with a
    positional "haiku" argument raises TypeError (wrong arity).
    """
    effort = {"by_model": {"haiku": None}, "by_phase": {}}
    models_json = _write_config(tmp_path, effort)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result = llm_subprocess._load_effort("haiku")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result is None, (
        f"Expected _load_effort('haiku') is None when by_model.haiku=null, "
        f"got {result!r}"
    )


# ─── AC3: by_phase overrides by_model when step_name matches ───────────────────

def test_by_phase_overrides_by_model_on_matching_step(monkeypatch, tmp_path):
    """AC3: dict {"by_model":{"sonnet":"medium"}, "by_phase":{"invoke_green_llm":"high"}}
    → _load_effort("sonnet", "invoke_green_llm") == "high" (by_phase beats by_model).

    Fails today: current _load_effort() takes zero args — calling it with two
    positional arguments raises TypeError (wrong arity).
    """
    effort = {
        "by_model": {"sonnet": "medium"},
        "by_phase": {"invoke_green_llm": "high"},
    }
    models_json = _write_config(tmp_path, effort)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result = llm_subprocess._load_effort("sonnet", "invoke_green_llm")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "high", (
        f"Expected _load_effort('sonnet', 'invoke_green_llm') == 'high' "
        f"(by_phase override wins), got {result!r}"
    )


# ─── AC4: by_model default for opus ─────────────────────────────────────────────

def test_by_model_opus_default(monkeypatch, tmp_path):
    """AC4: dict {"by_model":{"opus":"high"}} → _load_effort("opus") == "high".

    Fails today: current _load_effort() takes zero args — calling it with a
    positional "opus" argument raises TypeError (wrong arity).
    """
    effort = {"by_model": {"opus": "high"}, "by_phase": {}}
    models_json = _write_config(tmp_path, effort)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result = llm_subprocess._load_effort("opus")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "high", (
        f"Expected _load_effort('opus') == 'high' via by_model default, got {result!r}"
    )


# ─── AC5: step not present in by_phase falls back to by_model ──────────────────

def test_step_not_in_by_phase_falls_back_to_by_model(monkeypatch, tmp_path):
    """AC5: dict {"by_model":{"sonnet":"medium"}, "by_phase":{"invoke_green_llm":"high"}}
    → _load_effort("sonnet", "invoke_red_llm") == "medium" (step_name not matched
    in by_phase, so resolution falls through to by_model.sonnet).

    Fails today: current _load_effort() takes zero args — calling it with two
    positional arguments raises TypeError (wrong arity).
    """
    effort = {
        "by_model": {"sonnet": "medium"},
        "by_phase": {"invoke_green_llm": "high"},
    }
    models_json = _write_config(tmp_path, effort)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result = llm_subprocess._load_effort("sonnet", "invoke_red_llm")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "medium", (
        f"Expected _load_effort('sonnet', 'invoke_red_llm') == 'medium' "
        f"(unmatched step falls back to by_model), got {result!r}"
    )


# ─── AC6/AC7 spec: unrecognized model family → None (graceful) ─────────────────

def test_unrecognized_model_family_returns_none(monkeypatch, tmp_path):
    """Graceful AC: dict {"by_model":{"sonnet":"medium"}}, no by_phase hit →
    _load_effort("gpt-4-turbo") is None (unrecognized family, no matching by_phase).

    Fails today: current _load_effort() takes zero args — calling it with a
    positional "gpt-4-turbo" argument raises TypeError (wrong arity).
    """
    effort = {"by_model": {"sonnet": "medium"}, "by_phase": {}}
    models_json = _write_config(tmp_path, effort)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result = llm_subprocess._load_effort("gpt-4-turbo")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result is None, (
        f"Expected _load_effort('gpt-4-turbo') is None (unrecognized model family, "
        f"no by_phase match), got {result!r}"
    )


# ─── AC back-compat: plain-string claude.effort is a global default ────────────

def test_backcompat_string_effort_is_global_default_for_any_model_and_step(
    monkeypatch, tmp_path
):
    """Back-compat AC: {"claude":{"effort":"medium"}} (plain STRING, the 32ED59E2
    shape) → BOTH _load_effort("sonnet") == "medium" AND
    _load_effort("haiku", "invoke_green_llm") == "medium" — the string form is a
    global default applied regardless of model or step_name (preserves prior
    32ED59E2 behavior unchanged by the new dict-aware resolver).

    Fails today: current _load_effort() takes zero args — calling it with
    positional arguments raises TypeError (wrong arity), even though the string
    branch itself (isinstance(effort, str)) already exists in the zero-arg form.
    """
    models_json = _write_config(tmp_path, "medium")
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    try:
        result_sonnet = llm_subprocess._load_effort("sonnet")
        result_haiku_step = llm_subprocess._load_effort("haiku", "invoke_green_llm")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result_sonnet == "medium", (
        f"Expected _load_effort('sonnet') == 'medium' under legacy string back-compat, "
        f"got {result_sonnet!r}"
    )
    assert result_haiku_step == "medium", (
        f"Expected _load_effort('haiku', 'invoke_green_llm') == 'medium' under legacy "
        f"string back-compat (model/step ignored), got {result_haiku_step!r}"
    )
