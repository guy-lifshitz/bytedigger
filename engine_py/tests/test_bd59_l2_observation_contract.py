"""bd#59 — контракт наблюдений BD-L2: события, которые реально эмитятся.

Спека: `docs/decisions/2026-08-04-bd59-l2-observation-contract.md`.

Class: наблюдение без производителя. Замер экспозиции, обязательный по issue
ДО RED, показал: у пяти из шести требований BD-L2 нет не только последствия, но
и НАБЛЮДЕНИЯ — событий, которые `bd_l2` читает, никто не эмитит, а два
оставшихся эмитятся с ДРУГИМИ ключами. На реальном логе `bd_l2` не выносит ни
одного вердикта.

ЭТО ПРОТИВ МОЕГО ЖЕ bd#9. `_EVENT_FOR` был написан из имён, придуманных в
фикстурах того лота; все 16 ACs прошли, потому что каждая фикстура была
синтетической — чекер судил ровно ту форму, которую я ему и подавал.
Отрицательные ноги bd#9 верны (функция умеет сказать НЕТ), но §1l-якоря к
реальному прод-излучению там не было. Он добавляется здесь, и AC1 — именно тот
гейт, отсутствие которого пропустило дефект.
"""
from __future__ import annotations

from pathlib import Path

_PROD_ROOT = Path(__file__).resolve().parents[1] / "bytedigger_engine"


def _bd_l2():
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    return bd_l2


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _emitters(event_type: str) -> list[str]:
    """Прод-файлы, эмитящие `event_type` (вне `conformance/` — там потребители)."""
    needle = f'"{event_type}"'
    found = []
    for path in _PROD_ROOT.rglob("*.py"):
        if "conformance" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if f"_emit_safe({needle}" in text or f"emit({needle}" in text:
            found.append(path.name)
    return found


# ─── AC1: ГЕЙТ ПРОТИВ РЕЦИДИВА ────────────────────────────────────────────

def test_ac1_every_observed_requirement_has_a_production_emitter():
    """Требование, объявленное наблюдаемым, обязано иметь производителя.

    Без этого AC чекер может годами судить форму, которой в проде нет, и
    оставаться зелёным — ровно это и случилось в bd#9. Требование без
    производителя не запрещено: оно обязано быть ОБЪЯВЛЕНО в реестре
    ожидающих, а не молчать.
    """
    bd_l2 = _bd_l2()

    observed = set(bd_l2._EVENT_FOR) - set(bd_l2.AWAITING_PRODUCER)
    missing = {
        req: bd_l2._EVENT_FOR[req]
        for req in sorted(observed)
        if not _emitters(bd_l2._EVENT_FOR[req])
    }
    assert not missing, (
        f"требования объявлены наблюдаемыми, но производителя события нет: "
        f"{missing!r}. Либо появится эмиттер, либо требование обязано быть в "
        f"AWAITING_PRODUCER."
    )

    stale = {
        req for req in bd_l2.AWAITING_PRODUCER
        if _emitters(bd_l2._EVENT_FOR.get(req, ""))
    }
    assert not stale, (
        f"производитель появился, но требование всё ещё числится ожидающим: "
        f"{sorted(stale)!r} — реестр обязан пустеть, иначе он превращается в "
        f"вечное оправдание"
    )


# ─── AC2/AC3: R2.1 на РЕАЛЬНОМ payload (без `counted_as`) ─────────────────

def _real_red_outcome(*, n_passed, n_failed, exit_code):
    """Форма, которую эмитит `phase_5_implement.py:2635` — ровно эти ключи."""
    return {"type": "red_test_outcome", "payload": {
        "group": "py", "exit_code": exit_code, "n_passed": n_passed,
        "n_failed": n_failed, "phase": 5,
    }}


def test_ac2_zero_collected_on_the_real_payload_is_not_a_rejection():
    """Различитель обязан работать БЕЗ придуманного ключа `counted_as`."""
    report = _bd_l2().check_bd_l2([_real_red_outcome(
        n_passed=0, n_failed=0, exit_code=5)])

    assert report.labels["verdict:R2.1"] == _tok().REQUIREMENT_FAILED, (
        "прогон, собравший ноль тестов, — несрабатывание, а не отказ; "
        "различитель обязан читать реально эмитируемые ключи"
    )


def test_ac3_real_rejection_passes():
    """ОТРИЦАТЕЛЬНАЯ НОГА: «починка», роняющая всё подряд, зелена по AC2."""
    report = _bd_l2().check_bd_l2([_real_red_outcome(
        n_passed=0, n_failed=3, exit_code=1)])

    assert report.labels["verdict:R2.1"] == _tok().REQUIREMENT_PASSED


# ─── AC4/AC5: R2.6 на РЕАЛЬНОМ payload ────────────────────────────────────

def _real_delta_verdict(*, verdict, n_new_fails, enforced=True):
    """Форма `_baseline_delta.py:92` — ровно эти ключи."""
    return {"type": "baseline_delta_gate_verdict", "payload": {
        "suite": "py", "verdict": verdict, "new_fails": [],
        "n_new_fails": n_new_fails, "ledgered": [],
        "baseline_source": "declared", "enforced": enforced, "phase": 5,
    }}


def test_ac4_missing_baseline_source_is_not_a_measured_delta():
    report = _bd_l2().check_bd_l2([{
        "type": "baseline_delta_gate_verdict", "payload": {
            "suite": "py", "verdict": "PASS", "new_fails": [],
            "n_new_fails": 0, "ledgered": [], "baseline_source": None,
            "enforced": True, "phase": 5,
        }}])

    assert report.labels["verdict:R2.6"] != _tok().REQUIREMENT_PASSED, (
        "без объявленного базлайна дельта не измерена против него"
    )


def test_ac5_real_measured_delta_passes():
    """ОТРИЦАТЕЛЬНАЯ НОГА для R2.6."""
    report = _bd_l2().check_bd_l2([_real_delta_verdict(
        verdict="PASS", n_new_fails=0)])

    assert report.labels["verdict:R2.6"] == _tok().REQUIREMENT_PASSED


# ─── AC6: объявленный пробел, обе стороны ─────────────────────────────────

def test_ac6_awaiting_producer_is_declared_and_yields_not_checked():
    """Пробел обязан быть ОБЪЯВЛЕН, а не выражен молчанием."""
    bd_l2, t = _bd_l2(), _tok()

    assert bd_l2.AWAITING_PRODUCER, (
        "на этой базе четыре требования не имеют производителя — реестр не "
        "может быть пуст"
    )
    for req in bd_l2.AWAITING_PRODUCER:
        assert req in bd_l2.REQUIREMENTS
        report = bd_l2.check_bd_l2([])
        assert report.labels[f"verdict:{req}"] == t.REQUIREMENT_NOT_CHECKED


# ─── AC7: поверхность ─────────────────────────────────────────────────────

def test_ac7_public_surface_equals_dunder_all():
    bd_l2 = _bd_l2()
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__), (
        f"поверхность разошлась с __all__: лишние "
        f"{sorted(public - set(bd_l2.__all__))!r}"
    )
