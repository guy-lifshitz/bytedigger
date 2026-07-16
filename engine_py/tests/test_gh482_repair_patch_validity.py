"""RED tests for GH482 — directed-repair patch-validity gate.

Spec: SHARED/memory/Decisions/2026-07-10_GH482_repair_patch_validity_spec.md
ACs 1-9 (spec §3). Pre-GREEN expected:
  - AC1-5, AC7-9 FAIL (no `_validate_patched_text` guard exists yet; today
    `_run_repair_loop` writes whatever prose the model returns and (for AC1-5
    on a syntax-broken/collapsed patch) either converges falsely or leaves
    the corrupted artifact on disk).
  - AC6 is a regression guard for the unchanged converge-on-valid-patch path
    and may already PASS today — that is acceptable (§1l).

Conventions: `lib.directed_repair` exists today and is imported at module
top-level (collectable) via `importlib.import_module`, matching sibling
test_457DC7DC_directed_repair.py. The NOT-YET-EXISTING symbol
`_validate_patched_text` is resolved INSIDE test bodies via
`getattr(dr_mod, "_validate_patched_text", None)` (§1q ext / D1CF5FDF
collectability guard) so the file always COLLECTS even before GREEN. No
module-level sys.path manipulation, no conftest import (§1q). Every LLM seam
is stubbed (burn-guard); telemetry is spied via `telemetry_ctx.emit_safe`.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

import telemetry_ctx  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (mirrors test_457DC7DC_directed_repair.py) ───────────────


def _load_directed_repair():
    return importlib.import_module("lib.directed_repair")


def _get_validate_fn(dr_mod):
    """Presence gate (§1q ext / D1CF5FDF): resolved INSIDE the test body, not
    at module import time, so a missing symbol fails at ASSERT time (fast),
    never at COLLECT time (hang)."""
    fn = getattr(dr_mod, "_validate_patched_text", None)
    assert fn is not None, (
        "lib.directed_repair._validate_patched_text not yet implemented — "
        "GH482 §2.1 guard helper absent (expected RED fail)"
    )
    return fn


def _make_ctx(git_cwd: str, **extra_cfg: Any) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, **extra_cfg},
        question="q",
        session_id="test-gh482",
        persona="hal",
        framework=None,
        domain=None,
    )


def _rr(rr: Any, key: str) -> Any:
    if isinstance(rr, dict):
        assert key in rr, f"DirectedRepairResult missing {key!r}: {rr!r}"
        return rr[key]
    assert hasattr(rr, key), f"DirectedRepairResult missing {key!r}: {rr!r}"
    return getattr(rr, key)


def _patch_invoke(monkeypatch, dr_mod, patched_text: str, calls: list) -> None:
    """Stub the LLM seam on the directed_repair module (never a real call)."""
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
        import llm_subprocess  # noqa: PLC0415
        monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", fake_invoke)
        patched = True
    except ImportError:
        pass
    assert patched, "could not stub invoke_llm_subprocess — refusing to risk a real LLM call"


def _install_emit_spy(monkeypatch, dr_mod) -> list:
    events: list = []

    def spy(event_type, payload, *args, **kwargs):
        events.append((event_type, payload))

    monkeypatch.setattr(telemetry_ctx, "emit_safe", spy)
    for attr in ("emit_safe", "_emit_safe"):
        if hasattr(dr_mod, attr):
            monkeypatch.setattr(dr_mod, attr, spy)
    return events


_VALID_PY_1400 = (
    "def compute(a, b):\n"
    "    total = a + b\n"
    "    for i in range(10):\n"
    "        total += i\n"
    "    return total\n"
    + "# padding line to reach byte floor\n" * 40
)


def _rerun_gate_ok() -> StepResult:
    return StepResult(
        status="ok", data={"findings": []}, duration_ms=0, step_name="gate_ok",
    )


# ─── AC1/2/3: prose response destroys a .py artifact ──────────────────────


class TestAC1UnchangedOnDisk:
    """AC1 — .py artifact >=1400 bytes valid python; model returns a prose
    sentence; file content on disk must remain UNCHANGED after the loop."""

    def test_ac1_prose_response_leaves_py_artifact_unchanged_on_disk(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dr = _load_directed_repair()
        assert len(_VALID_PY_1400.encode("utf-8")) >= 1400
        artifact = tmp_path / "impl.py"
        artifact.write_text(_VALID_PY_1400, encoding="utf-8")
        gate_calls: list = []
        _patch_invoke(
            monkeypatch, dr,
            "The issue is caused by a missing null check in the validator.",
            [],
        )

        def rerun_gate() -> StepResult:
            gate_calls.append(1)
            return _rerun_gate_ok()

        dr.attempt_directed_repair(
            gate="green_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1,
                       "rule": "green-482", "evidence": "defect"}],
            rerun_gate=rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_green_lint",
            max_attempts=2,
            ctx=_make_ctx(str(tmp_path)),
        )

        assert artifact.read_text(encoding="utf-8") == _VALID_PY_1400, (
            "a prose response must never overwrite the on-disk artifact "
            f"(GH482 destroy-artifact bug); got {artifact.read_text(encoding='utf-8')!r}"
        )


class TestAC2RejectionTelemetry:
    """AC2 — the rejected prose patch emits directed_repair_patch_rejected
    with reason=='syntax_error' and the required payload keys."""

    def test_ac2_syntax_error_rejection_emits_telemetry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dr = _load_directed_repair()
        artifact = tmp_path / "impl.py"
        artifact.write_text(_VALID_PY_1400, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n    return total\n=======\n    return total )\n>>>>>>> REPLACE\n",
            [],
        )

        dr.attempt_directed_repair(
            gate="green_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1,
                       "rule": "green-482", "evidence": "defect"}],
            rerun_gate=_rerun_gate_ok,
            cheap_model="claude-fable-5",
            repair_step_name="repair_green_lint",
            max_attempts=2,
            ctx=_make_ctx(str(tmp_path)),
        )

        names = [name for name, _ in events]
        assert "directed_repair_patch_rejected" in names, (
            f"missing directed_repair_patch_rejected event; got {names!r}"
        )
        payload = dict(events)["directed_repair_patch_rejected"]
        assert payload.get("reason") == "syntax_error", (
            f"expected reason='syntax_error'; got {payload!r}"
        )
        for key in ("gate", "attempt", "pre_bytes", "post_bytes"):
            assert key in payload, (
                f"directed_repair_patch_rejected payload missing {key!r}; got {payload!r}"
            )


class TestAC3NoFalseConvergence:
    """AC3 — a prose response must never fabricate convergence: no
    directed_repair_converged event, rerun_gate never invoked, converged is
    False."""

    def test_ac3_prose_response_never_converges_gate_never_rerun(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dr = _load_directed_repair()
        artifact = tmp_path / "impl.py"
        artifact.write_text(_VALID_PY_1400, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n    return total\n=======\n    return total )\n>>>>>>> REPLACE\n",
            [],
        )

        gate_calls: list = []

        def rerun_gate() -> StepResult:
            gate_calls.append(1)
            return _rerun_gate_ok()

        rr = dr.attempt_directed_repair(
            gate="green_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1,
                       "rule": "green-482", "evidence": "defect"}],
            rerun_gate=rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_green_lint",
            max_attempts=2,
            ctx=_make_ctx(str(tmp_path)),
        )

        names = [name for name, _ in events]
        assert "directed_repair_converged" not in names, (
            f"prose response must never converge; got events {names!r}"
        )
        assert gate_calls == [], (
            f"rerun_gate must NEVER be invoked on a rejected patch; got {gate_calls!r} calls"
        )
        assert _rr(rr, "converged") is False, f"expected converged=False; got {rr!r}"


class TestAC4ExhaustionAfterRejections:
    """AC4 — with max_attempts=2 and a permanently-rejected patch: exactly 2
    directed_repair_patch_rejected events; directed_repair_exhausted with
    attempts==2, reason=='attempts'."""

    def test_ac4_two_rejections_then_exhausted_attempts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dr = _load_directed_repair()
        artifact = tmp_path / "impl.py"
        artifact.write_text(_VALID_PY_1400, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        _patch_invoke(
            monkeypatch, dr,
            "<<<<<<< SEARCH\n    return total\n=======\n    return total )\n>>>>>>> REPLACE\n",
            [],
        )

        rr = dr.attempt_directed_repair(
            gate="green_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1,
                       "rule": "green-482", "evidence": "defect"}],
            rerun_gate=_rerun_gate_ok,
            cheap_model="claude-fable-5",
            repair_step_name="repair_green_lint",
            max_attempts=2,
            ctx=_make_ctx(str(tmp_path)),
        )

        names = [name for name, _ in events]
        rejected_count = sum(1 for n in names if n == "directed_repair_patch_rejected")
        assert rejected_count == 2, (
            f"expected exactly 2 directed_repair_patch_rejected events; got {rejected_count} "
            f"in {names!r}"
        )
        assert "directed_repair_exhausted" in names, (
            f"missing directed_repair_exhausted event; got {names!r}"
        )
        exhausted = dict(events)["directed_repair_exhausted"]
        assert exhausted.get("attempts") == 2, (
            f"expected attempts==2; got {exhausted!r}"
        )
        assert exhausted.get("reason") == "attempts", (
            f"expected reason=='attempts'; got {exhausted!r}"
        )
        assert _rr(rr, "converged") is False, f"expected converged=False; got {rr!r}"


class TestAC5SizeCollapseMarkdown:
    """AC5 — .md artifact >=200 bytes; model returns <50% of pre-size text
    ⇒ rejected with reason=='size_collapse', file unchanged."""

    def test_ac5_markdown_size_collapse_rejected_file_unchanged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dr = _load_directed_repair()
        original = "# Spec\n" + ("detailed content line here\n" * 20)
        assert len(original.encode("utf-8")) >= 200
        artifact = tmp_path / "spec.md"
        artifact.write_text(original, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        tiny_patch = "ok"
        assert len(tiny_patch.encode("utf-8")) < 0.5 * len(original.encode("utf-8"))
        patch_block = (
            "<<<<<<< SEARCH\n" + original + "=======\n" + tiny_patch
            + "\n>>>>>>> REPLACE\n"
        )
        _patch_invoke(monkeypatch, dr, patch_block, [])

        dr.attempt_directed_repair(
            gate="spec_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 1,
                       "rule": "unverified-line-range", "evidence": "defect"}],
            rerun_gate=_rerun_gate_ok,
            cheap_model="claude-fable-5",
            repair_step_name="repair_spec_lint",
            max_attempts=1,
            ctx=_make_ctx(str(tmp_path)),
        )

        assert artifact.read_text(encoding="utf-8") == original, (
            "size-collapsed patch must never be written to disk; got "
            f"{artifact.read_text(encoding='utf-8')!r}"
        )
        names = [name for name, _ in events]
        assert "directed_repair_patch_rejected" in names, (
            f"missing directed_repair_patch_rejected event; got {names!r}"
        )
        payload = dict(events)["directed_repair_patch_rejected"]
        assert payload.get("reason") == "size_collapse", (
            f"expected reason='size_collapse'; got {payload!r}"
        )


# ─── AC6: regression guard — valid patch still converges ──────────────────


class TestAC6ValidPatchStillConverges:
    """AC6 — .py artifact, model returns VALID python of similar size,
    rerun_gate returns an ok StepResult ⇒ file rewritten, converged=True
    (regression guard; may already PASS today)."""

    def test_ac6_valid_python_patch_still_converges_and_is_written(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dr = _load_directed_repair()
        artifact = tmp_path / "impl.py"
        artifact.write_text(_VALID_PY_1400, encoding="utf-8")
        events = _install_emit_spy(monkeypatch, dr)
        patched_valid = _VALID_PY_1400.replace("total = a + b", "total = a + b + 1")

        gate_calls: list = []

        def rerun_gate() -> StepResult:
            gate_calls.append(1)
            return StepResult(
                status="ok", data={"findings": []}, duration_ms=0,
                step_name="verify_green_lint_rules",
            )

        patch_block = (
            "<<<<<<< SEARCH\n    total = a + b\n=======\n    total = a + b + 1\n"
            ">>>>>>> REPLACE\n"
        )
        _patch_invoke(monkeypatch, dr, patch_block, [])

        rr = dr.attempt_directed_repair(
            gate="green_lint",
            artifact_path=str(artifact),
            findings=[{"path": str(artifact), "line": 2,
                       "rule": "green-482", "evidence": "defect"}],
            rerun_gate=rerun_gate,
            cheap_model="claude-fable-5",
            repair_step_name="repair_green_lint",
            max_attempts=2,
            ctx=_make_ctx(str(tmp_path)),
        )

        assert artifact.read_text(encoding="utf-8") == patched_valid, (
            f"valid patch must be written to disk; got {artifact.read_text(encoding='utf-8')!r}"
        )
        names = [name for name, _ in events]
        assert "directed_repair_converged" in names, (
            f"missing directed_repair_converged event; got {names!r}"
        )
        assert gate_calls == [1], "gate must be re-run exactly once on convergence"
        assert _rr(rr, "converged") is True, f"expected converged=True; got {rr!r}"


# ─── AC7/8/9: direct unit tests of _validate_patched_text ─────────────────


class TestAC7SizeGateSkippedUnderFloor:
    """AC7 — pre-text under the byte floor (200) skips the size-collapse
    gate entirely: returns None even for a tiny patch."""

    def test_ac7_tiny_pretext_under_floor_returns_none(self) -> None:
        dr = _load_directed_repair()
        fn = _get_validate_fn(dr)
        pre_text = "short\n"  # well under 200 bytes
        assert len(pre_text.encode("utf-8")) < 200
        patched = "x"
        result = fn("x.md", pre_text, patched)
        assert result is None, (
            f"size gate must be skipped under the byte floor; got {result!r}"
        )


class TestAC8SizeGateAppliesToParsablePy:
    """AC8 — .py pre-text >=200 bytes that parses; a VALID-python but
    <50%-size patch is still rejected with reason=='size_collapse'."""

    def test_ac8_valid_py_size_collapse_still_rejected(self) -> None:
        dr = _load_directed_repair()
        fn = _get_validate_fn(dr)
        pre_text = "def f():\n    return 1\n" + ("# pad\n" * 200)
        assert len(pre_text.encode("utf-8")) >= 1000
        patched = "def g():\n    return 2\n"
        assert len(patched.encode("utf-8")) < 100
        assert len(patched.encode("utf-8")) < 0.5 * len(pre_text.encode("utf-8"))
        result = fn("x.py", pre_text, patched)
        assert result == "size_collapse", (
            f"size gate must apply to parsable .py; got {result!r}"
        )


class TestAC9SyntaxErrorGate:
    """AC9 — direct call with unparsable python patch ⇒ reason=='syntax_error'."""

    def test_ac9_unparsable_python_patch_returns_syntax_error(self) -> None:
        dr = _load_directed_repair()
        fn = _get_validate_fn(dr)
        pre_text = _VALID_PY_1400
        patched = "not python ("
        result = fn("x.py", pre_text, patched)
        assert result == "syntax_error", (
            f"unparsable python must be rejected as syntax_error; got {result!r}"
        )
