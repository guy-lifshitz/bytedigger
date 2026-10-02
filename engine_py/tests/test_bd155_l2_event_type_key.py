"""RED tests for bd#155 -- `check_bd_l2` reads the on-disk `event_type` key.

Spec: docs/decisions/2026-10-02-bd155-l2-event-type-key.md (AC1-AC8, r1).

Every `bytedigger_engine.*` import is inside a test body or helper, so the file
collects cleanly before `conformance/_event_type.py` exists and each AC fails
at assert time. Logs are written with the real `EventLog.append` and read back
by parsing the JSONL lines of the file. Nothing is mocked.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ENGINE_PY = Path(__file__).resolve().parents[1]
CONFORMANCE = ENGINE_PY / "bytedigger_engine" / "conformance"
SHA = "sha256:" + "0" * 64
ALL_REQS = ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")

EVENT_FOR = {
    "R2.1": "red_test_outcome",
    "R2.2": "red_stub_passability_violation",
    "R2.3": "acceptance_criteria_declared",
    "R2.4": "gate_decision",
    "R2.5": "known_reds_ledger_scan",
    "R2.6": "baseline_delta_gate_verdict",
}

PASSING: "dict[str, dict[str, Any]]" = {
    "R2.1": {"group": "x", "exit_code": 1, "n_passed": 1, "n_failed": 1, "phase": 5},
    "R2.2": {"hits": []},
    "R2.3": {"criteria": [{"binds_observable_effect": True}]},
    "R2.4": {"gate": "g", "raised": "E", "outcome": "failed"},
    "R2.5": {"rows": [{"issue": "#1", "status": "active"}]},
    "R2.6": {"verdict": "pass", "baseline_source": "main"},
}
VIOLATING: "dict[str, dict[str, Any]]" = {
    "R2.1": {"group": "x", "exit_code": 5, "n_passed": 0, "n_failed": 0, "phase": 5},
    "R2.2": {"hits": ["m"]},
    "R2.3": {"criteria": [{"binds_observable_effect": False}]},
    "R2.4": {"gate": "g", "raised": "E", "outcome": "passed"},
    "R2.5": {"rows": [{"issue": "", "status": "active"}]},
    "R2.6": {"verdict": "pass", "baseline_source": ""},
}


def _write_log(path: Path, events: "list[tuple[str, dict]]") -> Path:
    from bytedigger_engine.event_log import EventLog  # noqa: PLC0415

    log = EventLog(path)
    for event_type, payload in events:
        log.append(event_type, payload, run_id="run-1")
    return path


def _read(path: Path) -> "list[dict[str, Any]]":
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _check(path: Path):
    from bytedigger_engine.conformance.bd_l2 import check_bd_l2  # noqa: PLC0415

    return check_bd_l2(_read(path))


def _passing_log(tmp_path: Path) -> Path:
    return _write_log(tmp_path / "events.jsonl",
                      [(EVENT_FOR[r], PASSING[r]) for r in ALL_REQS])


def test_ac1_real_event_log_r21_violation_fails(tmp_path):
    p = _write_log(tmp_path / "events.jsonl",
                   [("red_test_outcome", VIOLATING["R2.1"])])
    report = _check(p)
    assert report.labels["verdict:R2.1"] == "failed"
    assert len(report.violations) == 1
    assert report.violations[0].startswith("R2.1:")
    assert report.labels["ADV-4"] == "executed"


@pytest.mark.parametrize("req", ALL_REQS)
def test_ac2_each_requirement_violating_event_fails(tmp_path, req):
    p = _write_log(tmp_path / "events.jsonl", [(EVENT_FOR[req], VIOLATING[req])])
    report = _check(p)
    assert report.labels[f"verdict:{req}"] == "failed"
    assert any(v.startswith(f"{req}:") for v in report.violations)


def test_ac2_all_six_passing_events_pass(tmp_path):
    report = _check(_passing_log(tmp_path))
    for req in ALL_REQS:
        assert report.labels[f"verdict:{req}"] == "passed", req
    assert report.violations == ()
    assert report.passed is True


def test_ac3_adversaries_executed_on_real_log(tmp_path):
    report = _check(_passing_log(tmp_path))
    for adv in ("ADV-3", "ADV-4", "ADV-5", "ADV-6"):
        assert report.labels[adv] == "executed", adv


def test_ac4_legacy_type_key_unchanged():
    from bytedigger_engine.conformance.bd_l2 import check_bd_l2  # noqa: PLC0415

    bad = check_bd_l2([{"type": "red_test_outcome", "payload": VIOLATING["R2.1"]}])
    good = check_bd_l2([{"type": "red_test_outcome", "payload": PASSING["R2.1"]}])
    assert bad.labels["verdict:R2.1"] == "failed"
    assert good.labels["verdict:R2.1"] == "passed"


def test_ac5_foreign_event_type_is_filtered(tmp_path):
    p = _write_log(tmp_path / "events.jsonl",
                   [("some_other_event", VIOLATING["R2.1"])])
    report = _check(p)
    assert report.labels["verdict:R2.1"] == "not-checked"
    assert report.violations == ()


def test_ac6_precedence_bd_l2():
    from bytedigger_engine.conformance.bd_l2 import check_bd_l2  # noqa: PLC0415

    report = check_bd_l2([{"event_type": "some_other_event",
                           "type": "red_test_outcome",
                           "payload": VIOLATING["R2.1"]}])
    assert report.labels["verdict:R2.1"] == "not-checked"
    assert report.violations == ()


def test_ac6_precedence_bd_l3():
    from bytedigger_engine.conformance import attest  # noqa: PLC0415
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415

    payload = {
        "step_name": "s1",
        "backend": "claude-subprocess",
        "model_requested": "sonnet",
        "prompt_sha256": SHA,
        "injections": [{"source_id": "role-template", "sha256": SHA}],
        "declared_capabilities": None,
        "capability_enforcement": "runtime-allowlist",
        "observed_model": "haiku",
        "observed_tools": None,
    }
    report = check_bd_l3([{"event_type": "some_other_event",
                           "type": attest.EVENT_TYPE, "payload": payload}])
    assert report.violations == ()

    control = check_bd_l3([{"event_type": attest.EVENT_TYPE,
                            "type": "some_other_event", "payload": payload}])
    assert control.labels["verdict:R3.3"] == "failed"


def test_ac6_resolver_event_type_wins_over_type():
    from bytedigger_engine.conformance._event_type import event_type_of  # noqa: PLC0415

    assert event_type_of({"event_type": "A", "type": "B"}) == "A"


@pytest.mark.parametrize("bad", ["absent", "", None, 5])
def test_ac6_resolver_falls_back_to_type(bad):
    from bytedigger_engine.conformance._event_type import event_type_of  # noqa: PLC0415

    event: "dict[str, Any]" = {"type": "B"}
    if bad != "absent":
        event["event_type"] = bad
    assert event_type_of(event) == "B"


def test_ac6_resolver_neither_key_is_none():
    from bytedigger_engine.conformance._event_type import event_type_of  # noqa: PLC0415

    assert event_type_of({"payload": {}}) is None


def _inline_key_reads(path: Path) -> "list[str]":
    keys = {"type", "event_type"}
    found: "list[str]" = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in keys):
            found.append(f"get({node.args[0].value!r}) line {node.lineno}")
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value in keys:
                found.append(f"[{sl.value!r}] line {node.lineno}")
    return found


@pytest.mark.parametrize("name", ["bd_l2", "bd_l3"])
def test_ac7_no_inline_event_type_key_read(name):
    assert _inline_key_reads(CONFORMANCE / f"{name}.py") == []


@pytest.mark.parametrize("name,func", [("bd_l2", "check_bd_l2"), ("bd_l3", "check_bd_l3")])
def test_ac7_checker_function_calls_resolver(name, func):
    tree = ast.parse((CONFORMANCE / f"{name}.py").read_text())
    fdefs = [n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == func]
    assert len(fdefs) == 1, f"{func} FunctionDef not found exactly once"
    calls = [n for n in ast.walk(fdefs[0])
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_event_type_of"]
    assert calls, f"{func} does not call _event_type_of"


def test_ac7_both_checkers_bind_the_shared_resolver():
    from bytedigger_engine.conformance import _event_type, bd_l2, bd_l3  # noqa: PLC0415

    assert bd_l2._event_type_of is _event_type.event_type_of
    assert bd_l3._event_type_of is _event_type.event_type_of


def test_ac8_public_surfaces_unchanged():
    from bytedigger_engine.conformance import bd_l2, bd_l3  # noqa: PLC0415

    assert set(bd_l2.__all__) == {"REQUIREMENTS", "AWAITING_PRODUCER", "ENFORCEMENT",
                                  "check_bd_l2", "validate_report"}
    assert set(bd_l3.__all__) == {"REQUIREMENTS", "AWAITING_PRODUCER", "SILENT_BACKENDS",
                                  "LABEL_EXCEPTIONS", "check_bd_l3", "validate_report"}
    for mod in (bd_l2, bd_l3):
        public = {n for n in vars(mod) if not n.startswith("_")}
        assert public == set(mod.__all__), sorted(public ^ set(mod.__all__))


def test_ac8_importing_resolver_adds_only_itself():
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(ENGINE_PY)!r})\n"
        "import bytedigger_engine.conformance\n"
        "before = set(sys.modules)\n"
        "import bytedigger_engine.conformance._event_type\n"
        "print(json.dumps(sorted(set(sys.modules) - before)))\n"
    )
    env = os.environ.copy()
    env.pop("PYTHONSAFEPATH", None)
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ENGINE_PY), env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    delta = json.loads(proc.stdout.strip().splitlines()[-1])
    assert set(delta) == {"bytedigger_engine.conformance._event_type"}
