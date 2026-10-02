"""bd#152 RED: a per-invocation output digest in `model_invocation_attested`.

Spec: docs/decisions/2026-10-02-bd152-output-digest.md (AC1-AC11).

Harness: a test backend registered through the public `register_backend` seam, the
real chokepoint `llm_subprocess.invoke_llm_subprocess`, a real `telemetry_ctx` run
with a REAL `EventLog` on `tmp_path`; every event is read back from the log file
(workflows.md 1l), never from a mock. Expected digests are computed here with
`hashlib` (AUTHORSHIP_SPEC 0.2), never through `attest.hash_text` or engine code.

Singleton resources (backend registry, telemetry slot, resolver disk sink) are
pre-staged to a known baseline and restored by the autouse fixture; nothing here
depends on timing (workflows.md 1i).

No module-level `sys.path` mutation and no `from conftest import`; only names that
exist today are imported, so this module collects and fails at assert time.

AC -> test map
--------------
AC1  test_ac1_exact_eleven_key_payload
AC2  test_ac2_output_sha256_matches_hashlib[ascii|cyrillic_emoji|empty]
AC3  test_ac3_output_sha256_null_when_not_a_str[...]
AC4  test_ac4_invocation_id_is_32_lowercase_hex_and_distinct_per_dispatch
AC5  test_ac5_returned_data_carries_event_invocation_id_backend_dict_untouched
AC6  test_ac6_forged_invocation_id_is_overwritten_in_run
     test_ac6_forged_invocation_id_is_removed_without_run_context
AC6c test_ac6c_forged_invocation_id_is_removed_when_event_log_is_none
AC6d test_ac6d_hard_gate_floor_refusal_in_a_run_has_no_attestation_and_no_id
AC7  test_ac7_pin_mismatch_refusal_records_output_and_invocation_id
     test_ac7_capability_escape_refusal_records_output_and_invocation_id
AC8  test_ac8_gh1169_fallback_two_events_distinct_ids_own_digests
AC9  test_ac9_failing_emit_does_not_raise_and_returns_unwritten_id
AC10 test_ac10_authorship_spec_lists_new_keys_and_follow_up
AC11 test_ac11_class_i_lint_stays_green
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import re
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog

EVENT_TYPE = "model_invocation_attested"

# Spec 2.3: the nine bd#10 AC-P1 keys plus the two bd#152 keys.
ELEVEN_KEYS = frozenset({
    "step_name", "backend", "model_requested", "prompt_sha256", "injections",
    "declared_capabilities", "capability_enforcement", "observed_model",
    "observed_tools", "invocation_id", "output_sha256",
})

# workflows/phase_2_explore.py:372 on this base: a real declared capability set.
REAL_DECLARED_TOOLS = [
    "Read", "Grep", "Glob", "WebSearch", "WebFetch", "Write",
    "Bash(graphify-shim.sh:*)",
]

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def sha256_of(text: str) -> str:
    """Spec digest form, from stdlib only (never `attest.hash_text`)."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Doubles and harness
# ---------------------------------------------------------------------------

class _Backend:
    """Test backend returning a caller-chosen `StepResult.data` object verbatim.

    Keeps `last_data` (the exact object handed back) so AC5 can check identity.
    """

    def __init__(self, data, *, status: str = "ok", error_code=None,
                 recoverable: bool = True) -> None:
        self._data = data
        self._status = status
        self._error_code = error_code
        self._recoverable = recoverable
        self.calls = 0
        self.last_data = None

    def __call__(self, **kwargs) -> StepResult:
        self.calls += 1
        self.last_data = self._data
        return StepResult(
            status=self._status,
            data=self._data,
            duration_ms=0,
            step_name=kwargs.get("step_name", "bd152"),
            error=None if self._status == "ok" else "bd152 backend error",
            error_code=self._error_code,
            recoverable=self._recoverable,
        )


class _RaisingAttestLog(EventLog):
    """Real EventLog that raises for EXACTLY the attestation event type
    (AC-P6 pattern), recording how often it was asked to."""

    def __init__(self, path) -> None:
        super().__init__(path)
        self.attempts = 0

    def append(self, event_type, payload, run_id=None):
        if event_type == EVENT_TYPE:
            self.attempts += 1
            raise RuntimeError("event log refuses the attestation")
        return super().append(event_type, payload, run_id)


def register(name: str, adapter, *, capabilities=("manifest",)) -> None:
    llm_subprocess.register_backend(
        name, adapter,
        manifest_source="harness_tool_record",
        capabilities=frozenset(capabilities),
        overwrite=True,
    )


def set_run(log) -> None:
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD152", step_name="invoke_bd152_llm",
        phase="phase_bd152", tier=None,
    )


def invoke(**overrides) -> StepResult:
    kwargs = dict(
        prompt="BD152 PROMPT BODY",
        model="sonnet",
        timeout_sec=1,
        step_name="invoke_bd152_llm",
        idle_timeout_sec=0,
        straggler_cfg=None,
        backend="bd152-rec",
    )
    kwargs.update(overrides)
    return llm_subprocess.invoke_llm_subprocess(**kwargs)


def attests(log: EventLog) -> list[dict]:
    """Attestation payloads read back from the log FILE."""
    return [e["payload"] for e in log.read_all() if e["event_type"] == EVENT_TYPE]


@pytest.fixture(autouse=True)
def _bd152_isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


@pytest.fixture
def log(tmp_path) -> EventLog:
    return EventLog(tmp_path / "events.jsonl")


def _one_attest(log: EventLog, what: str) -> dict:
    got = attests(log)
    assert len(got) == 1, f"{what}: expected exactly one {EVENT_TYPE!r} event; got {len(got)}"
    return got[0]


# ---------------------------------------------------------------------------
# AC1 - AC2 - AC3
# ---------------------------------------------------------------------------

def test_ac1_exact_eleven_key_payload(log) -> None:
    """AC1: one dispatch -> one event whose payload keys equal the eleven."""
    register("bd152-rec", _Backend({"raw_response": "hello"}))
    set_run(log)
    result = invoke()
    assert result.status == "ok", f"AC1 precondition: dispatch must succeed; got {result.status!r}"
    payload = _one_attest(log, "AC1")
    assert set(payload) == set(ELEVEN_KEYS), (
        f"AC1: payload keys must be EXACTLY {sorted(ELEVEN_KEYS)!r}; got {sorted(payload)!r} "
        f"(missing={sorted(ELEVEN_KEYS - set(payload))!r}, extra={sorted(set(payload) - ELEVEN_KEYS)!r})"
    )


@pytest.mark.parametrize(
    "raw",
    ["plain ascii answer", "\N{CYRILLIC CAPITAL LETTER O}\N{CYRILLIC SMALL LETTER TE}\N{CYRILLIC SMALL LETTER VE}\N{CYRILLIC SMALL LETTER IE}\N{CYRILLIC SMALL LETTER TE} \N{CYRILLIC SMALL LETTER EM}\N{CYRILLIC SMALL LETTER O}\N{CYRILLIC SMALL LETTER DE}\N{CYRILLIC SMALL LETTER IE}\N{CYRILLIC SMALL LETTER EL}\N{CYRILLIC SMALL LETTER I} \U0001F680 \N{CYRILLIC SMALL LETTER GHE}\N{CYRILLIC SMALL LETTER O}\N{CYRILLIC SMALL LETTER TE}\N{CYRILLIC SMALL LETTER O}\N{CYRILLIC SMALL LETTER VE}\N{CYRILLIC SMALL LETTER O}", "", "  answer\n"],
    ids=["ascii", "cyrillic_emoji", "empty", "whitespace"],
)
def test_ac2_output_sha256_matches_hashlib(raw, log) -> None:
    """AC2: output_sha256 equals the test-computed digest of raw_response (empty included)."""
    register("bd152-rec", _Backend({"raw_response": raw}))
    set_run(log)
    invoke()
    payload = _one_attest(log, "AC2")
    expected = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert payload.get("output_sha256") == expected, (
        f"AC2: output_sha256 must equal {expected!r} for raw_response={raw!r}; "
        f"got {payload.get('output_sha256')!r}"
    )


@pytest.mark.parametrize(
    "data",
    [
        {"worker_written_paths": []},
        {"raw_response": {"nested": "dict"}},
        {"raw_response": None},
        {"raw_response": 42},
        None,
    ],
    ids=["absent", "dict_value", "none_value", "int_value", "data_not_dict"],
)
def test_ac3_output_sha256_null_when_not_a_str(data, log) -> None:
    """AC3: output_sha256 is null unless raw_response is a str; event still has all eleven keys."""
    register("bd152-rec", _Backend(data))
    set_run(log)
    result = invoke()
    assert result.status == "ok", f"AC3 precondition: dispatch returns normally; got {result.status!r}"
    payload = _one_attest(log, "AC3")
    assert set(payload) == set(ELEVEN_KEYS), (
        f"AC3: the event must still carry all eleven keys; got {sorted(payload)!r}"
    )
    assert payload["output_sha256"] is None, (
        f"AC3: output_sha256 must be null for data={data!r}; got {payload['output_sha256']!r}"
    )
    if data is None:
        assert result.data is None, (
            f"AC3: non-dict data must stay as it is (None), not be wrapped; got {result.data!r}"
        )


# ---------------------------------------------------------------------------
# AC4 - AC5 - AC6
# ---------------------------------------------------------------------------

def test_ac4_invocation_id_is_32_lowercase_hex_and_distinct_per_dispatch(log) -> None:
    """AC4: 32 lowercase hex; two dispatches of one step_name yield distinct ids."""
    register("bd152-rec", _Backend({"raw_response": "same"}))
    set_run(log)
    invoke()
    invoke()
    got = attests(log)
    assert len(got) == 2, f"AC4: two dispatches must emit two events; got {len(got)}"
    ids = [p.get("invocation_id") for p in got]
    for i in ids:
        assert isinstance(i, str) and _HEX32.match(i), (
            f"AC4: invocation_id must be 32 lowercase hex chars; got {i!r}"
        )
    assert ids[0] != ids[1], f"AC4: two dispatches must have distinct invocation ids; got {ids!r}"


def test_ac5_returned_data_carries_event_invocation_id_backend_dict_untouched(log) -> None:
    """AC5: returned data["invocation_id"] equals the event's; the backend's dict is not mutated."""
    original = {"raw_response": "answer", "extra": ["x"]}
    snapshot = copy.deepcopy(original)
    backend = _Backend(original)
    register("bd152-rec", backend)
    set_run(log)
    result = invoke()
    payload = _one_attest(log, "AC5")
    assert isinstance(result.data, dict), f"AC5: returned data must be a dict; got {result.data!r}"
    assert result.data.get("invocation_id") == payload.get("invocation_id") is not None, (
        f"AC5: returned data['invocation_id'] must equal the event's "
        f"{payload.get('invocation_id')!r}; got {result.data.get('invocation_id')!r}"
    )
    assert result.data is not original, "AC5: the returned data must be a NEW dict, not the backend's"
    assert original == snapshot, (
        f"AC5: the backend's own dict must be unchanged; before={snapshot!r}, after={original!r}"
    )
    assert "invocation_id" not in original, "AC5: the backend's dict must not gain invocation_id"
    assert result.data.get("raw_response") == "answer" and result.data.get("extra") == ["x"], (
        f"AC5: the returned data must keep the backend's other keys; got {result.data!r}"
    )


def test_ac6_forged_invocation_id_is_overwritten_in_run(log) -> None:
    """AC6 (in run): a backend-set data["invocation_id"] is replaced by the event's id."""
    original = {"raw_response": "r", "invocation_id": "forged"}
    snapshot = copy.deepcopy(original)
    register("bd152-rec", _Backend(original))
    set_run(log)
    result = invoke()
    assert original == snapshot, f"AC6: the backend's dict must be unchanged; got {original!r}"
    assert result.data is not original, "AC6: the returned data must be a NEW dict"
    payload = _one_attest(log, "AC6")
    assert set(payload) == set(ELEVEN_KEYS), f"AC6: event must carry all eleven keys; got {sorted(payload)!r}"
    assert result.data.get("invocation_id") != "forged", "AC6: a forged id must not survive"
    assert result.data.get("invocation_id") == payload.get("invocation_id") is not None, (
        f"AC6: the returned id must be the event's {payload.get('invocation_id')!r}; "
        f"got {result.data.get('invocation_id')!r}"
    )


def test_ac6_forged_invocation_id_is_removed_without_run_context() -> None:
    """AC6 (no run): with no event emitted the key is absent and nothing raises."""
    original = {"raw_response": "r", "invocation_id": "forged"}
    snapshot = copy.deepcopy(original)
    register("bd152-rec", _Backend(original))
    telemetry_ctx.clear_current_run()
    assert telemetry_ctx.get_current_run() is None, "fixture sanity: no run context"
    result = invoke()
    assert original == snapshot, f"AC6: the backend's dict must be unchanged on the no-run path; got {original!r}"
    assert result.data is not original, "AC6: the returned data must be a NEW dict even with no run"
    assert result.status == "ok", f"AC6: dispatch must return normally; got {result.status!r}"
    assert isinstance(result.data, dict) and result.data.get("raw_response") == "r", (
        f"AC6: other backend data must survive; got {result.data!r}"
    )
    assert "invocation_id" not in result.data, (
        f"AC6: with no attestation emitted, data must not carry invocation_id; "
        f"got {result.data.get('invocation_id')!r}"
    )


def test_ac6c_forged_invocation_id_is_removed_when_event_log_is_none() -> None:
    """AC6c: a run context whose event_log is None emits nothing, so no key survives.

    Red today: the current tree passes the backend's forged id through untouched.
    """
    original = {"raw_response": "r", "invocation_id": "forged"}
    snapshot = copy.deepcopy(original)
    register("bd152-rec", _Backend(original))
    telemetry_ctx.set_current_run(event_log=None, run_id="R", step_name="s")
    result = invoke()
    assert result.status == "ok", f"AC6c: dispatch must return normally; got {result.status!r}"
    assert original == snapshot and result.data is not original, (
        "AC6c: the backend's dict must be untouched and the returned data a new dict"
    )
    assert result.data.get("raw_response") == "r"
    assert "invocation_id" not in result.data, (
        f"AC6c: with no event log, data must not carry invocation_id; got {result.data.get('invocation_id')!r}"
    )


def test_ac6d_hard_gate_floor_refusal_in_a_run_has_no_attestation_and_no_id(log) -> None:
    """AC6d: a pre-dispatch floor refusal (hard_gate + haiku) in a run: no event, no id.

    Likely a green guard on the current tree (the refusal builder uses data=None);
    it pins the behavior against a GREEN that stamps every return of the dispatcher.
    """
    backend = _Backend({"raw_response": "never"})
    register("bd152-rec", backend)
    set_run(log)
    result = invoke(model="haiku", hard_gate=True, gate_label="g")
    assert result.status == "error" and backend.calls == 0, (
        f"AC6d precondition: refused before dispatch; got {result.status!r}, calls={backend.calls}"
    )
    assert attests(log) == [], "AC6d: a pre-dispatch refusal must emit no attestation"
    assert not (isinstance(result.data, dict) and "invocation_id" in result.data), (
        f"AC6d: a pre-dispatch refusal must carry no invocation_id; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC7 - AC8 - AC9
# ---------------------------------------------------------------------------

def test_ac7_pin_mismatch_refusal_records_output_and_invocation_id(log) -> None:
    """AC7 (R3.3): the refused invocation still records output_sha256 and its id."""
    raw = "drifted answer"
    register("bd152-rec", _Backend(
        {"raw_response": raw, "observed_model": "claude-haiku-4-5-20251001"}))
    set_run(log)
    result = invoke(model="sonnet")
    assert result.status == "error" and result.error_code == "E_MODEL_PIN_MISMATCH", (
        f"AC7 precondition: pin mismatch must refuse; got {result.status!r}/{result.error_code!r}"
    )
    payload = _one_attest(log, "AC7 pin")
    assert payload.get("output_sha256") == sha256_of(raw), (
        f"AC7: refused event must carry the backend's output digest {sha256_of(raw)!r}; "
        f"got {payload.get('output_sha256')!r}"
    )
    assert result.data.get("invocation_id") == payload.get("invocation_id") is not None, (
        f"AC7: refusal data['invocation_id'] must equal the event's; got "
        f"{result.data.get('invocation_id')!r} vs {payload.get('invocation_id')!r}"
    )


def test_ac7_capability_escape_refusal_records_output_and_invocation_id(log) -> None:
    """AC7 (R3.6): the escaped invocation still records output_sha256 and its id."""
    raw = "escaped answer"
    register("bd152-rec", _Backend({"raw_response": raw, "observed_tools": ["Read", "Task"]}))
    set_run(log)
    result = invoke(allowed_tools=list(REAL_DECLARED_TOOLS))
    assert result.status == "error" and result.error_code == "E_CAPABILITY_ESCAPE", (
        f"AC7 precondition: escape must refuse; got {result.status!r}/{result.error_code!r}"
    )
    payload = _one_attest(log, "AC7 escape")
    assert payload.get("output_sha256") == sha256_of(raw), (
        f"AC7: refused event must carry the backend's output digest {sha256_of(raw)!r}; "
        f"got {payload.get('output_sha256')!r}"
    )
    assert result.data.get("invocation_id") == payload.get("invocation_id") is not None, (
        f"AC7: refusal data['invocation_id'] must equal the event's; got "
        f"{result.data.get('invocation_id')!r} vs {payload.get('invocation_id')!r}"
    )


def test_ac8_gh1169_fallback_two_events_distinct_ids_own_digests(monkeypatch, log) -> None:
    """AC8: the agent-sdk -> claude-subprocess fallback emits two events with their own digests/ids."""
    monkeypatch.delenv("HAL_AGENT_SDK_HANG_FALLBACK", raising=False)
    hang_raw, fb_raw = "partial output before the hang", "fallback answer"
    caps = ("manifest", "progress_since", "abort")
    register("agent-sdk", _Backend(
        {"raw_response": hang_raw, "hang_attempts": 1},
        status="error", error_code="E_LLM_API_TIMEOUT", recoverable=True,
    ), capabilities=caps)
    fallback = _Backend({"raw_response": fb_raw})
    register("claude-subprocess", fallback, capabilities=caps)
    set_run(log)
    result = invoke(backend="agent-sdk")
    assert fallback.calls == 1, f"AC8 precondition: the fallback must run; calls={fallback.calls}"
    got = attests(log)
    assert [p["backend"] for p in got] == ["agent-sdk", "claude-subprocess"], (
        f"AC8: two events in dispatch order expected; got {[p['backend'] for p in got]!r}"
    )
    assert [p.get("output_sha256") for p in got] == [sha256_of(hang_raw), sha256_of(fb_raw)], (
        f"AC8: each event must carry its own backend's digest; got "
        f"{[p.get('output_sha256') for p in got]!r}"
    )
    ids = [p.get("invocation_id") for p in got]
    assert all(isinstance(i, str) and _HEX32.match(i) for i in ids) and ids[0] != ids[1], (
        f"AC8: distinct 32-hex invocation ids expected; got {ids!r}"
    )
    assert result.data.get("invocation_id") == ids[1], (
        f"AC8: the returned id must be the SECOND event's {ids[1]!r}; "
        f"got {result.data.get('invocation_id')!r}"
    )


def test_ac9_failing_emit_does_not_raise_and_returns_unwritten_id(tmp_path) -> None:
    """AC9: a raising log does not break dispatch; the returned id names a never-written event."""
    failing = _RaisingAttestLog(tmp_path / "events.jsonl")
    register("bd152-rec", _Backend({"raw_response": "r"}))
    set_run(failing)
    result = invoke()
    assert failing.attempts == 1, (
        f"AC9: the attestation must have been attempted once; got {failing.attempts}"
    )
    assert result.status == "ok", f"AC9: a failing emit must not change the outcome; got {result.status!r}"
    assert attests(failing) == [], "AC9: the failing log must hold no attestation event"
    returned = (result.data or {}).get("invocation_id")
    assert isinstance(returned, str) and _HEX32.match(returned), (
        f"AC9 (declared limit): the returned id still names the unwritten event; got {returned!r}"
    )


# ---------------------------------------------------------------------------
# AC10 - AC11
# ---------------------------------------------------------------------------

def _authorship_spec_text() -> str:
    import bytedigger_engine
    path = Path(bytedigger_engine.__file__).parent / "conformance" / "AUTHORSHIP_SPEC.md"
    return path.read_text(encoding="utf-8")


def test_ac10_authorship_spec_lists_new_keys_and_follow_up() -> None:
    """AC10: spec section 3 AC-P1 lists both keys; section 4 drops 'records no model output'; names bd#206."""
    text = _authorship_spec_text()
    flat = " ".join(text.split())  # the source wraps lines mid-sentence
    start = text.index("- **AC-P1**")
    end = text.index("`[bd10:24]`", start)
    ac_p1 = text[start:end]
    assert "invocation_id" in ac_p1 and "output_sha256" in ac_p1, (
        "AC10: section 3 AC-P1 must list `invocation_id` and `output_sha256`"
    )
    assert "The event log records no model output" not in flat, (
        "AC10: section 4 class M must no longer claim the log records no model output"
    )
    m_start = text.index("**Class M,")
    m_end = text.index("**Chunk rule.**", m_start)
    class_m = " ".join(text[m_start:m_end].split())
    assert "bd#206" in class_m, "AC10: the class-M paragraph itself must name the follow-up issue bd#206"
    assert "invocation:<step_name>:<invocation_id>" in class_m, (
        "AC10: the class-M paragraph must state the matching rule `invocation:<step_name>:<invocation_id>`"
    )
    r32 = [ln for ln in text.splitlines() if ln.lstrip().startswith("| `R3.2` |")]
    assert len(r32) == 1, f"AC10: expected exactly one R3.2 label-table row; got {len(r32)}"
    assert "bd#206" in r32[0], "AC10: the R3.2 row must name bd#206 as the class-M follow-up"
    assert "(#152) are follow-ups" not in " ".join(r32[0].split()), (
        "AC10: the R3.2 row must no longer say '(#152) are follow-ups'"
    )


def test_ac11_class_i_lint_stays_green() -> None:
    """AC11: the class-I inventory lint is clean on the real tree (same entry as bd#150 AC10)."""
    import bytedigger_engine
    lint = importlib.import_module("bytedigger_engine.conformance.class_i_lint")
    root = Path(bytedigger_engine.__file__).parent
    assert lint.check(root, lint.load_inventory()) == []
