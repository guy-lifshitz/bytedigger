"""RED tests for GH635 — governor short-circuit must not amplify a
deterministic gate-fail into a dead stop.

Spec: SHARED/memory/Decisions/2026-07-12_GH635_governor_shortcircuit_gate_retry_spec.md
(§3 AC1-AC7).

D1CF5FDF: `lib.restart_governor` symbols are imported INSIDE each test body
(module behavior is changing under this ship — the module itself already
exists, but the `recoverable` kwarg on `governor_record_result` does not yet;
importing inline keeps collection robust regardless of what shape GREEN
lands).

No UUT mocking (stub-passability lint, §1l): governor_check/record_result/
record_start are exercised directly against real tmp_path state files, never
patched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ─── sys.path setup (mirrors test_gh625_restart_budget_split.py:35-44) ───────
ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))
LIB = ENGINE_PY / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


# ═══════════════════════════════════════════════════════════════════════════
# AC1-AC7 — recoverable-aware short-circuit contract
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_recoverable_gate_code_exempt_from_short_circuit(tmp_path):
    """AC1 (core RED, anti-amplification): two identical recoverable=True
    codes must NOT short-circuit. Today `governor_record_result` has no
    `recoverable` kwarg at all -> TypeError; even papered over, today's
    behavior short-circuits on the 2nd identical code regardless of
    recoverability."""
    from lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "gh635-ac1", "echo"
    governor_record_result(tmp_path, run_id, workflow, error_code="E_SPEC_LINT", recoverable=True)
    governor_record_result(tmp_path, run_id, workflow, error_code="E_SPEC_LINT", recoverable=True)

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3, gate_cap=10)
    assert allow is True, (
        f"AC1 FAIL: two identical recoverable=True codes must NOT short-circuit "
        f"(gate-retry class exempt); got allow={allow!r} deny_code={deny_code!r}. "
        "Today governor_record_result has no 'recoverable' kwarg -> TypeError pre-GREEN, "
        "or (once shimmed) short-circuits unconditionally on 2 identical codes."
    )
    assert deny_code is None, f"AC1 FAIL: expected deny_code None, got {deny_code!r}"


def test_ac2_terminal_code_still_short_circuits(tmp_path):
    """AC2: two identical non-recoverable (terminal) codes must still trip
    the short-circuit."""
    from lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "gh635-ac2", "echo"
    governor_record_result(
        tmp_path, run_id, workflow, error_code="E_SPEC_RETRY_FAIL_CAP2", recoverable=False
    )
    governor_record_result(
        tmp_path, run_id, workflow, error_code="E_SPEC_RETRY_FAIL_CAP2", recoverable=False
    )

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is False, (
        f"AC2 FAIL: two identical terminal (recoverable=False) codes must still "
        f"short-circuit; got allow={allow!r}"
    )
    assert deny_code == "E_RESTART_SHORT_CIRCUIT", (
        f"AC2 FAIL: expected deny_code=='E_RESTART_SHORT_CIRCUIT', got {deny_code!r}"
    )


def test_ac3_mixed_recoverable_then_terminal(tmp_path):
    """AC3: 3x recoverable gate-retry codes (interleaved start+result) must
    NOT short-circuit; a subsequent pair of identical terminal codes on the
    SAME run_id must then trip it."""
    from lib.restart_governor import (
        governor_check,
        governor_record_result,
        governor_record_start,
    )

    run_id, workflow = "gh635-ac3", "echo"
    for _ in range(3):
        governor_record_start(tmp_path, run_id, workflow)
        governor_record_result(tmp_path, run_id, workflow, error_code="E_GATE", recoverable=True)

    allow_mid, deny_mid = governor_check(tmp_path, run_id, workflow, cap=3, gate_cap=10)
    assert allow_mid is True, (
        f"AC3 FAIL (mid): 3x recoverable 'E_GATE' must not short-circuit; "
        f"got allow={allow_mid!r} deny_code={deny_mid!r}"
    )
    assert deny_mid is None, f"AC3 FAIL (mid): expected deny_code None, got {deny_mid!r}"

    governor_record_result(tmp_path, run_id, workflow, error_code="E_TERM", recoverable=False)
    governor_record_result(tmp_path, run_id, workflow, error_code="E_TERM", recoverable=False)

    allow_final, deny_final = governor_check(tmp_path, run_id, workflow, cap=3, gate_cap=10)
    assert allow_final is False, (
        f"AC3 FAIL (final): two identical terminal 'E_TERM' codes after the recoverable "
        f"run must still short-circuit; got allow={allow_final!r}"
    )
    assert deny_final == "E_RESTART_SHORT_CIRCUIT", (
        f"AC3 FAIL (final): expected deny_code=='E_RESTART_SHORT_CIRCUIT', got {deny_final!r}"
    )


def test_ac4_state_shape_sc_codes_excludes_recoverable(tmp_path):
    """AC4: state file's new 'sc_codes' key excludes recoverable=True codes,
    while the legacy 'error_codes' key keeps ALL codes unchanged."""
    from lib.restart_governor import governor_record_result

    run_id, workflow = "gh635-ac4", "echo"
    governor_record_result(tmp_path, run_id, workflow, error_code="E_SPEC_LINT", recoverable=True)
    governor_record_result(tmp_path, run_id, workflow, error_code="E_SPEC_LINT", recoverable=True)

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("sc_codes") == [], (
        f"AC4 FAIL: expected sc_codes==[] (recoverable codes never enter it), got {data!r}. "
        "Today there is no 'sc_codes' key at all."
    )
    assert data.get("error_codes") == ["E_SPEC_LINT", "E_SPEC_LINT"], (
        f"AC4 FAIL: expected error_codes==['E_SPEC_LINT','E_SPEC_LINT'] (telemetry "
        f"preserved unchanged), got {data!r}"
    )


def test_ac5_reentry_no_double_no_inherit(tmp_path):
    """AC5 (§2.3 process-boundary invariant): a crash-closed start appends
    NOTHING to sc_codes (no phantom double); a replayed revise loop of pure
    recoverable results never accrues sc-pressure (no inherit)."""
    from lib.restart_governor import (
        governor_check,
        governor_record_result,
        governor_record_start,
    )

    # Block A: crash-close then a single terminal result -> exactly one sc entry.
    run_id_a, workflow = "gh635-ac5a", "echo"
    governor_record_start(tmp_path, run_id_a, workflow)  # open #1
    governor_record_start(tmp_path, run_id_a, workflow)  # closes #1 as crash, opens #2
    governor_record_result(tmp_path, run_id_a, workflow, error_code="E_TERM", recoverable=False)

    state_file_a = tmp_path / f"restart-governor-{run_id_a}-{workflow}.json"
    data_a = json.loads(state_file_a.read_text(encoding="utf-8"))
    assert data_a.get("sc_codes") == ["E_TERM"], (
        f"AC5 FAIL (no-double): expected sc_codes==['E_TERM'] exactly (crash-close adds "
        f"nothing), got {data_a!r}"
    )

    # Block B: fresh run_id, 10x recoverable results -> sc_codes stays empty,
    # a replayed revise loop cannot inherit/double short-circuit pressure.
    run_id_b = "gh635-ac5b"
    for _ in range(10):
        governor_record_result(tmp_path, run_id_b, workflow, error_code="E_G", recoverable=True)

    state_file_b = tmp_path / f"restart-governor-{run_id_b}-{workflow}.json"
    data_b = json.loads(state_file_b.read_text(encoding="utf-8"))
    assert data_b.get("sc_codes") == [], (
        f"AC5 FAIL (no-inherit): expected sc_codes==[] after 10 recoverable results, "
        f"got {data_b!r}"
    )
    allow_b, deny_b = governor_check(tmp_path, run_id_b, workflow, cap=100, gate_cap=100)
    assert allow_b is True, (
        f"AC5 FAIL (no-inherit): expected allow after 10 recoverable results, "
        f"got allow={allow_b!r} deny_code={deny_b!r}"
    )


def test_ac6_run_py_threads_recoverable_flag():
    """AC6 (§1y Point->Host->Test-path, P6 sibling idiom): run.py's sole
    governor_record_result call site must thread `recoverable=result.recoverable`."""
    run_py_path = Path(__file__).parent.parent / "run.py"
    source = run_py_path.read_text(encoding="utf-8")

    assert "governor_record_result(" in source, (
        "AC6 FAIL: run.py must call governor_record_result("
    )
    assert "recoverable=result.recoverable" in source, (
        "AC6 FAIL: expected run.py's governor_record_result( call to thread "
        "'recoverable=result.recoverable'; not found in source. Today's call site "
        "does not pass a recoverable kwarg at all."
    )


def test_ac7_legacy_default_still_short_circuits(tmp_path):
    """AC7 (backward-compat guard): calling record_result with NO recoverable
    kwarg (default False) must still short-circuit on two identical codes,
    preserving GH374 A3/A4 + GH625 pins unedited."""
    from lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "gh635-ac7", "echo"
    governor_record_result(tmp_path, run_id, workflow, error_code="E_BOUNDARY_SUPPRESSION")
    governor_record_result(tmp_path, run_id, workflow, error_code="E_BOUNDARY_SUPPRESSION")

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=5)
    assert allow is False, (
        f"AC7 FAIL: un-annotated (default recoverable=False) identical codes must still "
        f"short-circuit; got allow={allow!r}"
    )
    assert deny_code == "E_RESTART_SHORT_CIRCUIT", (
        f"AC7 FAIL: expected deny_code=='E_RESTART_SHORT_CIRCUIT', got {deny_code!r}"
    )
