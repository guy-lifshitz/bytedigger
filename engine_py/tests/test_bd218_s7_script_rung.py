"""bd#218 step 7 RED -- the pre-GREEN gate's pre-screen gets a real script rung (shadow, add-only, no LLM).

Spec: docs/decisions/2026-10-10-bd218-s7-prescreen-script-rung.md (ACs AC1-AC12, spec r1).

New symbol (imported lazily so each test fails at assert/attribute time, not collection):
  check_ladder.script_findings(rung) -> list[Finding]
New behaviour: phase_5_implement._invoke_validation_llm feeds script_findings(preflight rung record) to
check_ladder.prescreen (always mode="shadow"), also with no classifier, after the preflight rung block.

Receipts are REAL (real tmp git repo, real preflight.run_engine_preflight). Only the LLM call, the event sink
and (as the s1/s2 tests do) the engine-side receipt producer inside _invoke_validation_llm are replaced, so the
pre-staged receipt is the one that is read. check_ladder.prescreen is the real function everywhere except AC10
(patched to raise). No singleton resource is contended (workflows.md 1i): every receipt state and every
classifier script is pre-staged deterministically; the AC7 timeout case sleeps far longer than its timeout_s.
AC12 (siblings pass unchanged, incl. the amended s1 test_AC7) is verified by running those files, not here.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(180)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_KNOWN_REDS_TODAY", raising=False)
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


CALC = "def add(a, b):\n    return a + b\n"
RED_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"
STUB_SRC = (
    "from unittest.mock import patch\n"
    "from calc import add\n\n\n"
    "def test_add():\n"
    "    with patch(\"calc.add\", return_value=4):\n"
    "        assert add(1, 2) == 4\n"
)
TEST_REL = "tests/test_calc.py"
EVENT_KEYS = {"outcome", "rung", "mode", "classifier_status", "classifier_label",
              "classifier_confidence", "classifier_ms", "cost_usd", "cycle", "phase"}


def _cl() -> Any:
    return importlib.import_module("bytedigger_engine.check_ladder")


def _pf() -> Any:
    return importlib.import_module("bytedigger_engine.preflight")


def _p5() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_5_implement")


# --------------------------------------------------------------------------
# Real temp git repo + real engine receipts
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mk(tmp_path: Path, name: str, test_src: str) -> Path:
    repo = tmp_path / name / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "calc.py", CALC)
    _write(repo / TEST_REL, test_src)
    _write(repo / ".gitignore", "__pycache__/\n.pytest_cache/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo.resolve()


def _staged(tmp_path: Path, state: str) -> Path:
    """Repo whose phase-red receipt state is exactly `state` (red = stub step)."""
    repo = _mk(tmp_path, f"s7_{state}", STUB_SRC if state in ("red", "stale-red") else RED_SRC)
    if state == "missing":
        return repo
    res = _pf().run_engine_preflight(str(repo), [TEST_REL], "# engine spec\n")
    assert res["exit_code"] == (1 if state in ("red", "stale-red") else 0)
    if state in ("stale", "stale-red"):
        (repo / "calc.py").write_text(CALC + "# edit\n", encoding="utf-8")
    want = "stale" if state.startswith("stale") else state
    assert _pf().receipt_rung("red", str(repo))["status"] == want
    if state in ("red", "stale-red"):
        assert _pf().receipt_rung("red", str(repo))["red_step"] == "stub"
    return repo


def _fake(tmp_path: Path, name: str, *, out: str | None = None, rc: int = 0, sleep: float = 0.0) -> list[str]:
    lines = ["import sys, time", "sys.stdin.read()"]
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


# --------------------------------------------------------------------------
# Phase-5 harness (LLM call + event sink + engine producer replaced; prescreen is real)
# --------------------------------------------------------------------------

def _ctx(**org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"complexity": "SIMPLE", **org_extra},
        question="bd218 s7", session_id="s", persona="hal",
        framework=None, domain=None,
    )


def _prev(tmp_path: Path, prompt: str = "PROMPT-218") -> Any:
    from bytedigger_engine.contracts import StepResult
    data = {
        "prompt": prompt, "doc_path": str(tmp_path / "d.md"),
        "spec_path": str(tmp_path / "s.md"), "red_log_path": str(tmp_path / "r.log"),
        "cycle": 1, "stable_prefix": "",
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="check_red_executable")


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **org: Any):
    """Returns (result, llm_calls, events, order)."""
    from bytedigger_engine.contracts import StepResult
    p5 = _p5()
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []
    order: list[tuple] = []

    def fake_llm(**kw: Any) -> Any:
        calls.append(kw)
        order.append(("llm",))
        return StepResult(status="ok", data={"raw_response": "VERDICT: PASS\n", **kw["extra_data"]},
                          duration_ms=0, step_name="invoke_validation_llm")

    def fake_emit(event_type: str, payload: dict, severity: str = "info") -> None:
        events.append((event_type, payload))
        order.append(("emit", event_type))

    monkeypatch.setattr(p5, "invoke_llm_subprocess", fake_llm)
    monkeypatch.setattr(p5, "_emit_safe", fake_emit)
    monkeypatch.setattr(_pf(), "run_engine_preflight", lambda *a, **k: None, raising=False)
    result = p5._invoke_validation_llm(_ctx(**org), _prev(tmp_path))
    return result, calls, events, order


def _verdicts(events: list) -> list[dict]:
    return [p for (t, p) in events if t == "prescreen_verdict"]


def _expected_kwargs(tmp_path: Path) -> dict:
    p5 = _p5()
    prev = _prev(tmp_path)
    return {
        "prompt": "PROMPT-218",
        "model": p5._default_validation_model(),
        "timeout_sec": p5._resolve_validation_timeout_sec({"complexity": "SIMPLE"}),
        "step_name": "invoke_validation_llm",
        "extra_data": {
            "doc_path": str(tmp_path / "d.md"), "spec_path": str(tmp_path / "s.md"),
            "red_log_path": str(tmp_path / "r.log"), "red_test_paths": [], "cycle": 1,
            "red_commit_sha": None,
        },
        "hard_gate": True,
        "gate_label": "validation",
        "allowed_tools": ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"],
        "stable_prefix": "",
        "injections": p5._declared_injections(prev.data),
    }


def _assert_config_error(evs: list[dict]) -> None:
    assert len(evs) == 1, f"exactly one prescreen_verdict event, got {len(evs)}"
    ev = evs[0]
    assert set(ev) == EVENT_KEYS
    assert ev["classifier_status"] == "config-error"
    assert ev["outcome"] == "escalate" and ev["mode"] == "shadow"
    for k in ("rung", "classifier_label", "classifier_confidence", "classifier_ms", "cost_usd"):
        assert ev[k] is None
    assert ev["cycle"] == 1 and ev["phase"] == 5


def _spy_prescreen(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Records calls and delegates to the REAL check_ladder.prescreen."""
    cl = _cl()
    real = cl.prescreen
    seen: list[dict] = []

    def spy(*a: Any, **k: Any) -> Any:
        seen.append(k)
        return real(*a, **k)

    monkeypatch.setattr(cl, "prescreen", spy)
    return seen


# --------------------------------------------------------------------------
# AC1 script_findings
# --------------------------------------------------------------------------

def test_AC1_script_findings_red_maps_to_one_major_finding() -> None:
    cl = _cl()
    got = cl.script_findings({"status": "red", "phase": "red", "red_step": "stub"})
    assert got == [{"rule": "preflight:stub", "severity": "MAJOR",
                    "detail": "preflight step stub is red on the tree the gate judges"}]


@pytest.mark.parametrize("step", [None, "", "   ", 5, ["stub"]], ids=["none", "empty", "blank", "int", "list"])
def test_AC1_script_findings_red_without_usable_step_is_unknown(step: Any) -> None:
    got = _cl().script_findings({"status": "red", "phase": "red", "red_step": step})
    assert [f["rule"] for f in got] == ["preflight:unknown"] and got[0]["severity"] == "MAJOR"
    assert "unknown" in got[0]["detail"]
    got2 = _cl().script_findings({"status": "red"})  # red_step key absent
    assert [f["rule"] for f in got2] == ["preflight:unknown"]


@pytest.mark.parametrize(
    "rung",
    [{"status": s, "phase": "red", "red_step": None} for s in ("fresh", "stale", "missing", "error")]
    + [{"status": "stale", "phase": "red", "red_step": "stub"}, {}, None, "x", 7, ["red"]],
    ids=["fresh", "stale", "missing", "error", "stale-was-red", "empty", "none", "str", "int", "list"],
)
def test_AC1_script_findings_everything_else_is_empty(rung: Any) -> None:
    assert _cl().script_findings(rung) == []


def test_AC1_findings_accepted_by_prescreen_and_never_raises() -> None:
    cl = _cl()
    findings = cl.script_findings({"status": "red", "phase": "red", "red_step": "stub"})
    verdict = cl.prescreen(findings=findings, gate_input="g", classifier_cmd=None, mode="shadow",
                           timeout_s=cl.DEFAULT_TIMEOUT_S)  # no ValueError
    assert verdict["outcome"] == "reject" and verdict["rung"] == "script"
    for bad in (object(), {"status": object()}, {"status": "red", "red_step": object()}, b"red", 1.5, True):
        out = cl.script_findings(bad)
        assert isinstance(out, list)


# --------------------------------------------------------------------------
# AC2 reachability (reference case), AC3 fresh
# --------------------------------------------------------------------------

def test_AC2_default_config_real_red_receipt_gives_script_reject_event(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    evs = _verdicts(events)
    assert len(evs) == 1, f"exactly one prescreen_verdict event, got {len(evs)}"
    ev = evs[0]
    assert set(ev) == EVENT_KEYS
    assert ev["outcome"] == "reject" and ev["rung"] == "script" and ev["mode"] == "shadow"
    assert ev["classifier_status"] == "off"
    assert ev["cycle"] == 1 and ev["phase"] == 5
    assert len(calls) == 1, "gate runs exactly once"
    assert "prescreen" not in calls[0]["extra_data"]
    assert calls[0]["extra_data"]["preflight"] == {"status": "red", "red_step": "stub"}
    assert result.status == "ok"


def test_AC3_fresh_receipt_escalates_without_rung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "fresh")
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    evs = _verdicts(events)
    assert len(evs) == 1
    assert evs[0]["outcome"] == "escalate" and evs[0]["rung"] is None and evs[0]["mode"] == "shadow"
    assert evs[0]["classifier_status"] == "off"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC4 no evidence -> nothing called, nothing emitted
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case", ["stale", "stale-red", "missing", "error", "ambient", "rung-off", "config-error"])
def test_AC4_no_classifier_and_no_evidence_means_no_prescreen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    # Stage every repo BEFORE the first _drive: _drive stubs run_engine_preflight to None.
    red_repo = _staged(tmp_path, "red")
    case_repo = _staged(tmp_path, case) if case in ("stale", "stale-red", "missing") else None
    # Forcing: with real evidence the same harness DOES call prescreen (so the spy is live).
    seen = _spy_prescreen(monkeypatch)
    _drive(monkeypatch, tmp_path, git_cwd=str(red_repo))
    assert len(seen) == 1, "forcing: a red receipt must reach prescreen"
    seen.clear()

    org: dict[str, Any] = {}
    if case_repo is not None:
        org["git_cwd"] = str(case_repo)
    elif case == "error":
        org["git_cwd"] = str(red_repo)

        def boom(*_a: Any, **_k: Any) -> dict:
            raise RuntimeError("rung exploded")

        monkeypatch.setattr(_pf(), "receipt_rung", boom)
    elif case == "rung-off":
        org["git_cwd"] = str(red_repo)
        org["preflight_rung"] = {"mode": "off"}
    elif case == "config-error":
        org["git_cwd"] = str(red_repo)
        org["preflight_rung"] = {"mode": "bogus"}
    # "ambient": no git_cwd at all
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, **org)
    assert not seen, "check_ladder.prescreen must not be called without script evidence or a classifier"
    assert not _verdicts(events)
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC5 classifier configured + enforce config still shadow
# --------------------------------------------------------------------------

def test_AC5_classifier_reject_with_red_receipt_and_enforce_config(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    cmd = _fake(tmp_path, "clf_reject", out=_ans("reject", 1.0, reasons=["x"]))
    seen = _spy_prescreen(monkeypatch)
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                   prescreen={"classifier_cmd": cmd, "mode": "enforce", "timeout_s": 20})
    (ev,) = _verdicts(events)
    assert ev["outcome"] == "reject" and ev["rung"] == "script" and ev["mode"] == "shadow"
    assert ev["classifier_status"] == "ok"
    assert seen and all(k.get("mode") == "shadow" for k in seen)
    assert len(calls) == 1, "gate runs once even with mode enforce in config"
    pre = calls[0]["extra_data"]["prescreen"]
    assert pre["findings"], "findings must carry the script rung's finding"
    assert pre["findings"][0]["rule"] == "preflight:stub"


# --------------------------------------------------------------------------
# AC6 kill switch
# --------------------------------------------------------------------------

def test_AC6_script_rung_off_no_event_without_classifier(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    # Forcing: default config on the same repo emits (so "off" is what suppresses it).
    _r0, _c0, events0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(_verdicts(events0)) == 1
    seen = _spy_prescreen(monkeypatch)
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"script_rung": "off"})
    assert not _verdicts(events) and not seen
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


def test_AC6_script_rung_off_with_classifier_has_empty_findings(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    cmd = _fake(tmp_path, "clf_pass", out=_ans("pass", 0.5))
    # Forcing: the same config minus the switch carries the script finding.
    _r0, c0, _e0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"classifier_cmd": cmd})
    assert c0[0]["extra_data"]["prescreen"]["findings"]
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                   prescreen={"classifier_cmd": cmd, "script_rung": "off"})
    (ev,) = _verdicts(events)
    assert ev["classifier_status"] == "ok" and ev["rung"] is None and ev["outcome"] == "escalate"
    assert calls[0]["extra_data"]["prescreen"]["findings"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["bogus", 7, True, ["off"]], ids=["str", "int", "bool", "list"])
def test_AC6_unknown_script_rung_value_is_config_error_event(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: Any) -> None:
    repo = _staged(tmp_path, "red")
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"script_rung": bad})
    _assert_config_error(_verdicts(events))
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


@pytest.mark.parametrize("val", ["shadow", None], ids=["shadow", "none"])
def test_AC6_shadow_and_none_script_rung_behave_as_default(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, val: Any) -> None:
    repo = _staged(tmp_path, "red")
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"script_rung": val})
    (ev,) = _verdicts(events)
    assert ev["outcome"] == "reject" and ev["rung"] == "script" and ev["classifier_status"] == "off"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


@pytest.mark.parametrize("bad", ["bogus", 7], ids=["str", "int"])
def test_AC6_unknown_value_with_classifier_is_one_config_error_and_no_spawn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: Any) -> None:
    repo = _staged(tmp_path, "red")
    marker = tmp_path / "spawned.marker"
    script = tmp_path / "clf_marker.py"
    script.write_text(
        "import sys\nsys.stdin.read()\n"
        f"open({str(marker)!r}, 'w').write('spawned')\n"
        f"print({_ans('reject', 1.0)!r})\n", encoding="utf-8")
    cmd = [sys.executable, str(script)]
    # Forcing: the classifier script really spawns when the config is valid.
    _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"classifier_cmd": cmd})
    assert marker.exists(), "forcing: the marker script must spawn under a valid config"
    marker.unlink()
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                   prescreen={"classifier_cmd": cmd, "script_rung": bad})
    _assert_config_error(_verdicts(events))
    assert not marker.exists(), "classifier must not be spawned for an invalid script_rung"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


def test_AC6_invalid_timeout_without_classifier_is_config_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"timeout_s": 0})
    _assert_config_error(_verdicts(events))
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]
    assert result.status == "ok"


@pytest.mark.parametrize("ps", ["x", [], None], ids=["str", "list", "none"])
def test_AC6_non_dict_prescreen_behaves_as_default(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ps: Any) -> None:
    repo = _staged(tmp_path, "red")
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen=ps)
    (ev,) = _verdicts(events)
    assert ev["outcome"] == "reject" and ev["rung"] == "script" and ev["classifier_status"] == "off"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC7 classifier optional / provider degrade
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["missing-binary", "exit-1", "timeout"])
def test_AC7_unusable_classifier_still_yields_script_verdict(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    repo = _staged(tmp_path, "red")
    timeout_s = 20
    if kind == "missing-binary":
        cmd = ["/nonexistent/binary"]
    elif kind == "exit-1":
        cmd = _fake(tmp_path, "clf_rc1", out=_ans("pass", 0.5), rc=1)
    else:
        cmd = _fake(tmp_path, "clf_slow", out=_ans("pass", 0.5), sleep=60)
        timeout_s = 1
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                       prescreen={"classifier_cmd": cmd, "timeout_s": timeout_s})
    (ev,) = _verdicts(events)
    assert ev["classifier_status"] in ("error", "timeout")
    if kind == "timeout":
        assert ev["classifier_status"] == "timeout"
    assert ev["outcome"] == "reject" and ev["rung"] == "script"
    assert len(calls) == 1 and result.status == "ok"


# --------------------------------------------------------------------------
# AC8 both billing modes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("env", ["api-key", "subscription", "neither"])
def test_AC8_identical_events_and_gate_kwargs_in_every_credential_environment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    if env == "api-key":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    elif env == "subscription":
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-not-real")
    repo = _staged(tmp_path, "red")
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert _verdicts(events) == [{
        "outcome": "reject", "rung": "script", "mode": "shadow",
        "classifier_status": "off", "classifier_label": None, "classifier_confidence": None,
        "classifier_ms": 0, "cost_usd": None, "cycle": 1, "phase": 5,
    }]
    want = _expected_kwargs(tmp_path)
    want["extra_data"]["preflight"] = {"status": "red", "red_step": "stub"}
    assert len(calls) == 1 and calls[0] == want


# --------------------------------------------------------------------------
# AC9 order
# --------------------------------------------------------------------------

def test_AC9_preflight_receipt_then_prescreen_verdict_then_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    _r, calls, events, order = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(_verdicts(events)) == 1 and len(calls) == 1
    assert order == [("emit", "preflight_receipt"), ("emit", "prescreen_verdict"), ("llm",)]


# --------------------------------------------------------------------------
# AC10 prescreen raising
# --------------------------------------------------------------------------

def test_AC10_prescreen_raising_is_config_error_and_gate_runs_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _staged(tmp_path, "red")
    called: list[int] = []

    def boom(*_a: Any, **_k: Any) -> Any:
        called.append(1)
        raise RuntimeError("prescreen exploded")

    monkeypatch.setattr(_cl(), "prescreen", boom)
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert called, "forcing: prescreen must be reached with no classifier and a red receipt"
    _assert_config_error(_verdicts(events))
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]
    assert result.status == "ok" and result.step_name == "invoke_validation_llm"
    assert result.data == {"raw_response": "VERDICT: PASS\n", **calls[0]["extra_data"]}


# --------------------------------------------------------------------------
# AC11 add-only
# --------------------------------------------------------------------------

@pytest.mark.parametrize("with_classifier", [False, True], ids=["no-classifier", "classifier"])
def test_AC11_gate_kwargs_minus_added_keys_equal_script_off_and_rung_off(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_classifier: bool) -> None:
    repo = _staged(tmp_path, "red")
    ps: dict[str, Any] = {}
    if with_classifier:
        ps["classifier_cmd"] = _fake(tmp_path, "clf11", out=_ans("reject", 1.0))
    _r, on_calls, on_events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen=dict(ps))
    _r2, off_calls, off_events, _o2 = _drive(
        monkeypatch, tmp_path, git_cwd=str(repo),
        prescreen={**ps, "script_rung": "off"}, preflight_rung={"mode": "off"})
    assert len(on_calls) == 1 and len(off_calls) == 1
    assert len(_verdicts(on_events)) == 1, "forcing: the script rung must emit in default mode"
    assert _verdicts(on_events)[0]["rung"] == "script", "forcing: red receipt must reach the script rung"
    if with_classifier:
        assert on_calls[0]["extra_data"]["prescreen"]["findings"], "forcing: script finding must be fed"
    on = dict(on_calls[0])
    on_extra = dict(on["extra_data"])
    assert on_extra.pop("preflight") == {"status": "red", "red_step": "stub"}
    on_extra.pop("prescreen", None)
    on["extra_data"] = on_extra
    off = dict(off_calls[0])
    off_extra = dict(off["extra_data"])
    off_extra.pop("prescreen", None)
    off["extra_data"] = off_extra
    assert on == off
    assert off == _expected_kwargs(tmp_path)
