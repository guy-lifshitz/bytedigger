"""RED tests for bd#141 item 4(e): the claude-subprocess backend reports the model it invoked.

Frozen spec: docs/decisions/2026-10-02-bd141-p4e-subprocess-observed-model.md (AC1-AC9).

Class: an observation channel with no producer on the default headless path.
`_pin_mismatch_refusal` (the R3.3 chokepoint) is inert there because
`_invoke_subprocess` never writes `data["observed_model"]`.

Collection safety: `_observed_model_from_events` does not exist yet. It is reached
only through `getattr` inside test bodies, so this module collects cleanly and a
missing attribute is a FAILED assertion, not a collection error.

Seam for AC5-AC8: only the external `claude` binary is faked, by patching
`subprocess.Popen` inside `llm_subprocess` with a process whose stdout is a
stream-json transcript (same seam as tests/test_gh1193_forward_subagent_text.py).
`invoke_llm_subprocess`, `_invoke_subprocess`, `_dispatch_backend` and
`_pin_mismatch_refusal` all run for real. The event log is the real run_ctx
mechanism (`telemetry_ctx.set_current_run`) used by tests/test_bd10_l3_authorship.py.
No `sys.path` mutation, no `from conftest import`.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5"
HAIKU_MODEL = "claude-haiku-4-5"


def _extractor():
    fn = getattr(llm_subprocess, "_observed_model_from_events", None)
    assert fn is not None, (
        "bd#141 4(e) AC1-AC4: llm_subprocess._observed_model_from_events must exist"
    )
    return fn


def _assistant(model, parent=None, *, key_less=False):
    ev = {"type": "assistant", "message": {"model": model, "content": []}}
    if not key_less:
        ev["parent_tool_use_id"] = parent
    return ev


def _init(model=...):
    ev = {"type": "system", "subtype": "init", "parent_tool_use_id": None}
    if model is not ...:
        ev["model"] = model
    return ev


# --- AC1-AC4: the extractor (pure function) --------------------------------

def test_ac1_returns_last_root_assistant_model():
    """AC1: init A, root assistant B then C -> C (last wins, not first, not init)."""
    events = [_init("A-model"), _assistant("B-model"), _assistant("C-model")]
    assert _extractor()(events) == "C-model"


def test_ac2_synthetic_and_invalid_values_are_skipped():
    """AC2: '<'-prefixed, empty and non-str values never override a real value."""
    fn = _extractor()
    assert fn([_assistant("C-model"), _assistant("<synthetic>")]) == "C-model"
    assert fn([_assistant("C-model"), _assistant("<other>")]) == "C-model"
    assert fn([_assistant("C-model"), _assistant("")]) == "C-model"
    assert fn([_assistant("C-model"), _assistant(42)]) == "C-model"
    assert fn([_assistant("C-model"), _assistant(None)]) == "C-model"


def test_ac2_only_synthetic_falls_back_to_init():
    """AC2: only '<synthetic>' plus init A -> A."""
    assert _extractor()([_init("A-model"), _assistant("<synthetic>")]) == "A-model"


def test_ac2_init_value_obeys_the_same_rules():
    """AC2 (same value rules for the fallback): a '<'-prefixed init model is not used."""
    fn = _extractor()
    assert fn([_init("<synthetic>")]) is None
    assert fn([_init("")]) is None
    assert fn([_init(7)]) is None


def _spawner(model=None):
    """Root assistant event that spawns Agent `toolu_agent1` (makes depth-1 events eligible)."""
    ev = _assistant(model)
    ev["message"]["content"] = [
        {"type": "tool_use", "id": "toolu_agent1", "name": "Agent", "input": {}}
    ]
    return ev


def _assert_depth1_eligible(events, sub):
    """Teeth: the subagent event must be kept by _manifest_eligible_events, so an
    extractor that walks that helper instead of root-only would pick it up."""
    eligible = llm_subprocess._manifest_eligible_events(events)
    assert any(e is sub for e in eligible), "fixture sanity: subagent event must be depth-1 eligible"


@pytest.mark.parametrize("spawner_model", [None, "<synthetic>"])
def test_ac3_subagent_events_are_excluded(spawner_model):
    """AC3: a depth-1 (manifest-eligible) subagent assistant model is ignored."""
    fn = _extractor()
    sub = _assistant("SUB-model", parent="toolu_agent1")
    with_init = [_init("A-model"), _spawner(spawner_model), sub]
    _assert_depth1_eligible(with_init, sub)
    assert fn(with_init) == "A-model"
    no_init = [_spawner(spawner_model), sub]
    _assert_depth1_eligible(no_init, sub)
    assert fn(no_init) is None


def test_ac3_root_after_subagent_still_wins_and_subagent_after_root_does_not():
    """AC3 teeth: a depth-1 event arriving LAST must not displace the root value."""
    fn = _extractor()
    sub = _assistant("SUB-model", parent="toolu_agent1")
    events = [_spawner("ROOT-model"), sub]
    _assert_depth1_eligible(events, sub)
    assert fn(events) == "ROOT-model"


def test_ac3b_first_root_init_wins():
    """AC3b: several root inits, no assistant model -> the first valid init wins."""
    assert _extractor()([_init("A-model"), _init("B-model")]) == "A-model"


def test_ac3_key_less_events_count_as_root():
    """AC3 / GH1193 s2.6.1: a missing parent_tool_use_id key is ROOT, not subagent."""
    assert _extractor()([_assistant("KEYLESS-model", key_less=True)]) == "KEYLESS-model"


def test_ac4_defensive_inputs_return_none_without_raising():
    """AC4: [], non-dict events, bad `message`, init without model -> None, never raises."""
    fn = _extractor()
    assert fn([]) is None
    assert fn(["x", 1, None, [], (1,)]) is None
    assert fn([{"type": "assistant"}]) is None
    assert fn([{"type": "assistant", "message": None}]) is None
    assert fn([{"type": "assistant", "message": "str"}]) is None
    assert fn([{"type": "assistant", "message": []}]) is None
    assert fn([{"type": "assistant", "message": {}}]) is None
    assert fn([_init()]) is None
    assert fn([{"type": "system", "subtype": "init", "model": None}]) is None


# --- AC5-AC8: end to end on the claude-subprocess backend ------------------

_RESULT_EVENT = {
    "type": "result", "subtype": "success", "result": "OK",
    "usage": {"input_tokens": 1, "output_tokens": 1,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    "total_cost_usd": 0.001, "duration_ms": 100,
}


class _FakeEventLog:
    """Records (event_type, payload, run_id); same shape as test_bd10_l3_authorship."""

    def __init__(self) -> None:
        self.events: "list[tuple[str, dict, str]]" = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def payloads(self, event_type: str) -> "list[dict]":
        return [p for (t, p, _) in self.events if t == event_type]


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_LLM_PROVIDER", raising=False)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


def _run(events, *, model="opus", extra_data=None):
    """Drive the REAL invoke_llm_subprocess; only the external binary (Popen) is faked."""
    lines = "".join(json.dumps(e) + "\n" for e in [*events, _RESULT_EVENT])

    def _popen(argv, **kwargs):
        proc = MagicMock()
        proc.pid = 99999
        proc.returncode = 0
        proc.stdout = io.StringIO(lines)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(lines, ""))
        return proc

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD141P4E", step_name="bd141_p4e",
        phase="phase_bd141", tier=None,
    )
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=_popen):
        result = llm_subprocess.invoke_llm_subprocess(
            prompt="hi",
            model=model,
            timeout_sec=10,
            step_name="bd141_p4e",
            idle_timeout_sec=0,
            straggler_cfg=None,
            backend="claude-subprocess",
            extra_data=extra_data,
        )
    return result, log


def test_ac5_family_mismatch_is_refused_end_to_end():
    """AC5: dispatched 'opus', transcript answered by a sonnet -> E_MODEL_PIN_MISMATCH."""
    result, log = _run([_init(SONNET), _assistant(SONNET)], model="opus")
    assert result.status == "error", (
        f"a sonnet answering an opus dispatch must be refused; got status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    assert result.error_code == "E_MODEL_PIN_MISMATCH", result.error_code
    mismatches = log.payloads("model_pin_mismatch")
    assert len(mismatches) == 1, f"expected one model_pin_mismatch event; got {mismatches!r}"
    assert mismatches[0]["chokepoint"] is True
    assert mismatches[0]["observed_model"] == SONNET


def test_ac6_matching_family_is_not_refused_and_is_attested():
    """AC6: opus answers an opus dispatch -> ok, data and attestation carry the string."""
    result, log = _run([_init(OPUS), _assistant(OPUS)], model="opus")
    assert result.status == "ok", (
        f"no false positive: got status={result.status!r} error_code={result.error_code!r}"
    )
    assert result.data.get("observed_model") == OPUS
    attested = log.payloads("model_invocation_attested")
    assert len(attested) == 1, f"expected one attestation; got {attested!r}"
    assert attested[0]["observed_model"] == OPUS
    assert log.payloads("model_pin_mismatch") == []


def test_ac7_absent_model_stays_not_checked():
    """AC7: no model anywhere -> ok, and the producer still writes the key as None."""
    result, log = _run([{"type": "system", "subtype": "init"},
                        {"type": "assistant", "message": {"content": []}}], model="opus")
    assert result.status == "ok", (
        f"absence is not-checked, not an error; got {result.status!r} {result.error_code!r}"
    )
    assert "observed_model" in result.data, (
        "the producer line must write observed_model unconditionally (None when absent)"
    )
    assert result.data["observed_model"] is None
    assert log.payloads("model_pin_mismatch") == []


def test_ac8_extra_data_cannot_shadow_observed_model():
    """AC8: the name is reserved; the observed value wins over a caller extra_data value."""
    result, _log = _run([_init(OPUS), _assistant(OPUS)], model="opus",
                        extra_data={"observed_model": "haiku"})
    assert result.status == "ok", (
        f"got status={result.status!r} error_code={result.error_code!r}"
    )
    assert result.data.get("observed_model") == OPUS, (
        f"extra_data shadowed the observed value: {result.data.get('observed_model')!r}"
    )


# --- AC9: registry drained --------------------------------------------------

def test_ac9_r33_is_observable_on_both_main_paths():
    """AC9: R3.3 leaves AWAITING_PRODUCER and observed_model has two producers."""
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    assert "R3.3" not in bd_l3.AWAITING_PRODUCER, (
        f"R3.3 is now produced by _invoke_subprocess and _invoke_in_session; "
        f"got AWAITING_PRODUCER={bd_l3.AWAITING_PRODUCER!r}"
    )
    producers = [
        name for name in ("_invoke_subprocess", "_invoke_in_session")
        if '"observed_model"' in inspect.getsource(getattr(llm_subprocess, name))
    ]
    assert producers == ["_invoke_subprocess", "_invoke_in_session"], producers
