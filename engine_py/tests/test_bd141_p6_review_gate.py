"""RED tests -- bd#141 item 6 (residue): review-readiness gate inside engine Phase 6.

Spec: docs/decisions/2026-10-02-bd141-review-gate.md (section 3, ACs R1..R24).
AC -> tests: test_r<N>_* (R9/R17 have several functions; R12 and R22/R23 are single functions).
The `_verdict` helper turns the pre-GREEN ValueError for stage 'review' into pytest.fail, so
R1-R8/R10 fail at assert time.

Section 1i: every contested state (labels, comments, events, gh switch) is PRE-STAGED in the rig
state file before the unit under test runs; nothing races.
Section 1q: new phase_6_review attributes (`_readiness`, `_readiness_review_gate`) and the new
readiness stage/policy key are touched only inside test bodies, so collection never errors on the
not-yet-built symbols and each RED fails at an assert.
R13 is a structure guard and is expected to pass before GREEN.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import test_bd117a_readiness as r117

import bytedigger_engine.workflows.phase_6_review as p6

REPO_ROOT = Path(__file__).resolve().parents[2]

READY = "ready-for-review"
EVENT = "readiness_review_verdict"
EVENT_KEYS = {"stage", "verdict", "reason", "issue", "record_sha256"}
REVIEW_POLICY = {"readiness": {"required": True, "review_label": READY}}

PINNED_STEPS = [
    "build_review_prompt", "invoke_review_llm",
    "write_review_artifact", "verify_findings", "verify_findings_semantic", "build_fix_prompt",
    "invoke_fix_llm", "fix_watchdog", "write_fix_artifact", "commit_fix_code",
    "commit_fix_tests", "run_pytest_post_fix", "verify_fix_typecheck",
    "build_satisfaction_prompt", "invoke_satisfaction_llm", "write_satisfaction_doc", "detect_mass_unverified",
]


# --------------------------------------------------------------------------- helpers


def _rig(tmp_path, monkeypatch, *, policy=REVIEW_POLICY, **kw):
    return r117.make_rig(tmp_path, monkeypatch, policy=policy, branch=kw.pop("branch", "gh42-x"), **kw)


def _ready_rig(tmp_path, monkeypatch, **kw):
    """R3 rig: record (ts 200), plan label (ts 300), ready label (ts 400) -- all after the record."""
    rig = _rig(tmp_path, monkeypatch, **kw)
    rig.seed(
        labels=[r117.LABEL, READY],
        comments=[r117.record(1, ts=200)],
        events=[r117.levent("LE_1", "alice", 300),
                r117.levent("LE_2", "alice", 400, label=READY)],
    )
    return rig


def _plan_only_rig(tmp_path, monkeypatch, **kw):
    """R4 rig: record and plan label present, no ready label."""
    rig = _rig(tmp_path, monkeypatch, **kw)
    rig.seed(labels=[r117.LABEL], comments=[r117.record(1, ts=200)],
             events=[r117.levent("LE_1", "alice", 300)])
    return rig


def _ctx(rig):
    from bytedigger_engine.contracts import WorkflowContext

    cfg = {"git_cwd": str(rig.repo), "scratchpad_dir": str(rig.root / "scratch")}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=cfg, question="build",
        session_id="p6-review-gate", persona="hal", framework=None, domain=None,
    )


def _capture(monkeypatch):
    events: list = []
    monkeypatch.setattr(p6, "_emit_safe", lambda et, payload=None, *a, **k: events.append((et, payload)))
    return events


def _review_events(events):
    return [(et, pl) for et, pl in events if et == EVENT]


def _gate():
    fn = getattr(p6, "_readiness_review_gate", None)
    assert fn is not None, "phase_6_review._readiness_review_gate is not built"
    return fn


def _rd():
    mod = getattr(p6, "_readiness", None)
    assert mod is not None, "phase_6_review has no `_readiness` import"
    return mod


def _verdict(rig, stage):
    from bytedigger_engine import readiness

    try:
        return readiness.verdict(rig.repo, stage)
    except ValueError as e:  # pre-GREEN: stage 'review' is rejected -> fail at assert time
        pytest.fail(f"stage 'review' not supported: {e}")


def _write_policy_rig(tmp_path, monkeypatch, readiness_cfg, **kw):
    return _rig(tmp_path, monkeypatch, policy={"readiness": readiness_cfg}, **kw)


def _cli(args):
    from bytedigger_engine import readiness

    try:
        return readiness.main(args)
    except SystemExit as exc:  # argparse rejection pre-GREEN -> code 2, asserted by the caller
        return exc.code


# --------------------------------------------------------------------------- R1 / R2


def test_r1_review_off_without_key(tmp_path, monkeypatch):
    from bytedigger_engine import readiness

    rig = _rig(tmp_path, monkeypatch, policy={"readiness": {"required": True}})
    rig.seed(labels=[r117.LABEL], comments=[r117.record(1)], events=[r117.levent("LE_1", "alice", 300)])
    assert _verdict(rig, "review") == readiness._default_result()
    assert rig.gh_calls() == []


def test_r1b_review_off_before_issue_binding(tmp_path, monkeypatch):
    from bytedigger_engine import readiness

    rig = _rig(tmp_path, monkeypatch, policy={"readiness": {"required": True}}, branch="feature-x")
    assert _verdict(rig, "review") == readiness._default_result()


def test_r2_review_off_when_not_required(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch,
               policy={"readiness": {"required": False, "review_label": READY}})
    v = _verdict(rig, "review")
    assert v["verdict"] == "OFF"
    assert rig.gh_calls() == []


# --------------------------------------------------------------------------- R3..R8


def test_r3_approved_with_ready_label(tmp_path, monkeypatch):
    rig = _ready_rig(tmp_path, monkeypatch)
    v = _verdict(rig, "review")
    assert v["verdict"] == "APPROVED", v
    assert v["label"] == READY
    assert v["issue"] == 42
    assert v["required"] is True
    assert r117.comment_creates(rig) == []
    assert r117.label_removals(rig) == []


def test_r3b_plan_consumption_does_not_leak(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch)
    rig.seed(
        labels=[r117.LABEL, READY],
        comments=[r117.record(1, ts=200),
                  r117.consumption(2, "LE_1", "gh99-other", ts=500)],
        events=[r117.levent("LE_1", "alice", 300),
                r117.levent("LE_2", "alice", 400, label=READY)],
    )
    v = _verdict(rig, "review")
    assert v["verdict"] == "APPROVED", v


def test_r4_only_plan_label_is_not_ready(tmp_path, monkeypatch):
    rig = _plan_only_rig(tmp_path, monkeypatch)
    v = _verdict(rig, "review")
    assert v["verdict"] == "NOT_APPROVED", v
    assert v["reason"] == "label_absent"


def test_r5_ready_label_predates_record(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch)
    rig.seed(
        labels=[r117.LABEL, READY],
        comments=[r117.record(1, ts=500)],
        events=[r117.levent("LE_1", "alice", 300),
                r117.levent("LE_2", "alice", 400, label=READY)],
    )
    v = _verdict(rig, "review")
    assert v["verdict"] == "NOT_APPROVED", v
    assert v["reason"] == "label_predates_spec"


def test_r6_self_marked_ready(tmp_path, monkeypatch):
    rig = _write_policy_rig(tmp_path, monkeypatch,
                            {"required": True, "review_label": READY, "distinct_actor": True})
    rig.seed(
        labels=[r117.LABEL, READY],
        comments=[r117.record(1, ts=200)],
        events=[r117.levent("LE_1", "alice", 300),
                r117.levent("LE_2", r117.BD_USER, 400, label=READY)],
    )
    v = _verdict(rig, "review")
    assert v["verdict"] == "NOT_APPROVED", v
    assert v["reason"] == "self_approved"


def test_r7_approvers_enforced(tmp_path, monkeypatch):
    rig = _write_policy_rig(tmp_path, monkeypatch,
                            {"required": True, "review_label": READY, "approvers": ["alice"]})
    rig.seed(
        labels=[r117.LABEL, READY],
        comments=[r117.record(1, ts=200)],
        events=[r117.levent("LE_1", "alice", 300),
                r117.levent("LE_2", "bob", 400, label=READY)],
    )
    v = _verdict(rig, "review")
    assert v["verdict"] == "NOT_APPROVED", v
    assert v["reason"] == "approver_not_allowed"


def test_r8_no_issue(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch, branch="feature-x")
    v = _verdict(rig, "review")
    assert v["verdict"] == "NOT_APPROVED", v
    assert v["reason"] == "no_issue"
    assert v["issue"] is None


# --------------------------------------------------------------------------- R9


@pytest.mark.parametrize("bad", ["", 5, "Plan-Approved"], ids=["empty", "int", "casefold-equals-label"])
@pytest.mark.parametrize("stage", ["start", "review", "ship"])
def test_r9_review_label_type_errors_unavailable(tmp_path, monkeypatch, bad, stage):
    rig = _write_policy_rig(tmp_path, monkeypatch, {"required": True, "review_label": bad})
    rig.seed(labels=[r117.LABEL], comments=[r117.record(1)], events=[r117.levent("LE_1", "alice", 300)])
    v = _verdict(rig, stage)
    assert v["verdict"] == "UNAVAILABLE", v
    assert "wrong type" in v["reason"]


def test_r9_malformed_review_label_unavailable_even_when_not_required(tmp_path, monkeypatch):
    rig = _write_policy_rig(tmp_path, monkeypatch, {"required": False, "review_label": 5})
    rig.seed(labels=[r117.LABEL], comments=[r117.record(1)], events=[r117.levent("LE_1", "alice", 300)])
    v = _verdict(rig, "review")
    assert v["verdict"] == "UNAVAILABLE", v
    assert "wrong type" in v["reason"]


def test_r9b_explicit_null_review_label_is_off(tmp_path, monkeypatch):
    from bytedigger_engine import readiness

    rig = _write_policy_rig(tmp_path, monkeypatch, {"required": True, "review_label": None})
    rig.seed(labels=[r117.LABEL], comments=[r117.record(1)], events=[r117.levent("LE_1", "alice", 300)])
    assert _verdict(rig, "review") == readiness._default_result()
    assert _verdict(rig, "start")["verdict"] != "UNAVAILABLE"


# --------------------------------------------------------------------------- R10


def test_r10_start_and_ship_ignore_review_label(tmp_path, monkeypatch):
    rig = _plan_only_rig(tmp_path, monkeypatch)
    assert _verdict(rig, "start")["verdict"] == "APPROVED"
    # the review stage must be accepted at all; plan-only is NOT ready. Evaluated before rig2 is
    # built: make_rig repoints the process-wide gh/ssh env.
    assert _verdict(rig, "review")["reason"] == "label_absent"
    # a ready label alone must not satisfy start
    rig2_root = tmp_path / "second"
    rig2_root.mkdir()
    rig2 = _rig(rig2_root, monkeypatch)
    rig2.seed(labels=[READY], comments=[r117.record(1, ts=200)],
              events=[r117.levent("LE_9", "alice", 400, label=READY)])
    v = _verdict(rig2, "start")
    assert v["verdict"] == "NOT_APPROVED", v
    assert v["reason"] == "label_absent"


# --------------------------------------------------------------------------- R11


def test_r11_stage_validation_names_review(tmp_path, monkeypatch):
    from bytedigger_engine import readiness

    rig = _rig(tmp_path, monkeypatch)
    with pytest.raises(ValueError) as ei:
        readiness.verdict(rig.repo, "deploy")
    assert "review" in str(ei.value)


# --------------------------------------------------------------------------- R12


def test_r12_cli_review_stage(tmp_path, monkeypatch, capsys):
    rig = _plan_only_rig(tmp_path, monkeypatch)
    rc = _cli(["check", "--stage", "review", "--repo", str(rig.repo)])
    err = capsys.readouterr().err
    assert rc == 3
    assert "E_READINESS_NOT_APPROVED label_absent #42" in err

    ok_root = tmp_path / "ok"
    ok_root.mkdir()
    ok = _ready_rig(ok_root, monkeypatch)
    assert _cli(["check", "--stage", "review", "--repo", str(ok.repo)]) == 0
    capsys.readouterr()

    bad_root = tmp_path / "bad"
    bad_root.mkdir()
    bad = _rig(bad_root, monkeypatch, policy={"readiness": "yes"})
    assert _cli(["check", "--stage", "review", "--repo", str(bad.repo)]) == 0
    assert _cli(["check", "--stage", "ship", "--repo", str(bad.repo)]) == 4


# --------------------------------------------------------------------------- R13


def test_r13_structure_unchanged_no_new_step():
    names = [s.name for s in p6.phase_6_review_workflow().steps]
    assert names == PINNED_STEPS
    assert not any("readiness" in n for n in names)


# --------------------------------------------------------------------------- R14


def test_r14_helper_off_no_event(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch, policy={"readiness": {"required": True}})
    events = _capture(monkeypatch)
    assert _gate()(_ctx(rig), None) is None
    assert _review_events(events) == []


# --------------------------------------------------------------------------- R15


def test_r15_helper_refuses(tmp_path, monkeypatch):
    rig = _plan_only_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    res = _gate()(_ctx(rig), None)
    assert res is not None, "NOT_APPROVED must return an error StepResult"
    assert res.status == "error"
    assert res.error_code == "E_READINESS_NOT_APPROVED"
    assert res.recoverable is False
    assert res.error == "readiness: not ready for review (label_absent) #42"
    assert res.step_name == "build_review_prompt"
    evs = _review_events(events)
    assert len(evs) == 1
    payload = evs[0][1]
    assert set(payload) == EVENT_KEYS
    assert payload["stage"] == "review"
    assert payload["verdict"] == "NOT_APPROVED"
    assert payload["reason"] == "label_absent"


# --------------------------------------------------------------------------- R16


def test_r16_helper_approves_not_consumed(tmp_path, monkeypatch):
    rig = _ready_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    gate = _gate()
    ctx = _ctx(rig)
    assert gate(ctx, None) is None
    evs = _review_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "APPROVED"
    assert gate(ctx, None) is None
    assert r117.comment_creates(rig) == []
    assert r117.label_removals(rig) == []


# --------------------------------------------------------------------------- R17


def test_r17a_gh_failure_fails_open(tmp_path, monkeypatch):
    rig = _ready_rig(tmp_path, monkeypatch)
    rig.switch(mode="exit1")
    events = _capture(monkeypatch)
    assert _gate()(_ctx(rig), None) is None
    evs = _review_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "UNAVAILABLE"


def test_r17b_verdict_crash_fails_open(tmp_path, monkeypatch):
    rig = _ready_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    gate = _gate()

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(_rd(), "verdict", boom)
    assert gate(_ctx(rig), None) is None
    evs = _review_events(events)
    assert len(evs) == 1
    assert evs[0][1]["reason"].startswith("internal error")
    assert "boom" in evs[0][1]["reason"]


def test_r17c_repo_resolution_crash_fails_open(tmp_path, monkeypatch):
    rig = _ready_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    gate = _gate()
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        return {"verdict": "APPROVED"}

    def bad_cwd(*a, **k):
        raise RuntimeError("cwdboom")

    monkeypatch.setattr(_rd(), "verdict", spy)
    monkeypatch.setattr(p6, "resolve_git_cwd_with_source", bad_cwd)
    assert gate(_ctx(rig), None) is None
    assert calls == []
    evs = _review_events(events)
    assert len(evs) == 1
    assert "cwdboom" in evs[0][1]["reason"]


# --------------------------------------------------------------------------- R18


def test_r18_verdict_arguments(tmp_path, monkeypatch):
    rig = _ready_rig(tmp_path, monkeypatch)
    _capture(monkeypatch)
    gate = _gate()
    rd = _rd()
    real = rd.verdict
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    monkeypatch.setattr(rd, "verdict", spy)
    gate(_ctx(rig), None)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert not kwargs
    assert len(args) == 2
    assert Path(args[0]).resolve() == Path(rig.repo).resolve()
    assert args[1] == "review"
    gate(_ctx(rig), None)
    assert len(calls) == 2
    assert all(c[0][1] not in ("start", "ship") for c in calls)


# --------------------------------------------------------------------------- R19


def test_r19_ambient_git_cwd_skipped(tmp_path, monkeypatch):
    from bytedigger_engine.contracts import WorkflowContext

    rig = _ready_rig(tmp_path, monkeypatch)
    events = _capture(monkeypatch)
    gate = _gate()
    calls: list = []

    def spy(*a, **k):
        calls.append((a, k))
        raise AssertionError("verdict must not be called for an ambient git_cwd")

    monkeypatch.setattr(_rd(), "verdict", spy)
    scratch = rig.root / "scratch"
    assert not any((p / ".git").exists() for p in [scratch.resolve(), *scratch.resolve().parents])
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config={"scratchpad_dir": str(scratch)},
        question="build", session_id="p6-review-gate-ambient", persona="hal", framework=None,
        domain=None,
    )
    monkeypatch.chdir(rig.repo)
    assert gate(ctx, None) is None
    assert calls == []
    evs = _review_events(events)
    assert len(evs) == 1
    assert evs[0][1]["verdict"] == "UNAVAILABLE"
    assert evs[0][1]["reason"] == "ambient_git_cwd"
    ref = subprocess.run(["git", "-C", str(rig.repo), "show-ref", "--verify", "refs/bd/policy"],
                         capture_output=True, text=True)
    assert ref.returncode != 0
    assert rig.gh_calls() == []


# --------------------------------------------------------------------------- R20 / R21


class _Stop(Exception):
    pass


def _record_review_plan(monkeypatch):
    calls: list = []

    def rec(*a, **k):
        calls.append((a, k))
        raise _Stop("recorder stops the step")

    monkeypatch.setattr(p6, "_review_plan", rec)
    return calls


def _run_step(ctx):
    try:
        return p6._build_review_prompt(ctx, None)
    except _Stop:
        return None


def test_r20_real_step_refuses_before_review_plan(tmp_path, monkeypatch):
    rig = _plan_only_rig(tmp_path, monkeypatch)
    _capture(monkeypatch)
    calls = _record_review_plan(monkeypatch)
    res = _run_step(_ctx(rig))
    assert calls == [], "gate must run before _review_plan"
    assert res is not None and res.error_code == "E_READINESS_NOT_APPROVED"


def test_r21_real_step_passes_through_when_off(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch, policy={"other": 1})
    _capture(monkeypatch)
    calls = _record_review_plan(monkeypatch)
    res = _run_step(_ctx(rig))
    assert res is None  # sentinel raised from the recorder: the step reached _review_plan
    assert len(calls) == 1
    # and the gate is wired: a refusing rig must not reach the recorder (guards a stub-free pass)
    rig2_root = tmp_path / "second"
    rig2_root.mkdir()
    rig2 = _plan_only_rig(rig2_root, monkeypatch)
    res2 = _run_step(_ctx(rig2))
    assert len(calls) == 1
    assert res2 is not None and res2.error_code == "E_READINESS_NOT_APPROVED"


def test_r24_abort_handler_writes_satisfaction_stub(tmp_path, monkeypatch):
    rig = _plan_only_rig(tmp_path, monkeypatch)
    _capture(monkeypatch)
    _record_review_plan(monkeypatch)
    ctx = _ctx(rig)
    res = _run_step(ctx)
    assert res is not None, "no refusal StepResult: the step reached _review_plan (gate not built)"
    p6._on_phase_6_abort(res, ctx)
    doc = rig.root / "scratch" / "reviews" / "build-satisfaction.md"
    assert doc.is_file(), "abort handler wrote no build-satisfaction.md"
    text = doc.read_text(encoding="utf-8")
    assert "E_READINESS_NOT_APPROVED" in text
    assert "build_review_prompt" in text


# --------------------------------------------------------------------------- R22 / R23


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    assert m, f"missing section {heading}"
    rest = text[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return rest if not n else rest[:n.start()]


def test_r22_registries_name_phase_6_review():
    from bytedigger_engine import error_codes

    assert "phase_6_review" in error_codes.ERROR_CODES["E_READINESS_NOT_APPROVED"]
    for rel in ("engine_py/ERROR_CODES.md", "engine_py/bytedigger_engine/ERROR_CODES.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "phase_6_review" in _section(text, "E_READINESS"), rel


def test_r23_docs_and_changelog():
    cfg = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    m = re.search(r"^#+ .*[Rr]eadiness.*$", cfg, re.M)
    assert m, "docs/configuration.md has no readiness heading"
    rest = cfg[m.end():]
    n = re.search(r"^## ", rest, re.M)
    section = rest if not n else rest[:n.start()]
    for needle in ("review_label", "start|review|ship", "phase_6_review"):
        assert needle in section, needle
    script = (REPO_ROOT / "scripts" / "readiness").read_text(encoding="utf-8")
    assert "{start|review|ship}" in script
    from bytedigger_engine import error_codes

    assert "review label" in error_codes.ERROR_CODES["E_READINESS_NOT_APPROVED"]
    log = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = _section(log, "[Unreleased]")
    assert "bd#141 item 6" in unreleased
    assert "review_label" in unreleased
