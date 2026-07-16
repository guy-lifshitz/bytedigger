"""RED tests for GH863 B5: helper-extraction-lint DG-45 wiring (warn-mode).

Spec: SHARED/memory/Decisions/2026-07-15_GH863_helper_extraction_dg45_spec.md

None of the following exist yet in production code (this worktree base):
  - phase_45_spec._verify_spec_helper_extraction
  - "verify_spec_helper_extraction" step in phase_45_spec_workflow()
  - flags_catalog.FLAGS["HAL_SPEC_HELPER_EXTRACTION_GATE"/"HAL_SPEC_HELPER_EXTRACTION_ENFORCE"]
  - error_codes.ERROR_CODES["E_SPEC_HELPER_EXTRACTION"/"E_SPEC_HELPER_EXTRACTION_FATAL"]
  - literal tokens "flip-by:2026-07-29" / "Refs #863" near the new flag in phase_45_spec.py

Deferred-import discipline (§1q-ext D1CF5FDF): every not-yet-existing symbol is
probed INSIDE the test function body (getattr/dict-lookup) so the file COLLECTS
cleanly and fails at assert/call time, never at collection time. `phase_45_spec`
(already existing) and `contracts` (WorkflowContext/StepResult) are imported at
module level, mirroring test_gh823_spec_reentry.py.

No UUT symbol is ever patched/mocked (§1l stub-passability) -- only
`phase_45_spec._emit_safe` (infra event-sink) is monkeypatched to capture
emits, and env vars are set via monkeypatch.setenv/delenv (§1i pre-stage).
scan_helper_extraction (helper_extraction.py) already exists in production and
is NOT the UUT -- it is used only to build fixture text guaranteed to trigger
findings; it is never mocked either.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_45_spec  # noqa: E402 -- module already exists; new symbols probed lazily


# ─── fixture spec texts ────────────────────────────────────────────────────

_SPEC_WITH_INLINE_MATH = """\
# Test Spec

## §2 Design
Some logic below.
if [ "$flag" = "1" ]; then
    count=$((count+1))
fi

## §3 Acceptance Criteria

| AC | Description |
|---|---|
| AC1 | frobnicate works |
"""

_SPEC_CLEAN = """\
# Test Spec

## §2 Design
- **frobnicate** — calls a named sourceable helper via `declare -f frobnicate`

## §3 Acceptance Criteria

| AC | Description |
|---|---|
| AC1 | frobnicate works |
"""


def _make_ctx(question: str = "GH863 helper extraction", **org_config_extra) -> WorkflowContext:
    org_config = {"complexity": "SIMPLE"}
    org_config.update(org_config_extra)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question=question,
        session_id="test-GH863",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(spec_path: Path, cycle: int = 1, **extra) -> StepResult:
    data: dict = {"spec_path": str(spec_path), "cycle": cycle, **extra}
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def _get_uut():
    assert hasattr(phase_45_spec, "_verify_spec_helper_extraction"), (
        "phase_45_spec._verify_spec_helper_extraction does not exist yet"
    )
    return phase_45_spec._verify_spec_helper_extraction


# ─── AC1 — flags_catalog defaults + flip-by token ──────────────────────────


def test_ac1_flags_catalog_defaults_and_flip_by_token():
    import flags_catalog  # noqa: PLC0415

    assert "HAL_SPEC_HELPER_EXTRACTION_GATE" in flags_catalog.FLAGS, (
        "HAL_SPEC_HELPER_EXTRACTION_GATE missing from flags_catalog.FLAGS"
    )
    gate_entry = flags_catalog.FLAGS["HAL_SPEC_HELPER_EXTRACTION_GATE"]
    assert gate_entry.get("default") == "1", f"expected default=='1', got {gate_entry!r}"

    assert "HAL_SPEC_HELPER_EXTRACTION_ENFORCE" in flags_catalog.FLAGS, (
        "HAL_SPEC_HELPER_EXTRACTION_ENFORCE missing from flags_catalog.FLAGS"
    )
    enforce_entry = flags_catalog.FLAGS["HAL_SPEC_HELPER_EXTRACTION_ENFORCE"]
    assert enforce_entry.get("default") == "0", f"expected default=='0', got {enforce_entry!r}"
    assert "flip-by:2026-07-29" in enforce_entry.get("description", ""), (
        f"expected 'flip-by:2026-07-29' in ENFORCE description, got {enforce_entry!r}"
    )


# ─── AC2 — error_codes registered ──────────────────────────────────────────


def test_ac2_error_codes_registered():
    import error_codes  # noqa: PLC0415

    assert "E_SPEC_HELPER_EXTRACTION" in error_codes.ERROR_CODES, (
        "E_SPEC_HELPER_EXTRACTION missing from error_codes.ERROR_CODES"
    )
    assert "E_SPEC_HELPER_EXTRACTION_FATAL" in error_codes.ERROR_CODES, (
        "E_SPEC_HELPER_EXTRACTION_FATAL missing from error_codes.ERROR_CODES"
    )


# ─── AC3 — workflow wiring immediately after verify_spec_reentry ──────────


def test_ac3_verify_spec_helper_extraction_wired_immediately_after_reentry():
    names = [s.name for s in phase_45_spec.phase_45_spec_workflow().steps]
    assert "verify_spec_reentry" in names, (
        f"'verify_spec_reentry' missing (needed for ordering check): {names}"
    )
    assert "verify_spec_helper_extraction" in names, (
        f"'verify_spec_helper_extraction' missing from step list; got: {names}"
    )
    i_reentry = names.index("verify_spec_reentry")
    i_helper = names.index("verify_spec_helper_extraction")
    assert i_helper == i_reentry + 1, (
        f"verify_spec_helper_extraction ({i_helper}) must be immediately after "
        f"verify_spec_reentry ({i_reentry}); got: {names}"
    )


# ─── AC4 — findings present, warn-mode → ok + emit enforce=False ──────────


def test_ac4_warn_mode_returns_ok_with_findings_and_emits_violation(tmp_path, monkeypatch):
    uut = _get_uut()

    events: list = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload))
    )
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_ENFORCE", raising=False)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_GATE", raising=False)

    from helper_extraction import scan_helper_extraction  # noqa: PLC0415

    assert scan_helper_extraction(_SPEC_WITH_INLINE_MATH), (
        "fixture must produce >=1 finding via scan_helper_extraction"
    )

    f = _write(tmp_path, "spec_math.md", _SPEC_WITH_INLINE_MATH)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = uut(ctx, prev)

    assert r.status == "ok", f"warn-mode must return 'ok', got {r.status!r}"
    assert (r.data or {}).get("spec_helper_extraction_findings"), (
        f"expected non-empty spec_helper_extraction_findings, got {(r.data or {})!r}"
    )
    violation_events = [e for e in events if e[0] == "spec_helper_extraction_violation"]
    assert violation_events, f"expected 'spec_helper_extraction_violation' emitted, got {events!r}"
    assert violation_events[0][1].get("enforce") is False, (
        f"expected enforce=False in warn-mode emit payload, got {violation_events[0][1]!r}"
    )


# ─── AC5 — enforce-mode returns error E_SPEC_HELPER_EXTRACTION ────────────


def test_ac5_enforce_mode_returns_error_e_spec_helper_extraction(tmp_path, monkeypatch):
    uut = _get_uut()

    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_GATE", raising=False)
    monkeypatch.setenv("HAL_SPEC_HELPER_EXTRACTION_ENFORCE", "1")
    # §1i pre-stage: directed repair OFF so enforce path deterministically
    # lands on the gated result, never a directed-repair attempt loop.
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")

    f = _write(tmp_path, "spec_math_enforce.md", _SPEC_WITH_INLINE_MATH)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = uut(ctx, prev)

    assert r.status == "error", f"enforce-mode: expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_HELPER_EXTRACTION", (
        f"enforce-mode: expected error_code=='E_SPEC_HELPER_EXTRACTION', got {r.error_code!r}"
    )


# ─── AC6 — clean spec → ok, empty findings ─────────────────────────────────


def test_ac6_clean_spec_returns_ok_empty_findings(tmp_path, monkeypatch):
    uut = _get_uut()

    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_ENFORCE", raising=False)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_GATE", raising=False)

    from helper_extraction import scan_helper_extraction  # noqa: PLC0415

    assert scan_helper_extraction(_SPEC_CLEAN) == [], (
        "fixture must produce no findings via scan_helper_extraction"
    )

    f = _write(tmp_path, "spec_clean.md", _SPEC_CLEAN)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = uut(ctx, prev)

    assert r.status == "ok", f"expected 'ok', got {r.status!r}"
    assert (r.data or {}).get("spec_helper_extraction_findings") == [], (
        f"expected empty spec_helper_extraction_findings, got {(r.data or {})!r}"
    )


# ─── AC7 — gate kill-switch → ok, env_skip, gate_disabled emitted ─────────


def test_ac7_gate_kill_switch_skips_and_emits_gate_disabled(tmp_path, monkeypatch):
    uut = _get_uut()

    events: list = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload))
    )
    monkeypatch.setenv("HAL_SPEC_HELPER_EXTRACTION_GATE", "0")

    f = _write(tmp_path, "spec_gate_off.md", _SPEC_WITH_INLINE_MATH)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r = uut(ctx, prev)

    assert r.status == "ok", f"gate-killed path must return 'ok', got {r.status!r}"
    assert (r.data or {}).get("spec_helper_extraction_skipped") == "env_skip", (
        f"expected data['spec_helper_extraction_skipped']=='env_skip', got {(r.data or {})!r}"
    )
    gate_disabled_events = [e for e in events if e[0] == "gate_disabled"]
    assert gate_disabled_events, f"expected 'gate_disabled' emitted, got events={events!r}"


# ─── AC8 — prev.data['is_frozen'] → status=='skip' ─────────────────────────


def test_ac8_frozen_prev_returns_skip_status(tmp_path, monkeypatch):
    uut = _get_uut()
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)

    f = _write(tmp_path, "spec_frozen.md", _SPEC_WITH_INLINE_MATH)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1, is_frozen=True)

    r = uut(ctx, prev)

    assert r.status == "skip", f"expected 'skip' for frozen prev, got {r.status!r}"


# ─── AC9 — nonexistent spec_path → gated E_SPEC_FILE_MISSING ──────────────


def test_ac9_missing_spec_file_returns_gated_e_spec_file_missing(tmp_path, monkeypatch):
    uut = _get_uut()
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_GATE", raising=False)

    missing = tmp_path / "does_not_exist.md"
    ctx = _make_ctx()
    prev = _make_prev(missing, cycle=1)

    r = uut(ctx, prev)

    assert r.error_code == "E_SPEC_FILE_MISSING", (
        f"expected error_code=='E_SPEC_FILE_MISSING', got {r.error_code!r}"
    )


# ─── AC10 — prev missing spec_path → error E_MISSING_PREV_DATA ────────────


def test_ac10_prev_missing_spec_path_returns_e_missing_prev_data():
    uut = _get_uut()

    ctx = _make_ctx()
    prev = StepResult(status="ok", data={"cycle": 1}, duration_ms=0, step_name="prev")

    r = uut(ctx, prev)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_MISSING_PREV_DATA", (
        f"expected error_code=='E_MISSING_PREV_DATA', got {r.error_code!r}"
    )
    assert r.recoverable is False, f"expected recoverable=False, got {r.recoverable!r}"


# ─── AC11 — literal rollout-completion tokens in phase_45_spec.py source ──


def test_ac11_source_contains_flip_by_and_issue_ref_tokens():
    src_path = HERE.parent / "workflows" / "phase_45_spec.py"
    src = src_path.read_text(encoding="utf-8")
    assert "flip-by:2026-07-29" in src, (
        "expected literal 'flip-by:2026-07-29' token in phase_45_spec.py"
    )
    assert "Refs #863" in src, (
        "expected literal 'Refs #863' token in phase_45_spec.py"
    )


# ─── AC12 — §1ab retry-idempotency: repeated calls, same result ───────────


def test_ac12_repeated_calls_with_identical_prev_are_equivalent(tmp_path, monkeypatch):
    uut = _get_uut()

    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_ENFORCE", raising=False)
    monkeypatch.delenv("HAL_SPEC_HELPER_EXTRACTION_GATE", raising=False)

    f = _write(tmp_path, "spec_repeat.md", _SPEC_WITH_INLINE_MATH)
    ctx = _make_ctx()
    prev = _make_prev(f, cycle=1)

    r1 = uut(ctx, prev)
    r2 = uut(ctx, prev)

    assert (r1.status, r1.error_code) == (r2.status, r2.error_code), (
        f"repeated calls must produce identical (status, error_code); "
        f"got r1=({r1.status!r},{r1.error_code!r}) r2=({r2.status!r},{r2.error_code!r})"
    )
