# bd#91: pre-GREEN gate is strict-AND, covers every AC, and does not soften by vote

**Status: r5 (APPROVED_WITH_FIXES by gate r4, fixes applied; r1, r2, r3 REJECTED by Opus gate; no round 5 gate, proceed to RED).** Branch `bd91-pregreen-strict-and`, base `origin/main` `56fbbfa`.

| | |
|---|---|
| **Issue** | bd#91 (HAL engine_py @456d46e97 incident family) |
| **Class** | SYSTEMATIC |
| **Chokepoint** | Два: (1) `phase_5_implement._gate_on_validation` вместе с новым чистым резолвером `_resolve_gate_passed(markdown_verdict, structured, ac_coverage)` — единственное место, где решается "pre-GREEN gate пройден"; (2) `phase_45_spec._invoke_review_llm` — единственное место, где вердикт ревьюера превращается в SHIP/REVISE. Ни один вызывающий код не принимает решение мимо них. |
| **Tier** | Option D (ручной RED -> Opus-гейт -> GREEN): прод-код движка, не `/build` и не MICRO. |
| **Rev** | r5 |
| **Enforcement layer** | Детерминированный: RED-тесты `engine_py/tests/test_bd91_*.py` (включая per-entry проверку `flags_catalog`; общего lint просроченных `flip-by:` в репозитории НЕТ). Prompt-правка — вторичный слой, сама по себе не считается enforcement (Principle C). |
| **Side of the seam** | engine |

## §1 Problem (измерено на `56fbbfa`)

| # | Дыра | Где сейчас |
|---|---|---|
| 1 | Валидатор вправе записать AC без RED-теста как `[GREEN-regression-deferral]` в Quality Findings, а не как FAIL | `phase_5_implement.py:6606-6614`, пункт 2 "TDD-pure GREEN-regression deferral" внутри `_build_validation_prompt` (`:6464`) |
| 2 | `structured.approve` перебивает markdown-вердикт в обе стороны. Расхождение только warn (`validation_verdict_drift`, `:6866-6875`); `passed = bool(structured.approve)` (`:7011-7012`). Docstring `_VALIDATION_STABLE_PREFIX` при этом утверждает "Verdict line is the authoritative gate" — код ему противоречит. | `_write_validation_doc`, `_gate_on_validation`, `_canonical_gate_verdict` (`:446`) |
| 3 | `HAL_SPEC_DEFECT_REROUTE` default `"0"`, flip-by 2026-08-14 истёк. SPEC_DEFECT без флага идёт по legacy-ветке TEST_GAP (ещё один retry тестов на дефектной спеке). | `flags_catalog.py:664`, `phase_5_implement.py:7037` (`get_config().flag(...)`) |
| 4 | `_verify_spec_ac_dsl` при `ACCEPT != result` только эмитит `spec_ac_dsl_warn` и пропускает; блокирует лишь `HAL_AC_DSL_GATE_ENFORCE=1`. Default `"0"`, flip-by 2026-07-25 истёк. | `flags_catalog.py:524`, `phase_45_spec.py:3178,3212` |
| 4a | **Механика флагов (gate r1 #1).** `get_config().flag()` истинен только при env ровно `"1"` (`config_provider.py:56-58`); default из каталога не читается. Одна смена каталога ничего не включает. Каноничный предикат "включено, если не `0`" — `gate_enabled` (`:52-54`). | `phase_5_implement.py:7037`, `phase_45_spec.py:3178,3212` |
| 5 | `_frozen_revise_repoll`: после REVISE ревьюер опрашивается ещё N раз (`spec_frozen_review_repolls`/`spec_review_repolls`, default 2), строгое большинство SHIP отменяет REVISE. | `phase_45_spec.py:3895-3941`, вызов `:3977` |

**Проверка предпосылки issue про "warm-resumed session".** В bd она неверна: `invoke_review_llm` идёт с `hard_gate=True`, а `_dispatch_backend` (`llm_subprocess.py:~1728`) форсит `fresh_session` для любого `hard_gate`/judge (bd#82, bd#101; warm resume разрешён только writer-шагам из `_WARM_RESUME_STEPS`). Значит re-poll уже идёт в свежей сессии. Настоящий дефект — именно голосование: независимый, квалифицированный REVISE гасится числом повторных запросов к недетерминированному судье. Решение ниже опирается на это, а не на session API.

## §2 Design

### §2.0 Решения по открытым вопросам r1 (зафиксированы)

| # | Решение |
|---|---|
| D1 | Forward Map: промпт предписывает фиксированный формат, один bullet на AC: `` - AC<n>: `test_name` `` либо `` - AC<n>: `MISSING` ``. Парсер терпимый (см. §2.1). Блокирующий, без shadow-фазы. |
| D2 | `retire-by:2027-01-15 Refs #91` принят. Флаги остаются kill-switch'ами (`"0"` = legacy байт-в-байт), удаляются только после чистого окна. |
| D3 | `ac_gap` расходует цикл (это FAIL как любой, ограничен `_resolve_validation_cycle_cap`). Непокрытые id идут В НАЧАЛО `findings` для направленного RED-раунда следующего цикла (GH706). |
| D4 | Удаление re-poll остаётся. |

### §2.1 AC coverage: детерминированно

Единственная точка вычисления: `_write_validation_doc` (там есть `validation_raw` и `prev.data["spec_path"]`). `spec_ac_ids` НЕ протаскивается через `_invoke_validation_llm`: `extra_data` там явный whitelist (`:6738-6746`, вне scope), id были бы потеряны, покрытие стало бы no-op. Цепочка, которую обязан пройти AC1: `_write_validation_doc` -> `_verify_validation_citations` (пробрасывает data без изменений, `helper.py:479`) -> `_gate_on_validation`. `_write_validation_doc` пересобирает `data` по whitelist (`:6891-6906`), поэтому ключ `ac_coverage` добавляется туда явно.

Код (всё в `phase_5_implement.py` рядом с `_build_validation_prompt`/`_write_validation_doc`, т.е. НИЖЕ строки 6240; импорты только отложенные, внутри функций):

- **id спеки: `verdict_verify.parse_spec_ac_ids`** (§1g, один парсер; section-scoped, он же источник для `ac_dsl.admit`). Второй парсер и whole-file regex не вводятся (иначе фантомные AC из таблиц §5/чужих матриц). Возвращает set: порядок для отчёта — натуральная сортировка. Сравнение id регистронезависимое по суффиксу (`AC1a` == `AC1A`; `_normalize_ac_id` поднимает только префикс `AC`, `ac_dsl.py:145-149`).
- **Расширение заголовка (только `verdict_verify`, gate r3 #3).** `_AC_SECTION_HEADER_RE` (`verdict_verify.py:40`) требует "acceptance criteria"; заголовки вида `## §3 Acceptance` дают пустой набор, и покрытие молча выключается. Поиск заголовка в `verdict_verify.parse_spec_ac_ids` становится двухпроходным (gate r2 #4): проход 1 = текущий `^#{2,6}\s.*acceptance criteria` (поведение байт-в-байт); ТОЛЬКО если заголовка нет, проход 2 = `^#{2,6}\s.*\bacceptance\b` с `re.IGNORECASE`, как и проход 1. Так заголовки вида `### Acceptance-test harness` или `## change-vs-acceptance` не крадут секцию у спек, где есть "acceptance criteria". В проходе 2 берутся ТОЛЬКО табличные строки `| AC<n> |`; numbered-list fallback (`_AC_NUMBERED_RE`) отключён, чтобы нумерованный список под голым `## Acceptance` не давал фантомные `AC1..ACn`. **Копия `phase_6_review._parse_spec_ac_ids` (`:3219`; regex заголовка `:3207`) НЕ меняется (решение r4).** Она питает жёсткий гейт фазы 6: `_verify_ac_checklist` (`phase_6_review.py:3298`, GH388, default on; fail -> `ac_checklist_fail`) и промпт satisfaction (`:2806-2807`, `:2846-2847`: "If the spec has no ## Acceptance Criteria section, write `- none`"). Расширение её заголовка перевело бы `## §N Acceptance` из `skip/no_spec_acs` в enforced и создало бы конфликт промпта (`- none` против списка id), т.е. изменило бы поведение фазы 6, что вне scope #91. Расхождение двух копий для `## §N Acceptance` ОСОЗНАННОЕ и фиксируется pinning-тестом AC29 и строкой в docstring `verdict_verify.parse_spec_ac_ids` (единственные docstring/comment-правки: здесь и в комментариях `verdict_verify.py:18` и `:39`). Побочный потребитель расширения: `verdict_gate.run_gate` / GH517 parity lint (`verify_ac_parity`, шов `phase_5_implement.py:~7195`, warn-only без `HAL_VERDICT_GATE_LINT_ENFORCE`) для спек с `## §N Acceptance (RED …)` (около 15 в `docs/decisions`) переходит из SKIP в проверяемое состояние; объявлено в §5.
- `_forward_map_coverage(validation_raw, ac_ids, red_texts: list[str] | None) -> {"status": "covered"|"uncovered"|"unverifiable", "uncovered": [...], "reasons": {id: code}, "bullets": {id: text}, "unverifiable_reason": str|None}` (gate r3 #4). Функция чистая, файлов не читает: `red_texts` = тексты прочитанных RED-файлов; `None` или `[]` = `unverifiable` с `unverifiable_reason="red_files_unreadable"`. AC2 передаёт `red_texts` напрямую. Файлы читает вызывающий (`_write_validation_doc`) через именованный helper `_read_red_texts(ctx, prev) -> list[str] | None` (§1aa), см. резолвинг пути ниже.
  - Секция `## Forward Map`; id как слово с префиксом `AC[- ]?` (как `verdict_verify._AC_TABLE_RE`), `AC1` не матчит `AC10`. Id берутся ТОЛЬКО из головы bullet: от начала bullet (или первой ячейки табличной строки `| AC… |` внутри секции Forward Map) до первого разделителя `:`, `->`, `→`, `—`, `–`, `=` или первого backtick. Списки и диапазоны раскрываются только внутри головы. AC-токены после головы — не id и не цитаты (правило (a)).
  - Раскрываются явные списки и диапазоны: `AC1, AC2`, `AC1–AC3`, `AC1..AC3`.
  - **Резолвинг путей RED-файлов (gate r3 #1).** В проде `red_test_paths` репо-относительные (`_derive_red_paths_via_git_diff`, `phase_5_implement.py:540-568`, из `git diff --name-only`); остальные потребители склеивают их с `git_cwd`. `_read_red_texts`: абсолютная запись используется как есть; относительная резолвится как `Path(_resolve_git_cwd(ctx, prev)) / entry` (§1g; `_resolve_git_cwd` — `:2063`, только ВЫЗЫВАЕТСЯ, не правится). Рабочий каталог процесса не используется напрямую (только как последний fallback внутри `_resolve_git_cwd`, `:2067`). Чтение внутри `try` (`OSError`/`UnicodeDecodeError` -> файл пропускается); список пуст/отсутствует/ни один не прочитан -> `None` -> `unverifiable` (`red_files_unreadable`) и событие; частично нечитаемый список проверяется по читаемым.
  - **Кандидаты в цитату (gate r3 #2, язык-агностично).** Кандидат = содержимое ЛЮБОГО backtick-span в bullet после id (любой язык: `test_x`, `TestX`, `testX`, `path::name`, `file.test.ts > name`, описание теста `it("rejects empty input")`), с нормализацией, по порядку: (1) если span имеет вид `it("…")`/`test("…")`/`describe("…")`, кандидат = строка внутри кавычек (описание); (2) взять последний сегмент после ` > `; (3) снять окружающие кавычки `"`/`'`; (4) если в span нет пробелов: взять ПОСЛЕДНИЙ сегмент после `::` (pytest `path::Cls::name`, Rust `mod::tests::name`); затем, если вид `A.B.c` из идентификаторных сегментов (не имя файла с расширением из списка test-extension `path_classifier`), последний сегмент; затем отрезать хвостовой `[…]` (pytest param id) и `(…)`/`()`; для `Test\w+/…` (Go subtest) часть до первого `/`. Результат — идентификатор (действуют правило (c) и граница идентификатора) или описание (правило (d) и подстрочное совпадение). Голый (без backtick) токен по `(?<![\w])(test_\w+|Test\w+|test[A-Z]\w*)(?![\w])` тоже кандидат (legacy формат `- AC1 -> test_one`, который требует `_VALIDATION_STABLE_PREFIX` и использует bd139). `MISSING` = целое слово верхним регистром.
  - **Что никогда не цитата (gate r3 #6).** Кандидат отбрасывается (-> `no_test_cited`, если не осталось других), если он: (a) совпадает с id AC (`^AC[\w-]*$`, регистронезависимо); (b) входит в зарезервированные {`n/a`, `na`, `none`, `tbd`, `deferred`, `missing`, `todo`, `-`, `?`} регистронезависимо или равен `[GREEN-regression-deferral]`/`[passes-pre-fix]`; (c) идентификатор (`^[A-Za-z_]\w*$`) короче 6 символов или без `_`, цифры и заглавной буквы после первой (так `assert`, `def`, `test`, `pytest` не проходят по форме); (d) строка-описание (содержит не-идентификаторные символы) короче 8 символов.
  - **Цитата должна существовать, токен целиком (gate r2 #3, r3 #6).** Оставшийся кандидат засчитывается, только если он встречается в тексте хотя бы одного прочитанного RED-файла как ЦЕЛЫЙ токен: идентификатор — с границами идентификатора `(?<![A-Za-z0-9_])tok(?![A-Za-z0-9_])` (так `test_a` НЕ находится внутри `test_ac1_x`), регистр сохраняется; строка-описание — точное совпадение подстроки после нормализации пробелов (в файле допустимы кавычки `"`, `'`, `` ` ``). Синтаксис языка не разбирается. Не найден -> `citation_not_found`.
  - **Известное ограничение (сознательное, deterministic-first).** Засчитывается любой кандидат из bullet, найденный в RED-файле, включая идентификатор, упомянутый в прозе; второй слой — markdown+structured голос валидатора и предписанный промптом формат `- AC<n>: \`test\``. Также: `red_test_paths` пуст в `prev`, но сохранён на диске — покрытие тихо деградирует в `unverifiable` (допустимо).
  - AC покрыт, iff есть bullet с id, в нём найденная (по правилу выше) цитата и нет `MISSING`. Дубли (gate r2 edge 7): если для id есть несколько bullet'ов, AC непокрыт, когда ЛЮБОЙ из них содержит `MISSING`; иначе покрыт, когда хотя бы один содержит найденную цитату.
  - Spec Compliance: статус = первое слово после id, пропуская разделители `:`, `-`, `->`, `→`, `—`, `–`, `=`, пробелы и markdown-выделение `*`/`_` (реальный формат `AC3 → partial: …`, bd139 `:351-354`), регистронезависимо. AC непокрыт только если статус равен `missing`/`partial` ("no missing negatives" не считается).
  - Коды причин (§1n, полный перечень): `absent` (нет bullet), `MISSING`, `no_test_cited`, `citation_not_found`, `spec_compliance_missing`, `spec_compliance_partial`.
- `data["ac_coverage"]` пишется всегда. **Отсутствие ключа `ac_coverage` в `prev.data` = `unverifiable` (НЕ fail, НЕ uncovered, без KeyError)**: около 6 sibling-файлов вызывают `_gate_on_validation` с руками собранным `prev` (см. §5), плюс resume со старым sentinel'ом. Читается только через `.get`.
- **`unverifiable`** (событие `ac_coverage_unverifiable`, `reason`; gate r3 #6: эмитится РОВНО ОДИН раз за вызов цепочки (за цикл валидации): `_write_validation_doc` эмитит, когда вычислил статус `unverifiable`; `_gate_on_validation` эмитит ТОЛЬКО когда ключа `ac_coverage` в `prev.data` нет (`reason="missing_key"`) и НИКОГДА не эмитит повторно, если ключ есть (в т.ч. со статусом `unverifiable`); следующий цикл — новое событие): спека без разбираемых AC (legacy), нечитаемый файл (чтение внутри `try`), исключение парсера, нет `spec_path`. Решает пара markdown+structured. Пустой список AC — отдельное видимое состояние, не "покрыто".
- **Uncovered AC = FAIL** с причиной `ac_gap`. Выходные поля (определение "reject_reason", gate r1 #5): `reject_reason` в r1 был полем structured-блока валидатора, не поля движка. Здесь: (a) `reason` из `_resolve_gate_passed` = `"ac_gap"` пишется в `data["gate_reason"]` и в строку reject-лога (`_log_validation_reject`) с `reason_code="VALIDATION_AC_GAP"` (словарь reject-лога — `VALIDATION_*`, `reject_log.py:216-222`; литерал `ac_gap` в `reason_code` не пишется); (b) id пишутся в `data["ac_gap_ids"]`, в голову `data.findings` в форме GH706 `ENGINE AC-COVERAGE GAP (cycle N): AC3, AC4`, и в payload события `ac_coverage_gap` (`uncovered`, `cycle`, `bullets` для калибровки). `gate_reason` присутствует на ТРЁХ возвратах `_gate_on_validation`: PASS (`phase_5_implement.py:~7215`, значения `and`/`md_only`, gate r3 #5), ниже cap и терминальном; `ac_gap_ids` и голова `findings` — на обоих fail-возвратах: ниже cap и терминальном (`cycle == cap`, `phase_5_implement.py:~7163-7177`, где `E_VALIDATION_FAILED` собирает собственный dict; исполнитель добавляет поля туда же, AC25).

**Prompt (вторичный слой).** Пункт 2 "TDD-pure GREEN-regression deferral" удаляется; заменяется: "Каждый AC обязан иметь тест в Forward Map, ровно один bullet на AC в формате `- AC<n>: \`test_name\`` или `- AC<n>: \`MISSING\``. AC, чьё поведение выполняется до фикса, не освобождается: перечисли его тест и пометь в Quality Findings `[passes-pre-fix]` (информативно). Нет теста = `MISSING` + FAIL." Токен `[GREEN-regression-deferral]` в коде и промпте исчезает. Требование "каждый AC падает в RED" не вводится (политика RED-ворот вне scope); исчезает лазейка превратить отсутствие теста в заметку.

### §2.2 Strict-AND: `_resolve_gate_passed(markdown_verdict, structured, ac_coverage) -> (passed, reason)`

Правила применяются СТРОГО по порядку, первое совпавшее решает. Таблица тотальна на 4 (markdown PASS/FAIL/UNKNOWN/PARTIAL) x 4 (structured: approve true / approve false / absent / unparsed; absent и unparsed = "второго голоса нет") x 2 (coverage: uncovered / не uncovered, где unverifiable и отсутствующий ключ = не uncovered) = 32 ячейки.

| # | Условие | passed | reason |
|---|---|---|---|
| 1 | markdown == FAIL (любой structured, любой coverage) | нет | `markdown_fail` |
| 2 | markdown == UNKNOWN (любой structured, в т.ч. отсутствует; любой coverage) | нет | `markdown_missing` (fail-closed, контракт "UNKNOWN treated as FAIL") |
| 2b | markdown == PARTIAL или любой токен, кроме PASS/FAIL/UNKNOWN (любой structured; любой coverage) | нет | `markdown_partial` (fail-closed) |
| 3 | markdown == PASS, structured approve false | нет | `structured_veto` |
| 4 | markdown == PASS, coverage uncovered (structured approve true/absent/unparsed) | нет | `ac_gap` |
| 5 | markdown == PASS, structured approve true, coverage не uncovered | да | `and` |
| 6 | markdown == PASS, structured absent/unparsed, coverage не uncovered | да | `md_only` |

**PARTIAL (gate r2 #1, политика зафиксирована).** Верификатор цитат ставит `forwarded["verdict"] = "PARTIAL"` (`lib/plugins/anti_hallucination/helper.py:589-590`; `verify_completeness_issues > 0` даёт FAIL, `:587-588`), и это значение доходит до `_gate_on_validation` через `prev.data["verdict"]`. Выбрано fail-closed: PARTIAL никогда не пасс при strict-AND, как и UNKNOWN (правило 2b; нумерация 2b сохраняет номера 3-6). Изменение поведения относительно сегодня: PARTIAL + `approve:true` сегодня проходит (structured перебивает), после r3 не проходит; один неверифицированный quote-line стоит цикла и терминален на cap — это сознательная цена (нет голоса, перебивающего демотированный вердикт). Sibling-правка: `test_phase_5_gate_verdict_canonical_gh349.py:276` (`test_canonical_gate_verdict_helper_invariant_matrix`, цикл по `"PARTIAL"`) — проверяет только `_canonical_gate_verdict`, остаётся зелёным; подтверждается прогоном.

**Кто решает категорию.** `verdict_category` берётся из structured-блока валидатора (`_resolve_verdict_category`), включая `SPEC_DEFECT`; движок принудительно ставит `TEST_GAP` ТОЛЬКО при `reason == "ac_gap"`. Так как `structured_veto` (правило 3) стоит раньше `ac_gap` (правило 4), SPEC_DEFECT-вердикт валидатора (всегда approve false) никогда не подавляется `ac_gap`. `ac_gap` — не `SPEC_DEFECT`: reroute в спеку только по решению самого валидатора.

Fail-closed на "markdown отсутствует + approve true": цена один лишний цикл, ограничен cap. Structured absent/`json_error`/`schema_violation` = решает markdown + покрытие (provider-agnostic).

`gate_verdict` строится из `passed` через `_canonical_gate_verdict` (инвариант `PASS in token == passed`). `validation_verdict_drift` остаётся, получает поле `resolved` и `severity="error"` при расхождении.

### §2.3 Reroute по умолчанию ON

- **Механика (gate r1 #1).** Вызов `get_config().flag("HAL_SPEC_DEFECT_REROUTE")` на `phase_5_implement.py:7037` заменяется на `get_config().gate_enabled("HAL_SPEC_DEFECT_REROUTE")` (unset = включено, ровно `"0"` = выключено). Запись каталога: `kind:"gate"`, `default:"1"`, description с `retire-by:2027-01-15 Refs #91` вместо просроченного `flip-by`. Любое значение кроме `"0"` (`false`, `off`, пустая строка) читается как включено; это фиксируется тестом (AC20).
- Предусловия `:7037-7125` (долговечный бюджет, `no_progress`, `_MAX_SPEC_DEFECT_REROUTES`) не меняются; при сомнении путь падает в legacy TEST_GAP.
- Явный `=0`: байт-в-байт legacy + событие `spec_defect_reroute_disabled` (`severity="warning"`, `cycle`, `source="explicit"`).
- Реальные выходы reroute (не `phase_reroute`/`reroute_to`): `StepResult.error_code == "E_SPEC_DEFECT"`, `data.reroute_attempt` (>=1), `data.spec_defect_reason` (непустой), `data.spec_sha`; маршрутизацию в phase_45_spec выполняет `lib/task_resume.py:58,68` (`REROUTE_TARGET`/`_REROUTE_CODE`), не гейт. Долговечный побочный эффект: `scratchpad/resume/spec-defect-reroutes-<run_id>.json` (запись `lib/spec_defect_ledger.py:75-96`) содержит `spec_sha`.

### §2.4 AC checklist gate блокирует по умолчанию

- **Механика.** `phase_45_spec.py:3178` и `:3212` переходят на `get_config().gate_enabled("HAL_AC_DSL_GATE_ENFORCE")`. Запись каталога: `kind:"gate"`, `default:"1"`, `retire-by:2027-01-15 Refs #91`; из description и из кодовых комментариев (`phase_45_spec.py:3212`, `flags_catalog.py:528`) удаляются токены `flip-by`. `HAL_AC_DSL_GATE=0` (-> `env_skip`) без изменений; `HAL_AC_DSL_GATE_ENFORCE=0` -> никогда не блокирует (старый warn-only); для спек БЕЗ `### AC-checks` payload легаси-ветки (ниже, AC21) действует независимо от ENFORCE; для спек с секцией поведение байт-в-байт legacy.
- **Легаси-ветка (gate r1 #2).** Без неё ENFORCE-по-умолчанию блокирует все спеки без `### AC-checks`: `ac_dsl.admit` возвращает REJECT и для "no ACs found", и для "AC-checks section missing" (`ac_dsl.py:215-226`); в репозитории 0 из 47 спек в `docs/decisions/` имеют `### AC-checks`. Решение: в `ac_dsl.py` добавляется публичный предикат `has_ac_checks_section(spec_text) -> bool` (по существующему `_AC_CHECKS_HEADER_RE`, `:152`), и `_verify_spec_ac_dsl` ДО вызова `admit` проверяет его. Нет секции -> эмит `spec_ac_dsl_warn` (`reason="legacy_no_ac_checks"`), возврат `ok` с `data["spec_ac_dsl_skipped"]="legacy_no_ac_checks"`. Опора на структурный предикат, а не на подстроку сообщения. Блокируются только спеки, где секция ЕСТЬ, но не принимается (`ACCEPT != result`): `_spec_gate_retry`/directed-repair (GH634), `E_SPEC_AC_UNCOMPILABLE`.
- **Инфраструктура деградирует, не блокирует.** Чтение спеки (`phase_45_spec.py:3173`, сейчас вне `try`) переносится ВНУТРЬ `try`; `OSError`/`UnicodeDecodeError`/исключение `admit()` -> `spec_ac_dsl_driver_error` (`severity="error"`) + `data["spec_ac_dsl_unverified"]=True`, шаг `ok`. Это меняет поведение при `ENFORCE=1` (сейчас fail-closed, `test_gh634`/`GH517A2 ac9`) — правки объявлены в §5.

### §2.5 Re-poll удаляется

- Вызов `_frozen_revise_repoll` (`phase_45_spec.py:3977`) убирается: единственный REVISE квалифицированного ревьюера (hard_gate, fresh session — `llm_subprocess.py:1728-1729`, `allowed_tools=["Read"]`) финален. Удаляются `_frozen_revise_repoll` (`:3895-3941`), `_ship_reachable` (`:3886-3892`), событие `phase_45_spec_review_repoll`.
- Почему не "re-poll в независимой сессии": больше повторов того же недетерминированного судьи — тот же голос по объёму.
- **Legacy-вход.** Ключи org_config `spec_frozen_review_repolls`/`spec_review_repolls` > 0 игнорируются; `spec_review_repoll_ignored` (`key`, `value`) эмитится один раз на пару (процесс, `run_id`, `key`) — модульный set; между процессами (каждая фаза = отдельный `run.py`) дедупа нет, это принято. `0`/отсутствие — без события. Проверка в `_invoke_review_llm` на каждом вызове (независимо от вердикта); оба ключа проверяются независимо от `is_frozen`; событие эмитится для каждого ключа с int-значением > 0.
- Провайдер-агностично; если свежую сессию получить нельзя, `invoke_llm_subprocess` вернёт `status="error"`, гейт остаётся REVISE/ошибкой, SHIP не выводится из объёма.

### §2.6 Fence (#94) и class-I

- Новые функции и импорты (`verdict_verify`, `ac_dsl`) НЕ выше строки 6240 `phase_5_implement.py`: импорты отложенные, внутри функций; helpers рядом с `_build_validation_prompt`/`_write_validation_doc`. `_resolve_gate_passed` размещается рядом с `_gate_on_validation` (ниже 6240).
- `_resolve_git_cwd` определён выше 6240 (`phase_5_implement.py:2063`), но новый код его только ВЫЗЫВАЕТ (из `_read_red_texts`); определение и строки fence #94 не правятся.
- Class-I (bd#150, gate r3 #4): ровно ДВА новых read-сайта, ключи `<rel>::<qualname>::read_text#<ordinal по строке>` (`class_i_lint.py:201-206`): (1) `workflows/phase_5_implement.py::_write_validation_doc::read_text#0` — спека, `not-prompt`, note "spec read for AC-id parse; only derived AC ids (never spec bytes) reach next-cycle findings"; (2) `workflows/phase_5_implement.py::_read_red_texts::read_text#0` — RED-файлы, `not-prompt`, note "substring citation check only; no bytes reach a prompt". Это ВСЕ новые ключи `class_i_inventory.json`; исполнитель сверяет фактические ординалы через `class_i_lint.call_sites`, прогоняет `class_i_lint.py` (rc0) и `test_bd150_class_i_inventory.py`. Ключ r1 в `_build_validation_prompt` НЕ вводится.

## §3 Acceptance (RED: `engine_py/tests/test_bd91_pregreen_strict_and.py`, `engine_py/tests/test_bd91_spec_review_verdict.py`)

Все RED-тесты импортируют новые символы (`_resolve_gate_passed`, `_forward_map_coverage`, `has_ac_checks_section`) ОТЛОЖЕННО внутри тела теста (§1q): падение на assert-time, не на collection.

| AC | Что падает до GREEN |
|---|---|
| AC1 | Реальная цепочка без ручного `prev.ac_coverage`: спека AC1..AC3 на диске, `validation_raw` с Forward Map по AC1, AC2, `Verdict: PASS` + `approve:true` -> `_write_validation_doc` -> `_verify_validation_citations` -> `_gate_on_validation`: не пасс, `data.gate_reason=="ac_gap"`, `data.ac_gap_ids==["AC3"]`, голова `data.findings` = `ENGINE AC-COVERAGE GAP (cycle N): AC3`, событие `ac_coverage_gap` (`uncovered`, `bullets`) |
| AC2 | `_forward_map_coverage(validation_raw, ac_ids, red_texts)` юнит (`red_texts` передаются напрямую): `` `N/A` ``, `` `TBD` ``, `` `none` ``, `` `[GREEN-regression-deferral]` `` и выдуманное имя теста = uncovered (`no_test_cited` для зарезервированных слов, `citation_not_found` для выдуманного); `` `test_a` `` против текста с одним `test_ac1_x` = `citation_not_found` (граница идентификатора), `` `AC3` `` = не цитата, `` `assert` ``/`` `def` `` = не цитата (форма), `` `none` `` в докстринге RED-файла не делает AC покрытым; Go `` `TestFoo` ``, Swift `` `testFoo` ``, TS-описание `` `"rejects empty input"` `` и `` `it("rejects empty input")` ``, `` `file.test.ts > rejects empty input` ``, присутствующие в тексте = covered; голый `- AC1 -> test_one` с `test_one` в тексте = covered; `red_texts=None`/`[]` = `unverifiable`/`red_files_unreadable`; Spec Compliance `AC3 → partial: …` (стрелка `→`) = uncovered;  реально присутствующее в RED-файле имя (в т.ч. с префиксом `path::`) = covered; дубли bullet'ов (один `MISSING`) = uncovered; `AC1` не матчит `AC10`; `MISSING`-bullet и bullet без цитаты = uncovered; списки/диапазоны `AC1, AC2` / `AC1–AC3` / `AC1..AC3` раскрываются; `AC1a`==`AC1A`; Spec Compliance `partial` = uncovered, а "no missing negatives" — нет; `[GREEN-regression-deferral]` не освобождает; все 6 кодов причин (`no_test_cited` достижим и для зарезервированных слов); (gate r4 #1) covered, когда `test_m`/`it_works`/`TestFoo` есть в тексте: `` `tests/x.py::TestCls::test_m` ``, `` `mod::tests::it_works` ``, `` `TestCls.test_m` ``, `` `test_m[case1]` ``, `` `test_m()` ``, `` `TestFoo/empty_input` ``; (gate r4 #2) `` - AC5: `MISSING` (unlike AC1) `` не делает AC1 uncovered; `` - AC1: `test_x` (see also AC5) `` не покрывает AC5; строка таблицы Forward Map `` | AC1 | `test_x` | `` = covered |
| AC3 | `approve=True`, markdown `Verdict: FAIL` -> `passed=False`, `reason=="markdown_fail"`, `gate_verdict` без `PASS`, LoopRunner re-iterate до cap; защищает и случай demotion цитат (`helper.py:457-460`) + `approve:true` |
| AC4 | `approve=True`, markdown UNKNOWN -> `markdown_missing` (fail-closed); также UNKNOWN + structured absent |
| AC5 | `approve=False`, markdown PASS -> `structured_veto` (регресс-щит, уже зелёный) |
| AC6 | structured absent / `json_error` / `schema_violation`, markdown PASS, покрытие ok -> `md_only` (щит) |
| AC7 | Полная параметризованная матрица 32 ячеек §2.2 (4 markdown x 4 structured x 2 coverage; `PARTIAL` -> `markdown_partial` для всех 8 ячеек) по `_resolve_gate_passed` с ожидаемыми `(passed, reason)` по порядку правил, плюс инвариант `PASS in canonical == passed`; включает PASS+approve false+uncovered -> `structured_veto` и category-правило: `TEST_GAP` форсируется только при `ac_gap`, validator `SPEC_DEFECT` сохраняется |
| AC8 | `_build_validation_prompt` не содержит `GREEN-regression-deferral`/`TDD-pure`; содержит формат bullet `- AC<n>:` и `passes-pre-fix` |
| AC9 | Чистое окружение (флаг не задан), SPEC_DEFECT от валидатора, предусловия §2.3 выполнены -> `StepResult.error_code=="E_SPEC_DEFECT"`, `data.reroute_attempt>=1`, непустой `data.spec_defect_reason` (а не legacy TEST_GAP); `get_config().gate_enabled("HAL_SPEC_DEFECT_REROUTE")` True без env |
| AC10 | **Побочный эффект в проде:** тот же сценарий без env-переопределений: на диске `scratchpad/resume/spec-defect-reroutes-<run_id>.json` содержит `spec_sha` спеки. Фикстура по образцу `test_bd139_single_reviewer.py:470 _spec_defect_setup` БЕЗ `HAL_SPEC_DEFECT_REROUTE=1`: ненулевой `telemetry_ctx.get_current_run()`, читаемый `spec_path`, `org_config.scratchpad_dir` |
| AC11 | `HAL_SPEC_DEFECT_REROUTE=0` -> legacy TEST_GAP байт-в-байт + `spec_defect_reroute_disabled` |
| AC12 | Спека с НЕвалидным, но присутствующим `### AC-checks`, без env -> `error`/`E_SPEC_AC_UNCOMPILABLE` (блок); фикстура ставит `org_config['directed_repair_skip']=True` (directed repair default ON, `lib/directed_repair.py:118-128`, иначе тест породит repair-LLM) |
| AC13 | `admit()` бросает исключение, без env -> `status="ok"`, `spec_ac_dsl_unverified`, `spec_ac_dsl_driver_error` |
| AC14 | Спека без `### AC-checks` и `HAL_AC_DSL_GATE=0` -> `ok`/`env_skip` (щит) |
| AC15 | REVISE от ревьюера; фейковый бэкенд на последующие вызовы SHIP -> результат REVISE, ровно 1 вызов бэкенда, нет события `phase_45_spec_review_repoll` |
| AC16 | `spec_frozen_review_repolls=2` -> игнорируется: 1 вызов, одно `spec_review_repoll_ignored`; повторный вызов в том же процессе/`run_id` — события нет (dedupe scope = процесс + run_id + key); другой `run_id` — событие снова; при `telemetry_ctx.get_current_run() is None` ключ dedupe = `(None, key)`, т.е. одно событие на процесс и key (не «всегда эмитить»); `0` -> без события |
| AC17 | `flags_catalog` per-entry (не общий lint): обе записи `kind=="gate"`, `default=="1"`, нет `flip-by:` в description, есть `retire-by:` с датой, ссылка на #91. Дата сравнивается с ФИКСИРОВАННОЙ константой теста (`retire-by` == `2027-01-15`), а не с «сегодня»: тест не краснеет у несвязанных PR в 2027-01-16; просрочку ловит ревью, а не CI (осознанно, gate r2 edge 6) |
| AC18 | Спека без разбираемых AC-строк через реальную цепочку write -> citations -> gate: ровно ОДНО событие `ac_coverage_unverifiable` (gate не эмитит повторно при наличии ключа), решает markdown+structured |
| AC19 | Заголовок `## §3 Acceptance` (без "criteria") даёт непустые id через `verdict_verify.parse_spec_ac_ids`; таблица AC вне секции (в §5) не даёт фантомных id; спека с ранним `### Acceptance-test harness` И поздним `## Acceptance Criteria` берёт секцию criteria (проход 1); голый `## Acceptance` с нумерованным списком даёт пустой набор (нет фантомов); `_write_validation_doc` не дублирует парсер: sentinel-monkeypatch `verdict_verify.parse_spec_ac_ids` (идиома GH1065 AC10, `test_gh1065...py:481-501`) фиксирует вызов. Parity с `phase_6_review` НЕ проверяется (копия не меняется, см. AC29) |
| AC20 | **RED** (gate r3 #5; до GREEN `flag()` истинен только при ровно `"1"`, `config_provider.py:56-58`): утверждается через вызывающие места: `HAL_SPEC_DEFECT_REROUTE` = `false`/`off`/пусто -> `_gate_on_validation` даёт `E_SPEC_DEFECT`; `HAL_AC_DSL_GATE_ENFORCE` = `false`/`off`/пусто -> `_verify_spec_ac_dsl` блокирует; ровно `0` — legacy. Default (unset) дополнительно покрыт AC9/AC12; ENFORCE-половина: фикстура ставит `org_config['directed_repair_skip']=True` (`lib/directed_repair.py:118-128`) |
| AC21 | Legacy-ветка: спека с AC-таблицей, без `### AC-checks`, без env -> `_verify_spec_ac_dsl` возвращает `ok`, ровно одно `spec_ac_dsl_warn` с `reason=="legacy_no_ac_checks"` И `reasons` содержащим строку `"AC-checks section not found"` (полный payload: `{reason, reasons: ["AC-checks section not found (legacy_no_ac_checks)"], spec_path}`; это держит `GH517A2 test_ac12:423-428` зелёным без правки теста), `data["spec_ac_dsl_skipped"]=="legacy_no_ac_checks"`, `admit` не вызван |
| AC22 | **Щит по исходу (без KeyError и сегодня), RED только по наблюдаемому:** `prev.data` без ключа `ac_coverage` (руками собранный), PASS + `approve:true` -> `_gate_on_validation` не бросает, проходит, `data.gate_reason=="and"` (поле на PASS-возврате, §2.1) и ровно одно событие `ac_coverage_unverifiable` с `reason=="missing_key"` (наблюдаемое существует только после GREEN) |
| AC23 | Нечитаемая спека/исключение парсера в `_write_validation_doc` -> `ac_coverage.status=="unverifiable"` + событие, без исключения; нечитаемая спека в `_verify_spec_ac_dsl` -> `ok`, `spec_ac_dsl_unverified` (чтение внутри `try`) |
| AC24 | `validation_verdict_drift` при расхождении содержит `resolved` и `severity=="error"` |
| AC25 | `ac_gap` на терминальном пути (`cycle == cap`): `StepResult.error_code=="E_VALIDATION_FAILED"`, `data.gate_reason=="ac_gap"`, `data.ac_gap_ids` и голова `data.findings` присутствуют (не только ниже cap, как в AC1); строка reject-лога имеет `reason_code=="VALIDATION_AC_GAP"` (читается из реального reject-лога на диске, и в AC1, и здесь) |
| AC26 | Источник и резолвинг RED-файлов через `_read_red_texts`/реальную цепочку: (a) `red_test_paths` задан, но все файлы нечитаемы -> `ac_coverage.status=="unverifiable"`, `unverifiable_reason=="red_files_unreadable"`, ровно одно событие, без исключения (деградация, не fail); (b) один читаем, один нет -> проверка по читаемому; (c) ПРОД-ФОРМА (gate r3 #1): ОТНОСИТЕЛЬНЫЕ `red_test_paths` + `org_config['git_cwd']=<tmp repo>` + рабочий каталог процесса в другом месте (`monkeypatch.chdir`) -> цитата проверяется (covered / `citation_not_found`), НЕ `unverifiable`; (d) worktree-случай: `org_config` без `git_cwd`, с `current_worktree_path=<tmp repo>`, cwd процесса в другом месте -> относительные записи резолвятся от `current_worktree_path` (precedence `_resolve_git_cwd`; `prev.data['git_cwd']` на этом сайте отсутствует из-за whitelist `:6738-6746`); (e) абсолютная запись используется как есть |
| AC27 | Фикстура AC1 допускает прохождение реальной `verify_validation_doc`: Reverse Map полон по `red_test_paths`, quote-строк `> path:line:` нет (иначе вердикт демотируется в FAIL/PARTIAL и `gate_reason` не будет `ac_gap`) — проверяется предусловием фикстуры (`verdict=="PASS"` после `_verify_validation_citations`) |
| AC28 | `PARTIAL` из верификатора цитат + `approve:true`, покрытие ok -> `passed=False`, `reason=="markdown_partial"` через реальную цепочку (одна не верифицируемая quote-строка в `validation_raw`), `gate_verdict` без `PASS` |
| AC29 | **Pinning расхождения, phase 6 не меняется (gate r3 #3):** спека с `## §3 Acceptance` и таблицей AC1, AC2: `verdict_verify.parse_spec_ac_ids` даёт `{AC1, AC2}` (RED до GREEN: сейчас пусто); `phase_6_review._parse_spec_ac_ids` по-прежнему возвращает `[]`, а `_verify_ac_checklist(spec, ответ без ## AC Checklist)` возвращает `skip`/`no_spec_acs` (щит: hard gate фазы 6 не изменён ни до, ни после GREEN); docstring-пометка о расхождении в `verdict_verify` присутствует |

**Shields (зелёные до GREEN, не RED):** AC5, AC6, AC14, AC22 (по исходу; RED только по наблюдаемому `gate_reason=="and"` и событию), phase-6 половина AC29. Остальные AC падают до GREEN: AC1-AC4, AC7-AC13, AC15-AC21, AC23-AC26, AC28, verdict_verify-половина AC29 (AC27 — предусловие фикстуры).

Anchors: AC1 (реальная цепочка write -> citations -> gate на диске), AC10 (долговечный ledger); AC15 — реальный вызов UUT с фейковым бэкендом (бэкенд не является UUT).

## §4 Degrade-not-fail

- Нет AC-id / парсер упал / файл нечитаем / нет ключа `ac_coverage` -> покрытие `unverifiable` с событием, гейт не падает (AC18, AC22, AC23).
- Нет structured или он битый -> решает markdown + покрытие (AC6).
- `admit()` упал или спека нечитаема в `_verify_spec_ac_dsl` -> шаг `ok`, `spec_ac_dsl_unverified` (AC13, AC23).
- Спека без `### AC-checks` -> warn-pass (AC21).
- Нечитаемые RED-файлы при проверке существования цитаты -> `unverifiable`, не fail (AC26).
- Устаревшие ключи re-poll -> игнорируются с событием (AC16).
- Нет независимой оценки ревьюера -> REVISE/ошибка стоит.
- Ошибка telemetry никогда не меняет вердикт (`_emit_safe`).

## §5 Sibling tests (проверено grep'ом по `engine_py/tests`)

**Нужна объявленная правка в RED-фазе:**
- `test_gh514p1_frozen_review_repoll.py`, `test_gh541_nonfrozen_review_repoll.py`, `test_gh707_reviewer_repoll_cap.py` — удаляются, заменяются AC15/AC16.
- `test_bd141_p4d_role_template_injections.py:747` — кейс `"phase_45_spec:4150-repoll"` (`_REVISE`, min calls 2) ломается при удалении re-poll: кейс удаляется или min calls = 1.
- `test_phase_5_gate_structured_verdict.py` — ломается только `test_gate_proceeds_when_markdown_fail_but_structured_approve` (меняется на непроход); AC6/AC7 этого файла остаются зелёными.
- `test_phase_5_gate_verdict_canonical_gh349.py` — ломаются `test_gate_inverse_drift_proceed_canonicalizes_verdict` и `test_gate_unknown_markdown_with_structured_approve_canonicalizes_to_pass`; `test_gate_terminal_drift_at_cap_...` (PASS + approve false) остаётся зелёным, как и `test_gate_core_drift_loop_continue`, `test_gate_marker_equivalence_on_drift`, `test_canonical_gate_verdict_helper_invariant_matrix`.
- `test_phase_45_ac_dsl_wiring_GH517A2.py` — `test_ac5` (warn-only при unset env) ломается сменой default; `test_ac8` (admit бросает, env unset) НЕ ломается: итог остаётся `ok` + один `spec_ac_dsl_driver_error` с `error`; `test_ac9` (fail-closed на исключении) меняется на деградацию (§2.4); `test_ac10` (`kind=="flag"`, `:386`, default `"0"`) -> `kind=="gate"`, `"1"`; `test_ac6`/`test_ac7` (env ENFORCE=1 явно) остаются; `test_ac12` (нет секции AC-checks, ждёт ровно одно `spec_ac_dsl_warn` с `reasons`, содержащим `"AC-checks section not found"`, `:423-428`) остаётся зелёным ТОЛЬКО благодаря легаси-ветке §2.4 С `reasons` в payload (AC21).
- `test_gh634_ac_uncompilable_directed_repair.py` — `test_ac9_enforce_off_regression_ok_and_repair_not_called` (env unset, uncompilable, ожидает `ok`; `:327`) ломается из-за смены default, не из-за обработки исключений. Тест НЕ задаёт env вообще (`:327-343`); правка в RED-фазе: добавить `monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "0")`. Остальные зелёные.

**Прогон по спискам, зелёными остаются только при закреплении §2.1 "нет ключа = unverifiable" (AC22):** прямые вызовы `_gate_on_validation` с руками собранным `prev`: `test_GH1674_injection_missing.py` (включая AC24 PASS-ветку с `"## Verdict\nPASS\n"`), `test_GH706_validate_cap_directed_reject.py`, `test_phase_5_implement_W13.py`, `test_phase_5_g1_band_aid_9_verify_green.py`. (`test_gh1018_orphan_green_cycle_resolution.py` и `test_gh1626d_orphan_green_recovery.py` `_gate_on_validation` НЕ вызывают; перенесены в «остаются зелёными» только как не затронутые.)

**Полные workflow-тесты phase_45** (фикстуры без `### AC-checks`: `test_phase_45_spec.py`, `test_E6602155_frozen_spec_ingest.py`, `test_phase_45_spec_EECB919C.py`, `test_gh557_resume_seam_flips.py`): зелёные благодаря легаси-ветке; подтверждаются прогоном.

**Вызовы `_build_validation_prompt`/`_write_validation_doc` с несуществующими файлами спеки** (`test_phase_5_graphfirst_DA48BEAC.py`, `test_gh705_callsite_stable_prefix.py`, `test_GH897_sentinel_input_hash.py`, `test_phase_5_b4d83b40_red_rubric.py`): зелёные, пока деградация на нечитаемой спеке держится (AC23).

**`ac_dsl.admit` (`ac_dsl.py:215`), gate r4 #7:** спека с `## §N Acceptance` + `### AC-checks` переходит из REJECT "no ACs found" в реальную проверку; run-confirmation: GH517A2, gh634.

**Потребители расширенного заголовка AC-секции (gate r2 #4; только `verdict_verify`):** `verdict_gate.run_gate` / GH517 parity lint (`verify_ac_parity`), хост audit-gate hook; run-confirmations: `test_GH749*` (`_classify_parity`), `test_gh751*`, `test_gh1065*` AC12, плюс любой тест `parse_spec_ac_ids`; для двухпроходного поиска (проход 1 байт-в-байт) ожидаются зелёными. Исполнитель прогоняет их `--require-clean`; для спек `## §N Acceptance (RED …)` SKIP -> проверяемое состояние (только warn, пока не задан `HAL_VERDICT_GATE_LINT_ENFORCE`).

**`phase_6_review` НЕ меняется (gate r3 #3):** `_parse_spec_ac_ids` (`:3219`; regex заголовка `:3207`), `_verify_ac_checklist` (`:3298`, GH388, `ac_checklist_fail`), `_ac_checklist_ids_directive` (GH1065) и промпт satisfaction (`:2806-2807`, `:2846-2847`) остаются как есть; run-confirmations без правок: `test_gh388_ac_checklist_satisfaction.py`, `test_gh1065_satisfaction_ac_ids.py` (`--require-clean`).

**Остаются зелёными:** `test_gh1018_orphan_green_cycle_resolution.py`, `test_gh1626d_orphan_green_recovery.py`, `test_bd139_single_reviewer.py` (флаг явно `=1`; он же образец фикстуры AC10), `test_phase_5_step4_validation_schema.py`, `test_gh925_terminal_fail_sentinel_invalidation.py`, `test_gh963_validation_execution_failure.py`. Исполнитель подтверждает прогоном `--require-clean`; pin текста deferral-промпта в тестах отсутствует (grep чист).

## §6 Scope

**Трогаются:** `workflows/phase_5_implement.py` (`_build_validation_prompt` только текст, `_write_validation_doc`, `_gate_on_validation`, новые `_forward_map_coverage`, `_read_red_texts`, `_resolve_gate_passed`; всё ниже 6240); `workflows/phase_45_spec.py` (`_verify_spec_ac_dsl`, `_invoke_review_llm`, удаление repoll, `gate_enabled`); `ac_dsl.py` (только `has_ac_checks_section`); `verdict_verify.py` (только двухпроходный поиск заголовка в `parse_spec_ac_ids` и docstring о расхождении с phase 6, плюс обновление комментариев "DUPLICATED … parity source" в `verdict_verify.py:18` и `:39` (для прохода 2 больше не верно)); `flags_catalog.py` (две записи); `conformance/class_i_inventory.json`; тесты из §5; `error_codes`/event-реестр, если события регистрируются централизованно. `_invoke_validation_llm` НЕ трогается.

**НЕ трогаются:** `phase_6_review.py` целиком (включая `_parse_spec_ac_ids` `:3219`, `_verify_ac_checklist` `:3298`, промпт `:2806`/`:2846-2847`; решение r4); `_resolve_git_cwd` (`phase_5_implement.py:2063`) — только вызывается. **НЕ трогаются** (параллельные лоты): #94 engine-owned paths (`phase_5_implement.py` строки <6240 и `lib/util/engine_owned.py`); #92 per-cycle artifact invalidation; #192/#206 class-M; #211 release version; `phases/`, `llm_subprocess.py`, `lib/task_resume.py`, `lib/spec_defect_ledger.py`.

## §7 Открытые вопросы

Нет (закрыты в §2.0).

## §8 Changelog r1 -> r2 (ответ на gate r1: REJECTED)

| Finding | Sev | Fix в r2 |
|---|---|---|
| 1 `flag()` == "1" only | BLOCKER | `gate_enabled` на 3 call sites, оба каталога `kind:"gate"`; AC9/AC17/AC20 переписаны (§2.3, §2.4) |
| 2 ENFORCE блокирует все спеки без AC-checks | BLOCKER | легаси-ветка `has_ac_checks_section` до `admit`, AC21 (§2.4) |
| 3 `spec_ac_ids` теряется в whitelist | MAJOR | ids считаются в `_write_validation_doc`, class-I ключ перенесён, AC1 через реальную цепочку (§2.1, §2.6) |
| 4 §2.2 не тотальна | MAJOR | упорядоченные правила, UNKNOWN-строки, 24 ячейки, владелец категории; AC7 (§2.2) |
| 5 Дрейф токенов | MAJOR | `E_SPEC_DEFECT`, `reroute_attempt`, `spec-defect-reroutes-<run_id>.json`; `reject_reason` определён как `gate_reason` + `findings` + payload (§2.1, §2.3) |
| 6 §5 без bd141_p4d:747 | MAJOR | добавлен; описания §5 исправлены (в т.ч. gh634 AC9) |
| 7 Нет ключа `ac_coverage` | MAJOR | = unverifiable, AC22 (§2.1) |
| 8 Второй парсер AC-id | MAJOR | `verdict_verify.parse_spec_ac_ids`, расширение заголовка в одном месте, AC19 |
| 9 Описания §5 неточны | MINOR | исправлены (§5) |
| 10 Нет AC на degrade-ветки | MINOR | AC23, AC24; чтение спеки внутри `try` (`phase_45_spec.py:3173`) |
| 11 Fence #94 | MINOR | отложенные импорты, размещение ниже 6240, удаление `flip-by` комментариев (§2.4, §2.6) |
| 12 Ложный "catalog lint" | MINOR | утверждение удалено, AC17 per-entry |
| 13 Deferred imports / dedupe | MINOR | §3 преамбула; AC16 определяет scope (процесс + run_id + key) |
| Open Q 1-3 | - | D1-D3 в §2.0 |

## §8b Changelog r2 -> r3 (ответ на gate r2: REJECTED)

| Finding | Sev | Fix в r3 |
|---|---|---|
| 1 PARTIAL вне матрицы | MAJOR | правило 2b, fail-closed `markdown_partial`; матрица 32 ячейки; изменение поведения заявлено; AC7, AC28 (§2.2) |
| 2 Легаси-payload ломает GH517A2 `test_ac12` | MAJOR | в payload добавлен `reasons` с "AC-checks section not found"; assert в AC21; §5 уточнён |
| 3 Цитата = любой backtick-токен | MAJOR | токен должен дословно встречаться в `red_test_paths`; `citation_not_found`; деградация `red_files_unreadable`; AC2, AC26 (§2.1) |
| 4 Побочки расширения заголовка | MINOR | двухпроходный поиск, табличный режим без numbered fallback в проходе 2, `phase_6_review` меняется безусловно + parity в AC19, потребитель `verdict_gate` в §5 |
| 5 AC20/AC22 зелёные сегодня | MINOR | объявлены щитами; AC20 через call sites; AC22 с наблюдаемым `gate_reason=="and"`; список shields в §3 |
| 6 Описания §5 | MINOR | gh634 ac9: добавить `setenv`; GH517A2 `ac8` не ломается; gh1018/gh1626d не вызывают гейт |
| 7 Литерал reject-log | MINOR | `VALIDATION_AC_GAP`, assert в AC1/AC25 |
| 8 ac_gap на терминальном пути | MINOR | оба возврата несут поля; AC25 |
| 9 Формулировки | MINOR | AC19 sentinel-monkeypatch; AC16 dedupe `(None, key)`; AC17 фиксированная дата (не CI-bomb); AC27 предусловие фикстуры AC1 |

## §8c Changelog r3 -> r4 (ответ на gate r3: REJECTED; последний раунд, cap 4)

| Finding | Sev | Fix в r4 |
|---|---|---|
| 1 Путь RED-файлов не определён | MAJOR | относительные записи резолвятся через `_resolve_git_cwd(ctx, prev)` (только вызов), абсолютные как есть, cwd процесса не используется; нечитаемо -> `unverifiable`; helper `_read_red_texts`; AC26 (c)-(e) (§2.1) |
| 2 Грамматика цитат только для Python | MAJOR | кандидат = любой backtick-span + голые `test_*`/`Test*`/`test[A-Z]*`; нормализация `path::`, ` > `, `it("…")`; AC2: Go/Swift/TS/legacy bullet |
| 3 Расширение `phase_6_review` меняет hard gate | MAJOR | РЕШЕНИЕ: копия phase 6 НЕ меняется; расширение только в `verdict_verify`; parity-часть AC19 удалена, добавлен pinning AC29 (расхождение намеренное); §2.1/§5/§6 обновлены, fence-bullet |
| 4 Сигнатура и class-I | MINOR | `_forward_map_coverage(validation_raw, ac_ids, red_texts)` (чистая); два новых ключа class-I: `_write_validation_doc::read_text#0`, `_read_red_texts::read_text#0` (полный список) (§2.1, §2.6) |
| 5 AC20 — RED, `gate_reason` на PASS | MINOR | AC20 перенесён в RED, shields обновлены; `gate_reason` на трёх возвратах (PASS, ниже cap, терминальный), AC22 опирается на это |
| 6 Точность матчинга / дедуп / стрелки / нормализация id | MINOR | токен целиком с границами идентификатора, зарезервированные слова, минимальная форма, id AC не цитата; `ac_coverage_unverifiable` ровно раз за вызов (gate эмитит только при отсутствии ключа, `missing_key`); статус Spec Compliance пропускает `: - -> → — – =`; нормализация id для parity снята вместе с parity-части AC19 |
| Wording | - | fence-bullet: `_resolve_git_cwd` определён выше 6240 (`:2063`), только вызывается, не правится |

## §8d Changelog r4 -> r5 (ответ на gate r4: APPROVED_WITH_FIXES; правки только текста, раунда 5 нет)

| Finding | Sev | Fix в r5 |
|---|---|---|
| 1 Нормализация цитат ложно режет частые формы | REQUIRED | §2.1 "Кандидаты в цитату": 4-шаговая нормализация (последний сегмент `::`/`.`, срез `[…]`/`()`, Go subtests); AC2 +6 случаев |
| 2 Область id Forward Map не ограничена | REQUIRED | §2.1: id только из головы bullet / первой ячейки табличной строки; AC2 +3 случая |
| 3 AC26(d) и "cwd не используется" | MINOR | AC26(d) переписан (`current_worktree_path`, whitelist `:6738-6746`); формулировка про cwd как fallback `:2067` |
| 4 Дрейф строк | MINOR | `:3219` (+regex `:3207`), `:2846-2847`, `:3895-3941`/`:3886-3892`, `:587-588`, `:6866-6875`; все проверены по коду |
| 5 Class-I note | MINOR | §2.6 п.1: note про производные AC-id в findings следующего цикла |
| 6 Изоляция directed repair | MINOR | AC12 и AC20: `directed_repair_skip=True` |
| 7 Формулировка §2.4 | MINOR | ENFORCE=0 / легаси-ветка уточнены; `ac_dsl.admit` в потребителях §5 |
| 8 Известный fail-open | MINOR | §2.1: абзац "Известное ограничение" (+ пустой `red_test_paths` -> `unverifiable`) |
| 9 Полнота scope/комментариев | MINOR | §6: `_read_red_texts`, комментарии `verdict_verify.py:18`/`:39`; IGNORECASE во втором проходе |
| 10 Legacy-событие re-poll | MINOR | §2.5: проверка на каждом вызове, оба ключа независимо от `is_frozen` |
