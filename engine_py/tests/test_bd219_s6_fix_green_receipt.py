"""bd#219 step 6 RED -- the engine writes the phase-green receipt before the fix-integrity gate.

Spec: docs/decisions/2026-10-03-bd219-s6-fix-integrity-receipt.md (r1; AC1-AC12 here;
AC13 is a sibling-suite run, not a test in this file).

Changed symbol (imported lazily, so every test fails at attribute/assert time, not collection):
  phase_6_fix_integrity._invoke_fix_integrity_llm -- new fail-open rung block before the LLM call
Real tmp git repos and real receipts everywhere; only the LLM call and the event sink of the
fix-integrity step (plus, where the spec says so, preflight.run_engine_preflight as a spy/raiser)
are replaced -- never the unit under test. No singleton resource is contended (workflows.md 1i):
each state is pre-staged deterministically in its own tmp repo.
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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_KNOWN_REDS_TODAY", raising=False)
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


def _pf() -> Any:
    return importlib.import_module("bytedigger_engine.preflight")


def _p5i() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_5_integrity")


def _p6() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_6_fix_integrity")


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


def _mk(tmp_path: Path, name: str, test_src: str = RED_SRC, prod_src: str = CALC_GREEN) -> Path:
    repo = tmp_path / name / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "calc.py", CALC)
    _write(repo / TEST_REL, test_src)
    _write(repo / ".gitignore", "__pycache__/\n.pytest_cache/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo / "calc.py", prod_src)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix")
    return repo.resolve()


def _green(repo: Path) -> Any:
    return _pf().run_engine_preflight(str(repo), [TEST_REL], "# engine spec\n", phase="green")


def _ctx(scratch: Path, **org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), **org_extra},
        question="bd219 s6", session_id="s", persona="hal",
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
        "prompt": "PROMPT-219-S6", "pre_fix_sha": "a" * 40, "fix_commit_sha": "b" * 40,
        "diff_command": ["git", "diff", "x", "y"], **extra,
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="build_fix_integrity_prompt")


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, paths: list[str] | None = None,
           raws: list[str] | None = None, prev_extra: dict | None = None, **org: Any):
    """Real _invoke_fix_integrity_llm and real producer; only the LLM call and event sink are faked."""
    from bytedigger_engine.contracts import StepResult
    p6 = _p6()
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"      # no .git above tmp_path: without git_cwd the cwd is ambient
    scratch.mkdir(exist_ok=True)
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []
    queue = list(raws or ["VERDICT: SPEC_CHANGE\n"])

    def fake_llm(**kw: Any) -> Any:
        calls.append(kw)
        raw = queue.pop(0) if len(queue) > 1 else queue[0]
        return StepResult(status="ok", data={"raw_response": raw, **kw["extra_data"]},
                          duration_ms=0, step_name="invoke_fix_integrity_llm")

    monkeypatch.setattr(p6, "invoke_llm_subprocess", fake_llm)
    monkeypatch.setattr(p6, "_emit_safe", lambda t, p, severity="info": events.append((t, p)), raising=False)
    prev = _prev(work, [TEST_REL] if paths is None else paths, **(prev_extra or {}))
    result = p6._invoke_fix_integrity_llm(_ctx(scratch, **org), prev)
    return result, calls, events


def _rung(events: list) -> list[dict]:
    return [p for (t, p) in events if t == "preflight_receipt"]


def _spy(monkeypatch: pytest.MonkeyPatch, raises: bool = False) -> list[tuple]:
    seen: list[tuple] = []
    real = _pf().run_engine_preflight

    def spy(*a: Any, **k: Any) -> Any:
        seen.append((a, k))
        if raises:
            raise RuntimeError("producer exploded")
        return real(*a, **k)

    monkeypatch.setattr(_pf(), "run_engine_preflight", spy)
    return seen


def _hand_diff(tmp_path: Path, headers: list[str]) -> str:
    body = "".join(f"{h}\n--- x\n+++ y\n@@ -1 +1 @@\n-x\n+y\n" for h in headers)
    out = tmp_path / "hand.patch"
    out.write_text(body, encoding="utf-8")
    return str(out)


def _clean_hdr(p: str = TEST_REL) -> str:
    return f"diff --git a/{p} b/{p}"


def _assert_missing(events: list, calls: list) -> None:
    (ev,) = _rung(events)
    assert ev["status"] == "missing", ev
    assert len(calls) == 1 and calls[0]["extra_data"]["preflight"]["status"] == "missing"


# ---------------------------------------------------------------- AC1

def test_AC1_reachability_fresh_green_receipt_before_fix_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac1a")
    assert not _pf().receipt_path(repo).exists()
    result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    (ev,) = _rung(events)
    assert ev["gate"] == "fix_integrity" and ev["phase"] == 6 and ev["status"] == "fresh"
    assert ev["cycle"] == 1
    assert len(calls) == 1 and calls[0]["extra_data"]["preflight"]["status"] == "fresh"
    doc = json.loads(_pf().receipt_path(repo).read_text(encoding="utf-8"))
    assert doc["producer"] == "engine" and doc["phase"] == "green"
    assert result.status == "ok"


def test_AC1_mocking_test_file_is_red_stub_gate_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac1b", STUB_SRC)
    _result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    (ev,) = _rung(events)
    assert ev["status"] == "red" and ev["red_step"] == "stub" and ev["gate"] == "fix_integrity"
    assert len(calls) == 1
    assert calls[0]["extra_data"]["preflight"] == {"status": "red", "red_step": "stub"}


def test_AC1_producer_called_with_green_phase_and_diff_test_paths(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac1c")
    seen = _spy(monkeypatch)
    _drive(monkeypatch, tmp_path, paths=[TEST_REL, "tests/gone.py"], git_cwd=str(repo))
    assert len(seen) == 1
    a, k = seen[0]
    assert k.get("phase") == "green"
    assert list(a[1]) == [TEST_REL]


# ---------------------------------------------------------------- AC2

def test_AC2_produce_false_off_and_bad_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac2")
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1 and _rung(events), "forcing: default mode calls the producer and emits"
    _pf().receipt_path(repo).unlink()

    seen.clear()
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"produce": False})
    assert not seen and _rung(events)[0]["status"] == "missing" and len(calls) == 1

    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"mode": "off"})
    assert not seen and not _rung(events) and "preflight" not in calls[0]["extra_data"] and len(calls) == 1

    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"produce": "no"})
    assert not seen and _rung(events)[0]["status"] == "config-error" and len(calls) == 1

    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"mode": "bogus"})
    assert not seen and _rung(events)[0]["status"] == "config-error" and len(calls) == 1


# ---------------------------------------------------------------- AC3

def test_AC3_producer_raising_is_swallowed_rung_and_gate_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac3")
    seen = _spy(monkeypatch, raises=True)
    result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1, "forcing: producer was reached"
    (ev,) = _rung(events)
    assert ev["status"] == "missing" and ev["gate"] == "fix_integrity"
    assert len(calls) == 1 and result.status == "ok"


# ---------------------------------------------------------------- AC4

def test_AC4_gate_kwargs_equal_across_modes_and_receipts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def norm(call: dict) -> dict:
        out = dict(call)
        out["extra_data"] = {k: (Path(v).name if k in ("doc_path", "diff_path") else v)
                             for k, v in call["extra_data"].items() if k != "preflight"}
        return out

    fresh_repo = _mk(tmp_path / "a", "ac4a")
    _r, on, _e = _drive(monkeypatch, tmp_path / "a", git_cwd=str(fresh_repo))
    assert on[0]["extra_data"]["preflight"]["status"] == "fresh", "forcing"
    off_repo = _mk(tmp_path / "b", "ac4b")
    _r, off, _e = _drive(monkeypatch, tmp_path / "b", git_cwd=str(off_repo), preflight_rung={"produce": False})
    assert off[0]["extra_data"]["preflight"]["status"] == "missing"
    red_repo = _mk(tmp_path / "c", "ac4c", STUB_SRC)
    _r, red, _e = _drive(monkeypatch, tmp_path / "c", git_cwd=str(red_repo))
    assert red[0]["extra_data"]["preflight"]["status"] == "red"

    mode_off_repo = _mk(tmp_path / "d", "ac4d")
    _r, mode_off, _e = _drive(monkeypatch, tmp_path / "d", git_cwd=str(mode_off_repo),
                              preflight_rung={"mode": "off"})
    assert "preflight" not in mode_off[0]["extra_data"]

    assert norm(on[0]) == norm(off[0]) == norm(red[0]) == norm(mode_off[0])
    expected_keys = {"prompt", "model", "timeout_sec", "step_name", "extra_data",
                     "hard_gate", "gate_label", "allowed_tools", "injections"}
    for c in (on[0], off[0], red[0], mode_off[0]):
        assert set(c) == expected_keys
        assert c["prompt"] == "PROMPT-219-S6"
        assert c["step_name"] == "invoke_fix_integrity_llm"
        assert c["gate_label"] == "fix_integrity" and c["hard_gate"] is True
        assert c["allowed_tools"] == ["Read"]
        assert set(k for k in c["extra_data"] if k != "preflight") == {"doc_path", "diff_path"}


def test_AC4b_event_cycle_comes_from_cfg_not_prev_data(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path / "a", "ac4b1")
    _r, _c, events = _drive(monkeypatch, tmp_path / "a", git_cwd=str(repo),
                            cycle=3, prev_extra={"cycle": 7})
    (ev,) = _rung(events)
    assert ev["status"] == "fresh", "forcing: the rung ran"
    assert ev["cycle"] == 3

    repo2 = _mk(tmp_path / "b", "ac4b2")
    _r, _c, events = _drive(monkeypatch, tmp_path / "b", git_cwd=str(repo2))
    (ev,) = _rung(events)
    assert ev["status"] == "fresh" and ev["cycle"] == 1


# ---------------------------------------------------------------- AC5

def test_AC5_ambient_cwd_does_not_call_producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac5")
    seen = _spy(monkeypatch)
    _r, _c, _e = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1, "forcing: non-ambient cwd reaches the producer"
    seen.clear()
    _r, calls, events = _drive(monkeypatch, tmp_path)
    assert not seen
    assert _rung(events)[0]["status"] == "ambient-skip" and len(calls) == 1


# ---------------------------------------------------------------- AC6

def test_AC6_no_changes_short_circuit_no_producer_no_rung(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac6")
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1 and _rung(events), "forcing: the normal path reaches the producer"
    seen.clear()
    result, calls, events = _drive(monkeypatch, tmp_path, prev_extra={"verdict_override": "NO_CHANGES"},
                                   git_cwd=str(repo))
    assert not seen and not _rung(events) and not calls
    assert result.status == "ok" and result.data["skipped"] is True


# ---------------------------------------------------------------- AC7

def test_AC7_only_deleted_test_files_is_missing_never_fresh(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pf = _pf()
    repo = _mk(tmp_path, "ac7a")
    assert _green(repo)["exit_code"] == 0, "forcing: a valid run pre-stages a fresh green receipt"
    assert pf.receipt_rung("green", str(repo))["status"] == "fresh"
    result, calls, events = _drive(monkeypatch, tmp_path, paths=["tests/gone.py"], git_cwd=str(repo))
    (ev,) = _rung(events)
    assert ev["status"] == "missing"
    assert len(calls) == 1 and result.status == "ok"
    assert not pf.receipt_path(repo).exists()


def test_AC7_absent_and_unreadable_diff_path_is_missing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac7b")
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1 and _rung(events)[0]["status"] == "fresh", "forcing: valid diff reaches the producer"
    _pf().receipt_path(repo).unlink()

    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                               prev_extra={"diff_path": str(tmp_path / "nope.patch")})
    _assert_missing(events, calls)

    unreadable = tmp_path / "dir.patch"
    unreadable.mkdir()                      # read_text on a directory raises deterministically
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                               prev_extra={"diff_path": str(unreadable)})
    _assert_missing(events, calls)


# ---------------------------------------------------------------- AC8

def test_AC8_reroll_runs_producer_and_event_once_llm_twice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac8")
    seen = _spy(monkeypatch)
    result, calls, events = _drive(
        monkeypatch, tmp_path, raws=["no verdict here\n", "VERDICT: SPEC_CHANGE\n"], git_cwd=str(repo))
    assert len(seen) == 1
    assert len(_rung(events)) == 1 and _rung(events)[0]["status"] == "fresh"
    assert len(calls) == 2 and result.status == "ok"
    assert calls[0]["extra_data"]["preflight"]["status"] == "fresh"
    assert calls[1]["extra_data"]["preflight"]["status"] == "fresh"


# ---------------------------------------------------------------- AC9

def test_AC9_c_quoted_header_empties_scope_never_fresh(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac9a")
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1 and _rung(events)[0]["status"] == "fresh", "forcing: clean header reaches producer"
    _write(repo / "tests" / "tä.py", STUB_SRC)
    diff = _hand_diff(tmp_path, [
        'diff --git "a/tests/t\\303\\244.py" "b/tests/t\\303\\244.py"', _clean_hdr()])
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prev_extra={"diff_path": diff})
    _assert_missing(events, calls)


def test_AC9_dotdot_header_path_empties_scope_never_fresh(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac9b")
    _write(repo / "tests" / "tm.py", STUB_SRC)
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1 and _rung(events)[0]["status"] == "fresh", "forcing: clean header reaches producer"
    diff = _hand_diff(tmp_path, [_clean_hdr("tests/../tests/tm.py"), _clean_hdr()])
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prev_extra={"diff_path": diff})
    _assert_missing(events, calls)


# ---------------------------------------------------------------- AC10

def test_AC10_non_git_git_cwd_is_error_gate_once_no_producer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac10")
    seen = _spy(monkeypatch)
    _r, _c, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(seen) == 1 and _rung(events)[0]["status"] == "fresh", "forcing: git repo reaches producer"
    seen.clear()
    nogit = tmp_path / "nogit"
    nogit.mkdir()
    result, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(nogit))
    assert not seen
    (ev,) = _rung(events)
    assert ev["status"] == "error" and ev["gate"] == "fix_integrity"
    assert len(calls) == 1 and calls[0]["extra_data"]["preflight"]["status"] == "error"
    assert result.status == "ok"


# ---------------------------------------------------------------- AC11

def test_AC11_no_test_run_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _mk(tmp_path, "ac11")
    real = subprocess.run
    cmds: list[str] = []

    def rec(cmd: Any, *a: Any, **k: Any) -> Any:
        cmds.append(" ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", rec)
    _r, calls, events = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    (ev,) = _rung(events)
    assert ev["status"] == "fresh" and len(calls) == 1, "forcing: producer ran and spawned git reads"
    assert cmds
    assert not [c for c in cmds if "pytest" in c or "bun test" in c]


# ---------------------------------------------------------------- AC12

def test_AC12_phase6_reuses_phase5_green_test_paths_and_preflight_module() -> None:
    p6, p5 = _p6(), _p5i()
    assert p6._green_test_paths is p5._green_test_paths
    assert p6.preflight is _pf()
