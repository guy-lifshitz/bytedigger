"""RED tests for bd#145: reserved observation fields cannot be shadowed by `extra_data`.

Frozen spec: docs/decisions/2026-10-02-bd145-reserved-observation-fields.md (AC1-AC10).

Class: SYSTEMATIC. Chokepoint: `llm_subprocess._dispatch_backend`, the single call
site that hands the caller's `extra_data` to every registered backend.

Collection safety: `RESERVED_OBSERVATION_FIELDS` does not exist yet. It is reached
only through `getattr` inside test bodies, so a missing attribute is a FAILED
assertion, not a collection error.

Seams (external only; `invoke_llm_subprocess`, `_dispatch_backend`,
`_invoke_in_session`, `_invoke_subprocess`, `_pin_mismatch_refusal` run for real):
  * in-session: the runner `.res.json` file written by a daemon thread after the
    `.req.json` appears (pattern of tests/test_bd29_in_session_pin_fail_closed.py);
  * any backend: a test backend via `register_backend` (cleaned by `reset_backends`);
  * claude-subprocess success: fake Popen with stream-json stdout
    (pattern of tests/test_bd141_p4e_subprocess_observed_model.py).
Event log: real run_ctx mechanism (`telemetry_ctx.set_current_run`).
No `sys.path` mutation, no `from conftest import`.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5"
OPUS_FORGED = "claude-opus-5-5"
NAMES = (
    "observed_model", "observed_tools", "worker_written_paths",
    "manifest_source", "mcp_server_losses",
    "billing_mode", "usage",  # bd#167 A1: amends AC1 (the set is now seven names)
)


def _forged_all() -> dict:
    return {
        "observed_model": "FORGED-model",
        "observed_tools": ["FORGED-tool"],
        "worker_written_paths": ["FORGED/path"],
        "manifest_source": "FORGED-source",
        "mcp_server_losses": ["FORGED-loss"],
        "billing_mode": "FORGED-billing",  # bd#167 A1 sentinels: never equal to a producer value
        "usage": {"FORGED-usage": 1},
    }


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: "list[tuple[str, dict, str]]" = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def payloads(self, event_type: str) -> "list[dict]":
        return [p for (t, p, _) in self.events if t == event_type]


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_LLM_PROVIDER", raising=False)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


def _set_run(log, run_id="RUN-BD145", phase="phase_5"):
    telemetry_ctx.set_current_run(
        event_log=log, run_id=run_id, step_name="bd145", phase=phase, tier=None,
    )


# --- AC1 --------------------------------------------------------------------

def test_ac1_reserved_observation_fields_constant():
    """AC1: frozenset of exactly the five names."""
    got = getattr(llm_subprocess, "RESERVED_OBSERVATION_FIELDS", None)
    assert got is not None, "llm_subprocess.RESERVED_OBSERVATION_FIELDS must exist"
    assert isinstance(got, frozenset), type(got)
    assert got == frozenset(NAMES), sorted(got)


# --- in-session driver ------------------------------------------------------

def _drive_in_session(tmp_path: Path, monkeypatch, *, dispatched_model, model="opus",
                      extra_data=None, sub="run"):
    nonce = "bd145aabbccdd11223344556"
    request_dir = tmp_path / sub / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(request_dir))
    log = _FakeEventLog()
    _set_run(log)

    class _FakeUUID:
        hex = nonce

    monkeypatch.setattr(llm_subprocess.uuid, "uuid4", staticmethod(lambda: _FakeUUID()))
    nonce_dir = request_dir / "RUN-BD145" / "phase_5"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    request_path = nonce_dir / f"{nonce}.req.json"
    result_path = nonce_dir / f"{nonce}.res.json"
    payload = {
        "subtype": "success",
        "raw_response": "worker output",
        "worker_written_paths": [],
        "manifest_source": "orchestrator_observed",
        "request_nonce": nonce,
        "__hal_integrity": "end",
        "dispatched_model": dispatched_model,
    }

    def _runner() -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if request_path.exists():  # §1i: written only after the request appears
                tmp_result = str(result_path) + ".tmp"
                with open(tmp_result, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_result, str(result_path))
                return
            time.sleep(0.02)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    try:
        result = llm_subprocess.invoke_llm_subprocess(
            prompt="do the thing", model=model, timeout_sec=5, step_name="bd145",
            backend="claude-in-session", extra_data=extra_data,
        )
    finally:
        t.join(timeout=12.0)
    return result, log


def test_ac2_in_session_forged_fields_do_not_shadow_producer(tmp_path, monkeypatch):
    """AC2: producer values win; observed_tools / mcp_server_losses stay absent."""
    base, _ = _drive_in_session(tmp_path, monkeypatch, dispatched_model=OPUS, sub="base")
    assert base.status == "ok", (base.status, base.error_code)
    result, _ = _drive_in_session(
        tmp_path, monkeypatch, dispatched_model=OPUS, extra_data=_forged_all(), sub="forged",
    )
    assert result.status == "ok", (result.status, result.error_code)
    d = result.data
    assert d.get("observed_model") == OPUS, f"observed_model shadowed: {d.get('observed_model')!r}"
    assert d.get("worker_written_paths") == base.data.get("worker_written_paths"), (
        f"worker_written_paths shadowed: {d.get('worker_written_paths')!r}"
    )
    assert d.get("manifest_source") == base.data.get("manifest_source"), (
        f"manifest_source shadowed: {d.get('manifest_source')!r}"
    )
    assert "observed_tools" not in d, f"observed_tools injected: {d.get('observed_tools')!r}"
    assert "mcp_server_losses" not in d, f"mcp_server_losses injected: {d.get('mcp_server_losses')!r}"


def test_ac3_in_session_forged_model_cannot_mask_pin_mismatch(tmp_path, monkeypatch):
    """AC3: runner says sonnet, pin opus, forged opus -> refused, event shows sonnet."""
    result, log = _drive_in_session(
        tmp_path, monkeypatch, dispatched_model=SONNET, model="opus",
        extra_data={"observed_model": OPUS_FORGED},
    )
    assert result.error_code == "E_MODEL_PIN_MISMATCH", (
        f"forged observed_model masked the drift: status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    ev = log.payloads("model_pin_mismatch")
    assert len(ev) == 1, ev
    assert ev[0]["observed_model"] == SONNET, ev[0]


def test_ac3b_in_session_forged_model_cannot_cause_false_refusal(tmp_path, monkeypatch):
    """AC3b: runner says opus, pin opus, forged sonnet -> no refusal, no event.

    Red on base: the forged value shadows the runner's and is refused.
    """
    result, log = _drive_in_session(
        tmp_path, monkeypatch, dispatched_model=OPUS, model="opus",
        extra_data={"observed_model": SONNET},
    )
    assert result.status == "ok", (
        f"forged observed_model caused a false refusal: {result.error_code!r}"
    )
    assert log.payloads("model_pin_mismatch") == []


# (AC4/AC5 straggler drivers retired by bd#89 P3b1b-i: the watchdog is gone.)

# --- any registered backend -------------------------------------------------

def _register_echo_backend():
    seen: list = []

    def _echo(**kwargs):
        extra = kwargs.get("extra_data")
        seen.append(extra)
        return StepResult(
            status="ok",
            data={"raw_response": "x", **(extra or {})},
            duration_ms=0,
            step_name=kwargs.get("step_name", "echo"),
        )

    llm_subprocess.register_backend(
        "bd145-echo", _echo, manifest_source="harness_tool_record",
    )
    return seen


def _invoke_echo(*, model="opus", extra_data=None):
    return llm_subprocess.invoke_llm_subprocess(
        prompt="x", model=model, timeout_sec=5, step_name="bd145",
        backend="bd145-echo", extra_data=extra_data,
    )


def test_ac6_registered_backend_cannot_be_fed_reserved_values():
    """AC6: forged reserved values never reach result.data; no false pin refusal."""
    _register_echo_backend()
    _set_run(_FakeEventLog())
    result = _invoke_echo(model="opus", extra_data=_forged_all())
    leaked = [n for n in NAMES if n in (result.data or {})]
    assert leaked == [], f"reserved fields passed through a registered backend: {leaked!r}"

    result2 = _invoke_echo(model="opus", extra_data={"observed_model": SONNET})
    assert result2.status == "ok", (
        f"forged observed_model refused a registered-backend run: {result2.error_code!r}"
    )


def test_ac7_non_reserved_keys_still_reach_backend():
    """AC7 (guard, green on base): over-stripping would turn this red."""
    seen = _register_echo_backend()
    _set_run(_FakeEventLog())
    result = _invoke_echo(extra_data={"doc_path": "p", **_forged_all()})
    assert seen and seen[0] is not None and seen[0].get("doc_path") == "p", seen
    assert result.data.get("doc_path") == "p", result.data


def test_ac8_caller_dict_is_not_mutated():
    """AC8 (guard, green on base): stripping in place on the caller's dict would redden it."""
    _register_echo_backend()
    _set_run(_FakeEventLog())
    extra = {"doc_path": "p", **_forged_all()}
    snapshot = {k: (list(v) if isinstance(v, list) else v) for k, v in extra.items()}
    _invoke_echo(extra_data=extra)
    assert extra == snapshot, f"caller extra_data was mutated: {extra!r}"


def test_ac9_dropped_reserved_name_is_logged_once(caplog):
    """AC9: one WARNING on the llm_subprocess logger naming the dropped names;
    none when no reserved name is present."""
    _register_echo_backend()
    _set_run(_FakeEventLog())
    lname = llm_subprocess.logger.name
    with caplog.at_level(logging.WARNING, logger=lname):
        _invoke_echo(extra_data={"observed_model": "x", "observed_tools": ["y"]})
    prefix = getattr(llm_subprocess, "RESERVED_DROP_LOG_PREFIX", None)
    assert isinstance(prefix, str) and prefix, "RESERVED_DROP_LOG_PREFIX must exist"
    # bd#167 B1: select by the drop-log prefix (the no-usage warning also contains "usage")
    warns = [r for r in caplog.records if r.name == lname and r.levelno == logging.WARNING
             and r.getMessage().startswith(prefix) and "observed_model" in r.getMessage()]
    assert len(warns) == 1, f"expected one WARNING naming the dropped fields; got {len(warns)}"
    assert warns[0].getMessage().startswith(prefix + "observed_model"), warns[0].getMessage()
    assert "observed_tools" in warns[0].getMessage(), warns[0].getMessage()

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=lname):
        _invoke_echo(extra_data={"doc_path": "p"})
    named = [r for r in caplog.records if r.name == lname
             and r.getMessage().startswith(prefix)
             and any(n in r.getMessage() for n in NAMES)]
    assert named == [], [r.getMessage() for r in named]


# --- AC11: edges ------------------------------------------------------------

def _drop_warnings(caplog):
    lname = llm_subprocess.logger.name
    prefix = getattr(llm_subprocess, "RESERVED_DROP_LOG_PREFIX", "")
    # bd#167 B1: select by the drop-log prefix, not by a bare name (other warnings may name a field)
    return [r for r in caplog.records if r.name == lname and r.levelno == logging.WARNING
            and r.getMessage().startswith(prefix)
            and any(n in r.getMessage() for n in NAMES)]


def test_ac11_none_valued_reserved_key_is_dropped_and_logged(caplog):
    """AC11: a reserved key is dropped whatever its value, None included; one warning."""
    _register_echo_backend()
    _set_run(_FakeEventLog())
    with caplog.at_level(logging.WARNING, logger=llm_subprocess.logger.name):
        result = _invoke_echo(extra_data={"observed_model": None})
    assert "observed_model" not in (result.data or {}), result.data
    assert len(_drop_warnings(caplog)) == 1, [r.getMessage() for r in caplog.records]


def test_ac11_empty_and_none_extra_data_pass_through_silently(caplog):
    """AC11 (guard, green on base): {} and None reach the backend unchanged, no warning."""
    seen = _register_echo_backend()
    _set_run(_FakeEventLog())
    with caplog.at_level(logging.WARNING, logger=llm_subprocess.logger.name):
        _invoke_echo(extra_data={})
        _invoke_echo(extra_data=None)
    assert seen[0] == {} and seen[0] is not None, seen
    assert seen[1] is None, seen
    assert _drop_warnings(caplog) == []


def test_ac11_non_dict_extra_data_passes_through_unchanged(caplog):
    """AC11 (guard, green on base): a list is handed to the backend unchanged, no warning,
    no dispatcher exception."""
    seen = _register_echo_backend()
    _set_run(_FakeEventLog())
    payload = ["observed_model", "x"]
    got = []

    def _list_backend(**kwargs):
        got.append(kwargs.get("extra_data"))
        return StepResult(status="ok", data={"raw_response": "x"}, duration_ms=0,
                          step_name=kwargs.get("step_name", "echo"))

    llm_subprocess.register_backend(
        "bd145-list", _list_backend, manifest_source="harness_tool_record",
    )
    with caplog.at_level(logging.WARNING, logger=llm_subprocess.logger.name):
        llm_subprocess.invoke_llm_subprocess(
            prompt="x", model="opus", timeout_sec=5, step_name="bd145",
            backend="bd145-list", extra_data=payload,
        )
    assert got and got[0] is payload, got
    assert _drop_warnings(caplog) == []


# --- AC10: claude-subprocess success path -----------------------------------

_RESULT_EVENT = {
    "type": "result", "subtype": "success", "result": "OK",
    "usage": {"input_tokens": 1, "output_tokens": 1,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    "total_cost_usd": 0.001, "duration_ms": 100,
}


def test_ac10_subprocess_success_keeps_producer_values():
    """AC10 (guard, green on base): the safe path keeps producer values under forged extra_data."""
    events = [
        {"type": "system", "subtype": "init", "parent_tool_use_id": None, "model": OPUS},
        {"type": "assistant", "parent_tool_use_id": None,
         "message": {"model": OPUS, "content": []}},
        _RESULT_EVENT,
    ]
    lines = "".join(json.dumps(e) + "\n" for e in events)

    def _popen(argv, **kwargs):
        proc = MagicMock()
        proc.pid = 99999
        proc.returncode = 0
        proc.stdout = io.StringIO(lines)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(lines, ""))
        return proc

    _set_run(_FakeEventLog(), run_id="RUN-BD145-AC10", phase="phase_bd145")
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=_popen):
        result = llm_subprocess.invoke_llm_subprocess(
            prompt="hi", model="opus", timeout_sec=10, step_name="bd145",
            idle_timeout_sec=0, straggler_cfg=None, backend="claude-subprocess",
            extra_data=_forged_all(),
        )
    assert result.status == "ok", (result.status, result.error_code)
    forged = _forged_all()
    shadowed = [n for n in NAMES if result.data.get(n) == forged[n]]
    assert shadowed == [], f"producer values shadowed: {shadowed!r}"
    assert result.data["observed_model"] == OPUS
    assert result.data["manifest_source"] == "harness_tool_record"
