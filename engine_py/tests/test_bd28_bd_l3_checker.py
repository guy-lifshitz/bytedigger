"""bd#28 — BD-L3 conformance checker + attestation report.

Спека: `docs/decisions/2026-08-04-bd28-bd-l3-conformance-checker.md`.

Class: оракул, который не умеет отказать (B-1 гейта bd#10 раунда 1, форма
`[G22:18]`, инверсия P2 из CL §1). Чекер с недостижимой веткой `failed` не
слабый, а ПЕРЕВЁРНУТЫЙ: он превращает отсутствие свидетельств в утверждение о
соответствии. Гейт bd#10 назвал три правдоподобных GREEN, проходивших все 36
тестов раунда 1 — ветка `failed` опущена целиком; `passed=True` безусловно;
`violations=()` всегда. Поэтому здесь ветка `failed` утверждается ПЕРВОЙ, а
`passed`/`not-checked` строятся тем же построителем лога, что и она.

`conformance.bd_l3` на этой базе НЕ СУЩЕСТВУЕТ, поэтому каждый тест падает на
`ImportError` в СВОЁМ теле. Импорты `conformance.*` — только внутри тел
(дисциплина bd#24): сбор остаётся чистым, а RED получается assert/import-time
внутри теста, а не collection-error на весь файл.

Интерфейс несёт СПЕКА §3, не этот файл (`CONTRACTS_SPEC.md` §1.5 / bd#7 §3.0).

Форма фикстур снята с живого производителя (`llm_subprocess._attest_payload`,
`:1060-1085`): девять ключей — step_name, backend, model_requested,
prompt_sha256, injections, declared_capabilities, capability_enforcement,
observed_model, observed_tools.
"""
from __future__ import annotations

from typing import Any


def _attested(
    *,
    model_requested: str = "sonnet",
    observed_model: "str | None" = None,
    declared_capabilities: "list[str] | None" = None,
    observed_tools: "list[str] | None" = None,
    capability_enforcement: str = "runtime-allowlist",
    event_type: "str | None" = None,
    extra: "dict[str, Any] | None" = None,
) -> "dict[str, Any]":
    """Одно событие в форме живого производителя.

    ОДИН построитель на все ветки — требование issue «утверждать в обе стороны
    на одном наборе фикстур». Если бы `failed` и `passed` строились разными
    фикстурами, разошлись бы не вердикты, а фикстуры.
    """
    if event_type is None:
        from bytedigger_engine.conformance import attest  # noqa: PLC0415

        event_type = attest.EVENT_TYPE
    payload: "dict[str, Any]" = {
        "step_name": "s1",
        "backend": "claude-subprocess",
        "model_requested": model_requested,
        "prompt_sha256": "sha256:00",
        "injections": [],
        "declared_capabilities": declared_capabilities,
        "capability_enforcement": "declared",
        "observed_model": observed_model,
        "observed_tools": observed_tools,
        # bd#63: R3.5 читает заявление вместе со свидетельством побега. Полностью
        # конформная инвокация ЗАЯВЛЯЕТ принуждение и его не нарушает; бэкенд,
        # ничего не заявивший, оставляет R3.5 в not-checked — проверять нечего.
        "capability_enforcement": capability_enforcement,
    }
    if extra:
        payload.update(extra)
    return {"type": event_type, "payload": payload}


def _check(events):
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415

    return check_bd_l3(events)


def _tokens():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


# ─── AC1/AC2: ветка `failed` — утверждается ПЕРВОЙ ────────────────────────

def test_ac1_r33_family_drift_is_a_violation():
    """Ветка, которой не существовало в RED раунда 1: `REQUIREMENT_FAILED` не
    был даже связан в тест-файле, и ни одна фикстура не подавала нарушение."""
    report = _check([_attested(model_requested="sonnet", observed_model="haiku")])

    assert report.labels["verdict:R3.3"] == _tokens().REQUIREMENT_FAILED
    assert report.passed is False, "нарушение обязано ронять отчёт"
    assert report.violations, "нарушение обязано быть перечислено"


def test_ac2_r36_capability_escape_is_a_violation():
    report = _check([_attested(
        declared_capabilities=["Read"], observed_tools=["bash"],
    )])

    assert report.labels["verdict:R3.6"] == _tokens().REQUIREMENT_FAILED
    assert report.passed is False
    assert report.violations


# ─── AC3: ветка `passed`, ТОТ ЖЕ построитель ──────────────────────────────

def test_ac3_conformant_invocation_passes_both_requirements():
    report = _check([_attested(
        model_requested="sonnet", observed_model="sonnet",
        declared_capabilities=["Read"], observed_tools=["Read"],
    )])
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_PASSED
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_PASSED
    assert report.passed is True
    assert report.violations == ()


# ─── AC4: ветка `not-checked` ─────────────────────────────────────────────

def test_ac4_null_observations_are_not_checked():
    report = _check([_attested(observed_model=None, observed_tools=None)])
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_NOT_CHECKED
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_NOT_CHECKED


# ─── AC5: EDGE-1, ОТРИЦАТЕЛЬНАЯ НОГА — нуль свидетельств ≠ соответствие ───

def test_ac5_empty_log_is_not_checked_and_not_passed():
    """Отчёт по нулю свидетельств, возвращающий passed=True, — чистейшая
    форма B-1: у пустого лога нет ненулевых наблюдений, и `passed` не
    определён ни одним пунктом агрегации. `not-checked` НЕ есть `passed`.
    """
    report = _check([])
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_NOT_CHECKED
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_NOT_CHECKED
    assert report.passed is False, (
        "пустой лог не даёт свидетельств соответствия — passed обязан быть False"
    )


# ─── AC6: EDGE-8, ОТРИЦАТЕЛЬНАЯ НОГА — фильтр входа ───────────────────────

def test_ac6_violation_in_a_foreign_event_type_is_ignored():
    """Реальный лог гетерогенен, а корпус фикстур однороден — GREEN без
    фильтра неотличим на однородном корпусе и врёт на настоящем.

    Нарушающая нагрузка лежит в событии ЧУЖОГО типа; аттестованное событие
    чистое. Вердикт обязан быть `passed`.
    """
    events = [
        _attested(event_type="runner_result_consumed",
                  model_requested="sonnet", observed_model="haiku",
                  declared_capabilities=["Read"], observed_tools=["bash"]),
        _attested(model_requested="sonnet", observed_model="sonnet",
                  declared_capabilities=["Read"], observed_tools=["Read"]),
    ]
    report = _check(events)
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_PASSED, (
        "нарушение в событии чужого типа обязано игнорироваться"
    )
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_PASSED
    assert report.passed is True


# ─── AC7: ОТРИЦАТЕЛЬНАЯ НОГА — пересчёт из payload, не записанный флаг ────

def test_ac7_recorded_verdict_flag_does_not_override_recomputation():
    """Доверять записанному флагу значит дать эмиттеру оценивать самого себя —
    subtype (3) из hal#1373 на уровне всей системы."""
    report = _check([_attested(
        model_requested="sonnet", observed_model="haiku",
        extra={"verdict": "passed", "verdict:R3.3": "passed"},
    )])

    assert report.labels["verdict:R3.3"] == _tokens().REQUIREMENT_FAILED, (
        "вердикт обязан пересчитываться из payload, а не читаться из флага"
    )
    assert report.passed is False


# ─── AC8: B-2 — поверхность экспортов против __all__ ──────────────────────

def test_ac8_public_surface_equals_dunder_all():
    """Замерено: инстансы встроенных типов НЕ несут `__module__`
    (`"x".__module__` -> AttributeError), поэтому форма
    `getattr(value, "__module__", module.__name__) == module.__name__`
    считает импортированные константы экспортами. `bd_l3` обязан импортировать
    `REQUIREMENT_FAILED` — то есть починка B-1 ДЕТОНИРУЕТ B-2. Поэтому
    равенство утверждается против `__all__`, а не против вычисления.
    """
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    assert hasattr(bd_l3, "__all__"), "модуль обязан объявлять __all__ (B-2)"
    # bd#63 не менял поверхность — REQUIREMENTS выросли, имена те же.
    assert set(bd_l3.__all__) == {"REQUIREMENTS", "check_bd_l3", "validate_report"}
    for name in bd_l3.__all__:
        assert hasattr(bd_l3, name), f"__all__ называет отсутствующее имя {name!r}"
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__), (
        f"публичная поверхность разошлась с __all__: лишние "
        f"{sorted(public - set(bd_l3.__all__))!r}"
    )


# ─── AC9: validate_report как судья, обе стороны ──────────────────────────

def test_ac9_validate_report_judges_both_ways():
    """Судья, всегда возвращающий (), зелен — поэтому обе стороны."""
    from bytedigger_engine.conformance.bd_l3 import validate_report  # noqa: PLC0415
    from bytedigger_engine.conformance.report import L0Report  # noqa: PLC0415

    t = _tokens()
    consistent = _check([_attested(
        model_requested="sonnet", observed_model="sonnet",
        declared_capabilities=["Read"], observed_tools=["Read"],
    )])
    assert validate_report(consistent) == ()

    lying = L0Report(
        passed=True,
        requirements=("R3.3", "R3.6"),
        violations=("R3.3: drift",),
        labels={"verdict:R3.3": t.REQUIREMENT_FAILED,
                "verdict:R3.6": t.REQUIREMENT_PASSED},
    )
    assert validate_report(lying), (
        "passed=True при вердикте failed обязан быть отвергнут судьёй"
    )


# ─── AC10/AC11: ADV-9 и нерасширенный контракт ────────────────────────────

def test_ac10_adv9_recorded_as_not_executed():
    report = _check([])
    assert report.labels["ADV-9"] == _tokens().ADVERSARY_NOT_EXECUTED


def test_ac11_l0report_contract_not_extended():
    """Четыре поля — контракт bd#22 (`CONTRACTS_SPEC.md` §2 AC-C2)."""
    import dataclasses  # noqa: PLC0415

    report = _check([])
    names = {f.name for f in dataclasses.fields(report)}
    assert names == {"passed", "requirements", "violations", "labels"}
    # bd#63 добавил R3.5: заявление о принуждении стало опровержимым.
    assert tuple(report.requirements) == ("R3.3", "R3.5", "R3.6")
