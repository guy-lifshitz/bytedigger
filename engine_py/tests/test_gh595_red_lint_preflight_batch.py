"""RED tests for GH595 — red-lint pre-flight batch (single-pass RED-lint collector).

Frozen spec: SHARED/memory/Decisions/2026-07-11_GH595_red_lint_preflight_batch_spec.md

All tests MUST FAIL until GREEN implements:
  - `_collect_red_lint_findings(resolved_paths, git_cwd, ctx, cfg)` in
    workflows/phase_5_implement.py (§2.1, new named helper — §1aa).
  - The batch dispatcher inside `_verify_red_lint_rules` (§2.2) that collects
    ALL content-lint violations before invoking a single directed-repair call
    and reports `data["preflight_error_codes"]` / `data["preflight_findings"]`.

§1q/D1CF5FDF: `_collect_red_lint_findings` does not exist yet — every test
referencing it imports it INSIDE the test body (deferred import), so the
file COLLECTS cleanly and each test FAILS at assert time (ImportError or
assertion), never at collection time. No module-level sys.path.insert /
`from conftest import` here — sys.path wiring is provided by conftest.py
(§1q / 81F97F3D gate).

§1i: no singleton/time-dependent resources; fixtures are written deterministically
to tmp_path before invoking the UUT (`_verify_red_lint_rules`) — no racing.

UUT (`_verify_red_lint_rules`, `_collect_red_lint_findings`) is never mocked
(§1l). `attempt_directed_repair` (a dependency) is monkeypatched on the
`phase_5_implement` module per the existing sibling pattern
(test_7AD3D393_stub_passability.py::test_ac12).
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

from contracts import StepResult, WorkflowContext


# ─── shared helpers (mirror test_7AD3D393_stub_passability.py) ──────────────

def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-gh595",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths: list) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": "scratchpad/spec.md",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _write_test_file(tmp_path: Path, relpath: str, content: str) -> str:
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return relpath


# Double-violation fixture: suite-safe (no sys.path.insert / from conftest
# import) mock-the-UUT pattern (import + patch.object of the same symbol,
# triggers stub-passability) AND a §1q exec-import token on a separate line
# (built via concatenation so this test FILE's own source never contains the
# literal pattern in one piece — data, not code).
_EXEC_IMPORT_TOKEN = "spec_from_file_location" + "(__name__, " + '"x.py")'
_DOUBLE_VIOLATION_CONTENT = (
    "from unittest.mock import patch\n"
    "import helper_mod\n"
    "from helper_mod import target_fn\n"
    "\n"
    "\n"
    "def test_mock_uut():\n"
    "    with patch.object(helper_mod, \"target_fn\", return_value=42):\n"
    "        result = target_fn()\n"
    "    assert result is not None\n"
    "\n"
    "\n"
    "def test_exec_loader_reference():\n"
    "    # incidental reference: " + _EXEC_IMPORT_TOKEN + "\n"
    "    assert True\n"
)

# Clean fixture — no stub-passability, no §1q, no suite-safety hits.
_CLEAN_CONTENT = (
    "from unittest.mock import patch\n"
    "import helper_mod\n"
    "from helper_mod import target_fn\n"
    "import side_dep_mod\n"
    "\n"
    "\n"
    "def test_patches_only_dep():\n"
    "    with patch.object(side_dep_mod, \"side_dep\", return_value=0):\n"
    "        result = target_fn()\n"
    "    assert result is not None\n"
)

# Suite-safety-only violation (from conftest import), no mock-UUT, no §1q token.
_SUITE_UNSAFE_ONLY_CONTENT = (
    "from conftest import something\n"
    "\n"
    "\n"
    "def test_x():\n"
    "    assert something\n"
)


def _capture_emit_factory():
    captured: list[tuple] = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    return captured, _capture


# ─── AC1 ─────────────────────────────────────────────────────────────────

def test_ac1_batched_terminal_result_has_both_codes_and_and_semantics(
    tmp_path, monkeypatch
) -> None:
    """AC1: double-violation fixture, repair disabled (HAL_DIRECTED_REPAIR=0)
    -> single terminal StepResult with error_code == E_RED_STUB_PASSABLE
    (primary, canonical order), data["preflight_error_codes"] ==
    ["E_RED_STUB_PASSABLE", "E_RED_1Q_EXEC_IMPORT"] (list, canonical order,
    NOT set), error mentions both codes, recoverable is False.
    """
    from phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    # GH602: delta-retry pinned off — this test asserts the terminal AND-semantics path
    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.error_code == "E_RED_STUB_PASSABLE", (
        f"expected primary code E_RED_STUB_PASSABLE, got {result.error_code!r}"
    )
    assert result.data is not None and result.data.get("preflight_error_codes") == [
        "E_RED_STUB_PASSABLE",
        "E_RED_1Q_EXEC_IMPORT",
    ], f"expected canonical-order batch codes, got {result.data!r}"
    assert "E_RED_STUB_PASSABLE" in (result.error or "") and "E_RED_1Q_EXEC_IMPORT" in (result.error or ""), (
        f"expected both codes cited in error message, got {result.error!r}"
    )
    assert result.recoverable is False, f"expected recoverable=False, got {result.recoverable!r}"


# ─── AC2 ─────────────────────────────────────────────────────────────────

def test_ac2_directed_repair_invoked_exactly_once_with_combined_findings(
    tmp_path, monkeypatch
) -> None:
    """AC2: double-violation fixture, HAL_DIRECTED_REPAIR=1, attempt_directed_repair
    mocked non-convergent -> called EXACTLY once with gate=="red_lint_preflight"
    and findings covering both rules {"stub-passability", "1q-exec-import"}.
    """
    import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    calls: list[dict] = []

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=False, final=None, attempts=2, exhausted_reason="model_unavailable")

    with patch.object(p5, "attempt_directed_repair", side_effect=_fake_repair):
        p5._verify_red_lint_rules(ctx, prev)

    assert len(calls) == 1, f"expected exactly 1 directed-repair call, got {len(calls)}"
    assert calls[0]["gate"] == "red_lint_preflight", (
        f"expected gate='red_lint_preflight', got {calls[0].get('gate')!r}"
    )
    rules = {f.get("rule") for f in calls[0]["findings"]}
    assert {"stub-passability", "1q-exec-import"}.issubset(rules), (
        f"expected both rules in combined findings, got {rules!r}"
    )


# ─── AC3 ─────────────────────────────────────────────────────────────────

def test_ac3_clean_fixture_happy_path_preserves_prev_data(tmp_path, monkeypatch) -> None:
    """AC3: clean fixture (no violations) -> status=='ok', prev.data keys
    passed through in the returned StepResult.data."""
    from helpers.host_tools import skip_without
    skip_without("semgrep")
    from phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_clean.py", _CLEAN_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status!r}; error={result.error!r}"
    for key in ("red_log_path", "spec_path", "cycle"):
        assert key in (result.data or {}), f"expected prev.data key {key!r} passed through, got {result.data!r}"


# ─── AC4 ─────────────────────────────────────────────────────────────────

def test_ac4_master_kill_switch_off_reverts_to_legacy_first_fail(
    tmp_path, monkeypatch
) -> None:
    """AC4: HAL_RED_LINT_PREFLIGHT_BATCH=0 + double-violation fixture ->
    legacy first-fail: error_code==E_RED_STUB_PASSABLE, data WITHOUT
    preflight_error_codes, and gate_disabled(gate='HAL_RED_LINT_PREFLIGHT_BATCH')
    emitted."""
    import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_STUB_PASSABLE", (
        f"expected legacy first-fail code, got {result.error_code!r}"
    )
    assert "preflight_error_codes" not in (result.data or {}), (
        f"expected legacy path (no preflight_error_codes key), got {result.data!r}"
    )
    disabled_calls = [c for c in captured if c[0] == "gate_disabled" and c[1].get("gate") == "HAL_RED_LINT_PREFLIGHT_BATCH"]
    assert disabled_calls, f"expected gate_disabled(HAL_RED_LINT_PREFLIGHT_BATCH) emission, got events={captured!r}"


# ─── AC5 ─────────────────────────────────────────────────────────────────

def test_ac5_per_lint_kill_switch_inside_batch(tmp_path, monkeypatch) -> None:
    """AC5: double-violation fixture + HAL_STUB_PASSABILITY_GATE=0 (batch ON)
    -> error_code==E_RED_1Q_EXEC_IMPORT, preflight_error_codes==
    ["E_RED_1Q_EXEC_IMPORT"], and gate_disabled(HAL_STUB_PASSABILITY_GATE)
    emitted."""
    import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_STUB_PASSABILITY_GATE", "0")
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_1Q_EXEC_IMPORT", (
        f"expected E_RED_1Q_EXEC_IMPORT, got {result.error_code!r}"
    )
    assert result.data is not None and result.data.get("preflight_error_codes") == ["E_RED_1Q_EXEC_IMPORT"], (
        f"expected preflight_error_codes==['E_RED_1Q_EXEC_IMPORT'], got {result.data!r}"
    )
    disabled_calls = [c for c in captured if c[0] == "gate_disabled" and c[1].get("gate") == "HAL_STUB_PASSABILITY_GATE"]
    assert disabled_calls, f"expected gate_disabled(HAL_STUB_PASSABILITY_GATE) emission, got events={captured!r}"


# ─── AC6 ─────────────────────────────────────────────────────────────────

def test_ac6_single_violation_and_semantics_all_recoverable(tmp_path, monkeypatch) -> None:
    """AC6: suite-unsafe-only fixture (single finding, recoverable=True),
    batch ON, repair off -> error_code==E_RED_SUITE_UNSAFE, recoverable is True
    (AND-semantics: True only when ALL findings recoverable)."""
    from phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_suite_unsafe.py", _SUITE_UNSAFE_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_SUITE_UNSAFE", f"got {result.error_code!r}"
    assert result.recoverable is True, f"expected recoverable=True, got {result.recoverable!r}"


# ─── AC7 ─────────────────────────────────────────────────────────────────

def test_ac7_semgrep_infra_priority_batch_over_missing(tmp_path, monkeypatch) -> None:
    """AC7 (§2.3): double-violation fixture + semgrep missing on PATH ->
    batch reported (E_RED_STUB_PASSABLE primary), NOT E_RED_LINT_SEMGREP_MISSING,
    data["semgrep_skipped"]=="missing". Clean fixture + semgrep missing ->
    E_RED_LINT_SEMGREP_MISSING as today (empty-batch branch unaffected)."""
    import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")

    # Half 1: non-empty batch — semgrep-missing must be demoted to a data flag.
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])
    with patch.object(p5.shutil, "which", return_value=None):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_LINT_SEMGREP_MISSING", (
        f"expected batch to take priority over semgrep-missing, got {result.error_code!r}"
    )
    assert result.error_code == "E_RED_STUB_PASSABLE", f"got {result.error_code!r}"
    assert result.data is not None and result.data.get("semgrep_skipped") == "missing", (
        f"expected data['semgrep_skipped']=='missing', got {result.data!r}"
    )

    # Half 2: empty batch — legacy immediate semgrep-missing error unaffected.
    relpath2 = _write_test_file(tmp_path, "tests/test_clean2.py", _CLEAN_CONTENT)
    prev2 = _make_prev([relpath2])
    with patch.object(p5.shutil, "which", return_value=None):
        result2 = p5._verify_red_lint_rules(ctx, prev2)

    assert result2.error_code == "E_RED_LINT_SEMGREP_MISSING", (
        f"expected legacy semgrep-missing on empty batch, got {result2.error_code!r}"
    )


# ─── AC8 ─────────────────────────────────────────────────────────────────

def test_ac8_batch_summary_event_emitted_with_canonical_codes(tmp_path, monkeypatch) -> None:
    """AC8: double-violation fixture, _emit_safe captured -> among the
    emissions is 'red_lint_preflight_batch' with codes==
    ["E_RED_STUB_PASSABLE", "E_RED_1Q_EXEC_IMPORT"] (list, canonical order)
    and findings_n>=2; the per-lint violation events
    (red_stub_passability_violation, red_1q_exec_import_violation) are also
    present (real event-counter side effect, §1l)."""
    import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        p5._verify_red_lint_rules(ctx, prev)

    names = [c[0] for c in captured]
    batch_events = [c for c in captured if c[0] == "red_lint_preflight_batch"]
    assert batch_events, f"expected 'red_lint_preflight_batch' event, got events={names!r}"
    payload = batch_events[0][1]
    assert payload.get("codes") == ["E_RED_STUB_PASSABLE", "E_RED_1Q_EXEC_IMPORT"], (
        f"expected canonical-order codes list, got {payload!r}"
    )
    assert payload.get("findings_n", 0) >= 2, f"expected findings_n>=2, got {payload!r}"
    assert "red_stub_passability_violation" in names, f"missing per-lint event, got {names!r}"
    assert "red_1q_exec_import_violation" in names, f"missing per-lint event, got {names!r}"


# ─── AC9 ─────────────────────────────────────────────────────────────────

def test_ac9_directed_repair_convergence_returns_final_for_batched_gate(
    tmp_path, monkeypatch
) -> None:
    """AC9: double-violation fixture, attempt_directed_repair mocked
    convergent -> _verify_red_lint_rules returns rr.final (status=='ok') AND
    the repair was invoked with the BATCHED gate/findings (gate==
    'red_lint_preflight', findings covering both rules) -- proves the
    convergence path is wired through the new batch dispatcher, not the
    legacy single-gate call site."""
    import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    final_result = StepResult(status="ok", data={"repaired": True}, duration_ms=0, step_name="verify_red_lint_rules")
    calls: list[dict] = []

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=True, final=final_result, attempts=1, exhausted_reason=None)

    with patch.object(p5, "attempt_directed_repair", side_effect=_fake_repair):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.status == "ok", f"expected status=='ok' (rr.final passthrough), got {result.status!r}"
    assert len(calls) == 1, f"expected exactly 1 directed-repair call, got {len(calls)}"
    assert calls[0]["gate"] == "red_lint_preflight", (
        f"expected batched gate='red_lint_preflight', got {calls[0].get('gate')!r}"
    )
    rules = {f.get("rule") for f in calls[0]["findings"]}
    assert {"stub-passability", "1q-exec-import"}.issubset(rules), (
        f"expected combined findings across both rules, got {rules!r}"
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────

def test_ac10_path_escape_infra_precheck_unaffected_no_preflight_keys(tmp_path) -> None:
    """AC10: red_test_paths escapes git_cwd -> immediate E_RED_LINT_PATH_ESCAPE,
    data has NO preflight-batch keys (infra pre-check DEFER, untouched)."""
    from phase_5_implement import _verify_red_lint_rules

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(["../escape.py"])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_LINT_PATH_ESCAPE", f"got {result.error_code!r}"
    assert "preflight_error_codes" not in (result.data or {}), f"got {result.data!r}"
    assert "preflight_findings" not in (result.data or {}), f"got {result.data!r}"


# ─── AC11 ─────────────────────────────────────────────────────────────────

def test_ac11_collect_red_lint_findings_named_helper_returns_normalized_batch(
    tmp_path,
) -> None:
    """AC11 (§1aa forcing function): _collect_red_lint_findings is importable
    as a standalone named helper (not inlined) and, called directly on the
    double-violation fixture, returns a list of >=2 normalized finding dicts
    with keys path/line/rule/evidence/error_code/recoverable."""
    from phase_5_implement import _collect_red_lint_findings  # ImportError pre-GREEN -> FAIL
    import phase_5_implement as p5

    relpath = _write_test_file(tmp_path, "tests/test_double.py", _DOUBLE_VIOLATION_CONTENT)
    resolved = str((tmp_path / relpath).resolve())
    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}

    findings = _collect_red_lint_findings([resolved], str(tmp_path), ctx, cfg)

    assert isinstance(findings, list), f"expected list, got {type(findings)!r}"
    assert len(findings) >= 2, f"expected >=2 findings, got {findings!r}"
    required_keys = {"path", "line", "rule", "evidence", "error_code", "recoverable"}
    for f in findings:
        assert required_keys.issubset(f.keys()), f"finding missing keys, got {f!r}"
    rules = {f["rule"] for f in findings}
    assert {"stub-passability", "1q-exec-import"}.issubset(rules), (
        f"expected both rules present, got {rules!r}"
    )


# ─── AC12 ─────────────────────────────────────────────────────────────────

def test_ac12_test_only_mode_regression_floor_unbroken(tmp_path) -> None:
    """AC12: commit_red_tests_skipped=='test_only_mode' -> status=='ok' and
    'red_verify_skipped_test_only' event emitted (pre-existing L1838-ish path
    must survive the batch dispatcher untouched)."""
    import phase_5_implement as p5

    ctx = _make_ctx(tmp_path)
    prev = StepResult(
        status="ok",
        data={"commit_red_tests_skipped": "test_only_mode"},
        duration_ms=0,
        step_name="commit_red_tests",
    )

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.status == "ok", f"got {result.status!r}"
    names = [c[0] for c in captured]
    assert "red_verify_skipped_test_only" in names, f"got events={names!r}"
