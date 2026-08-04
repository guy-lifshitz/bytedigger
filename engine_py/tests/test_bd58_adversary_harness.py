"""bd#58 — харнесс исполнения адверсариев + публикация аттестации.

Спека: `docs/decisions/2026-08-04-bd58-adversary-harness-attestation.md`.
Замороженная спека уровней: `2026-07-26_bytedigger_conformance_levels.md`
(HAL `fd35e1304`), §4 (Attestation output), §8 (последний абзац), §9 шаг 1.

Class: щит, зелёный потому что его никто не поднимал. `bd_l2` и `bd_l3` умеют
сказать НЕТ — доказано их отрицательными ногами. Но ни один адверсарий против
собственного хоста не прогоняется, поэтому на реальном логе каждый вердикт
`not-checked`, а публикатора нет вовсе. §8 запрещает ровно это: «уровень
заявляется только по реально исполненным адверсариям; реализация, тихо
считающая неисполненный адверсарий пройденным, сама есть провал конформанса».

Поэтому здесь четыре отрицательные ноги подряд — на каждый способ НЕ сдать
адверсария (`not_executed`, `undefended`, `errored`, заявка выше факта). Грант,
который выдаётся при любом из них, есть тот же перевёрнутый оракул уровнем выше.

`conformance.harness` на этой базе НЕ СУЩЕСТВУЕТ; каждый тест падает в своём
теле (дисциплина bd#24 — отложенные импорты, сбор чист).
"""
from __future__ import annotations


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _attestation(outcomes, *, level_claimed="BD-L2"):
    h = _h()
    return h.build_attestation(
        outcomes,
        level_claimed=level_claimed,
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-08-04T00:00:00Z",
    )


def _all_defended():
    """Каждый исполняемый адверсарий отбит — общая точка отсчёта."""
    h = _h()
    return {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}


# ─── AC1: исполнение РЕАЛЬНОЕ, а не константа ─────────────────────────────

def test_ac1_run_adversaries_covers_the_set_and_actually_defends_one():
    h = _h()
    outcomes = h.run_adversaries()

    assert set(outcomes) == set(h.ADVERSARIES), (
        f"исход обязан быть у каждого адверсария; отсутствуют "
        f"{sorted(set(h.ADVERSARIES) - set(outcomes))!r}"
    )
    assert h.OUTCOME_DEFENDED in outcomes.values(), (
        f"ни один адверсарий не отбит — харнесс, ничего не исполняющий, есть "
        f"ровно тот дефект, который лот закрывает; получено {outcomes!r}"
    )


# ─── AC2-AC4: ТРИ СПОСОБА НЕ СДАТЬ, каждый обязан снимать уровень ─────────

def test_ac2_not_executed_adversary_removes_the_level():
    """§8: неисполненный адверсарий, зачтённый пройденным, — провал конформанса."""
    h, t = _h(), _tok()
    outcomes = _all_defended()
    outcomes["ADV-3"] = t.ADVERSARY_NOT_EXECUTED
    att = _attestation(outcomes)

    assert att["level_achieved"] != "BD-L2", (
        f"BD-L2 выдан при неисполненном ADV-3: {att['level_achieved']!r}"
    )
    assert h.validate_attestation(att), "заявка BD-L2 обязана быть отвергнута"


def test_ac3_undefended_adversary_removes_the_level():
    h = _h()
    outcomes = _all_defended()
    outcomes["ADV-3"] = h.OUTCOME_UNDEFENDED
    att = _attestation(outcomes)

    assert att["level_achieved"] != "BD-L2"
    assert h.validate_attestation(att)


def test_ac4_errored_adversary_is_not_passed_fail_closed():
    """Исполнение, кинувшее исключение, — НЕ сдан, а не «отсутствует».

    Дух R2.4: страж, не достигший вердикта, отказывает. Харнесс, считающий
    свой собственный сбой отсутствием проверки, воспроизводит fail-open ровно
    на том слое, который создан его закрывать.
    """
    h = _h()
    outcomes = _all_defended()
    outcomes["ADV-3"] = h.OUTCOME_ERRORED
    att = _attestation(outcomes)

    assert att["level_achieved"] != "BD-L2"
    assert h.validate_attestation(att)


# ─── AC5: заявка выше факта отвергается ───────────────────────────────────

def test_ac5_claim_above_achievement_is_rejected():
    h, t = _h(), _tok()
    outcomes = {name: t.ADVERSARY_NOT_EXECUTED for name in h.ADVERSARIES}
    att = _attestation(outcomes, level_claimed="BD-L3")

    complaints = h.validate_attestation(att)
    assert complaints, (
        "заявленный уровень выше достигнутого обязан быть отвергнут публикатором"
    )


# ─── AC6: кумулятивность ──────────────────────────────────────────────────

def test_ac6_level_is_cumulative_l1_failure_sinks_l2():
    """Все L2-адверсарии отбиты, но L1 провален ⇒ BD-L2 не выдан."""
    h = _h()
    outcomes = _all_defended()
    outcomes["ADV-1"] = h.OUTCOME_UNDEFENDED
    att = _attestation(outcomes)

    assert att["level_achieved"] not in ("BD-L1", "BD-L2", "BD-L3"), (
        f"уровень кумулятивен: провал L1 обязан снимать L2, получено "
        f"{att['level_achieved']!r}"
    )


# ─── AC7: ADV-9 присутствует и объявлен ───────────────────────────────────

def test_ac7_adv9_is_present_and_declared_not_executed():
    """Молчание про ADV-9 — та самая форма, которую §8 запрещает: он
    декларативен, и это обязано быть ВИДНО, а не выражено отсутствием ключа."""
    att = _attestation(_all_defended())

    assert "ADV-9" in att["adversaries"], "ADV-9 обязан присутствовать в отчёте"
    assert att["adversaries"]["ADV-9"] == _tok().ADVERSARY_NOT_EXECUTED


# ─── AC8/AC9: схема §4 и судья ────────────────────────────────────────────

def test_ac8_attestation_carries_exactly_the_frozen_schema():
    att = _attestation(_all_defended())
    assert set(att) == {
        "level_claimed", "level_achieved", "adversaries", "engine_version",
        "adapter_identity", "host_identity", "timestamp",
    }, f"схема §4 разошлась: {sorted(att)!r}"


def test_ac9_validate_attestation_is_silent_on_a_consistent_one():
    """Судья, всегда жалующийся, так же бесполезен, как всегда молчащий."""
    h = _h()
    att = _attestation(_all_defended(), level_claimed="BD-L3")
    assert h.validate_attestation(att) == (), (
        f"согласованная аттестация не должна вызывать претензий; получено "
        f"{h.validate_attestation(att)!r}"
    )


# ─── AC10/AC11: поверхность и детерминизм ─────────────────────────────────

def test_ac10_public_surface_equals_dunder_all():
    h = _h()
    public = {n for n in vars(h) if not n.startswith("_")}
    assert public == set(h.__all__), (
        f"поверхность разошлась с __all__: лишние "
        f"{sorted(public - set(h.__all__))!r}"
    )


def test_ac11_run_adversaries_is_deterministic():
    """Харнесс, чей вердикт скачет между прогонами, не прибор."""
    h = _h()
    assert h.run_adversaries() == h.run_adversaries()
