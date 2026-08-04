"""RED tests for GH897 — input-hashed resume-sentinel key for LLM-shaped
validation-cycle steps (stale-sentinel-replay-on-red-fix class).

Spec: SHARED/memory/Decisions/2026-07-16_GH897_sentinel_input_hash_spec.md

UUT symbols (never mocked/patched -- stub-passability §1l):
  lib.step_sentinel.effective_ctx_hash (does not exist yet)
  lib.step_sentinel.maybe_read_sentinel / maybe_write_sentinel (prev= kwarg)
  lib.step_sentinel.invalidate_cycle_sentinels (glob branch)
  engine.LoopRunner.run / ._run_consuming (prev= threading)
  workflows.phase_5_implement.build_validation_loop_contract

§1q/D1CF5FDF: ``effective_ctx_hash`` does not exist on today's prod
``step_sentinel`` module. The module itself is imported at module level
(safe -- mirrors test_GH374_step_sentinel_primitive.py / test_gh750); only
the not-yet-existing attribute is accessed via getattr/hasattr inside test
bodies, so the file COLLECTS cleanly and every test FAILs at assert time,
never at collection time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.lib import step_sentinel  # noqa: E402 — module exists today; new attr accessed lazily
from bytedigger_engine.contracts import (  # noqa: E402
    LoopStepContract,
    StepContract,
    StepResult,
    WorkflowContext,
)
from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # noqa: E402


def _make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="GH897 sentinel input hash",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_effective_ctx_hash_presence_and_legacy_degrade():
    fn = getattr(step_sentinel, "effective_ctx_hash", None)
    assert fn is not None, (
        "AC1: lib.step_sentinel.effective_ctx_hash must exist -- GH897 helper "
        "not yet built on today's prod"
    )

    class _Step:
        sentinel_input_field = None

    class _StepWithField:
        sentinel_input_field = "prompt"

    class _Prev:
        def __init__(self, data):
            self.data = data

    # (i) step without a field -> passthrough
    assert fn("abc123", _Step(), _Prev({"prompt": "A"})) == "abc123"

    # (ii) field present, different values -> different 12-hex outputs
    hash_a = fn("abc123", _StepWithField(), _Prev({"prompt": "A"}))
    hash_b = fn("abc123", _StepWithField(), _Prev({"prompt": "B"}))
    assert hash_a != hash_b
    assert isinstance(hash_a, str) and len(hash_a) == 12
    assert isinstance(hash_b, str) and len(hash_b) == 12

    # (iii) same input -> deterministic same output
    hash_a2 = fn("abc123", _StepWithField(), _Prev({"prompt": "A"}))
    assert hash_a == hash_a2

    # (iv) legacy degrade: missing key / non-str / empty string / prev=None
    assert fn("abc123", _StepWithField(), _Prev({})) == "abc123"
    assert fn("abc123", _StepWithField(), _Prev({"prompt": 42})) == "abc123"
    assert fn("abc123", _StepWithField(), _Prev({"prompt": ""})) == "abc123"
    assert fn("abc123", _StepWithField(), None) == "abc123"


# ─── AC2 / AC3 shared LoopRunner harness ────────────────────────────────────


def _make_flagged_step(counts: dict, name: str = "invoke_red_llm"):
    def _exec(ctx, prev):
        counts["calls"] = counts.get("calls", 0) + 1
        return StepResult(status="ok", data={"raw_response": "live"}, duration_ms=0, step_name=name)

    return StepContract(name=name, execute=_exec, resume_sentinel=True, sentinel_input_field="prompt")


def _run_base_loop_with_prompt(tmp_path, prompt: str, counts: dict, run_id: str, iteration_prev_field="prompt"):
    from bytedigger_engine import telemetry_ctx
    from bytedigger_engine.engine import LoopRunner
    from bytedigger_engine.event_log import EventLog

    ctx = _make_ctx(tmp_path)
    event_log = EventLog(tmp_path / "events.jsonl")
    body_step = _make_flagged_step(counts)
    contract = LoopStepContract(name="wf_ac2", body=[body_step], max_iterations=1)

    telemetry_ctx.set_current_run(
        event_log=event_log, run_id=run_id, step_name="loop_wrapper", cycle=1,
    )
    try:
        prev = StepResult(status="ok", data={iteration_prev_field: prompt}, duration_ms=0, step_name="build_prompt")
        result = LoopRunner(contract).run(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()
    return result


def test_ac2_base_loop_same_prompt_second_run_cache_hit():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        counts: dict = {}
        run_id = "runAC2"
        _run_base_loop_with_prompt(tmp_path, "same prompt", counts, run_id)
        _run_base_loop_with_prompt(tmp_path, "same prompt", counts, run_id)
        assert counts.get("calls", 0) == 1, (
            "AC2: second run with the SAME prompt+run_id/iteration must be served "
            f"from the input-hashed cache, not re-executed; got {counts.get('calls', 0)} calls"
        )


def test_ac3_base_loop_changed_prompt_second_run_reexecutes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        counts: dict = {}
        run_id = "runAC3"
        _run_base_loop_with_prompt(tmp_path, "prompt A", counts, run_id)
        _run_base_loop_with_prompt(tmp_path, "prompt B (red-fix)", counts, run_id)
        assert counts.get("calls", 0) == 2, (
            "AC3: a changed prompt (red-fix) between runs of the same run_id/iteration "
            f"must re-execute (fresh cache miss), got {counts.get('calls', 0)} calls"
        )


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_build_validation_loop_contract_marks_only_two_llm_steps():
    from bytedigger_engine.workflows.phase_5_implement import build_validation_loop_contract

    contract = build_validation_loop_contract()
    by_name = {s.name: s for s in contract.body}
    assert "invoke_red_llm" in by_name and "invoke_validation_llm" in by_name

    for target in ("invoke_red_llm", "invoke_validation_llm"):
        field = getattr(by_name[target], "sentinel_input_field", None)
        assert field == "sentinel_input", (
            f"AC4 (r2): {target}.sentinel_input_field must equal 'sentinel_input', got {field!r}"
        )

    others = [name for name in by_name if name not in ("invoke_red_llm", "invoke_validation_llm")]
    assert len(others) == 10, f"expected 10 other body steps, got {len(others)}: {others}"
    for name in others:
        field = getattr(by_name[name], "sentinel_input_field", None)
        assert field is None, f"AC4: {name}.sentinel_input_field must stay None, got {field!r}"


# ─── AC5 (r2) — real producer, content-digest, #897 integration repro ───────


def test_ac5_part1_build_validation_prompt_sentinel_input_reacts_to_file_bytes(tmp_path):
    """r2 MAJOR-1: the real _build_validation_prompt producer must emit
    data['sentinel_input'] that changes on an in-place red-fix (same paths,
    changed bytes), even though data['prompt'] (built from paths + static
    text) stays byte-identical."""
    from bytedigger_engine.workflows.phase_5_implement import _build_validation_prompt

    scratchpad = tmp_path
    red_test_file = scratchpad / "test_red.py"
    red_test_file.write_text("def test_x(): assert True\n")
    spec_file = scratchpad / "spec.md"
    spec_file.write_text("# spec v1\n")

    ctx = _make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "red_log.md"),
            "spec_path": str(spec_file),
            "cycle": 1,
            "red_test_paths": [str(red_test_file)],
        },
        duration_ms=0,
        step_name="verify_red_lint_rules",
    )

    result1 = _build_validation_prompt(ctx, prev)
    sentinel_input_1 = result1.data.get("sentinel_input") if isinstance(result1.data, dict) else None

    # In-place red-fix: same paths, different bytes.
    red_test_file.write_text("def test_x(): assert True  # red-fix\n")

    result2 = _build_validation_prompt(ctx, prev)
    sentinel_input_2 = result2.data.get("sentinel_input") if isinstance(result2.data, dict) else None

    assert sentinel_input_1 is not None, "AC5 r2: _build_validation_prompt must emit data['sentinel_input']"
    assert sentinel_input_2 is not None
    assert sentinel_input_1 != sentinel_input_2, (
        "AC5 r2 (MAJOR-1): an in-place red-fix (same paths, changed bytes) must "
        "change sentinel_input even though the path+static-text prompt is unaffected"
    )
    assert result1.data.get("prompt") == result2.data.get("prompt"), (
        "sanity: the prompt built from paths+static text stays byte-identical across the red-fix"
    )


def test_ac5_part2_e2e_loop_reddrive_after_ondisk_redfix_forces_fresh_validation(tmp_path):
    """End-to-end #897 repro: real _build_validation_prompt feeds a fake
    invoke_validation_llm whose verdict is derived from the same on-disk red
    test file. First loop run -> FAIL (cached). On-disk red-fix (same path,
    changed bytes). Re-drive with the SAME run_id -> the fake validation step
    must be invoked AGAIN (not replayed) and this time return PASS."""
    from bytedigger_engine import telemetry_ctx
    from bytedigger_engine.engine import LoopRunner
    from bytedigger_engine.event_log import EventLog
    from bytedigger_engine.workflows.phase_5_implement import _build_validation_prompt

    scratchpad = tmp_path
    red_test_file = scratchpad / "test_red.py"
    red_test_file.write_text("def test_x(): assert False  # pre-fix\n")
    spec_file = scratchpad / "spec.md"
    spec_file.write_text("# spec v1\n")

    ctx = _make_ctx(scratchpad)
    run_id = "runAC5e2e"
    val_calls: list = []

    def _fake_invoke_validation(ctx, prev):
        sentinel_input = prev.data.get("sentinel_input") if isinstance(prev.data, dict) else None
        val_calls.append(sentinel_input)
        verdict = "PASS" if "red-fix" in red_test_file.read_text() else "FAIL"
        return StepResult(status="ok", data={"verdict": verdict}, duration_ms=0, step_name="invoke_validation_llm")

    build_prompt_step = StepContract(name="build_validation_prompt", execute=_build_validation_prompt)
    invoke_step = StepContract(
        name="invoke_validation_llm", execute=_fake_invoke_validation,
        resume_sentinel=True, sentinel_input_field="sentinel_input",
    )

    def _run_once():
        event_log = EventLog(scratchpad / "events.jsonl")
        telemetry_ctx.set_current_run(event_log=event_log, run_id=run_id, step_name="phase_5_validation_cycle", cycle=1)
        try:
            prev = StepResult(
                status="ok",
                data={
                    "red_log_path": str(scratchpad / "red_log.md"),
                    "spec_path": str(spec_file),
                    "cycle": 1,
                    "red_test_paths": [str(red_test_file)],
                },
                duration_ms=0,
                step_name="check_red_executable",
            )
            contract = LoopStepContract(
                name="phase_5_validation_cycle", body=[build_prompt_step, invoke_step], max_iterations=1,
                until_marker="PASS", marker_field="verdict",
            )
            return LoopRunner(contract).run(ctx, prev)
        finally:
            telemetry_ctx.clear_current_run()

    result1 = _run_once()
    assert result1.data.get("verdict") == "FAIL"
    assert len(val_calls) == 1

    # On-disk red-fix: same path, changed bytes.
    red_test_file.write_text("def test_x(): assert True  # red-fix\n")

    result2 = _run_once()
    assert len(val_calls) == 2, (
        "AC5 r2 part2 (#897 repro): an on-disk red-fix must force a FRESH "
        f"invoke_validation_llm call via a changed sentinel_input, not a stale replay; "
        f"got {len(val_calls)} total calls"
    )
    assert result2.data.get("verdict") == "PASS"
    assert result2.metadata.get("terminated_by") == "marker"


# ─── AC5b ───────────────────────────────────────────────────────────────────


def test_ac5b_verdict_content_digest_deterministic_byte_sensitive_order_normalized(tmp_path):
    from bytedigger_engine.workflows import phase_5_implement as p5

    fn = getattr(p5, "_verdict_content_digest", None)
    assert fn is not None, (
        "AC5b: workflows.phase_5_implement._verdict_content_digest must exist -- "
        "GH897 r2 content-digest helper not yet built on today's prod"
    )

    f1 = tmp_path / "a.py"
    f1.write_text("alpha")
    f2 = tmp_path / "b.py"
    f2.write_text("beta")
    missing = tmp_path / "missing.py"

    d1 = fn([str(f1), str(f2)])
    d2 = fn([str(f1), str(f2)])
    assert d1 == d2, "AC5b: deterministic -- same inputs must yield the same digest"

    f1.write_text("alpha-changed")
    d3 = fn([str(f1), str(f2)])
    assert d3 != d1, "AC5b: byte-sensitive -- changed file content must change the digest"

    # missing file -> empty bytes, no exception
    d_missing = fn([str(missing)])
    assert isinstance(d_missing, str)

    # path order normalized -> permutation-invariant digest
    d_order_a = fn([str(f1), str(f2)])
    d_order_b = fn([str(f2), str(f1)])
    assert d_order_a == d_order_b, "AC5b: path order must be normalized (sorted) before hashing"


# ─── AC5c ───────────────────────────────────────────────────────────────────


def test_ac5c_build_red_prompt_sentinel_input_reacts_to_validation_doc_bytes(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt

    scratchpad = tmp_path
    (scratchpad / "specs").mkdir(parents=True)
    (scratchpad / "specs" / "build-spec.md").write_text("# spec\n")
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True)
    validation_doc = reviews_dir / "build-opus-validation.md"
    validation_doc.write_text("findings v1")

    ctx = _make_ctx(scratchpad)

    result1 = _build_red_prompt(ctx, None)
    sentinel_input_1 = result1.data.get("sentinel_input") if isinstance(result1.data, dict) else None

    validation_doc.write_text("findings v2 (revised)")
    result2 = _build_red_prompt(ctx, None)
    sentinel_input_2 = result2.data.get("sentinel_input") if isinstance(result2.data, dict) else None

    assert sentinel_input_1 is not None, "AC5c: _build_red_prompt must emit data['sentinel_input']"
    assert sentinel_input_2 is not None
    assert sentinel_input_1 != sentinel_input_2, (
        "AC5c: changing reviews/build-opus-validation.md bytes between cycles "
        "must change sentinel_input even though findings/cycle inputs are unchanged"
    )


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_unflagged_field_step_keeps_byte_identical_sentinel_filename(tmp_path):
    """A step with sentinel_input_field left unset must produce the exact
    same sentinel filename as before this ship (backward-compat with live
    resume files)."""
    from bytedigger_engine import telemetry_ctx
    from bytedigger_engine.engine import LoopRunner
    from bytedigger_engine.event_log import EventLog
    from bytedigger_engine.lib.step_sentinel import compute_ctx_hash

    ctx = _make_ctx(tmp_path)
    event_log = EventLog(tmp_path / "events.jsonl")
    run_id = "runAC6"
    ctx_hash = compute_ctx_hash(ctx)

    counts: dict = {}

    def _exec(ctx, prev):
        counts["calls"] = counts.get("calls", 0) + 1
        return StepResult(status="ok", data={"raw_response": "live"}, duration_ms=0, step_name="invoke_red_llm")

    # No sentinel_input_field set -- legacy step, exactly as prod today.
    body_step = StepContract(name="invoke_red_llm", execute=_exec, resume_sentinel=True)
    contract = LoopStepContract(name="wf_ac6", body=[body_step], max_iterations=1)

    telemetry_ctx.set_current_run(event_log=event_log, run_id=run_id, step_name="loop_wrapper", cycle=1)
    try:
        LoopRunner(contract).run(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    expected_name = resume_sentinel_name("invoke_red_llm", 1, run_id, ctx_hash, workflow_name="wf_ac6")
    sentinel_path = tmp_path / "resume" / expected_name
    assert sentinel_path.exists(), (
        f"AC6: unflagged-field step must keep the pre-GH897 sentinel filename; "
        f"expected {expected_name} to exist"
    )


# ─── AC7 / AC8 ───────────────────────────────────────────────────────────────


def test_ac7_invalidate_cycle_sentinels_glob_removes_input_hashed_variants(tmp_path):
    fn = getattr(step_sentinel, "invalidate_cycle_sentinels", None)
    assert fn is not None

    scratch = tmp_path / "scratch"
    resume_dir = scratch / "resume"
    resume_dir.mkdir(parents=True)

    step = StepContract(name="invoke_validation_llm", execute=lambda ctx, prev: prev, resume_sentinel=True, sentinel_input_field="prompt")
    run_id = "R1"
    ctx = _make_ctx(scratch)

    # Two input-hashed variants of the SAME (step, cycle, run_id) -- distinct
    # hex suffixes standing in for two different observed prompt hashes.
    hashed_1 = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, run_id, "aaaaaaaaaaaa", "wf_ac7")
    hashed_2 = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, run_id, "bbbbbbbbbbbb", "wf_ac7")
    unrelated_cycle = resume_dir / resume_sentinel_name("invoke_validation_llm", 1, run_id, "aaaaaaaaaaaa", "wf_ac7")
    unrelated_run = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, "R2", "aaaaaaaaaaaa", "wf_ac7")

    for p in (hashed_1, hashed_2, unrelated_cycle, unrelated_run):
        p.write_text(json.dumps({"marker": p.name}))

    removed = fn(ctx, [step], 2, run_id, workflow_name="wf_ac7")

    assert not hashed_1.exists(), "AC7: first input-hashed variant must be removed by glob"
    assert not hashed_2.exists(), "AC7: second input-hashed variant must be removed by glob"
    assert set(removed) >= {hashed_1.name, hashed_2.name}, f"removed list missing entries: {removed}"


def test_ac8_glob_invalidation_does_not_touch_neighbours(tmp_path):
    fn = getattr(step_sentinel, "invalidate_cycle_sentinels", None)
    assert fn is not None

    scratch = tmp_path / "scratch"
    resume_dir = scratch / "resume"
    resume_dir.mkdir(parents=True)

    step = StepContract(name="invoke_validation_llm", execute=lambda ctx, prev: prev, resume_sentinel=True, sentinel_input_field="prompt")
    run_id = "R1"
    ctx = _make_ctx(scratch)

    target = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, run_id, "aaaaaaaaaaaa", "wf_ac8")
    other_step = resume_dir / resume_sentinel_name("invoke_red_llm", 2, run_id, "aaaaaaaaaaaa", "wf_ac8")
    other_run = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, "R9", "aaaaaaaaaaaa", "wf_ac8")
    other_cycle = resume_dir / resume_sentinel_name("invoke_validation_llm", 5, run_id, "aaaaaaaaaaaa", "wf_ac8")

    for p in (target, other_step, other_run, other_cycle):
        p.write_text(json.dumps({"marker": p.name}))

    fn(ctx, [step], 2, run_id, workflow_name="wf_ac8")

    assert not target.exists()
    assert other_step.exists(), "AC8: different step_name sentinel must survive"
    assert other_run.exists(), "AC8: different run_id sentinel must survive"
    assert other_cycle.exists(), "AC8: different cycle sentinel must survive"


def test_ac8_run_id_prefix_collision_r1_vs_r12_survives(tmp_path):
    """r2 MINOR-2: invalidating run_id='R1' must NOT sweep up a sentinel
    keyed to run_id='R12' -- the glob must anchor on '_h' immediately after
    the run_id, not merely prefix-match the run_id segment."""
    fn = getattr(step_sentinel, "invalidate_cycle_sentinels", None)
    assert fn is not None

    scratch = tmp_path / "scratch"
    resume_dir = scratch / "resume"
    resume_dir.mkdir(parents=True)

    step = StepContract(name="invoke_validation_llm", execute=lambda ctx, prev: prev, resume_sentinel=True, sentinel_input_field="prompt")
    ctx = _make_ctx(scratch)

    target_r1 = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, "R1", "aaaaaaaaaaaa", "wf_ac8b")
    collider_r12 = resume_dir / resume_sentinel_name("invoke_validation_llm", 2, "R12", "aaaaaaaaaaaa", "wf_ac8b")

    for p in (target_r1, collider_r12):
        p.write_text(json.dumps({"marker": p.name}))

    fn(ctx, [step], 2, "R1", workflow_name="wf_ac8b")

    assert not target_r1.exists(), "AC8: run_id='R1' target must be removed"
    assert collider_r12.exists(), (
        "AC8 (r2 MINOR-2): run_id='R12' must survive invalidation of run_id='R1' "
        "-- glob must not over-match on the run_id prefix"
    )


# ─── AC9 ────────────────────────────────────────────────────────────────────


def _run_consuming_with_prompt(tmp_path, prompt: str, counts: dict, run_id: str):
    """One top-level LoopRunner._run_consuming pass (consume_recoverable_retry=True,
    single flagged body step, max_iterations=1) fed a prev carrying ``prompt``."""
    from bytedigger_engine import telemetry_ctx
    from bytedigger_engine.engine import LoopRunner
    from bytedigger_engine.event_log import EventLog

    ctx = _make_ctx(tmp_path)
    event_log = EventLog(tmp_path / "events.jsonl")

    def _flagged_exec(ctx, prev):
        counts["calls"] = counts.get("calls", 0) + 1
        return StepResult(status="ok", data={"raw_response": "live", "verdict": "PASS"}, duration_ms=0, step_name="invoke_red_llm")

    flagged_step = StepContract(name="invoke_red_llm", execute=_flagged_exec, resume_sentinel=True, sentinel_input_field="prompt")
    contract = LoopStepContract(
        name="wf_ac9", body=[flagged_step], max_iterations=1,
        until_marker="PASS", marker_field="verdict", consume_recoverable_retry=True,
    )

    telemetry_ctx.set_current_run(event_log=event_log, run_id=run_id, step_name="loop_wrapper", cycle=1)
    try:
        prev = StepResult(status="ok", data={"prompt": prompt}, duration_ms=0, step_name="build_prompt")
        LoopRunner(contract).run(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()


def test_ac9_run_consuming_path_same_prompt_cache_hit_changed_prompt_fresh():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        counts: dict = {}
        run_id = "runAC9"
        # same sentinel_iteration slot (both first pass of a fresh LoopRunner
        # call), same prompt -> second call must be a cache hit.
        _run_consuming_with_prompt(tmp_path, "p1", counts, run_id)
        _run_consuming_with_prompt(tmp_path, "p1", counts, run_id)
        assert counts.get("calls", 0) == 1, (
            "AC9: _run_consuming path -- unchanged prompt in the same "
            f"sentinel_iteration slot must be a cache hit, got {counts.get('calls', 0)} calls"
        )

        counts2: dict = {}
        run_id2 = "runAC9b"
        _run_consuming_with_prompt(tmp_path, "p1", counts2, run_id2)
        _run_consuming_with_prompt(tmp_path, "p2 (red-fix)", counts2, run_id2)
        assert counts2.get("calls", 0) == 2, (
            "AC9: _run_consuming path -- a changed prompt in the same "
            f"sentinel_iteration slot must force a fresh execute, got {counts2.get('calls', 0)} calls"
        )
