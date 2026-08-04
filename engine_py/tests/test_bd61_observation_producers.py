"""bd#61 — производители наблюдений BD-L2: R2.2 становится наблюдаемым.

Спека: `docs/decisions/2026-08-04-bd61-observation-producers.md`.

Class: отсутствие свидетельства, неотличимое от свидетельства отсутствия. Скан
вакуумности уже работает в проде (`phase_5_implement.py:3062-3080`, гейт
`HAL_STUB_PASSABILITY_GATE`) и эмитит `red_stub_passability_violation` — но
ТОЛЬКО когда `stub_hits` непуст. Чистый скан не пишет ничего, поэтому
«проверено и чисто» неотличимо от «не проверялось», и вердикт `passed`
недостижим по построению.

ПОПРАВКА К #61, КОТОРЫЙ Я САМ ПИСАЛ: там сказано «R2.2 — 0 эмиттеров». Замер
точнее — эмиттер есть, но ОДНОСТОРОННИЙ. Дефект не в отсутствии события, а в
том, что событие пишется только на плохой исход. Починка от этого другая и
дешевле.

Сердце лота — AC3: три РАЗНЫХ исхода на три РАЗНЫХ входа. До лота чистый скан и
несостоявшийся скан давали один и тот же `not-checked`.
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
    """Форма, которую эмитит прод-ветка гейта."""
    return {"type": EVENT, "payload": {"phase": 5, "hits": list(hits)}}


# ─── AC3: РАЗЛИЧИМОСТЬ ТРЁХ ИСХОДОВ — сердце лота ────────────────────────

def test_ac3_clean_dirty_and_absent_are_three_distinct_verdicts():
    bd_l2, t = _bd_l2(), _tok()

    clean = bd_l2.check_bd_l2([_scan_event([])])
    dirty = bd_l2.check_bd_l2([_scan_event(["tests/t.py:30 'compute'"])])
    absent = bd_l2.check_bd_l2([])

    assert clean.labels["verdict:R2.2"] == t.REQUIREMENT_PASSED, (
        "чистый скан обязан давать passed — иначе положительная эмиссия "
        "бессмысленна"
    )
    assert dirty.labels["verdict:R2.2"] == t.REQUIREMENT_FAILED
    assert absent.labels["verdict:R2.2"] == t.REQUIREMENT_NOT_CHECKED, (
        "без наблюдения — not-checked, и это ОТЛИЧАЕТСЯ от чистого скана"
    )
    assert len({
        clean.labels["verdict:R2.2"],
        dirty.labels["verdict:R2.2"],
        absent.labels["verdict:R2.2"],
    }) == 3, "три входа обязаны давать три разных вердикта"


# ─── AC5/AC6: реестр ожидающих пустеет ровно на одну запись ───────────────

def test_ac5_r22_left_the_awaiting_registry():
    bd_l2 = _bd_l2()
    assert "R2.2" not in bd_l2.AWAITING_PRODUCER, (
        "у R2.2 появился производитель — запись обязана исчезнуть, иначе "
        "реестр превращается в вечное оправдание"
    )


def test_ac6_the_other_three_remain_declared():
    bd_l2, t = _bd_l2(), _tok()
    assert set(bd_l2.AWAITING_PRODUCER) == {"R2.3", "R2.4", "R2.5"}
    report = bd_l2.check_bd_l2([])
    for req in ("R2.3", "R2.4", "R2.5"):
        assert report.labels[f"verdict:{req}"] == t.REQUIREMENT_NOT_CHECKED


# ─── AC1/AC2: производитель эмитит В ОБЕ СТОРОНЫ ─────────────────────────

def _run_stub_gate(tmp_path, monkeypatch, *, source: str, gate_on: bool = True):
    """Прогнать прод-ветку гейта stub-passability и собрать эмиссии.

    UUT не мокан: зовётся настоящая функция фазы 5 через её же точку входа,
    подменяется только приёмник событий.
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

    # UUT — настоящий `_collect_red_lint_findings` фазы 5 (§1aa named helper,
    # GH595 §2.1): именно он гоняет stub-passability и эмитит наблюдение.
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
    """Без этой эмиссии `passed` недостижим — чистый скан молчит."""
    events = _run_stub_gate(tmp_path, monkeypatch, source=_CLEAN)

    names = [n for n, _ in events]
    assert EVENT in names, (
        f"чистый скан обязан эмитить наблюдение; эмитировано {names!r}"
    )
    payload = next(p for n, p in events if n == EVENT)
    assert payload["hits"] == [], f"чистый скан — пустой hits, получено {payload!r}"


def test_ac2_dirty_scan_still_emits_with_hits(tmp_path, monkeypatch):
    """ОТРИЦАТЕЛЬНАЯ НОГА: правка телеметрии не имеет права снять сам гейт."""
    events = _run_stub_gate(tmp_path, monkeypatch, source=_VACUOUS)

    payload = next((p for n, p in events if n == EVENT), None)
    assert payload is not None, "нарушение обязано по-прежнему эмититься"
    assert payload["hits"], (
        f"грязный скан обязан нести непустой hits, получено {payload!r}"
    )


def test_ac4_disabled_gate_is_not_a_clean_scan(tmp_path, monkeypatch):
    """ОТРИЦАТЕЛЬНАЯ НОГА. Выключенный гейт не имеет права читаться как «чисто» —
    это ровно тот путь, которым отсутствие проверки притворяется её прохождением.
    """
    events = _run_stub_gate(tmp_path, monkeypatch, source=_CLEAN, gate_on=False)

    assert EVENT not in [n for n, _ in events], (
        "при выключенном гейте наблюдения быть не должно"
    )
    report = _bd_l2().check_bd_l2([])
    assert report.labels["verdict:R2.2"] == _tok().REQUIREMENT_NOT_CHECKED


# ─── AC7: поверхность ─────────────────────────────────────────────────────

def test_ac7_public_surface_equals_dunder_all():
    bd_l2 = _bd_l2()
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__)
