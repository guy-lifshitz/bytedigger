"""bd#63 — R3.5: заявление о принуждении становится опровержимым.

Спека: `docs/decisions/2026-08-04-bd63-r35-enforcement-falsifiable.md`.

Class: актор оценивает себя. `capability_enforcement` приходит из реестра,
который заполняет сам бэкенд (`register_backend(..., capabilities=...)`).
Заявление о принуждении, исходящее от принуждаемого, неотличимо от вежливой лжи.

Прибор для опровержения УЖЕ есть и не требует инструментации хоста: payload
аттестации несёт и заявление (`capability_enforcement`), и свидетельство
(`observed_tools`, `declared_capabilities`) — в ОДНОМ событии. Бэкенд,
заявивший `runtime-allowlist` и показавший побег, опровергнут собственной же
записью.

AC3 — не украшение: если наказывать за побег и того, кто ничего не обещал,
честное объявление станет дороже ложного. Инверсия стимула хуже исходного
дефекта, поэтому у неё своя нога.
"""
from __future__ import annotations

CLAIM = "runtime-allowlist"
NO_CLAIM = "not-enforced"
CODE = "E_CAPABILITY_ENFORCEMENT_UNSUBSTANTIATED"


def _bd_l3():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    return bd_l3


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _ev(*, enforcement, declared, observed):
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    return {"type": attest.EVENT_TYPE, "payload": {
        "step_name": "s1",
        "backend": "some-backend",
        "model_requested": "sonnet",
        "observed_model": None,
        "capability_enforcement": enforcement,
        "declared_capabilities": declared,
        "observed_tools": observed,
    }}


# ─── AC1: СЕРДЦЕ — вход, на котором механизм говорит НЕТ самообъявившемуся ─

def test_ac1_declared_enforcement_refuted_by_its_own_escape():
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=CLAIM, declared=["Read"], observed=["bash"])])

    assert report.labels["verdict:R3.5"] == _tok().REQUIREMENT_FAILED, (
        "бэкенд заявил принуждение и в той же записи показал побег — заявление "
        "опровергнуто собственным свидетельством"
    )
    assert any(CODE in v for v in report.violations), (
        f"нарушение обязано называть код; получено {report.violations!r}"
    )


# ─── AC2: положительная нога ──────────────────────────────────────────────

def test_ac2_declared_enforcement_without_escapes_passes():
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=CLAIM, declared=["Read"], observed=["Read"])])

    assert report.labels["verdict:R3.5"] == _tok().REQUIREMENT_PASSED


# ─── AC3: ОТРИЦАТЕЛЬНАЯ — честность не наказывается ──────────────────────

def test_ac3_an_honest_backend_is_not_punished_for_the_escape():
    """`not-enforced` ничего не обещал: побег у него ловит R3.6, не R3.5.

    Иначе честное объявление стало бы дороже ложного — инверсия стимула.
    """
    t = _tok()
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=NO_CLAIM, declared=["Read"], observed=["bash"])])

    assert report.labels["verdict:R3.5"] != t.REQUIREMENT_FAILED, (
        "бэкенд, не заявлявший принуждения, не может нарушить R3.5"
    )
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_FAILED, (
        "побег обязан остаться пойманным — R3.6 никуда не делся"
    )


# ─── AC4: нет свидетельства — не passed ──────────────────────────────────

def test_ac4_claim_without_evidence_is_not_checked():
    """Молча засчитывать неподтверждённое заявление — вернуть самооценку."""
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=CLAIM, declared=None, observed=None)])

    assert report.labels["verdict:R3.5"] == _tok().REQUIREMENT_NOT_CHECKED


# ─── AC5: замер реестра зафиксирован ─────────────────────────────────────

def test_ac5_measured_backend_declarations_are_pinned():
    """Дрейф реестра обязан ронять AC, а не проезжать молча."""
    from bytedigger_engine import llm_subprocess as L  # noqa: PLC0415

    assert L._capability_enforcement("claude-subprocess") == CLAIM
    assert L._capability_enforcement("claude-in-session") == NO_CLAIM


# ─── AC6: ADV-10 не опирается на самоописание ────────────────────────────

def test_ac6_adv10_does_not_read_the_self_declaration():
    """Если бы проба читала заявление, уровень BD-L3 держался бы на нём."""
    import inspect  # noqa: PLC0415

    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    src = inspect.getsource(harness._adv_10)
    assert "capability_enforcement" not in src, (
        "проба ADV-10 обязана опираться на наблюдение, а не на самоописание"
    )
    assert harness.run_adversaries()["ADV-10"] == harness.OUTCOME_DEFENDED


# ─── AC7/AC8: код и поверхность ──────────────────────────────────────────

def test_ac7_error_code_registered():
    from bytedigger_engine.error_codes import ERROR_CODES  # noqa: PLC0415

    assert CODE in ERROR_CODES


def test_ac8_requirements_grew_deliberately():
    bd_l3 = _bd_l3()
    assert "R3.5" in bd_l3.REQUIREMENTS
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
