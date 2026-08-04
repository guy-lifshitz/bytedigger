"""bd#9 — BD-L2: фальсифицируемый оракул + fail-closed гейты (ADV-3…ADV-6).

Спека: `docs/decisions/2026-08-04-bd9-bd-l2-falsifiable-oracle.md`.
Замороженная спека уровней: `2026-07-26_bytedigger_conformance_levels.md` (HAL
`fd35e1304`), §4 таблица адверсариев, §8 последний абзац.

Class: оракул, который нельзя ФАЛЬСИФИЦИРОВАТЬ, оракулом не является. BD-L2 —
первый уровень, на котором зелёный результат начинает нести информацию, поэтому
у КАЖДОГО требования здесь есть предъявленный вход, на котором чекер обязан
сказать НЕТ. Это мандат лота, а не украшение: оракул с недостижимой веткой
отказа не слабый, а ПЕРЕВЁРНУТЫЙ — он превращает отсутствие свидетельств в
утверждение о соответствии.

`conformance.bd_l2` на этой базе НЕ СУЩЕСТВУЕТ, поэтому каждый тест падает в
СВОЁМ теле. Импорты `conformance.*` — только внутри тел (дисциплина bd#24).

Один построитель лога на все ноги: если `failed` и `passed` собирать разными
фикстурами, разойдутся не вердикты, а фикстуры.
"""
from __future__ import annotations


def _ev(event_type: str, **payload):
    return {"type": event_type, "payload": dict(payload)}


def _red_outcome(*, n_passed: int, n_failed: int, exit_code: int = 1,
                 counted_as: str = "rejected"):
    """Наблюдение R2.1. `counted_as` — то, ЧЕМ движок счёл прогон."""
    return _ev("red_test_outcome", group="py", exit_code=exit_code,
               n_passed=n_passed, n_failed=n_failed, phase=5,
               counted_as=counted_as)


def _vacuity(*, hits=()):
    """Наблюдение R2.2/ADV-3 в РЕАЛЬНОЙ форме `red_stub_passability_violation`.

    bd#61: прежняя версия строила придуманное событие `oracle_vacuity_scan` с
    ключом `findings`. Прод эмитит `red_stub_passability_violation` с `hits`,
    и с bd#61 — на ОБА исхода, что и делает вердикт `passed` достижимым.
    """
    return _ev("red_stub_passability_violation", phase=5, hits=list(hits))


def _gate(*, name: str, raised, outcome: str):
    """Наблюдение R2.4/ADV-5."""
    return _ev("gate_decision", gate=name, raised=raised, outcome=outcome)


def _suppression(*, rows):
    """Наблюдение R2.5/ADV-6 — строки реестра known-reds."""
    return _ev("known_reds_ledger_scan", rows=rows)


def _delta(*, verdict="PASS", baseline_source="declared"):
    """Наблюдение R2.6 в РЕАЛЬНОЙ форме `_baseline_delta.py:92`.

    bd#59: прежняя версия строила придуманные ключи `scoped_result` /
    `full_suite_delta`, которых ни один производитель не пишет — из-за чего
    предикат не мог сработать на живом логе. Ключи приведены к тем, что
    действительно эмитятся.
    """
    return _ev("baseline_delta_gate_verdict", suite="py", verdict=verdict,
               new_fails=[], n_new_fails=0, ledgered=[],
               baseline_source=baseline_source, enforced=True, phase=5)


def _acs(*, binds_observable_effect: bool):
    """Наблюдение R2.3."""
    return _ev("acceptance_criteria_declared",
               criteria=[{"id": "AC1", "binds_observable_effect": binds_observable_effect}])


def _clean_log():
    """Полностью конформный лог — общая точка отсчёта для всех ног."""
    return [
        _red_outcome(n_passed=0, n_failed=1),
        _vacuity(),
        _acs(binds_observable_effect=True),
        _gate(name="baseline_delta", raised=None, outcome="failed"),
        _suppression(rows=[{"issue": "#123", "kill_by": "2099-01-01",
                            "status": "active"}]),
        _delta(),
    ]


def _check(events):
    from bytedigger_engine.conformance.bd_l2 import check_bd_l2  # noqa: PLC0415

    return check_bd_l2(events)


def _t():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _swap(events, event_type, replacement):
    """Заменить ОДНО наблюдение в конформном логе — остальное неизменно."""
    return [replacement if e["type"] == event_type else e for e in events]


# ─── R2.1: отказ ≠ несрабатывание ─────────────────────────────────────────

def test_ac1_r21_genuine_rejection_passes():
    report = _check(_clean_log())
    assert report.labels["verdict:R2.1"] == _t().REQUIREMENT_PASSED


def test_ac2_r21_zero_collected_counted_as_rejection_is_a_violation():
    """ОТРИЦАТЕЛЬНАЯ НОГА. Прогон, не собравший ни одного теста, зачтённый как
    отказ, — это несрабатывание, выданное за отказ. Замороженная спека R2.1:
    load/collection error или таймаут НЕ считаются отказом.
    """
    events = _swap(_clean_log(), "red_test_outcome",
                   _red_outcome(n_passed=0, n_failed=0, exit_code=5,
                                counted_as="rejected"))
    report = _check(events)

    assert report.labels["verdict:R2.1"] == _t().REQUIREMENT_FAILED
    assert any("E_ORACLE_INDETERMINATE" in v for v in report.violations), (
        f"нарушение обязано называть код; получено {report.violations!r}"
    )
    assert report.passed is False


# ─── R2.2 / ADV-3: вакуумный оракул ───────────────────────────────────────

def test_ac3_r22_oracle_mocking_its_own_uut_is_a_violation():
    """ОТРИЦАТЕЛЬНАЯ НОГА, ADV-3. Форма findings — `stub_passability`."""
    events = _swap(_clean_log(), "red_stub_passability_violation", _vacuity(hits=["tests/t.py:30 'compute_digest'"]))
    report = _check(events)

    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_FAILED
    assert any("E_ORACLE_VACUOUS" in v for v in report.violations)
    assert report.passed is False


def test_ac4_r22_clean_oracle_passes():
    report = _check(_clean_log())
    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_PASSED


# ─── R2.4 / ADV-5: гейт с исключением падает ЗАКРЫТО ──────────────────────

def test_ac5_r24_raising_gate_treated_as_absent_is_a_violation():
    """ОТРИЦАТЕЛЬНАЯ НОГА, ADV-5. Гейт, поднявший исключение, обязан считаться
    УПАВШИМ, никогда отсутствующим: гейт, не достигший вердикта, падает закрыто.
    """
    events = _swap(_clean_log(), "gate_decision",
                   _gate(name="baseline_delta", raised="RuntimeError: boom",
                         outcome="absent"))
    report = _check(events)

    assert report.labels["verdict:R2.4"] == _t().REQUIREMENT_FAILED
    assert any("E_GATE_INDETERMINATE" in v for v in report.violations)
    assert report.passed is False


# ─── R2.5 / ADV-6: подавление с владельцем и сроком ───────────────────────

def test_ac6_r25_unbounded_suppression_is_a_violation_both_halves():
    """ОТРИЦАТЕЛЬНАЯ НОГА, ADV-6, ОБЕ половины дизъюнкции.

    «Без владельца ИЛИ просрочено» — дизъюнкция; проверка одной половины
    оставляет вторую недостижимой, то есть ровно ту дыру, ради которой уровень
    и вводится.
    """
    t = _t()

    no_owner = _check(_swap(_clean_log(), "known_reds_ledger_scan", _suppression(
        rows=[{"issue": "", "kill_by": "2099-01-01", "status": "active"}])))
    assert no_owner.labels["verdict:R2.5"] == t.REQUIREMENT_FAILED, "нет владельца"
    assert any("E_SUPPRESSION_UNBOUNDED" in v for v in no_owner.violations)

    expired = _check(_swap(_clean_log(), "known_reds_ledger_scan", _suppression(
        rows=[{"issue": "#123", "kill_by": "2020-01-01", "status": "expired"}])))
    assert expired.labels["verdict:R2.5"] == t.REQUIREMENT_FAILED, "просрочено"
    assert any("E_SUPPRESSION_UNBOUNDED" in v for v in expired.violations)

    bounded = _check(_clean_log())
    assert bounded.labels["verdict:R2.5"] == t.REQUIREMENT_PASSED


# ─── R2.6: дельта полного сьюта — это про ГЕЙТ, не про оракул ─────────────

def test_ac7_r26_scoped_only_result_is_not_passed():
    """ОТРИЦАТЕЛЬНАЯ НОГА. Прямое напоминание issue: R2.6 не про оракул."""
    events = _swap(_clean_log(), "baseline_delta_gate_verdict",
                   _delta(baseline_source=None))
    report = _check(events)

    assert report.labels["verdict:R2.6"] != _t().REQUIREMENT_PASSED
    assert report.passed is False


# ─── R2.3: хотя бы один AC привязан к наблюдаемому эффекту ────────────────

def test_ac8_r23_no_ac_binding_an_observable_effect_is_not_passed():
    events = _swap(_clean_log(), "acceptance_criteria_declared",
                   _acs(binds_observable_effect=False))
    report = _check(events)

    assert report.labels["verdict:R2.3"] != _t().REQUIREMENT_PASSED
    assert report.passed is False


# ─── EDGE-1: нуль свидетельств ≠ соответствие ─────────────────────────────

def test_ac9_empty_log_is_not_checked_and_not_passed():
    """ОТРИЦАТЕЛЬНАЯ НОГА. Отчёт по нулю свидетельств с passed=True — форма B-1."""
    report = _check([])
    t = _t()

    for req in ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6"):
        assert report.labels[f"verdict:{req}"] == t.REQUIREMENT_NOT_CHECKED, req
    assert report.passed is False


# ─── Фильтр входа и пересчёт ──────────────────────────────────────────────

def test_ac10_violation_in_a_foreign_event_type_is_ignored():
    """Реальный лог гетерогенен, корпус фикстур однороден."""
    poisoned = dict(_vacuity(hits=["tests/t.py:2 'x'"]))
    poisoned["type"] = "some_unrelated_event"
    report = _check([*_clean_log(), poisoned])

    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_PASSED
    assert report.passed is True


def test_ac11_recorded_verdict_flag_does_not_override_recomputation():
    """Доверие записанному флагу = эмиттер оценивает себя."""
    lying = _vacuity(hits=["tests/t.py:2 'x'"])
    lying["payload"]["verdict"] = "passed"
    report = _check(_swap(_clean_log(), "red_stub_passability_violation", lying))

    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_FAILED
    assert report.passed is False


# ─── §8: уровень заявляется ТОЛЬКО по исполненным адверсариям ─────────────

def test_ac12_unexecuted_adversary_cannot_be_counted_as_passed():
    """Замороженная спека §8: «реализация, тихо считающая неисполненный
    адверсарий пройденным, сама есть провал конформанса»."""
    from bytedigger_engine.conformance.bd_l2 import validate_report  # noqa: PLC0415
    from bytedigger_engine.conformance.report import L0Report  # noqa: PLC0415

    t = _t()
    lying = L0Report(
        passed=True,
        requirements=("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6"),
        violations=(),
        labels={f"verdict:{r}": t.REQUIREMENT_PASSED
                for r in ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")}
        | {"ADV-3": t.ADVERSARY_NOT_EXECUTED},
    )
    assert validate_report(lying), (
        "отчёт, объявляющий passed по НЕИСПОЛНЕННОМУ адверсарию, обязан быть "
        "отвергнут (§8 замороженной спеки)"
    )


def test_ac13_validate_report_judges_both_ways():
    """Судья, всегда возвращающий (), зелен — поэтому обе стороны."""
    from bytedigger_engine.conformance.bd_l2 import validate_report  # noqa: PLC0415

    assert validate_report(_check(_clean_log())) == ()

    broken = _check(_swap(_clean_log(), "red_stub_passability_violation", _vacuity(hits=["tests/t.py:2 'x'"])))
    assert validate_report(broken) == (), (
        "честный отчёт о нарушении самосогласован — судья не обязан жаловаться"
    )


# ─── Поверхность, контракт, реестр кодов ──────────────────────────────────

def test_ac14_public_surface_equals_dunder_all():
    """B-2. В bd#28 моя первая редакция утекла тремя именами (`annotations`,
    `Any`, `TYPE_CHECKING`) — здесь форма закладывается с первой строки."""
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    # bd#59 добавил AWAITING_PRODUCER — объявленный реестр требований без
    # производителя. Это часть публичного контракта: пробел обязан быть виден.
    # bd#59 добавил ENFORCEMENT — объявленную связь «требование -> прод-отказ».
    # Без неё «отказа нет» и «отказ есть, но выключен» неразличимы.
    assert set(bd_l2.__all__) == {
        "REQUIREMENTS", "AWAITING_PRODUCER", "ENFORCEMENT", "check_bd_l2",
        "validate_report"}
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__), (
        f"поверхность разошлась с __all__: лишние "
        f"{sorted(public - set(bd_l2.__all__))!r}"
    )


def test_ac15_l0report_contract_not_extended():
    import dataclasses  # noqa: PLC0415

    report = _check([])
    assert {f.name for f in dataclasses.fields(report)} == {
        "passed", "requirements", "violations", "labels"}
    assert tuple(report.requirements) == (
        "R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")


def test_ac16_new_error_codes_registered_and_drift_gate_clean():
    """Регистрация кода БЕЗ излучателя даёт `DEAD <CODE>` и роняет гейт дрейфа —
    замерено на bd#48, где так упало пять тестов. Поэтому код и его излучатель
    обязаны приехать одним диффом."""
    from bytedigger_engine.error_codes import ERROR_CODES  # noqa: PLC0415

    for code in ("E_ORACLE_VACUOUS", "E_GATE_INDETERMINATE",
                 "E_SUPPRESSION_UNBOUNDED"):
        assert code in ERROR_CODES, f"{code} не зарегистрирован"
