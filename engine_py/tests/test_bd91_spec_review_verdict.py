"""RED tests for bd#91 AC15 / AC16 - spec-review majority re-poll removed.

Spec: docs/decisions/2026-10-02-bd91-pregreen-strict-and.md (rev r5), section 3.

UUT: the real `phase_45_spec._invoke_review_llm`. Only the leaf backend
`invoke_llm_subprocess` (a scripted fake, not the UUT) and the event sink
`_emit_safe` (recorder) are replaced.

Dedupe scope for `spec_review_repoll_ignored` is (process, run_id, key); with no
current run the key is `(None, key)`. The dedupe container name is a GREEN detail, so
`_reset_dedupe` clears any module-level set/dict in phase_45_spec whose name looks
like a dedupe store; it is a no-op today. The run id is set through the real
telemetry_ctx, with fixed ids (no timing, workflows.md section 1i).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows import phase_45_spec as p45

_KEYS = ("spec_frozen_review_repolls", "spec_review_repolls")


def _reset_dedupe() -> None:
    for name, val in list(vars(p45).items()):
        low = name.lower()
        if isinstance(val, (set, dict)) and any(
            w in low for w in ("repoll", "ignored", "dedupe", "warned", "seen")
        ):
            val.clear()


@pytest.fixture(autouse=True)
def _isolation():
    telemetry_ctx.clear_current_run()
    _reset_dedupe()
    yield
    telemetry_ctx.clear_current_run()
    _reset_dedupe()


class _Ctx:
    def __init__(self, **cfg):
        self.org_config = cfg


def _prev(is_frozen=False, cycle=1):
    return StepResult(
        status="ok",
        data={"prompt": "review this spec", "doc_path": "/tmp/review.md",
              "spec_path": "/tmp/spec.md", "cycle": cycle, "is_frozen": is_frozen},
        duration_ms=0, step_name="build_review_prompt",
    )


def _ok(verdict):
    return StepResult(status="ok", data={"raw_response": f"## Verdict\n{verdict}\n"},
                      duration_ms=0, step_name="invoke_review_llm")


class _Backend:
    """Scripted fake backend: first REVISE, every later call SHIP."""

    def __init__(self, first="REVISE"):
        self.calls = []
        self.first = first

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _ok(self.first if len(self.calls) == 1 else "SHIP")


class _Events:
    def __init__(self):
        self.rows = []

    def __call__(self, event_type, payload=None, severity="warning"):
        self.rows.append((event_type, dict(payload or {})))

    def of(self, et):
        return [p for (t, p) in self.rows if t == et]


def _run(ctx, prev=None, backend=None, events=None, run_id="run-a"):
    backend = backend or _Backend()
    events = events if events is not None else _Events()
    if run_id is not None:
        telemetry_ctx.set_current_run(event_log=None, run_id=run_id, step_name="invoke_review_llm")
    with patch.object(p45, "invoke_llm_subprocess", side_effect=backend), \
         patch.object(p45, "_emit_safe", side_effect=events):
        result = p45._invoke_review_llm(ctx, prev or _prev())
    return result, backend, events


# --------------------------------------------------------------------------- #
# AC15
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("is_frozen", [False, True], ids=["non_frozen", "frozen"])
def test_ac15_revise_is_final_single_backend_call_no_repoll_event(is_frozen):
    """FAILS today: REVISE triggers a majority re-poll (3 calls, SHIP wins)."""
    result, backend, events = _run(_Ctx(), prev=_prev(is_frozen=is_frozen))
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    assert len(backend.calls) == 1, f"expected exactly 1 backend call, got {len(backend.calls)}"
    assert events.of("phase_45_spec_review_repoll") == []


# --------------------------------------------------------------------------- #
# AC16
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", _KEYS)
def test_ac16_legacy_key_is_ignored_with_one_event(key):
    """FAILS today: the key still drives re-polls; no ignored event exists."""
    _result, backend, events = _run(_Ctx(**{key: 2}))
    assert len(backend.calls) == 1
    ev = events.of("spec_review_repoll_ignored")
    assert len(ev) == 1, events.rows
    assert ev[0]["key"] == key and ev[0]["value"] == 2


def test_ac16_repeat_call_same_run_emits_nothing():
    """FAILS today: no event at all (first call already fails the count)."""
    ctx = _Ctx(spec_frozen_review_repolls=2)
    _r1, _b1, events = _run(ctx, run_id="run-a")
    _r2, _b2, events = _run(ctx, events=events, run_id="run-a")
    assert len(events.of("spec_review_repoll_ignored")) == 1, events.rows


def test_ac16_different_run_id_emits_again():
    """FAILS today: no event."""
    ctx = _Ctx(spec_frozen_review_repolls=2)
    _r1, _b1, events = _run(ctx, run_id="run-a")
    _r2, _b2, events = _run(ctx, events=events, run_id="run-b")
    assert len(events.of("spec_review_repoll_ignored")) == 2, events.rows


def test_ac16_no_current_run_dedupes_per_process_and_key():
    """FAILS today: no event. With no run the dedupe key is (None, key): one event."""
    ctx = _Ctx(spec_review_repolls=2)
    assert telemetry_ctx.get_current_run() is None
    _r1, _b1, events = _run(ctx, run_id=None)
    _r2, _b2, events = _run(ctx, events=events, run_id=None)
    ev = events.of("spec_review_repoll_ignored")
    assert len(ev) == 1, events.rows
    assert ev[0]["key"] == "spec_review_repolls"


@pytest.mark.parametrize("key", _KEYS)
def test_ac16_value_zero_emits_nothing(key):
    """SHIELD-like (green today: no event exists), pins that 0 stays silent."""
    _result, backend, events = _run(_Ctx(**{key: 0}))
    assert len(backend.calls) == 1
    assert events.of("spec_review_repoll_ignored") == []


def test_ac16_both_keys_emit_one_event_each():
    """FAILS today: no events."""
    _result, _backend, events = _run(_Ctx(spec_frozen_review_repolls=2, spec_review_repolls=3))
    ev = events.of("spec_review_repoll_ignored")
    assert sorted(e["key"] for e in ev) == sorted(_KEYS), events.rows
    assert {e["key"]: e["value"] for e in ev} == {
        "spec_frozen_review_repolls": 2, "spec_review_repolls": 3}


def test_ac16_event_emitted_even_when_first_verdict_is_ship():
    """FAILS today: no event. Emission is independent of verdict and is_frozen."""
    backend = _Backend(first="SHIP")
    _result, backend, events = _run(_Ctx(spec_review_repolls=2), backend=backend)
    assert len(backend.calls) == 1
    assert len(events.of("spec_review_repoll_ignored")) == 1, events.rows
