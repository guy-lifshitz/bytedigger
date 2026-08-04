# bd#29 — in-session model-pin fail-closed flip (supersede 220E5F63)

**Class:** декларация без измерения (B-3 гейта bd#10). Путь не доходил до чокпоинта
не потому, что чокпоинт его не покрывал, а потому что **не заполнял канал, который
чокпоинт читает**. Щит был зелёным всё время, пока дыра оставалась открытой.
Сопутствующая коллизия: соглашение 220E5F63/GH#222 («warn-only») и CL:99
(«объявленный пин при несовпадении MUST fail») одновременно истинными быть не могут.

**Chokepoint:** `llm_subprocess._pin_mismatch_refusal` (`:1088`, вызывается из
`invoke_llm_subprocess` на `:1301`). Он **уже стоит на пути КАЖДОГО бэкенда** — после
диспатча, до возврата. Канал, который он читает — `StepResult.data["observed_model"]`.

**Ключевой замер:** `_invoke_in_session` формирует `data` (`:939-951`) из
`raw_response`, `response_bytes`, `model`, `tokens_out`, `tokens_in`, `cost_usd`,
`worker_written_paths`, `manifest_source` — **`observed_model` там нет**. Поэтому
`_pin_mismatch_refusal` на in-session результате получает `observed = None` и выходит
`not-checked`, а параллельная warn-only ветка (`:929-932`,
`_detect_nonhardgate_model_drift`) эмитит предупреждение и пропускает шаг дальше.

⇒ **Флип не требует второй реализации проверки.** Он получается СЛЕДСТВИЕМ чокпоинта,
как и требует issue п.2: достаточно заполнить канал и снять超seded warn-only вызов.

**§1b живая база**, снята ДО заморозки, `ada6585`, из `engine_py`, после сноса
`build`/`__pycache__`: **`4497 passed / 39 skipped / 0 failed`**, 297 с.

---

## §1. Замер экспозиции (входное требование issue, п.1) — числом, на этом хосте

AST-скан всех прод-сайтов `invoke_llm_subprocess` в `bytedigger_engine/`:

| | сайтов |
|---|---|
| всего прод-сайтов | **22** |
| `hard_gate=True` (уже fail-closed через `_assert_in_session_model_or_downgrade`) | **7** |
| **не-hard-gate — это и есть warn-only экспозиция** | **15** |
| сайтов, не передающих пин модели | **0** |

**Против себя — первый счёт был неверен, и я поймал его по невозможному сигналу.**
Первый скан дал «2 сайта без пина» (`phase_45_spec.py:3854,3905`), хотя `model: str` —
**обязательный** параметр без значения по умолчанию. Невозможность и была уликой: оба
сайта зовут `invoke_llm_subprocess(**invoke_kwargs)`, а словарь строится выше и несёт
`model=rev_model` **и** `hard_gate=True`. Значит статический скан ошибся ДВАЖДЫ — и по
пину, и по `hard_gate`; эти два сайта не «не-hard-gate без пина», а обычные hard-gate.
Числа выше уже исправлены. **Форма: splat-вызов статическому скану непрозрачен;
классификацию по kwargs проверять на невозможные комбинации.**

**Рантайм-экспозиция.** `_DEFAULT_BACKEND = "agent-sdk"`; in-session включается только
явным `backend=` или `HAL_RUNNER_BACKEND=claude-in-session` (`_resolve_backend`,
kwarg > env > default). **Ни один прод-сайт in-session не выбирает** — единственное
текстовое совпадение (`phase_6_review.py:973`) лишь ЧИТАЕТ разрешённый бэкенд, чтобы
решить про `straggler_abort`.

⇒ **Что станет при fail-closed:** 15 не-hard-gate сайтов получают возможность жёстко
упасть, но только при совпадении трёх условий — прогон идёт in-session (явный opt-in),
сервисёр вернул `dispatched_model`, и его семейство отличается от пина. Ни один
существующий вызывающий не ломается из-за отсутствия пина, потому что таких **ноль**.
Это делает флип дешёвым по риску — и именно этот замер отсутствовал в bd#10.

## §2. Решения

**D1 — заполнить канал, не писать вторую проверку.** `_invoke_in_session` кладёт в
`data` ключ `observed_model` со значением `result_obj.get("dispatched_model")`.
Адъюдикация остаётся ровно одна — чокпоинтная.

**D2 — снять超seded warn-only вызов** `_detect_nonhardgate_model_drift` из
`_invoke_in_session` (`:929-932`). **Саму функцию НЕ удалять** — её юнит-тесты
AC5–AC9 остаются в силе (требование issue п.5), и она сохраняет ценность как
чистый предикат.

**D3 — событие сохраняется, требование issue п.4.** Чокпоинт эмитит
`model_pin_mismatch` с `observed_model`, `pinned_model`, `step_name` (+ `phase`,
`pinned_family`, `observed_family`, `severity="error"`, `chokepoint=True`). Выжившие
половины AC10 переякорены в **новом** оракуле (AC2 ниже), а не проверяются только тем
файлом, который лот сам и правит.

**D4 — «нераспознанное семейство» НЕ трогаем.** bd#10 решил явно: «an adapter that
reported nothing, or reported an unrecognised token, is `not-checked` — an unrecognised
token is not evidence of drift». Делать это fail-closed означало бы **отменить решение
bd#10**, а не выполнить bd#29. Цена названа: сервисёр, вернувший неизвестный токен
модели, гейт не поднимет. Если это надо менять — это отдельный предмет со своим
доводом.

**D5 — 220E5F63 супersedeн.** Дата 2026-08-04, причина: конфликт с CL:99, который
требует падения при несовпадении объявленного пина. Warn-only был законен, пока
уровень не объявил обратного; после CL:99 два правила одновременно истинными быть не
могут. Метка `attest.REQUIREMENT_LABELS["R3.3"]` меняется с `"in-session-warn-only"`
на `"chokepoint-enforced"`.

## §3. §5 Scope

**Правится**
- `engine_py/bytedigger_engine/llm_subprocess.py` — D1 (+1 ключ), D2 (снятие вызова).
- `engine_py/bytedigger_engine/conformance/attest.py` — метка R3.3 (D5).
- `engine_py/tests/test_2FDA949D_model_pin_warn.py` — **только** ожидаемый статус и код
  ошибки в AC10 (требование issue п.5). Юнит-assert'ы AC5–AC9 не трогать.
- `engine_py/tests/test_bd10_l3_authorship.py` — ожидаемое значение метки R3.3.

**Новые файлы**
- `engine_py/tests/test_bd29_in_session_pin_fail_closed.py` — RED.
- этот документ (супersession 220E5F63, требование issue п.6).

**§1v — НЕ в области**
- `_detect_nonhardgate_model_drift` как функция (D2), её юнит-тесты.
- `_assert_in_session_model_or_downgrade` — hard-gate путь уже fail-closed.
- `_pin_mismatch_refusal` — не меняется ни на строку; в этом и смысл (флип как
  следствие чокпоинта, а не его вторая реализация).
- «Нераспознанное семейство» (D4).

## §4. §1a Sibling-audit

| файл | тестов | что читает |
|---|---|---|
| `test_bd10_l3_authorship.py` | 29 | `REQUIREMENT_LABELS`, `observed_model`, `model_pin_mismatch` — **прямой риск** (пинует `"in-session-warn-only"` на `:200`) |
| `test_2FDA949D_model_pin_warn.py` | 11 | AC10 end-to-end + AC5–AC9 юниты — **предмет правки** |
| `test_02FF48F4_model_pin_insession.py` | 8 | `observed_model`, hard-gate downgrade |
| `test_llm_subprocess_hard_gate.py` | 8 | `observed_model` |

Итого **56** тестов, прогнать с `--require-clean`.

## §5. Acceptance criteria

Все ноги гоняют `_invoke_in_session` end-to-end через файловый протокол (форма AC10,
§1y), UUT не мокан.

- **AC1 (предмет).** Не-hard-gate шаг, `.res.json` несёт `dispatched_model` другого
  семейства, чем пин ⇒ `status == "error"`, `error_code == "E_MODEL_PIN_MISMATCH"`,
  `recoverable is False`.
- **AC2 (переякорение выживших половин AC10, п.4).** Тот же прогон по-прежнему эмитит
  `model_pin_mismatch`, и событие несёт `observed_model`, `pinned_model`, `step_name`.
  Утверждается в НОВОМ файле, а не только в том, который лот правит.
- **AC3 (ОТРИЦАТЕЛЬНАЯ НОГА — канал).** `StepResult.data` in-session результата несёт
  `observed_model`, равный `dispatched_model`. Без этого AC «флип» мог бы быть сделан
  второй проверкой внутри `_invoke_in_session`, а канал остался бы пустым — то есть
  ровно дефект B-3, воспроизведённый под видом починки.
- **AC4 (ОТРИЦАТЕЛЬНАЯ НОГА — гейт не инертен).** Fail-closed, который не падает,
  не существует: прогон БЕЗ дрейфа (совпадающие семейства) обязан дать
  `status == "ok"` и НИ ОДНОГО `model_pin_mismatch`. Без этой ноги «починка»,
  роняющая всё подряд, была бы зелёной по AC1.
- **AC5 (граница bd#10, D4).** `dispatched_model` отсутствует в `.res.json` ⇒
  `status == "ok"`, событий нет (`not-checked`). Пинует, что bd#29 НЕ отменял решение
  bd#10 про нераспознанное/отсутствующее.
- **AC6 (hard-gate не задет).** `hard_gate=True` с дрейфом по-прежнему обслуживается
  `_assert_in_session_model_or_downgrade` и падает своим кодом, а не подменяется
  чокпоинтным.
- **AC7 (D5, метка).** `attest.REQUIREMENT_LABELS["R3.3"] == "chokepoint-enforced"`.

## §5a. НАЙДЕНО ПОПУТНО, НЕ ЧИНИТСЯ ЗДЕСЬ: два стража на hard-gate пути, поздний затирает раннего

Замер при написании AC6. `_assert_in_session_model_or_downgrade` отказывает своим
кодом **`E_HARD_GATE_MODEL_DOWNGRADE`** и кладёт в `data` ключ `observed_model`
(`:3386`). Дальше `invoke_llm_subprocess` на `:1301` зовёт `_pin_mismatch_refusal`,
тот читает **этот же** `observed_model`, видит различие семейств и **перезаписывает
результат своим `E_MODEL_PIN_MISMATCH`**.

⇒ Код hard-gate-стража **до вызывающего не доходит**, когда семейства вдобавок
различаются. Замерено: `hard_gate=True`, пин `sonnet`, dispatched `haiku` ⇒ на выходе
`E_MODEL_PIN_MISMATCH`, а не `E_HARD_GATE_MODEL_DOWNGRADE`.

Дефект **предсуществующий**, bd#29 его не создаёт и здесь **не чинит** — это второй
предмет со своим доводом (какой из двух кодов правилен и должен ли поздний страж
уважать уже принятый отказ). AC6 пинует сегодняшнее наблюдаемое поведение, чтобы флип
не сдвинул его незаметно; когда дефект будут чинить, AC6 обязан упасть и потребовать
решения, а не промолчать.

## §5b. ОТСТУПЛЕНИЕ ОТ ТРЕБОВАНИЯ issue п.5 — с замером, а не по удобству

Issue требует: «Правка `test_2FDA949D_model_pin_warn.py` ограничена ожидаемым статусом
и кодом ошибки в AC10». **Замерено: это недостижимо, потому что предмет AC10 переехал
уровнем выше.**

AC10 зовёт `_invoke_in_session` НАПРЯМУЮ. После D1/D2 внутренняя функция про дрейф
больше не решает ничего: адъюдикация — `_pin_mismatch_refusal`, вызываемая из
`invoke_llm_subprocess` (`:1301`). Прямой вызов теперь даёт `status="ok"` и **ноль**
событий (замер: `events captured: ['resolver_runner_request_dir_resolved',
'runner_request_built', 'runner_result_consumed']`). То есть ни статуса, ни кода
ошибки, на которые можно было бы «перенацелить» AC10, на этом уровне не существует.

**Что сделано вместо:** AC10 перепрофилирован в регрессионный страж САМОГО СНЯТИЯ —
внутренняя функция обязана больше не эмитить и не блокировать. Выжившие половины
старого контракта (событие с `observed_model`/`pinned_model`/`step_name` и падение
шага) переякорены в НОВОМ оракуле `test_bd29_in_session_pin_fail_closed.py`
(AC2/AC1) — ровно как требует issue п.4, чтобы изменение не оценивалось артефактом,
который лот сам правит. Юнит-assert'ы AC5–AC9 не тронуты.

## §6. Чего этот PR НЕ утверждает

- Не утверждает, что in-session путь защищён при нераспознанном токене модели (D4).
- Не утверждает, что warn-only форма исчезла из кода: функция-предикат остаётся, снят
  только её вызов из прод-пути.
- Не меняет hard-gate путь и не трогает `_pin_mismatch_refusal`.
