"""bd#218 step 9 RED -- the fix-integrity gate gets a shadow pre-screen verdict fed by the script rung.

Spec: docs/decisions/2026-10-11-bd277-s9-fix-integrity-prescreen.md (ACs AC1-AC10 here; AC11, the sibling
suites passing unchanged, is a run of those files, not a test in this file).

AC map:
  AC1  test_AC1_*                       red/stub green receipt -> one script-reject event (gate fix_integrity,
                                        cycle 1; cycle 3 from org_config)
  AC2  test_AC2_*                       fresh green receipt -> one escalate event
  AC3  test_AC3_*                       no evidence (stale, stale-red, missing, raising rung, ambient, rung off,
                                        rung config error, verdict_override short-circuit) -> no event,
                                        prescreen not called
  AC4  test_AC4_*                       script_rung kill switch, default-equivalent values, config errors
  AC5  test_AC5_*                       a configured classifier is never spawned (marker file stays absent),
                                        mode stays shadow: red receipt, script_rung off, no evidence
                                        (stale / stale-red / missing / ambient), invalid script_rung
  AC6  test_AC6_*                       prescreen or script_findings raising -> one config-error event
  AC7  test_AC7_*                       identical events and model-call kwargs under three environments
  AC8  test_AC8_*                       order: preflight_receipt, prescreen_verdict, model call
  AC9  test_AC9_*                       model-call kwargs equal with and without the script rung
  AC10 test_AC10_*                      one event per step call although the model is re-rolled

Changed behaviour: phase_6_fix_integrity._invoke_fix_integrity_llm hands the green receipt record to
check_ladder.script_findings / check_ladder.prescreen (always mode="shadow", no classifier) and emits one
prescreen_verdict event carrying gate="fix_integrity", phase=6, cycle from org_config. Everything is imported
lazily so each test fails at assert time, not at collection.

Receipts are real (real tmp git repo, real producer run by the fix-integrity step itself). Only the model call
and the event sink of the step are replaced; check_ladder.prescreen and script_findings are the real functions
everywhere except where a test patches them to raise (AC6). Stale and missing receipts are staged with
preflight_rung {"produce": False}, so the step reads the pre-staged state instead of re-producing it.
No singleton resource is contended (workflows.md 1i): every state is pre-staged in its own tmp repo and the
classifier script of AC5 only writes a marker file.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(180)

CALC = "def add(a, b):\n    return a + b\n"
CALC_GREEN = "def add(a, b):\n    return a + b + 0\n"
RED_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
STUB_SRC = (
    "from unittest.mock import patch\n"
    "from calc import add\n\n\n"
    "def test_add():\n"
    "    with patch(\"calc.add\", return_value=3):\n"
    "        assert add(1, 2) == 3\n"
)
TEST_REL = "tests/test_calc.py"
EVENT_KEYS = {"outcome", "rung", "mode", "classifier_status", "classifier_label",
              "classifier_confidence", "classifier_ms", "cost_usd", "cycle", "phase", "gate"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_KNOWN_REDS_TODAY", raising=False)
    monkeypatch.delenv("HAL_INTEGRITY_VERDICT_RETRY_MAX", raising=False)
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


def _cl() -> Any:
    return importlib.import_module("bytedigger_engine.check_ladder")


def _pf() -> Any:
    return importlib.import_module("bytedigger_engine.preflight")


def _p6() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_6_fix_integrity")


# --------------------------------------------------------------------------
# Real temp git repos + real engine receipts
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


def _mk(tmp_path: Path, name: str, test_src: str = RED_SRC) -> Path:
    """origin/main = initial commit (calc + test); a fix commit then changes the production file."""
    repo = tmp_path / name / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "calc.py", CALC)
    _write(repo / TEST_REL, test_src)
    _write(repo / ".gitignore", "__pycache__/\n.pytest_cache/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo / "calc.py", CALC_GREEN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix")
    return repo.resolve()


def _red(tmp_path: Path, name: str = "red") -> Path:
    """Repo whose green receipt the fix-integrity step produces as red / stub."""
    return _mk(tmp_path, name, STUB_SRC)


def _fresh(tmp_path: Path, name: str = "fresh") -> Path:
    """Repo whose green receipt the fix-integrity step produces as fresh."""
    return _mk(tmp_path, name, RED_SRC)


def _staged(tmp_path: Path, state: str) -> Path:
    """Repo with a PRE-STAGED green receipt in `state`; use with preflight_rung {"produce": False}."""
    repo = _mk(tmp_path, f"s9_{state}", STUB_SRC if state == "stale-red" else RED_SRC)
    if state == "missing":
        assert _pf().receipt_rung("green", str(repo))["status"] == "missing"
        return repo
    res = _pf().run_engine_preflight(str(repo), [TEST_REL], "# engine spec\n", phase="green")
    assert res["exit_code"] == (1 if state == "stale-red" else 0)
    if state in ("stale", "stale-red"):
        (repo / "calc.py").write_text(CALC_GREEN + "# edit\n", encoding="utf-8")
        assert _pf().receipt_rung("green", str(repo))["status"] == "stale"
        if state == "stale-red":
            assert _pf().receipt_rung("green", str(repo))["red_step"] == "stub"
    return repo


# --------------------------------------------------------------------------
# Fix-integrity-step harness (model call + event sink replaced; real producer and real prescreen)
# --------------------------------------------------------------------------

def _ctx(scratch: Path, **org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), **org_extra},
        question="bd218 s9", session_id="s", persona="hal",
        framework=None, domain=None,
    )


def _diff_text(paths: list[str]) -> str:
    return "".join(
        f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-x\n+y\n" for p in paths
    )


def _prev(work: Path, paths: list[str], **extra: Any) -> Any:
    from bytedigger_engine.contracts import StepResult
    diff = work / "fix-test-diff.patch"
    diff.write_text(_diff_text(paths), encoding="utf-8")
    data = {
        "diff_path": str(diff), "doc_path": str(work / "review.md"),
        "prompt": "PROMPT-218-S9", "pre_fix_sha": "a" * 40, "fix_commit_sha": "b" * 40,
        "diff_command": ["git", "diff", "x", "y"], **extra,
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="build_fix_integrity_prompt")


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raws: list[str] | None = None,
           prev_extra: dict | None = None, **org: Any):
    """Real _invoke_fix_integrity_llm. Returns (result, llm_calls, events, order)."""
    from bytedigger_engine.contracts import StepResult
    p6 = _p6()
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"      # no .git above tmp_path: without git_cwd the cwd is ambient
    scratch.mkdir(exist_ok=True)
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []
    order: list[tuple] = []
    queue = list(raws or ["VERDICT: SPEC_CHANGE\n"])

    def fake_llm(**kw: Any) -> Any:
        calls.append(kw)
        order.append(("llm",))
        raw = queue.pop(0) if len(queue) > 1 else queue[0]
        return StepResult(status="ok", data={"raw_response": raw, **kw["extra_data"]},
                          duration_ms=0, step_name="invoke_fix_integrity_llm")

    def fake_emit(event_type: str, payload: dict, severity: str = "info") -> None:
        events.append((event_type, payload))
        order.append(("emit", event_type))

    monkeypatch.setattr(p6, "invoke_llm_subprocess", fake_llm)
    monkeypatch.setattr(p6, "_emit_safe", fake_emit, raising=False)
    prev = _prev(work, [TEST_REL], **(prev_extra or {}))
    result = p6._invoke_fix_integrity_llm(_ctx(scratch, **org), prev)
    return result, calls, events, order


def _verdicts(events: list) -> list[dict]:
    return [p for (t, p) in events if t == "prescreen_verdict"]


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


def _assert_reject(evs: list[dict], cycle: int = 1) -> None:
    assert len(evs) == 1, f"exactly one prescreen_verdict event, got {len(evs)}"
    ev = evs[0]
    assert set(ev) == EVENT_KEYS
    assert ev["outcome"] == "reject" and ev["rung"] == "script" and ev["mode"] == "shadow"
    assert ev["gate"] == "fix_integrity" and ev["classifier_status"] == "off"
    assert ev["cycle"] == cycle and ev["phase"] == 6


def _assert_config_error(evs: list[dict]) -> None:
    assert len(evs) == 1, f"exactly one prescreen_verdict event, got {len(evs)}"
    ev = evs[0]
    assert set(ev) == EVENT_KEYS
    assert ev["classifier_status"] == "config-error"
    assert ev["outcome"] == "escalate" and ev["mode"] == "shadow" and ev["gate"] == "fix_integrity"
    for k in ("rung", "classifier_label", "classifier_confidence", "classifier_ms", "cost_usd"):
        assert ev[k] is None
    assert ev["cycle"] == 1 and ev["phase"] == 6


# --------------------------------------------------------------------------
# AC1 reference case, AC2 fresh
# --------------------------------------------------------------------------

def test_AC1_red_stub_green_receipt_gives_script_reject_event_with_gate_key(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    _assert_reject(_verdicts(events))
    assert len(calls) == 1, "the model runs exactly once"
    assert "prescreen" not in calls[0]["extra_data"]
    assert calls[0]["extra_data"]["preflight"] == {"status": "red", "red_step": "stub"}
    assert result.status == "ok"

    repo3 = _red(tmp_path / "c3", "red3")
    _r3, calls3, events3, _o3 = _drive(monkeypatch, tmp_path / "c3", git_cwd=str(repo3), cycle=3)
    _assert_reject(_verdicts(events3), cycle=3)
    assert len(calls3) == 1


def test_AC2_fresh_green_receipt_escalates_without_rung(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _fresh(tmp_path)
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    evs = _verdicts(events)
    assert len(evs) == 1, f"exactly one prescreen_verdict event, got {len(evs)}"
    assert set(evs[0]) == EVENT_KEYS
    assert evs[0]["outcome"] == "escalate" and evs[0]["rung"] is None and evs[0]["mode"] == "shadow"
    assert evs[0]["gate"] == "fix_integrity" and evs[0]["classifier_status"] == "off"
    assert evs[0]["cycle"] == 1 and evs[0]["phase"] == 6
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC3 no evidence -> nothing called, nothing emitted
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case", ["stale", "stale-red", "missing", "error", "ambient", "rung-off", "config-error", "override"])
def test_AC3_no_receipt_evidence_means_no_prescreen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    red_repo = _red(tmp_path)
    case_repo = _staged(tmp_path, case) if case in ("stale", "stale-red", "missing") else None
    # Forcing: with real evidence the same harness DOES call prescreen (so the spy is live).
    seen = _spy_prescreen(monkeypatch)
    _r0, _c0, events0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(red_repo))
    assert len(seen) == 1, "forcing: a red receipt must reach prescreen"
    assert len(_verdicts(events0)) == 1, "forcing: a red receipt must emit prescreen_verdict"
    seen.clear()

    org: dict[str, Any] = {}
    prev_extra: dict[str, Any] = {}
    if case_repo is not None:
        org["git_cwd"] = str(case_repo)
        org["preflight_rung"] = {"produce": False}     # read the pre-staged state, do not re-produce
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
    elif case == "override":
        org["git_cwd"] = str(red_repo)
        prev_extra["verdict_override"] = "NO_CHANGES"
    # "ambient": no git_cwd at all
    result, calls, events, _o = _drive(monkeypatch, tmp_path, prev_extra=prev_extra, **org)
    assert not seen, "check_ladder.prescreen must not be called without a fresh or red receipt"
    assert not _verdicts(events)
    if case == "override":
        assert not calls, "the short-circuit never calls the model"
        assert result.status == "ok" and result.data["skipped"] is True
    else:
        assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC4 kill switch
# --------------------------------------------------------------------------

def test_AC4_script_rung_off_no_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    # Forcing: default config on the same repo emits (so "off" is what suppresses it).
    _r0, _c0, events0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    _assert_reject(_verdicts(events0))
    seen = _spy_prescreen(monkeypatch)
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"script_rung": "off"})
    assert not _verdicts(events) and not seen
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


@pytest.mark.parametrize("ps", [{"script_rung": "shadow"}, {"script_rung": None}, "x", [], None],
                         ids=["shadow", "none", "str", "list", "none-prescreen"])
def test_AC4_default_equivalent_values_behave_as_default(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ps: Any) -> None:
    repo = _red(tmp_path)
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen=ps)
    _assert_reject(_verdicts(events))
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


@pytest.mark.parametrize("bad", ["bogus", 7], ids=["str", "int"])
@pytest.mark.parametrize("rung_off", [False, True], ids=["rung-on", "rung-off"])
def test_AC4_unknown_script_rung_value_is_one_config_error_event(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: Any, rung_off: bool) -> None:
    repo = _red(tmp_path)
    # Forcing: a valid config on the same repo emits a reject event, so the config-error one is new.
    _r0, _c0, events0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(_verdicts(events0)) == 1, "forcing: the gate emits a verdict in default config"
    seen = _spy_prescreen(monkeypatch)
    org: dict[str, Any] = {"git_cwd": str(repo), "prescreen": {"script_rung": bad}}
    if rung_off:
        org["preflight_rung"] = {"mode": "off"}
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, **org)
    _assert_config_error(_verdicts(events))
    assert not seen, "prescreen is not called for a config error"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC5 classifier never used
# --------------------------------------------------------------------------

def _marker_classifier(tmp_path: Path) -> tuple[list[str], Path]:
    """A classifier command that writes a marker file if (and only if) it is ever spawned."""
    marker = tmp_path / "spawned.marker"
    script = tmp_path / "clf_marker.py"
    script.write_text(
        "import sys\nsys.stdin.read()\n"
        f"open({str(marker)!r}, 'w').write('spawned')\n"
        "print('{\"label\": \"pass\", \"confidence\": 0.5}')\n", encoding="utf-8")
    return [sys.executable, str(script)], marker


def test_AC5_classifier_cmd_is_never_spawned_and_mode_stays_shadow(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    cmd, marker = _marker_classifier(tmp_path)
    seen = _spy_prescreen(monkeypatch)
    _r, calls, events, _o = _drive(
        monkeypatch, tmp_path, git_cwd=str(repo),
        prescreen={"classifier_cmd": cmd, "mode": "enforce", "timeout_s": 20})
    _assert_reject(_verdicts(events))
    assert not marker.exists(), "the classifier must never be spawned at the fix-integrity gate"
    assert len(seen) == 1, "forcing: prescreen was reached"
    assert seen[0].get("classifier_cmd") is None and seen[0].get("mode") == "shadow"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


def test_AC5_classifier_cmd_with_script_rung_off_is_never_spawned_and_emits_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    cmd, marker = _marker_classifier(tmp_path)
    # Forcing: the same repo and classifier_cmd WITHOUT script_rung off reaches prescreen and emits.
    seen = _spy_prescreen(monkeypatch)
    _r0, _c0, events0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                    prescreen={"classifier_cmd": cmd})
    _assert_reject(_verdicts(events0))
    assert len(seen) == 1, "forcing: without script_rung off the gate reaches prescreen"
    assert not marker.exists()
    seen.clear()
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                   prescreen={"classifier_cmd": cmd, "script_rung": "off"})
    assert not marker.exists(), "the classifier must never be spawned when the script rung is off"
    assert not _verdicts(events)
    assert not seen, "prescreen must not be called when the script rung is off"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


@pytest.mark.parametrize("case", ["stale", "stale-red", "missing", "ambient"])
def test_AC5_classifier_cmd_without_receipt_evidence_is_never_spawned(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    cmd, marker = _marker_classifier(tmp_path)
    red_repo = _red(tmp_path)
    case_repo = _staged(tmp_path, case) if case != "ambient" else None
    # Forcing: on a red repo with the same classifier_cmd the gate DOES reach prescreen and emit.
    seen = _spy_prescreen(monkeypatch)
    _r0, _c0, events0, _o0 = _drive(monkeypatch, tmp_path, git_cwd=str(red_repo),
                                    prescreen={"classifier_cmd": cmd})
    _assert_reject(_verdicts(events0))
    assert len(seen) == 1, "forcing: a red receipt must reach prescreen"
    assert not marker.exists(), "the classifier must not be spawned even on a red receipt"
    seen.clear()
    org: dict[str, Any] = {"prescreen": {"classifier_cmd": cmd}}
    if case_repo is not None:
        org["git_cwd"] = str(case_repo)
        org["preflight_rung"] = {"produce": False}     # read the pre-staged state, do not re-produce
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, **org)
    assert not marker.exists(), "the classifier must never be spawned without receipt evidence"
    assert not _verdicts(events)
    assert not seen, "prescreen must not be called without a fresh or red receipt"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


def test_AC5_classifier_cmd_with_invalid_script_rung_is_config_error_and_never_spawned(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    cmd, marker = _marker_classifier(tmp_path)
    seen = _spy_prescreen(monkeypatch)
    _r, calls, events, _o = _drive(
        monkeypatch, tmp_path, git_cwd=str(repo),
        prescreen={"classifier_cmd": cmd, "script_rung": "bogus"})
    _assert_config_error(_verdicts(events))
    assert not marker.exists(), "the classifier must never be spawned on a config error"
    assert not seen, "prescreen is not called for a config error"
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]


# --------------------------------------------------------------------------
# AC6 degrade
# --------------------------------------------------------------------------

@pytest.mark.parametrize("target", ["prescreen", "script_findings"])
def test_AC6_raising_ladder_is_config_error_and_gate_result_passes_through(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    repo = _red(tmp_path)
    called: list[int] = []

    def boom(*_a: Any, **_k: Any) -> Any:
        called.append(1)
        raise RuntimeError(f"{target} exploded")

    monkeypatch.setattr(_cl(), target, boom)
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert called, f"forcing: {target} must be reached with a red receipt"
    _assert_config_error(_verdicts(events))
    assert len(calls) == 1 and "prescreen" not in calls[0]["extra_data"]
    assert result.status == "ok" and result.step_name == "invoke_fix_integrity_llm"
    assert result.data["raw_response"] == "VERDICT: SPEC_CHANGE\n"
    assert result.data["preflight"] == {"status": "red", "red_step": "stub"}


# --------------------------------------------------------------------------
# AC7 both billing modes
# --------------------------------------------------------------------------

def test_AC7_identical_events_and_kwargs_in_every_credential_environment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: dict[str, tuple[list[dict], list[dict]]] = {}
    for env in ("api-key", "subscription", "neither"):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        if env == "api-key":
            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
        elif env == "subscription":
            monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-not-real")
        repo = _red(tmp_path, f"red_{env}")
        _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
        _assert_reject(_verdicts(events))
        assert len(calls) == 1
        runs[env] = (_verdicts(events), calls)
    base_events, base_calls = runs["api-key"]
    for env in ("subscription", "neither"):
        assert runs[env][0] == base_events, env
        assert runs[env][1] == base_calls, env


# --------------------------------------------------------------------------
# AC8 order
# --------------------------------------------------------------------------

def test_AC8_preflight_receipt_then_prescreen_verdict_then_model_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    _r, calls, events, order = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(_verdicts(events)) == 1 and len(calls) == 1
    assert order == [("emit", "preflight_receipt"), ("emit", "prescreen_verdict"), ("llm",)]


# --------------------------------------------------------------------------
# AC9 add-only
# --------------------------------------------------------------------------

def test_AC9_model_call_kwargs_equal_with_and_without_the_script_rung(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    _r1, on_calls, on_events, _o1 = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    _r2, off_calls, off_events, _o2 = _drive(
        monkeypatch, tmp_path, git_cwd=str(repo), prescreen={"script_rung": "off"})
    _assert_reject(_verdicts(on_events))
    assert not _verdicts(off_events)
    assert len(on_calls) == 1 and len(off_calls) == 1
    assert on_calls[0] == off_calls[0]
    for key in ("prompt", "model", "timeout_sec", "allowed_tools", "hard_gate", "gate_label",
                "injections", "extra_data"):
        assert key in on_calls[0], key
    assert on_calls[0]["extra_data"]["preflight"] == {"status": "red", "red_step": "stub"}


# --------------------------------------------------------------------------
# AC10 once per step call
# --------------------------------------------------------------------------

def test_AC10_reroll_calls_model_twice_but_emits_one_verdict(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _red(tmp_path)
    seen = _spy_prescreen(monkeypatch)
    result, calls, events, _o = _drive(
        monkeypatch, tmp_path, raws=["analysis only\n", "VERDICT: SPEC_CHANGE\n"], git_cwd=str(repo))
    assert len(calls) == 2, "the existing completeness re-roll runs the model a second time"
    _assert_reject(_verdicts(events))
    assert len(seen) == 1
    assert result.status == "ok"
