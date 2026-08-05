"""bd#61 — BD-L2 observation producers: R2.2 becomes observable.

Spec: `docs/decisions/2026-08-04-bd61-observation-producers.md`.

Class: an absence of evidence indistinguishable from evidence of absence. The vacuity
scan already runs in production (`phase_5_implement.py:3062-3080`, gate
`HAL_STUB_PASSABILITY_GATE`) and emits `red_stub_passability_violation` — but
ONLY when `stub_hits` is non-empty. A clean scan writes nothing, so
"checked and clean" is indistinguishable from "never checked", and the `passed` verdict
is unreachable by construction.

A CORRECTION TO #61, WHICH I WROTE MYSELF: it says "R2.2 — 0 emitters". The measurement is
sharper — an emitter exists, but it is ONE-SIDED. The defect is not the absence of an event but
the fact that the event is written only on a bad outcome. The fix therefore differs, and is
cheaper.

The heart of the lot is AC3: three DIFFERENT outcomes for three DIFFERENT inputs. Before the lot a clean scan and
a scan that never happened both gave the same `not-checked`.
"""
from __future__ import annotations

EVENT = "red_stub_passability_violation"


def _bd_l2():
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    return bd_l2


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _scan_event(hits):
    """The form the production branch of the gate emits."""
    return {"type": EVENT, "payload": {"phase": 5, "hits": list(hits)}}


# ─── AC3: DISTINGUISHABILITY OF THREE OUTCOMES — the heart of the lot ────

def test_ac3_clean_dirty_and_absent_are_three_distinct_verdicts():
    bd_l2, t = _bd_l2(), _tok()

    clean = bd_l2.check_bd_l2([_scan_event([])])
    dirty = bd_l2.check_bd_l2([_scan_event(["tests/t.py:30 'compute'"])])
    absent = bd_l2.check_bd_l2([])

    assert clean.labels["verdict:R2.2"] == t.REQUIREMENT_PASSED, (
        "a clean scan must give passed — otherwise the positive emission "
        "is pointless"
    )
    assert dirty.labels["verdict:R2.2"] == t.REQUIREMENT_FAILED
    assert absent.labels["verdict:R2.2"] == t.REQUIREMENT_NOT_CHECKED, (
        "no observation — not-checked, and this DIFFERS from a clean scan"
    )
    assert len({
        clean.labels["verdict:R2.2"],
        dirty.labels["verdict:R2.2"],
        absent.labels["verdict:R2.2"],
    }) == 3, "three inputs must give three different verdicts"


# ─── AC5/AC6: the pending registry drains by exactly one entry ───────────

def test_ac5_r22_left_the_awaiting_registry():
    bd_l2 = _bd_l2()
    assert "R2.2" not in bd_l2.AWAITING_PRODUCER, (
        "R2.2 has acquired a producer — the entry must disappear, otherwise "
        "the registry turns into a permanent excuse"
    )


def test_ac6_the_other_three_remain_declared():
    bd_l2, t = _bd_l2(), _tok()
    assert set(bd_l2.AWAITING_PRODUCER) == {"R2.3", "R2.4", "R2.5"}
    report = bd_l2.check_bd_l2([])
    for req in ("R2.3", "R2.4", "R2.5"):
        assert report.labels[f"verdict:{req}"] == t.REQUIREMENT_NOT_CHECKED


# ─── AC1/AC2: the producer emits IN BOTH DIRECTIONS ──────────────────────

def _run_stub_gate(tmp_path, monkeypatch, *, source: str, gate_on: bool = True):
    """Run the production branch of the stub-passability gate and collect the emissions.

    The UUT is not mocked: the real phase-5 function is called through its own entry point,
    only the event sink is substituted.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    red = tmp_path / "test_red.py"
    red.write_text(source, encoding="utf-8")

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        p5, "_emit_safe",
        lambda name, payload, **kw: captured.append((name, payload)),
    )
    monkeypatch.setenv("HAL_STUB_PASSABILITY_GATE", "1" if gate_on else "0")

    # The UUT is the real `_collect_red_lint_findings` of phase 5 (§1aa named helper,
    # GH595 §2.1): it is the one that runs stub-passability and emits the observation.
    p5._collect_red_lint_findings([str(red)], str(tmp_path), None, {})
    return captured


_VACUOUS = '''\
from unittest.mock import patch
from mypkg.subject import compute

def test_thing():
    with patch("mypkg.subject.compute") as m:
        assert compute() is m.return_value
'''

_CLEAN = '''\
from mypkg.subject import compute

def test_thing():
    assert compute(2) == 4
'''


def test_ac1_clean_scan_emits_the_observation(tmp_path, monkeypatch):
    """Without this emission `passed` is unreachable — a clean scan is silent."""
    events = _run_stub_gate(tmp_path, monkeypatch, source=_CLEAN)

    names = [n for n, _ in events]
    assert EVENT in names, (
        f"a clean scan must emit an observation; emitted {names!r}"
    )
    payload = next(p for n, p in events if n == EVENT)
    assert payload["hits"] == [], f"a clean scan — empty hits, got {payload!r}"


def test_ac2_dirty_scan_still_emits_with_hits(tmp_path, monkeypatch):
    """NEGATIVE LEG: a telemetry edit has no right to remove the gate itself."""
    events = _run_stub_gate(tmp_path, monkeypatch, source=_VACUOUS)

    payload = next((p for n, p in events if n == EVENT), None)
    assert payload is not None, "the violation must still be emitted"
    assert payload["hits"], (
        f"a dirty scan must carry non-empty hits, got {payload!r}"
    )


def test_ac4_disabled_gate_is_not_a_clean_scan(tmp_path, monkeypatch):
    """NEGATIVE LEG. A disabled gate has no right to read as "clean" —
    that is exactly the path by which the absence of a check pretends to be its passing.
    """
    events = _run_stub_gate(tmp_path, monkeypatch, source=_CLEAN, gate_on=False)

    assert EVENT not in [n for n, _ in events], (
        "with the gate off there must be no observation"
    )
    report = _bd_l2().check_bd_l2([])
    assert report.labels["verdict:R2.2"] == _tok().REQUIREMENT_NOT_CHECKED


# ─── AC7: the surface ─────────────────────────────────────────────────────

def test_ac7_public_surface_equals_dunder_all():
    bd_l2 = _bd_l2()
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__)
