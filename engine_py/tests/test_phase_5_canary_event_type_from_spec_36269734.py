"""RED tests for agreement 36269734 — canary event_type sourced from spec metadata.

Problem: phase_5_integration_canary always resolves event_type as
cfg.get("integration_canary_event_type") or "agent_spawn", so any build
whose integration point emits a non-SubagentStart event (e.g. yolo_permission_decision)
always fails E_CANARY_NO_MATCH (012F2C02 RCA-4).

Fix: phase_45_spec parses a "## Canary Integration" block from the spec and writes a
sidecar JSON at <scratchpad>/integration/canary-meta.json; phase_5_integration_canary
reads the sidecar as a second precedence level between the explicit org_config override
and the hardcoded "agent_spawn" default.

Expected RED FAIL for isolated runs:
  AC1  — CANARY_META_RELPATH constant + _parse_canary_integration callable absent today
  AC2  — _parse_canary_integration does not exist; correct return value never produced
  AC5  — sidecar write path and data["canary_event_type"] not implemented
  AC7  — canary_integration_parsed event never emitted
  AC9  — _verify_integration_event still resolves to "agent_spawn" (sidecar read absent),
           so the yolo_permission_decision event is never matched → status error, not ok
AC3, AC4, AC6, AC8 are absence-guards that may pass today.
AC10 is a regression-guard that passes today.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

import phase_45_spec  # noqa: E402
import phase_5_integration_canary  # noqa: E402
import telemetry_ctx  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers ────────────────────────────────────────────────────────────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for telemetry assertions."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _make_spec_ctx(scratchpad: Path, *, git_cwd: str | None = None) -> WorkflowContext:
    """Build ctx for phase_45_spec._write_spec_doc calls.

    Mirror of make_ctx in test_phase_45_spec_D02C615D.py.
    """
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    else:
        org["git_cwd"] = str(scratchpad)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session-36269734",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_spec_prev(doc_path: Path, raw_response: str, *, cycle: int = 1) -> StepResult:
    """Build a StepResult shaped like Opus writer output, mirroring D02C615D test."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="call_opus_writer",
    )


def _make_canary_ctx(
    *,
    run_id_in_org: str,
    scratchpad_dir: str,
    events_path: str,
    **org_extra,
) -> WorkflowContext:
    """Build a WorkflowContext for canary tests.

    Mirror of _make_ctx in test_phase_5_integration_canary.py, with
    scratchpad_dir added so the sidecar reader can find the file.
    """
    org: dict = {
        "integration_canary_run_id": run_id_in_org,
        "scratchpad_dir": scratchpad_dir,
        "integration_canary_events_path": events_path,
        **org_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="task",
        session_id="canary-test-36269734",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _run_canary_step(ctx: WorkflowContext) -> StepResult:
    wf = phase_5_integration_canary.phase_5_integration_canary_workflow()
    assert wf.steps, "workflow must have at least one step"
    return wf.steps[0].execute(ctx, None)


# ─── AC1 ───────────────────────────────────────────────────────────────────────


def test_ac1_constant_and_helper_exist():
    """AC1: CANARY_META_RELPATH == "integration/canary-meta.json" in BOTH modules,
    and _parse_canary_integration is callable in phase_45_spec.

    Fails today: neither the constant nor the helper exists in either module.
    """
    # Constant in phase_45_spec
    const_spec = getattr(phase_45_spec, "CANARY_META_RELPATH", None)
    assert const_spec is not None, (
        "phase_45_spec.CANARY_META_RELPATH does not exist. "
        "36269734 requires this constant to be added."
    )
    assert const_spec == "integration/canary-meta.json", (
        f"phase_45_spec.CANARY_META_RELPATH expected 'integration/canary-meta.json', "
        f"got {const_spec!r}"
    )

    # Constant in phase_5_integration_canary
    const_canary = getattr(phase_5_integration_canary, "CANARY_META_RELPATH", None)
    assert const_canary is not None, (
        "phase_5_integration_canary.CANARY_META_RELPATH does not exist. "
        "36269734 requires this constant to be added."
    )
    assert const_canary == "integration/canary-meta.json", (
        f"phase_5_integration_canary.CANARY_META_RELPATH expected "
        f"'integration/canary-meta.json', got {const_canary!r}"
    )

    # Both module constants must be string-identical (single source of truth)
    assert const_spec == const_canary, (
        f"CANARY_META_RELPATH mismatch: spec={const_spec!r}, canary={const_canary!r}. "
        "Must be identical strings per design decision §1."
    )

    # _parse_canary_integration must be callable
    fn = getattr(phase_45_spec, "_parse_canary_integration", None)
    assert fn is not None, (
        "phase_45_spec._parse_canary_integration does not exist. "
        "36269734 requires this helper to be added."
    )
    assert callable(fn), (
        f"phase_45_spec._parse_canary_integration is not callable: {fn!r}"
    )


# ─── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_parse_canary_integration_returns_event_type():
    """AC2: _parse_canary_integration with a spec containing a Canary Integration block
    returns {"event_type": "yolo_permission_decision"}.

    Fails today: _parse_canary_integration does not exist (AttributeError).
    """
    fn = phase_45_spec._parse_canary_integration  # type: ignore[attr-defined]

    raw = "## Canary Integration\n\nevent_type: yolo_permission_decision\n"
    result = fn(raw)
    assert result == {"event_type": "yolo_permission_decision"}, (
        f"_parse_canary_integration returned {result!r}; "
        "expected {{'event_type': 'yolo_permission_decision'}}"
    )


# ─── AC3 ───────────────────────────────────────────────────────────────────────


def test_ac3_parse_canary_integration_no_heading_returns_empty():
    """AC3: _parse_canary_integration with no '## Canary Integration' heading returns {}.

    May pass today as an absence-guard once _parse_canary_integration exists;
    fails today only because the helper is absent (AttributeError).
    """
    fn = phase_45_spec._parse_canary_integration  # type: ignore[attr-defined]

    raw = "## Setup\n\nSome setup text.\n\n## Goals\n\nGoals here.\n"
    result = fn(raw)
    assert result == {}, (
        f"No canary integration heading → expected {{}}, got {result!r}"
    )


# ─── AC4 ───────────────────────────────────────────────────────────────────────


def test_ac4_parse_canary_integration_heading_without_event_type_returns_empty():
    """AC4: Heading present but no event_type: line → {}.

    May pass today as absence-guard; fails today because helper is absent.
    """
    fn = phase_45_spec._parse_canary_integration  # type: ignore[attr-defined]

    raw = (
        "## Canary Integration\n"
        "\n"
        "Notes about the integration point.\n"
        "No event_type line here.\n"
        "\n"
        "## Next Heading\n"
        "Other content.\n"
    )
    result = fn(raw)
    assert result == {}, (
        f"Heading with no event_type line → expected {{}}, got {result!r}"
    )


# ─── AC5 ───────────────────────────────────────────────────────────────────────


def test_ac5_write_spec_doc_writes_sidecar_and_data_key(tmp_path):
    """AC5: _write_spec_doc with a spec containing a Canary Integration block writes
    <scratchpad>/integration/canary-meta.json with JSON {"event_type": "yolo_permission_decision"}
    and sets data["canary_event_type"] == "yolo_permission_decision".

    Fails today: _write_spec_doc has no sidecar-write path (constant + helper absent).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_response = (
        "## Context\n\nSome context text.\n\n"
        "## Canary Integration\n\n"
        "event_type: yolo_permission_decision\n\n"
        "## Goals\n\nBuild goals here.\n"
    )

    ctx = _make_spec_ctx(scratchpad)
    prev = _make_spec_prev(doc_path, raw_response, cycle=1)

    result = phase_45_spec._write_spec_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_spec_doc failed unexpectedly: status={result.status!r}, "
        f"error={result.error!r}"
    )

    # Sidecar must exist
    sidecar_path = scratchpad / "integration" / "canary-meta.json"
    assert sidecar_path.exists(), (
        f"Expected sidecar at {sidecar_path} but file was not created. "
        "36269734 AC5: _write_spec_doc must write <scratchpad>/integration/canary-meta.json"
    )

    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_data == {"event_type": "yolo_permission_decision"}, (
        f"Sidecar content expected {{\"event_type\": \"yolo_permission_decision\"}}, "
        f"got {sidecar_data!r}"
    )

    # data["canary_event_type"] must be set
    assert result.data is not None
    assert result.data.get("canary_event_type") == "yolo_permission_decision", (
        f"data['canary_event_type'] expected 'yolo_permission_decision', "
        f"got {result.data.get('canary_event_type')!r}"
    )


# ─── AC6 ───────────────────────────────────────────────────────────────────────


def test_ac6_write_spec_doc_no_block_no_sidecar_no_data_key(tmp_path):
    """AC6: _write_spec_doc with spec lacking the Canary Integration block writes NO
    sidecar and does NOT add "canary_event_type" to data.

    May pass today (no sidecar-write code exists); functions as regression-guard post-GREEN.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_response = "## Context\n\nSome context text.\n\n## Goals\n\nGoals here.\n"

    ctx = _make_spec_ctx(scratchpad)
    prev = _make_spec_prev(doc_path, raw_response, cycle=1)

    result = phase_45_spec._write_spec_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_spec_doc failed: status={result.status!r}, error={result.error!r}"
    )

    # No sidecar must be written
    sidecar_path = scratchpad / "integration" / "canary-meta.json"
    assert not sidecar_path.exists(), (
        f"AC6: Sidecar must NOT be written when spec lacks the block; "
        f"found unexpected file at {sidecar_path}"
    )

    # "canary_event_type" must NOT be in data
    assert result.data is not None
    assert "canary_event_type" not in result.data, (
        f"AC6: 'canary_event_type' must NOT be in data when block absent; "
        f"data keys: {list(result.data.keys())}"
    )


# ─── AC7 ───────────────────────────────────────────────────────────────────────


def test_ac7_write_spec_doc_emits_canary_integration_parsed_telemetry(tmp_path):
    """AC7: _write_spec_doc emits a 'canary_integration_parsed' event with the event_type
    when the block is present; emits no such event when the block is absent.

    Fails today: the _emit_safe("canary_integration_parsed", ...) call does not exist.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_with_block = (
        "## Context\n\nSome context.\n\n"
        "## Canary Integration\n\n"
        "event_type: yolo_permission_decision\n\n"
        "## Goals\n\nGoals.\n"
    )

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="test-36269734-ac7-present",
        step_name="write_spec_doc",
        phase="phase_45_spec",
    )
    try:
        ctx = _make_spec_ctx(scratchpad)
        prev = _make_spec_prev(doc_path, raw_with_block, cycle=1)
        result = phase_45_spec._write_spec_doc(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        f"_write_spec_doc failed: {result.error!r}"
    )

    emitted = [(etype, payload) for etype, payload, _ in log.events
               if etype == "canary_integration_parsed"]
    assert len(emitted) == 1, (
        f"Expected exactly 1 'canary_integration_parsed' event when block present, "
        f"got {len(emitted)}. All events: {[e[0] for e in log.events]}"
    )
    _, payload = emitted[0]
    assert payload.get("event_type") == "yolo_permission_decision", (
        f"canary_integration_parsed payload expected event_type='yolo_permission_decision', "
        f"got {payload!r}"
    )

    # Now verify no such event emitted when block absent
    scratchpad2 = tmp_path / "scratch2"
    scratchpad2.mkdir()
    doc_path2 = scratchpad2 / "specs" / "build-spec.md"
    doc_path2.parent.mkdir(parents=True, exist_ok=True)

    raw_no_block = "## Context\n\nSome context.\n\n## Goals\n\nGoals.\n"

    log2 = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log2,
        run_id="test-36269734-ac7-absent",
        step_name="write_spec_doc",
        phase="phase_45_spec",
    )
    try:
        ctx2 = _make_spec_ctx(scratchpad2)
        prev2 = _make_spec_prev(doc_path2, raw_no_block, cycle=1)
        result2 = phase_45_spec._write_spec_doc(ctx2, prev2)
    finally:
        telemetry_ctx.clear_current_run()

    assert result2.status == "ok", f"_write_spec_doc failed (no-block run): {result2.error!r}"

    absent_events = [e for e in log2.events if e[0] == "canary_integration_parsed"]
    assert len(absent_events) == 0, (
        f"AC7: No 'canary_integration_parsed' event must be emitted when block absent; "
        f"found {len(absent_events)}: {absent_events}"
    )


# ─── AC8 ───────────────────────────────────────────────────────────────────────


def test_ac8_read_canary_meta_event_type(tmp_path):
    """AC8: _read_canary_meta_event_type returns event_type when sidecar exists;
    returns None when scratchpad_dir absent; returns None when sidecar file missing (no crash).

    May pass today as absence-guard; fails today because helper doesn't exist yet.
    """
    fn = phase_5_integration_canary._read_canary_meta_event_type  # type: ignore[attr-defined]

    # Case 1: sidecar exists with valid content
    sidecar_dir = tmp_path / "integration"
    sidecar_dir.mkdir(parents=True)
    sidecar_file = sidecar_dir / "canary-meta.json"
    sidecar_file.write_text(json.dumps({"event_type": "yolo_permission_decision"}))
    cfg_with_sidecar = {"scratchpad_dir": str(tmp_path)}
    result_present = fn(cfg_with_sidecar)
    assert result_present == "yolo_permission_decision", (
        f"Expected 'yolo_permission_decision' when sidecar exists, got {result_present!r}"
    )

    # Case 2: no scratchpad_dir in cfg → None, no crash
    cfg_no_sp = {}
    result_no_sp = fn(cfg_no_sp)
    assert result_no_sp is None, (
        f"Expected None when scratchpad_dir absent, got {result_no_sp!r}"
    )

    # Case 3: scratchpad_dir set but sidecar file missing → None, no crash
    empty_sp = tmp_path / "empty_sp"
    empty_sp.mkdir()
    cfg_missing_file = {"scratchpad_dir": str(empty_sp)}
    result_missing = fn(cfg_missing_file)
    assert result_missing is None, (
        f"Expected None when sidecar file missing, got {result_missing!r}"
    )


# ─── AC9 ───────────────────────────────────────────────────────────────────────


def test_ac9_canary_uses_sidecar_event_type_when_no_org_config_override(tmp_path):
    """AC9 (forcing function): canary with integration_canary_required=True, a run_id set,
    an events file containing a 'yolo_permission_decision' event for that run_id, a sidecar
    with event_type=yolo_permission_decision, and NO integration_canary_event_type in org_config
    → status ok and matched_count >= 1.

    Proves the sidecar value is used as the second precedence level, not the hardcoded
    'agent_spawn' default.

    Fails today: line 63 of phase_5_integration_canary.py resolves to "agent_spawn" since
    _read_canary_meta_event_type does not exist, so the yolo_permission_decision event is
    never matched and status is error / E_CANARY_NO_MATCH.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Pre-stage sidecar deterministically (workflows.md §1i: pre-stage, never race)
    sidecar_dir = scratchpad / "integration"
    sidecar_dir.mkdir(parents=True)
    sidecar_file = sidecar_dir / "canary-meta.json"
    sidecar_file.write_text(json.dumps({"event_type": "yolo_permission_decision"}))

    run_id = "rid-36269734-ac9"
    events_path = tmp_path / "build-events.jsonl"
    _write_events(
        events_path,
        [
            {
                "ts": "2026-05-22T10:00:00.000Z",
                "run_id": run_id,
                "event_type": "yolo_permission_decision",
                "payload": {"decision": "allow", "tool": "Write"},
            }
        ],
    )

    ctx = _make_canary_ctx(
        run_id_in_org=run_id,
        scratchpad_dir=str(scratchpad),
        events_path=str(events_path),
        integration_canary_required=True,
        # Deliberately NO integration_canary_event_type — must come from sidecar
    )

    result = _run_canary_step(ctx)

    assert result.status == "ok", (
        f"AC9 forcing function: expected status='ok' (sidecar event_type used), "
        f"got status={result.status!r}, error_code={getattr(result, 'error_code', None)!r}, "
        f"error={result.error!r}. "
        f"Sidecar was pre-staged at {sidecar_file} with yolo_permission_decision. "
        f"Current code resolves to 'agent_spawn' (hardcoded), not reading the sidecar."
    )
    assert isinstance(result.data, dict)
    matched = result.data.get("matched_count", 0)
    assert matched >= 1, (
        f"AC9: expected matched_count >= 1, got {matched}. "
        f"result.data={result.data!r}"
    )


# ─── AC10 ──────────────────────────────────────────────────────────────────────


def test_ac10_precedence_org_config_wins_and_default_fallback(tmp_path):
    """AC10 (regression-guard): precedence behaves correctly:
    (i) explicit integration_canary_event_type in org_config wins even when sidecar present;
    (ii) neither org_config nor sidecar → required_event_type == "agent_spawn".

    PASSES today: today's code uses cfg.get("integration_canary_event_type") or "agent_spawn",
    which already honors org_config override and uses agent_spawn as default.
    Post-GREEN this guards that the new sidecar level doesn't break either existing behavior.
    """
    # Part (i): org_config explicit value wins over sidecar
    scratchpad_i = tmp_path / "scratch_i"
    scratchpad_i.mkdir()
    sidecar_dir_i = scratchpad_i / "integration"
    sidecar_dir_i.mkdir(parents=True)
    (sidecar_dir_i / "canary-meta.json").write_text(
        json.dumps({"event_type": "yolo_permission_decision"})
    )

    # Events file with the org_config override event type so it can match
    run_id_i = "rid-36269734-ac10i"
    events_i = tmp_path / "events_i.jsonl"
    _write_events(
        events_i,
        [
            {
                "ts": "2026-05-22T10:00:00.000Z",
                "run_id": run_id_i,
                "event_type": "agent_spawn",
                "payload": {
                    "subagent_type": "haiku",
                    "parent_skill": "build",
                    "description": "x",
                },
            }
        ],
    )

    ctx_i = _make_canary_ctx(
        run_id_in_org=run_id_i,
        scratchpad_dir=str(scratchpad_i),
        events_path=str(events_i),
        integration_canary_required=True,
        integration_canary_event_type="agent_spawn",  # explicit override
    )
    result_i = _run_canary_step(ctx_i)

    assert result_i.status == "ok", (
        f"AC10(i): org_config override 'agent_spawn' should match the event in file; "
        f"got status={result_i.status!r}, error_code={getattr(result_i, 'error_code', None)!r}"
    )
    assert result_i.data.get("required_event_type") == "agent_spawn", (
        f"AC10(i): required_event_type should be 'agent_spawn' (from org_config), "
        f"got {result_i.data.get('required_event_type')!r}"
    )

    # Part (ii): no org_config key, no sidecar → default "agent_spawn"
    scratchpad_ii = tmp_path / "scratch_ii"
    scratchpad_ii.mkdir()
    # Deliberately no sidecar

    run_id_ii = "rid-36269734-ac10ii"
    events_ii = tmp_path / "events_ii.jsonl"
    _write_events(events_ii, [])  # empty; we only care about required_event_type, not match

    ctx_ii = _make_canary_ctx(
        run_id_in_org=run_id_ii,
        scratchpad_dir=str(scratchpad_ii),
        events_path=str(events_ii),
        integration_canary_required=True,
        # No integration_canary_event_type, no sidecar → falls back to "agent_spawn"
    )
    result_ii = _run_canary_step(ctx_ii)

    # Status will be error (no matching event in empty file), but required_event_type must
    # be "agent_spawn" (the default fallback)
    assert result_ii.data is not None, "result_ii.data must not be None"
    required = result_ii.data.get("required_event_type")
    assert required == "agent_spawn", (
        f"AC10(ii): with neither org_config key nor sidecar, required_event_type "
        f"must be 'agent_spawn' (default fallback), got {required!r}"
    )
