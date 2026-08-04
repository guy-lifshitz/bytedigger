"""RED tests for GH706 — cfg-configurable validation-cycle cap + directed
reject_reason convergence.

Spec: SHARED/memory/Decisions/2026-07-13_GH706_phase5_validate_cap_directed_reject_spec.md

Pre-GREEN classification (mirrors spec §3):
  AC1 (test_ac1_*)          -> FAIL. `_gate_on_validation` today threads only
        the raw validation doc tail into `findings`; it never reads
        `prev.data["structured_verdict"].reject_reason`, so neither the
        "VALIDATOR REJECT_REASON" literal nor the reject_reason sentinel
        appear in `findings` or the assembled RED prompt.
  AC2 (test_ac2_*)          -> PASS (guard). Cycle-1 with no findings never
        touches the REVISION/reject_reason machinery either before or after
        GREEN — pinned so GREEN cannot regress the byte-identical path.
  AC3 (test_ac3_*)          -> FAIL. Today's cap is hardcoded
        `MAX_VALIDATION_CYCLES=2`; at cycle=2 the gate takes the terminal
        `E_VALIDATION_FAILED` branch (2 is not < 2) instead of returning
        `status='ok'` with `data['findings']`/`data['cycle']`, so subscripting
        `result.data["findings"]` raises KeyError before any idempotency
        assertion is reached.
  AC4 (test_ac4_*)          -> FAIL. `phase_5_implement._resolve_validation_cycle_cap`
        does not exist yet -> AttributeError.
  AC5 (test_ac5_*)          -> FAIL for the cap=3 case (hardcoded cap=2 makes
        cycle=2 terminal instead of re-iterating); the cfg={} case is a
        deliberate PASS guard (today's hardcoded default already matches the
        post-GREEN base-cap default of 2 for an empty cfg).
  AC6 (test_ac6_*)          -> FAIL for `build_validation_loop_contract(3)`
        (TypeError: takes no positional args today); the zero-arg call is a
        PASS guard (today's contract already resolves max_iterations=2).

Anchor classification (§1l): every AC calls the REAL production functions
(`_gate_on_validation`, `_build_red_prompt`, `_resolve_validation_cycle_cap`,
`build_validation_loop_contract`) with no UUT mocks — behavior, not text
greps. `monkeypatch` is used ONLY to force the legacy (non delta-retry)
`_build_red_prompt` branch via `HAL_IMPL_DELTA_RETRY=0`, exactly as
`tests/test_gh496_impl_delta_retry.py` does, so the REVISION-block assertions
are deterministic regardless of the (already-shipped, orthogonal) GH496
delta-retry toggle.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: E402  (import first — wires lib/plugins onto sys.path)
from bytedigger_engine.lib.plugins.disk_truth import ValidationVerdict  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────


def make_ctx(**org_extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=dict(org_extra),
        question="GH706 validate-cap directed-reject test",
        session_id="test-gh706",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_fail_prev(tmp_path: Path, cycle: int, reject_reason: str | None,
                    validation_raw: str) -> StepResult:
    """Build a FAIL-verdict prev shaped like verify_validation_citations output."""
    doc_path = tmp_path / f"validation_cycle{cycle}.md"
    doc_path.write_text(validation_raw, encoding="utf-8")
    red_log_path = tmp_path / f"red_cycle{cycle}.log"
    red_log_path.write_text("stub red log\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "verdict": "FAIL",
            "cycle": cycle,
            "validation_doc_path": str(doc_path),
            "spec_path": str(tmp_path / "spec.md"),
            "red_log_path": str(red_log_path),
            "validation_raw": validation_raw,
            "structured_verdict": ValidationVerdict(
                approve=False, reject_reason=reject_reason
            ),
        },
        duration_ms=0,
        step_name="verify_validation_citations",
    )


# ─── AC1: directed reject_reason present in findings + assembled prompt ────


def test_ac1_gate_threads_structured_reject_reason_into_findings_and_prompt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "0")
    rr_sentinel = "SENTINEL_RR_build_security_world_uncovered_AC15"
    validation_raw = (
        "## Verdict\nFAIL\n\nFinding: build_security_world() uncovered by tests.\n"
    )
    assert rr_sentinel not in validation_raw

    ctx = make_ctx(scratchpad_dir=str(tmp_path))
    prev = make_fail_prev(tmp_path, cycle=1, reject_reason=rr_sentinel,
                           validation_raw=validation_raw)

    result = p5._gate_on_validation(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok' on cycle-1 FAIL (cap default 2), got "
        f"{result.status!r} error_code={result.error_code!r}"
    )
    findings = result.data["findings"]
    assert "VALIDATOR REJECT_REASON" in findings, (
        "GH706 AC1 (forcing): expected the gate to prepend the "
        "'VALIDATOR REJECT_REASON' literal to findings; today the gate "
        f"threads only the raw validation doc tail. findings={findings!r}"
    )
    assert rr_sentinel in findings, (
        "GH706 AC1 (forcing): expected the structured reject_reason sentinel "
        f"in findings, got findings={findings!r}"
    )

    prompt_result = p5._build_red_prompt(ctx, result)
    assert prompt_result.status == "ok"
    assert rr_sentinel in prompt_result.data["prompt"], (
        "GH706 AC1 (forcing): expected the reject_reason sentinel to flow "
        "through into the assembled RED prompt via the threaded findings."
    )


# ─── AC2: cycle-1 byte-identical back-compat (no REVISION / no reject_reason) ─


def test_ac2_cycle1_prompt_has_no_revision_or_reject_reason_block(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "0")
    ctx = make_ctx(scratchpad_dir=str(tmp_path))

    result = p5._build_red_prompt(ctx, None)

    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "REVISION" not in prompt, (
        "GH706 AC2 (guard): cycle-1 prompt with no findings must not contain "
        f"a REVISION block, got prompt containing 'REVISION'."
    )
    assert "VALIDATOR REJECT_REASON" not in prompt, (
        "GH706 AC2 (guard): cycle-1 prompt with no findings must not contain "
        "the reject_reason prefix."
    )


# ─── AC3: §1ab re-entry idempotency — no compounding on repeat gate calls ───


def test_ac3_gate_reentry_is_idempotent_and_prompt_has_single_reject_reason(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "0")
    rr_sentinel = "SENTINEL_RR_reentry_gh706_idempotency"
    validation_raw = "## Verdict\nFAIL\n\nFinding: still missing coverage.\n"
    assert rr_sentinel not in validation_raw

    ctx = make_ctx(scratchpad_dir=str(tmp_path), validation_max_cycles=3)
    prev = make_fail_prev(tmp_path, cycle=2, reject_reason=rr_sentinel,
                           validation_raw=validation_raw)

    call1 = p5._gate_on_validation(ctx, prev)
    call2 = p5._gate_on_validation(ctx, prev)

    assert call1.status == "ok", (
        "GH706 AC3 (forcing): expected status='ok' at cycle=2 with cfg cap=3 "
        f"(cycle < cap must re-iterate), got {call1.status!r} "
        f"error_code={call1.error_code!r}. Pre-GREEN: cap is hardcoded to "
        "MAX_VALIDATION_CYCLES=2, so cycle=2 is NOT < 2 and the gate takes "
        "the terminal E_VALIDATION_FAILED branch instead."
    )
    assert call2.status == "ok"
    assert call1.data["findings"] == call2.data["findings"], (
        "GH706 AC3 (forcing): repeat gate calls on the SAME prev must "
        "produce identical findings (reject_reason prefix computed fresh "
        "each call, not compounded)."
    )
    assert call1.data["cycle"] == 3, (
        f"GH706 AC3: expected cycle incremented to 3, got {call1.data['cycle']!r}"
    )
    assert call2.data["cycle"] == 3, (
        f"GH706 AC3: expected cycle incremented to 3 on repeat call too, "
        f"got {call2.data['cycle']!r}"
    )

    prompt_result = p5._build_red_prompt(ctx, call1)
    assert prompt_result.status == "ok"
    prompt = prompt_result.data["prompt"]
    assert prompt.count("VALIDATOR REJECT_REASON") == 1, (
        "GH706 AC3 (forcing): expected exactly ONE 'VALIDATOR REJECT_REASON' "
        f"occurrence in the cycle-3 prompt (no compounding), got "
        f"{prompt.count('VALIDATOR REJECT_REASON')}."
    )


# ─── AC4: cap-cfg resolver — both states ────────────────────────────────────


def test_ac4_resolve_validation_cycle_cap_precedence():
    assert p5._resolve_validation_cycle_cap({}) == 2, (
        "GH706 AC4 (forcing): expected base default cap 2 for empty cfg; "
        "_resolve_validation_cycle_cap does not exist on main."
    )
    assert p5._resolve_validation_cycle_cap({"validation_max_cycles": 5}) == 5, (
        "GH706 AC4 (forcing): expected explicit cfg cap 5 to win."
    )
    assert p5._resolve_validation_cycle_cap({"complexity": "COMPLEX"}) == 3, (
        "GH706 AC4 (forcing): expected COMPLEX-tier cap 3."
    )
    # MINOR-2 (gate advisory): malformed/falsy override must fall through to base.
    assert p5._resolve_validation_cycle_cap({"validation_max_cycles": "abc"}) == 2, (
        "GH706 AC4 (forcing): malformed cfg override must fall through to base 2."
    )
    assert p5._resolve_validation_cycle_cap({"validation_max_cycles": 0}) == 2, (
        "GH706 AC4 (forcing): falsy (0) override must be skipped -> base 2."
    )


# ─── AC5: gate honors cfg cap — decisive ────────────────────────────────────


def test_ac5_gate_reiterates_when_cfg_cap_is_3_at_cycle2(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "0")
    ctx = make_ctx(scratchpad_dir=str(tmp_path), validation_max_cycles=3)
    prev = make_fail_prev(
        tmp_path, cycle=2, reject_reason="rr",
        validation_raw="## Verdict\nFAIL\n\nstill bad.\n",
    )

    result = p5._gate_on_validation(ctx, prev)

    assert result.status == "ok", (
        "GH706 AC5 (forcing, decisive): with cfg cap=3, cycle=2 must "
        f"re-iterate (status='ok'), got {result.status!r} "
        f"error_code={result.error_code!r}. Pre-GREEN: cap hardcoded to 2 "
        "makes cycle=2 terminal instead."
    )
    assert result.data["cycle"] == 3, (
        f"GH706 AC5: expected cycle incremented to 3, got {result.data.get('cycle')!r}"
    )


def test_ac5_gate_stays_terminal_with_default_cfg_at_cycle2(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "0")
    ctx = make_ctx(scratchpad_dir=str(tmp_path))
    prev = make_fail_prev(
        tmp_path, cycle=2, reject_reason="rr",
        validation_raw="## Verdict\nFAIL\n\nstill bad.\n",
    )

    result = p5._gate_on_validation(ctx, prev)

    assert result.status == "error", (
        "GH706 AC5 (guard — legacy preserved): with default cfg (base cap "
        f"2), cycle=2 must stay terminal, got {result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_FAILED", (
        f"GH706 AC5 (guard): expected E_VALIDATION_FAILED, got {result.error_code!r}"
    )


# ─── AC6: loop contract honors cap ──────────────────────────────────────────


def test_ac6_build_validation_loop_contract_honors_max_cycles_param():
    contract = p5.build_validation_loop_contract(3)
    assert contract.max_iterations == 3, (
        "GH706 AC6 (forcing): expected build_validation_loop_contract(3)."
        f"max_iterations == 3, got {contract.max_iterations!r}. Pre-GREEN: "
        "build_validation_loop_contract takes no positional args -> TypeError."
    )


def test_ac6_build_validation_loop_contract_default_stays_2():
    contract = p5.build_validation_loop_contract()
    assert contract.max_iterations == 2, (
        "GH706 AC6 (guard): default build_validation_loop_contract() must "
        f"keep max_iterations == MAX_VALIDATION_CYCLES == 2, got "
        f"{contract.max_iterations!r}"
    )


# ─── AC7: integration seam — loop-execute wires the resolved cap (MINOR-1) ──


def test_ac7_validation_cycle_loop_execute_wires_resolved_cap(tmp_path, monkeypatch):
    """Closes the gate's MINOR-1 teeth-gap: if _validation_cycle_loop_execute
    fails to resolve the cap from ctx.org_config and pass it to
    build_validation_loop_contract, then with cfg cap=3 the loop would cap at 2
    while the gate re-iterates -> a FAIL verdict could fall through to GREEN.

    We monkeypatch the collaborators (NOT the UUT) to capture the cap the
    UUT forwards. On main _validation_cycle_loop_execute calls
    build_validation_loop_contract() with no argument, so the captured cap is
    None != 3 -> FAIL (forcing)."""
    captured: dict = {}
    real_contract = p5.build_validation_loop_contract()  # valid contract (zero-arg works pre & post)

    def fake_builder(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_contract

    class FakeLoopRunner:
        def __init__(self, contract):
            captured["contract"] = contract

        def run(self, ctx, prev):
            return StepResult(status="ok", data={"verdict": "PASS"},
                              duration_ms=0, step_name="fake_loop")

    monkeypatch.setattr(p5, "build_validation_loop_contract", fake_builder)
    monkeypatch.setattr(p5, "LoopRunner", FakeLoopRunner)

    ctx = make_ctx(scratchpad_dir=str(tmp_path), validation_max_cycles=3)
    prev = StepResult(status="ok", data={"cycle": 1}, duration_ms=0, step_name="prev")

    p5._validation_cycle_loop_execute(ctx, prev)

    passed_cap = (
        captured["args"][0] if captured.get("args")
        else captured.get("kwargs", {}).get("max_cycles")
    )
    assert passed_cap == 3, (
        "GH706 AC7 (forcing): _validation_cycle_loop_execute must resolve the "
        "cap from ctx.org_config (validation_max_cycles=3) and pass it to "
        f"build_validation_loop_contract, got {passed_cap!r}. Pre-GREEN the "
        "loop-execute builds the contract with no cap argument, so a raised "
        "gate cap would not raise the loop's max_iterations -> FAIL->GREEN "
        "fall-through risk."
    )
