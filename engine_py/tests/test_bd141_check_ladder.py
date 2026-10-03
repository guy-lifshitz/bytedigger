"""RED tests for bd#141 item 3 -- check ladder (``check_ladder``).

Spec: docs/decisions/2026-10-01-bd141-check-ladder.md (ACs L1-L19, r3 / RED rev3).

The module under test does not exist yet. Every AC reaches it through a real
``python -m bytedigger_engine.check_ladder`` subprocess or a lazy ``_cl()`` import
inside the test, so the file collects and each AC fails at assert time
independently (workflows.md 1q). Nothing here mocks the unit under test.
Classifiers are REAL executables (python scripts written to tmp_path and run as
[sys.executable, script]); spawn and stdin content are proved by side-effect
files. Timeouts are pre-staged (timeout_s small, fake sleeps 10 s), never raced
(workflows.md 1i).

L16-L18 drive ``workflows.phase_5_implement._invoke_validation_llm``; only its
collaborators ``invoke_llm_subprocess`` (the Opus call) and ``_emit_safe`` are
monkeypatched, never the unit under test.

Sibling tests found (phase-5 validation tests driving _invoke_validation_llm):
  test_gh963_validation_execution_failure.py (model for the seam used here),
  test_phase_5_implement_A3398552.py, test_phase_5_graphfirst_DA48BEAC.py,
  test_gh705_callsite_stable_prefix.py, test_7C4D70ED_red_executability_check.py,
  test_llm_subprocess_allowed_tools.py.
Registration/hygiene siblings: test_core_boundary.py, test_bd141_close_gate.py.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import itertools
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ENGINE_PY = Path(__file__).resolve().parents[1]
MODULE_FILE = ENGINE_PY / "bytedigger_engine" / "check_ladder.py"


def _cl() -> Any:
    """Lazy import of the unit under test (fails at assert time, not collect)."""
    return importlib.import_module("bytedigger_engine.check_ladder")


# --------------------------------------------------------------------------
# Fake classifier (real executable)
# --------------------------------------------------------------------------

def _fake(tmp_path: Path, name: str, *, out: str | None = None, rc: int = 0,
          sleep: float = 0.0, stdin_file: Path | None = None,
          spawn_file: Path | None = None) -> list[str]:
    """Write a python script; return its argv. ``out`` is printed as stdout."""
    lines = ["import sys, time"]
    if spawn_file is not None:
        lines.append(f"open({str(spawn_file)!r}, 'w').write('spawned')")
    lines.append("data = sys.stdin.read()")
    if stdin_file is not None:
        lines.append(f"open({str(stdin_file)!r}, 'w', encoding='utf-8').write(data)")
    if sleep:
        lines.append(f"time.sleep({sleep!r})")
    if out is not None:
        lines.append(f"print({out!r})")
    lines.append(f"sys.exit({rc})")
    script = tmp_path / f"{name}.py"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [sys.executable, str(script)]


def _ans(label: str, conf: Any, **extra: Any) -> str:
    return json.dumps({"label": label, "confidence": conf, **extra})


def _f(rule: str = "r", severity: str = "MAJOR", detail: str = "d") -> dict:
    return {"rule": rule, "severity": severity, "detail": detail}


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.check_ladder", *args],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=60,
    )


def _gate_file(tmp_path: Path, text: str = "spec and red text\n") -> Path:
    p = tmp_path / "gate_input.txt"
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# L1-L2 constants and no-approve invariant
# --------------------------------------------------------------------------

def test_L1_constants() -> None:
    """L1: RUNGS / OUTCOMES / MODES exact tuples."""
    cl = _cl()
    assert cl.RUNGS == ("script", "classifier", "llm", "gate")
    assert cl.OUTCOMES == ("reject", "escalate")
    assert cl.MODES == ("shadow", "enforce")
    assert cl.DEFAULT_THRESHOLD == 0.9
    assert cl.DEFAULT_TIMEOUT_S == 30


def test_L2_no_path_returns_approve(tmp_path: Path) -> None:
    """L2: outcome is always in OUTCOMES over the full input matrix."""
    cl = _cl()
    finding_sets = {
        "none": [],
        "minor": [_f(severity="MINOR")],
        "major": [_f(severity="MAJOR")],
    }
    classifiers: dict[str, list[str] | None] = {
        "off": None,
        "pass": _fake(tmp_path, "pass", out=_ans("pass", 1.0)),
        "rej_hi": _fake(tmp_path, "rej_hi", out=_ans("reject", 0.99)),
        "rej_lo": _fake(tmp_path, "rej_lo", out=_ans("reject", 0.1)),
        "error": _fake(tmp_path, "err", rc=3),
        "timeout": _fake(tmp_path, "slow", out=_ans("reject", 1.0), sleep=10),
    }
    for (fk, fs), (ck, cmd), mode in itertools.product(
            finding_sets.items(), classifiers.items(), ("shadow", "enforce")):
        v = cl.prescreen(fs, "gate text", classifier_cmd=cmd, mode=mode, timeout_s=1)
        assert v["outcome"] in cl.OUTCOMES, f"L2: {fk}/{ck}/{mode} -> {v['outcome']!r}"


# --------------------------------------------------------------------------
# L3-L8 prescreen decisions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_L3_major_rejects_at_script_rung(mode: str) -> None:
    """L3: MAJOR -> reject/script, findings = MAJORs only, in order, both modes."""
    cl = _cl()
    fs = [_f("a", "MAJOR"), _f("m", "MINOR"), _f("b", "MAJOR")]
    v = cl.prescreen(fs, "g", mode=mode)
    assert v["outcome"] == "reject" and v["rung"] == "script"
    assert [x["rule"] for x in v["findings"]] == ["a", "b"]
    assert v["mode"] == mode


def test_L4_minor_only_classifier_off_escalates() -> None:
    """L4: MINOR-only, classifier off -> escalate, rung None, findings []."""
    cl = _cl()
    v = cl.prescreen([_f(severity="MINOR")], "g")
    assert v["outcome"] == "escalate" and v["rung"] is None
    assert v["findings"] == []
    assert v["classifier"]["status"] == "off"


def test_L5_enforce_classifier_reject_threshold(tmp_path: Path) -> None:
    """L5: enforce, no MAJOR: conf >= threshold rejects (== counts); below escalates."""
    cl = _cl()
    hi = _fake(tmp_path, "hi", out=_ans("reject", 0.95))
    eq = _fake(tmp_path, "eq", out=_ans("reject", 0.9))
    lo = _fake(tmp_path, "lo", out=_ans("reject", 0.89))
    v = cl.prescreen([], "g", classifier_cmd=hi, mode="enforce", threshold=0.9)
    assert (v["outcome"], v["rung"]) == ("reject", "classifier")
    v = cl.prescreen([], "g", classifier_cmd=eq, mode="enforce", threshold=0.9)
    assert (v["outcome"], v["rung"]) == ("reject", "classifier")
    v = cl.prescreen([], "g", classifier_cmd=lo, mode="enforce", threshold=0.9)
    assert (v["outcome"], v["rung"]) == ("escalate", None)


def test_L6_shadow_classifier_never_changes_outcome(tmp_path: Path) -> None:
    """L6: shadow + classifier reject 1.0 -> escalate, classifier recorded."""
    cl = _cl()
    cmd = _fake(tmp_path, "rej", out=_ans("reject", 1.0))
    v = cl.prescreen([], "g", classifier_cmd=cmd, mode="shadow")
    assert (v["outcome"], v["rung"]) == ("escalate", None)
    assert v["classifier"]["status"] == "ok"
    assert v["classifier"]["label"] == "reject"
    assert v["classifier"]["confidence"] == 1.0


def test_L7_spawn_decision_by_mode(tmp_path: Path) -> None:
    """L7: shadow+MAJOR spawns the classifier; enforce+MAJOR does not."""
    cl = _cl()
    sh_flag = tmp_path / "shadow_spawned"
    en_flag = tmp_path / "enforce_spawned"
    sh = _fake(tmp_path, "sh", out=_ans("pass", 0.5), spawn_file=sh_flag)
    en = _fake(tmp_path, "en", out=_ans("pass", 0.5), spawn_file=en_flag)
    cl.prescreen([_f()], "g", classifier_cmd=sh, mode="shadow")
    cl.prescreen([_f()], "g", classifier_cmd=en, mode="enforce")
    assert sh_flag.exists(), "L7: shadow + MAJOR must still spawn the classifier"
    assert not en_flag.exists(), "L7: enforce + MAJOR must not spawn the classifier"
    v = cl.prescreen([_f()], "g", classifier_cmd=en, mode="enforce")
    assert v["classifier"] == {"status": "skipped", "label": None, "confidence": None,
                               "reasons": None, "rc": None, "ms": 0, "cost_usd": None}


def test_L8_classifier_stdin_payload(tmp_path: Path) -> None:
    """L8: stdin is exactly {"gate_input", "findings"} with all findings."""
    cl = _cl()
    seen = tmp_path / "stdin.json"
    cmd = _fake(tmp_path, "cap", out=_ans("pass", 0.5), stdin_file=seen)
    fs = [_f("a", "MAJOR"), _f("m", "MINOR")]
    cl.prescreen(fs, "gate body é", classifier_cmd=cmd, mode="shadow")
    payload = json.loads(seen.read_text(encoding="utf-8"))
    assert set(payload) == {"gate_input", "findings"}
    assert payload["gate_input"] == "gate body é"
    assert payload["findings"] == fs


# --------------------------------------------------------------------------
# L9 run_classifier statuses
# --------------------------------------------------------------------------

def test_L9_classifier_statuses(tmp_path: Path) -> None:
    """L9: status/rc/label/confidence/cost_usd/reasons contract."""
    cl = _cl()
    run = lambda cmd, t=10: cl.run_classifier(cmd, {"gate_input": "x", "findings": []}, t)  # noqa: E731

    r = run(None)
    assert r["status"] == "off" and r["ms"] == 0
    assert {k: v for k, v in r.items() if k not in ("status", "ms")} == {
        "label": None, "confidence": None, "reasons": None, "rc": None, "cost_usd": None}

    for bad_cmd in ([], "python", ["a", 1]):  # op1: malformed cmd -> error, never raises
        r = run(bad_cmd)
        assert r["status"] == "error" and r["rc"] is None, f"L9: malformed cmd {bad_cmd!r}"

    r = run([str(tmp_path / "no-such-binary")])
    assert r["status"] == "error" and r["rc"] is None

    r = run(_fake(tmp_path, "rc3", rc=3))
    assert r["status"] == "error" and r["rc"] == 3

    t0 = time.monotonic()
    r = run(_fake(tmp_path, "slow", out=_ans("pass", 1.0), sleep=10), 1)
    assert r["status"] == "timeout" and r["rc"] is None
    assert time.monotonic() - t0 < 1 + 5

    bad_outputs = {
        "nonjson": "not json",
        "array": "[1, 2]",
        "approve": _ans("approve", 0.9),
        "unknown": _ans("maybe", 0.9),
        "conf_high": _ans("reject", 1.5),
        "conf_bool": '{"label": "reject", "confidence": true}',
        "conf_str": _ans("reject", "0.9"),
        "conf_neg": _ans("reject", -0.1),
        "conf_nan": '{"label": "reject", "confidence": NaN}',
    }
    for key, out in bad_outputs.items():
        r = run(_fake(tmp_path, f"bad_{key}", out=out))
        assert r["status"] == "error", f"L9: {key} must be error, got {r['status']}"
        assert r["label"] is None, f"L9: {key} label must be None"
        assert r["rc"] == 0, f"L9: {key} exit-0 parse error must carry rc 0, got {r['rc']!r}"
    r = run(_fake(tmp_path, "empty"))
    assert r["status"] == "error", "L9: no stdout line must be error"
    assert r["rc"] == 0, "L9: exit-0 with no stdout line must carry rc 0"

    r = run(_fake(tmp_path, "ok", out=_ans("reject", 0.7, reasons=["a", "b"], cost_usd=0.04)))
    assert r["status"] == "ok" and r["label"] == "reject" and r["confidence"] == 0.7
    assert r["reasons"] == ["a", "b"] and r["cost_usd"] == 0.04 and r["rc"] == 0
    assert isinstance(r["ms"], int) and r["ms"] >= 0

    for key, cost in {"neg": -1, "bool": True, "str": "0.04",
                      "inf": float("inf")}.items():
        r = run(_fake(tmp_path, f"cost_{key}", out=_ans("pass", 0.5, cost_usd=cost)))
        assert r["status"] == "ok" and r["cost_usd"] is None, f"L9: cost {key}"
    r = run(_fake(tmp_path, "reasons_bad", out=_ans("pass", 0.5, reasons="nope")))
    assert r["status"] == "ok" and r["reasons"] == []
    r = run(_fake(tmp_path, "reasons_mixed", out=_ans("pass", 0.5, reasons=[1, "a"])))
    assert r["status"] == "ok" and r["reasons"] == [], "L9: non-str reasons element -> []"


def test_L9_first_non_empty_line_is_used(tmp_path: Path) -> None:
    """L9: the first non-empty stdout line is the JSON object."""
    cl = _cl()
    cmd = _fake(tmp_path, "blank_first", out="\n" + _ans("pass", 0.5) + "\ntrailing junk")
    r = cl.run_classifier(cmd, {"gate_input": "x", "findings": []}, 10)
    assert r["status"] == "ok" and r["label"] == "pass"


# --------------------------------------------------------------------------
# L10-L11
# --------------------------------------------------------------------------

def test_L10_fail_open_in_enforce(tmp_path: Path) -> None:
    """L10: classifier error/timeout, enforce, no MAJOR -> escalate."""
    cl = _cl()
    err = _fake(tmp_path, "err", rc=3, out=_ans("reject", 1.0))
    slow = _fake(tmp_path, "slow", out=_ans("reject", 1.0), sleep=10)
    for cmd, status in ((err, "error"), (slow, "timeout")):
        v = cl.prescreen([], "g", classifier_cmd=cmd, mode="enforce", timeout_s=1)
        assert v["outcome"] == "escalate" and v["rung"] is None
        assert v["classifier"]["status"] == status


def test_L11_argument_validation() -> None:
    """L11: ValueError on bad mode / threshold / timeout / finding shape."""
    cl = _cl()
    bad_calls = [
        dict(findings=[], mode="block"),
        dict(findings=[], threshold=1.5),
        dict(findings=[], threshold=True),
        dict(findings=[], timeout_s=0),
        dict(findings=[], timeout_s=True),
        dict(findings=[{"severity": "MAJOR", "detail": "d"}]),
        dict(findings=[_f(severity="BLOCKER")]),
        dict(findings=["not a dict"]),
        dict(findings="not a list"),
        dict(findings=[], classifier_cmd=[]),
        dict(findings=[], classifier_cmd="python"),
        dict(findings=[], classifier_cmd=["a", 1]),
    ]
    for kw in bad_calls:
        with pytest.raises(ValueError):
            cl.prescreen(gate_input="g", **kw)


def test_L11_value_error_spawns_no_classifier(tmp_path: Path) -> None:
    """L11/op2: validation precedes any spawn; a ValueError leaves the side-effect file absent."""
    cl = _cl()
    bad_kwargs = [
        dict(mode="block"),
        dict(threshold=1.5),
        dict(timeout_s=0),
        dict(findings=[_f(severity="BLOCKER")]),
        dict(findings="not a list"),
    ]
    for i, extra in enumerate(bad_kwargs):
        flag = tmp_path / f"spawned_{i}"
        cmd = _fake(tmp_path, f"ok_{i}", out=_ans("pass", 0.5), spawn_file=flag)
        kw: dict = dict(findings=[], classifier_cmd=cmd, mode="shadow")
        kw.update(extra)
        with pytest.raises(ValueError):
            cl.prescreen(gate_input="g", **kw)
        assert not flag.exists(), f"L11: classifier spawned despite ValueError for {extra!r}"


# --------------------------------------------------------------------------
# L12-L15 CLI
# --------------------------------------------------------------------------

def test_L12_cli_happy_path_matches_prescreen(tmp_path: Path) -> None:
    """L12: one JSON line equal to the op2 verdict; rc 0 for reject and escalate."""
    cl = _cl()
    gate = _gate_file(tmp_path)
    text = gate.read_text(encoding="utf-8")
    fpath = tmp_path / "findings.json"
    fpath.write_text(json.dumps([_f("a", "MAJOR")]), encoding="utf-8")

    proc = _cli("prescreen", "--gate-input", str(gate), "--findings", str(fpath))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert len(proc.stdout.strip().splitlines()) == 1
    assert json.loads(proc.stdout) == cl.prescreen([_f("a", "MAJOR")], text)
    assert json.loads(proc.stdout)["outcome"] == "reject"

    proc = _cli("prescreen", "--gate-input", str(gate))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert len(proc.stdout.strip().splitlines()) == 1
    assert json.loads(proc.stdout) == cl.prescreen([], text)
    assert json.loads(proc.stdout)["outcome"] == "escalate"


def test_L12_cli_with_classifier_cmd(tmp_path: Path) -> None:
    """L12: --classifier-cmd/--mode/--threshold/--timeout-s are honoured."""
    gate = _gate_file(tmp_path)
    cmd = _fake(tmp_path, "rej", out=_ans("reject", 0.95))
    proc = _cli("prescreen", "--gate-input", str(gate), "--classifier-cmd", json.dumps(cmd),
                "--mode", "enforce", "--threshold", "0.9", "--timeout-s", "20")
    assert proc.returncode == 0, proc.stderr[-300:]
    v = json.loads(proc.stdout)
    assert (v["outcome"], v["rung"], v["mode"]) == ("reject", "classifier", "enforce")
    assert v["classifier"]["status"] == "ok"


def test_L13_cli_usage_errors_rc2(tmp_path: Path) -> None:
    """L13: rc 2, empty stdout, message on stderr for each usage error."""
    gate = _gate_file(tmp_path)
    not_json = tmp_path / "nj.json"
    not_json.write_text("{{{", encoding="utf-8")
    obj = tmp_path / "obj.json"
    obj.write_text("{}", encoding="utf-8")
    bad_sev = tmp_path / "bad_sev.json"
    bad_sev.write_text('[{"rule":"r","severity":"BLOCKER","detail":"d"}]', encoding="utf-8")
    g = str(gate)
    cases = {
        "no_subcommand": [],
        "unknown_subcommand": ["frobnicate", "--gate-input", g],
        "unknown_flag": ["prescreen", "--gate-input", g, "--bogus", "1"],
        "missing_gate_input": ["prescreen"],
        "missing_gate_file": ["prescreen", "--gate-input", str(tmp_path / "nope.txt")],
        "findings_not_json": ["prescreen", "--gate-input", g, "--findings", str(not_json)],
        "findings_object": ["prescreen", "--gate-input", g, "--findings", str(obj)],
        "findings_missing_file": ["prescreen", "--gate-input", g, "--findings",
                                  str(tmp_path / "nope.json")],
        "cmd_empty_array": ["prescreen", "--gate-input", g, "--classifier-cmd", "[]"],
        "cmd_string": ["prescreen", "--gate-input", g, "--classifier-cmd", '"x"'],
        "mode_block": ["prescreen", "--gate-input", g, "--mode", "block"],
        "threshold_high": ["prescreen", "--gate-input", g, "--threshold", "1.5"],
        "timeout_zero": ["prescreen", "--gate-input", g, "--timeout-s", "0"],
        "finding_bad_severity": ["prescreen", "--gate-input", g, "--findings", str(bad_sev)],
    }
    for label, args in cases.items():
        proc = _cli(*args)
        assert proc.returncode == 2, f"L13 {label}: rc 2, got {proc.returncode}; {proc.stderr[-200:]!r}"
        assert proc.stdout == "", f"L13 {label}: stdout must be empty"
        assert proc.stderr.strip(), f"L13 {label}: stderr message required"


def test_L14_cli_log_journal(tmp_path: Path) -> None:
    """L14: --log creates dir, appends one line per run, no gate text, reasons -> int."""
    secret = "SECRET-SPEC-TEXT-12345"
    gate = tmp_path / "gate_crlf.txt"
    gate.write_bytes((secret + "\r\nline2\r\n").encode("utf-8"))  # CRLF: hash must be of raw bytes
    cmd = _fake(tmp_path, "rr", out=_ans("reject", 0.8, reasons=[secret, "second"]))
    log = tmp_path / "newdir" / "sub" / "journal.jsonl"
    args = ["prescreen", "--gate-input", str(gate), "--classifier-cmd", json.dumps(cmd),
            "--log", str(log)]
    p1 = _cli(*args)
    p2 = _cli(*args)
    assert p1.returncode == 0 and p2.returncode == 0, (p1.stderr, p2.stderr)
    raw = log.read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 2, f"L14: two runs -> two lines, got {len(lines)}"
    rec = json.loads(lines[0])
    assert rec["ts"].endswith("Z")
    assert rec["gate_input_sha256"] == hashlib.sha256(gate.read_bytes()).hexdigest()
    assert rec["verdict"]["classifier"]["reasons"] == 2
    assert isinstance(rec["verdict"]["classifier"]["reasons"], int)
    assert secret not in raw, "L14: gate-input / reasons text must not be journaled"
    # stdout verdict keeps the real reasons list
    assert json.loads(p1.stdout)["classifier"]["reasons"] == [secret, "second"]


def test_L15_cli_log_io_error_is_non_fatal(tmp_path: Path) -> None:
    """L15: log parent is a regular file -> verdict unchanged, rc 0, stderr non-empty."""
    gate = _gate_file(tmp_path)
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    base = _cli("prescreen", "--gate-input", str(gate))
    proc = _cli("prescreen", "--gate-input", str(gate), "--log", str(blocker / "j.jsonl"))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert proc.stdout == base.stdout and proc.stdout.strip()
    assert proc.stderr.strip(), "L15: journal I/O error must be reported on stderr"


# --------------------------------------------------------------------------
# L16-L18 phase-5 shadow call
# --------------------------------------------------------------------------

def _p5() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_5_implement")


def _p5_ctx(**org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"complexity": "SIMPLE", **org_extra},
        question="bd141 check ladder", session_id="s", persona="hal",
        framework=None, domain=None,
    )


def _p5_prev(tmp_path: Path, prompt: str) -> Any:
    from bytedigger_engine.contracts import StepResult
    data = {
        "prompt": prompt, "doc_path": str(tmp_path / "d.md"),
        "spec_path": str(tmp_path / "s.md"), "red_log_path": str(tmp_path / "r.log"),
        "cycle": 1, "stable_prefix": "",
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="check_red_executable")


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prompt: str, **org: Any):
    """Run _invoke_validation_llm with the Opus call + event sink replaced."""
    from bytedigger_engine.contracts import StepResult
    p5 = _p5()
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    def fake_llm(**kw: Any) -> Any:
        calls.append(kw)
        return StepResult(status="ok", data={"raw_response": "VERDICT: PASS\n", **kw["extra_data"]},
                          duration_ms=0, step_name="invoke_validation_llm")

    def fake_emit(event_type: str, payload: dict, severity: str = "info") -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(p5, "invoke_llm_subprocess", fake_llm)
    monkeypatch.setattr(p5, "_emit_safe", fake_emit)
    result = p5._invoke_validation_llm(_p5_ctx(**org), _p5_prev(tmp_path, prompt))
    return result, calls, events


_ABSENT = object()
_EVENT_KEYS = {"outcome", "rung", "mode", "classifier_status", "classifier_label",
               "classifier_confidence", "classifier_ms", "cost_usd", "cycle", "phase"}


def _baseline(monkeypatch, tmp_path: Path, prompt: str) -> dict:
    """Full kwargs of the no-config run (the L16 baseline)."""
    _r, calls, _e = _drive(monkeypatch, tmp_path, prompt)
    assert len(calls) == 1
    return calls[0]


def _assert_same_as_baseline(kw: dict, base: dict, what: str) -> None:
    """All kwargs equal baseline; extra_data equal after removing ``prescreen``."""
    assert set(kw) == set(base), f"{what}: kwarg names differ"
    for k in base:
        if k == "extra_data":
            continue
        assert kw[k] == base[k], f"{what}: kwarg {k!r} differs from baseline"
    assert {k: v for k, v in kw["extra_data"].items() if k != "prescreen"} == base["extra_data"], (
        f"{what}: extra_data differs from baseline beyond the prescreen key")


def _assert_gate_result_passthrough(result: Any, call: dict, what: str) -> None:
    """The returned result is exactly what the fake gate built for this call."""
    assert result.status == "ok", f"{what}: status"
    assert result.step_name == "invoke_validation_llm", f"{what}: step_name"
    assert result.data == {"raw_response": "VERDICT: PASS\n", **call["extra_data"]}, (
        f"{what}: the pre-screen must not rewrite the gate result")


def _events(events: list) -> list[dict]:
    return [p for (t, p) in events if t == "prescreen_verdict"]


@pytest.mark.parametrize("cfg", [_ABSENT, "x", None, [], {}, {"timeout_s": 5},
                                 {"classifier_cmd": None}],
                         ids=["absent", "str", "none", "list", "empty-dict", "no-classifier-cmd",
                              "classifier-cmd-none"])
def test_L16_no_prescreen_config_is_byte_for_byte_today(tmp_path: Path, monkeypatch, cfg) -> None:
    """L16: no/invalid-shape prescreen config -> no event, no key, kwargs == no-config baseline."""
    base = _baseline(monkeypatch, tmp_path, "PROMPT-A")
    assert base["prompt"] == "PROMPT-A" and base["hard_gate"] is True
    p5 = _p5()
    assert base["model"] == p5._default_validation_model()
    assert base["gate_label"] == "validation"
    assert base["allowed_tools"] == ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"]
    assert base["timeout_sec"] == p5._resolve_validation_timeout_sec({"complexity": "SIMPLE"})
    assert "prescreen" not in base["extra_data"]
    assert set(base["extra_data"]) == {"doc_path", "spec_path", "red_log_path",
                                       "red_test_paths", "cycle", "red_commit_sha", "preflight"}
    org = {} if cfg is _ABSENT else {"prescreen": cfg}
    _result, calls, events = _drive(monkeypatch, tmp_path, "PROMPT-A", **org)
    assert len(calls) == 1
    assert calls[0] == base, "L16: every kwarg incl. extra_data must equal the no-config run"
    assert "prescreen" not in calls[0]["extra_data"]
    assert not _events(events)
    # Today's behaviour must not even touch the new module.
    assert "prescreen" not in calls[0]["extra_data"]
    # Forcing: the same run WITH a valid config must differ only by the added key
    # (proves this test pins the feature, not just emptiness).
    if cfg is _ABSENT:
        cmd = _fake(tmp_path, "pass", out=_ans("pass", 0.5))
        _r2, calls2, events2 = _drive(monkeypatch, tmp_path, "PROMPT-A",
                                      prescreen={"classifier_cmd": cmd})
        assert len(_events(events2)) == 1 and "prescreen" in calls2[0]["extra_data"]


@pytest.mark.parametrize("extra_cfg", [{}, {"mode": "enforce"}])
def test_L17_shadow_call_event_and_gate_still_runs(tmp_path: Path, monkeypatch, extra_cfg) -> None:
    """L17: classifier reject 1.0 -> escalate event; Opus call identical; mode ignored."""
    base = _baseline(monkeypatch, tmp_path, "PROMPT-B")
    seen = tmp_path / "stdin.json"
    cmd = _fake(tmp_path, "rej", out=_ans("reject", 1.0, reasons=["quote of spec"], cost_usd=0.04),
                stdin_file=seen)
    cfg = {"classifier_cmd": cmd, "timeout_s": 20, **extra_cfg}
    result, calls, events = _drive(monkeypatch, tmp_path, "PROMPT-B", prescreen=cfg)

    evs = _events(events)
    assert len(evs) == 1, f"L17: exactly one prescreen_verdict event, got {len(evs)}"
    ev = evs[0]
    assert set(ev) == _EVENT_KEYS, f"L17: event key set {sorted(ev)}"
    assert ev["outcome"] == "escalate"
    assert ev["classifier_status"] == "ok"
    assert ev["classifier_label"] == "reject" and ev["classifier_confidence"] == 1.0
    assert ev["cost_usd"] == 0.04 and ev["cycle"] == 1 and ev["phase"] == 5
    assert ev["mode"] == "shadow"
    assert isinstance(ev["classifier_ms"], int)

    assert len(calls) == 1, "L17: the gate (Opus call) must still run exactly once"
    _assert_same_as_baseline(calls[0], base, "L17")
    pre = calls[0]["extra_data"]["prescreen"]
    assert pre["outcome"] == "escalate"
    assert "reasons" not in pre["classifier"]
    _assert_gate_result_passthrough(result, calls[0], "L17")
    payload =json.loads(seen.read_text(encoding="utf-8"))
    assert payload["gate_input"] == "PROMPT-B" and payload["findings"] == []


def _off_classifier() -> dict:
    return {"status": "off", "label": None, "confidence": None, "reasons": None,
            "rc": None, "ms": 0, "cost_usd": None}


def _assert_config_error(evs: list[dict]) -> None:
    assert len(evs) == 1, f"op4.5: exactly one event, got {len(evs)}"
    ev = evs[0]
    assert set(ev) == _EVENT_KEYS, f"op4.2: event key set {sorted(ev)}"
    assert ev["classifier_status"] == "config-error"
    assert ev["outcome"] == "escalate" and ev["mode"] == "shadow"
    for k in ("rung", "classifier_label", "classifier_confidence", "classifier_ms", "cost_usd"):
        assert ev[k] is None, f"op4.5: {k} must be None, got {ev[k]!r}"
    assert ev["cycle"] == 1 and ev["phase"] == 5


def test_L17b_prescreen_reject_verdict_does_not_skip_gate(tmp_path: Path, monkeypatch) -> None:
    """L17b(a): a substituted prescreen returning reject/script -> Opus call still runs once."""
    base = _baseline(monkeypatch, tmp_path, "PROMPT-D")
    cl = _cl()  # fails inside the body while the module does not exist
    called: list[dict] = []

    stub = {"outcome": "reject", "rung": "script", "mode": "shadow",
            "findings": [_f()], "classifier": {**_off_classifier(), "reasons": None}}

    def fake_prescreen(**kw: Any) -> dict:
        called.append(kw)
        return stub

    monkeypatch.setattr(cl, "prescreen", fake_prescreen)
    cmd = _fake(tmp_path, "unused", out=_ans("pass", 0.5))
    result, calls, events = _drive(monkeypatch, tmp_path, "PROMPT-D",
                                   prescreen={"classifier_cmd": cmd})
    assert len(called) == 1, "L17b: phase 5 must call check_ladder.prescreen via the module attribute"
    assert called[0].get("mode") == "shadow" and called[0].get("findings") == []
    assert called[0].get("gate_input") == "PROMPT-D" and called[0].get("classifier_cmd") == cmd
    assert len(calls) == 1, "L17b(a): reject verdict must not skip or replace the Opus call"
    _assert_same_as_baseline(calls[0], base, "L17b(a)")
    assert called[0].get("timeout_s") == cl.DEFAULT_TIMEOUT_S, (
        "L17b(a): config without timeout_s -> DEFAULT_TIMEOUT_S passed to prescreen")
    evs = _events(events)
    assert len(evs) == 1
    assert evs[0] == {"outcome": "reject", "rung": "script", "mode": "shadow",
                      "classifier_status": "off", "classifier_label": None,
                      "classifier_confidence": None, "classifier_ms": 0, "cost_usd": None,
                      "cycle": 1, "phase": 5}
    expected_pre = {**stub, "classifier": {k: v for k, v in stub["classifier"].items()
                                           if k != "reasons"}}
    assert calls[0]["extra_data"]["prescreen"] == expected_pre
    _assert_gate_result_passthrough(result, calls[0], "L17b(a)")


def test_L17b_prescreen_raising_is_config_error(tmp_path: Path, monkeypatch) -> None:
    """L17b(b): prescreen raises RuntimeError -> config-error event, Opus call baseline."""
    base = _baseline(monkeypatch, tmp_path, "PROMPT-E")
    cl = _cl()

    def boom(**_kw: Any) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(cl, "prescreen", boom)
    cmd = _fake(tmp_path, "unused", out=_ans("pass", 0.5))
    result, calls, events = _drive(monkeypatch, tmp_path, "PROMPT-E",
                                   prescreen={"classifier_cmd": cmd})
    _assert_config_error(_events(events))
    assert len(calls) == 1
    assert "prescreen" not in calls[0]["extra_data"]
    _assert_same_as_baseline(calls[0], base, "L17b(b)")
    assert result.status == "ok"


@pytest.mark.parametrize("bad", ["not-a-list", "valid-cmd-timeout-0"])
def test_L18_bad_config_swallowed_gate_runs(tmp_path: Path, monkeypatch, bad) -> None:
    """L18: bad classifier_cmd / timeout_s=0 -> config-error event, gate runs, no exception."""
    base = _baseline(monkeypatch, tmp_path, "PROMPT-C")
    spawned = tmp_path / "spawned"
    if bad == "not-a-list":
        cfg: dict = {"classifier_cmd": "not-a-list"}
    else:
        cmd = _fake(tmp_path, "v", out=_ans("pass", 0.5), spawn_file=spawned)
        cfg = {"classifier_cmd": cmd, "timeout_s": 0}
    result, calls, events = _drive(monkeypatch, tmp_path, "PROMPT-C", prescreen=cfg)
    _assert_config_error(_events(events))
    assert not spawned.exists(), "L18: an invalid config must not spawn the classifier"
    assert len(calls) == 1
    assert "prescreen" not in calls[0]["extra_data"]
    _assert_same_as_baseline(calls[0], base, "L18")
    assert result.status == "ok"


# --------------------------------------------------------------------------
# L19 hygiene
# --------------------------------------------------------------------------

def test_L19_registered_clean_stdlib_only() -> None:
    """L19: manifest + mypy list; core-boundary clean; no Cyrillic; stdlib-only imports."""
    manifest = json.loads((ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    assert "check_ladder.py" in manifest["core_modules"], "L19: missing from core_modules"
    strict = (ENGINE_PY / "bytedigger_engine" / "mypy-strict-modules.txt").read_text(
        encoding="utf-8").splitlines()
    assert "check_ladder.py" in [ln.strip() for ln in strict], "L19: missing from mypy strict list"
    assert MODULE_FILE.exists(), "L19: check_ladder.py must exist"
    source = MODULE_FILE.read_text(encoding="utf-8")
    cyrillic = "[" + chr(0x400) + "-" + chr(0x4FF) + "]"
    assert re.search(cyrillic, source) is None, "L19: Cyrillic char in module"
    tree = ast.parse(source)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "L19: relative import (non-stdlib) in module"
            mods.add((node.module or "").split(".")[0])
    non_std = sorted(m for m in mods if m not in sys.stdlib_module_names)
    assert not non_std, f"L19: non-stdlib imports: {non_std}"
    proc = subprocess.run(
        [sys.executable, "core-boundary-lint.py", "--json"],
        cwd=str(ENGINE_PY.parent), capture_output=True, text=True, timeout=120,
    )
    report = json.loads(proc.stdout)
    assert "check_ladder.py" not in json.dumps(report.get("violations", [])), (
        f"L19: boundary violation for check_ladder.py: {report.get('violations')}")
    assert "check_ladder.py" not in json.dumps(report.get("errors", [])), (
        f"L19: boundary lint error for check_ladder.py: {report.get('errors')}")
    assert "HAL_" not in source, "L19: no HAL_ names in the module"
    assert re.search(r"(?i)\bjev\b|typesafe", source) is None, "L19: no vendor name in the module"
