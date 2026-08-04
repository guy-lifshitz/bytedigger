"""bd#68 — BD-L3: канал наблюдения получает производителя.

Спека: `docs/decisions/2026-08-05-bd68-l3-observation-producers.md`.

Class: канал наблюдения без производителя. Замер: `observed_tools` не пишет
НИКТО (ни `_invoke_subprocess`, ни `_invoke_in_session`, ни один из шести
референс-бэкендов), а `observed_model` пишет только in-session. ⇒ R3.5 и R3.6
на реальном логе вечно `not-checked`, R3.3 наблюдаем лишь для одного бэкенда.

ПРОТИВ СЕБЯ: урок bd#59 («отрицательная нога доказывает, что функция УМЕЕТ
отказать, а не что её СПРОСЯТ») я записал и не применил к L3 — построил поверх
пустого канала bd#28 и bd#63. Гейта против рецидива у L3 не было, поэтому дефект
пережил три лота. AC5 — этот гейт.

Улика при этом уже существует: `_written_paths_from_events` обходит транскрипт
и читает `block["name"]`, отбирая write-инструменты. Имена ВСЕХ инструментов
лежат в тех же блоках, поэтому новая инструментация хоста не нужна.
"""
from __future__ import annotations


def _L():
    from bytedigger_engine import llm_subprocess  # noqa: PLC0415

    return llm_subprocess


def _bd_l3():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    return bd_l3


def _transcript(*tool_names: str):
    """Транскрипт stream-json в форме, которую обходит производитель."""
    return [{
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": n, "input": {"file_path": f"/x/{n}.py"}}
            for n in tool_names
        ]},
    }]


# ─── AC1/AC2: производитель существует и НЕ всегда пуст ──────────────────

def test_ac1_extractor_returns_sorted_deduped_tool_names():
    out = _L()._observed_tools_from_events(_transcript("Read", "Bash", "Read"))
    assert out == ["Bash", "Read"], f"получено {out!r}"


def test_ac2_empty_and_nonempty_are_both_reachable():
    """ОТРИЦАТЕЛЬНАЯ НОГА: производитель, всегда пишущий одно и то же, инертен
    так же, как его отсутствие."""
    L = _L()
    assert L._observed_tools_from_events([]) == []
    assert L._observed_tools_from_events(_transcript("Bash")) == ["Bash"]


# ─── AC3: поле доезжает до data ──────────────────────────────────────────

def test_ac3_observed_tools_lands_in_step_result_data(monkeypatch):
    """Проверяется СБОРКА data продовой функции, а не только экстрактор."""
    import inspect  # noqa: PLC0415

    src = inspect.getsource(_L()._invoke_subprocess)
    assert 'data["observed_tools"]' in src, (
        "_invoke_subprocess обязан класть observed_tools в data — иначе "
        "экстрактор существует, а канал по-прежнему пуст"
    )
    assert "_observed_tools_from_events" in src


# ─── AC4: сквозная связь улика -> вердикт ────────────────────────────────

def test_ac4_escape_seen_only_in_the_transcript_reaches_the_verdict():
    """Цепочка замкнута: побег из транскрипта становится нарушением R3.6."""
    from bytedigger_engine.conformance import attest, tokens  # noqa: PLC0415

    observed = _L()._observed_tools_from_events(_transcript("Bash"))
    report = _bd_l3().check_bd_l3([{
        "type": attest.EVENT_TYPE,
        "payload": {"step_name": "s1", "model_requested": "sonnet",
                    "observed_model": None, "declared_capabilities": ["Read"],
                    "observed_tools": observed},
    }])
    assert report.labels["verdict:R3.6"] == tokens.REQUIREMENT_FAILED


# ─── AC5: ГЕЙТ ПРОТИВ РЕЦИДИВА, обе стороны ──────────────────────────────

def _producers(field: str) -> int:
    """Сколько прод-функций кладут `field` в StepResult.data."""
    import inspect  # noqa: PLC0415

    L = _L()
    n = 0
    for name in ("_invoke_subprocess", "_invoke_in_session"):
        fn = getattr(L, name, None)
        if fn is not None and f'"{field}"' in inspect.getsource(fn):
            n += 1
    return n


_FIELD_FOR = {"R3.3": "observed_model", "R3.5": "observed_tools",
              "R3.6": "observed_tools"}


def test_ac5_observable_requirements_have_producers_and_registry_is_current():
    bd_l3 = _bd_l3()

    observable = set(bd_l3.REQUIREMENTS) - set(bd_l3.AWAITING_PRODUCER)
    missing = sorted(r for r in observable if _producers(_FIELD_FOR[r]) == 0)
    assert not missing, (
        f"требование объявлено наблюдаемым, а поле никто не пишет: {missing!r}"
    )

    stale = sorted(r for r in bd_l3.AWAITING_PRODUCER
                   if _producers(_FIELD_FOR.get(r, "")) >= 2)
    assert not stale, (
        f"производитель появился у всех путей, а требование числится ожидающим: "
        f"{stale!r} — реестр обязан пустеть"
    )


def test_ac6_r33_partial_observability_is_declared():
    """D4: `observed_model` пишет только in-session — это объявлено, не скрыто."""
    bd_l3 = _bd_l3()
    assert "R3.3" in bd_l3.AWAITING_PRODUCER, (
        "R3.3 наблюдаем только для одного бэкенда — пока это так, он обязан "
        "числиться ожидающим, а не выдаваться за полностью наблюдаемый"
    )
    assert _producers("observed_model") == 1


# ─── AC7/AC8: соседний контракт и поверхность ────────────────────────────

def test_ac7_write_path_extractor_contract_unchanged():
    """`_written_paths_from_events` обязан по-прежнему отдавать только пути
    write-инструментов — смешение двух смыслов в одном обходе не делается."""
    L = _L()
    events = _transcript("Read", "Write")
    assert L._written_paths_from_events(events) == ["/x/Write.py"]


def test_ac8_public_surface_equals_dunder_all():
    bd_l3 = _bd_l3()
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
