"""RED tests — agent_sdk backend stderr-tail capture + outage tagging (GH933).

Spec: SHARED/memory/Decisions/2026-07-17_GH933_agent_sdk_stderr_outage_spec.md

15 tests, one per AC1-AC15 (§3 table). `_stderr_tail` / `_external_outage_indicator`
do NOT exist yet on prod `lib.reference_backends.agent_sdk` -> RED phase. All
access to these not-yet-existing symbols is deferred to inside test bodies
(getattr / call inside the test function) so the file COLLECTS cleanly and
FAILS at assert time, not at collection/import time (D1CF5FDF / §1q).

§1q: no `spec_from_file_location` / `exec_module`; no module-level
`sys.path` mutation; no `from conftest import`.
§1i: all env pre-staged via monkeypatch.setenv BEFORE invoking the backend
(HAL_STATUS_PROBE_URL / HAL_OUTAGE_PROBE / HAL_AGENT_SDK_STDERR_TAIL_LINES);
outage probes use `file://` fixtures written to tmp_path, never network.
§1l stub-passability: only the external `claude_agent_sdk` module (sys.modules
injection, matches test_GH885's pattern) is faked. UUT
(`agent_sdk_backend`, `_stderr_tail`, `_external_outage_indicator`) is
exercised directly, unstubbed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Deferred import (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_agent_sdk_backend():
    import lib.reference_backends.agent_sdk as mod  # noqa: PLC0415
    return mod


class ResultMessage:
    def __init__(self, result=None, usage=None, session_id=None):
        self.result = result
        self.usage = usage
        self.session_id = session_id


def _install_fake_agent_sdk(monkeypatch, options_cls=None):
    """Local copy of test_GH885's fake, extended: `query()` calls
    `options.stderr(line)` for each entry in behavior['stderr_lines'] BEFORE
    yielding the result/raising/sleeping, when the constructed options object
    has a callable `.stderr` attribute (duck-typed callback wiring per §2.1).
    """
    recorded_calls = []
    behavior_queue = []

    class DefaultOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.kwargs = kwargs

    ClaudeAgentOptions = options_cls or DefaultOptions

    def _assistant_like():
        return types.SimpleNamespace(kind="assistant-like")

    async def query(*, prompt, options):
        recorded_calls.append(options)
        behavior = behavior_queue.pop(0) if behavior_queue else {}
        stderr_cb = getattr(options, "stderr", None) or options.kwargs.get("stderr")
        for line in behavior.get("stderr_lines", []):
            if callable(stderr_cb):
                stderr_cb(line)
        if "sleep" in behavior:
            import asyncio  # noqa: PLC0415
            await asyncio.sleep(behavior["sleep"])
        if "raise" in behavior:
            yield _assistant_like()
            raise behavior["raise"]
        yield _assistant_like()
        yield _assistant_like()
        if "result" in behavior:
            yield behavior["result"]
        else:
            yield ResultMessage(
                result="default output",
                usage={"input_tokens": 10, "output_tokens": 5},
                session_id="default-sid",
            )

    fake_mod = types.ModuleType("claude_agent_sdk")
    fake_mod.query = query
    fake_mod.ClaudeAgentOptions = ClaudeAgentOptions
    fake_mod.ResultMessage = ResultMessage
    fake_mod.recorded_calls = recorded_calls
    fake_mod.behavior_queue = behavior_queue
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_mod)
    return fake_mod


def _make_result(text, tokens_in, tokens_out, session_id):
    return ResultMessage(
        result=text,
        usage={"input_tokens": tokens_in, "output_tokens": tokens_out},
        session_id=session_id,
    )


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root)] + list(args),
        check=True, capture_output=True, text=True,
    )


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "commit", "--allow-empty", "-q", "-m", "init")


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    sys.modules.pop("lib.reference_backends.agent_sdk", None)
    sys.modules.pop("claude_agent_sdk", None)


@pytest.fixture(autouse=True)
def _hermetic_probe_url(monkeypatch, tmp_path):
    """M1: default every test to a deterministic 'none' probe fixture so
    AC1/AC2/AC5/AC10 (and any other non-probe-specific test) never resolve
    the real default https URL. Probe-specific tests override
    HAL_STATUS_PROBE_URL in-body AFTER this fixture runs.
    """
    p = tmp_path / "_default_none_probe.json"
    p.write_text(json.dumps({"status": {"indicator": "none"}}), encoding="utf-8")
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", f"file://{p}")


def _common_kwargs(root, run_id="run-x"):
    return dict(
        model="claude-sonnet",
        extra_data={"workspace_root": str(root)},
        allowed_tools=None,
        run_ctx=types.SimpleNamespace(run_id=run_id),
        hard_gate=False,
        gate_label=None,
        straggler_cfg=None,
        idle_timeout_sec=None,
    )


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    return root


# ---------------------------------------------------------------------------
# AC1 — exception with .stderr attribute -> tail in error + data
# ---------------------------------------------------------------------------

def test_ac1_exception_with_stderr_attr_captured_in_error_and_data(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    exc = RuntimeError("boom")
    exc.stderr = "boom-details"
    fake.behavior_queue.append({"raise": exc})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac1")
    )

    assert result.status == "error"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE"
    assert result.recoverable is True
    assert result.error is not None and "stderr tail: " in result.error and "boom-details" in result.error, (
        f"AC1: error must contain stderr tail with the exc.stderr content; got {result.error!r}"
    )
    assert result.data is not None and result.data.get("stderr_tail") == "boom-details", (
        f"AC1: data['stderr_tail'] must equal exc.stderr; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — exception without .stderr, callback lines accumulate
# ---------------------------------------------------------------------------

def test_ac2_exception_without_stderr_uses_callback_buffer(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "stderr_lines": ["cb-line-1", "cb-line-2"],
        "raise": RuntimeError("no-stderr-attr"),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac2")
    )

    assert result.status == "error"
    assert result.data is not None
    tail = result.data.get("stderr_tail")
    assert tail is not None and "cb-line-1" in tail and "cb-line-2" in tail and "cb-line-1\ncb-line-2" in tail, (
        f"AC2: data['stderr_tail'] must join callback lines with '\\n'; got {tail!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — old-SDK options TypeError-on-stderr-kwarg falls back gracefully
# ---------------------------------------------------------------------------

def test_ac3_options_typeerror_on_stderr_kwarg_falls_back(monkeypatch, tmp_path):
    class OldOptions:
        def __init__(self, **kwargs):
            if "stderr" in kwargs:
                raise TypeError("unexpected keyword argument 'stderr'")
            self.kwargs = kwargs
            self.__dict__.update(kwargs)

    fake = _install_fake_agent_sdk(monkeypatch, options_cls=OldOptions)
    fake.behavior_queue.append({"result": _make_result("out1", 10, 2, "sid-a")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac3")
    )

    assert result.status == "ok", (
        f"AC3: old SDK without stderr kwarg support must not break the run; "
        f"got {result.status!r} error={result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — new-SDK options recorded_calls[0] carries a callable stderr kwarg
# ---------------------------------------------------------------------------

def test_ac4_new_sdk_options_receive_callable_stderr_kwarg(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"result": _make_result("out1", 10, 2, "sid-a")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac4")
    )

    assert len(fake.recorded_calls) == 1
    stderr_kw = fake.recorded_calls[0].kwargs.get("stderr")
    assert callable(stderr_kw), (
        f"AC4: options kwargs must include a callable 'stderr' callback; got "
        f"{fake.recorded_calls[0].kwargs!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — timeout branch carries stderr_tail collected before the timeout
# ---------------------------------------------------------------------------

def test_ac5_timeout_branch_carries_stderr_tail(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "stderr_lines": ["pre-timeout-line"],
        "sleep": 2.0,
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=0.3, step_name="gate.step1", **_common_kwargs(root, "run-ac5")
    )

    assert result.status == "error"
    assert result.error_code == "E_LLM_API_TIMEOUT"
    assert result.data is not None and result.data.get("timed_out") is True
    tail = result.data.get("stderr_tail")
    assert tail is not None and "pre-timeout-line" in tail, (
        f"AC5: timeout data['stderr_tail'] must contain lines collected before "
        f"the timeout fired; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# Outage-probe fixtures
# ---------------------------------------------------------------------------

def _write_probe_json(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return f"file://{p}"


# ---------------------------------------------------------------------------
# AC6 — outage indicator 'major' tags data + error suffix
# ---------------------------------------------------------------------------

def test_ac6_outage_probe_major_indicator_tags_error_and_data(monkeypatch, tmp_path):
    url = _write_probe_json(tmp_path, "major.json", {"status": {"indicator": "major"}})
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", url)
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("boom")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac6")
    )

    assert result.status == "error"
    assert result.data is not None and result.data.get("external_outage") is True, (
        f"AC6: data['external_outage'] must be True; got {result.data!r}"
    )
    assert result.data.get("outage_indicator") == "major", (
        f"AC6: data['outage_indicator'] must be 'major'; got {result.data!r}"
    )
    assert result.error is not None and result.error.endswith("[external_outage: major]"), (
        f"AC6: error must end with the outage suffix; got {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — outage indicator 'none' -> no tag
# ---------------------------------------------------------------------------

def test_ac7_outage_probe_none_indicator_no_tag(monkeypatch, tmp_path):
    url = _write_probe_json(tmp_path, "none.json", {"status": {"indicator": "none"}})
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", url)
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("boom")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac7")
    )

    assert result.status == "error"
    assert result.data is not None and "external_outage" not in result.data, (
        f"AC7: 'none' indicator must NOT tag external_outage; got {result.data!r}"
    )
    assert result.error is not None and "[external_outage:" not in result.error, (
        f"AC7: error must not carry an outage suffix; got {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — kill-switch HAL_OUTAGE_PROBE=0 disables the probe entirely
# ---------------------------------------------------------------------------

def test_ac8_kill_switch_disables_probe(monkeypatch, tmp_path):
    url = _write_probe_json(tmp_path, "major.json", {"status": {"indicator": "major"}})
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", url)
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("boom")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac8")
    )

    assert result.status == "error"
    assert result.data is not None and "external_outage" not in result.data, (
        f"AC8: kill-switch=0 must prevent any outage tag even with a "
        f"'major' fixture available; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC9 — probe fail-open: broken/missing URL does not raise, does not tag
# ---------------------------------------------------------------------------

def test_ac9_probe_fail_open_on_missing_file(monkeypatch, tmp_path):
    bad_url = f"file://{tmp_path / 'does-not-exist.json'}"
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", bad_url)
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("boom")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac9")
    )

    assert result.status == "error"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE"
    assert result.data is not None and "external_outage" not in result.data, (
        f"AC9: a broken probe URL must fail open (no tag, no raised exception "
        f"propagating out of the backend); got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — stderr tail is capped to the last _STDERR_TAIL_BYTES bytes
# ---------------------------------------------------------------------------

def test_ac10_stderr_tail_capped_to_tail_bytes(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    long_lines = [f"line-{i:04d}-" + ("x" * 50) for i in range(200)]
    fake.behavior_queue.append({
        "stderr_lines": long_lines,
        "raise": RuntimeError("boom"),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac10")
    )

    assert result.status == "error"
    tail = result.data.get("stderr_tail") if result.data else None
    assert tail is not None, "AC10: stderr_tail must be present"
    tail_bytes = tail.encode("utf-8")
    assert len(tail_bytes) <= 4096, (
        f"AC10: stderr_tail must be capped at 4096 bytes; got {len(tail_bytes)}"
    )
    assert long_lines[-1] in tail, (
        f"AC10: the tail must preserve the END of the buffer (last line present); "
        f"got tail ending {tail[-80:]!r}"
    )
    assert long_lines[0] not in tail, (
        f"AC10: with >4096 bytes of input, the FIRST line must have been "
        f"truncated away; got tail starting {tail[:80]!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — success path never calls the outage probe
# ---------------------------------------------------------------------------

def test_ac11_success_path_never_calls_probe(monkeypatch, tmp_path):
    poisoned_url = f"file://{tmp_path / 'poison-does-not-exist.json'}"
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", poisoned_url)
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"result": _make_result("out1", 10, 2, "sid-a")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac11")
    )

    assert result.status == "ok"
    assert result.data is not None
    assert "external_outage" not in result.data and "outage_indicator" not in result.data, (
        f"AC11: success path must never tag outage keys (probe not invoked on "
        f"the happy path); got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC12 — _stderr_tail named module-level helper
# ---------------------------------------------------------------------------

def test_ac12_stderr_tail_is_named_module_helper(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    helper = getattr(mod, "_stderr_tail", None)
    assert callable(helper), "AC12: _stderr_tail must exist as a module-level callable"

    from collections import deque  # noqa: PLC0415

    assert helper(deque(["a", "b"]), None) == "a\nb", (
        "AC12: _stderr_tail(buf, None) must join buffered lines with '\\n'"
    )

    exc = RuntimeError("x")
    exc.stderr = "from-exc"
    assert helper(deque(["a", "b"]), exc) == "from-exc", (
        "AC12: _stderr_tail must prefer a non-empty exc.stderr over the buffer"
    )


# ---------------------------------------------------------------------------
# AC13 — timeout branch ALSO gets outage-tagged (M2)
# ---------------------------------------------------------------------------

def test_ac13_timeout_branch_tags_external_outage(monkeypatch, tmp_path):
    url = _write_probe_json(tmp_path, "major.json", {"status": {"indicator": "major"}})
    monkeypatch.setenv("HAL_STATUS_PROBE_URL", url)
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "stderr_lines": ["pre-timeout-line"],
        "sleep": 2.0,
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=0.3, step_name="gate.step1", **_common_kwargs(root, "run-ac13")
    )

    assert result.status == "error"
    assert result.error_code == "E_LLM_API_TIMEOUT"
    assert result.data is not None and result.data.get("external_outage") is True, (
        f"AC13: timeout data['external_outage'] must be True; got {result.data!r}"
    )
    assert result.data.get("outage_indicator") == "major", (
        f"AC13: timeout data['outage_indicator'] must be 'major'; got {result.data!r}"
    )
    assert result.error is not None and result.error.endswith("[external_outage: major]"), (
        f"AC13: timeout error must end with the outage suffix; got {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC14 — _stderr_tail multibyte cap: cyrillic overflow + bytes exc.stderr
# ---------------------------------------------------------------------------

def test_ac14_stderr_tail_multibyte_cap_and_bytes_stderr_fallback(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    helper = getattr(mod, "_stderr_tail", None)
    assert callable(helper), "AC14: _stderr_tail must exist as a module-level callable"

    from collections import deque  # noqa: PLC0415

    cyrillic_lines = ["привет-мир-строка-" + str(i) * 20 for i in range(500)]
    buf = deque(cyrillic_lines)
    tail = helper(buf, None)
    assert isinstance(tail, str), "AC14: tail must be a decoded str, no decode exception"
    assert len(tail.encode("utf-8")) <= 4096, (
        f"AC14: multibyte tail must still be capped at 4096 bytes; got "
        f"{len(tail.encode('utf-8'))}"
    )

    exc = RuntimeError("x")
    exc.stderr = b"raw-bytes-stderr"
    tail2 = helper(deque(["fallback-line"]), exc)
    assert tail2 == "fallback-line", (
        f"AC14: bytes exc.stderr must NOT crash and must fall back to the "
        f"buffer (bytes is not treated as a non-empty str); got {tail2!r}"
    )


# ---------------------------------------------------------------------------
# AC15 — TypeError raised INSIDE query() (not options construction) still
# surfaces as a generic bad-response error (construction-scoped fallback).
# ---------------------------------------------------------------------------

def test_ac15_typeerror_inside_query_surfaces_as_bad_response(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": TypeError("unrelated type error inside query")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, "run-ac15")
    )

    assert result.status == "error"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE", (
        f"AC15: a TypeError raised inside query() (not during options "
        f"construction) must NOT be swallowed by the stderr-kwarg fallback "
        f"path — it must surface as the generic bad-response error; got "
        f"{result.error_code!r}"
    )
    assert result.recoverable is True
