"""bd#167 - cost ledger: price cache tokens, record billing mode per LLM call.

Spec: docs/decisions/2026-10-02-bd167-cost-cache-billing.md (section 6 = the ACs).

Real side effects only: the pure module lib/llm_cost, events in a real EventLog
written by the real chokepoint (invoke_llm_subprocess -> _dispatch_backend) with
a registered fake backend, the real claude-subprocess and anthropic-api
adapters (Popen / urlopen faked at the infra seam), the real cost_rollup module
and its CLI. Pricing comes from a tmp models.json with explicit rates.

Hand-computed reference (rates per MTok, model "sonnet"):
  in 3.0, out 15.0, cache_read 0.3, cache_write_5m 3.75, cache_write_1h 6.0
  usage: in 1000, out 500, read 2000, write 500 (of which 1h 200)
  cost = (1000*3 + 500*15 + 2000*0.3 + 300*3.75 + 200*6.0) / 1e6
       = (3000 + 7500 + 600 + 1125 + 1200) / 1e6 = 0.013425
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import config_provider, llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)

try:  # the module under test does not exist before GREEN
    from bytedigger_engine.lib import llm_cost as _llm_cost_real
except ImportError:  # pragma: no cover - the RED state
    _llm_cost_real = None

from bytedigger_engine.lib import cost_rollup


def _lc():
    """Accessor: the real module, or a FAILED assertion in the calling test only."""
    assert _llm_cost_real is not None, "bytedigger_engine.lib.llm_cost is not importable (bd#167 not built)"
    return _llm_cost_real


class _LlmCostProxy:
    """`llm_cost.<attr>` resolves through `_lc()`, so only tests that touch it need the module."""

    def __getattr__(self, name):
        return getattr(_lc(), name)


llm_cost = _LlmCostProxy()

ENGINE_DIR = Path(__file__).resolve().parents[1]

RATES = {
    "in": 3.0,
    "out": 15.0,
    "cache_read": 0.3,
    "cache_write_5m": 3.75,
    "cache_write_1h": 6.0,
}
FULL_USAGE = {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cache_read_input_tokens": 2000,
    "cache_creation_input_tokens": 500,
    "cache_creation": {"ephemeral_1h_input_tokens": 200},
}
FULL_COST = 0.013425


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    for var in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
        monkeypatch.delenv(var, raising=False)
    for var in ("COST_DIVERGENCE_PCT", "RUNNER_BACKEND", "RUNNER_BACKEND_JUDGE", "RUNNER_BACKEND_WORKER"):
        for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
            monkeypatch.delenv(prefix + var, raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


def _pricing(monkeypatch, tmp_path, table=None):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"claude": {"pricing": table if table is not None else {"sonnet": RATES}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: path)


def _log(tmp_path, log=None):
    log = log if log is not None else EventLog(tmp_path / "e.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-r", step_name="s", phase="phase_5", cycle=2)
    return log


def _events(log, event_type):
    return [e["payload"] for e in log.read_all() if e["event_type"] == event_type]


def _fake_backend(name, data, *, caps=(), status="ok"):
    """Register a backend that returns `data` merged with the extra_data it was handed
    (like the real adapters, caller extra_data wins on collision)."""
    def _impl(**kwargs):
        if data is None:
            return StepResult(status=status, data=None, duration_ms=1, step_name=kwargs["step_name"],
                              error="boom", error_code="E_FAKE", recoverable=False)
        merged = {**data, **(kwargs.get("extra_data") or {})}
        return StepResult(status=status, data=merged, duration_ms=1, step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", capabilities=set(caps), overwrite=True)


def _invoke(backend, *, model="sonnet", extra_data=None):
    return invoke_llm_subprocess(
        prompt="p", model=model, timeout_sec=5, step_name="bd167", backend=backend,
        extra_data=extra_data, idle_timeout_sec=0,
    )


# ─── AC1: pure pricing ────────────────────────────────────────────────────────


def _u(**kw):
    base = {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "cache_write_1h_tokens": 0}
    base.update(kw)
    return base


TABLE = {"sonnet": dict(RATES)}


def test_AC1_price_usage_prices_each_cache_class_separately():
    assert llm_cost.price_usage(_u(cache_read_tokens=1_000_000), "sonnet", TABLE) == pytest.approx(0.3)
    assert llm_cost.price_usage(_u(cache_write_tokens=1_000_000), "sonnet", TABLE) == pytest.approx(3.75)
    assert llm_cost.price_usage(
        _u(cache_write_tokens=1_000_000, cache_write_1h_tokens=1_000_000), "sonnet", TABLE
    ) == pytest.approx(6.0)
    assert llm_cost.price_usage(_u(tokens_in=1_000_000, tokens_out=1_000_000), "sonnet", TABLE) == pytest.approx(18.0)


def test_AC1_price_usage_mixed_case():
    usage = _u(tokens_in=1000, tokens_out=500, cache_read_tokens=2000,
               cache_write_tokens=500, cache_write_1h_tokens=200)
    assert llm_cost.price_usage(usage, "sonnet", TABLE) == pytest.approx(FULL_COST)


def test_AC1_price_usage_model_lookup_alias_then_family():
    usage = _u(tokens_in=1_000_000)
    assert llm_cost.price_usage(usage, "SONNET", TABLE) == pytest.approx(3.0)
    assert llm_cost.price_usage(usage, "claude-sonnet-4-6", TABLE) == pytest.approx(3.0)


def test_AC1_price_usage_unknown_model_or_no_usage_is_none_never_zero():
    assert llm_cost.price_usage(_u(tokens_in=10), "mystery-model", TABLE) is None
    assert llm_cost.price_usage(_u(tokens_in=10), None, TABLE) is None
    assert llm_cost.price_usage(None, "sonnet", TABLE) is None
    assert llm_cost.price_usage(_u(tokens_in=10), "sonnet", {}) is None


def test_AC1_price_usage_missing_cache_rate_only_matters_for_nonzero_tokens():
    bare = {"sonnet": {"in": 3.0, "out": 15.0}}
    assert llm_cost.price_usage(_u(tokens_in=1_000_000, cache_read_tokens=10), "sonnet", bare) is None
    assert llm_cost.price_usage(_u(tokens_in=1_000_000, cache_write_tokens=10), "sonnet", bare) is None
    assert llm_cost.price_usage(
        _u(tokens_in=1_000_000, cache_write_tokens=10, cache_write_1h_tokens=10), "sonnet", bare
    ) is None
    assert llm_cost.price_usage(_u(tokens_in=1_000_000), "sonnet", bare) == pytest.approx(3.0)


def test_AC1_price_usage_cache_write_alias_is_the_5m_rate():
    table = {"sonnet": {"in": 3.0, "out": 15.0, "cache_write": 3.75}}
    assert llm_cost.price_usage(_u(cache_write_tokens=1_000_000), "sonnet", table) == pytest.approx(3.75)


def test_AC1_normalize_usage_accepts_both_key_dialects():
    expected = {"tokens_in": 1000, "tokens_out": 500, "cache_read_tokens": 2000,
                "cache_write_tokens": 500, "cache_write_1h_tokens": 200}
    assert llm_cost.normalize_usage({"usage": FULL_USAGE}) == expected
    flat = {"tokens_in": 1000, "tokens_out": 500, "cache_read_tokens": 2000,
            "cache_write_tokens": 500, "cache_write_1h_tokens": 200}
    assert llm_cost.normalize_usage(flat) == expected
    assert llm_cost.normalize_usage({"usage": flat}) == expected


def test_AC1_normalize_usage_no_usage_is_none():
    assert llm_cost.normalize_usage({}) is None
    assert llm_cost.normalize_usage({"raw_response": "x"}) is None
    assert llm_cost.normalize_usage({"usage": {}}) is None
    assert llm_cost.normalize_usage(None) is None


def test_AC1_normalize_usage_bad_counts_become_none_and_one_good_count_suffices():
    out = llm_cost.normalize_usage({"usage": {
        "input_tokens": True, "output_tokens": -1, "cache_read_input_tokens": float("nan"),
        "cache_creation_input_tokens": "7", "cache_creation": {"ephemeral_1h_input_tokens": float("inf")},
    }})
    assert out is None
    out = llm_cost.normalize_usage({"usage": {"input_tokens": 12, "output_tokens": True}})
    assert out["tokens_in"] == 12
    assert out["tokens_out"] is None and out["cache_read_tokens"] is None


def test_AC1_normalize_usage_clamps_1h_to_the_write_total():
    out = llm_cost.normalize_usage({"usage": {
        "cache_creation_input_tokens": 100, "cache_creation": {"ephemeral_1h_input_tokens": 500}}})
    assert out["cache_write_tokens"] == 100
    assert out["cache_write_1h_tokens"] == 100


def test_AC1_price_usage_none_token_class_counts_as_zero():
    assert llm_cost.price_usage(_u(tokens_in=1_000_000, tokens_out=None, cache_read_tokens=None,
                                   cache_write_tokens=None, cache_write_1h_tokens=None),
                                "sonnet", TABLE) == pytest.approx(3.0)


def test_AC1_price_usage_clamps_1h_to_the_write_total_itself():
    # 1h (500) > write total (100): only 100 tokens exist, all at the 1h rate, 5m part never negative
    assert llm_cost.price_usage(_u(cache_write_tokens=100, cache_write_1h_tokens=500),
                                "sonnet", TABLE) == pytest.approx(100 * 6.0 / 1e6)
    # missing write total with a 1h part: write total = 1h
    assert llm_cost.price_usage(_u(cache_write_tokens=None, cache_write_1h_tokens=1_000_000),
                                "sonnet", TABLE) == pytest.approx(6.0)


# ─── AC2: one llm_cost_observed per dispatch ─────────────────────────────────


def test_AC2_metered_backend_observation(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-m", {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE})
    res = _invoke("bd167-m")
    assert res.status == "ok"
    obs = _events(log, "llm_cost_observed")
    assert len(obs) == 1
    p = obs[0]
    assert p["backend"] == "bd167-m" and p["model"] == "sonnet"
    assert p["billing_mode"] == "metered" and p["cost_kind"] == "metered"
    assert p["usage_reported"] is True
    assert (p["tokens_in"], p["tokens_out"], p["cache_read_tokens"],
            p["cache_write_tokens"], p["cache_write_1h_tokens"]) == (1000, 500, 2000, 500, 200)
    assert p["derived_cost_usd"] == pytest.approx(FULL_COST)
    assert p["cost_usd"] == pytest.approx(FULL_COST) and p["cost_source"] == "derived"
    assert p["reported_cost_usd"] is None
    assert p["phase"] == "phase_5" and p["cycle"] == 2 and p["step_name"]


def test_AC2_subscription_backend_is_notional(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-s", {"raw_response": "ok", "billing_mode": "subscription", "usage": FULL_USAGE})
    _invoke("bd167-s")
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "subscription" and p["cost_kind"] == "notional"
    assert p["cost_usd"] == pytest.approx(FULL_COST)


def test_AC2_registry_capability_supplies_billing_mode_when_backend_is_silent(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-cap", {"raw_response": "ok", "usage": FULL_USAGE}, caps={"billing:subscription"})
    _invoke("bd167-cap")
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "subscription" and p["cost_kind"] == "notional"


def test_AC2_no_declaration_at_all_is_unknown(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-u", {"raw_response": "ok", "usage": FULL_USAGE})
    _invoke("bd167-u")
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "unknown" and p["cost_kind"] == "unknown"


def test_AC2_backend_without_usage_records_none_costs_and_warns(monkeypatch, tmp_path, caplog):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-nousage", {"raw_response": "ok", "billing_mode": "metered"})
    with caplog.at_level(logging.WARNING):
        res = _invoke("bd167-nousage")
    assert res.status == "ok"
    (p,) = _events(log, "llm_cost_observed")
    assert p["usage_reported"] is False
    for key in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens",
                "cache_write_1h_tokens", "derived_cost_usd", "reported_cost_usd", "cost_usd", "cost_source"):
        assert p[key] is None, key
    prefix = getattr(llm_subprocess, "NO_USAGE_LOG_PREFIX", None)
    assert prefix == "llm cost: backend reported no usage: "
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING
              and r.getMessage().startswith(prefix + "bd167-nousage")]
    assert len(warned) == 1


def test_AC2_error_result_is_observed_like_any_other(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-err", None, status="error")
    res = _invoke("bd167-err")
    assert res.status == "error" and res.error_code == "E_FAKE"
    (p,) = _events(log, "llm_cost_observed")
    assert p["usage_reported"] is False and p["cost_usd"] is None


def test_AC2_invalid_billing_mode_is_not_trusted(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-bad", {"raw_response": "ok", "billing_mode": "free-lunch", "usage": FULL_USAGE})
    _invoke("bd167-bad")
    _fake_backend("bd167-bad2", {"raw_response": "ok", "billing_mode": "free-lunch", "usage": FULL_USAGE},
                  caps={"billing:metered"})
    _invoke("bd167-bad2")
    first, second = _events(log, "llm_cost_observed")
    assert first["billing_mode"] == "unknown"
    assert second["billing_mode"] == "metered"  # falls through to the registry capability


def test_AC2_caller_extra_data_cannot_forge_billing_mode_or_usage(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-forge", {"raw_response": "ok", "usage": FULL_USAGE}, caps={"billing:metered"})
    res = _invoke("bd167-forge", extra_data={
        "billing_mode": "subscription", "usage": {"input_tokens": 1, "output_tokens": 1}})
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "metered"
    assert p["tokens_in"] == 1000
    assert res.data.get("billing_mode") != "subscription"
    assert {"billing_mode", "usage"} <= llm_subprocess.RESERVED_OBSERVATION_FIELDS


def test_AC2_no_run_context_means_no_observation_and_a_context_still_gets_exactly_one(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    _fake_backend("bd167-noctx", {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE})
    res = _invoke("bd167-noctx")  # no run context set
    assert res.status == "ok"
    log = _log(tmp_path)
    _invoke("bd167-noctx")
    assert len(_events(log, "llm_cost_observed")) == 1  # the context-less call left nothing behind


def test_AC2_two_billing_capabilities_resolve_to_unknown(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-two", {"raw_response": "ok", "usage": FULL_USAGE},
                  caps={"billing:metered", "billing:subscription"})
    _invoke("bd167-two")
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "unknown" and p["cost_kind"] == "unknown"


def test_AC2_valid_data_billing_mode_wins_over_the_capability(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-win", {"raw_response": "ok", "billing_mode": "subscription", "usage": FULL_USAGE},
                  caps={"billing:metered"})
    _invoke("bd167-win")
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "subscription"


def test_AC2_pre_dispatch_refusal_emits_no_observation(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    calls = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "ok", "billing_mode": "metered",
                                             "usage": FULL_USAGE},
                          duration_ms=1, step_name=kwargs["step_name"])

    register_backend("bd167-refuse", _impl, manifest_source="git_diff", overwrite=True)
    # a hard gate on a model below the gate floor is refused before any dispatch
    refused = invoke_llm_subprocess(
        prompt="p", model="haiku", timeout_sec=5, step_name="bd167", backend="bd167-refuse",
        hard_gate=True, gate_label="g", idle_timeout_sec=0,
    )
    assert refused.status == "error" and calls == []
    assert _events(log, "llm_cost_observed") == []
    _invoke("bd167-refuse")  # a real dispatch afterwards is the only thing that is observed
    assert len(calls) == 1
    assert len(_events(log, "llm_cost_observed")) == 1


def test_AC2_reported_cost_requires_the_reports_cost_capability(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    data = {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE, "cost_usd": 0.02}
    _fake_backend("bd167-norep", dict(data))
    _fake_backend("bd167-rep2", dict(data), caps={"reports_cost"})
    _invoke("bd167-norep")
    _invoke("bd167-rep2")
    plain, declared = _events(log, "llm_cost_observed")
    assert plain["reported_cost_usd"] is None and plain["cost_source"] == "derived"
    assert declared["reported_cost_usd"] == pytest.approx(0.02)
    assert len(_events(log, "llm_cost_divergence")) == 1  # only the declaring backend can diverge


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -0.5, "0.02"])
def test_AC2_unsafe_reported_cost_is_not_a_reported_cost(monkeypatch, tmp_path, bad):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-badrep", {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE,
                                   "cost_usd": bad}, caps={"reports_cost"})
    _invoke("bd167-badrep")
    (p,) = _events(log, "llm_cost_observed")
    assert p["reported_cost_usd"] is None
    assert p["cost_usd"] == pytest.approx(FULL_COST) and p["cost_source"] == "derived"
    assert _events(log, "llm_cost_divergence") == []


def test_AC2_no_usage_warning_is_logged_once_per_backend(monkeypatch, tmp_path, caplog):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-once-a", {"raw_response": "ok"})
    _fake_backend("bd167-once-b", {"raw_response": "ok"})
    with caplog.at_level(logging.WARNING):
        _invoke("bd167-once-a")
        _invoke("bd167-once-a")
        _invoke("bd167-once-b")
    assert len(_events(log, "llm_cost_observed")) == 3  # every dispatch is still observed
    prefix = getattr(llm_subprocess, "NO_USAGE_LOG_PREFIX", None)
    assert prefix == "llm cost: backend reported no usage: "
    for name, expected in (("bd167-once-a", 1), ("bd167-once-b", 1)):
        warned = [r for r in caplog.records if r.levelno >= logging.WARNING
                  and r.getMessage().startswith(prefix + name)]
        assert len(warned) == expected, name


def test_AC2_reset_backends_clears_the_once_per_backend_dedup(monkeypatch, tmp_path, caplog):
    _pricing(monkeypatch, tmp_path)
    _log(tmp_path)
    prefix = getattr(llm_subprocess, "NO_USAGE_LOG_PREFIX", None)
    assert prefix == "llm cost: backend reported no usage: "
    with caplog.at_level(logging.WARNING):
        _fake_backend("bd167-reset", {"raw_response": "ok"})
        _invoke("bd167-reset")
        reset_backends()
        _fake_backend("bd167-reset", {"raw_response": "ok"})
        _invoke("bd167-reset")
    warned = [r for r in caplog.records if r.getMessage().startswith(prefix + "bd167-reset")]
    assert len(warned) == 2


def test_AC2_only_valid_billing_tokens_count(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend("bd167-vi", {"raw_response": "ok", "usage": FULL_USAGE},
                  caps={"billing:metered", "billing:bogus"})
    _invoke("bd167-vi")
    _fake_backend("bd167-inv", {"raw_response": "ok", "usage": FULL_USAGE}, caps={"billing:bogus"})
    _invoke("bd167-inv")
    valid_plus_invalid, only_invalid = _events(log, "llm_cost_observed")
    assert valid_plus_invalid["billing_mode"] == "metered"
    assert only_invalid["billing_mode"] == "unknown"


# ─── AC2b: billing mode ladder ───────────────────────────────────────────────


def test_AC2b_reported_source_wins_over_env():
    f = llm_cost.billing_mode_from_auth
    assert f("none", {"ANTHROPIC_API_KEY": "sk-x"}) == "subscription"
    assert f("ANTHROPIC_API_KEY", {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}) == "metered"
    assert f("apiKeyHelper", {}) == "metered"


def test_AC2b_bedrock_and_vertex_are_metered_before_the_reported_source():
    f = llm_cost.billing_mode_from_auth
    assert f("none", {"CLAUDE_CODE_USE_BEDROCK": "1"}) == "metered"
    assert f("none", {"CLAUDE_CODE_USE_VERTEX": "1"}) == "metered"
    assert f("none", {"CLAUDE_CODE_USE_BEDROCK": "0"}) == "subscription"


def test_AC2b_env_only_both_ways_and_unknown():
    f = llm_cost.billing_mode_from_auth
    assert f(None, {"ANTHROPIC_API_KEY": "sk-x"}) == "metered"
    assert f(None, {"CLAUDE_CODE_USE_BEDROCK": "1"}) == "metered"
    assert f(None, {"CLAUDE_CODE_USE_VERTEX": "1"}) == "metered"
    assert f(None, {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}) == "subscription"
    assert f("", {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}) == "subscription"
    assert f(None, {}) == "unknown"
    assert f(None, {"ANTHROPIC_API_KEY": ""}) == "unknown"
    assert f(None, {"CLAUDE_CODE_USE_BEDROCK": "0"}) == "unknown"
    assert set(llm_cost.BILLING_MODES) == {"metered", "subscription", "unknown"}
    assert llm_cost.cost_kind("subscription") == "notional"
    assert llm_cost.cost_kind("metered") == "metered"
    assert llm_cost.cost_kind("unknown") == "unknown"


# ─── AC3: divergence ─────────────────────────────────────────────────────────


def test_AC3_divergence_function():
    d = llm_cost.divergence(0.10, 0.12, 10)
    assert d is not None
    assert d["derived_cost_usd"] == 0.10 and d["reported_cost_usd"] == 0.12
    assert d["delta_usd"] == pytest.approx(0.02) and d["threshold_pct"] == 10
    assert llm_cost.divergence(0.10, 0.105, 10) is None
    assert llm_cost.divergence(0.10, None, 10) is None
    assert llm_cost.divergence(None, 0.10, 10) is None
    assert llm_cost.divergence(0.0, 1e-7, 10) is None  # below the absolute floor


def test_AC3_divergence_delta_is_signed_reported_minus_derived():
    over = llm_cost.divergence(0.10, 0.12, 10)
    under = llm_cost.divergence(0.12, 0.10, 10)
    assert over["delta_usd"] == pytest.approx(0.02)
    assert under["delta_usd"] == pytest.approx(-0.02)


def _diverging_run(monkeypatch, tmp_path, name, reported):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    _fake_backend(name, {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE,
                         "cost_usd": reported}, caps={"reports_cost"})
    _invoke(name)
    return log


def test_AC3_beyond_threshold_emits_one_divergence_and_keeps_both_values(monkeypatch, tmp_path):
    log = _diverging_run(monkeypatch, tmp_path, "bd167-d1", 0.05)
    (div,) = _events(log, "llm_cost_divergence")
    assert div["derived_cost_usd"] == pytest.approx(FULL_COST)
    assert div["reported_cost_usd"] == pytest.approx(0.05)
    assert div["delta_usd"] == pytest.approx(0.05 - FULL_COST)  # reported - derived, signed
    assert div["threshold_pct"] == 10
    assert div["backend"] == "bd167-d1" and div["model"] == "sonnet" and div["step_name"]
    (p,) = _events(log, "llm_cost_observed")
    assert p["derived_cost_usd"] == pytest.approx(FULL_COST)
    assert p["reported_cost_usd"] == pytest.approx(0.05)
    assert p["cost_usd"] == pytest.approx(FULL_COST) and p["cost_source"] == "derived"


def test_AC3_within_threshold_emits_no_divergence(monkeypatch, tmp_path):
    log = _diverging_run(monkeypatch, tmp_path, "bd167-d2", 0.0135)
    assert _events(log, "llm_cost_divergence") == []
    (p,) = _events(log, "llm_cost_observed")
    assert p["reported_cost_usd"] == pytest.approx(0.0135)


def test_AC3_env_threshold_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_COST_DIVERGENCE_PCT", "0.1")
    log = _diverging_run(monkeypatch, tmp_path, "bd167-d3", 0.0135)
    (div,) = _events(log, "llm_cost_divergence")
    assert div["threshold_pct"] == pytest.approx(0.1)


@pytest.mark.parametrize("bad", ["abc", "", "0", "-5", "nan", "inf"])
def test_AC3_malformed_env_threshold_falls_back_to_default_10(monkeypatch, tmp_path, bad):
    monkeypatch.setenv("HAL_COST_DIVERGENCE_PCT", bad)
    log = _diverging_run(monkeypatch, tmp_path, "bd167-d4", 0.0135)
    assert _events(log, "llm_cost_divergence") == []
    log2 = _diverging_run(monkeypatch, tmp_path, "bd167-d5", 0.05)
    (div,) = _events(log2, "llm_cost_divergence")
    assert div["threshold_pct"] == 10


# ─── AC4: rollup + A/B export ────────────────────────────────────────────────


def _obs_payload(mode, cost, tin, tout, read, write, write1h, phase="phase_5", usage_reported=True):
    return {"backend": "b", "model": "sonnet", "billing_mode": mode,
            "cost_kind": {"metered": "metered", "subscription": "notional"}.get(mode, "unknown"),
            "usage_reported": usage_reported, "tokens_in": tin, "tokens_out": tout, "cache_read_tokens": read,
            "cache_write_tokens": write, "cache_write_1h_tokens": write1h,
            "derived_cost_usd": cost, "reported_cost_usd": None, "cost_usd": cost,
            "cost_source": "derived" if cost is not None else None,
            "phase": phase, "step_name": "s", "cycle": 1}


def _write_events(path, rows):
    log = EventLog(path)
    for event_type, payload, run_id in rows:
        log.append(event_type, payload, run_id)
    return path


OBS_ROWS = [
    ("llm_cost_observed", _obs_payload("metered", 0.5, 100, 50, 1000, 200, 50), "r1"),
    ("llm_cost_observed", _obs_payload("metered", None, 10, 5, 0, 0, 0), "r1"),
    ("llm_cost_observed", _obs_payload("subscription", 0.25, 200, 20, 5, 0, 0), "r1"),
    ("llm_cost_observed", _obs_payload("metered", 1.0, 7, 3, 0, 0, 0), "r2"),
]


def test_AC4_rollup_by_billing_mode_totals_and_unpriced(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", OBS_ROWS + [
        ("llm_cost_divergence", {"backend": "b"}, "r1"),
        ("llm_cost_divergence", {"backend": "b"}, "r2"),
    ])
    roll = cost_rollup.compute_cost_rollup(path, "r1")
    by = roll["by_billing_mode"]
    assert set(by) == {"metered", "subscription"}
    m = by["metered"]
    assert (m["calls"], m["tokens_in"], m["tokens_out"], m["cache_read_tokens"],
            m["cache_write_tokens"], m["cache_write_1h_tokens"]) == (2, 110, 55, 1000, 200, 50)
    assert m["cost_usd"] == pytest.approx(0.5) and m["unpriced_calls"] == 1 and m["cost_kind"] == "metered"
    s = by["subscription"]
    assert (s["calls"], s["tokens_in"], s["tokens_out"], s["cache_read_tokens"]) == (1, 200, 20, 5)
    assert s["cost_usd"] == pytest.approx(0.25) and s["unpriced_calls"] == 0 and s["cost_kind"] == "notional"
    assert roll["cost_divergences"] == 1


def test_AC4_flat_totals_unchanged_and_untouched_by_new_events(tmp_path):
    legacy = [
        ("subprocess_exited", {"tokens_in": 10, "tokens_out": 5, "cost_usd": 0.1, "phase": "p1", "cycle": 1}, "r1"),
        ("runner_result_consumed", {"tokens_in": 20, "tokens_out": 7, "cost_usd": None, "phase": "p2"}, "r1"),
    ]
    flat_keys = ("run_id", "calls", "tokens_in", "tokens_out", "cost_usd", "by_phase", "by_cycle")
    old = cost_rollup.compute_cost_rollup(_write_events(tmp_path / "a.jsonl", legacy), "r1")
    new = cost_rollup.compute_cost_rollup(_write_events(tmp_path / "b.jsonl", legacy + OBS_ROWS), "r1")
    # hand values (the legacy flat fields, unchanged by this lot)
    assert (old["calls"], old["tokens_in"], old["tokens_out"]) == (2, 30, 12)
    assert old["cost_usd"] == pytest.approx(0.1)
    assert old["by_phase"]["p1"]["tokens_in"] == 10 and old["by_cycle"]["unattributed"]["tokens_out"] == 7
    # the new events never touch the flat fields: old and new serialize identically
    assert json.dumps({k: old[k] for k in flat_keys}, sort_keys=True) == \
        json.dumps({k: new[k] for k in flat_keys}, sort_keys=True)
    assert old["by_billing_mode"] == {} and old["cost_divergences"] == 0
    assert set(new["by_billing_mode"]) == {"metered", "subscription"}


def test_AC4_no_usage_calls_are_not_unpriced_calls(tmp_path):
    rows = [
        ("llm_cost_observed", _obs_payload("metered", 0.5, 100, 50, 0, 0, 0), "r1"),
        ("llm_cost_observed", _obs_payload("metered", None, 10, 5, 0, 0, 0), "r1"),  # usage, no price
        ("llm_cost_observed", _obs_payload("metered", None, None, None, None, None, None,
                                           usage_reported=False), "r1"),  # no usage at all
    ]
    path = _write_events(tmp_path / "ev.jsonl", rows)
    m = cost_rollup.compute_cost_rollup(path, "r1")["by_billing_mode"]["metered"]
    assert m["calls"] == 3 and m["unpriced_calls"] == 1 and m["no_usage_calls"] == 1
    t = cost_rollup.export_ab_ledger(path, run_id="r1")["totals"]
    assert t["unpriced"] == 1 and t["records"] == 3


def _write_raw(path, rows):
    """Rows with explicit `ts` (EventLog stamps now): (ts, event_type, payload, run_id)."""
    with open(path, "w", encoding="utf-8") as f:
        for ts, event_type, payload, run_id in rows:
            f.write(json.dumps({"ts": ts, "run_id": run_id, "event_type": event_type,
                                "payload": payload}) + "\n")
    return path


def test_AC4_export_ab_ledger_since_until_filter_inclusive(tmp_path):
    p = lambda cost: _obs_payload("metered", cost, 1, 1, 0, 0, 0)  # noqa: E731
    path = _write_raw(tmp_path / "ev.jsonl", [
        ("2026-09-30T23:59:59+00:00", "llm_cost_observed", p(1.0), "r1"),
        ("2026-10-01T00:00:00+00:00", "llm_cost_observed", p(2.0), "r1"),
        ("2026-10-01T12:00:00+00:00", "llm_cost_observed", p(4.0), "r1"),
        ("2026-10-02T00:00:00+00:00", "llm_cost_observed", p(8.0), "r1"),
        ("2026-10-02T00:00:01+00:00", "llm_cost_observed", p(16.0), "r1"),
    ])
    doc = cost_rollup.export_ab_ledger(path, run_id="r1", since="2026-10-01T00:00:00+00:00",
                                       until="2026-10-02T00:00:00+00:00")
    assert doc["marker"] == "OK" and doc["totals"]["records"] == 3
    assert doc["totals"]["usd"] == pytest.approx(14.0)
    only_since = cost_rollup.export_ab_ledger(path, since="2026-10-02T00:00:00+00:00")
    assert only_since["totals"]["records"] == 2
    window_empty = cost_rollup.export_ab_ledger(path, since="2027-01-01T00:00:00+00:00")
    assert window_empty["marker"] == "NO_RECORDS_FOR_RUN"


def test_AC4_time_filter_with_real_eventlog_ts_format_and_iso_bounds(tmp_path):
    # EventLog stamps `YYYY-MM-DDTHH:MM:SS.mmmZ`; bounds arrive JS toISOString-style.
    p = lambda cost: _obs_payload("metered", cost, 1, 1, 0, 0, 0)  # noqa: E731
    path = _write_raw(tmp_path / "ev.jsonl", [
        ("2026-10-01T11:59:59.999Z", "llm_cost_observed", p(1.0), "r1"),
        ("2026-10-01T12:00:00.000Z", "llm_cost_observed", p(2.0), "r1"),
        ("2026-10-01T12:00:00.500Z", "llm_cost_observed", p(4.0), "r1"),
        ("2026-10-01T12:00:01.000Z", "llm_cost_observed", p(8.0), "r1"),
        ("2026-10-01T12:00:01.001Z", "llm_cost_observed", p(16.0), "r1"),
        (None, "llm_cost_observed", p(32.0), "r1"),
        ("not-a-time", "llm_cost_observed", p(64.0), "r1"),
    ])
    doc = cost_rollup.export_ab_ledger(path, run_id="r1", since="2026-10-01T12:00:00.000Z",
                                       until="2026-10-01T12:00:01.000Z")
    assert doc["totals"]["records"] == 3 and doc["totals"]["usd"] == pytest.approx(14.0)
    # the same instants spelled +00:00, and a date-only bound (UTC midnight)
    doc2 = cost_rollup.export_ab_ledger(path, run_id="r1", since="2026-10-01T12:00:00+00:00",
                                        until="2026-10-01T12:00:01+00:00")
    assert doc2["totals"]["records"] == 3
    day = cost_rollup.export_ab_ledger(path, run_id="r1", since="2026-10-01")
    assert day["totals"]["records"] == 5  # rows with a missing/unparseable ts are excluded under a bound
    assert cost_rollup.export_ab_ledger(path, run_id="r1")["totals"]["records"] == 7  # no bound: all rows
    real = EventLog(tmp_path / "real.jsonl")
    real.append("llm_cost_observed", p(1.0), "r1")
    assert cost_rollup.export_ab_ledger(real.path, since="2000-01-01T00:00:00.000Z")["totals"]["records"] == 1
    assert cost_rollup.export_ab_ledger(real.path, until="2000-01-02T00:00:00.000Z")["marker"] != "OK"


def test_AC4_unparseable_time_bound_is_a_usage_error(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", OBS_ROWS)
    with pytest.raises(ValueError):
        cost_rollup.export_ab_ledger(path, since="yesterday-ish")
    with pytest.raises(ValueError):
        cost_rollup.export_ab_ledger(path, until="2026-13-45T00:00:00Z")
    proc = _cli("--events", str(path), "--since", "yesterday-ish", "--json")
    assert proc.returncode == 2


def test_AC4_export_ab_ledger_metered_and_notional_usd(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", OBS_ROWS[:3])
    t = cost_rollup.export_ab_ledger(path, run_id="r1")["totals"]
    assert t["metered_usd"] == pytest.approx(0.5) and t["notional_usd"] == pytest.approx(0.25)
    assert t["unknown_usd"] == 0
    assert t["usd"] == pytest.approx(t["metered_usd"] + t["notional_usd"] + t["unknown_usd"]) == pytest.approx(0.75)


def test_AC4_export_ab_ledger_unknown_mode_cost_and_no_usage(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", [
        ("llm_cost_observed", _obs_payload("metered", 0.5, 1, 1, 0, 0, 0), "r1"),
        ("llm_cost_observed", _obs_payload("unknown", 0.125, 1, 1, 0, 0, 0), "r1"),  # priced, mode unknown
        ("llm_cost_observed", _obs_payload("unknown", None, None, None, None, None, None,
                                           usage_reported=False), "r1"),
    ])
    t = cost_rollup.export_ab_ledger(path, run_id="r1")["totals"]
    assert t["unknown_usd"] == pytest.approx(0.125)
    assert t["metered_usd"] == pytest.approx(0.5) and t["notional_usd"] == 0
    assert t["usd"] == pytest.approx(0.625)
    assert t["no_usage"] == 1 and t["unpriced"] == 0 and t["records"] == 3


def test_AC4_export_ab_ledger_totals_and_run_filter(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", [r for r in OBS_ROWS if r[1]["billing_mode"] == "metered"])
    doc = cost_rollup.export_ab_ledger(path, run_id="r1")
    assert doc["schema"] == 1 and doc["marker"] == "OK"
    t = doc["totals"]
    assert t["usd"] == pytest.approx(0.5) and t["unpriced"] == 1 and t["records"] == 2
    assert (t["input"], t["output"], t["cache_read"], t["cache_write"], t["cache_write_1h"]) == (
        110, 55, 1000, 200, 50)
    assert doc["by_billing_mode"]["metered"]["calls"] == 2
    everything = cost_rollup.export_ab_ledger(path)
    assert everything["marker"] == "OK"
    assert everything["totals"]["records"] == 3 and everything["totals"]["usd"] == pytest.approx(1.5)


def test_AC4_export_ab_ledger_markers(tmp_path):
    assert cost_rollup.export_ab_ledger(tmp_path / "missing.jsonl")["marker"] == "EMPTY_CORPUS"
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert cost_rollup.export_ab_ledger(empty, run_id="r1")["marker"] == "EMPTY_CORPUS"
    path = _write_events(tmp_path / "ev.jsonl", OBS_ROWS)
    doc = cost_rollup.export_ab_ledger(path, run_id="r3")
    assert doc["marker"] == "NO_RECORDS_FOR_RUN"
    assert doc["totals"]["records"] == 0 and doc["totals"]["usd"] == 0
    legacy_only = _write_events(tmp_path / "old.jsonl", [("subprocess_exited", {"cost_usd": 1}, "r1")])
    assert cost_rollup.export_ab_ledger(legacy_only, run_id="r1")["marker"] == "NO_RECORDS_FOR_RUN"


def _cli(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.lib.cost_rollup", *args],
        cwd=str(ENGINE_DIR), env=env, capture_output=True, text=True, timeout=60,
    )


def test_AC4_cli_prints_document_exit_0_and_ignores_unknown_args(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", OBS_ROWS)
    proc = _cli("--events", str(path), "--run-id", "r1", "--json",
                "--branch", "main", "--since", "2026-01-01", "--whatever")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["marker"] == "OK" and doc["schema"] == 1 and doc["totals"]["records"] == 3
    assert doc["totals"]["usd"] == pytest.approx(0.75)


def test_AC4_cli_honours_run_id_since_until(tmp_path):
    p = lambda cost: _obs_payload("metered", cost, 1, 1, 0, 0, 0)  # noqa: E731
    path = _write_raw(tmp_path / "ev.jsonl", [
        ("2026-10-01T00:00:00+00:00", "llm_cost_observed", p(1.0), "r1"),
        ("2026-10-02T00:00:00+00:00", "llm_cost_observed", p(2.0), "r1"),
        ("2026-10-02T00:00:00+00:00", "llm_cost_observed", p(4.0), "r2"),
    ])
    proc = _cli("--events", str(path), "--run-id", "r1", "--since", "2026-10-02T00:00:00+00:00",
                "--until", "2026-10-03T00:00:00+00:00", "--json", "--branch", "main")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["totals"]["records"] == 1 and doc["totals"]["usd"] == pytest.approx(2.0)


def test_AC4_cli_exit_3_when_not_ok(tmp_path):
    path = _write_events(tmp_path / "ev.jsonl", OBS_ROWS)
    proc = _cli("--events", str(path), "--run-id", "nope", "--json", "--branch", "x")
    assert proc.returncode == 3
    assert json.loads(proc.stdout)["marker"] == "NO_RECORDS_FOR_RUN"
    proc = _cli("--events", str(tmp_path / "missing.jsonl"), "--json")
    assert proc.returncode == 3
    assert json.loads(proc.stdout)["marker"] == "EMPTY_CORPUS"


# ─── AC5: degrade paths ──────────────────────────────────────────────────────


def test_AC5_pricing_file_missing_costs_none_but_observation_emitted(monkeypatch, tmp_path):
    monkeypatch.setattr(config_provider, "models_config_path", lambda: tmp_path / "does-not-exist.json")
    log = _log(tmp_path)
    _fake_backend("bd167-nopricing", {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE})
    res = _invoke("bd167-nopricing")
    assert res.status == "ok"
    (p,) = _events(log, "llm_cost_observed")
    assert p["usage_reported"] is True and p["tokens_in"] == 1000 and p["cache_read_tokens"] == 2000
    assert p["derived_cost_usd"] is None and p["cost_usd"] is None and p["cost_source"] is None
    assert p["billing_mode"] == "metered"


def test_AC5_pricing_file_malformed_costs_none(monkeypatch, tmp_path):
    bad = tmp_path / "models.json"
    bad.write_text("{not json")
    monkeypatch.setattr(config_provider, "models_config_path", lambda: bad)
    log = _log(tmp_path)
    _fake_backend("bd167-badpricing", {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE})
    _invoke("bd167-badpricing")
    (p,) = _events(log, "llm_cost_observed")
    assert p["derived_cost_usd"] is None and p["cost_usd"] is None


def test_AC5_reported_cost_is_the_fallback_when_no_derivation_possible(monkeypatch, tmp_path):
    monkeypatch.setattr(config_provider, "models_config_path", lambda: tmp_path / "does-not-exist.json")
    log = _log(tmp_path)
    _fake_backend("bd167-rep", {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE,
                                "cost_usd": 0.02}, caps={"reports_cost"})
    _invoke("bd167-rep")
    (p,) = _events(log, "llm_cost_observed")
    assert p["derived_cost_usd"] is None
    assert p["cost_usd"] == pytest.approx(0.02) and p["cost_source"] == "reported"
    assert _events(log, "llm_cost_divergence") == []


class _LogFailingOnCost(EventLog):
    attempts = 0

    def append(self, event_type, payload, run_id=None):
        if event_type.startswith("llm_cost_"):
            type(self).attempts += 1
            raise OSError("disk full")
        return super().append(event_type, payload, run_id)


def test_AC5_failing_event_append_leaves_the_step_result_unchanged(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    _LogFailingOnCost.attempts = 0
    _log(tmp_path, log=_LogFailingOnCost(tmp_path / "e.jsonl"))
    data = {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE}
    _fake_backend("bd167-failappend", dict(data))
    res = _invoke("bd167-failappend")
    assert _LogFailingOnCost.attempts >= 1, "the cost observation was never attempted"
    assert res.status == "ok" and res.error is None
    assert res.data == data


def test_AC5_exception_inside_the_cost_step_is_logged_and_result_unchanged(monkeypatch, tmp_path, caplog):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    data = {"raw_response": "ok", "billing_mode": "metered", "usage": FULL_USAGE}
    _fake_backend("bd167-explode", dict(data))

    def _boom(*a, **kw):
        raise RuntimeError("kaboom-bd167")

    monkeypatch.setattr(_lc(), "normalize_usage", _boom)  # A2 seam
    with caplog.at_level(logging.WARNING):
        res = _invoke("bd167-explode")
    assert res.status == "ok" and res.error is None
    assert res.data == data
    assert _events(log, "llm_cost_observed") == []  # nothing is emitted on a failed cost step
    prefix = getattr(llm_subprocess, "COST_STEP_FAILED_LOG_PREFIX", None)
    assert prefix == "llm cost observation failed: "
    failed = [r for r in caplog.records if r.levelno == logging.WARNING
              and r.getMessage().startswith(prefix)]
    assert len(failed) == 1 and "kaboom-bd167" in failed[0].getMessage()


# ─── AC6: adapters, one metered and one subscription, end to end ─────────────


@contextmanager
def _ok_urlopen_ctx(body):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__ = lambda s: resp
    resp.__exit__ = MagicMock(return_value=False)
    yield resp


def test_AC6_anthropic_api_is_metered_and_reports_usage(monkeypatch, tmp_path):
    from bytedigger_engine.lib.reference_backends import anthropic_api  # noqa: PLC0415

    _pricing(monkeypatch, tmp_path)
    anthropic_api.register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    log = _log(tmp_path)
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 2000,
             "cache_creation_input_tokens": 500}
    body = {"content": [{"type": "text", "text": "PONG"}], "usage": usage}

    def _urlopen(request, timeout=None):
        return _ok_urlopen_ctx(body)

    with patch("urllib.request.urlopen", _urlopen):
        res = _invoke("anthropic-api")
    assert res.status == "ok", res.error
    assert res.data["billing_mode"] == "metered"
    assert res.data["usage"]["cache_read_input_tokens"] == 2000
    assert res.data["usage"]["input_tokens"] == 1000
    (p,) = _events(log, "llm_cost_observed")
    assert p["backend"] == "anthropic-api" and p["billing_mode"] == "metered" and p["cost_kind"] == "metered"
    # (1000*3 + 500*15 + 2000*0.3 + 500*3.75) / 1e6 = 12975 / 1e6
    assert p["cost_usd"] == pytest.approx(0.012975)
    assert "billing:metered" in llm_subprocess._BACKEND_CAPABILITIES["anthropic-api"]


def _claude_stream(api_key_source, total_cost):
    init = {"type": "system", "subtype": "init", "apiKeySource": api_key_source}
    result = {"type": "result", "subtype": "success", "result": "OK", "usage": FULL_USAGE,
              "total_cost_usd": total_cost, "duration_ms": 1}
    return json.dumps(init) + "\n" + json.dumps(result) + "\n"


def _run_claude_subprocess(api_key_source):
    stream = _claude_stream(api_key_source, FULL_COST)

    def _popen(argv, **kwargs):
        proc = MagicMock()
        proc.pid = 1
        proc.returncode = 0
        proc.stdout = io.StringIO(stream)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(stream, ""))
        return proc

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=_popen):
        return _invoke("claude-subprocess")


def test_AC6_claude_subprocess_subscription_from_init_api_key_source(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-must-not-win")
    log = _log(tmp_path)
    res = _run_claude_subprocess("none")
    assert res.status == "ok", res.error
    assert res.data["billing_mode"] == "subscription"
    assert res.data["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] == 200
    assert res.data["usage"]["cache_read_input_tokens"] == 2000
    (p,) = _events(log, "llm_cost_observed")
    assert p["backend"] == "claude-subprocess"
    assert p["billing_mode"] == "subscription" and p["cost_kind"] == "notional"
    assert p["cache_write_1h_tokens"] == 200
    assert p["derived_cost_usd"] == pytest.approx(FULL_COST)
    assert p["reported_cost_usd"] == pytest.approx(FULL_COST)
    assert _events(log, "llm_cost_divergence") == []


def test_AC6_claude_subprocess_metered_from_init_api_key_source(monkeypatch, tmp_path):
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    res = _run_claude_subprocess("ANTHROPIC_API_KEY")
    assert res.status == "ok", res.error
    assert res.data["billing_mode"] == "metered"
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "metered" and p["cost_kind"] == "metered"
    assert p["cost_usd"] == pytest.approx(FULL_COST) and p["cost_source"] == "derived"
    assert res.data["cost_usd"] == pytest.approx(FULL_COST)  # A5: last result event total_cost_usd


def test_AC6_claude_subprocess_legacy_tokens_helper_is_unchanged():
    """A6 guard: `_tokens_and_cost_from_events` keeps its 2-tuple and `tokens` shape."""
    events = [{"type": "system", "subtype": "init", "apiKeySource": "none"},
              {"type": "result", "subtype": "success", "result": "OK", "usage": FULL_USAGE,
               "total_cost_usd": FULL_COST}]
    tokens, cost = llm_subprocess._tokens_and_cost_from_events(events)
    assert tokens == {"input": 1000, "output": 500, "cache_read": 2000, "cache_write": 500}
    assert cost == pytest.approx(FULL_COST)


def test_AC6_reports_cost_capability_registry():
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    caps = llm_subprocess._BACKEND_CAPABILITIES
    assert "reports_cost" in caps["claude-subprocess"]
    assert "reports_cost" not in caps["claude-in-session"]
    sdk = types_module_fake_sdk()
    try:
        agent_sdk.register()
        assert "reports_cost" in llm_subprocess._BACKEND_CAPABILITIES["agent-sdk"]
    finally:
        sdk()


# ─── AC6: agent-sdk (A3) ─────────────────────────────────────────────────────


def types_module_fake_sdk():
    """Install a bare fake `claude_agent_sdk` so agent_sdk.register() is allowed; returns the undo."""
    import types as _types  # noqa: PLC0415

    previous = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = _types.ModuleType("claude_agent_sdk")

    def _undo():
        if previous is None:
            sys.modules.pop("claude_agent_sdk", None)
        else:
            sys.modules["claude_agent_sdk"] = previous

    return _undo


@pytest.fixture
def fake_agent_sdk(monkeypatch, tmp_path):
    """A fake `claude_agent_sdk` (async-generator query, SystemMessage with a `data` dict) and a real git repo."""
    import importlib  # noqa: PLC0415
    import types as _types  # noqa: PLC0415
    from dataclasses import dataclass  # noqa: PLC0415

    @dataclass
    class SystemMessage:
        subtype: str
        data: dict

    @dataclass
    class ResultMessage:
        subtype: str = "success"
        duration_ms: int = 1
        is_error: bool = False
        num_turns: int = 1
        session_id: "str | None" = "sid-bd167"
        total_cost_usd: "float | None" = None
        usage: "dict | None" = None
        result: "str | None" = "done"

    state = {"api_key_source": "none"}

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def query(*, prompt, options):
        yield SystemMessage("init", {"apiKeySource": state["api_key_source"]})
        yield ResultMessage(usage=dict(FULL_USAGE), total_cost_usd=FULL_COST)

    mod = _types.ModuleType("claude_agent_sdk")
    mod.query, mod.ClaudeAgentOptions = query, ClaudeAgentOptions
    mod.ResultMessage, mod.SystemMessage = ResultMessage, SystemMessage
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    modname = "bytedigger_engine.lib.reference_backends.agent_sdk"
    sys.modules.pop(modname, None)
    backend_mod = importlib.import_module(modname)
    backend_mod.register()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=T",
                    "commit", "--allow-empty", "-q", "-m", "init"], check=True, capture_output=True)
    yield state, repo
    sys.modules.pop(modname, None)


def _run_agent_sdk(repo):
    return _invoke("agent-sdk", extra_data={"workspace_root": str(repo)})


def test_AC6_agent_sdk_subscription_from_init_system_message(monkeypatch, tmp_path, fake_agent_sdk):
    state, repo = fake_agent_sdk
    state["api_key_source"] = "none"
    _pricing(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-must-not-win")
    log = _log(tmp_path)
    res = _run_agent_sdk(repo)
    assert res.status == "ok", res.error
    assert res.data["billing_mode"] == "subscription"
    assert res.data["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] == 200
    (p,) = _events(log, "llm_cost_observed")
    assert p["backend"] == "agent-sdk"
    assert p["billing_mode"] == "subscription" and p["cost_kind"] == "notional"
    assert p["derived_cost_usd"] == pytest.approx(FULL_COST)
    assert p["reported_cost_usd"] == pytest.approx(FULL_COST)
    assert _events(log, "llm_cost_divergence") == []


def test_AC6_agent_sdk_metered_from_init_system_message(monkeypatch, tmp_path, fake_agent_sdk):
    state, repo = fake_agent_sdk
    state["api_key_source"] = "ANTHROPIC_API_KEY"
    _pricing(monkeypatch, tmp_path)
    log = _log(tmp_path)
    res = _run_agent_sdk(repo)
    assert res.status == "ok", res.error
    assert res.data["billing_mode"] == "metered"
    (p,) = _events(log, "llm_cost_observed")
    assert p["billing_mode"] == "metered" and p["cost_kind"] == "metered"
    assert p["cost_usd"] == pytest.approx(FULL_COST) and p["cost_source"] == "derived"


# ─── AC6: pydantic backends (A4) ─────────────────────────────────────────────


def _run_usage(**attrs):
    import types as _types  # noqa: PLC0415

    return _types.SimpleNamespace(usage=_types.SimpleNamespace(**attrs))


def test_AC6_pydantic_usage_reader_subtracts_cache_from_input_tokens():
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    # NAME PINNED BY THIS RED (spec A4 says "the shared reader" without naming it).
    reader = getattr(pydantic_openai, "_usage_for_ledger", None)
    assert reader is not None, "pydantic_openai._usage_for_ledger (the shared RunUsage reader) is missing"
    got = reader(_run_usage(input_tokens=1500, output_tokens=40, cache_read_tokens=1000,
                            cache_write_tokens=300))
    assert got == {"tokens_in": 200, "tokens_out": 40, "cache_read_tokens": 1000, "cache_write_tokens": 300}
    assert _lc().normalize_usage(got)["tokens_in"] == 200  # the flat dialect round-trips


def test_AC6_pydantic_usage_reader_missing_and_overlarge_cache():
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    reader = getattr(pydantic_openai, "_usage_for_ledger", None)
    assert reader is not None, "pydantic_openai._usage_for_ledger (the shared RunUsage reader) is missing"
    plain = reader(_run_usage(input_tokens=50, output_tokens=5))
    assert (plain["tokens_in"], plain["tokens_out"], plain["cache_read_tokens"],
            plain["cache_write_tokens"]) == (50, 5, 0, 0)
    clamped = reader(_run_usage(input_tokens=100, output_tokens=1, cache_read_tokens=80, cache_write_tokens=80))
    assert clamped["tokens_in"] == 0
    bare = reader(_run_usage())
    assert bare["tokens_in"] is None and bare["tokens_out"] is None


def test_AC6_pydantic_backends_declare_their_billing_capability(monkeypatch):
    import importlib  # noqa: PLC0415
    import types as _types  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "pydantic_ai", _types.ModuleType("pydantic_ai"))
    monkeypatch.setitem(sys.modules, "anthropic", _types.ModuleType("anthropic"))
    openai_mod = importlib.import_module("bytedigger_engine.lib.reference_backends.pydantic_openai")
    anthropic_mod = importlib.import_module("bytedigger_engine.lib.reference_backends.pydantic_anthropic")
    openai_mod.register()
    anthropic_mod.register()
    caps = llm_subprocess._BACKEND_CAPABILITIES
    assert "billing:metered" in caps["pydantic-openai"]
    assert "billing:subscription" in caps["pydantic-anthropic"]
    assert "billing:metered" not in caps["pydantic-anthropic"]
