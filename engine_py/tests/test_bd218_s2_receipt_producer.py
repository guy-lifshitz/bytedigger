"""bd#218 step 2 RED -- the engine writes the phase-red receipt before the validation gate.

Spec: docs/decisions/2026-10-03-bd218-s2-receipt-producer.md (AC1-AC12).

New symbol (imported lazily, so every test fails at attribute/assert time, not collection):
  preflight.run_engine_preflight(top, red_tests, spec_text, *, base=None) -> PreflightResult
Real tmp git repos and real receipts everywhere; only the LLM call and the event sink of the
phase-5 harness are replaced (never the unit under test). No singleton resource is contended
(workflows.md 1i): each state is pre-staged deterministically in its own tmp repo.
AC11's sibling-file run is verified by running those files, not by a test here.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(180)

CALC = "def add(a, b):\n    return a + b\n"
RED_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"
STUB_SRC = (
    "from unittest.mock import patch\n"
    "from calc import add\n\n\n"
    "def test_add():\n"
    "    with patch(\"calc.add\", return_value=4):\n"
    "        assert add(1, 2) == 4\n"
)
SYNTAX_SRC = "def test_add(:\n    pass\n"
TEST_REL = "tests/test_calc.py"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_KNOWN_REDS_TODAY", raising=False)
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


def _pf() -> Any:
    return importlib.import_module("bytedigger_engine.preflight")


def _p5() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_5_implement")


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


def _produce(repo: Path) -> Any:
    return _pf().run_engine_preflight(str(repo), [TEST_REL], "# engine spec\n")


def _receipt(repo: Path) -> dict:
    return json.loads(_pf().receipt_path(repo).read_text(encoding="utf-8"))


def _statuses(doc: dict) -> dict[str, str]:
    return {s["name"]: s["status"] for s in doc["steps"]}


# ---------------------------------------------------------------- AC1-AC5, AC12

def test_AC1_clean_red_writes_engine_receipt_fresh(tmp_path: Path) -> None:
    repo = _mk(tmp_path, "ac1")
    res = _produce(repo)
    assert res["exit_code"] == 0
    doc = _receipt(repo)
    assert doc["phase"] == "red" and doc["ok"] is True and doc["producer"] == "engine"
    assert [s["name"] for s in doc["steps"]] == ["syntax", "stub", "facts"]
    assert set(doc["not_run"]) == {"cite", "tier", "scoped", "siblings", "prescreen"}
    assert _pf().receipt_rung("red", str(repo)) == {"status": "fresh", "phase": "red", "red_step": None}


def test_AC2_mocking_red_file_is_red_at_stub_facts_skipped(tmp_path: Path) -> None:
    repo = _mk(tmp_path, "ac2", STUB_SRC)
    res = _produce(repo)
    assert res["exit_code"] == 1
    doc = _receipt(repo)
    assert doc["ok"] is False and doc["producer"] == "engine"
    assert _statuses(doc) == {"syntax": "ok", "stub": "red", "facts": "skipped"}
    assert _pf().receipt_rung("red", str(repo)) == {"status": "red", "phase": "red", "red_step": "stub"}


def test_AC3_syntax_error_is_red_at_syntax_rest_skipped(tmp_path: Path) -> None:
    repo = _mk(tmp_path, "ac3", SYNTAX_SRC)
    _produce(repo)
    assert _statuses(_receipt(repo)) == {"syntax": "red", "stub": "skipped", "facts": "skipped"}
    assert _pf().receipt_rung("red", str(repo))["red_step"] == "syntax"


def test_AC4_no_base_or_not_git_is_failure_without_receipt(tmp_path: Path) -> None:
    pf = _pf()
    plain = tmp_path / "plain"
    plain.mkdir()
    res = pf.run_engine_preflight(str(plain), [TEST_REL], "")
    assert res["receipt"] is None and res["exit_code"] != 0

    unborn = tmp_path / "unborn"          # git work tree, no commit: none of origin/main, main, HEAD resolves
    unborn.mkdir()
    _git(unborn, "init", "-q", "-b", "main")
    old = pf.receipt_path(unborn)
    _write(old, "{}")
    res2 = pf.run_engine_preflight(str(unborn), [TEST_REL], "")
    assert res2["receipt"] is None and res2["exit_code"] != 0
    assert not old.exists(), "the previous receipt must be removed"


def test_AC5_no_test_run_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac5")
    real = subprocess.run
    cmds: list[str] = []

    def rec(cmd: Any, *a: Any, **k: Any) -> Any:
        cmds.append(" ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", rec)
    res = _produce(repo)
    assert res["exit_code"] == 0 and cmds, "producer ran and spawned git reads"
    assert not [c for c in cmds if "pytest" in c or "bun test" in c]


def test_AC12_fresh_after_producer_stale_after_edit(tmp_path: Path) -> None:
    pf = _pf()
    repo = _mk(tmp_path, "ac12")
    _produce(repo)
    assert pf.verify_receipt("red", repo) == "fresh"
    (repo / "calc.py").write_text(CALC + "# edit\n", encoding="utf-8")
    assert pf.verify_receipt("red", repo) == "stale"


def test_AC11_cli_receipt_has_no_producer_key_engine_receipt_does(tmp_path: Path) -> None:
    pf = _pf()
    repo = _mk(tmp_path, "ac11")
    spec = tmp_path / "ac11" / "spec.md"
    _write(spec, f"---\nred_tests: [{TEST_REL}]\npaths: [calc.py]\n---\n`calc.py` defines `add`.\n")
    pf.run_preflight(str(spec), "red", cwd=str(repo))
    assert "producer" not in _receipt(repo) and "not_run" not in _receipt(repo)
    _produce(repo)
    assert _receipt(repo)["producer"] == "engine"


# ---------------------------------------------------------------- phase-5 harness

def _ctx(**org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"complexity": "SIMPLE", **org_extra},
        question="bd218 s2", session_id="s", persona="hal",
        framework=None, domain=None,
    )


def _prev(tmp_path: Path) -> Any:
    from bytedigger_engine.contracts import StepResult
    spec = tmp_path / "s.md"
    spec.write_text("# engine spec\n", encoding="utf-8")
    data = {
        "prompt": "PROMPT-218", "doc_path": str(tmp_path / "d.md"), "spec_path": str(spec),
        "red_log_path": str(tmp_path / "r.log"), "cycle": 1, "stable_prefix": "",
        "red_test_paths": [TEST_REL],
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="check_red_executable")


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **org: Any):
    """Real _invoke_validation_llm and real producer; only the LLM call and event sink are faked."""
    from bytedigger_engine.contracts import StepResult
    p5 = _p5()
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    def fake_llm(**kw: Any) -> Any:
        calls.append(kw)
        return StepResult(status="ok", data={"raw_response": "VERDICT: PASS\n", **kw["extra_data"]},
                          duration_ms=0, step_name="invoke_validation_llm")

    monkeypatch.setattr(p5, "invoke_llm_subprocess", fake_llm)
    monkeypatch.setattr(p5, "_emit_safe", lambda t, p, severity="info": events.append((t, p)))
    result = p5._invoke_validation_llm(_ctx(**org), _prev(tmp_path))
    return result, calls, events


def _rung(events: list) -> list[dict]:
    return [p for (t, p) in events if t == "preflight_receipt"]


def _spy(monkeypatch: pytest.MonkeyPatch, raises: bool = False) -> list[tuple]:
    seen: list[tuple] = []
    real = getattr(_pf(), "run_engine_preflight", None)

    def spy(*a: Any, **k: Any) -> Any:
        seen.append((a, k))
        if raises:
            raise RuntimeError("producer exploded")
        return real(*a, **k) if real else None

    monkeypatch.setattr(_pf(), "run_engine_preflight", spy, raising=False)
    return seen


def test_AC6_reachability_fresh_receipt_produced_before_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac6a")
    assert not _pf().receipt_path(repo).exists()
    result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    (ev,) = _rung(events)
    assert ev["status"] == "fresh"
    assert len(calls) == 1 and calls[0]["extra_data"]["preflight"]["status"] == "fresh"
    assert _receipt(repo)["producer"] == "engine"      # side effect on disk
    assert result.status == "ok"


def test_AC6_reachability_mocking_red_file_is_red_stub_gate_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac6b", STUB_SRC)
    _result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    (ev,) = _rung(events)
    assert ev["status"] == "red" and ev["red_step"] == "stub"
    assert len(calls) == 1
    assert calls[0]["extra_data"]["preflight"] == {"status": "red", "red_step": "stub"}


def test_AC7_produce_false_off_and_bad_produce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac7")
    seen = _spy(monkeypatch)
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1, "forcing: default mode calls the producer"
    assert _pf().receipt_path(repo).exists()
    _pf().receipt_path(repo).unlink()

    seen.clear()
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                               preflight_rung={"produce": False})
    assert not seen and _rung(events)[0]["status"] == "missing" and len(calls) == 1

    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"mode": "off"})
    assert not seen and not _rung(events) and "preflight" not in calls[0]["extra_data"]

    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"produce": "no"})
    assert not seen and _rung(events)[0]["status"] == "config-error" and len(calls) == 1


def test_AC8_producer_raising_is_swallowed_rung_and_gate_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac8")
    seen = _spy(monkeypatch, raises=True)
    result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1, "forcing: producer was reached"
    (ev,) = _rung(events)
    assert ev["status"] == "missing"
    assert len(calls) == 1 and result.status == "ok"


def test_AC9_gate_kwargs_equal_with_and_without_producer_and_across_receipts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def stripped(call: dict) -> dict:
        out = dict(call)
        out["extra_data"] = {k: v for k, v in call["extra_data"].items() if k != "preflight"}
        return out

    fresh_repo = _mk(tmp_path, "ac9a")
    _r, on, _e = _drive(monkeypatch, tmp_path, git_cwd=str(fresh_repo))
    assert on[0]["extra_data"]["preflight"]["status"] == "fresh", "forcing"
    off_repo = _mk(tmp_path, "ac9b")
    _r, off, _e = _drive(monkeypatch, tmp_path, git_cwd=str(off_repo), preflight_rung={"produce": False})
    assert off[0]["extra_data"]["preflight"]["status"] == "missing"
    red_repo = _mk(tmp_path, "ac9c", STUB_SRC)
    _r, red, _e = _drive(monkeypatch, tmp_path, git_cwd=str(red_repo))
    assert red[0]["extra_data"]["preflight"]["status"] == "red"
    assert stripped(on[0]) == stripped(off[0]) == stripped(red[0])


def test_AC10_ambient_cwd_does_not_call_producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac10")
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1, "forcing: non-ambient cwd reaches the producer"
    seen.clear()
    _r, calls, events = _drive(monkeypatch, tmp_path)
    assert not seen
    assert _rung(events)[0]["status"] == "ambient-skip" and len(calls) == 1
