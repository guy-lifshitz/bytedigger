"""RED tests for GH602 — findings-driven delta-retry for terminal RED-lint
preflight gates (E_RED_STUB_PASSABLE / E_RED_1Q_EXEC_IMPORT / E_RED_SUITE_UNSAFE).

Frozen spec: SHARED/memory/Decisions/2026-07-11_GH602_red_lint_delta_retry_spec.md

§1q/D1CF5FDF: `_preflight_retry_eligible`, `_PREFLIGHT_RETRY_ELIGIBLE_CODES`,
`E_RED_LINT_FAIL_CAP2`, the `red_lint_preflight` policy-matrix rows, and the
`HAL_RED_PREFLIGHT_DELTA_RETRY` flag entry do not exist yet. Every reference
to them is resolved INSIDE the test body via getattr/dict-lookup + assert
(never a module-level `from x import missing_name`), so this file COLLECTS
cleanly and each test FAILS at assert time, never at collection time. No
module-level sys.path.insert / `from conftest import` here — sys.path wiring
is provided by conftest.py (§1q / 81F97F3D gate).

§1i: no singleton/time-dependent resources; fixtures are written
deterministically to tmp_path before invoking the UUT
(`_verify_red_lint_rules`) — no racing.

UUT (`_verify_red_lint_rules`, `_preflight_retry_eligible`, `resolve_policy`)
is never mocked (§1l). `attempt_directed_repair` is disabled via
HAL_DIRECTED_REPAIR=0 (existing kill switch, per sibling pattern), not
mocked-around.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.contracts import StepResult, WorkflowContext


# ─── shared helpers (mirror test_gh595_red_lint_preflight_batch.py) ─────────

def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-gh602",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(
    red_test_paths: list, cycle: int = 1, gate_attempts: dict | None = None
) -> StepResult:
    """GH625: `gate_attempts` optionally seeds per-gate attempt accounting —
    `_verify_red_lint_rules` spreads `**prev.data` into its forwarded_data
    (phase_5_implement.py:2475 `data = {**prev.data, ...}`), so this key
    flows straight into gated_step_result's cap decision."""
    data: dict = {
        "red_test_paths": red_test_paths,
        "red_log_path": "tests/build-red-output.log",
        "spec_path": "scratchpad/spec.md",
        "cycle": cycle,
    }
    if gate_attempts is not None:
        data["gate_attempts"] = gate_attempts
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _write_test_file(tmp_path: Path, relpath: str, content: str) -> str:
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return relpath


def _capture_emit_factory():
    captured: list[tuple] = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    return captured, _capture


# Single-violation fixture: stub-passability ONLY (mock-the-UUT pattern), no
# §1q token, no suite-unsafe import.
_STUB_ONLY_CONTENT = (
    "from unittest.mock import patch\n"
    "import helper_mod\n"
    "from helper_mod import target_fn\n"
    "\n"
    "\n"
    "def test_mock_uut():\n"
    "    with patch.object(helper_mod, \"target_fn\", return_value=42):\n"
    "        result = target_fn()\n"
    "    assert result is not None\n"
)

# Single-violation fixture: §1q exec-import token ONLY, no mock-UUT pattern,
# no suite-unsafe import. Token built via concatenation so this file's own
# source never contains the literal pattern in one piece (data, not code).
_EXEC_IMPORT_TOKEN = "spec_from_file_location" + "(__name__, " + '"x.py")'
_1Q_ONLY_CONTENT = (
    "import helper_mod\n"
    "\n"
    "\n"
    "def test_exec_loader_reference():\n"
    "    # incidental reference: " + _EXEC_IMPORT_TOKEN + "\n"
    "    assert helper_mod is not None\n"
)

# Single-violation fixture: suite-safety ONLY (from conftest import), no
# mock-UUT, no §1q token.
_SUITE_UNSAFE_ONLY_CONTENT = (
    "from conftest import something\n"
    "\n"
    "\n"
    "def test_x():\n"
    "    assert something\n"
)


# ─── AC1 ─────────────────────────────────────────────────────────────────

def test_ac1_stub_violation_cycle1_returns_retry_shape(tmp_path, monkeypatch) -> None:
    """AC1: single stub-passability violation, cycle=1, directed repair off
    -> status='error', recoverable=True, error_code='E_RED_STUB_PASSABLE',
    data['retry_from_step']==0, data['cycle_count']==1, data['findings']
    contains 'E_RED_STUB_PASSABLE' and the fixture path."""
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_stub.py", _STUB_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath], cycle=1)

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r}; err={result.error!r}"
    assert result.recoverable is True, (
        f"expected recoverable=True (dead-recoverable revived), got {result.recoverable!r}"
    )
    assert result.error_code == "E_RED_STUB_PASSABLE", f"got {result.error_code!r}"
    data = result.data or {}
    assert data.get("retry_from_step") == 0, f"expected retry_from_step==0, got {data!r}"
    assert data.get("cycle_count") == 1, f"expected cycle_count==1, got {data!r}"
    findings = data.get("findings") or ""
    assert "E_RED_STUB_PASSABLE" in findings and relpath in findings, (
        f"expected findings to cite code + path, got {findings!r}"
    )


# ─── AC2 ─────────────────────────────────────────────────────────────────

def test_ac2_1q_violation_cycle1_returns_retry_shape(tmp_path, monkeypatch) -> None:
    """AC2: single §1q exec-import violation, cycle=1 -> retry-shape as AC1,
    error_code='E_RED_1Q_EXEC_IMPORT'."""
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_1q.py", _1Q_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath], cycle=1)

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r}; err={result.error!r}"
    assert result.recoverable is True, f"expected recoverable=True, got {result.recoverable!r}"
    assert result.error_code == "E_RED_1Q_EXEC_IMPORT", f"got {result.error_code!r}"
    data = result.data or {}
    assert data.get("retry_from_step") == 0, f"expected retry_from_step==0, got {data!r}"
    assert data.get("cycle_count") == 1, f"expected cycle_count==1, got {data!r}"


# ─── AC3 ─────────────────────────────────────────────────────────────────

def test_ac3_stub_violation_cycle2_terminal_cap(tmp_path, monkeypatch) -> None:
    """AC3: same stub-only fixture but prev.data cycle=2 -> cap exhausted:
    status='error', recoverable=False, error_code=='E_RED_LINT_FAIL_CAP2',
    'retry_from_step' NOT in data.

    GH625: cap decision now keyed on per-gate gate_attempts, not raw cycle;
    red_lint_preflight stays recoverable_twice/cap=2 (unchanged by §2.5).
    Seed 2 prior attempts (== cap) so this stays terminal.
    """
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_stub.py", _STUB_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath], cycle=2, gate_attempts={"red_lint_preflight": 2})

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.recoverable is False, f"expected recoverable=False at cap, got {result.recoverable!r}"
    assert result.error_code == "E_RED_LINT_FAIL_CAP2", f"got {result.error_code!r}"
    data = result.data or {}
    assert "retry_from_step" not in data, f"expected no retry_from_step at cap, got {data!r}"


# ─── AC4 ─────────────────────────────────────────────────────────────────

def test_ac4_preflight_retry_eligible_true_for_retryable_codes() -> None:
    """AC4: `_preflight_retry_eligible` (module-level helper, §1aa) is True
    when EVERY finding's error_code is in the retry-eligible set (stub /
    1q / suite / collect-probe)."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    fn = getattr(p5, "_preflight_retry_eligible", None)
    assert fn is not None, "expected _preflight_retry_eligible to exist on phase_5_implement"

    eligible_codes = ["E_RED_STUB_PASSABLE", "E_RED_1Q_EXEC_IMPORT", "E_RED_SUITE_UNSAFE", "E_RED_COLLECT_PROBE"]
    for code in eligible_codes:
        batch = [{"error_code": code}]
        assert fn(batch) is True, f"expected True for batch of {code!r}"

    mixed_batch = [{"error_code": "E_RED_STUB_PASSABLE"}, {"error_code": "E_RED_1Q_EXEC_IMPORT"}]
    assert fn(mixed_batch) is True, f"expected True for all-eligible mixed batch, got False"


def test_ac4_preflight_retry_eligible_false_for_semgrep_finding() -> None:
    """AC4 (negative half): a batch containing an E_RED_LINT_F1 (semgrep)
    finding is NOT eligible — semgrep stays terminal, even mixed with
    otherwise-eligible codes."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    fn = getattr(p5, "_preflight_retry_eligible", None)
    assert fn is not None, "expected _preflight_retry_eligible to exist on phase_5_implement"

    batch_pure_semgrep = [{"error_code": "E_RED_LINT_F1"}]
    assert fn(batch_pure_semgrep) is False, "expected False for pure semgrep batch"

    batch_mixed_with_semgrep = [
        {"error_code": "E_RED_STUB_PASSABLE"},
        {"error_code": "E_RED_LINT_F1"},
    ]
    assert fn(batch_mixed_with_semgrep) is False, (
        "expected False when ANY finding is semgrep, even if others are eligible"
    )


# ─── AC5 ─────────────────────────────────────────────────────────────────

def test_ac5_suite_unsafe_only_cycle1_retry_shape_dead_recoverable_revived(
    tmp_path, monkeypatch
) -> None:
    """AC5: suite-safety-only violation (recoverable=True finding, already
    today), cycle=1 -> retry-shape (recoverable=True, retry_from_step==0),
    error_code=='E_RED_SUITE_UNSAFE' — the mixin path now actually retries
    the engine, not just marks a dead field."""
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    relpath = _write_test_file(tmp_path, "tests/test_suite_unsafe.py", _SUITE_UNSAFE_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath], cycle=1)

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_SUITE_UNSAFE", f"got {result.error_code!r}"
    assert result.recoverable is True, f"expected recoverable=True, got {result.recoverable!r}"
    data = result.data or {}
    assert data.get("retry_from_step") == 0, (
        f"expected retry_from_step==0 (engine actually retries), got {data!r}"
    )


# ─── AC6 ─────────────────────────────────────────────────────────────────

def test_ac6_policy_matrix_red_lint_preflight_recoverable_twice_all_classes() -> None:
    """AC6: resolve_policy(bc, 'red_lint_preflight') for SIMPLE/FEATURE/COMPLEX
    -> slot=='recoverable_twice', cycle_cap==2. Today this gate is absent
    from the matrix and falls back to _DEFAULT_POLICY (recoverable_once, 1)."""
    from bytedigger_engine.workflows._recoverable_policy import resolve_policy

    for bc in ("SIMPLE", "FEATURE", "COMPLEX"):
        pol = resolve_policy(bc, "red_lint_preflight")
        assert pol.slot == "recoverable_twice", (
            f"expected slot=='recoverable_twice' for {bc}, got {pol.slot!r}"
        )
        assert pol.cycle_cap == 2, f"expected cycle_cap==2 for {bc}, got {pol.cycle_cap!r}"


# ─── AC7 ─────────────────────────────────────────────────────────────────

def test_ac7_error_code_cap2_registered_in_module_and_doc() -> None:
    """AC7: error_codes.ERROR_CODES contains 'E_RED_LINT_FAIL_CAP2', and
    ERROR_CODES.md documents it."""
    from bytedigger_engine import error_codes

    assert "E_RED_LINT_FAIL_CAP2" in error_codes.ERROR_CODES, (
        f"expected E_RED_LINT_FAIL_CAP2 registered, got keys sample="
        f"{[k for k in error_codes.ERROR_CODES if k.startswith('E_RED_LINT')]!r}"
    )

    doc_path = Path(error_codes.__file__).resolve().parent / "ERROR_CODES.md"
    assert doc_path.is_file(), f"expected {doc_path} to exist"
    doc_text = doc_path.read_text(encoding="utf-8")
    assert "E_RED_LINT_FAIL_CAP2" in doc_text, (
        "expected ERROR_CODES.md to document E_RED_LINT_FAIL_CAP2"
    )


# ─── AC8 ─────────────────────────────────────────────────────────────────

def test_ac8_recoverable_gate_attempted_event_emitted_on_retry(tmp_path, monkeypatch) -> None:
    """AC8: telemetry_ctx.emit_safe captured, AC1 scenario -> a
    'recoverable_gate_attempted' event fires with payload.gate==
    'red_lint_preflight' and payload.outcome=='retry'."""
    from bytedigger_engine import telemetry_ctx

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")

    captured: list[tuple] = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _capture)

    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    relpath = _write_test_file(tmp_path, "tests/test_stub.py", _STUB_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath], cycle=1)

    _verify_red_lint_rules(ctx, prev)

    gate_events = [
        c for c in captured
        if c[0] == "recoverable_gate_attempted"
        and c[1].get("gate") == "red_lint_preflight"
        and c[1].get("outcome") == "retry"
    ]
    assert gate_events, (
        f"expected recoverable_gate_attempted(gate=red_lint_preflight, outcome=retry), "
        f"got events={[(c[0], c[1]) for c in captured]!r}"
    )


# ─── AC9 ─────────────────────────────────────────────────────────────────

def test_ac9_delta_retry_chain_gate_prompt_gate_no_git_ops(tmp_path, monkeypatch) -> None:
    """AC9 (exit-criterion (g)): gate@cycle1 -> findings; _build_red_prompt
    with cycle=2 + those findings (HAL_IMPL_DELTA_RETRY on) -> compact
    delta prompt containing the findings substring; gate@cycle2 -> terminal
    cap. No git operations anywhere in this chain (0 git reset).

    GH625: cap is now keyed on per-gate gate_attempts, not raw cycle. This
    chain only drives 2 discrete gate calls (cycle1, cycle2) — step1's own
    retry only advances gate_attempts to 1 (< cap=2), so step3's prev is
    seeded directly with gate_attempts=2 (== cap) to reach the terminal cap
    in one more call, matching the AC's 2-call chain shape.
    """
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "1")

    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules, _build_red_prompt

    relpath = _write_test_file(tmp_path, "tests/test_stub.py", _STUB_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir(exist_ok=True)
    ctx.org_config["scratchpad_dir"] = str(scratchpad)

    # Step 1: gate at cycle 1 -> retry-shape with findings.
    prev1 = _make_prev([relpath], cycle=1)
    result1 = _verify_red_lint_rules(ctx, prev1)
    assert result1.status == "error" and result1.recoverable is True, (
        f"expected step1 retry-shape, got status={result1.status!r} recoverable={result1.recoverable!r}"
    )
    findings_str = (result1.data or {}).get("findings")
    assert findings_str, f"expected non-empty findings from step1, got {result1.data!r}"

    # Step 2: build the RED prompt for the retried cycle with those findings.
    result2 = _build_red_prompt(ctx, {"cycle": 2, "findings": findings_str})
    assert result2.data.get("delta_retry") is True, (
        f"expected delta_retry==True on cycle>=2 prompt, got {result2.data!r}"
    )
    prompt = result2.data.get("prompt") or ""
    assert findings_str in prompt, (
        f"expected findings substring in delta prompt; findings={findings_str!r} not found"
    )

    # Step 3: gate at cycle 2 -> terminal cap (GH625: seed gate_attempts==cap).
    prev2 = _make_prev([relpath], cycle=2, gate_attempts={"red_lint_preflight": 2})
    result3 = _verify_red_lint_rules(ctx, prev2)
    assert result3.status == "error" and result3.recoverable is False, (
        f"expected step3 terminal cap, got status={result3.status!r} recoverable={result3.recoverable!r}"
    )
    assert result3.error_code == "E_RED_LINT_FAIL_CAP2", f"got {result3.error_code!r}"


# ─── AC10 ─────────────────────────────────────────────────────────────────

def test_ac10_kill_switch_off_restores_terminal_behavior(tmp_path, monkeypatch) -> None:
    """AC10: HAL_RED_PREFLIGHT_DELTA_RETRY=0 + AC1 scenario -> status='error',
    recoverable=False, error_code=='E_RED_STUB_PASSABLE' (old terminal
    behavior) + a 'gate_disabled' event with gate==
    'HAL_RED_PREFLIGHT_DELTA_RETRY'."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    relpath = _write_test_file(tmp_path, "tests/test_stub.py", _STUB_ONLY_CONTENT)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath], cycle=1)

    captured, _capture = _capture_emit_factory()
    from unittest.mock import patch

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.recoverable is False, f"expected recoverable=False (kill switch off), got {result.recoverable!r}"
    assert result.error_code == "E_RED_STUB_PASSABLE", f"got {result.error_code!r}"
    disabled_calls = [
        c for c in captured
        if c[0] == "gate_disabled" and c[1].get("gate") == "HAL_RED_PREFLIGHT_DELTA_RETRY"
    ]
    assert disabled_calls, (
        f"expected gate_disabled(HAL_RED_PREFLIGHT_DELTA_RETRY) emission, got events={captured!r}"
    )


# ─── AC11 ─────────────────────────────────────────────────────────────────

def test_ac11_flag_registered_in_catalog() -> None:
    """AC11: flags_catalog.FLAGS contains 'HAL_RED_PREFLIGHT_DELTA_RETRY'
    with kind=='gate', default=='1'."""
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS.get("HAL_RED_PREFLIGHT_DELTA_RETRY")
    assert entry is not None, (
        f"expected HAL_RED_PREFLIGHT_DELTA_RETRY registered, got keys sample="
        f"{[k for k in flags_catalog.FLAGS if 'RED' in k]!r}"
    )
    assert entry.get("kind") == "gate", f"got {entry!r}"
    assert entry.get("default") == "1", f"got {entry!r}"
