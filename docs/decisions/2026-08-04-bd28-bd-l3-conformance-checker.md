# bd#28 — BD-L3 conformance checker + attestation report

**Class:** оракул, который не умеет отказать (B-1 гейта bd#10 раунда 1, форма
`[G22:18]`, инверсия P2 из CL §1). Чекер, у которого ветка `failed` недостижима,
это не слабый контроль, а **перевёрнутый**: он превращает отсутствие свидетельств в
утверждение о соответствии.

**Chokepoint:** новый модуль `conformance/bd_l3.py` — единственное место, где
вердикты по R3.3/R3.6 **вычисляются**. Источник фактов —
события `model_invocation_attested` (`conformance.attest.EVENT_TYPE`), которые
пишет чокпоинт `_dispatch_backend` (bd#10). Метки требований потребляются из
`conformance.attest.REQUIREMENT_LABELS`, не переобъявляются.

---

## §1b. Живая база — ДВА КОРПУСА, замерены ДО заморозки

Дисциплина issue: дельта снимается **по каждому корпусу отдельно**. На `8548c39`,
на этом хосте:

| корпус | команда | результат |
|---|---|---|
| **pytest** (`ci.yml`) | `python3 -m pytest tests/ -q -p no:cacheprovider --timeout=120` из `engine_py` | **5336 passed / 47 skipped / 1 xfailed / 0 failed**, 385 с |
| **clean-room suite** (`clean-room.yml`) | `bash scripts/clean-room/run.sh suite 3.11` (docker, `git archive`) | **5250 passed / 68 skipped / 1 xfailed / 0 failed**, 216 с, вердикт `PASS` |

**Расхождение корпусов выросло:** issue называет 11 тестов (`test_gh792_native_sentinel_emit.py`
за `importorskip dbos`) на базе `dc6f0d0`; сегодня разница **86 passed / 21 skipped**.
На этом хосте `dbos` установлен, в clean-room его нет — но одним этим файлом разница
больше не объясняется. **Не выводить причину рассуждением**: обе стороны дельты
снимаются в СВОЁМ корпусе, и число из чужого корпуса в отчёт не подставляется.

## §2. Предмет жив

`engine_py/bytedigger_engine/conformance/bd_l3.py` **не существует** на `8548c39`
(проверено `ls`). Лот строит его с нуля; `check_bd_l3` в bd#10 был бы отчётом без
потребителя, поэтому и отщеплён.

## §3. Интерфейс — несёт СПЕКА, а не RED (§1.5 / bd#7 §3.0)

Модуль `bytedigger_engine/conformance/bd_l3.py`.

```
__all__ = ["REQUIREMENTS", "check_bd_l3", "validate_report"]

REQUIREMENTS: tuple[str, ...] = ("R3.3", "R3.6")

def check_bd_l3(events: "Iterable[Mapping[str, Any]]") -> L0Report: ...
def validate_report(report: L0Report) -> tuple[str, ...]: ...
```

`L0Report` импортируется из `conformance.report` и **НЕ расширяется** — его четыре
поля контракт bd#22 (`CONTRACTS_SPEC.md` §2 AC-C2).

`labels` отчёта несёт `verdict:R3.3` и `verdict:R3.6` со значениями из
`conformance.tokens` (`REQUIREMENT_PASSED` / `REQUIREMENT_FAILED` /
`REQUIREMENT_NOT_CHECKED`), плюс метки требований из
`attest.REQUIREMENT_LABELS`.

## §4. Механизм — закрепляется ЗДЕСЬ, потому что v3 bd#10 его не закрепила

**Агрегация, три ветки, по каждому требованию отдельно (`[bd10:13]`):**
1. `failed` — если **хоть одна** инвокация записала нарушение;
2. иначе `not-checked` — если **ни одна** не несла ненулевого наблюдения;
3. иначе `passed`.

**Что есть нарушение — пересчёт из payload, НЕ записанный флаг вердикта.**
Записанный флаг дал бы эмиттеру оценивать самого себя (subtype (3) из hal#1373 на
уровне всей системы).
- **R3.3 нарушено**, когда `observed_model` не `null` **и** его семейство отличается
  от семейства `model_requested`. Семейство — `lib.llm_provider` (`model_family`).
- **R3.6 нарушено**, когда `observed_tools` не `null`, `declared_capabilities` не
  `null`, и `attest.capability_escapes(observed_tools, declared_capabilities)`
  возвращает непустой кортеж.

**Что есть «ненулевое наблюдение»** (различитель ветки 2 от ветки 3):
- для R3.3 — `observed_model` не `null`;
- для R3.6 — `observed_tools` не `null` **и** `declared_capabilities` не `null`.

**Фильтр входа.** `check_bd_l3` читает **только** события с
`type == attest.EVENT_TYPE` и игнорирует остальные. Закрепляется отдельно, потому
что реальный лог гетерогенен, а однородный корпус фикстур (EDGE-8 гейта) пропустил
бы GREEN без фильтра.

**`passed` отчёта** истинен, только когда **каждое** требование из `REQUIREMENTS`
имеет вердикт `passed`. `not-checked` — **не** `passed` (EDGE-1).

**ADV-9** записывается как `ADVERSARY_NOT_EXECUTED`; судья — `validate_report`
(CL:221-224): она возвращает кортеж строк-претензий, пустой кортеж = отчёт
самосогласован.

## §5. Scope

**Новые файлы**
- `engine_py/bytedigger_engine/conformance/bd_l3.py`
- `engine_py/tests/test_bd28_bd_l3_checker.py` — RED

**Правится** — ничего. Ни один существующий модуль не меняется.

**§1v — НЕ в области**
- `L0Report` (контракт bd#22).
- `attest.py`, `tokens.py`, `report.py` — потребляются, не правятся.
- Грант уровня BD-L3: требует BD-L0/L1/L2 (bd#8, bd#9, bd#27 открыты). Этот лот
  строит чекер и отчёт, **не выдаёт уровень**.
- `_dispatch_backend` и payload аттестации — bd#10.

## §6. §1a Sibling-audit

Потребители того, что лот трогает (импортирует, не правит):

Существование проверено на `8548c39`, а не предположено:

| файл | связь |
|---|---|
| `test_bd10_l3_authorship.py` | `REQUIREMENT_LABELS`, payload аттестации |
| `test_bd22_contracts.py` | `L0Report`, контракт AC-C2 |
| `test_bd24_quant_lint.py` | форма поверхности экспортов, дисциплина отложенных импортов |
| `test_contracts.py` | контракты |

**Итого 163 теста, все зелены на базе.** `test_bd29_in_session_pin_fail_closed.py` в
списке НЕТ: он приезжает моим же PR bd#54, ещё не смерженным.

**★★★Против себя:** первый прогон этой поверхности перечислил несуществующий файл
bd#29 и вернул **`no tests ran`** — pytest на отсутствующем пути обнуляет ВЕСЬ вызов,
а не пропускает один аргумент. Правдоподобный ноль снова оказался сломанным прибором,
а не находкой. Форма: перед прогоном списка файлов проверять их существование, и
любой `no tests ran` читать как отказ прибора.

Прогнать прицельно + **оба** полных корпуса (§1b).

## §7. Acceptance criteria

Импорты `conformance.*` — **внутри тел тестов**, никогда на уровне модуля
(дисциплина bd#24: сбор остаётся чистым, RED падает на assert/ImportError в теле).

- **AC1 (ветка `failed`, R3.3).** Лог с инвокацией, где `observed_model="haiku"` при
  `model_requested="sonnet"` ⇒ `labels["verdict:R3.3"] == REQUIREMENT_FAILED`,
  `report.passed is False`, `violations` непуст.
- **AC2 (ветка `failed`, R3.6).** Инвокация с `observed_tools=["bash"]` и
  `declared_capabilities=["Read"]` ⇒ `verdict:R3.6 == REQUIREMENT_FAILED`,
  `passed is False`, `violations` непуст.
- **AC3 (ветка `passed`).** Инвокация с совпадающим семейством и без побегов ⇒ оба
  вердикта `passed`, `report.passed is True`, `violations == ()`.
- **AC4 (ветка `not-checked`).** Инвокация с `observed_model=None` и
  `observed_tools=None` ⇒ оба вердикта `not-checked`.
- **AC5 (EDGE-1, ОТРИЦАТЕЛЬНАЯ НОГА).** `check_bd_l3([])` ⇒ оба вердикта
  `not-checked` **и `report.passed is False`**. Отчёт по нулю свидетельств,
  возвращающий `passed=True`, — чистейшая форма B-1.
- **AC6 (EDGE-8, ОТРИЦАТЕЛЬНАЯ НОГА — фильтр входа).** Лог, где НАРУШАЮЩАЯ инвокация
  лежит в событии ЧУЖОГО типа (`"runner_result_consumed"` с теми же ключами), а
  событие `model_invocation_attested` чистое ⇒ вердикты `passed`, не `failed`.
  Ловит GREEN без фильтра, который на однородном корпусе неотличим.
- **AC7 (ОТРИЦАТЕЛЬНАЯ НОГА — пересчёт, не флаг).** Инвокация НАРУШАЮЩАЯ по payload,
  но несущая `"verdict": "passed"` ⇒ вердикт всё равно `failed`. Ловит GREEN,
  доверяющий записанному флагу, то есть дающий эмиттеру оценивать себя.
- **AC8 (B-2, поверхность экспортов).** Публичная поверхность модуля равна
  `__all__`. Утверждается ПРОТИВ `__all__`, а не вычислением по `__module__`:
  замерено, что инстансы встроенных типов не несут `__module__` (`"x".__module__`
  кидает `AttributeError`), поэтому вычисляющая форма считает импортированные
  константы экспортами — а `bd_l3.py` обязан импортировать `REQUIREMENT_FAILED`,
  то есть **починка B-1 детонирует B-2**.
- **AC9 (`validate_report` как судья).** Самосогласованный отчёт ⇒ `()`.
  Отчёт с `passed=True` при вердикте `failed` ⇒ непустой кортеж претензий.
  Обе стороны, иначе судья, всегда возвращающий `()`, зелен.
- **AC10 (ADV-9).** `labels` несёт ADV-9 со значением `ADVERSARY_NOT_EXECUTED`.
- **AC11 (`L0Report` не расширён).** Поля отчёта ровно четыре — контракт bd#22.

**В обе стороны на одном наборе фикстур** (требование issue): AC1/AC2 против AC3 —
один и тот же построитель лога, разные наблюдения.

## §8. Чего этот PR НЕ утверждает

- Не выдаёт грант BD-L3: BD-L0/L1/L2 не существуют (bd#8, bd#9, bd#27 открыты).
- Не меняет ни одного существующего модуля.
- Не утверждает полноту R3.1/R3.2/R3.5 — `REQUIREMENTS` этого лота ровно
  `("R3.3", "R3.6")`, как задаёт issue.
