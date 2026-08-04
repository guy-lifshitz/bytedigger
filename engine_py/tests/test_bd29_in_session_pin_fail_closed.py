"""bd#29 — in-session model-pin fail-closed flip (supersede 220E5F63).

Спека: `docs/decisions/2026-08-04-bd29-in-session-model-pin-fail-closed.md`.

Class: декларация без измерения (B-3 гейта bd#10). Чокпоинт
`_pin_mismatch_refusal` (`llm_subprocess.py:1088`, вызов из
`invoke_llm_subprocess` на `:1301`) УЖЕ стоит на пути каждого бэкенда. Он читает
`StepResult.data["observed_model"]`. `_invoke_in_session` этот ключ не пишет
(`:939-951`), поэтому на in-session результате чокпоинт получает `observed=None`
и выходит `not-checked`, а параллельная warn-only ветка (`:929-932`) эмитит
предупреждение и пропускает шаг. Щит зелёный, пока дыра открыта.

⇒ Флип обязан быть СЛЕДСТВИЕМ чокпоинта, а не второй реализацией проверки:
достаточно заполнить канал и снять超seded warn-only вызов.

ПОЧЕМУ ЭТИ НОГИ ГОНЯЮТ `invoke_llm_subprocess`, А НЕ `_invoke_in_session`.
Живой AC10 (`test_2FDA949D_model_pin_warn.py`) зовёт `_invoke_in_session`
НАПРЯМУЮ. Скопировать эту форму значило бы написать AC, который не может
позеленеть никогда: чокпоинт живёт УРОВНЕМ ВЫШЕ, внутри `invoke_llm_subprocess`.
Поэтому ноги входят через публичную точку с `backend="claude-in-session"` — это
и есть in-session путь end-to-end, и он единственный доходит до адъюдикации.

Экспозиция замерена ДО RED (спека §1): 22 прод-сайта, из них 15 не-hard-gate
(это и есть warn-only экспозиция), 7 hard-gate, и НОЛЬ сайтов без пина модели.
Дефолтный бэкенд — `agent-sdk`; in-session включается только явным opt-in.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess

from test_2FDA949D_model_pin_warn import _FakeEventLog

NONCE = "bd29aabbccdd112233445566"


def _drive_in_session(
    tmp_path: Path,
    monkeypatch,
    *,
    dispatched_model: "str | None",
    pinned_model: str = "sonnet",
    hard_gate: bool = False,
):
    """Прогнать in-session шаг end-to-end через ПУБЛИЧНУЮ точку входа.

    Возвращает (StepResult, _FakeEventLog). Демон-нить пишет `.res.json` ПОСЛЕ
    появления `.req.json` (§1i / ABD52613) — иначе stale-sweep снесёт заранее
    положенный артефакт.
    """
    from bytedigger_engine import llm_subprocess as _llm_mod

    request_dir = tmp_path / "requests"
    request_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(request_dir))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD29", step_name="invoke_step", phase="phase_5",
    )

    class _FakeUUID:
        hex = NONCE

    monkeypatch.setattr(_llm_mod.uuid, "uuid4", staticmethod(lambda: _FakeUUID()))

    nonce_dir = request_dir / "RUN-BD29" / "phase_5"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    request_path = nonce_dir / f"{NONCE}.req.json"
    result_path = nonce_dir / f"{NONCE}.res.json"

    payload = {
        "subtype": "success",
        "raw_response": "worker output",
        "worker_written_paths": [],
        "manifest_source": "orchestrator_observed",
        "request_nonce": NONCE,
        "__hal_integrity": "end",
    }
    if dispatched_model is not None:
        payload["dispatched_model"] = dispatched_model

    def _runner() -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if request_path.exists():
                tmp_result = str(result_path) + ".tmp"
                with open(tmp_result, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_result, str(result_path))
                return
            time.sleep(0.02)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    try:
        result = invoke_llm_subprocess(
            prompt="do the thing",
            model=pinned_model,
            timeout_sec=5,
            step_name="invoke_step",
            hard_gate=hard_gate,
            backend="claude-in-session",
        )
    finally:
        t.join(timeout=12.0)
    return result, log


# ─── AC1: предмет — дрейф на не-hard-gate шаге обязан ронять шаг ───────────

def test_ac1_non_hardgate_drift_fails_closed(tmp_path, monkeypatch):
    """CL:99: объявленный пин при несовпадении MUST fail.

    Сегодня красный: путь эмитит `model_pin_mismatch` и возвращает `ok`
    (220E5F63 / GH#222).
    """
    result, _log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="haiku")

    assert result.status == "error", (
        f"дрейф модели на не-hard-gate in-session шаге обязан ронять шаг "
        f"(CL:99), получено status={result.status!r}"
    )
    assert result.error_code == "E_MODEL_PIN_MISMATCH", (
        f"ожидался E_MODEL_PIN_MISMATCH, получено {result.error_code!r}"
    )
    assert result.recoverable is False, (
        "несовпадение пина неисправимо ретраем — recoverable обязан быть False"
    )


# ─── AC2: выжившие половины AC10, переякоренные в НОВОМ оракуле ────────────

def test_ac2_mismatch_event_still_emitted_with_its_keys(tmp_path, monkeypatch):
    """Требование issue п.4: правка живого регрессионного теста на
    противоположный исход означала бы, что изменение оценивается артефактом,
    который правит сам лот. Выжившие assert'ы AC10 переякорены здесь.
    """
    _result, log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="haiku")

    events = log.by_type("model_pin_mismatch")
    assert len(events) >= 1, (
        f"событие model_pin_mismatch обязано по-прежнему эмититься; "
        f"захвачено: {[et for (et, _, _) in log.events]!r}"
    )
    ev = events[0]
    for key in ("observed_model", "pinned_model", "step_name"):
        assert key in ev, (
            f"payload model_pin_mismatch потерял ключ {key!r}; ключи: {list(ev.keys())!r}"
        )
    assert ev["observed_model"] == "haiku"
    assert ev["pinned_model"] == "sonnet"


# ─── AC3: ОТРИЦАТЕЛЬНАЯ НОГА — канал, а не вторая проверка ────────────────

def test_ac3_in_session_result_exposes_observed_model_channel(tmp_path, monkeypatch):
    """Без этой ноги «флип» можно было бы сделать второй проверкой ВНУТРИ
    `_invoke_in_session`, оставив канал пустым — то есть воспроизвести дефект
    B-3 под видом починки. Здесь пинуется сам канал, на прогоне БЕЗ дрейфа.
    """
    result, _log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="sonnet")

    assert isinstance(result.data, dict), f"ожидался dict data, got {type(result.data)}"
    assert result.data.get("observed_model") == "sonnet", (
        f"in-session результат обязан нести observed_model из dispatched_model — "
        f"это канал, который читает чокпоинт; получено "
        f"{result.data.get('observed_model')!r}"
    )


# ─── AC4: ОТРИЦАТЕЛЬНАЯ НОГА — fail-closed, который не падает, не существует ─

def test_ac4_no_drift_still_passes_and_emits_nothing(tmp_path, monkeypatch):
    """Контроль: «починка», роняющая всё подряд, была бы зелёной по AC1."""
    result, log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="sonnet")

    assert result.status == "ok", (
        f"совпадающие семейства — шаг обязан пройти, получено "
        f"status={result.status!r} error_code={result.error_code!r}"
    )
    assert log.by_type("model_pin_mismatch") == [], (
        "без дрейфа событие model_pin_mismatch эмититься не должно"
    )


# ─── AC5: граница bd#10 не сдвинута (D4) ──────────────────────────────────

def test_ac5_absent_dispatched_model_stays_not_checked(tmp_path, monkeypatch):
    """bd#10 решил явно: «an adapter that reported nothing ... is not-checked».

    bd#29 этого решения НЕ отменяет. Нога сторожит, чтобы флип не расползся в
    fail-closed по отсутствию отчёта — это был бы другой предмет с другим доводом.
    """
    result, log = _drive_in_session(tmp_path, monkeypatch, dispatched_model=None)

    assert result.status == "ok", (
        f"отсутствующий dispatched_model — not-checked, не отказ; получено "
        f"{result.status!r} / {result.error_code!r}"
    )
    assert log.by_type("model_pin_mismatch") == []


# ─── AC7: метка требования объявляет новое состояние (D5) ─────────────────

def test_ac7_requirement_label_declares_chokepoint_enforced():
    """Супersession 220E5F63 обязана быть ВИДНА аттестации, а не только в коде."""
    from bytedigger_engine.conformance.attest import REQUIREMENT_LABELS

    assert REQUIREMENT_LABELS["R3.3"] == "chokepoint-enforced", (
        f"метка R3.3 обязана объявлять новое состояние, получено "
        f"{REQUIREMENT_LABELS['R3.3']!r}"
    )


# ─── AC6: hard-gate путь не задет ─────────────────────────────────────────

def test_ac6_hard_gate_drift_still_handled_by_its_own_guard(tmp_path, monkeypatch):
    """hard_gate=True уже fail-closed через `_assert_in_session_model_or_downgrade`
    (02FF48F4). Флип не имеет права подменить его собственным кодом отказа —
    иначе два стража на одном пути начнут спорить о коде ошибки.
    """
    result, _log = _drive_in_session(
        tmp_path, monkeypatch, dispatched_model="haiku", hard_gate=True,
    )

    assert result.status == "error", (
        f"hard-gate дрейф обязан ронять шаг, получено {result.status!r}"
    )
    # ЗАМЕР, а не ожидание: сегодня сюда приходит E_MODEL_PIN_MISMATCH, хотя
    # отказывает `_assert_in_session_model_or_downgrade` СВОИМ кодом
    # E_HARD_GATE_MODEL_DOWNGRADE. Его отказ несёт data["observed_model"], и
    # чокпоинт на :1301 читает этот ключ, видит дрейф семейств и ПЕРЕЗАПИСЫВАЕТ
    # результат своим кодом. То есть код hard-gate-стража до вызывающего не
    # доходит, когда семейства вдобавок различаются.
    #
    # Это ПРЕДСУЩЕСТВУЮЩИЙ дефект (два стража на одном пути, поздний затирает
    # раннего), он НЕ создан bd#29 и здесь НЕ чинится — это был бы второй
    # предмет. Нога пинует сегодняшнее наблюдаемое поведение, чтобы флип его
    # не сдвинул незаметно; когда дефект будут чинить, эта строка обязана
    # упасть и потребовать решения.
    assert result.error_code == "E_MODEL_PIN_MISMATCH", (
        f"hard-gate поведение сдвинулось: ожидалось сегодняшнее "
        f"E_MODEL_PIN_MISMATCH (чокпоинт затирает E_HARD_GATE_MODEL_DOWNGRADE), "
        f"получено {result.error_code!r}"
    )
