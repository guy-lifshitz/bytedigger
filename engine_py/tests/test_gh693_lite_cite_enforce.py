"""Regression-guard tests for GH693 — lite-lane AC for spec cite pre-lint
ENFORCE path through the REAL composite (pre-flip, HAL_SPEC_CITE_PRELINT_ENFORCE
default OFF).

Spec: SHARED/memory/Decisions/2026-07-12_GH693_lite_cite_enforce_ac_spec.md

Mode: characterization / regression guard (spec §0/§2). The intersection of
#688 (GH682 lite port), #691 (GH681 enforce branch), and #671/#641
(consume_recoverable_retry) is BELIEVED correct today but has never been
driven together through the real composite. These tests assert the
believed-correct behavior strictly per spec §3 — they may PASS today (that
is expected and fine per the task brief); if any FAILs it reveals a real
seam defect at the GREEN escalation boundary.

UUT (never mocked/patched, §1l): `phase_45_spec._verify_spec_cite_prelint`,
`RecoverableGateMixin.gated_step_result` (invoked transitively),
`engine.LoopRunner._run_consuming`, `phase_45_spec_lite.build_review_loop_contract`.
Only the ENFORCE env flag, the on-disk spec/helpers fixture, ctx/prev data,
and a trivial marker/terminal body step are controlled (spec §2.1).

conftest.py registers the engine_py root + workflows dir on `sys.path` at
import time (singleton pattern) — no module-level `sys.path` mutation here.
"""
from __future__ import annotations

from collections import Counter

from bytedigger_engine.workflows import phase_45_spec
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import LoopStepContract, StepContract, StepResult, WorkflowContext
from bytedigger_engine.engine import LoopRunner
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.workflows.phase_45_spec_lite import MAX_REVIEW_CYCLES, VERDICT_SHIP, build_review_loop_contract


# ─── Harness (spec §2.1 — copied from siblings) ─────────────────────────────


def _dirty_fixture(root):
    """Repo root + spec file with one resolvable symbol and one bogus symbol."""
    root.mkdir(parents=True, exist_ok=True)
    code_file = root / "helpers.py"
    code_file.write_text("def real_helper():\n    pass\n")
    spec_path = root / "spec.md"
    spec_path.write_text(
        "The function `real_helper` in `helpers.py` performs validation.\n"
        "The function `Zzz_nonexistent_symbol` in `helpers.py` does the lookup.\n"
    )
    return spec_path, root


def _clean_fixture(root):
    """Repo root + spec file with zero unresolved citations (control)."""
    root.mkdir(parents=True, exist_ok=True)
    code_file = root / "helpers.py"
    code_file.write_text("def real_helper():\n    pass\n")
    spec_path = root / "spec.md"
    spec_path.write_text("The function `real_helper` in `helpers.py` performs validation.\n")
    return spec_path, root


def _make_ctx(root, extra_org: dict | None = None) -> WorkflowContext:
    org = {"hal_root": str(root)}
    if extra_org:
        org.update(extra_org)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="gh693 lite cite enforce", session_id="s", persona="hal",
        framework=None, domain=None,
    )


def _marker_step_fn(_ctx, prev) -> StepResult:
    """Trivial terminal body step — ships the loop via the real marker_field
    default ('raw_response'). Not the UUT; only a loop-completion stub."""
    data = prev.data if isinstance(prev, StepResult) and isinstance(prev.data, dict) else {}
    return StepResult(status="ok", data={**data, "raw_response": VERDICT_SHIP},
                      duration_ms=0, step_name="ship_marker")


def _make_contract() -> LoopStepContract:
    """Minimal 2-step composite: REAL cite verifier + trivial marker step,
    driven through the REAL LoopRunner._run_consuming (spec §2.1 preferred
    low-fragility path)."""
    return LoopStepContract(
        name="gh693_cite_enforce_loop",
        body=[
            StepContract(name="verify_spec_cite_prelint", execute=phase_45_spec._verify_spec_cite_prelint),
            StepContract(name="ship_marker", execute=_marker_step_fn),
        ],
        max_iterations=MAX_REVIEW_CYCLES,
        until_marker=VERDICT_SHIP,
        consume_recoverable_retry=True,
    )


def _run_composite(contract: LoopStepContract, ctx: WorkflowContext, prev, log: EventLog, run_id: str) -> StepResult:
    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="gh693_cite_enforce_loop", cycle=1)
    try:
        return LoopRunner(contract).run(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()


def _events_for_run(log: EventLog, run_id: str) -> list[dict]:
    return [e for e in log.read_all() if e.get("run_id") == run_id]


def _event_counts(events: list[dict]) -> Counter:
    return Counter(e["event_type"] for e in events)


# ─── AC-L1 (structural) ──────────────────────────────────────────────────────


def test_l1_build_review_loop_contract_has_cite_step_and_consume_flag():
    loop = build_review_loop_contract()
    names = [s.name for s in loop.body]
    assert "verify_spec_cite_prelint" in names, (
        f"expected verify_spec_cite_prelint in real loop body, got: {names!r}"
    )
    assert loop.consume_recoverable_retry is True, (
        "expected real build_review_loop_contract() to opt into consume_recoverable_retry"
    )


# ─── AC-L2 (fresh -> recoverable cite retry consumed in-loop) ───────────────


def test_l2_fresh_recoverable_cite_retry_consumed_in_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")

    spec_path, root = _dirty_fixture(tmp_path / "dirty")
    ctx = _make_ctx(root)
    prev = {"spec_path": str(spec_path), "cycle": 1}

    log = EventLog(tmp_path / "events.jsonl")
    dirty_result = _run_composite(_make_contract(), ctx, prev, log, "run-l2-dirty")
    dirty_counts = _event_counts(_events_for_run(log, "run-l2-dirty"))

    # Presence gate: the cite step actually ran.
    assert dirty_counts.get("spec_cite_prelint_result", 0) >= 1, (
        f"cite step never ran (vacuous absence check risk); events={dirty_counts!r}"
    )
    assert dirty_counts.get("spec_cite_prelint_enforce_retry", 0) == 1, (
        f"expected spec_cite_prelint_enforce_retry emitted exactly once, got counts={dirty_counts!r}"
    )
    assert dirty_result.status == "ok", (
        f"consumed retry must not surface as a terminal composite error; got status={dirty_result.status!r}"
    )
    assert dirty_result.metadata.get("terminated_by") != "error", (
        f"expected non-error termination, got terminated_by={dirty_result.metadata.get('terminated_by')!r}"
    )

    # Baseline comparison — clean-spec control must NOT spend more review budget.
    clean_spec_path, clean_root = _clean_fixture(tmp_path / "clean")
    clean_ctx = _make_ctx(clean_root)
    clean_prev = {"spec_path": str(clean_spec_path), "cycle": 1}
    clean_result = _run_composite(_make_contract(), clean_ctx, clean_prev, log, "run-l2-clean")

    assert dirty_result.metadata.get("iterations") == clean_result.metadata.get("iterations"), (
        "consumed cite retry must be budget-neutral vs a clean-spec control run: "
        f"dirty iterations={dirty_result.metadata.get('iterations')!r}, "
        f"clean iterations={clean_result.metadata.get('iterations')!r}"
    )


# ─── AC-L3 (cap-exhaustion -> non-terminal advance) ─────────────────────────


def test_l3_cap_exhaustion_advances_nonterminal(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")

    spec_path, root = _dirty_fixture(tmp_path)
    ctx = _make_ctx(root)
    prev = {"spec_path": str(spec_path), "cycle": 1}

    log = EventLog(tmp_path / "events.jsonl")
    result = _run_composite(_make_contract(), ctx, prev, log, "run-l3")
    counts = _event_counts(_events_for_run(log, "run-l3"))

    # Presence gate.
    assert counts.get("spec_cite_prelint_result", 0) >= 1, (
        f"cite step never ran; events={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_exhausted", 0) == 1, (
        f"expected spec_cite_prelint_enforce_exhausted emitted exactly once, got counts={counts!r}"
    )
    assert result.status != "error", (
        f"cap-exhaustion must advance non-terminal, got status={result.status!r}"
    )
    assert result.metadata.get("terminated_by") != "retry_ceiling", (
        f"cap-exhaustion must not be mistaken for the hard retry ceiling, "
        f"got terminated_by={result.metadata.get('terminated_by')!r}"
    )


# ─── AC-L4 (§1ab idempotency: gate_attempts caps at 1, verifier runs 2x) ────


def test_l4_gate_attempts_caps_at_one_verifier_runs_exactly_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")

    spec_path, root = _dirty_fixture(tmp_path)
    ctx = _make_ctx(root)
    prev = {"spec_path": str(spec_path), "cycle": 1}

    log = EventLog(tmp_path / "events.jsonl")
    result = _run_composite(_make_contract(), ctx, prev, log, "run-l4")
    counts = _event_counts(_events_for_run(log, "run-l4"))

    assert counts.get("spec_cite_prelint_result", 0) == 2, (
        f"expected the cite verifier entered exactly twice (fresh + one re-entry), "
        f"got spec_cite_prelint_result count={counts.get('spec_cite_prelint_result', 0)}; counts={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_retry", 0) == 1, (
        f"expected enforce_retry count==1, got counts={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_exhausted", 0) == 1, (
        f"expected enforce_exhausted count==1, got counts={counts!r}"
    )
    gate_attempts = (result.data or {}).get("gate_attempts", {})
    assert gate_attempts.get("spec_cite_prelint") == 1, (
        f"expected gate_attempts['spec_cite_prelint']==1 (never double-incremented), "
        f"got {gate_attempts!r}"
    )


# ─── AC-L5 (§1ab resume/replay idempotency) ─────────────────────────────────


def test_l5_resume_replay_seeded_gate_attempts_caps_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")

    spec_path, root = _dirty_fixture(tmp_path)
    ctx = _make_ctx(root)
    prev = {
        "spec_path": str(spec_path),
        "cycle": 2,
        "gate_attempts": {"spec_cite_prelint": 1},
    }

    log = EventLog(tmp_path / "events.jsonl")
    result = _run_composite(_make_contract(), ctx, prev, log, "run-l5")
    counts = _event_counts(_events_for_run(log, "run-l5"))

    # Presence gate.
    assert counts.get("spec_cite_prelint_result", 0) >= 1, (
        f"cite step never ran; events={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_retry", 0) == 0, (
        f"resumed/replayed run with already-spent attempts must NOT emit a fresh retry, "
        f"got counts={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_exhausted", 0) == 1, (
        f"expected the seeded-cap run to emit enforce_exhausted exactly once, got counts={counts!r}"
    )
    assert result.status != "error", (
        f"seeded-cap resume must advance non-terminal, got status={result.status!r}"
    )
    gate_attempts = (result.data or {}).get("gate_attempts", {})
    assert gate_attempts.get("spec_cite_prelint") == 1, (
        f"gate_attempts must remain 1 (not re-incremented on resume/replay), got {gate_attempts!r}"
    )


# ─── AC-L6 (ENFORCE OFF stays warn-only through the composite) ──────────────


def test_l6_enforce_off_stays_warn_only_through_composite(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_CITE_PRELINT_ENFORCE", raising=False)

    spec_path, root = _dirty_fixture(tmp_path)
    ctx = _make_ctx(root)
    prev = {"spec_path": str(spec_path), "cycle": 1}

    log = EventLog(tmp_path / "events.jsonl")
    result = _run_composite(_make_contract(), ctx, prev, log, "run-l6")
    counts = _event_counts(_events_for_run(log, "run-l6"))

    # Presence gate: the cite step actually ran and reached the warn branch.
    assert counts.get("spec_cite_prelint_warn", 0) == 1, (
        f"expected spec_cite_prelint_warn emitted exactly once (cite step reached), "
        f"got counts={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_retry", 0) == 0, (
        f"ENFORCE OFF must NOT emit spec_cite_prelint_enforce_retry, got counts={counts!r}"
    )
    assert counts.get("spec_cite_prelint_enforce_exhausted", 0) == 0, (
        f"ENFORCE OFF must NOT emit spec_cite_prelint_enforce_exhausted, got counts={counts!r}"
    )
    assert result.status == "ok", (
        f"ENFORCE OFF composite run must complete status='ok', got {result.status!r}"
    )
