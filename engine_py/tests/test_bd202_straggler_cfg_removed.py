"""RED tests for bd#202: the deprecated `straggler_cfg` kwarg is removed.

Frozen spec: docs/decisions/2026-10-11-bd202-remove-straggler-cfg.md (AC1-AC8).

Chokepoint: `invoke_llm_subprocess` (single public entry) and the dispatcher that
hands kwargs to every registered backend. Seams: `register_backend` fakes (cleaned by
`reset_backends`), a recording wrapper around the real `claude-subprocess` handler
(Popen + `_stream_read_events` mocked, as test_bd89_p3b1b_i), and a recording wrapper
around the real `anthropic-api` reference backend (urlopen faked, as
test_2A6986ED). No sys.path mutation, no conftest import. No time-dependent or
singleton-resource fixtures (workflows.md section 1i not triggered).
"""
from __future__ import annotations

import functools
import importlib.util
import inspect
import json
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog

TESTS_DIR = Path(__file__).resolve().parent
ENGINE_PY = TESTS_DIR.parent
REPO = ENGINE_PY.parent
LLM_SRC = ENGINE_PY / "bytedigger_engine" / "llm_subprocess.py"
EXAMPLE = REPO / "examples" / "library" / "custom_backend.py"

FAKE = "bd202-fake"


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


@pytest.fixture
def real_log(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD202", step_name="bd202", phase="phase_6",
    )
    return log


def _records(log: EventLog, event_type: str) -> list[dict]:
    return [e for e in log.read_all() if e["event_type"] == event_type]


def _call(backend: str, **kw):
    base = dict(prompt="p", model="sonnet", timeout_sec=5, step_name="bd202-step", backend=backend)
    base.update(kw)
    return llm_subprocess.invoke_llm_subprocess(**base)


def _params(fn) -> dict:
    return dict(inspect.signature(fn).parameters)


def _recording(real, seen: list) -> object:
    """Wrap a real backend: record the kwargs the dispatcher passes, then delegate."""
    @functools.wraps(real)
    def _w(**kwargs):
        seen.append(dict(kwargs))
        return real(**kwargs)
    return _w


# --- AC1 --------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, {"reviews_dir": "/x", "expected_n": 2}], ids=["none", "dict"])
def test_ac1_kwarg_raises_typeerror(value):
    """AC1: passing straggler_cfg (None or dict) -> TypeError.

    Pre-GREEN fail: the kwarg is still accepted.
    """
    llm_subprocess.register_backend(
        FAKE, lambda **kw: StepResult(status="ok", data={}, duration_ms=0, step_name="s"),
        manifest_source="harness_tool_record",
    )
    with pytest.raises(TypeError):
        _call(FAKE, straggler_cfg=value)


def test_ac1_not_in_signature():
    """AC1: 'straggler_cfg' is not in invoke_llm_subprocess's signature."""
    assert "straggler_cfg" not in _params(llm_subprocess.invoke_llm_subprocess)


# --- AC2 --------------------------------------------------------------------

def test_ac2_protocol_and_handler_drop_param():
    """AC2: not a parameter of LLMBackend.__call__ nor of the claude-subprocess handler."""
    assert "straggler_cfg" not in _params(llm_subprocess.LLMBackend.__call__)
    assert "straggler_cfg" not in _params(llm_subprocess._invoke_subprocess)
    assert "straggler_cfg" not in _params(llm_subprocess._BACKENDS["claude-subprocess"])


@pytest.mark.parametrize("mod,fn", [
    ("agent_sdk", "agent_sdk_backend"),
    ("anthropic_api", "anthropic_api_backend"),
    ("openai_compat", "openai_compat_backend"),
    ("pydantic_anthropic", "pydantic_anthropic_backend"),
    ("pydantic_openai", "pydantic_openai_backend"),
])
def test_ac2_reference_backends_drop_param(mod, fn):
    """AC2: none of the five reference backends declares straggler_cfg."""
    m = importlib.import_module(f"bytedigger_engine.lib.reference_backends.{mod}")
    assert "straggler_cfg" not in _params(getattr(m, fn))


# --- AC3 --------------------------------------------------------------------

def test_ac3_dispatcher_does_not_pass_kwarg_to_fake_backend():
    """AC3: fake backend with no straggler_cfg in its signature is callable; kwarg never recorded.

    Pre-GREEN fail: the dispatcher passes straggler_cfg=None -> TypeError in the fake.
    """
    recorded: list[dict] = []

    def fake(*, prompt, model, timeout_sec, step_name, extra_data, allowed_tools,
             run_ctx, hard_gate, gate_label, idle_timeout_sec, stable_prefix: str = ""):
        recorded.append({"prompt": prompt, "step_name": step_name})
        return StepResult(status="ok", data={"raw_response": "r"}, duration_ms=0, step_name=step_name)

    llm_subprocess.register_backend(FAKE, fake, manifest_source="harness_tool_record")
    result = _call(FAKE)
    assert result.status == "ok", (result.status, result.error_code, result.error)
    assert len(recorded) == 1


def test_ac3_recording_spy_sees_no_straggler_key():
    """AC3: a **kwargs spy records the exact kwargs; 'straggler_cfg' is absent."""
    seen: list[dict] = []

    def spy(**kwargs):
        seen.append(dict(kwargs))
        return StepResult(status="ok", data={"raw_response": "r"}, duration_ms=0,
                          step_name=kwargs["step_name"])

    llm_subprocess.register_backend(FAKE, spy, manifest_source="harness_tool_record")
    assert _call(FAKE).status == "ok"
    assert len(seen) == 1 and "straggler_cfg" not in seen[0], sorted(seen[0])


# --- AC4 --------------------------------------------------------------------

def test_ac4_subscription_path_claude_subprocess_never_sees_kwarg():
    """AC4: real claude-subprocess handler (Popen mocked) gets no straggler_cfg and works."""
    result_event = {
        "type": "result", "subtype": "success", "result": "ok",
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "total_cost_usd": 0.001, "duration_ms": 10,
    }
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = 0
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.poll = MagicMock(return_value=0)

    seen: list[dict] = []
    real = llm_subprocess._BACKENDS["claude-subprocess"]
    wrapper = _recording(real, seen)

    with patch.dict(llm_subprocess._BACKENDS, {"claude-subprocess": wrapper}), \
         patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc), \
         patch("bytedigger_engine.llm_subprocess._stream_read_events",
               side_effect=lambda *a, **kw: (False, "", "", [result_event], False, False)):
        result = _call("claude-subprocess")
    assert result.status == "ok", (result.status, result.error_code)
    assert len(seen) == 1 and "straggler_cfg" not in seen[0], sorted(seen[0])


@contextmanager
def _fake_urlopen_ctx(request, timeout=None):
    resp = MagicMock()
    resp.read.return_value = json.dumps({"content": [{"type": "text", "text": "PONG"}]}).encode()
    yield resp


def test_ac4_api_token_path_anthropic_api_never_sees_kwarg(monkeypatch):
    """AC4: real anthropic-api reference backend (urlopen faked) gets no straggler_cfg and works."""
    from bytedigger_engine.lib.reference_backends import anthropic_api  # noqa: PLC0415

    anthropic_api.register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    seen: list[dict] = []
    real = llm_subprocess._BACKENDS["anthropic-api"]
    monkeypatch.setitem(llm_subprocess._BACKENDS, "anthropic-api", _recording(real, seen))

    with patch("urllib.request.urlopen", _fake_urlopen_ctx):
        result = _call("anthropic-api", model="haiku")
    assert result.status == "ok", (result.status, result.error_code, result.error)
    assert result.data["raw_response"] == "PONG"
    assert len(seen) == 1 and "straggler_cfg" not in seen[0], sorted(seen[0])


# --- AC5 --------------------------------------------------------------------

def test_ac5_event_string_gone_from_source():
    """AC5: no 'straggler_cfg_ignored' in llm_subprocess.py.

    Pre-GREEN fail: the emit block still exists.
    """
    assert "straggler_cfg_ignored" not in LLM_SRC.read_text(encoding="utf-8")


def test_ac5_no_event_on_normal_call(real_log):
    """AC5: a normal call emits no straggler_cfg_ignored event."""
    llm_subprocess.register_backend(
        FAKE, lambda **kw: StepResult(status="ok", data={}, duration_ms=0, step_name="s"),
        manifest_source="harness_tool_record",
    )
    assert _call(FAKE).status == "ok"
    assert _records(real_log, "straggler_cfg_ignored") == []


# --- AC6 --------------------------------------------------------------------

def test_ac6_example_custom_backend_clean_and_dispatchable():
    """AC6: examples/library/custom_backend.py has no straggler_cfg and is dispatcher-callable.

    Pre-GREEN fail: the example's stub still requires straggler_cfg.
    """
    assert "straggler_cfg" not in EXAMPLE.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("bd202_example_custom_backend", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    llm_subprocess.register_backend(
        "bd202-example", mod.stub_backend, manifest_source="orchestrator_observed", overwrite=True,
    )
    result = _call("bd202-example", model="stub-model")
    assert result.status == "ok", (result.status, result.error_code, result.error)
    assert "[stub:stub-model]" in result.data["raw_response"]


# --- AC7 --------------------------------------------------------------------

def test_ac7_changelog_top_section_breaking():
    """AC7: top CHANGELOG section mentions straggler_cfg, BREAKING and #202.

    Pre-GREEN fail: the top section does not describe the removal.
    """
    from helpers.changelog import entry_sections  # noqa: PLC0415

    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    hits = [
        s for s in entry_sections(text, "straggler_cfg")
        if "BREAKING" in s.body and "#202" in s.body
    ]
    assert hits, "no CHANGELOG section mentions straggler_cfg with BREAKING and #202"


def test_ac7_backends_doc_no_longer_claims_kwarg_passed():
    """AC7: docs/backends.md no longer says the keyword is still passed.

    Pre-GREEN fail: 'still passed to your backend, always as None' is present.
    """
    text = " ".join((REPO / "docs" / "backends.md").read_text(encoding="utf-8").split())
    assert not re.search(r"straggler_cfg.{0,40}(still )?passed", text), "doc still claims kwarg is passed"
    assert "always as `None`" not in text


# --- AC8 (GUARD) ------------------------------------------------------------

def test_ac8_guard_abort_capability_kept():
    """AC8 GUARD: the 'abort' token stays; passes pre-GREEN by design."""
    assert "abort" in llm_subprocess._DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]
