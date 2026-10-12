"""RED tests for bd#291: claude-in-session reads `observed_tools` from `.res.json`.

Frozen spec: docs/decisions/2026-10-13-bd291-in-session-observed-tools.md (cases 1-7).

Chokepoint: the result dict built in `llm_subprocess._invoke_in_session`.
`_capability_escape_refusal` and `_attest_payload` (in `_dispatch_backend`) already
read `observed_tools` from the result; they run for real here.

Seams (external only): the runner `.res.json` written atomically by a daemon thread
after the real `.req.json` appears (pattern of test_bd145_reserved_observation_fields.py);
the event log through the real run_ctx mechanism (`telemetry_ctx.set_current_run`).
No `sys.path` mutation, no `from conftest import`. New symbols are reached only
inside test bodies, so absent behaviour is a failed assertion, not a collection error.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx

OPUS = "claude-opus-5"
RUN_ID = "RUN-BD291"
FLAG = "HAL_IN_SESSION_OBSERVED_TOOLS"
ATTEST = "model_invocation_attested"
DROPPED = "in_session_observed_tools_dropped"
_ABSENT = object()


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: "list[tuple[str, dict, str]]" = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def payloads(self, event_type: str) -> "list[dict]":
        return [p for (t, p, _) in self.events if t == event_type]

    def run_ids(self, event_type: str) -> "list[str]":
        return [r for (t, _, r) in self.events if t == event_type]


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_LLM_PROVIDER", raising=False)
    suffix = FLAG[len("HAL_"):]
    for name in (FLAG, "BD_" + suffix, "BYTEDIGGER_" + suffix):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAL_IN_SESSION_ENFORCES_TOOLS", "1")
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


def _drive(tmp_path: Path, monkeypatch, *, observed=_ABSENT, allowed_tools=("Read",),
           extra_data=None, use_run=True, sub="run"):
    """Run one real in-session dispatch; the runner reports `observed` as
    `observed_tools` (omitted when `_ABSENT`)."""
    nonce = "bd291aabbccdd11223344556"
    request_dir = tmp_path / sub / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(request_dir))
    log = _FakeEventLog()
    if use_run:
        telemetry_ctx.set_current_run(
            event_log=log if use_run != "no_log" else None, run_id=RUN_ID, step_name="bd291", phase="phase_5", tier=None,
        )

    class _FakeUUID:
        hex = nonce

    monkeypatch.setattr(llm_subprocess.uuid, "uuid4", staticmethod(lambda: _FakeUUID()))
    nonce_dir = request_dir / RUN_ID / "phase_5"
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
        "dispatched_model": OPUS,
    }
    if observed is not _ABSENT:
        payload["observed_tools"] = observed

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
            prompt="do the thing", model="opus", timeout_sec=5, step_name="bd291",
            backend="claude-in-session", extra_data=extra_data,
            allowed_tools=list(allowed_tools) if allowed_tools is not None else None,
        )
    finally:
        t.join(timeout=12.0)
    return result, log


def _attested(log: _FakeEventLog) -> dict:
    ev = log.payloads(ATTEST)
    assert len(ev) == 1, ev
    return ev[0]


def _positive_control(tmp_path, monkeypatch, *, observed=("Read",), **kw) -> None:
    """Positive control (switch unset, valid list on the wire): the new behaviour
    must be live before a guard property is asserted. Fails today."""
    result, log = _drive(
        tmp_path, monkeypatch, observed=list(observed), sub="control", **kw,
    )
    assert result.status == "ok", (result.status, result.error_code)
    assert result.data.get("observed_tools") == list(observed), result.data
    assert _attested(log)["observed_tools"] == list(observed)
    telemetry_ctx.clear_current_run()


# --- case 1 -----------------------------------------------------------------

def test_case1_observed_escape_is_refused(tmp_path, monkeypatch):
    result, log = _drive(tmp_path, monkeypatch, observed=["Read", "Write"])
    assert result.status == "error", (result.status, result.error_code)
    assert result.error_code == "E_CAPABILITY_ESCAPE", result.error_code
    assert result.data["capability_escapes"] == ["Write"], result.data
    assert _attested(log)["observed_tools"] == ["Read", "Write"]


# --- case 2 -----------------------------------------------------------------

def test_case2_observed_inside_allowed_is_ok_and_attested(tmp_path, monkeypatch):
    result, log = _drive(tmp_path, monkeypatch, observed=["Read"])
    assert result.status == "ok", (result.status, result.error_code)
    assert result.data.get("observed_tools") == ["Read"], result.data
    assert _attested(log)["observed_tools"] == ["Read"]
    assert log.payloads(DROPPED) == []


def test_empty_list_is_kept_as_an_observation(tmp_path, monkeypatch):
    result, log = _drive(tmp_path, monkeypatch, observed=[])
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" in result.data, result.data
    assert result.data["observed_tools"] == []
    assert _attested(log)["observed_tools"] == []
    assert log.payloads(DROPPED) == []


def test_observed_tools_wire_list_roundtrips_with_equality_only(tmp_path, monkeypatch):
    result, _ = _drive(tmp_path, monkeypatch, observed=["Read", "Read"])
    assert result.data.get("observed_tools") == ["Read", "Read"], result.data


def test_allowed_tools_none_means_no_escape_check(tmp_path, monkeypatch):
    result, log = _drive(tmp_path, monkeypatch, observed=["Read", "Write"], allowed_tools=None)
    assert result.status == "ok", (result.status, result.error_code)
    assert result.data.get("observed_tools") == ["Read", "Write"], result.data
    assert _attested(log)["observed_tools"] == ["Read", "Write"]


# --- case 3 -----------------------------------------------------------------

def test_case3_field_absent_adds_nothing_and_no_event(tmp_path, monkeypatch):
    _positive_control(tmp_path, monkeypatch)
    result, log = _drive(tmp_path, monkeypatch, sub="absent")
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data
    assert _attested(log)["observed_tools"] is None
    assert log.payloads(DROPPED) == []


# --- case 4 -----------------------------------------------------------------

@pytest.mark.parametrize("bad, type_name", [
    ("Read", "str"),
    ([1], "list"),
    (["Read", 2], "list"),
    (None, "NoneType"),
    ({"Read": True}, "dict"),
])
def test_case4_invalid_field_is_dropped_with_one_event(tmp_path, monkeypatch, bad, type_name):
    result, log = _drive(tmp_path, monkeypatch, observed=bad)
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data
    assert _attested(log)["observed_tools"] is None
    ev = log.payloads(DROPPED)
    assert len(ev) == 1, log.events
    assert ev[0] == {
        "backend": "claude-in-session",
        "reason": "not_list_of_strings",
        "value_type": type_name,
    }, ev[0]
    assert log.run_ids(DROPPED) == [RUN_ID]


def test_case4_invalid_field_without_event_log_does_not_fail(tmp_path, monkeypatch):
    """Run id kept, run ctx has no event log; positive control first."""
    _positive_control(tmp_path, monkeypatch)
    result, _ = _drive(tmp_path, monkeypatch, observed="Read", use_run="no_log", sub="nolog")
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data


# --- case 5 -----------------------------------------------------------------

def test_case5_switch_off_restores_old_behaviour(tmp_path, monkeypatch):
    ctl, _ = _drive(tmp_path, monkeypatch, observed=["Read", "Write"], sub="control")
    assert ctl.error_code == "E_CAPABILITY_ESCAPE", (ctl.status, ctl.error_code)
    telemetry_ctx.clear_current_run()
    monkeypatch.setenv(FLAG, "0")
    result, log = _drive(tmp_path, monkeypatch, observed=["Read", "Write"], sub="off")
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data
    assert _attested(log)["observed_tools"] is None
    assert log.payloads(DROPPED) == []


def test_case5_switch_off_does_not_emit_drop_event_for_invalid_field(tmp_path, monkeypatch):
    ctl, ctl_log = _drive(tmp_path, monkeypatch, observed="Read", sub="control")
    assert len(ctl_log.payloads(DROPPED)) == 1, ctl_log.events
    telemetry_ctx.clear_current_run()
    monkeypatch.setenv(FLAG, "0")
    result, log = _drive(tmp_path, monkeypatch, observed="Read", sub="off")
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data
    assert log.payloads(DROPPED) == []


def test_case5_bd_alias_switch_off_restores_old_behaviour(tmp_path, monkeypatch):
    ctl, _ = _drive(tmp_path, monkeypatch, observed=["Read", "Write"], sub="control")
    assert ctl.error_code == "E_CAPABILITY_ESCAPE", (ctl.status, ctl.error_code)
    telemetry_ctx.clear_current_run()
    monkeypatch.setenv("BD_IN_SESSION_OBSERVED_TOOLS", "0")
    result, log = _drive(tmp_path, monkeypatch, observed=["Read", "Write"], sub="off")
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data
    assert _attested(log)["observed_tools"] is None
    assert log.payloads(DROPPED) == []


def test_switch_set_to_other_than_zero_stays_on(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    result, _ = _drive(tmp_path, monkeypatch, observed=["Read", "Write"])
    assert result.error_code == "E_CAPABILITY_ESCAPE", (result.status, result.error_code)


# --- case 6 -----------------------------------------------------------------

def test_case6_extra_data_cannot_inject_observed_tools(tmp_path, monkeypatch):
    _positive_control(tmp_path, monkeypatch)
    result, log = _drive(
        tmp_path, monkeypatch, extra_data={"observed_tools": ["Write"]}, sub="forged",
    )
    assert result.status == "ok", (result.status, result.error_code)
    assert "observed_tools" not in result.data, result.data
    assert _attested(log)["observed_tools"] is None


def test_case6_extra_data_cannot_override_the_res_json_value(tmp_path, monkeypatch):
    result, log = _drive(
        tmp_path, monkeypatch, observed=["Read", "Write"],
        extra_data={"observed_tools": ["Read"]},
    )
    assert result.error_code == "E_CAPABILITY_ESCAPE", (result.status, result.error_code)
    assert _attested(log)["observed_tools"] == ["Read", "Write"]


# --- case 7: catalog --------------------------------------------------------

def test_case7_catalog_entry():
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS.get(FLAG)
    assert entry is not None, f"{FLAG} missing from flags_catalog.FLAGS"
    assert entry["kind"] == "gate", entry
    assert entry["default"] == "1", entry
    assert entry["module"] == "llm_subprocess.py", entry
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip(), entry
    assert str(entry.get("provenance", "")).startswith("introduced:"), entry
    assert "retire-by:" in entry.get("description", ""), entry
