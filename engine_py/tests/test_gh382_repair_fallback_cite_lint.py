"""RED tests for GH382 — directed-repair model fallback + cite-lint
planned-symbol downgrade.

Spec: SHARED/memory/Decisions/2026-07-07_GH382_directed_repair_fallback_cite_lint_spec.md
§3 AC A1-A7 (lib/directed_repair.py fallback model) and B1-B5 (spec_cite.py
planned-symbol downgrade).

Conventions: `lib.directed_repair` and `spec_cite` ALREADY EXIST today, so
plain top-level module imports are safe (§1q only forbids importing symbols
that don't exist yet). The NEW symbols under test
(`_sr_model_unavailable`, `_resolve_directed_repair_fallback_model`,
`DirectedRepairResult.exhausted_reason`, the extended `_NEW_CONTEXT_RE`, and
the cross-line planned-symbol downgrade in `lint_spec`) are resolved INSIDE
test bodies via `getattr`/behavioral assertions, so absence fails at assert
time, never at collection time (§1q / D1CF5FDF).

Forcing functions today:
  - A1-A3, A6: `_sr_model_unavailable` / `_resolve_directed_repair_fallback_model`
    are absent from `lib.directed_repair` — `getattr(..., None)` is None,
    `_getattr_or_fail` raises a clean pytest.fail.
  - A4/A5: `_run_repair_loop` has no fallback-switch branch today — an
    "unavailable"-shaped StepResult from `invoke_llm_subprocess` is treated
    as a normal (non-patching) attempt; the gate is re-run every iteration
    regardless, and no `directed_repair_model_unavailable` event is emitted.
  - A7: `DirectedRepairResult` has no `exhausted_reason` field and
    `directed_repair_exhausted`'s payload carries no `reason` key.
  - B1/B2/B4: `_NEW_CONTEXT_RE` (spec_cite.py:34) only recognizes
    create/add/new/(new) — none of MODIFY/import/gains/extends/wired/becomes
    match, so absent symbols on those lines stay `unresolved_symbol`.
  - B2 additionally needs the cross-line planned-symbol set (not implemented)
    to downgrade a PLAIN citation of a symbol that is CREATE-cited elsewhere.
  - B5: the extended verb set is entirely absent from `_NEW_CONTEXT_RE` today.

Expected pre-GREEN: A1-A7 FAIL, B1/B2/B4/B5 FAIL (11 total) — B3 is a
regression pin and PASSES both pre- and post-GREEN (1 total). 11F / 1P.

No UUT (`_sr_model_unavailable`, `_run_repair_loop`, `attempt_directed_repair`,
`_resolve_directed_repair_fallback_model`, `lint_spec`, `scan_citations`,
`check_citation`) is mocked. Only the infra seams `invoke_llm_subprocess`
(module attr on `lib.directed_repair`) and `telemetry_ctx.emit_safe` are
monkeypatched, per spec Out-of-Role. §1i: N/A — no time/singleton resources;
all fixtures are pre-staged tmp_path repos and canned StepResult sequences.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.lib import directed_repair as directed_repair  # noqa: E402
from bytedigger_engine import spec_cite  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers ───────────────────────────────────────────────────────────


def _getattr_or_fail(mod: Any, name: str, ac: str):
    fn = getattr(mod, name, None)
    if fn is None:
        pytest.fail(f"{ac}: {mod.__name__}.{name} absent — GH382 not yet implemented")
    return fn


def _rr(rr: Any, key: str) -> Any:
    if isinstance(rr, dict):
        assert key in rr, f"DirectedRepairResult missing {key!r}: {rr!r}"
        return rr[key]
    assert hasattr(rr, key), f"DirectedRepairResult missing {key!r}: {rr!r}"
    return getattr(rr, key)


def _exhausted_reason(rr: Any) -> Any:
    if isinstance(rr, dict):
        return rr.get("exhausted_reason")
    return getattr(rr, "exhausted_reason", None)


def _make_ctx(git_cwd: str, **extra_cfg: Any) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, **extra_cfg},
        question="q",
        session_id="test-gh382",
        persona="hal",
        framework=None,
        domain=None,
    )


def _install_emit_spy(monkeypatch) -> list:
    events: list = []

    def spy(event_type, payload, *args, **kwargs):
        events.append((event_type, dict(payload) if isinstance(payload, dict) else payload))

    monkeypatch.setattr(telemetry_ctx, "emit_safe", spy)
    return events


def _patch_invoke_sequence(monkeypatch, responses: list[StepResult]) -> list:
    calls: list = []

    def fake_invoke(*args, **kwargs):
        calls.append(dict(kwargs))
        idx = len(calls) - 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(directed_repair, "invoke_llm_subprocess", fake_invoke)
    return calls


def _unavailable_sr(label: str) -> StepResult:
    return StepResult(
        status="error",
        data={"stderr_tail": "api_error_status=429"},
        duration_ms=1,
        step_name="repair",
        error=f"spend limit hit for {label}",
        error_code="E_LLM_EXIT",
    )


def _ok_patch_sr(text: str) -> StepResult:
    return StepResult(
        status="ok",
        data={"raw_response": text, "response_bytes": len(text), "command": ["stub"]},
        duration_ms=1,
        step_name="repair",
    )


# ─── A1-A3: _sr_model_unavailable ─────────────────────────────────────────────


def test_a1_sr_model_unavailable_true_on_error_message_marker() -> None:
    fn = _getattr_or_fail(directed_repair, "_sr_model_unavailable", "A1")
    sr = StepResult(
        status="error", data={}, duration_ms=0, step_name="repair",
        error="You've hit your monthly spend limit", error_code="E_LLM_EXIT",
    )
    assert fn(sr) is True, f"A1: expected True for spend-limit error message; got {fn(sr)!r}"


def test_a2_sr_model_unavailable_true_on_stderr_tail_marker() -> None:
    fn = _getattr_or_fail(directed_repair, "_sr_model_unavailable", "A2")
    sr = StepResult(
        status="error",
        data={"stderr_tail": "log line api_error_status=429 observed"},
        duration_ms=0, step_name="repair",
        error="generic subprocess failure", error_code="E_LLM_EXIT",
    )
    assert fn(sr) is True, f"A2: expected True for stderr_tail marker; got {fn(sr)!r}"


def test_a3_sr_model_unavailable_false_on_ok_and_on_unmarked_error() -> None:
    fn = _getattr_or_fail(directed_repair, "_sr_model_unavailable", "A3")
    ok_sr = StepResult(status="ok", data={"raw_response": "patch"}, duration_ms=0, step_name="repair")
    assert fn(ok_sr) is False, f"A3: expected False for an ok StepResult; got {fn(ok_sr)!r}"
    err_sr = StepResult(
        status="error", data={"stderr_tail": "generic timeout"}, duration_ms=0,
        step_name="repair", error="gate still failing", error_code="E_LLM_EXIT",
    )
    assert fn(err_sr) is False, f"A3: expected False for an unmarked error; got {fn(err_sr)!r}"


# ─── A6: fallback model resolver ─────────────────────────────────────────────


def test_a6_resolve_directed_repair_fallback_model_default_and_override() -> None:
    fn = _getattr_or_fail(directed_repair, "_resolve_directed_repair_fallback_model", "A6")
    assert fn({}) == "sonnet", f"A6: default fallback model must be 'sonnet'; got {fn({})!r}"
    override = fn({"directed_repair_fallback_model": "haiku"})
    assert override == "haiku", f"A6: cfg override must win; got {override!r}"


# ─── A4: first model dead, fallback converges, dead attempt not consumed ────


def test_a4_first_model_unavailable_falls_back_and_converges_without_consuming_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    events = _install_emit_spy(monkeypatch)
    artifact = tmp_path / "spec.md"
    artifact.write_text("defect\n", encoding="utf-8")
    calls = _patch_invoke_sequence(monkeypatch, [
        _unavailable_sr("claude-fable-5"),
        _ok_patch_sr("fixed via fallback\n"),
    ])
    gate_runs: list = []

    def rerun_gate() -> StepResult:
        gate_runs.append(1)
        return StepResult(status="ok", data={}, duration_ms=0, step_name="verify_spec_lint")

    rr = directed_repair.attempt_directed_repair(
        gate="spec_lint",
        artifact_path=str(artifact),
        findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
        rerun_gate=rerun_gate,
        cheap_model="claude-fable-5",
        repair_step_name="repair_spec_lint",
        max_attempts=2,
        ctx=_make_ctx(str(tmp_path)),
    )

    assert _rr(rr, "converged") is True, f"A4: expected converged True; got {rr!r}"
    assert _rr(rr, "attempts") == 1, f"A4: dead-model attempt must not be consumed; got {rr!r}"
    assert len(calls) == 2, f"A4: expected exactly 2 invoke_llm_subprocess calls; got {len(calls)}"
    assert calls[0].get("model") == "claude-fable-5", f"A4: first call must use original model; got {calls[0]!r}"
    assert calls[1].get("model") == "sonnet", f"A4: fallback call must use resolved fallback model; got {calls[1]!r}"
    assert len(gate_runs) == 1, f"A4: gate must only be re-run for the real (fallback) attempt; got {gate_runs!r}"
    unavailable_events = [p for n, p in events if n == "directed_repair_model_unavailable"]
    assert len(unavailable_events) == 1, f"A4: expected exactly 1 directed_repair_model_unavailable event; got {events!r}"
    assert unavailable_events[0].get("model") == "claude-fable-5", (
        f"A4: unavailable event must name the dead model; got {unavailable_events[0]!r}"
    )
    names = [n for n, _ in events]
    assert "directed_repair_converged" in names, f"A4: expected converged terminal event; got {names!r}"


# ─── A5: both models dead → bounded exhaustion, reason=model_unavailable ────


def test_a5_both_models_unavailable_exhausted_reason_model_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    events = _install_emit_spy(monkeypatch)
    artifact = tmp_path / "spec.md"
    artifact.write_text("defect\n", encoding="utf-8")
    calls = _patch_invoke_sequence(monkeypatch, [
        _unavailable_sr("claude-fable-5"),
        _unavailable_sr("sonnet"),
    ])
    gate_calls: list = []

    def rerun_gate() -> StepResult:
        gate_calls.append(1)
        return StepResult(
            status="error", data={}, duration_ms=0, step_name="verify_spec_lint",
            error="unexpected gate call", error_code="E_SPEC_LINT_FAIL",
        )

    rr = directed_repair.attempt_directed_repair(
        gate="spec_lint",
        artifact_path=str(artifact),
        findings=[{"path": str(artifact), "line": 1, "rule": "r", "evidence": "e"}],
        rerun_gate=rerun_gate,
        cheap_model="claude-fable-5",
        repair_step_name="repair_spec_lint",
        max_attempts=2,
        ctx=_make_ctx(str(tmp_path)),
    )

    assert _rr(rr, "converged") is False, f"A5: expected converged False; got {rr!r}"
    exhausted_reason = _exhausted_reason(rr)
    assert exhausted_reason == "model_unavailable", (
        f"A5: DirectedRepairResult.exhausted_reason must be 'model_unavailable'; got {exhausted_reason!r} from {rr!r}"
    )
    assert len(calls) == 2, f"A5: invoke_llm_subprocess must be called exactly twice (bounded); got {len(calls)}"
    assert gate_calls == [], f"A5: gate must never be re-run when both models are unavailable; calls={gate_calls!r}"
    unavailable_events = [p for n, p in events if n == "directed_repair_model_unavailable"]
    assert len(unavailable_events) == 2, f"A5: expected exactly 2 directed_repair_model_unavailable events; got {events!r}"
    exhausted = [p for n, p in events if n == "directed_repair_exhausted"]
    assert exhausted, f"A5: missing directed_repair_exhausted event; got {events!r}"
    assert exhausted[-1].get("reason") == "model_unavailable", (
        f"A5: exhausted payload must carry reason='model_unavailable'; got {exhausted[-1]!r}"
    )


# ─── A7: normal exhaustion regression, reason=attempts ──────────────────────


def test_a7_normal_exhaustion_reason_attempts_regression(tmp_path: Path, monkeypatch) -> None:
    events = _install_emit_spy(monkeypatch)
    artifact = tmp_path / "spec.md"
    artifact.write_text("still broken\n", encoding="utf-8")
    calls = _patch_invoke_sequence(monkeypatch, [
        _ok_patch_sr("patched but still broken v1\n"),
        _ok_patch_sr("patched but still broken v2\n"),
    ])

    def rerun_gate() -> StepResult:
        return StepResult(
            status="error", data={}, duration_ms=0, step_name="verify_spec_lint",
            error="gate still failing", error_code="E_SPEC_LINT_FAIL", recoverable=False,
        )

    rr = directed_repair.attempt_directed_repair(
        gate="spec_lint",
        artifact_path=str(artifact),
        findings=[{"path": str(artifact), "line": 2, "rule": "r", "evidence": "e"}],
        rerun_gate=rerun_gate,
        cheap_model="claude-fable-5",
        repair_step_name="repair_spec_lint",
        max_attempts=2,
        ctx=_make_ctx(str(tmp_path)),
    )

    assert _rr(rr, "converged") is False, f"A7: got {rr!r}"
    assert _rr(rr, "attempts") == 2, f"A7: attempts must equal max_attempts cap; got {rr!r}"
    exhausted_reason = _exhausted_reason(rr)
    assert exhausted_reason == "attempts", (
        f"A7: normal exhaustion must set exhausted_reason='attempts'; got {exhausted_reason!r}"
    )
    exhausted = [p for n, p in events if n == "directed_repair_exhausted"]
    assert exhausted and exhausted[-1].get("reason") == "attempts", (
        f"A7: exhausted payload must carry reason='attempts'; got {events!r}"
    )
    assert len(calls) == 2, f"A7: one repair LLM call per attempt (cap=2); got {len(calls)}"


# ─── B1: MODIFY-line new-context downgrade ──────────────────────────────────


def test_b1_modify_line_new_symbol_downgrade_exit_0(tmp_path: Path) -> None:
    """B1: MODIFY-line citing an absent symbol against an EXISTING file
    downgrades from unresolved_symbol to new_symbol (advisory); exit_code 0.

    Expected pre-GREEN: FAIL — _NEW_CONTEXT_RE lacks 'modify'/'import' verbs
    today, so the line is not is_new; fn_x absent -> unresolved_symbol, exit 1.
    """
    code_file = tmp_path / "a" / "b.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("def existing():\n    pass\n")

    spec_text = "MODIFY: `a/b.py` — import flag + `fn_x`\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, f"B1: expected exit 0, got {exit_code}; findings={findings!r}"
    fn_x = [f for f in findings if f.symbol == "fn_x"]
    assert fn_x and all(f.status == "new_symbol" for f in fn_x), (
        f"B1: expected 'fn_x' finding downgraded to new_symbol; got {findings!r}"
    )


# ─── B2: cross-line planned-symbol set closes the cross-product ────────────


def test_b2_cross_line_symbol_reuse_planned_set_downgrades_all_exit_0(tmp_path: Path) -> None:
    """B2: a symbol cited PLAIN on one line (existing file, absent there) AND
    CREATE-cited on another line (missing file) — the planned-symbol set
    closes the cross-product: ALL findings for that symbol downgrade
    (new_symbol / missing_file); exit_code 0.

    Expected pre-GREEN: FAIL — no cross-line planned-symbol set today; the
    plain-line citation stays unresolved_symbol, exit 1.
    """
    code_file = tmp_path / "a" / "b.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("def unrelated():\n    pass\n")
    # a/c.py deliberately absent.

    spec_text = (
        "Call `shared_sym` from `a/b.py` to process the request.\n"
        "CREATE `shared_sym` in `a/c.py` for the new pipeline.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, f"B2: expected exit 0, got {exit_code}; findings={findings!r}"
    shared = [f for f in findings if f.symbol == "shared_sym"]
    assert len(shared) == 2, f"B2: expected 2 findings for 'shared_sym'; got {findings!r}"
    assert all(f.status in ("new_symbol", "missing_file") for f in shared), (
        f"B2: ALL findings for a planned symbol must downgrade; got {shared!r}"
    )
    assert not any(f.status == "unresolved_symbol" for f in shared), (
        f"B2: no unresolved_symbol may remain for a planned symbol; got {shared!r}"
    )


# ─── B3: regression pin — genuinely stale citation keeps teeth ─────────────


def test_b3_genuinely_stale_citation_stays_unresolved_exit_1(tmp_path: Path) -> None:
    """B3 (regression pin — expected to PASS both pre- and post-GREEN): a
    citation with NO new-context line and NO missing-file sibling must stay
    'unresolved_symbol'; exit_code 1. Lint keeps teeth."""
    code_file = tmp_path / "a" / "b.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("def correct_name():\n    pass\n")

    spec_text = "Invoke `gone_fn` inside `a/b.py` to finish the request.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 1, f"B3: expected exit 1, got {exit_code}; findings={findings!r}"
    gone = [f for f in findings if f.symbol == "gone_fn"]
    assert gone and all(f.status == "unresolved_symbol" for f in gone), (
        f"B3: expected 'gone_fn' to stay unresolved_symbol; got {findings!r}"
    )


# ─── B4: repro-shape corpus (live spec lines 21/24/25) ──────────────────────


def test_b4_repro_shape_corpus_create_and_modify_lines_exit_0_zero_unresolved(
    tmp_path: Path,
) -> None:
    """B4: repro-shape corpus mirroring the live spec's CREATE + MODIFY lines
    — a CREATE line for a not-yet-existing file, and a MODIFY line citing
    that same symbol plus a second symbol against an EXISTING file — must
    yield exit_code 0 with zero unresolved_symbol findings.

    Expected pre-GREEN: FAIL — 'MODIFY'/'import' are not recognized
    new-context markers today, so `has_adverse_open`/`_polarity_on` on the
    MODIFY line stay unresolved_symbol, exit 1.
    """
    (tmp_path / "compile_posture_raw.py").write_text(
        "def compile_posture_raw():\n    pass\n"
    )
    # finding_polarity.py deliberately absent (CREATE target).

    spec_text = (
        "CREATE `finding_polarity.py` with `has_adverse_open`\n"
        "MODIFY `compile_posture_raw.py` — import flag + `has_adverse_open`; "
        "resolve `_polarity_on`\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, f"B4: expected exit 0, got {exit_code}; findings={findings!r}"
    unresolved = [f for f in findings if f.status == "unresolved_symbol"]
    assert unresolved == [], f"B4: expected zero unresolved_symbol findings; got {findings!r}"


# ─── B5: extended verb set on _NEW_CONTEXT_RE ───────────────────────────────


def test_b5_new_context_regex_matches_new_verbs_not_resolve_or_bare_line() -> None:
    """B5: _NEW_CONTEXT_RE must match 'gains', 'extends', 'wired', 'becomes',
    'Modifies' (case-insensitive) and must NOT match 'resolve' or a bare
    citation line with no context marker.

    Expected pre-GREEN: FAIL — today's regex only recognizes
    create/add/new/(new); none of gains/extends/wired/becomes/modifies match.
    """
    positive = [
        "The helper gains a new parameter for validation.",
        "This extends the existing contract with a flag.",
        "The retry loop was wired into the caller.",
        "The result becomes final after the second pass.",
        "Modifies the posture check to add a guard.",
    ]
    for line in positive:
        assert spec_cite._NEW_CONTEXT_RE.search(line), (
            f"B5: expected _NEW_CONTEXT_RE to match new-context verb line: {line!r}"
        )

    negative = [
        "Call `resolve` from `foo.py` to validate the request.",
        "The function returns `bar` from `foo.py` unchanged.",
    ]
    for line in negative:
        assert not spec_cite._NEW_CONTEXT_RE.search(line), (
            f"B5: expected _NEW_CONTEXT_RE to NOT match: {line!r}"
        )
