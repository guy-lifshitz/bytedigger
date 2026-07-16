"""RED tests for BC45C403 — phase_6 step-sentinel durable resume.

All 8 tests MUST FAIL at RED time because _write_step_sentinel and
_read_step_sentinel do not yet exist in phase_6_review.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import WorkflowContext, StepResult  # these exist


# ─── helpers ─────────────────────────────────────────────────────────────────

_LIB_PATH = str(ENGINE_ROOT / "lib")


def _ensure_lib_path() -> None:
    """Insert engine_py/lib/ into sys.path inside a test body."""
    if _LIB_PATH not in sys.path:
        sys.path.insert(0, _LIB_PATH)


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="test feature",
        session_id="test-BC45C403",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_review_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult for _invoke_review_llm (build_review_prompt output)."""
    scratch = tmp_path / "scratch"
    return StepResult(
        status="ok",
        data={
            "doc_path": str(scratch / "reviews" / "build-review.md"),
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/red.log",
            "green_log_path": "/tmp/green.log",
            "prompt": "review this code",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )


def _make_fix_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult for _invoke_fix_llm (build_fix_prompt output)."""
    scratch = tmp_path / "scratch"
    return StepResult(
        status="ok",
        data={
            "log_path": str(scratch / "fix.log"),
            "spec_path": "/tmp/spec.md",
            "review_doc_path": str(scratch / "reviews" / "build-review.md"),
            "verdict": "FAIL",
            "prompt": "fix the issues",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="build_fix_prompt",
    )


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_write_step_sentinel_importable():
    """AC1: write_step_sentinel must be importable from lib step_sentinel."""
    from step_sentinel import write_step_sentinel  # noqa: F401
    assert callable(write_step_sentinel), "write_step_sentinel must be callable"


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_read_step_sentinel_importable():
    """AC2: read_step_sentinel must be importable from lib step_sentinel."""
    from step_sentinel import read_step_sentinel  # noqa: F401
    assert callable(read_step_sentinel), "read_step_sentinel must be callable"


# ─── AC7 ──────────────────────────────────────────────────────────────────────


def test_ac7_sentinel_path_is_resume_subdir(tmp_path):
    """AC7: sentinel file path is {scratchpad}/resume/<resume_sentinel_name(...)> (run_id-keyed)."""
    from step_sentinel import write_step_sentinel, read_step_sentinel  # noqa: PLC0415
    from resume_keying import resume_sentinel_name  # noqa: PLC0415

    write_step_sentinel(tmp_path, "invoke_review_llm", 1, {"x": 1}, "runZ")
    sentinel_file = tmp_path / "resume" / resume_sentinel_name("invoke_review_llm", 1, "runZ")
    assert sentinel_file.exists(), f"sentinel must be written to {sentinel_file}"
    data = read_step_sentinel(tmp_path, "invoke_review_llm", 1, "runZ")
    assert data == {"x": 1}, f"read_step_sentinel must return the written data; got {data!r}"


# ─── AC8 ──────────────────────────────────────────────────────────────────────


def test_ac8_read_step_sentinel_returns_none_on_missing_and_corrupt(tmp_path):
    """AC8: read_step_sentinel returns None when file is missing or JSON corrupt."""
    from step_sentinel import read_step_sentinel  # noqa: PLC0415
    from resume_keying import resume_sentinel_name  # noqa: PLC0415

    # Missing file
    result = read_step_sentinel(tmp_path, "invoke_review_llm", 99, "runZ")
    assert result is None, f"missing file must yield None; got {result!r}"

    # Corrupt JSON — write at the run_id-keyed name so the read actually targets it
    corrupt_dir = tmp_path / "resume"
    corrupt_dir.mkdir(parents=True, exist_ok=True)
    (corrupt_dir / resume_sentinel_name("invoke_review_llm", 1, "runZ")).write_text("{invalid json")
    result = read_step_sentinel(tmp_path, "invoke_review_llm", 1, "runZ")
    assert result is None, f"corrupt JSON must yield None; got {result!r}"


# ─── 7E274B85: run_id-keyed sentinel (cross-run replay fix) ───────────────────


def test_7e274b85_sentinel_name_differs_across_run_ids():
    """AC1: sentinel names for 'aaaa' vs 'bbbb' differ; each contains its run_id.

    Pre-fix forcing reason: lib/resume_keying.py does not exist.
    ImportError raised at the `from resume_keying import …` call inside this
    function → test FAILS (not at collection — §1q-ext).
    """
    _ensure_lib_path()
    from resume_keying import resume_sentinel_name  # noqa: PLC0415

    name_a = resume_sentinel_name("invoke_review_llm", 1, "aaaa")
    name_b = resume_sentinel_name("invoke_review_llm", 1, "bbbb")

    assert name_a != name_b, (
        f"sentinel names for different run_ids must differ; got {name_a!r} == {name_b!r}"
    )
    assert "aaaa" in name_a, (
        f"run_id 'aaaa' must appear as a substring in name_a; got {name_a!r}"
    )
    assert "bbbb" in name_b, (
        f"run_id 'bbbb' must appear as a substring in name_b; got {name_b!r}"
    )


def test_7e274b85_cross_run_replay_returns_none(tmp_path: Path):
    """AC2 (TRUE RED): write sentinel with runA, read with runB → returns None.

    This is the stale-replay bug. The CURRENT helpers have NO run_id parameter:
      _write_step_sentinel(scratchpad, step_name, cycle, data)
      _read_step_sentinel(scratchpad, step_name, cycle)
    Calling them with a 5th / 4th positional arg (run_id) raises TypeError.
    → test FAILS at the first call-site inside this function.

    Post-fix: different run_id → different filename → read returns None.

    §1i (singleton-resource): no singleton contention — tmp_path is unique per
    test; pre-staged empty (pytest guarantees it). No timing dependency.
    """
    from step_sentinel import write_step_sentinel, read_step_sentinel  # noqa: PLC0415

    scratchpad = tmp_path  # helpers write under scratchpad/"resume"/ internally

    # Write sentinel for runA
    write_step_sentinel(
        scratchpad, "invoke_review_llm", 1, {"x": 1}, "runA"
    )

    # Read with a different run_id — must NOT find runA's sentinel
    result = read_step_sentinel(
        scratchpad, "invoke_review_llm", 1, "runB"
    )
    assert result is None, (
        f"reading sentinel with a different run_id must return None (no cross-run "
        f"replay); got {result!r}"
    )


def test_7e274b85_same_run_id_round_trips(tmp_path: Path):
    """AC3: write with runA then read with runA returns the original data.

    Ensures that folding run_id into the key does NOT break same-run resume.
    Pre-fix: TypeError on the extra positional arg → FAILS.
    Post-fix: same-run sentinel found → returns {"x": 1}.

    §1i: no singleton contention — tmp_path unique and pre-staged empty.
    """
    from step_sentinel import write_step_sentinel, read_step_sentinel  # noqa: PLC0415

    scratchpad = tmp_path

    write_step_sentinel(
        scratchpad, "invoke_review_llm", 1, {"x": 1}, "runA"
    )

    result = read_step_sentinel(
        scratchpad, "invoke_review_llm", 1, "runA"
    )
    assert result == {"x": 1}, (
        f"same run_id read must return the written data; got {result!r}"
    )


def test_7e274b85_none_run_id_contains_norun():
    """AC4: resume_sentinel_name(…, None) contains 'norun' and does not raise.

    Defensive fallback for the edge case where run_id is unavailable.
    Pre-fix: lib/resume_keying.py absent → ImportError at the in-body import.
    Post-fix: returns a filename containing 'norun'.
    """
    _ensure_lib_path()
    from resume_keying import resume_sentinel_name  # noqa: PLC0415

    name = resume_sentinel_name("s", 2, None)
    assert "norun" in name, (
        f"None run_id must produce a filename containing 'norun'; got {name!r}"
    )


# ─── GH752: workflow/phase-namespace in the resume sentinel key ─────────────


def test_gh752_ac1_key_builder_namespaces_by_workflow_name():
    """GH752-AC1: two workflows sharing the same step name produce distinct
    sentinel keys once workflow_name is folded in, each key containing its
    own workflow_name as a substring.

    Pre-fix forcing reason: `resume_sentinel_name` has no `workflow_name`
    kwarg → TypeError at the call below → FAIL.
    """
    _ensure_lib_path()
    from resume_keying import resume_sentinel_name  # noqa: PLC0415

    name_spec = resume_sentinel_name(
        "invoke_review_llm", 1, "r1", workflow_name="phase_45_spec"
    )
    name_review = resume_sentinel_name(
        "invoke_review_llm", 1, "r1", workflow_name="phase_6_review"
    )

    assert name_spec != name_review, (
        f"cross-workflow same-step keys must differ; got {name_spec!r} == {name_review!r}"
    )
    assert "phase_45_spec" in name_spec, (
        f"workflow_name 'phase_45_spec' must appear in the key; got {name_spec!r}"
    )
    assert "phase_6_review" in name_review, (
        f"workflow_name 'phase_6_review' must appear in the key; got {name_review!r}"
    )


def test_gh752_ac2_no_workflow_name_preserves_legacy_filename():
    """GH752-AC2: back-compat — omitting workflow_name reproduces the exact
    legacy filename. This is a regression guard and may legitimately PASS
    both pre- and post-fix.
    """
    _ensure_lib_path()
    from resume_keying import resume_sentinel_name  # noqa: PLC0415

    name = resume_sentinel_name("invoke_review_llm", 1, "r1")
    assert name == "invoke_review_llm_done_c1_rr1.json", (
        f"legacy filename must be byte-preserved when workflow_name is absent; got {name!r}"
    )


def test_gh752_ac3_namespaced_round_trip_and_cross_workflow_no_replay(tmp_path: Path):
    """GH752-AC3: same-namespace write/read round-trips; cross-workflow read
    (different workflow_name) returns None — the helper-layer cal8b fix.

    Pre-fix forcing reason: write_step_sentinel/read_step_sentinel have no
    workflow_name kwarg → TypeError at the first call below → FAIL.
    """
    from step_sentinel import write_step_sentinel, read_step_sentinel  # noqa: PLC0415

    write_step_sentinel(
        tmp_path, "invoke_review_llm", 1, {"x": 1}, "r1", workflow_name="phase_6_review"
    )

    same_ns = read_step_sentinel(
        tmp_path, "invoke_review_llm", 1, "r1", workflow_name="phase_6_review"
    )
    assert same_ns == {"x": 1}, (
        f"same-namespace read must return the written data; got {same_ns!r}"
    )

    cross_ns = read_step_sentinel(
        tmp_path, "invoke_review_llm", 1, "r1", workflow_name="phase_45_spec"
    )
    assert cross_ns is None, (
        f"cross-workflow read must NOT replay a different workflow's sentinel; got {cross_ns!r}"
    )


def test_gh752_ac4_execute_steps_threads_workflow_name_into_sentinel(tmp_path: Path):
    """GH752-AC4: a top-level flagged step run through WorkflowEngine.execute
    writes its sentinel under a filename prefixed `{workflow.name}__` —
    proof `_execute_steps` threads `workflow.name` (engine.py:361).

    §1y: Point=engine.py:361, Host=`_execute_steps` (runs via
    WorkflowEngine.execute), Test-path=a flagged top-level step +
    resolvable scratchpad_dir make the write reachable.

    Pre-fix forcing reason: maybe_write_sentinel/resume_sentinel_name are
    not called with workflow_name → the written filename has no
    'phase_6_review_stub__' prefix → the startswith assertion FAILS.
    """
    from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition  # noqa: PLC0415
    from engine import WorkflowEngine  # noqa: PLC0415
    from event_log import EventLog  # noqa: PLC0415

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)

    def _execute(ctx, prev):
        return StepResult(status="ok", data={"raw_response": "live"}, duration_ms=0,
                          step_name="invoke_review_llm")

    workflow = WorkflowDefinition(
        name="phase_6_review_stub",
        steps=[StepContract(name="invoke_review_llm", execute=_execute, resume_sentinel=True)],
    )
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="GH752 AC4",
        session_id="test-gh752-ac4",
        persona="hal",
        framework=None,
        domain=None,
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_6_review_stub", workflow)
    eng.execute("phase_6_review_stub", ctx, run_id="run-gh752-ac4")

    resume_dir = scratch / "resume"
    assert resume_dir.exists(), f"expected resume dir at {resume_dir}"
    written = list(resume_dir.glob("*.json"))
    assert any(f.name.startswith("phase_6_review_stub__") for f in written), (
        f"expected a sentinel file prefixed 'phase_6_review_stub__'; found {[f.name for f in written]}"
    )


def test_gh752_ac5_loop_runner_threads_loop_contract_name_into_sentinel(tmp_path: Path):
    """GH752-AC5: a loop-body flagged step run through LoopRunner.run writes
    its sentinel under a filename prefixed `{c.name}__` (the LoopStepContract's
    OWN name, NOT workflow.name — unavailable inside LoopRunner, §4).

    §1y: Point=engine.py:634, Host=`LoopRunner.run`, Test-path=a flagged
    body step + an active run-ctx + resolvable scratchpad_dir make the
    write reachable.

    Pre-fix forcing reason: the written filename has no 'rev_loop__' prefix
    → the startswith assertion FAILS.
    """
    import telemetry_ctx  # noqa: PLC0415
    from contracts import LoopStepContract, StepContract, StepResult, WorkflowContext  # noqa: PLC0415
    from engine import LoopRunner  # noqa: PLC0415
    from event_log import EventLog  # noqa: PLC0415

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="GH752 AC5",
        session_id="test-gh752-ac5",
        persona="hal",
        framework=None,
        domain=None,
    )

    def _execute(ctx, prev):
        return StepResult(status="ok", data={"raw_response": "live"}, duration_ms=0,
                          step_name="invoke_red_llm")

    body_step = StepContract(name="invoke_red_llm", execute=_execute, resume_sentinel=True)
    contract = LoopStepContract(name="rev_loop", body=[body_step], max_iterations=1)

    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="run-gh752-ac5", step_name="rev_loop", cycle=1,
    )
    try:
        result = LoopRunner(contract).run(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    resume_dir = scratch / "resume"
    assert resume_dir.exists(), f"expected resume dir at {resume_dir}"
    written = list(resume_dir.glob("*.json"))
    assert any(f.name.startswith("rev_loop__") for f in written), (
        f"expected a sentinel file prefixed 'rev_loop__'; found {[f.name for f in written]}"
    )




# ─── GH752×GH750 cross-PR bug (§2.5) — invalidate must target the namespaced key ──


def test_gh752_ac6_invalidate_removes_namespaced_sentinel(tmp_path):
    """AC6 (§2.5): invalidate_cycle_sentinels must accept workflow_name and
    unlink the NAMESPACED sentinel that maybe_write_sentinel/write_step_sentinel
    now produce. Pre-fix (invalidate lacks the workflow_name kwarg / does not
    thread it) the call either raises TypeError (kwarg absent) or computes the
    non-namespaced name and misses the on-disk namespaced file -> the poisoned
    sentinel survives a same-cycle retry (§1ac recursion-cap nullified).
    """
    _ensure_lib_path()
    from step_sentinel import write_step_sentinel, invalidate_cycle_sentinels
    from contracts import StepContract

    ctx = _make_ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    wf = "phase_6_review"

    # write a namespaced sentinel exactly as the engine does post-#757
    write_step_sentinel(
        scratch, "invoke_review_llm", 1, {"x": 1}, "r1", workflow_name=wf,
    )
    sentinel = scratch / "resume" / "phase_6_review__invoke_review_llm_done_c1_rr1.json"
    assert sentinel.exists(), f"precondition: namespaced sentinel must exist, listing={[p.name for p in (scratch/'resume').glob('*.json')]}"

    step = StepContract(
        name="invoke_review_llm",
        execute=lambda ctx, prev: StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_review_llm"),
        resume_sentinel=True,
    )

    removed = invalidate_cycle_sentinels(ctx, [step], 1, "r1", None, workflow_name=wf)

    assert not sentinel.exists(), (
        "namespaced sentinel MUST be unlinked by invalidate_cycle_sentinels(workflow_name=...) — "
        f"still on disk; removed={removed}"
    )
    assert "phase_6_review__invoke_review_llm_done_c1_rr1.json" in removed, (
        f"expected the namespaced filename in the removed list, got {removed}"
    )


def test_gh752_ac7_invalidate_no_workflow_name_is_legacy(tmp_path):
    """AC7 (§2.5 back-compat): invalidate_cycle_sentinels with no workflow_name
    removes the LEGACY (non-namespaced) sentinel byte-for-byte as before — the
    GH750 helper-layer callers that pass legacy-written sentinels are unaffected.
    """
    _ensure_lib_path()
    from step_sentinel import write_step_sentinel, invalidate_cycle_sentinels
    from contracts import StepContract

    ctx = _make_ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])

    write_step_sentinel(scratch, "invoke_review_llm", 1, {"x": 1}, "r1")
    legacy = scratch / "resume" / "invoke_review_llm_done_c1_rr1.json"
    assert legacy.exists(), "precondition: legacy sentinel must exist"

    step = StepContract(
        name="invoke_review_llm",
        execute=lambda ctx, prev: StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_review_llm"),
        resume_sentinel=True,
    )
    removed = invalidate_cycle_sentinels(ctx, [step], 1, "r1")
    assert not legacy.exists(), f"legacy sentinel must be unlinked; removed={removed}"
    assert "invoke_review_llm_done_c1_rr1.json" in removed
