"""RED tests -- bd#141 item 6 (v2): readiness start gate inside engine Phase 5.

Spec: docs/decisions/2026-10-02-bd141-start-gate.md (section 3, ACs S1..S14).

AC -> tests: one test per AC, test_S1_* .. test_S14_*.

Section 1i: every contested state (labels, comments, events, gh switch) is PRE-STAGED in the rig
state file before the unit under test runs; nothing races.
Section 1q: phase_5_implement attributes (`_readiness`, `_readiness_start_gate`) are accessed inside
test bodies only, so collection never errors on the not-yet-built symbols.
S1 is a structure guard and is expected to pass before GREEN.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import test_bd117a_readiness as r117

REPO_ROOT = Path(__file__).resolve().parents[2]

EVENT = "readiness_start_verdict"
EVENT_KEYS = {"stage", "verdict", "reason", "issue", "record_sha256"}

PINNED_STEPS = [
    "validation_cycle_loop", "build_green_prompt", "cwd_preflight", "invoke_green_llm",
    "check_green_token_budget", "write_green_artifact", "verify_green_lint_rules",
    "verify_security_lint", "verify_green_passing", "verify_green_typecheck",
    "verify_registered_skills", "commit_green_code", "green_watchdog",
]


def _p5():
    import bytedigger_engine.workflows.phase_5_implement as p5
    return p5


def _ctx(rig):
    from bytedigger_engine.contracts import WorkflowContext

    cfg = {"git_cwd": str(rig.repo), "scratchpad_dir": str(rig.root / "scratch")}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=cfg, question="build",
        session_id="p6-start-gate", persona="hal", framework=None, domain=None,
    )


def _spec_file(rig, text=r117.SPEC_TEXT) -> Path:
    p = rig.root / "scratch" / "specs" / "build-spec.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))
    return p


def _capture(monkeypatch):
    p5 = _p5()
    events: list = []
    monkeypatch.setattr(p5, "_emit_safe", lambda et, payload=None, *a, **k: events.append((et, payload)))
    return events


def _start_events(events):
    return [(et, pl) for et, pl in events if et == EVENT]


def _approved_rig(tmp_path, monkeypatch, **kw):
    rig = r117.make_rig(tmp_path, monkeypatch, **kw)
    rig.approve()
    _spec_file(rig)
    return rig


def _label_absent_rig(tmp_path, monkeypatch, **kw):
    rig = r117.make_rig(tmp_path, monkeypatch, **kw)
    rig.seed(labels=[], comments=[r117.record(1)], events=[])
    _spec_file(rig)
    return rig


def _assert_refusal(res, reason, *, tag="42"):
    assert res is not None, "NOT_APPROVED must return an error StepResult"
    assert res.status == "error"
    assert res.error_code == "E_READINESS_NOT_APPROVED"
    assert res.recoverable is False
    assert res.step_name == "validation_cycle_loop"
    assert res.error == f"readiness: not approved ({reason}) #{tag}"


# --------------------------------------------------------------------------- S1


def test_S1_structure_unchanged_no_new_step():
    names = [s.name for s in _p5().phase_5_implement_workflow().steps]
    assert names == PINNED_STEPS
    assert "readiness_start_gate" not in names


# --------------------------------------------------------------------------- S2


def test_S2_off_proceeds_no_gh_no_event(tmp_path, monkeypatch):
    rig = r117.make_rig(tmp_path, monkeypatch, policy={"other": 1})
    _spec_file(rig)
    events = _capture(monkeypatch)
    assert _p5()._readiness_start_gate(_ctx(rig), None) is None
    assert rig.gh_calls() == []
    assert _start_events(events) == []


# --------------------------------------------------------------------------- S3


def test_S3_approved_not_consumed(tmp_path, monkeypatch):
    rig = _approved_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    p5 = _p5()
    ctx = _ctx(rig)
    assert p5._readiness_start_gate(ctx, None) is None
    evs = _start_events(events)
    assert len(evs) == 1
    payload = evs[0][1]
    assert set(payload) == EVENT_KEYS
    assert payload["verdict"] == "APPROVED"
    assert payload["issue"] == 42
    assert payload["stage"] == "start"
    assert p5._readiness_start_gate(ctx, None) is None
    assert r117.comment_creates(rig) == []
    assert r117.label_removals(rig) == []


# --------------------------------------------------------------------------- S4


def test_S4_label_absent_refuses(tmp_path, monkeypatch):
    rig = _label_absent_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    res = _p5()._readiness_start_gate(_ctx(rig), None)
    _assert_refusal(res, "label_absent")
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "NOT_APPROVED"
    assert evs[0][1]["reason"] == "label_absent"


# --------------------------------------------------------------------------- S5


def test_S5_spec_changed_refuses(tmp_path, monkeypatch):
    rig = r117.make_rig(tmp_path, monkeypatch)
    rig.approve(r117.SPEC_TEXT)
    _spec_file(rig, r117.OTHER_SPEC_TEXT)
    events = _capture(monkeypatch)
    res = _p5()._readiness_start_gate(_ctx(rig), None)
    _assert_refusal(res, "spec_changed")
    assert _start_events(events)[0][1]["reason"] == "spec_changed"


# --------------------------------------------------------------------------- S6


def test_S6_spec_absent_still_approved(tmp_path, monkeypatch):
    rig = r117.make_rig(tmp_path, monkeypatch)
    rig.approve()
    events = _capture(monkeypatch)
    assert _p5()._readiness_start_gate(_ctx(rig), None) is None
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "APPROVED"


# --------------------------------------------------------------------------- S7


def test_S7_no_issue_refuses(tmp_path, monkeypatch):
    rig = r117.make_rig(tmp_path, monkeypatch, branch="feature-x")
    _spec_file(rig)
    events = _capture(monkeypatch)
    res = _p5()._readiness_start_gate(_ctx(rig), None)
    _assert_refusal(res, "no_issue", tag="")
    assert _start_events(events)[0][1]["reason"] == "no_issue"


# --------------------------------------------------------------------------- S8


def test_S8_unavailable_fails_open(tmp_path, monkeypatch):
    rig = _approved_rig(tmp_path, monkeypatch)
    rig.switch(mode="exit1")
    events = _capture(monkeypatch)
    assert _p5()._readiness_start_gate(_ctx(rig), None) is None
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "UNAVAILABLE"
    assert evs[0][1]["reason"]


# --------------------------------------------------------------------------- S9


def test_S9_crash_fails_open(tmp_path, monkeypatch):
    rig = _approved_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    p5 = _p5()

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(p5._readiness, "verdict", boom)
    assert p5._readiness_start_gate(_ctx(rig), None) is None
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "UNAVAILABLE"
    assert evs[0][1]["reason"].startswith("internal error")
    assert "boom" in evs[0][1]["reason"]


# --------------------------------------------------------------------------- S10


def test_S10_verdict_arguments(tmp_path, monkeypatch):
    rig = _approved_rig(tmp_path, monkeypatch)
    _capture(monkeypatch)
    p5 = _p5()
    real = p5._readiness.verdict
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    monkeypatch.setattr(p5._readiness, "verdict", spy)
    p5._readiness_start_gate(_ctx(rig), None)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert not kwargs
    assert Path(args[0]).resolve() == Path(rig.repo).resolve()
    assert args[1] == "start"
    assert args[2] == str(rig.root / "scratch" / "specs" / "build-spec.md")
    p5._readiness_start_gate(_ctx(rig), None)
    assert len(calls) == 2
    assert all(c[0][1] != "ship" for c in calls)


# --------------------------------------------------------------------------- S11


class _Stop(Exception):
    pass


def _record_llm(monkeypatch, p5):
    calls: list = []

    def rec(*a, **k):
        calls.append((a, k))
        raise _Stop("recorder stops the loop")

    monkeypatch.setattr(p5, "invoke_llm_subprocess", rec)
    return calls


def _head(repo) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_S11_composite_refuses_before_red(tmp_path, monkeypatch):
    rig = _label_absent_rig(tmp_path, monkeypatch)
    _capture(monkeypatch)
    p5 = _p5()
    calls = _record_llm(monkeypatch, p5)
    head_before = _head(rig.repo)
    res = None
    try:
        res = p5.phase_5_implement_workflow().steps[0].execute(_ctx(rig), None)
    except _Stop:
        pass
    assert calls == []
    assert res is not None and res.error_code == "E_READINESS_NOT_APPROVED"
    status =subprocess.run(["git", "-C", str(rig.repo), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    assert status == ""
    assert _head(rig.repo) == head_before


# --------------------------------------------------------------------------- S12


def test_S12_composite_passes_through_when_off(tmp_path, monkeypatch):
    rig = r117.make_rig(tmp_path, monkeypatch, policy={"other": 1})
    _spec_file(rig)
    _capture(monkeypatch)
    p5 = _p5()
    real = p5._readiness.verdict
    verdict_calls: list = []

    def spy(*a, **k):
        verdict_calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(p5._readiness, "verdict", spy)
    calls = _record_llm(monkeypatch, p5)
    res = None
    try:
        res = p5.phase_5_implement_workflow().steps[0].execute(_ctx(rig), None)
    except _Stop:
        pass
    # the gate was wired into the composite and consulted at stage "start" ...
    assert len(verdict_calls) >= 1 and verdict_calls[0][1] == "start"
    # ... and did not swallow the loop
    assert calls or (res is not None and res.error_code != "E_READINESS_NOT_APPROVED")


# --------------------------------------------------------------------------- S15


def test_S15_spec_resolution_failure_degrades(tmp_path, monkeypatch):
    rig = _approved_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    p5 = _p5()
    real = p5._readiness.verdict
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    def bad_scratch(*a, **k):
        raise RuntimeError("scratchboom")

    monkeypatch.setattr(p5._readiness, "verdict", spy)
    monkeypatch.setattr(p5, "_resolve_scratchpad", bad_scratch)
    assert p5._readiness_start_gate(_ctx(rig), None) is None
    assert len(calls) == 1
    args, kwargs = calls[0]
    spec_arg = kwargs["spec_path"] if "spec_path" in kwargs else (args[2] if len(args) > 2 else None)
    assert spec_arg is None
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "APPROVED"


# --------------------------------------------------------------------------- S16


def test_S16_repo_resolution_failure_fails_open(tmp_path, monkeypatch):
    rig = _approved_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    p5 = _p5()
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        return {"verdict": "APPROVED"}

    def bad_cwd(*a, **k):
        raise RuntimeError("cwdboom")

    monkeypatch.setattr(p5._readiness, "verdict", spy)
    monkeypatch.setattr(p5, "_resolve_git_cwd_with_source", bad_cwd)
    assert p5._readiness_start_gate(_ctx(rig), None) is None
    assert calls == []
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "UNAVAILABLE"
    assert evs[0][1]["reason"].startswith("internal error")
    assert "cwdboom" in evs[0][1]["reason"]


# --------------------------------------------------------------------------- S17


def test_S17_ambient_git_cwd_skipped(tmp_path, monkeypatch):
    from bytedigger_engine.contracts import WorkflowContext

    rig = _approved_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    p5 = _p5()
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        raise AssertionError("verdict must not be called for an ambient git_cwd")

    monkeypatch.setattr(p5._readiness, "verdict", spy)
    scratch = rig.root / "scratch"
    assert not any((p / ".git").exists() for p in [scratch.resolve(), *scratch.resolve().parents])
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config={"scratchpad_dir": str(scratch)},
        question="build", session_id="p6-start-gate-ambient", persona="hal", framework=None,
        domain=None,
    )
    monkeypatch.chdir(rig.repo)
    calls_before = list(rig.gh_calls())
    assert p5._readiness_start_gate(ctx, None) is None
    assert calls == []
    evs = _start_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "UNAVAILABLE"
    assert evs[0][1]["reason"] == "ambient_git_cwd"
    ref = subprocess.run(["git", "-C", str(rig.repo), "show-ref", "--verify", "refs/bd/policy"],
                         capture_output=True, text=True)
    assert ref.returncode != 0
    assert rig.gh_calls() == calls_before == []


# --------------------------------------------------------------------------- S18


def test_S18_refusal_before_reroute_block(tmp_path, monkeypatch):
    from types import SimpleNamespace

    rig = _label_absent_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    p5 = _p5()
    llm_calls = _record_llm(monkeypatch, p5)
    consumed_q: list = []
    marked: list = []

    def rec_consumed(*a, **k):
        consumed_q.append(a)
        return False

    def rec_mark(*a, **k):
        marked.append(a)
        return True

    monkeypatch.setattr(p5, "reroute_already_consumed", rec_consumed)
    monkeypatch.setattr(p5, "mark_reroute_consumed", rec_mark)
    monkeypatch.setattr(p5.telemetry_ctx, "get_current_run", lambda: SimpleNamespace(run_id="p6-run"))
    ctx = _ctx(rig)
    ctx.org_config["phase_reroute"] = {"attempt": 1, "from_phase": "prior_phase"}
    res = None
    raised = None
    try:
        res = p5.phase_5_implement_workflow().steps[0].execute(ctx, None)
    except Exception as exc:  # pre-GREEN the loop runs past the reroute block and may crash
        raised = exc
    assert consumed_q == []
    assert marked == []
    assert [et for et, _ in events if et == "phase_reroute_entry"] == []
    assert llm_calls == []
    assert raised is None, repr(raised)
    assert res is not None and res.error_code == "E_READINESS_NOT_APPROVED"


# --------------------------------------------------------------------------- S13


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    assert m, f"missing section {heading}"
    rest = text[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return rest if not n else rest[:n.start()]


def test_S13_registries_name_both_callers():
    from bytedigger_engine import error_codes

    desc = error_codes.ERROR_CODES["E_READINESS_NOT_APPROVED"]
    assert "phase_5_implement" in desc
    assert "phase_8_post_deploy" in desc
    for rel in ("engine_py/ERROR_CODES.md", "engine_py/bytedigger_engine/ERROR_CODES.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "phase_5_implement" in _section(text, "E_READINESS"), rel


# --------------------------------------------------------------------------- S14


def test_S14_docs_and_changelog():
    cfg = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert "get only the ship gate" not in cfg
    m = re.search(r"^#+ .*[Rr]eadiness.*$", cfg, re.M)
    assert m, "docs/configuration.md has no readiness heading"
    rest = cfg[m.end():]
    n = re.search(r"^## ", rest, re.M)
    section = rest if not n else rest[:n.start()]
    assert "phase_5_implement" in section
    log = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    from helpers.changelog import require_entry

    require_entry(log, "**Readiness start gate in engine Phase 5 (bd#141 item 6).**")
