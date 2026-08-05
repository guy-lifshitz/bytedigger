"""bd#68 — BD-L3: the observation channel gets a producer.

Spec: `docs/decisions/2026-08-05-bd68-l3-observation-producers.md`.

Class: an observation channel with no producer. Measured: `observed_tools` is written by
NOBODY (neither `_invoke_subprocess`, nor `_invoke_in_session`, nor any of the six
reference backends), while `observed_model` is written only in-session. ⇒ R3.5 and R3.6
are forever `not-checked` on a real log, and R3.3 is observable for one backend only.

AGAINST MYSELF: the bd#59 lesson ("a negative leg proves the function CAN
refuse, not that it WILL BE ASKED") I wrote down and did not apply to L3 — I built bd#28
and bd#63 on top of an empty channel. L3 had no gate against a relapse, so the defect
outlived three lots. AC5 is that gate.

The evidence meanwhile already exists: `_written_paths_from_events` walks the transcript
and reads `block["name"]`, selecting write tools. The names of ALL tools
lie in those same blocks, so no new host instrumentation is needed.
"""
from __future__ import annotations


def _L():
    from bytedigger_engine import llm_subprocess  # noqa: PLC0415

    return llm_subprocess


def _bd_l3():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    return bd_l3


def _transcript(*tool_names: str):
    """A stream-json transcript in the form the producer walks."""
    return [{
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": n, "input": {"file_path": f"/x/{n}.py"}}
            for n in tool_names
        ]},
    }]


# ─── AC1/AC2: the producer exists and is NOT always empty ────────────────

def test_ac1_extractor_returns_sorted_deduped_tool_names():
    out = _L()._observed_tools_from_events(_transcript("Read", "Bash", "Read"))
    assert out == ["Bash", "Read"], f"got {out!r}"


def test_ac2_empty_and_nonempty_are_both_reachable():
    """NEGATIVE LEG: a producer that always writes the same thing is as inert
    as its absence."""
    L = _L()
    assert L._observed_tools_from_events([]) == []
    assert L._observed_tools_from_events(_transcript("Bash")) == ["Bash"]


# ─── AC3: the field reaches data ─────────────────────────────────────────

def test_ac3_observed_tools_lands_in_step_result_data(monkeypatch):
    """It is the production function's ASSEMBLY of data that is checked, not just the extractor."""
    import inspect  # noqa: PLC0415

    src = inspect.getsource(_L()._invoke_subprocess)
    assert 'data["observed_tools"]' in src, (
        "_invoke_subprocess must put observed_tools into data — otherwise "
        "the extractor exists and the channel is still empty"
    )
    assert "_observed_tools_from_events" in src


# ─── AC4: the end-to-end link evidence -> verdict ────────────────────────

def test_ac4_escape_seen_only_in_the_transcript_reaches_the_verdict():
    """The chain is closed: an escape in the transcript becomes an R3.6 violation."""
    from bytedigger_engine.conformance import attest, tokens  # noqa: PLC0415

    observed = _L()._observed_tools_from_events(_transcript("Bash"))
    report = _bd_l3().check_bd_l3([{
        "type": attest.EVENT_TYPE,
        "payload": {"step_name": "s1", "model_requested": "sonnet",
                    "observed_model": None, "declared_capabilities": ["Read"],
                    "observed_tools": observed},
    }])
    assert report.labels["verdict:R3.6"] == tokens.REQUIREMENT_FAILED


# ─── AC5: THE GATE AGAINST A RELAPSE, both sides ─────────────────────────

def _producers(field: str) -> int:
    """How many production functions put `field` into StepResult.data."""
    import inspect  # noqa: PLC0415

    L = _L()
    n = 0
    for name in ("_invoke_subprocess", "_invoke_in_session"):
        fn = getattr(L, name, None)
        if fn is not None and f'"{field}"' in inspect.getsource(fn):
            n += 1
    return n


# bd#73: R3.1 and R3.2 read fields written CENTRALLY by `_attest_payload`
# on every attestation, rather than by one backend or another. This lot's gate checks
# producers PER BACKEND, so such requirements are excluded from it —
# their producer is guarded by bd#73 (a label must have a verdict).
_CENTRALLY_PRODUCED = {"R3.1", "R3.2"}

_FIELD_FOR = {"R3.3": "observed_model", "R3.5": "observed_tools",
              "R3.6": "observed_tools"}


def test_ac5_observable_requirements_have_producers_and_registry_is_current():
    bd_l3 = _bd_l3()

    observable = (set(bd_l3.REQUIREMENTS) - set(bd_l3.AWAITING_PRODUCER)
                  - _CENTRALLY_PRODUCED)
    missing = sorted(r for r in observable if _producers(_FIELD_FOR[r]) == 0)
    assert not missing, (
        f"a requirement is declared observable and nobody writes the field: {missing!r}"
    )

    stale = sorted(r for r in bd_l3.AWAITING_PRODUCER
                   if _producers(_FIELD_FOR.get(r, "")) >= 2)
    assert not stale, (
        f"a producer has appeared on every path while the requirement is listed as pending: "
        f"{stale!r} — the registry must drain"
    )


def test_ac6_r33_partial_observability_is_declared():
    """D4: `observed_model` is written only in-session — this is declared, not hidden."""
    bd_l3 = _bd_l3()
    assert "R3.3" in bd_l3.AWAITING_PRODUCER, (
        "R3.3 is observable for one backend only — while that holds, it must be "
        "listed as pending rather than passed off as fully observable"
    )
    assert _producers("observed_model") == 1


# ─── AC7/AC8: the neighbouring contract and the surface ──────────────────

def test_ac7_write_path_extractor_contract_unchanged():
    """`_written_paths_from_events` must still return only the paths of
    write tools — the two meanings are not conflated into one walk."""
    L = _L()
    events = _transcript("Read", "Write")
    assert L._written_paths_from_events(events) == ["/x/Write.py"]


def test_ac8_public_surface_equals_dunder_all():
    bd_l3 = _bd_l3()
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
