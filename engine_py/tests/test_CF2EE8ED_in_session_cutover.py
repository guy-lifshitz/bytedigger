"""RED tests for CF2EE8ED — HAL_RUNNER_BACKEND default flip to claude-in-session.

Spec: SHARED/memory/Decisions/2026-05-23_CF2EE8ED_in_session_cutover_spec.md
Agreement: CF2EE8ED (SYSTEMATIC — in-session backend production cutover)
Ratified: 2026-05-24

§1d cite-verify table (verified by orchestrator pre-freeze):
  llm_subprocess.py:59        _DEFAULT_BACKEND = "claude-subprocess"  (post-GREEN: "claude-in-session")
  llm_subprocess.py:331-353   def _resolve_backend(kwarg, env) -> tuple[str, str]
  llm_subprocess.py:763       resolved_backend, resolved_source = _resolve_backend(backend, os.environ)
  phase_6_review.py:1059      def _invoke_review_llm(ctx, prev) -> StepResult:
  phase_6_review.py:1067      cfg = ctx.org_config or {}
  phase_6_review.py:1085-1086 # CCBB65DC: straggler-abort watchdog ... / straggler_cfg = None
  phase_6_review.py:227       def _emit_safe(event_type: str, payload: dict) -> None

§1l forcing-function classification (pre-GREEN):
  AC1: FAIL — _resolve_backend(None, {}) returns ("claude-subprocess","default") not ("claude-in-session","default")
  AC2: PASS — regression-fence; env-override path unchanged; documents opt-out lever
  AC3: FAIL — no warn+degrade pre-flight block exists; straggler_abort fires E_LLM_WATCHDOG_UNSUPPORTED
  AC4: SKIP — E2E live-corpus; satisfied at canary step
  AC5: SKIP — canary AC
  AC6: FAIL — runner_backend_resolved event not emitted (emit_resolver_resolved emits different event name)
  AC7: FAIL — same as AC6 (event doesn't exist)

§1q: NO spec_from_file_location / module_from_spec / exec_module used.
§1i: No singleton-resource / timing fixtures.
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch
import io

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
WORKFLOWS = ENGINE_ROOT / "bytedigger_engine/workflows"

# engine_py/ on sys.path (mirrors grandfathered sibling-test pattern).

from bytedigger_engine.llm_subprocess import _resolve_backend, invoke_llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _invoke_review_llm  # noqa: E402
from bytedigger_engine.contracts import StepResult  # noqa: E402


# ─── Fake event log (verbatim from test_4C03CCED_ship1a_backend_selector.py) ──


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


# ─── Fake Popen proc (success path, verbatim from ship1a selector) ────────────

_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_ASSISTANT_DELTA = (
    '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}],'
    '"usage":{"input_tokens":0,"output_tokens":0}}}'
)
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"CF2EE8ED_ANSWER",'
    '"usage":{"input_tokens":5,"output_tokens":5,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":100}'
)


def _success_stdout() -> str:
    lines = [_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK]
    return "".join(line + "\n" for line in lines)


def _make_success_proc() -> MagicMock:
    """Return a mock Popen process that simulates a successful stream-json run."""
    proc = MagicMock()
    proc.pid = 55555
    proc.returncode = 0
    proc.stdout = io.StringIO(_success_stdout())
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.communicate = MagicMock(return_value=(_success_stdout(), ""))
    return proc


# ─── Autouse fixture: clear telemetry between tests ──────────────────────────


@pytest.fixture(autouse=True)
def _clear_telemetry_ctx():
    """Reset telemetry run context before and after every test."""
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ─── AC1 — default resolves to claude-in-session (DEFERRED to CF2EE8ED-FOLLOWUP) ─


@pytest.mark.xfail(
    reason=(
        "CF2EE8ED §3.1 default-flip DEFERRED to CF2EE8ED-FOLLOWUP after "
        "Risk-R1 caller-cascade hang in test_phase_45_spec* family (2026-05-24). "
        "This test asserts the TARGET semantics (default → claude-in-session) and "
        "is expected to FAIL until the follow-up lands. Flip to PASS confirms the "
        "follow-up shipped correctly. DO NOT remove."
    ),
    strict=True,
)
def test_ac1_default_resolves_to_in_session(monkeypatch):
    """AC1 (CF2EE8ED §3.1 — DEFERRED): default resolves to claude-in-session.
    Current state: regression-fence asserting the FOLLOWUP-target. xfail-strict
    until the follow-up agreement lands the actual default-flip.
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    result = _resolve_backend(None, {})
    assert result == ("claude-in-session", "default"), (
        f"AC1 (target): expected ('claude-in-session','default'); got {result!r}. "
        f"Until CF2EE8ED-FOLLOWUP lands, this xfails."
    )


# ─── AC2 — env override back to subprocess ───────────────────────────────────


def test_ac2_env_override_back_to_subprocess(monkeypatch):
    """AC2: HAL_RUNNER_BACKEND=claude-subprocess env override still yields
    ("claude-subprocess", "env").

    Spec AC2: 'HAL_RUNNER_BACKEND=claude-subprocess env override still yields
    ("claude-subprocess", "env")'

    Regression-fence — must continue to PASS post-cutover (opt-out lever).
    CF2EE8ED: env-pin preserves subprocess-dispatch coverage post-cutover
    (default resolves to claude-in-session after GREEN).

    Pre-GREEN: PASS — env-override path in _resolve_backend is unchanged by this
    spec; the path was established in Ship 1A. This is a deliberate regression-fence:
    it asserts the backward-compat behavior that must survive the default flip.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")

    result = _resolve_backend(None, {"HAL_RUNNER_BACKEND": "claude-subprocess"})

    assert result == ("claude-subprocess", "env"), (
        f"AC2: _resolve_backend(None, env) with HAL_RUNNER_BACKEND=claude-subprocess "
        f"must return ('claude-subprocess', 'env'); got {result!r}"
    )


# ─── AC3 — in-session + straggler_abort warns and degrades ───────────────────


def test_ac3_in_session_with_straggler_abort_warns_and_degrades(
    tmp_path, monkeypatch, caplog
):
    """AC3: With cutover + org_config['straggler_abort']=true, _invoke_review_llm does NOT
    return E_LLM_WATCHDOG_UNSUPPORTED; instead (a) logger.warning emitted with token
    'auto-degrading', (b) straggler_abort_skipped_in_session event emitted, (c) step
    proceeds without straggler (straggler_cfg=None passed to invoke_llm_subprocess).

    Spec AC3: 'With cutover + org_config["straggler_abort"]=true, _invoke_review_llm does
    NOT return E_LLM_WATCHDOG_UNSUPPORTED; instead BOTH (a) logger.warning emitted at
    WARNING level with token "auto-degrading" in message AND (b)
    straggler_abort_skipped_in_session event emitted AND (c) step proceeds without
    straggler returning ok-status'

    AC3 forces in-session via HAL_RUNNER_BACKEND=claude-in-session env to deterministically
    reproduce post-cutover behavior pre-GREEN, since pre-cutover default is still
    claude-subprocess (§1i: pre-stage contested state, never race).

    Pre-GREEN FAIL (3 assertions):
      (a) no WARNING log containing "auto-degrading" — pre-flight block not yet coded
      (b) straggler_abort_skipped_in_session event does not exist
      (c) straggler_cfg is NOT None pre-GREEN (current code builds a real straggler_cfg dict
          when straggler_abort=True and does NOT degrade it to None)

    Isolation: forced to in-session via env pin — deterministic, not racy.
    Victim-coupled: directly tests the pre-flight warn+degrade block at phase_6_review.py:1085
    (the production code that GREEN must add). A trivial stub cannot satisfy (a)+(b)+(c).
    Not-already-shipped: grep confirms no warn+degrade block exists at phase_6_review.py:1085.
    """
    # Pre-stage: force in-session backend via env (deterministic, not racy — §1i)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-in-session")

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="ac3-run", step_name="invoke_review_llm", phase="p6"
    )

    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return StepResult(
            status="ok",
            data={"raw_response": "x", "response_bytes": 1, "command": []},
            duration_ms=0,
            step_name="invoke_review_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = types.SimpleNamespace(
        org_config={
            "scratchpad_dir": str(scratchpad),
            "straggler_abort": True,
        }
    )
    prev_data = {
        "doc_path": "doc.md",
        "spec_path": "spec.md",
        "red_log_path": "red.log",
        "green_log_path": "green.log",
        "prompt": "review this",
    }
    prev = StepResult(
        status="ok",
        data=prev_data,
        duration_ms=0,
        step_name="build_review_prompt",
    )

    with caplog.at_level(logging.WARNING, logger="phase_6_review"):
        result = _invoke_review_llm(ctx, prev)

    # (a) WARNING log must contain "auto-degrading"
    assert "auto-degrading" in caplog.text, (
        f"AC3(a): logger.warning must contain 'auto-degrading' when straggler_abort=True "
        f"under claude-in-session backend; got caplog.text={caplog.text!r}. "
        f"Pre-GREEN FAIL: no warn+degrade pre-flight block exists at phase_6_review.py:1085."
    )

    # (b) straggler_abort_skipped_in_session event must be emitted
    event_types = [e[0] for e in log.events]
    assert "straggler_abort_skipped_in_session" in event_types, (
        f"AC3(b): straggler_abort_skipped_in_session event must be emitted; "
        f"got event_types={event_types!r}. "
        f"Pre-GREEN FAIL: event doesn't exist pre-cutover."
    )

    # (c) straggler_cfg passed to invoke_llm_subprocess must be None (auto-degraded)
    assert "straggler_cfg" in captured, (
        "AC3(c): invoke_llm_subprocess must be called (captured must have straggler_cfg key)"
    )
    assert captured["straggler_cfg"] is None, (
        f"AC3(c): straggler_cfg must be None (auto-degraded) when straggler_abort=True "
        f"under claude-in-session; got straggler_cfg={captured.get('straggler_cfg')!r}. "
        f"Pre-GREEN FAIL: current code builds a real straggler_cfg dict, not None."
    )

    # (d) step must NOT return E_LLM_WATCHDOG_UNSUPPORTED
    assert result.error_code != "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"AC3(d): must NOT return E_LLM_WATCHDOG_UNSUPPORTED after degrade; "
        f"got error_code={result.error_code!r}"
    )


# ─── AC4 — E2E smoke (SKIPPED — canary-validated) ────────────────────────────


@pytest.mark.skip(
    reason="E2E live-corpus AC; satisfied at canary stage (bash SYSTEM/cli/build/canary.sh) "
    "and /build smoke, not by pytest. See spec AC4."
)
def test_ac4_default_org_config_e2e_no_error():
    """AC4: With cutover + default org_config, end-to-end /build smoke produces
    non-error StepResult for _invoke_review_llm AND _invoke_fix_llm.

    Spec AC4: 'With cutover + default org_config, end-to-end /build smoke produces
    non-error StepResult for _invoke_review_llm AND _invoke_fix_llm'
    """
    pass


# ─── AC5 — canary green (SKIPPED — canary-validated) ─────────────────────────


@pytest.mark.skip(
    reason="canary AC; satisfied by SYSTEM/cli/build/canary.sh at ship-checklist step 7. "
    "Existing watchdog/manifest baselines verified there, not here. See spec AC5."
)
def test_ac5_canary_green():
    """AC5: Canary (bash SYSTEM/cli/build/canary.sh) GREEN.

    Spec AC5: 'Canary (bash SYSTEM/cli/build/canary.sh) GREEN'
    """
    pass


# ─── AC6 — runner_backend_resolved emitted per call (N>1 multi-call) ─────────


def test_ac6_runner_backend_resolved_emitted_per_call(monkeypatch):
    """AC6: Every call to invoke_llm_subprocess emits exactly one runner_backend_resolved
    event with keys {backend, source, step_name} where source in {"kwarg","env","default",
    "default-fallback"} and backend in _KNOWN_BACKENDS. N>1 multi-call AC (§1l): assert
    exactly-2 events for 2 invocations across two distinct backends.

    Spec AC6: 'Every call to invoke_llm_subprocess (any backend, any source) emits exactly
    one runner_backend_resolved event with keys {backend, source, step_name} where
    source ∈ {"kwarg","env","default","default-fallback"} and backend ∈ _KNOWN_BACKENDS
    ... assert exactly-N events for N invocations across two distinct backends'.
    GH1001 C0B6D653 (2026-07-19): "default-fallback" added — default-path with agent-sdk
    unregistered now reports the actually-dispatched fallback backend/source.

    Pre-GREEN FAIL: runner_backend_resolved event not emitted pre-GREEN. The current code
    calls emit_resolver_resolved() (a different helper/event) — not _emit_safe("runner_backend_resolved", ...).
    grep confirms: zero hits for 'runner_backend_resolved' in llm_subprocess.py today.

    Isolation: fails in isolation — event type simply doesn't exist in production code.
    Victim-coupled: directly tied to the _emit_safe("runner_backend_resolved",...) emission
    site GREEN must add at llm_subprocess.py after _resolve_backend().
    Not-already-shipped: confirmed by grep (zero hits).
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="ac6-run", step_name="s", phase="p"
    )

    from bytedigger_engine.llm_subprocess import _KNOWN_BACKENDS

    # Call 1: explicit kwarg backend="claude-subprocess" (source="kwarg")
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result1 = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="step-kwarg",
            backend="claude-subprocess",
        )

    # Call 2: backend=None + env unset → default source (pre-GREEN: "claude-subprocess";
    # post-GREEN: "claude-in-session"). Either way, source="default". Dispatch may error
    # for in-session (no Popen used); tolerate result.status in {"ok","error"} — we
    # assert ONLY on event presence + payload shape.
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result2 = invoke_llm_subprocess(
            prompt="y",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="step-default",
            backend=None,  # resolves to default
        )

    resolved_events = [e for e in log.events if e[0] == "runner_backend_resolved"]
    event_types = [e[0] for e in log.events]

    # Exactly 2 runner_backend_resolved events for 2 invocations (N>1 §1l)
    assert len(resolved_events) == 2, (
        f"AC6: exactly 2 runner_backend_resolved events expected for 2 invocations; "
        f"got {len(resolved_events)} — all event_types={event_types!r}. "
        f"Pre-GREEN FAIL: event type doesn't exist in production code yet."
    )

    # Each event must have keys {backend, source, step_name}
    for i, ev in enumerate(resolved_events):
        payload = ev[1]
        for key in ("backend", "source", "step_name"):
            assert key in payload, (
                f"AC6: runner_backend_resolved event[{i}] missing key {key!r}; "
                f"payload={payload!r}"
            )
        assert payload["source"] in ("kwarg", "env", "default", "default-fallback"), (
            f"AC6: event[{i}] source must be in "
            f"('kwarg','env','default','default-fallback'); got {payload['source']!r}"
        )
        assert payload["backend"] in _KNOWN_BACKENDS, (
            f"AC6: event[{i}] backend must be in _KNOWN_BACKENDS={_KNOWN_BACKENDS!r}; "
            f"got {payload['backend']!r}"
        )

    # First event: kwarg call → source="kwarg", backend="claude-subprocess"
    p0 = resolved_events[0][1]
    assert p0["source"] == "kwarg", (
        f"AC6: first call (explicit kwarg) must have source='kwarg'; got {p0['source']!r}"
    )
    assert p0["backend"] == "claude-subprocess", (
        f"AC6: first call must have backend='claude-subprocess'; got {p0['backend']!r}"
    )
    assert p0["step_name"] == "step-kwarg", (
        f"AC6: first call step_name must be 'step-kwarg'; got {p0['step_name']!r}"
    )

    # Second event: default call → source="default" or "default-fallback" (GH1001)
    p1 = resolved_events[1][1]
    assert p1["source"] in ("default", "default-fallback"), (
        f"AC6: second call (no kwarg, no env) must have source in "
        f"('default','default-fallback'); got {p1['source']!r}"
    )
    assert p1["step_name"] == "step-default", (
        f"AC6: second call step_name must be 'step-default'; got {p1['step_name']!r}"
    )


# ─── AC7 — default-source event count equals default-resolution invocations ──


def test_ac7_default_source_count_equals_invocation_count(monkeypatch):
    """AC7: Default-source resolution event count after invocations equals total
    invoke_llm_subprocess invocations with backend=None + env unset (i.e. no kwarg/env
    overrides accidentally leak into production code).

    Spec AC7: 'Default-source resolution event count after a full /build smoke equals
    total invoke_llm_subprocess invocations (i.e. no kwarg/env overrides accidentally
    leak into production code)'

    This unit test scopes it: 1 default-source invocation → exactly 1 event with
    source in {"default","default-fallback"} (GH1001 C0B6D653, 2026-07-19: both
    values are the default-path bucket — "default-fallback" is the actually-
    dispatched backend when agent-sdk is unregistered). Validates adoption-%
    sanity gate at unit level.

    Pre-GREEN FAIL: runner_backend_resolved event doesn't exist (same root as AC6).

    Isolation: fails in isolation — same event-absent forcing reason as AC6.
    Victim-coupled: tied to the same _emit_safe("runner_backend_resolved",...) emission.
    Not-already-shipped: confirmed by grep.
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="ac7-run", step_name="s", phase="p"
    )

    # One default-source invocation
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        invoke_llm_subprocess(
            prompt="z",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="step-default-only",
            backend=None,
        )

    default_source_events = [
        e for e in log.events
        if e[0] == "runner_backend_resolved"
        and e[1].get("source") in ("default", "default-fallback")
    ]

    # 1 default-source invocation → exactly 1 default-path-bucket runner_backend_resolved event
    assert len(default_source_events) == 1, (
        f"AC7: 1 default-source invocation must emit exactly 1 runner_backend_resolved "
        f"event with source in ('default','default-fallback'); got "
        f"{len(default_source_events)} — "
        f"all events={[(e[0], e[1]) for e in log.events]!r}. "
        f"Pre-GREEN FAIL: event type doesn't exist in production code yet."
    )

    ev_payload = default_source_events[0][1]
    assert ev_payload.get("source") in ("default", "default-fallback"), (
        f"AC7: event source must be in ('default','default-fallback'); "
        f"got {ev_payload.get('source')!r}"
    )
