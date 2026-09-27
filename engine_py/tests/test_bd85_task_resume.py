"""RED tests for bd#85 (P4) — the driver seam: resume the failed phase with the
same run id, stop when a human is needed, cap each task at runs / dollars.

Spec: docs/decisions/2026-09-27-bd85-retry-budgets-phase-resume.md §4.

`bytedigger_engine.lib.task_resume` does not exist yet. It is imported inside
test bodies only, so the file collects cleanly and fails at run time. Event
logs are real JSONL files written in the engine's row shape.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
PHASES = ["phase_1_discovery", "phase_45_spec", "phase_5_implement", "phase_6_review"]


def _tr():
    from bytedigger_engine.lib import task_resume
    return task_resume


def _row(run_id: str, event_type: str, payload: dict) -> str:
    return json.dumps({"ts": "t", "run_id": run_id, "event_type": event_type, "payload": payload})


def _finished(run_id: str, phase: str, status: str, error_code: str | None = None) -> str:
    payload = {"workflow_name": phase, "status": status, "wall_ms": 1}
    if error_code:
        payload["error_code"] = error_code
    return _row(run_id, "workflow_finished", payload)


def _cost(run_id: str, usd: float | None) -> str:
    return _row(run_id, "subprocess_exited", {"cost_usd": usd, "tokens_in": 1, "tokens_out": 1})


def _log(tmp_path: Path, *rows: str) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    return p


# ─── plan_resume ──────────────────────────────────────────────────────────────


def test_ac13_resume_from_the_failed_phase(tmp_path: Path) -> None:
    log = _log(tmp_path,
               _finished("R", "phase_1_discovery", "ok"),
               _finished("R", "phase_45_spec", "ok"),
               _finished("R", "phase_5_implement", "error", "E_GREEN_TESTS_RED"),
               _finished("OTHER", "phase_5_implement", "ok"))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert plan.action == "resume"
    assert plan.resume_from == "phase_5_implement"
    assert plan.completed == ["phase_1_discovery", "phase_45_spec"]


def test_ac13b_fresh_task_starts_at_the_first_phase(tmp_path: Path) -> None:
    plan = _tr().plan_resume(tmp_path / "missing.jsonl", "R", PHASES)
    assert (plan.action, plan.resume_from, plan.completed) == ("resume", "phase_1_discovery", [])


def test_ac13c_all_done(tmp_path: Path) -> None:
    log = _log(tmp_path, *(_finished("R", p, "ok") for p in PHASES))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert (plan.action, plan.resume_from) == ("done", None)


def test_ac13d_last_row_per_phase_wins(tmp_path: Path) -> None:
    log = _log(tmp_path,
               _finished("R", "phase_1_discovery", "error", "E_X"),
               _finished("R", "phase_1_discovery", "ok"))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert plan.resume_from == "phase_45_spec" and plan.completed == ["phase_1_discovery"]


def test_ac13e_shadowed_zombie_rows_are_ignored(tmp_path: Path) -> None:
    from bytedigger_engine.execution_provenance import SHADOW_EVENT_TYPE
    log = _log(tmp_path,
               _row("R", SHADOW_EVENT_TYPE, {"shadowed_event": "workflow_finished",
                                             "workflow_name": "phase_1_discovery", "status": "ok"}))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert plan.resume_from == "phase_1_discovery" and plan.completed == []


def test_ac13f_rerun_of_an_earlier_phase_makes_later_rows_stale(tmp_path: Path) -> None:
    log = _log(tmp_path,
               _finished("R", "phase_1_discovery", "ok"),
               _finished("R", "phase_45_spec", "ok"),
               _finished("R", "phase_5_implement", "error", "E_SPEC_DEFECT"),
               _finished("R", "phase_45_spec", "ok"))  # rerouted spec re-ran
    plan = _tr().plan_resume(log, "R", PHASES)
    assert (plan.action, plan.resume_from) == ("resume", "phase_5_implement"), plan
    assert plan.completed == ["phase_1_discovery", "phase_45_spec"]


def test_ac13g_stale_ok_is_not_completed(tmp_path: Path) -> None:
    log = _log(tmp_path,
               _finished("R", "phase_45_spec", "ok"),
               _finished("R", "phase_1_discovery", "ok"))  # earlier phase re-ran after
    plan = _tr().plan_resume(log, "R", PHASES)
    assert (plan.action, plan.resume_from) == ("resume", "phase_45_spec"), plan


@pytest.mark.parametrize("status, code", [
    ("escalate", None),
    ("error", "E_RED_WORKTREE_DIRTY"),
    ("error", "E_SPEC_DEFECT_BUDGET"),
    ("error", "E_RESTART_CAP"),
    ("error", "E_RESTART_SHORT_CIRCUIT"),
])
def test_ac14_needs_a_human_means_stop(tmp_path: Path, status: str, code: str | None) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "ok"),
               _finished("R", "phase_45_spec", status, code))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert plan.action == "stop" and plan.resume_from is None, plan


def test_ac14b_spec_defect_reroutes_to_spec(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "ok"),
               _finished("R", "phase_45_spec", "ok"),
               _finished("R", "phase_5_implement", "error", "E_SPEC_DEFECT"))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert (plan.action, plan.resume_from) == ("reroute", "phase_45_spec")


def test_ac14c_paused_resumes_same_phase(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "paused"))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert (plan.action, plan.resume_from, plan.last_status) == ("resume", "phase_1_discovery", "paused")


@pytest.mark.parametrize("rows", [
    [_finished("R", "phase_1_discovery", "escalate")],
    [_finished("R", p, "ok") for p in PHASES],
])
def test_ac14e_resume_from_is_none_for_stop_and_done(tmp_path: Path, rows) -> None:
    assert _tr().plan_resume(_log(tmp_path, *rows), "R", PHASES).resume_from is None


def test_ac14d_reset_unsticks_a_stop(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "error", "E_RED_WORKTREE_DIRTY"),
               _row("R", "task_cap_reset", {"reason": "cleaned"}))
    plan = _tr().plan_resume(log, "R", PHASES)
    assert (plan.action, plan.resume_from) == ("resume", "phase_1_discovery"), plan


# ─── begin_task_run ───────────────────────────────────────────────────────────


def _begin(tmp_path: Path, log: Path, **kw):
    return _tr().begin_task_run(tmp_path / "state", "R", log, PHASES,
                                max_runs=kw.get("max_runs", 3), max_cost_usd=kw.get("max_cost_usd", 60.0))


def test_ac15_counts_runs_and_caps_at_max_runs(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "error", "E_X"))
    for n in (1, 2, 3):
        d = _begin(tmp_path, log)
        assert d.allowed and d.runs == n, d
    d = _begin(tmp_path, log)
    assert not d.allowed and d.error_code == "E_TASK_CAP_REACHED" and d.runs == 3, d


def test_ac15b_caps_on_cost(tmp_path: Path) -> None:
    log = _log(tmp_path, _cost("R", 40.0), _cost("R", 25.0), _cost("OTHER", 500.0),
               _finished("R", "phase_1_discovery", "error", "E_X"))
    d = _begin(tmp_path, log)
    assert not d.allowed and d.error_code == "E_TASK_CAP_REACHED", d
    assert d.cost_usd == pytest.approx(65.0)
    assert d.runs == 0, "a refused begin records nothing"


def test_ac15c_unknown_cost_rows_are_reported(tmp_path: Path) -> None:
    log = _log(tmp_path, _cost("R", None), _cost("R", 1.5))
    d = _begin(tmp_path, log)
    assert d.allowed and d.cost_unknown_calls == 1 and d.cost_usd == pytest.approx(1.5), d


def test_ac15d_paused_run_is_not_charged(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "error", "E_X"))
    assert _begin(tmp_path, log).runs == 1
    log.write_text(log.read_text() + _finished("R", "phase_1_discovery", "paused") + "\n")
    d = _begin(tmp_path, log)
    assert d.allowed and d.runs == 1, f"the paused run's slot is reused: {d}"


def test_ac15d2_paused_credit_is_single_use(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "paused"))
    first = _begin(tmp_path, log, max_runs=1)
    assert first.allowed and first.runs == 0, f"the paused slot is credited once: {first}"
    # a refusal upstream writes no new workflow_finished row: the same paused row
    # must not credit a second begin
    second = _begin(tmp_path, log, max_runs=1)
    assert second.allowed and second.runs == 1, second
    third = _begin(tmp_path, log, max_runs=1)
    assert not third.allowed and third.error_code == "E_TASK_CAP_REACHED", third


def test_ac15e_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "task-runs-R.json").write_text("{not json", encoding="utf-8")
    d = _begin(tmp_path, _log(tmp_path))
    assert not d.allowed and d.error_code == "E_TASK_LEDGER_CORRUPT", d


def test_ac15f_unreadable_log_fails_closed(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.mkdir()  # exists but is not a readable file
    d = _begin(tmp_path, log)
    assert not d.allowed and d.error_code == "E_TASK_COST_UNREADABLE", d


def test_ac15g_reset_clears_the_ledger_and_logs_reason(tmp_path: Path) -> None:
    log = _log(tmp_path)
    for _ in range(3):
        _begin(tmp_path, log)
    _tr().reset_task_runs(tmp_path / "state", "R", log, "operator fixed the worktree")
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    resets = [r for r in rows if r["event_type"] == "task_cap_reset"]
    assert resets and resets[-1]["payload"]["reason"] == "operator fixed the worktree"
    assert _begin(tmp_path, log).runs == 1


@pytest.mark.parametrize("rows, action", [
    ([_finished("R", p, "ok") for p in PHASES], "done"),
    ([_finished("R", "phase_1_discovery", "escalate")], "stop"),
    ([_finished("R", "phase_1_discovery", "error", "E_SPEC_DEFECT")], "reroute"),
])
def test_ac15h_done_stop_reroute_are_not_allowed(tmp_path: Path, rows, action) -> None:
    d = _begin(tmp_path, _log(tmp_path, *rows))
    assert not d.allowed and d.action == action and d.runs == 0, d


# ─── run.py --task-begin / --task-reset ───────────────────────────────────────


def _run_py(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ENGINE_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.run", *args],
        capture_output=True, text=True, env=env, cwd=str(ENGINE_ROOT), timeout=120,
    )


def test_ac16_cli_task_begin(tmp_path: Path) -> None:
    log = _log(tmp_path, _finished("R", "phase_1_discovery", "ok"),
               _finished("R", "phase_45_spec", "error", "E_X"))
    p = _run_py("--task-begin", ",".join(PHASES), "--run-id", "R", "--event-log", str(log))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["action"] == "resume" and out["resume_from"] == "phase_45_spec", out
    assert out["runs"] == 1 and out["allowed"] is True and out["run_id"] == "R"
    p2 = _run_py("--task-begin", ",".join(PHASES), "--run-id", "R", "--event-log", str(log),
                 "--task-max-runs", "1")
    assert p2.returncode == 1
    assert json.loads(p2.stdout.strip().splitlines()[-1])["error_code"] == "E_TASK_CAP_REACHED"


def test_ac16b_cli_task_begin_requires_run_id_and_log(tmp_path: Path) -> None:
    p = _run_py("--task-begin", "phase_1_discovery")
    assert p.returncode == 2


def test_ac16c_cli_task_reset(tmp_path: Path) -> None:
    log = _log(tmp_path)
    _run_py("--task-begin", "phase_1_discovery", "--run-id", "R", "--event-log", str(log),
            "--task-max-runs", "1")
    p = _run_py("--task-reset", "manual unblock", "--run-id", "R", "--event-log", str(log))
    assert p.returncode == 0, p.stderr
    again = _run_py("--task-begin", "phase_1_discovery", "--run-id", "R", "--event-log", str(log),
                    "--task-max-runs", "1")
    assert again.returncode == 0, again.stdout


# ─── templates/driver-resume.sh ───────────────────────────────────────────────


_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\n")
if "--task-begin" in args:
    print(json.dumps(json.loads(os.environ["STUB_PLAN"])))
    sys.exit(0 if json.loads(os.environ["STUB_PLAN"])["allowed"] else 1)
wf = args[args.index("--workflow") + 1]
fail = os.environ.get("STUB_FAIL_AT")
print(json.dumps({"status": "error" if wf == fail else "ok"}))
sys.exit(1 if wf == fail else 0)
'''


def _drive(tmp_path: Path, plan: dict, fail_at: str | None = None) -> tuple[int, list[list[str]]]:
    stub = tmp_path / "engine-stub"
    stub.write_text(_STUB)
    stub.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    ctx = tmp_path / "ctx.json"
    ctx.write_text("{}")
    env = {**os.environ, "BD_ENGINE_RUN": str(stub), "STUB_CALLS": str(calls),
           "STUB_PLAN": json.dumps(plan), "STUB_FAIL_AT": fail_at or ""}
    p = subprocess.run(
        ["bash", str(REPO_ROOT / "templates" / "driver-resume.sh"),
         "RUN42", ",".join(PHASES), str(ctx), str(tmp_path / "events.jsonl")],
        capture_output=True, text=True, env=env, timeout=60,
    )
    return p.returncode, [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


def test_ac17_template_resumes_from_plan_with_same_run_id(tmp_path: Path) -> None:
    plan = {"allowed": True, "action": "resume", "resume_from": "phase_5_implement"}
    rc, calls = _drive(tmp_path, plan)
    assert rc == 0
    assert "--task-begin" in calls[0]
    ran = [c[c.index("--workflow") + 1] for c in calls[1:]]
    assert ran == ["phase_5_implement", "phase_6_review"], ran
    assert all(c[c.index("--run-id") + 1] == "RUN42" for c in calls), calls


def test_ac17b_template_stops_on_first_failure(tmp_path: Path) -> None:
    plan = {"allowed": True, "action": "resume", "resume_from": "phase_1_discovery"}
    rc, calls = _drive(tmp_path, plan, fail_at="phase_45_spec")
    assert rc != 0
    ran = [c[c.index("--workflow") + 1] for c in calls[1:]]
    assert ran == ["phase_1_discovery", "phase_45_spec"], ran


@pytest.mark.parametrize("plan", [
    {"allowed": False, "action": "resume", "resume_from": "phase_1_discovery",
     "error_code": "E_TASK_CAP_REACHED"},
    {"allowed": False, "action": "stop", "resume_from": None},
    {"allowed": False, "action": "reroute", "resume_from": "phase_45_spec"},
])
def test_ac17c_template_runs_nothing_when_not_allowed(tmp_path: Path, plan: dict) -> None:
    rc, calls = _drive(tmp_path, plan)
    assert rc != 0
    assert len(calls) == 1 and "--task-begin" in calls[0], calls


def test_ac17d_template_done_exits_zero(tmp_path: Path) -> None:
    rc, calls = _drive(tmp_path, {"allowed": False, "action": "done", "resume_from": None})
    assert rc == 0 and len(calls) == 1, calls
