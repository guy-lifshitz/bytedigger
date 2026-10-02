"""RED tests for bd#141 item 4 (c/d/e) -- `bd_l3` host CLI and the on-disk event filter.

Spec: docs/decisions/2026-10-02-bd141-p4-bd-l3-cli.md (AC1-AC10).

Every `bytedigger_engine.*` import is inside a test body, so the file collects
cleanly and each AC fails at assert time. Logs are written with the real
`EventLog.append` (no hand-built production-shape lines, except the malformed
cases of AC8). The CLI is run as a real subprocess. Nothing is mocked.
Model pair: "sonnet" vs "haiku" resolves to different families (proven by
test_bd28 AC1); "sonnet" vs "sonnet" is the same family.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ENGINE_PY = Path(__file__).resolve().parents[1]
SHA = "sha256:" + "0" * 64
ALL_REQS = ("R3.1", "R3.2", "R3.3", "R3.5", "R3.6")


def _payload(requested: str = "sonnet", observed: "str | None" = "haiku") -> "dict[str, Any]":
    return {
        "step_name": "s1",
        "backend": "claude-subprocess",
        "model_requested": requested,
        "prompt_sha256": SHA,
        "injections": [{"source_id": "role-template", "sha256": SHA}],
        "declared_capabilities": None,
        "capability_enforcement": "runtime-allowlist",
        "observed_model": observed,
        "observed_tools": None,
    }


def _event_type() -> str:
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    return attest.EVENT_TYPE


def _write_log(path: Path, events: "list[tuple[str, dict, str | None]]") -> Path:
    from bytedigger_engine.event_log import EventLog  # noqa: PLC0415

    log = EventLog(path)
    for event_type, payload, run_id in events:
        log.append(event_type, payload, run_id=run_id)
    return path


def _cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.bd_l3", *args],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=60,
    )


def _report(proc: "subprocess.CompletedProcess[str]") -> "dict[str, Any]":
    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr!r}"
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1, f"expected exactly one stdout line, got {proc.stdout!r}"
    return json.loads(lines[0])


def test_ac1_real_event_log_r33_violation_fails_through_cli(tmp_path):
    p = _write_log(tmp_path / "events.jsonl",
                   [(_event_type(), _payload("sonnet", "haiku"), "run-1")])
    out = _report(_cli("--event-log", str(p)))
    assert out["labels"]["verdict:R3.3"] == "failed"
    assert out["passed"] is False
    assert any(v.startswith("R3.3:") for v in out["violations"])


def test_ac2_event_type_key_alone_is_read_by_checker():
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415

    report = check_bd_l3([{"event_type": _event_type(), "payload": _payload()}])
    assert report.labels["verdict:R3.3"] == "failed"


def test_ac3_legacy_type_key_unchanged_pass_and_fail():
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415

    bad = check_bd_l3([{"type": _event_type(), "payload": _payload("sonnet", "haiku")}])
    good = check_bd_l3([{"type": _event_type(), "payload": _payload("sonnet", "sonnet")}])
    assert bad.labels["verdict:R3.3"] == "failed" and bad.passed is False
    assert good.labels["verdict:R3.3"] == "passed"


def test_ac4_foreign_event_type_is_still_filtered(tmp_path):
    p = _write_log(tmp_path / "events.jsonl",
                   [("model_pin_mismatch", _payload("sonnet", "haiku"), "run-1")])
    out = _report(_cli("--event-log", str(p)))
    assert out["events_read"] == 1
    assert out["labels"]["verdict:R3.3"] == "not-checked"
    assert out["violations"] == []


def test_ac5_output_key_set_and_complaints(tmp_path):
    p = _write_log(tmp_path / "events.jsonl",
                   [(_event_type(), _payload("sonnet", "haiku"), "run-1")])
    out = _report(_cli("--event-log", str(p)))
    assert set(out) == {"passed", "requirements", "violations", "labels",
                        "complaints", "events_read", "run_id"}
    assert out["complaints"] == []
    assert out["run_id"] is None


def test_ac6_run_id_filter(tmp_path):
    p = _write_log(tmp_path / "events.jsonl", [
        (_event_type(), _payload("sonnet", "haiku"), "B"),
        (_event_type(), _payload("sonnet", "sonnet"), "A"),
    ])
    a = _report(_cli("--event-log", str(p), "--run-id", "A"))
    assert a["labels"]["verdict:R3.3"] != "failed"
    assert a["events_read"] == 1
    assert a["run_id"] == "A"
    b = _report(_cli("--event-log", str(p), "--run-id", "B"))
    assert b["labels"]["verdict:R3.3"] == "failed"


def test_ac7_empty_file_is_not_a_pass(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    out = _report(_cli("--event-log", str(p)))
    assert out["events_read"] == 0
    assert out["passed"] is False
    for req in ALL_REQS:
        assert out["labels"][f"verdict:{req}"] == "not-checked"


def _malformed(tmp_path: Path) -> "list[str]":
    p = tmp_path / "bad.jsonl"
    p.write_text('{"a": 1}\n{not json\n', encoding="utf-8")
    return ["--event-log", str(p)]


def _bad_utf8(tmp_path: Path) -> "list[str]":
    p = tmp_path / "utf8.jsonl"
    p.write_bytes(b"\xff\xfe\x00\n")
    return ["--event-log", str(p)]


@pytest.mark.parametrize("case", [
    "no_event_log", "unknown_flag", "missing_file", "malformed_line", "invalid_utf8"])
def test_ac8_usage_and_input_errors_exit_2(tmp_path, case):
    args = {
        "no_event_log": lambda: [],
        "unknown_flag": lambda: ["--event-log", str(tmp_path / "x.jsonl"), "--bogus"],
        "missing_file": lambda: ["--event-log", str(tmp_path / "nope.jsonl")],
        "malformed_line": lambda: _malformed(tmp_path),
        "invalid_utf8": lambda: _bad_utf8(tmp_path),
    }[case]()
    proc = _cli(*args)
    assert proc.returncode == 2, f"rc={proc.returncode} stderr={proc.stderr!r}"
    assert proc.stdout == ""
    assert proc.stderr.startswith("bd_l3: "), proc.stderr
    if case == "malformed_line":
        assert "2" in proc.stderr


def test_ac9_public_surface_unchanged():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415
    import types  # noqa: PLC0415

    assert set(bd_l3.__all__) == {
        "REQUIREMENTS", "AWAITING_PRODUCER", "SILENT_BACKENDS",
        "LABEL_EXCEPTIONS", "check_bd_l3", "validate_report"}
    public = {n for n, v in vars(bd_l3).items()
              if not n.startswith("_") and not isinstance(v, types.ModuleType)}
    assert public == set(bd_l3.__all__), sorted(public - set(bd_l3.__all__))


def test_ac10_cli_is_read_only(tmp_path):
    p = _write_log(tmp_path / "events.jsonl",
                   [(_event_type(), _payload("sonnet", "haiku"), "run-1")])
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    out = _report(_cli("--event-log", str(p)))
    assert out["events_read"] == 1, "CLI must actually have read the log"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
