"""RED tests for GH690 / 857C7684 — directed_repair search/replace patch
protocol + per-batch circuit-breaker (Bug B, calibration #5).

Spec: SHARED/memory/Decisions/2026-07-12_857C7684_gh690_directed_repair_patch_protocol_spec.md
ACs 1-17 (spec §3, revised post-Opus-REVISE). Pre-GREEN expected: ALL FAIL (a
few AC14/AC16 sub-fixtures are DEFER/regression guards over UNCHANGED §1n
paths and may already pass today — acceptable per spec §1n; every other AC
fails for the forcing reason noted inline).

Collectability (§1q / D1CF5FDF): `lib.directed_repair` EXISTS today, so the
module import itself is safe at any point. The symbols that do NOT exist yet
(`_parse_search_replace_blocks`, `_apply_search_replace`,
`_repair_batch_error_threshold`) and the 2-arg `_extract_patched_text` call
are ONLY referenced INSIDE test-function bodies via the `_dr()` helper —
never at module import time — so the file collects cleanly and every test
fails at assert/attribute-access/TypeError time, never at collection time.

Every LLM seam is stubbed (burn-guard); no real subprocess is ever invoked.
"""
from __future__ import annotations

import pytest

import telemetry_ctx  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers ───────────────────────────────────────────────────────────


def _dr():
    """Import lib.directed_repair lazily inside test bodies (§1q convention;
    the module itself exists today, but new-symbol access must stay deferred)."""
    import lib.directed_repair as dr
    return dr


def _make_ctx(git_cwd: str, **extra_cfg) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, **extra_cfg},
        question="q",
        session_id="test-857c7684",
        persona="hal",
        framework=None,
        domain=None,
    )


_FULL_DUMP = "This is a full artifact rewrite without any search/replace markers.\n"


def _valid_patch_raw(search: str, replace: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n"


def _err_rerun_gate() -> StepResult:
    return StepResult(
        status="error", data={}, duration_ms=0, step_name="verify_x",
        error="gate still failing", error_code="E_X", recoverable=False,
    )


# ─── AC1: prompt shape ────────────────────────────────────────────────────────


class TestAC1BuildPromptSearchReplace:
    def test_ac1_build_prompt_uses_search_replace_markers_not_full_artifact(self) -> None:
        dr = _dr()
        findings = [{"path": "x.py", "line": 1, "rule": "r", "evidence": "e"}]
        prompt = dr._build_prompt("gate", "x.py", "artifact text\n", findings)
        assert "<<<<<<< SEARCH" in prompt, f"missing SEARCH marker: {prompt!r}"
        assert "=======" in prompt, f"missing separator marker: {prompt!r}"
        assert ">>>>>>> REPLACE" in prompt, f"missing REPLACE marker: {prompt!r}"
        assert "full corrected artifact" not in prompt, (
            f"old full-artifact-dump instruction must be removed: {prompt!r}"
        )


# ─── AC2: block parsing ───────────────────────────────────────────────────────


class TestAC2ParseSearchReplaceBlocks:
    def test_ac2_parses_ordered_blocks_and_empty_on_no_markers(self) -> None:
        dr = _dr()
        raw = (
            "<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE\n"
            "<<<<<<< SEARCH\nbaz\n=======\nqux\n>>>>>>> REPLACE\n"
        )
        blocks = dr._parse_search_replace_blocks(raw)
        assert isinstance(blocks, list) and len(blocks) == 2, f"got {blocks!r}"
        assert blocks[0] == ("foo", "bar"), f"got {blocks!r}"
        assert blocks[1] == ("baz", "qux"), f"got {blocks!r}"
        assert dr._parse_search_replace_blocks("no markers here at all") == []


# ─── AC3-6: apply logic ───────────────────────────────────────────────────────


class TestAC3ApplySearchReplaceExactlyOnce:
    def test_ac3_apply_search_replace_single_exact_match(self) -> None:
        dr = _dr()
        pre = "alpha\nbeta\ngamma\n"
        result = dr._apply_search_replace(pre, [("beta", "BETA-FIXED")])
        assert result == "alpha\nBETA-FIXED\ngamma\n", f"got {result!r}"


class TestAC4ApplySearchReplaceZeroMatches:
    def test_ac4_apply_search_replace_zero_matches_returns_none(self) -> None:
        dr = _dr()
        pre = "alpha\nbeta\n"
        assert dr._apply_search_replace(pre, [("nomatch-token", "x")]) is None


class TestAC5ApplySearchReplaceAmbiguousOrEmpty:
    def test_ac5_apply_search_replace_ambiguous_match_returns_none(self) -> None:
        dr = _dr()
        pre = "dup\ndup\nother\n"
        assert dr._apply_search_replace(pre, [("dup", "X")]) is None, (
            "SEARCH occurring >1 time is ambiguous — must return None"
        )

    def test_ac5_apply_search_replace_empty_search_returns_none(self) -> None:
        dr = _dr()
        pre = "alpha\nbeta\n"
        assert dr._apply_search_replace(pre, [("", "X")]) is None, (
            "empty SEARCH must be rejected — must return None"
        )


class TestAC6ApplySearchReplaceSequential:
    def test_ac6_apply_search_replace_two_blocks_sequential(self) -> None:
        dr = _dr()
        pre = "one\ntwo\nthree\n"
        blocks = [("two", "TWO-A"), ("TWO-A", "TWO-B")]
        result = dr._apply_search_replace(pre, blocks)
        assert result == "one\nTWO-B\nthree\n", f"got {result!r}"


# ─── AC7/AC9: _extract_patched_text new signature ────────────────────────────


class TestAC7ExtractPatchedTextNewSignature:
    def test_ac7_extract_patched_text_valid_blocks_and_inapplicable(self) -> None:
        dr = _dr()
        pre = "hello\nworld\n"
        raw_valid = _valid_patch_raw("world", "WORLD-FIXED")
        sr_valid = StepResult(
            status="ok", data={"raw_response": raw_valid}, duration_ms=0, step_name="repair"
        )
        assert dr._extract_patched_text(sr_valid, pre) == "hello\nWORLD-FIXED\n"

        raw_bad = "no markers, just prose"
        sr_bad = StepResult(
            status="ok", data={"raw_response": raw_bad}, duration_ms=0, step_name="repair"
        )
        assert dr._extract_patched_text(sr_bad, pre) is None


class TestAC9RegressionGuardFullArtifactDump:
    def test_ac9_full_artifact_dump_rejected_no_full_replacement_fallback(self) -> None:
        dr = _dr()
        pre = "original text unchanged\n"
        sr = StepResult(
            status="ok", data={"raw_response": _FULL_DUMP}, duration_ms=0, step_name="repair"
        )
        assert dr._extract_patched_text(sr, pre) is None, (
            "a full-artifact dump with no markers must NOT fall back to "
            "full-replacement — that fallback is the bug being fixed"
        )


# ─── AC8: integration with UNCHANGED _validate_patched_text ─────────────────


class TestAC8IntegrationSyntaxErrorRejected:
    def test_ac8_invalid_patch_rejected_syntax_error(self) -> None:
        dr = _dr()
        pre = "def f():\n    return 1\n"
        blocks = [("return 1", "return (")]
        patched = dr._apply_search_replace(pre, blocks)
        assert patched is not None, f"expected an applied patch; got {patched!r}"
        reason = dr._validate_patched_text("artifact.py", pre, patched)
        assert reason == "syntax_error", f"got {reason!r}"


# ─── AC10: circuit-breaker threshold helper ──────────────────────────────────


class TestAC10RepairBatchErrorThreshold:
    def test_ac10_cfg_override_default_and_clamp(self) -> None:
        dr = _dr()
        assert dr._repair_batch_error_threshold(
            {"directed_repair_batch_error_threshold": 5}
        ) == 5
        assert dr._repair_batch_error_threshold({}) == 3
        assert dr._repair_batch_error_threshold(
            {"directed_repair_batch_error_threshold": 0}
        ) == 3


# ─── AC11: circuit-breaker early abort ───────────────────────────────────────


class TestAC11CircuitBreakerEarlyAbort:
    def test_ac11_k_consecutive_failures_trip_breaker_early(
        self, tmp_path, monkeypatch
    ) -> None:
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("line one\nline two\nline three\n", encoding="utf-8")
        calls: list = []

        def fake_invoke(*args, **kwargs):
            calls.append(kwargs)
            return StepResult(
                status="ok", data={"raw_response": _FULL_DUMP}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=10,
            ctx=_make_ctx(str(tmp_path), directed_repair_batch_error_threshold=3),
        )

        assert rr.exhausted_reason == "batch_circuit_breaker", (
            f"expected early breaker trip; got {rr.exhausted_reason!r}"
        )
        assert rr.converged is False
        assert len(calls) <= 3, (
            f"LLM invoked {len(calls)} times — breaker (K=3) must abort early, "
            "not burn every attempt/batch"
        )


# ─── AC12: counter reset on success ───────────────────────────────────────────


class TestAC12CircuitBreakerResetsOnSuccess:
    def test_ac12_success_resets_counter_delaying_trip(
        self, tmp_path, monkeypatch
    ) -> None:
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        responses = [
            _FULL_DUMP,                              # attempt1: failure (1)
            _valid_patch_raw("beta", "BETA-FIXED"),   # attempt2: success (reset to 0)
            _FULL_DUMP,                               # attempt3: failure (1)
            _FULL_DUMP,                               # attempt4: failure (2) -> trip
        ]
        calls: list = []

        def fake_invoke(*args, **kwargs):
            idx = len(calls)
            calls.append(kwargs)
            raw = responses[idx] if idx < len(responses) else _FULL_DUMP
            return StepResult(
                status="ok", data={"raw_response": raw}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=4,
            ctx=_make_ctx(str(tmp_path), directed_repair_batch_error_threshold=2),
        )

        assert rr.exhausted_reason == "batch_circuit_breaker", (
            f"reset must delay the trip to attempt 4 (not attempt 2); "
            f"got {rr.exhausted_reason!r}"
        )
        assert len(calls) == 4, (
            f"expected exactly 4 LLM calls (trip only after reset + 2 more "
            f"failures); got {len(calls)}"
        )


# ─── AC13: circuit-breaker telemetry ─────────────────────────────────────────


class TestAC13CircuitBreakerTelemetry:
    def test_ac13_breaker_trip_emits_telemetry_exactly_once(
        self, tmp_path, monkeypatch
    ) -> None:
        dr = _dr()
        events: list = []

        def spy(event_type, payload, *args, **kwargs):
            events.append((event_type, payload))

        monkeypatch.setattr(telemetry_ctx, "emit_safe", spy)

        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\n", encoding="utf-8")

        def fake_invoke(*args, **kwargs):
            return StepResult(
                status="ok", data={"raw_response": _FULL_DUMP}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=6,
            ctx=_make_ctx(str(tmp_path), directed_repair_batch_error_threshold=2),
        )

        names = [name for name, _ in events]
        assert names.count("directed_repair_circuit_breaker") == 1, (
            f"expected exactly one breaker-trip event; got {names!r}"
        )
        payload = dict(events)["directed_repair_circuit_breaker"]
        for key in ("gate", "consecutive_failures", "threshold"):
            assert key in payload, f"missing {key!r} in breaker payload {payload!r}"
        assert payload["gate"] == "spec_lint", f"got {payload!r}"
        assert payload["threshold"] == 2, f"got {payload!r}"


# ─── AC14: §1n taxonomy preserved (DEFER paths) ──────────────────────────────


class TestAC14TaxonomyPreserved:
    def test_ac14a_attempts_exhaustion_reason_preserved_when_no_breaker_trip(
        self, tmp_path, monkeypatch
    ) -> None:
        """Patches apply+validate fine every attempt (never a failure per the
        breaker's own definition) but the gate never converges -> the
        pre-existing "attempts" reason is unaffected by the new breaker.

        Idempotent no-op patch (SEARCH==REPLACE) so EVERY attempt genuinely
        SUCCEEDS an identical batch (disk content never drifts, so SEARCH
        never stops matching) -> the failure counter never climbs. A
        non-idempotent patch (e.g. beta->BETA-FIXED) would apply on attempt 1
        only; attempt 2's SEARCH would then find 0 occurrences on disk ->
        inapplicable -> FAILURE -> a spec-correct breaker would trip instead
        of exhausting by attempts."""
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\n", encoding="utf-8")

        def fake_invoke(*args, **kwargs):
            return StepResult(
                status="ok",
                data={"raw_response": _valid_patch_raw("alpha", "alpha")},
                duration_ms=1, step_name=kwargs.get("step_name", "repair"),
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=3,
            # Belt-and-suspenders: threshold far above max_attempts so even a
            # miscounted failure could not accidentally trip the breaker.
            ctx=_make_ctx(str(tmp_path), directed_repair_batch_error_threshold=50),
        )
        assert rr.exhausted_reason == "attempts", f"got {rr.exhausted_reason!r}"
        assert rr.converged is False

    def test_ac14b_model_unavailable_reason_preserved_not_counted_as_breaker_failure(
        self, tmp_path, monkeypatch
    ) -> None:
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\n", encoding="utf-8")

        def fake_invoke(*args, **kwargs):
            return StepResult(
                status="error", data={}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
                error="rate_limit_error", error_code="E_LLM_EXIT", recoverable=True,
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        def _gate_must_not_run() -> StepResult:
            pytest.fail("gate must not be re-run when the model is unavailable")

        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_gate_must_not_run,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=5,
            ctx=_make_ctx(
                str(tmp_path),
                directed_repair_batch_error_threshold=1,
                directed_repair_fallback_model="claude-fable-5",
            ),
        )
        assert rr.exhausted_reason == "model_unavailable", (
            f"model-unavailable iterations must not be miscounted as breaker "
            f"failures (threshold=1 would trip on the very first call if so); "
            f"got {rr.exhausted_reason!r}"
        )
        assert rr.converged is False


# ─── AC15: §1o consumer-transparency on breaker trip ─────────────────────────


class TestAC15ConsumerTransparencyOnBreakerTrip:
    def test_ac15_breaker_trip_return_shape_converged_false_final_none(
        self, tmp_path, monkeypatch
    ) -> None:
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\n", encoding="utf-8")

        def fake_invoke(*args, **kwargs):
            return StepResult(
                status="ok", data={"raw_response": _FULL_DUMP}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=8,
            ctx=_make_ctx(str(tmp_path), directed_repair_batch_error_threshold=2),
        )
        assert rr.converged is False, f"got {rr.converged!r}"
        assert rr.final is None, f"got {rr.final!r}"
        assert rr.exhausted_reason == "batch_circuit_breaker", (
            f"a consumer dispatching only on converged/final would fall through "
            f"correctly, but the reason itself must be the new breaker value; "
            f"got {rr.exhausted_reason!r}"
        )


# ─── AC16: §1n taxonomy preserved — no_progress, breaker not tripped ─────────


class TestAC16NoProgressPreservedWhenBreakerNotTripped:
    def test_ac16_no_progress_preserved_when_breaker_not_tripped(
        self, tmp_path, monkeypatch
    ) -> None:
        """Every LLM call errors (not model-unavailable) -> the pre-existing
        end-of-attempt no_progress fail-fast fires. Threshold set HIGH (5)
        so the (<=max_attempts=2) failure count never reaches it -> the
        DEFER'd "no_progress" reason must survive unchanged."""
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\n", encoding="utf-8")

        def fake_invoke(*args, **kwargs):
            return StepResult(
                status="error", data={}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
                error="some other real error", error_code="E_LLM_EXIT",
                recoverable=True,
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=2,
            ctx=_make_ctx(str(tmp_path), directed_repair_batch_error_threshold=5),
        )
        assert rr.exhausted_reason == "no_progress", f"got {rr.exhausted_reason!r}"
        assert rr.converged is False


# ─── AC17: breaker checks per-batch mid-attempt, pre-empts no_progress ───────


class TestAC17MultibatchMidAttemptBreakerPrecedence:
    def test_ac17_multibatch_midattempt_breaker_precedence(
        self, tmp_path, monkeypatch
    ) -> None:
        """5 findings, batch_size=1 -> 5 batches within attempt 1 alone. Every
        batch's LLM response is an inapplicable full-artifact dump (no
        markers) -> FAILURE each batch. threshold=3 must trip the breaker
        AFTER THE 3RD BATCH, mid-attempt-1 — proving the check runs per
        BATCH, not only at attempt boundaries (which would otherwise let all
        5 batches run and fall through to the end-of-attempt no_progress
        path with 5 LLM calls)."""
        dr = _dr()
        artifact = tmp_path / "spec.md"
        artifact.write_text("alpha\nbeta\ngamma\ndelta\nepsilon\n", encoding="utf-8")
        calls: list = []

        def fake_invoke(*args, **kwargs):
            calls.append(kwargs)
            return StepResult(
                status="ok", data={"raw_response": _FULL_DUMP}, duration_ms=1,
                step_name=kwargs.get("step_name", "repair"),
            )

        monkeypatch.setattr(dr, "invoke_llm_subprocess", fake_invoke)

        findings = [
            {"path": str(artifact), "line": i, "rule": "r", "evidence": f"e{i}"}
            for i in range(1, 6)
        ]
        rr = dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=findings,
            rerun_gate=_err_rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=2,
            ctx=_make_ctx(
                str(tmp_path),
                directed_repair_batch_error_threshold=3,
                directed_repair_batch_size=1,
            ),
        )
        assert rr.exhausted_reason == "batch_circuit_breaker", (
            f"got {rr.exhausted_reason!r}"
        )
        assert len(calls) == 3, (
            f"expected exactly 3 LLM calls — the breaker must trip mid-attempt "
            f"after the 3rd of 5 batches, pre-empting the remaining 2 batches "
            f"and the end-of-attempt no_progress fallback; got {len(calls)}"
        )
