"""bd#59 — enforcement: объявленное соответствие «вердикт ⇄ прод-отказ».

Спека: `docs/decisions/2026-08-04-bd59-enforcement-map.md`.

Class: дублирующий страж. Замер экспозиции (обязательный по issue ДО RED)
показал, что прод-отказ УЖЕ существует у всех трёх наблюдаемых требований:
R2.2 — `E_RED_STUB_PASSABLE` (recoverable=False, 6 сайтов, гейт включён по
умолчанию); R2.1 — `E_RED_COLLECT_PROBE` (recoverable=True, флаг принуждения
default=0); R2.6 — блокировка `_baseline_delta` (флаг default=0).

⇒ Провести вердикты L2 в фазы значило бы поставить ВТОРОГО стража на уже
охраняемое условие. Цена известна поимённо из bd#29 §5a: поздний страж
перезаписывает код раннего, и до вызывающего доходит не та причина.

Чего действительно нет — ОБЪЯВЛЕННОЙ связи «требование → (код, флаг, включён
ли)». Сегодня «отказа нет» и «отказ есть, но выключен» выглядят одинаково, и
именно это делает enforcement непроверяемым.
"""
from __future__ import annotations


def _bd_l2():
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    return bd_l2


# ─── AC1/AC3: реестр и реальность не расходятся ──────────────────────────

def test_ac1_every_observable_requirement_is_bound_to_a_consequence():
    bd_l2 = _bd_l2()
    observable = set(bd_l2.REQUIREMENTS) - set(bd_l2.AWAITING_PRODUCER)
    missing = sorted(observable - set(bd_l2.ENFORCEMENT))
    assert not missing, (
        f"требование наблюдаемо, но последствие не объявлено: {missing!r} — "
        "вердикт без объявленного отказа непроверяем"
    )


def test_ac3_unobservable_requirements_declare_no_consequence():
    """ОТРИЦАТЕЛЬНАЯ НОГА: нельзя объявлять последствие тому, чего не наблюдаешь."""
    bd_l2 = _bd_l2()
    overreach = sorted(set(bd_l2.AWAITING_PRODUCER) & set(bd_l2.ENFORCEMENT))
    assert not overreach, (
        f"последствие объявлено требованию без производителя: {overreach!r}"
    )


# ─── AC2: запись обязана называть СУЩЕСТВУЮЩИЕ код и флаг ────────────────

def test_ac2_every_entry_names_a_real_code_and_a_real_flag():
    """ОТРИЦАТЕЛЬНАЯ НОГА. Ссылка на выдуманное имя обязана ронять гейт — это
    ровно та ошибка, которую я допустил дважды: в bd#9 с именами событий и в
    bd#59 с ключами payload. Оба раза всё было зелено, потому что никто не
    сверял объявление с реестром."""
    from bytedigger_engine.error_codes import ERROR_CODES  # noqa: PLC0415
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    bd_l2 = _bd_l2()
    flags = set(flags_catalog.FLAGS)

    for req, entry in bd_l2.ENFORCEMENT.items():
        assert entry["error_code"] in ERROR_CODES, (
            f"{req}: код {entry['error_code']!r} отсутствует в реестре error_codes"
        )
        assert entry["flag"] in flags, (
            f"{req}: флаг {entry['flag']!r} отсутствует в flags_catalog"
        )
        assert isinstance(entry["enforced_by_default"], bool)


# ─── AC4/AC5: ТРИ состояния различимы ────────────────────────────────────

def test_ac4_three_enforcement_states_are_distinguishable():
    """Отказ включён · отказ есть, но выключен · отказа нет — три метки."""
    bd_l2 = _bd_l2()
    report = bd_l2.check_bd_l2([])

    enforced = report.labels["enforcement:R2.2"]
    declared_off = report.labels["enforcement:R2.1"]
    absent = report.labels["enforcement:R2.5"]

    assert len({enforced, declared_off, absent}) == 3, (
        f"три состояния обязаны быть различимы, получено "
        f"{enforced!r} / {declared_off!r} / {absent!r}"
    )


def test_ac5_a_disabled_consequence_never_reads_as_enforced():
    """ОТРИЦАТЕЛЬНАЯ НОГА: выключенный отказ не имеет права выглядеть включённым."""
    bd_l2 = _bd_l2()
    report = bd_l2.check_bd_l2([])

    assert report.labels["enforcement:R2.1"] != report.labels["enforcement:R2.2"], (
        "R2.1 принуждается только под флагом default=0, R2.2 — по умолчанию; "
        "одинаковая метка стирает разницу"
    )


# ─── AC6: замер зафиксирован и обязан упасть при дрейфе ──────────────────

def test_ac6_measured_defaults_are_pinned():
    """Если значение флага в каталоге изменится, этот AC падает и потребует
    решения, а не проедет молча."""
    bd_l2 = _bd_l2()

    assert bd_l2.ENFORCEMENT["R2.2"]["enforced_by_default"] is True
    assert bd_l2.ENFORCEMENT["R2.1"]["enforced_by_default"] is False
    assert bd_l2.ENFORCEMENT["R2.6"]["enforced_by_default"] is False


def test_ac7_public_surface_equals_dunder_all():
    bd_l2 = _bd_l2()
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__)
