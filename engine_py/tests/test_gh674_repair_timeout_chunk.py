"""RED tests for GH674 — directed_repair: chunking + scalable timeout + fail-fast.

Spec: SHARED/memory/Decisions/2026-07-12_A7C4F1E2_gh674_repair_timeout_chunk_spec.md
ACs 1-12 (spec Section 3). Pre-GREEN expected: ALL FAIL except AC9/AC10/AC12
(explicit regression guards over already-shipped, chunking-unaffected behavior
per spec framing -- these may already PASS today; that is acceptable).

Forcing functions today:
  - `lib/directed_repair.py` exists but does NOT define `_batch_findings`,
    `_repair_batch_size`, or `_repair_timeout_sec` -> AC1/AC2/AC4/AC5 raise
    AttributeError INSIDE the test body (collectable module import at top
    level; missing symbols resolved at call time only -- D1CF5FDF guard).
  - `_run_repair_loop` sends ALL findings in ONE unbatched LLM call and has
    no fail-fast/no_progress branch -> AC3/AC6/AC7/AC11 fail on wrong call
    counts / wrong exhausted_reason.

Conventions: `lib.directed_repair` is imported at module level (it already
exists -- collectable). No conftest import, no sys.path manipulation, no
spec_from_file_location/exec_module (Sec1q). Every LLM seam is stubbed
(burn-guard) -- no real subprocess/network call is ever risked. All fixtures
(artifact file, findings list, gate closures) are pre-staged deterministically
on disk / in-memory before invoking the UUT -- no timing-dependent singleton
resources (Sec1i N/A: this primitive has none).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bytedigger_engine.lib import directed_repair as dr  # noqa: E402
from bytedigger_engine.contracts import StepResult  # noqa: E402


# --- shared fixtures / helpers ------------------------------------------------

# Fixed, deterministic artifact body. The stub always returns THIS SAME text
# as the "patch", so atomic_write is idempotent and artifact byte-size stays
# constant across batches/attempts -- required for the AC5 timeout math to be
# deterministic (no incidental byte-size drift from a real edit).
ARTIFACT_TEXT = (
    "# spec457 body\n"
    + ("lorem ipsum filler padding line for byte-size stability.\n" * 120)
)


def _make_findings22(artifact_path: str) -> list[dict]:
    """22 findings mirroring calibration No4's unresolved-citation shape.
    Every evidence string is unique so per-batch containment is assertable."""
    findings: list[dict] = []
    for i in range(11):
        findings.append({
            "path": artifact_path, "line": None, "rule": "spec_cite_lint",
            "evidence": f"DocumentClassifier not found in production #{i:02d}",
        })
    for i in range(7):
        findings.append({
            "path": artifact_path, "line": None, "rule": "spec_cite_lint",
            "evidence": f"build_citations_from_sw unresolved #{i:02d}",
        })
    for i in range(4):
        findings.append({
            "path": artifact_path, "line": None, "rule": "spec_cite_lint",
            "evidence": f"unresolved_citation_extra #{i:02d}",
        })
    assert len(findings) == 22
    evidences = {f["evidence"] for f in findings}
    assert len(evidences) == 22, "every finding's evidence must be unique"
    return findings


def _patch_invoke(monkeypatch, dr_mod, patched_text: str, calls: list) -> None:
    """Stub the LLM seam on the directed_repair module -- never a real call."""
    def fake_invoke(*args, **kwargs):
        calls.append(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": patched_text,
                "response_bytes": len(patched_text),
                "command": ["stub"],
            },
            duration_ms=1,
            step_name=kwargs.get("step_name", "repair"),
        )

    patched = False
    if hasattr(dr_mod, "invoke_llm_subprocess"):
        monkeypatch.setattr(dr_mod, "invoke_llm_subprocess", fake_invoke)
        patched = True
    try:
        from bytedigger_engine import llm_subprocess  # noqa: PLC0415
        monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", fake_invoke)
        patched = True
    except ImportError:
        pass
    assert patched, "could not stub invoke_llm_subprocess -- refusing to risk a real LLM call"


def _fail_gate_constant(evidence_list: list[str]):
    """A gate that FAILs forever, returning the SAME-length findings set every
    call -- the fail-fast / no-progress fixture (AC6/AC11)."""
    def gate() -> StepResult:
        return StepResult(
            status="error",
            data={"spec_cite_lint_findings": list(evidence_list)},
            duration_ms=0,
            step_name="verify_spec_cite_lint",
            error="gate still failing",
            error_code="E_SPEC_CITE_LINT_FAIL",
            recoverable=False,
        )
    return gate


def _fail_then_ok_gate(shorter_evidence_list: list[str]):
    """Attempt-1 FAILs with a strictly SHORTER findings set (progress); every
    subsequent call returns ok (AC7/AC11)."""
    state = {"n": 0}

    def gate() -> StepResult:
        state["n"] += 1
        if state["n"] == 1:
            return StepResult(
                status="error",
                data={"spec_cite_lint_findings": list(shorter_evidence_list)},
                duration_ms=0,
                step_name="verify_spec_cite_lint",
                error="gate still failing",
                error_code="E_SPEC_CITE_LINT_FAIL",
                recoverable=False,
            )
        return StepResult(
            status="ok", data={"spec_cite_lint_findings": []},
            duration_ms=0, step_name="verify_spec_cite_lint",
        )
    return gate


def _ok_gate() -> StepResult:
    return StepResult(
        status="ok", data={"spec_cite_lint_findings": []},
        duration_ms=0, step_name="verify_spec_cite_lint",
    )


def _install_emit_spy(monkeypatch, dr_mod) -> list:
    events: list = []

    def spy(event_type, payload, *args, **kwargs):
        events.append((event_type, payload))

    monkeypatch.setattr(dr_mod.telemetry_ctx, "emit_safe", spy)
    return events


# --- AC1: _batch_findings partition -------------------------------------------


class TestAC1BatchFindings:
    def test_ac1_batch_findings_partitions_22_into_8_8_6_no_loss_no_reorder(self):
        findings = _make_findings22("/tmp/artifact457.md")
        batches = dr._batch_findings(findings, 8)
        sizes = [len(b) for b in batches]
        assert sizes == [8, 8, 6], f"expected batch sizes [8, 8, 6]; got {sizes!r}"
        flat = [f for b in batches for f in b]
        assert flat == findings, "flattened batches must equal input (no loss/dup/reorder)"


# --- AC2: _repair_batch_size --------------------------------------------------


class TestAC2RepairBatchSize:
    def test_ac2_default_and_cfg_overrides(self):
        assert dr._repair_batch_size({}) == 8
        assert dr._repair_batch_size(None) == 8
        assert dr._repair_batch_size({"directed_repair_batch_size": 5}) == 5
        assert dr._repair_batch_size({"directed_repair_batch_size": 0}) == 8
        assert dr._repair_batch_size({"directed_repair_batch_size": -1}) == 8


# --- AC3: batched calls with per-batch containment ----------------------------


class TestAC3BatchedCallsContainment:
    def test_ac3_one_attempt_over_22_findings_issues_3_calls_with_containment(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, ARTIFACT_TEXT, invoke_calls)
        gate = _fail_gate_constant([f["evidence"] for f in findings])
        ctx = SimpleNamespace(org_config={})

        dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=1, ctx=ctx,
        )

        assert len(invoke_calls) == 3, (
            f"expected 3 batched LLM calls (ceil(22/8)); got {len(invoke_calls)}: "
            f"{invoke_calls!r}"
        )
        expected_batches = [findings[0:8], findings[8:16], findings[16:22]]
        all_evidence = {f["evidence"] for f in findings}
        for call, batch in zip(invoke_calls, expected_batches):
            prompt = call.get("prompt", "")
            assert ARTIFACT_TEXT in prompt, "prompt must contain the full artifact text"
            own = {f["evidence"] for f in batch}
            for ev in own:
                assert ev in prompt, f"own-batch evidence {ev!r} missing from prompt"
            for ev in all_evidence - own:
                assert ev not in prompt, (
                    f"foreign-batch evidence {ev!r} leaked into this call's prompt"
                )


# --- AC4: timeout formula ------------------------------------------------------


class TestAC4TimeoutFormula:
    def test_ac4_repair_timeout_sec_monotonic_base_and_cap(self):
        base = dr._repair_timeout_sec({}, 1, 100)
        assert base >= 120, f"base timeout must be >= 120; got {base!r}"

        grid_n = [dr._repair_timeout_sec({}, n, 100) for n in (1, 5, 10, 50)]
        assert grid_n == sorted(grid_n), (
            f"must be monotonic non-decreasing in num_findings; got {grid_n!r}"
        )

        grid_b = [dr._repair_timeout_sec({}, 1, b) for b in (100, 1000, 10000, 1000000)]
        assert grid_b == sorted(grid_b), (
            f"must be monotonic non-decreasing in artifact_bytes; got {grid_b!r}"
        )

        large = dr._repair_timeout_sec({}, 100, 200000)
        assert large == 600, f"large (n=100, bytes=200000) must hit the 600s cap; got {large!r}"
        assert large != 300, "must never fall back to the retired hard 300s constant"


# --- AC5: per-batch timeout wired into invoke_llm_subprocess -------------------


class TestAC5PerBatchTimeoutWired:
    def test_ac5_each_call_timeout_sec_matches_helper_output(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, ARTIFACT_TEXT, invoke_calls)
        gate = _fail_gate_constant([f["evidence"] for f in findings])
        ctx = SimpleNamespace(org_config={})

        dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=1, ctx=ctx,
        )

        assert invoke_calls, "no LLM calls were recorded"
        artifact_bytes = len(ARTIFACT_TEXT.encode("utf-8", "replace"))
        expected_batches = [findings[0:8], findings[8:16], findings[16:22]]
        for call, batch in zip(invoke_calls, expected_batches):
            expected_timeout = dr._repair_timeout_sec({}, len(batch), artifact_bytes)
            assert call.get("timeout_sec") == expected_timeout, (
                f"call timeout_sec={call.get('timeout_sec')!r} != "
                f"_repair_timeout_sec output {expected_timeout!r}"
            )
            assert call.get("timeout_sec") != 300, (
                "must not use the retired hard 300s constant"
            )


# --- AC6: fail-fast is ERROR-scoped, NOT count-scoped ---------------------------


def _always_error_invoke(monkeypatch, dr_mod, calls: list) -> None:
    """Stub invoke_llm_subprocess so EVERY call returns a non-ok StepResult
    (the model emitted NO output at all -- e.g. a real timeout). Never risks
    a real LLM/subprocess call."""
    def fake_invoke(*args, **kwargs):
        calls.append(kwargs)
        return StepResult(
            status="error", data={}, duration_ms=0,
            step_name=kwargs.get("step_name", "repair"),
            error="timed out", error_code="E_LLM_TIMEOUT", recoverable=True,
        )

    patched = False
    if hasattr(dr_mod, "invoke_llm_subprocess"):
        monkeypatch.setattr(dr_mod, "invoke_llm_subprocess", fake_invoke)
        patched = True
    try:
        from bytedigger_engine import llm_subprocess  # noqa: PLC0415
        monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", fake_invoke)
        patched = True
    except ImportError:
        pass
    assert patched, "could not stub invoke_llm_subprocess -- refusing to risk a real LLM call"


class TestAC6FailFastErrorScoped:
    def test_ac6_failfast_on_all_calls_errored(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Every attempt-1 repair LLM call errors (no ok output at all) AND
        the gate still fails -> fail-fast BEFORE attempt-2. Fires purely on
        all-calls-errored, NOT on any finding-count comparison."""
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        invoke_calls: list = []
        _always_error_invoke(monkeypatch, dr, invoke_calls)
        gate = _fail_gate_constant([f["evidence"] for f in findings])
        ctx = SimpleNamespace(
            org_config={"directed_repair_batch_error_threshold": 4}
        )

        rr = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=2, ctx=ctx,
        )

        assert len(invoke_calls) == 3, (
            "all-calls-errored attempt-1 must fail-fast after its 3 batches "
            f"only (NO attempt-2 calls); got {len(invoke_calls)} calls"
        )
        assert rr.converged is False, f"expected converged=False; got {rr!r}"
        assert rr.exhausted_reason == "no_progress", (
            f"expected exhausted_reason='no_progress'; got {rr.exhausted_reason!r}"
        )

    def test_ac6_regression_ok_patch_still_exhausts_to_attempts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Regression guard (457DC7DC-AC2 / gh382-A7 / gh482-AC4): when the
        model DOES emit ok output every attempt but the gate never passes,
        exhaustion follows the normal attempts-cap path, NOT fail-fast."""
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))[:5]  # 1 batch (<=8)
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, ARTIFACT_TEXT, invoke_calls)  # status="ok" stub
        gate = _fail_gate_constant([f["evidence"] for f in findings])
        ctx = SimpleNamespace(org_config={})

        rr = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=2, ctx=ctx,
        )

        assert len(invoke_calls) == 2, (
            f"ok-output attempts must NOT be fail-fasted; expected 2 calls "
            f"(1 batch x 2 attempts); got {len(invoke_calls)}"
        )
        assert rr.attempts == 2, f"expected attempts==2 (cap reached); got {rr.attempts!r}"
        assert rr.exhausted_reason == "attempts", (
            f"expected exhausted_reason='attempts'; got {rr.exhausted_reason!r}"
        )


# --- AC7: progress + refresh recognizes spec_cite_lint_findings ----------------


class TestAC7ProgressRefreshRebatches:
    def test_ac7_refreshed_smaller_set_rebatched_on_attempt_2_and_converges(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """ok-output stubs throughout (no errors) -- attempt-1 rerun_gate FAILs
        with a strictly SMALLER spec_cite_lint_findings set, attempt-2 rerun
        returns ok. attempt-2's LLM calls must reflect ONLY the refreshed
        5-item set, re-batched (1 batch)."""
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        invoke_calls: list = []
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n# spec457 body\n=======\n# spec457 body\n>>>>>>> REPLACE\n",
            invoke_calls,
        )
        refreshed_evidence = [f["evidence"] for f in findings[:5]]
        gate = _fail_then_ok_gate(refreshed_evidence)
        ctx = SimpleNamespace(org_config={})

        rr = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=2, ctx=ctx,
        )

        assert rr.converged is True, f"expected convergence on attempt 2; got {rr!r}"
        # attempt-1: 3 batches (22/8); attempt-2: refreshed 5 findings -> 1 batch -> 1 call.
        assert len(invoke_calls) == 4, (
            f"expected 3 attempt-1 calls + 1 attempt-2 call == 4; got {len(invoke_calls)}"
        )
        second_attempt_prompt = invoke_calls[3].get("prompt", "")
        for ev in refreshed_evidence:
            assert ev in second_attempt_prompt, (
                f"refreshed evidence {ev!r} missing from attempt-2 prompt"
            )
        stale_evidence = {f["evidence"] for f in findings[5:]}
        for ev in stale_evidence:
            assert ev not in second_attempt_prompt, (
                f"stale evidence {ev!r} leaked into attempt-2 prompt -- attempt-2 "
                "must run on the REFRESHED 5-item set, not the original 22"
            )


# --- AC8: convergence authority -------------------------------------------------


class TestAC8ConvergenceAuthority:
    def test_ac8_ok_gate_converges_with_final_ok_sr(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        invoke_calls: list = []
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n# spec457 body\n=======\n# spec457 body\n>>>>>>> REPLACE\n",
            invoke_calls,
        )
        ctx = SimpleNamespace(org_config={})

        rr = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=_ok_gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=1, ctx=ctx,
        )
        assert rr.converged is True, f"got {rr!r}"
        assert rr.final is not None and rr.final.status == "ok", f"got final={rr.final!r}"

    def test_ac8_never_ok_gate_never_converges(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, ARTIFACT_TEXT, invoke_calls)
        gate = _fail_gate_constant([f["evidence"] for f in findings])
        ctx = SimpleNamespace(org_config={})

        rr = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=2, ctx=ctx,
        )
        assert rr.converged is False, (
            f"a gate that never passes must never yield converged=True; got {rr!r}"
        )


# --- AC9: kill-switch regression -----------------------------------------------


class TestAC9KillSwitchRegression:
    def test_ac9_env_0_disables(self, monkeypatch) -> None:
        dr._in_repair.set(False)
        monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
        ctx = SimpleNamespace(org_config={})
        assert dr._directed_repair_enabled(ctx) is False

    def test_ac9_org_config_skip_disables(self, monkeypatch) -> None:
        dr._in_repair.set(False)
        monkeypatch.delenv("HAL_DIRECTED_REPAIR", raising=False)
        ctx = SimpleNamespace(org_config={"directed_repair_skip": True})
        assert dr._directed_repair_enabled(ctx) is False


# --- AC10: backward-compat small findings set ----------------------------------


class TestAC10SmallSetSingleBatch:
    def test_ac10_3_findings_batch_8_yields_1_call_per_attempt(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))[:3]
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, ARTIFACT_TEXT, invoke_calls)
        gate = _fail_gate_constant([f["evidence"] for f in findings])
        ctx = SimpleNamespace(org_config={})

        dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=1, ctx=ctx,
        )
        assert len(invoke_calls) == 1, (
            f"3 findings <= batch_size 8 must yield exactly 1 call; got {len(invoke_calls)}"
        )


# --- AC11: re-entry -- fresh attempt counter, no leaked state ------------------


class TestAC11ReentryFreshAttemptCounter:
    def test_ac11_second_invocation_starts_fresh_counter_no_leaked_state(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "spec457.md"
        artifact.write_text(ARTIFACT_TEXT, encoding="utf-8")
        findings = _make_findings22(str(artifact))
        ctx = SimpleNamespace(org_config={})

        # 1st invocation: no-progress gate -> fail-fast (AC6 path).
        invoke_calls_1: list = []
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n# spec457 body\n=======\n# spec457 body\n>>>>>>> REPLACE\n",
            invoke_calls_1,
        )
        gate1 = _fail_gate_constant([f["evidence"] for f in findings])
        rr1 = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate1, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=2, ctx=ctx,
        )
        assert rr1.converged is False
        assert rr1.exhausted_reason in ("attempts", "no_progress"), (
            f"1st invocation must exhaust; got {rr1.exhausted_reason!r}"
        )
        assert dr._in_repair.get() is False, "_in_repair must be reset after 1st call"

        # 2nd invocation: immediately-ok gate -> must start a FRESH attempt
        # counter, not carry over state from the exhausted 1st call.
        invoke_calls_2: list = []
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n# spec457 body\n=======\n# spec457 body\n>>>>>>> REPLACE\n",
            invoke_calls_2,
        )
        rr2 = dr.attempt_directed_repair(
            gate="spec_cite_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=_ok_gate, cheap_model="sonnet",
            repair_step_name="repair_spec_cite_lint", max_attempts=2, ctx=ctx,
        )
        assert rr2.converged is True, f"got {rr2!r}"
        assert rr2.attempts == 1, (
            f"2nd invocation must start a fresh attempt counter (immediate "
            f"convergence -> attempts==1); got {rr2.attempts!r}"
        )
        assert dr._in_repair.get() is False, "_in_repair must be reset after 2nd call"


# --- AC12: authored-boundary regression under chunking -------------------------


class TestAC12AuthoredBoundaryUnderChunking:
    def test_ac12_new_suppression_token_restored_and_rejected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "app457.py"
        original = "def f():\n    eval(user_input)  # flagged\n" + ("x = 1\n" * 60)
        artifact.write_text(original, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        patch_block = (
            "<<<<<<< SEARCH\n    eval(user_input)  # flagged\n=======\n"
            "    eval(user_input)  # nosem\n>>>>>>> REPLACE\n"
        )
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, patch_block, invoke_calls)
        findings = [{"path": str(artifact), "line": 2, "rule": "eval-danger", "evidence": "eval"}]
        gate = _fail_gate_constant(["eval"])
        ctx = SimpleNamespace(org_config={})

        rr = dr.attempt_directed_repair(
            gate="green_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_green_lint", max_attempts=1, ctx=ctx,
        )
        assert rr.converged is False, f"suppression-injected patch must not converge; got {rr!r}"
        assert artifact.read_text(encoding="utf-8") == original, (
            "artifact must be RESTORED after a rejected suppression-token patch"
        )
        names = [n for n, _ in events]
        assert "directed_repair_suppression_rejected" in names, (
            f"missing directed_repair_suppression_rejected event; got {names!r}"
        )

    def test_ac12_size_collapsed_patch_rejected_no_write(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        artifact = tmp_path / "app458.py"
        original = "x = 1\n" * 100  # well above the 200-byte size-gate floor
        artifact.write_text(original, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        patch_block = (
            "<<<<<<< SEARCH\n" + original + "=======\nx = 1\n>>>>>>> REPLACE\n"
        )
        invoke_calls: list = []
        _patch_invoke(monkeypatch, dr, patch_block, invoke_calls)
        findings = [{"path": str(artifact), "line": 1, "rule": "some-rule", "evidence": "e"}]
        gate = _fail_gate_constant(["e"])
        ctx = SimpleNamespace(org_config={})

        dr.attempt_directed_repair(
            gate="green_lint", artifact_path=str(artifact), findings=findings,
            rerun_gate=gate, cheap_model="sonnet",
            repair_step_name="repair_green_lint", max_attempts=1, ctx=ctx,
        )
        assert artifact.read_text(encoding="utf-8") == original, (
            "artifact must remain UNWRITTEN after a rejected size-collapse patch"
        )
        names = [n for n, _ in events]
        assert "directed_repair_patch_rejected" in names, (
            f"missing directed_repair_patch_rejected event; got {names!r}"
        )
