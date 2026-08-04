"""RED for GH514(3) — E_LLM_SPEND_LIMIT: spend/usage-limit exit is env-state, not code-fail.

Spec: SHARED/memory/Decisions/2026-07-10_EABE6D3A_gh514_spend_limit_classify_spec.md
Agreement: EABE6D3A-9686-4B98-879D-BB9AA74454CF

AC1-AC5 exercise the real prod path (_invoke_subprocess via invoke_llm_subprocess)
using a real subprocess (python3 -c ...) registered through the lib.llm_provider
injection seam (test_gh451_llm_provider_port.py pattern) — NOT a stub backend
that would bypass the classifier under test. AC6-AC11 defer imports of the
not-yet-existing lib.env_limit module (and the not-yet-registered error code /
flag) to inside the test bodies so the file COLLECTS cleanly and FAILS at
assert time (workflows.md §1q extension, per spec Out-of-Role block).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import pytest  # noqa: E402
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, reset_backends  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine import llm_subprocess  # noqa: E402


# ---------------------------------------------------------------------------
# §1i autouse teardown: restore provider registry + backend singleton + telemetry
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    from bytedigger_engine.lib.llm_provider import reset_providers

    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()
    reset_providers()


# ---------------------------------------------------------------------------
# Real-subprocess provider fixture (mirrors test_gh451_llm_provider_port.py
# AC4's register_provider(stub) + HAL_LLM_PROVIDER seam). Spawns an actual
# python3 process so _invoke_subprocess's real exit_code!=0 branch runs
# unmocked — the UUT under test is NOT stubbed (§1l).
# ---------------------------------------------------------------------------

def _register_fake_claude(monkeypatch, *, stderr_text: str = "", stdout_text: str = "", exit_code: int = 1) -> None:
    from bytedigger_engine.lib.llm_provider import ProviderSpec, register_provider

    script = (
        "import sys\n"
        f"sys.stderr.write({stderr_text!r})\n"
        f"sys.stdout.write({stdout_text!r})\n"
        f"sys.exit({exit_code})\n"
    )
    argv = [sys.executable, "-c", script]
    provider = ProviderSpec(
        name="gh514-fake-claude",
        build_argv=lambda model: argv,
        stream_flags=(),
        parse_result=lambda events: None,
        model_rank={"m": 0},
        model_family=lambda model: None,
        default_gate_floor="m",
    )
    register_provider(provider)
    monkeypatch.setenv("HAL_LLM_PROVIDER", "gh514-fake-claude")


def _invoke(**overrides) -> object:
    kwargs = dict(
        prompt="hi",
        model="m",
        timeout_sec=10,
        step_name="step",
        backend="claude-subprocess",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    kwargs.update(overrides)
    return invoke_llm_subprocess(**kwargs)


# ---------------------------------------------------------------------------
# AC1 — spend-limit marker in stderr classifies E_LLM_SPEND_LIMIT
# ---------------------------------------------------------------------------

def test_ac1_spend_limit_marker_in_stderr_classifies_env_limit(monkeypatch):
    _register_fake_claude(
        monkeypatch,
        stderr_text="You've hit your monthly spend limit",
        exit_code=3,
    )
    r = _invoke()
    assert r.status == "error"
    assert r.error_code == "E_LLM_SPEND_LIMIT"
    assert r.recoverable is True
    assert r.data["env_limit_marker"] == "spend limit"
    assert r.data["pausable"] is True
    assert r.data["exit_code"] == 3


# ---------------------------------------------------------------------------
# AC2 — usage-limit-reached marker (Max-plan CLI form)
# ---------------------------------------------------------------------------

def test_ac2_usage_limit_reached_marker_classifies_env_limit(monkeypatch):
    _register_fake_claude(
        monkeypatch,
        stderr_text="Claude AI usage limit reached|1783658948",
        exit_code=1,
    )
    r = _invoke()
    assert r.error_code == "E_LLM_SPEND_LIMIT"
    assert r.data["env_limit_marker"] == "usage limit reached"


# ---------------------------------------------------------------------------
# AC3 — unrelated stderr regression pin: stays E_LLM_EXIT
# ---------------------------------------------------------------------------

def test_ac3_unrelated_stderr_stays_e_llm_exit(monkeypatch):
    _register_fake_claude(
        monkeypatch,
        stderr_text="Traceback (most recent call last):\nValueError: boom",
        exit_code=1,
    )
    r = _invoke()
    assert r.error_code == "E_LLM_EXIT"


# ---------------------------------------------------------------------------
# AC4 — marker present only in stdout (not stderr) still classifies
# ---------------------------------------------------------------------------

def test_ac4_marker_in_stdout_only_classifies_env_limit(monkeypatch):
    _register_fake_claude(
        monkeypatch,
        stdout_text="credit balance is too low",
        stderr_text="",
        exit_code=1,
    )
    r = _invoke()
    assert r.error_code == "E_LLM_SPEND_LIMIT"


# ---------------------------------------------------------------------------
# AC5 — kill-switch HAL_ENV_LIMIT_CLASSIFY=0 forces legacy E_LLM_EXIT
# ---------------------------------------------------------------------------

def test_ac5_kill_switch_disables_classification(monkeypatch):
    monkeypatch.setenv("HAL_ENV_LIMIT_CLASSIFY", "0")
    _register_fake_claude(
        monkeypatch,
        stderr_text="You've hit your monthly spend limit",
        exit_code=3,
    )
    r = _invoke()
    assert r.error_code == "E_LLM_EXIT"


# ---------------------------------------------------------------------------
# AC6 — error_codes.py registers E_LLM_SPEND_LIMIT + check() passes
# ---------------------------------------------------------------------------

def test_ac6_error_codes_registers_e_llm_spend_limit():
    from bytedigger_engine import error_codes

    assert "E_LLM_SPEND_LIMIT" in error_codes.ERROR_CODES
    unregistered, dead = error_codes.check(HERE.parent)
    assert "E_LLM_SPEND_LIMIT" not in unregistered
    assert "E_LLM_SPEND_LIMIT" not in dead


# ---------------------------------------------------------------------------
# AC7 — dbos_setup evicts on E_LLM_SPEND_LIMIT
# ---------------------------------------------------------------------------

def test_ac7_dbos_setup_evicts_on_e_llm_spend_limit():
    from bytedigger_engine.lib import dbos_setup

    assert "E_LLM_SPEND_LIMIT" in dbos_setup._EVICT_ON_RETRY_CODES


# ---------------------------------------------------------------------------
# AC8 — directed_repair markers are the SAME object as env_limit's (§1g)
# ---------------------------------------------------------------------------

def test_ac8_directed_repair_markers_single_source_identity():
    from bytedigger_engine.lib import directed_repair
    from bytedigger_engine.lib.env_limit import ENV_LIMIT_MARKERS

    assert directed_repair._MODEL_UNAVAILABLE_MARKERS is ENV_LIMIT_MARKERS
    assert "spend limit" in directed_repair._MODEL_UNAVAILABLE_MARKERS


# ---------------------------------------------------------------------------
# AC9 — detect_env_limit() pure-function edge cases: no args / None / non-str
# ---------------------------------------------------------------------------

def test_ac9_detect_env_limit_empty_and_non_str_inputs_return_none():
    from bytedigger_engine.lib.env_limit import detect_env_limit

    assert detect_env_limit() is None
    assert detect_env_limit(None, 42, "") is None


# ---------------------------------------------------------------------------
# AC10 — detect_env_limit() case-insensitive match, first-marker order
# ---------------------------------------------------------------------------

def test_ac10_detect_env_limit_case_insensitive_match():
    from bytedigger_engine.lib.env_limit import detect_env_limit

    assert detect_env_limit("XX Rate_Limit_Error yy") == "rate_limit_error"


# ---------------------------------------------------------------------------
# AC11 — flags_catalog.FLAGS declares HAL_ENV_LIMIT_CLASSIFY
# ---------------------------------------------------------------------------

def test_ac11_flags_catalog_declares_env_limit_classify():
    from bytedigger_engine import flags_catalog

    assert "HAL_ENV_LIMIT_CLASSIFY" in flags_catalog.FLAGS
