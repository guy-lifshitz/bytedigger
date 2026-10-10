"""Live test for bd#83: one real call through the openai-compat backend to a local
Ollama server (spec section 3.4, AC14).

Marker `live_ollama`: deselected by default (see `addopts` in pyproject.toml).
Run explicitly with:  python3 -m pytest -m live_ollama engine_py/tests/test_bd83_openai_compat_live_ollama.py

Skips (never fails) when the server is unreachable. The default base URL applies to
this test only; HAL_OPENAI_BASE_URL overrides it.
"""
from __future__ import annotations

import importlib
import os
import urllib.error
import urllib.request

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, reset_backends

pytestmark = pytest.mark.live_ollama

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_MODEL = "gemma4:26b"


def _base_url() -> str:
    return (os.environ.get("HAL_OPENAI_BASE_URL") or "").strip() or _DEFAULT_BASE_URL


def _server_reachable(base: str) -> bool:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/models", timeout=3):
            return True
    except urllib.error.HTTPError:
        return True  # the server answered
    except Exception:  # noqa: BLE001 - refused, DNS, timeout, bad scheme: all mean unreachable
        return False


def test_live_ollama_call_reports_text_tokens_and_wall_time(monkeypatch, tmp_path):
    base = _base_url()
    if not _server_reachable(base):
        pytest.skip(f"no OpenAI-compatible server reachable at {base}")
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", base)
    monkeypatch.delenv("HAL_OPENAI_API_KEY", raising=False)
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-bd83-live", step_name="live", phase="phase_6", cycle=1)
    try:
        module = importlib.import_module("bytedigger_engine.lib.reference_backends.openai_compat")
        assert module.register() is True
        result = invoke_llm_subprocess(
            prompt="Reply with the single word: pong",
            model=_MODEL,
            timeout_sec=600,
            step_name="bd83-live",
            backend="openai-compat",
            idle_timeout_sec=0,
        )
        events = [e["payload"] for e in log.read_all() if e["event_type"] == "llm_cost_observed"]
    finally:
        telemetry_ctx.clear_current_run()
        reset_backends()
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert isinstance(result.data["raw_response"], str) and result.data["raw_response"].strip()
    (payload,) = events
    assert payload["tokens_in"] and payload["tokens_in"] > 0
    assert payload["tokens_out"] and payload["tokens_out"] > 0
    assert payload["wall_ms"] > 0
