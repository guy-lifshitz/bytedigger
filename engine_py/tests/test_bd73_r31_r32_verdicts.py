"""bd#73 — R3.1 и R3.2 получают вердикт.

Спека: `docs/decisions/2026-08-05-bd73-r31-r32-verdicts.md`.

Class: объявлено ≠ проверяется. `attest.REQUIREMENT_LABELS` объявляет пять
требований (R3.1, R3.2, R3.3, R3.5, R3.6), `bd_l3.REQUIREMENTS` судит три.
R3.1 и R3.2 несут метку, читающуюся как вердикт, и молчание отчёта о них
неотличимо от «соответствует». Четвёртая разновидность семейства после bd#59,
bd#68 и bd#71.

ЧЕСТНАЯ ГРАНИЦА R3.2 (AC8): чокпоинт валидирует инъекции ДО диспатча и при
отказе не эмитит ничего, поэтому неатрибутированный блок в лог не попадает по
построению. Вердикт R3.2 на реальном логе — ПОДТВЕРЖДЕНИЕ и страж регрессии
чокпоинта, а не независимая проверка. Выдавать его за независимую значило бы
построить ровно тот зелёный щит, который весь заход разбираем.
"""
from __future__ import annotations

GOOD_HASH = "sha256:" + "a" * 64


def _bd_l3():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    return bd_l3


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _ev(**overrides):
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    payload = {
        "step_name": "s1",
        "backend": "b",
        "model_requested": "sonnet",
        "prompt_sha256": GOOD_HASH,
        "injections": [{"source_id": "role-template", "sha256": GOOD_HASH}],
        "declared_capabilities": None,
        "capability_enforcement": "not-enforced",
        "observed_model": None,
        "observed_tools": None,
    }
    payload.update(overrides)
    return {"type": attest.EVENT_TYPE, "payload": payload}


def _verdict(req, **overrides):
    return _bd_l3().check_bd_l3([_ev(**overrides)]).labels[f"verdict:{req}"]


# ─── R3.1: хеш эффективного промпта ──────────────────────────────────────

def test_ac1_valid_prompt_hash_passes():
    assert _verdict("R3.1") == _tok().REQUIREMENT_PASSED


def test_ac2_missing_prompt_hash_is_a_violation():
    """ОТРИЦАТЕЛЬНАЯ НОГА."""
    assert _verdict("R3.1", prompt_sha256=None) == _tok().REQUIREMENT_FAILED


def test_ac3_malformed_hash_is_a_violation_form_not_presence():
    """ОТРИЦАТЕЛЬНАЯ НОГА: ловит проверку «ключ есть».

    `"ok"` удовлетворило бы присутствие и не является хешем — если предикат
    пинует только наличие ключа, эта нога его и вскрывает.
    """
    assert _verdict("R3.1", prompt_sha256="ok") == _tok().REQUIREMENT_FAILED


# ─── R3.2: атрибуция инъекций, ОБЕ половины конъюнкции ───────────────────

def test_ac4_unattributed_injection_is_a_violation_both_halves():
    """ОТРИЦАТЕЛЬНАЯ НОГА x2: `source_id` И `sha256`.

    Конъюнкция: проверка одной половины оставляет вторую недостижимой.
    """
    t, bd_l3 = _tok(), _bd_l3()

    no_src = bd_l3.check_bd_l3([_ev(injections=[{"sha256": GOOD_HASH}])])
    assert no_src.labels["verdict:R3.2"] == t.REQUIREMENT_FAILED, "нет source_id"
    assert any("E_INJECT_UNATTRIBUTED" in v for v in no_src.violations), (
        f"нарушение обязано называть существующий код; {no_src.violations!r}"
    )

    no_hash = bd_l3.check_bd_l3([_ev(injections=[{"source_id": "x"}])])
    assert no_hash.labels["verdict:R3.2"] == t.REQUIREMENT_FAILED, "нет sha256"


def test_ac5_fully_attributed_injection_passes():
    assert _verdict("R3.2") == _tok().REQUIREMENT_PASSED


def test_ac6_no_injections_is_not_a_violation():
    """Пустой список законен: требовать инъекций от каждого шага — не наш случай."""
    assert _verdict("R3.2", injections=[]) != _tok().REQUIREMENT_FAILED


# ─── AC7: ГЕЙТ — каждая метка имеет вердикт или объявлена исключением ────

def test_ac7_every_declared_label_has_a_verdict_or_is_declared_exempt():
    """Обе стороны. Отсутствие этого гейта и дало пробел: R3.1/R3.2 несли
    метку и не имели вердикта, а отчёт молчал."""
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    bd_l3 = _bd_l3()
    labelled = set(attest.REQUIREMENT_LABELS)
    judged = set(bd_l3.REQUIREMENTS)
    exempt = set(bd_l3.LABEL_EXCEPTIONS)

    unjudged = sorted(labelled - judged - exempt)
    assert not unjudged, (
        f"метка объявлена, вердикта нет и исключение не заявлено: {unjudged!r} — "
        "молчание отчёта неотличимо от «соответствует»"
    )
    stale = sorted(exempt & judged)
    assert not stale, (
        f"требование объявлено исключением, но судится: {stale!r} — исключение "
        "обязано исчезать, когда вердикт появился"
    )


# ─── AC8: граница R3.2 объявлена в исходнике, а не только в спеке ───────

def test_ac8_r32_states_that_it_corroborates_rather_than_checks():
    import inspect  # noqa: PLC0415

    doc = inspect.getdoc(_bd_l3()._r32) or ""
    low = doc.lower()
    assert "corroborat" in low or "подтвержд" in low, (
        "докстрока R3.2 обязана называть, что на реальном логе это "
        "подтверждение и страж регрессии, а не независимая проверка"
    )


def test_ac9_requirements_grew_and_surface_matches():
    bd_l3 = _bd_l3()
    assert {"R3.1", "R3.2"} <= set(bd_l3.REQUIREMENTS)
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
