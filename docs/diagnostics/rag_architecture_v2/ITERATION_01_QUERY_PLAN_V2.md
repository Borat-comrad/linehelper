# Итерация 01. QueryPlan v2

## 1. Scope

Итерация отделяет предмет вопроса от типа запрашиваемого факта и временного
scope. Изменены только QueryPlan, Query Analyzer, deterministic validation,
operational boundary, native diagnostics и тестовая инфраструктура.

Не изменялись conversation history, `resolved_question`, retrieval/FTS,
scoring, boosts, deduplication, context selection, evidence gate, answer prompt,
MemoryStore, SQLite schema, chunks, ingestion, organization parser, UI-дизайн и
модель данных ответственности.

Исходная ветка и commit:

| Параметр | Значение |
|---|---|
| Ветка | `refactor/rag-architecture-v2` |
| Исходный commit | `bf29d6e2c5d82ea9cbd6c0d1b77ea54719dca78b` |
| Answer model | `qwen2.5:14b` |
| Analyzer model | `qwen2.5:3b` |
| Python | `3.12.13` |
| Платформа | Windows 11 |

## 2. Исходная проблема

До изменения `subject` и тип факта были неявно смешаны внутри `intent`.
Детерминированный detector считал слова `заказ`, `склад`, `отгрузка`,
`оплата`, `счёт` и похожие достаточным основанием для
`one_c_operational_lookup`.

Поэтому вопрос «Кто отвечает за отгрузку клиенту?» после нахождения корректного
organization evidence переводился в operational intent, а context
обнулялся.

## 3. Текущий механизм до изменения

Фактический порядок до итерации:

```text
LLM intent
→ JSON parsing
→ deterministic _sanitize_plan
→ широкий _is_one_c_operational_lookup_question
→ fallback one_c_operational_lookup
→ final intent
```

В `_sanitize_plan` operational detector вызывался раньше
`_is_roles_responsibility_question`. Вопрос T06 одновременно содержал
конструкцию `кто отвечает` и keyword `отгрузку`; ранний operational branch
завершал validation до проверки ответственности.

Baseline trace T06:

```text
QueryPlan requested_fact_type = not_available
→ intent = one_c_operational_lookup
→ operational_lookup = true (derived from intent)
→ route 594 присутствует в raw retrieval
→ selected context = []
→ response_kind = no_answer
→ status = failed
```

## 4. Новая модель QueryPlan

В `QueryPlan` добавлены поля:

| Поле | Безопасный default | Назначение |
|---|---|---|
| `requested_fact_type` | `unknown` | Какой факт запрошен |
| `temporal_scope` | `unknown` | Временная область факта |
| `subject` | пустая строка | Нормализованный предмет |
| `operational_lookup` | `false` | Native operational decision |
| `operational_decision_reason` | `not_evaluated` | Основание boundary |
| `raw_intent` | `None` | Исходный intent LLM |
| `raw_requested_fact_type` | `None` | Исходный тип факта LLM |
| `raw_temporal_scope` | `None` | Исходный scope LLM |
| `raw_subject` | `None` | Исходный subject LLM |
| `validation_reasons` | `[]` | Причины deterministic correction |

Новые поля добавлены после прежних обязательных полей, поэтому старые
конструкторы `QueryPlan` продолжают работать. Старый JSON без v2-полей
принимается и получает безопасные defaults с последующим deterministic
inference. Fallback также формирует валидный QueryPlan v2.

## 5. Requested fact type

Валидируемый набор:

```text
definition
procedure
responsible_person
primary_contact
unit_head
document_recipient
list
comparison
current_status
current_value
price
availability
unknown
```

Fixture приведён к этому контракту: прежние описательные aliases
`policy_list`, `named_recipient`, `procedure_and_deadline`,
`compound_responsibility`, `normative_rule`, `inventory_status` и
`payment_status` заменены на ближайшие значения enum. Для разговорных
формулировок уточнены различия `primary_contact` и `unit_head`.

Live availability — 32/32, accuracy — 30/32 (93,75%). Два несовпадения:

- T01, второй ход: `unknown` вместо `procedure`, потому что conversation
  resolution вне scope;
- T09: `unknown` вместо `procedure`, поскольку LLM не предложил тип, а
  универсальный procedure-признак в формулировке отсутствует.

## 6. Temporal scope

Валидируемый набор:

```text
static
current
historical
unknown
```

Static fact types получают `static`; current status/value/price/availability
получают `current`. Явные исторические признаки получают `historical`.
Historical не маршрутизируется как current lookup: текущая интеграция 1С не
расширялась.

## 7. Operational boundary

Новый порядок решения:

```text
requested_fact_type
→ temporal_scope
→ subject
→ operational_lookup
→ совместимый final intent
```

Решение:

- `current_status/current_value/price/availability + current` → operational;
- static fact types → semantic/static вне зависимости от subject keywords;
- `historical` → не current operational lookup;
- legacy `one_c_operational_lookup` используется только как compatibility
  fallback, если тип факта действительно не определён.

Live:

| Метрика | Baseline | После |
|---|---:|---:|
| Operational misroutes | 5 | 0 |
| Missed operational | 3 | 0 |
| Operational boundary cases | 0/7 | 7/7 |

## 8. Deterministic validation

LLM теперь предлагает `intent`, `requested_fact_type`, `temporal_scope` и
`subject`, но final decision не доверяет этим значениям без проверки.

Validator:

1. проверяет оба enum;
2. распознаёт универсальные языковые признаки ответственности, процедуры,
   списка, определения и current lookup;
3. исправляет противоречивые type/scope;
4. вычисляет native operational decision;
5. делает final intent совместимым с validated fact type;
6. сохраняет raw-поля и список причин.

Пример фактической нестабильности T06 в трёх повторах:

```text
raw intent: order_disposition / zrs_definition / zrs_definition
raw requested fact: primary_contact / unknown / unknown

validated intent: roles_responsibility / roles_responsibility / roles_responsibility
validated fact: responsible_person / responsible_person / responsible_person
temporal scope: static / static / static
operational lookup: false / false / false
```

## 9. Изменённые файлы

| Файл | Назначение |
|---|---|
| `linehelper/rag/query_analyzer.py` | QueryPlan v2, schema/prompt, parsing, type-first validator и boundary |
| `linehelper/llm/answer_generator.py` | Native QueryPlan diagnostics и использование operational decision |
| `linehelper/cli.py` | Вывод v2 diagnostics в существующем debug-режиме |
| `tests/fixtures/rag_architecture_v2_cases.json` | Enum-normalизация ожидаемых fact types |
| `scripts/rag_architecture_v2_harness.py` | Новые метрики и repeatability dimensions |
| `scripts/run_rag_architecture_v2_baseline.py` | Чтение native diagnostics вместо derived intent |
| `tests/test_query_plan_v2.py` | Unit и deterministic integration pack QueryPlan v2 |
| `tests/test_answer_generator.py` | Обновлённый diagnostics contract |
| `tests/test_rag_architecture_v2_harness.py` | Метрики и runner diagnostics tests |
| `docs/diagnostics/rag_architecture_v2/ITERATION_01_QUERY_PLAN_V2.md` | Настоящий отчёт |

Streamlit-код не менялся: существующий debug block уже выводит весь словарь
`result.query_plan`.

## 10. Unit tests

Новый `tests/test_query_plan_v2.py` содержит 36 collected test items. Из них
34 проверяют QueryPlan/Analyzer/validator без orchestration, а два — реальный
`RagAnswerGenerator` с fake LLM и fake retriever.

Дополнительно добавлены:

- один unit test метрик harness;
- один deterministic runner integration test native diagnostics.

Покрыты enum/defaults, старый JSON, invalid values, prompt schema, fallback,
responsibility, current status, procedure, definition/list, historical scope,
negative keywords и двусторонняя коррекция ошибочного raw plan.

## 11. Integration tests

Проверены три deterministic integration сценария:

1. raw operational plan для T06 исправляется и semantic retrieval сохраняет
   route 594 в context;
2. raw responsibility plan для current status исправляется в operational,
   semantic noise не используется как answer context;
3. architecture runner получает native fact/scope/subject/boundary из
   `RagAnswer`.

Тесты не сравнивают полный текст ответа и не запускают Ollama.

Targeted pack:

```text
222 passed in 0.40s
```

## 12. Architecture target pack

Артефакт:

```text
data/test_runs/rag_architecture_v2/iteration_01/20260728_122513/baseline.json
```

Результат:

```text
12 passed / 20 failed / 0 blocked / 0 not_applicable
```

Baseline был `0/32 passed`. Улучшение относится прежде всего к requested fact
type и operational boundary. Общий strict status не считается чистой метрикой
QueryPlan, потому что включает retrieval, context, clarification и answer
evidence.

Core T01–T09 (T05 разделён на A/B):

| ID | Результат | Состояние исправленного слоя |
|---|---|---|
| T01 | failed | Второй ход не разрешён; вне scope |
| T02 | failed | `list/static/non-operational` корректны; context coverage не исправлялся |
| T03 | failed | `procedure/static/non-operational` корректны; chunk 29 не найден |
| T04 | failed | `document_recipient/static` корректны; partial evidence gap остаётся |
| T05A | failed | `procedure/static/non-operational` корректны; context coverage остаётся |
| T05B | passed | `current_status/current/operational` |
| T06 | passed | `responsible_person/static/non-operational`, route 594 в context |
| T07 | passed | `responsible_person/static/non-operational` |
| T08 | failed | Semantic route корректен; chunks 437/439 не найдены |
| T09 | failed | Non-operational и без false clarification; fact/domain accuracy остаётся |

## 13. T06 trace до/после

До:

```text
raw/fallback QueryPlan
→ requested_fact_type = not_available
→ intent = one_c_operational_lookup
→ operational = true
→ raw retrieval содержит route 594
→ context = []
→ no_answer
```

После:

```text
raw analyzer:
  intent = order_disposition
  requested_fact_type = primary_contact
  temporal_scope = static
  subject = "отгрузка клиенту"
→ deterministic validation:
  reason = explicit_responsibility_question
  requested_fact_type = responsible_person
  temporal_scope = static
  intent = roles_responsibility
→ operational decision:
  false, reason = static_requested_fact_type
→ retrieval:
  route 594 / responsibility_route:logistika_i_sklad_dostavka_klientu:2025-12-17
→ context:
  route 594 включён
→ final behavior:
  response_kind = answer
  status = passed
```

## 14. Парные сценарии

| Пара | Responsibility | Current | Routing |
|---|---|---|---|
| Заказ | PI01 `responsible_person/static` | PI02 `current_status/current` | корректно |
| Отгрузка | PI03 `responsible_person/static` | PI04 `current_status/current` | корректно |
| Склад | PI05 `responsible_person/static` | PI06 `availability/current` | корректно |
| Оплата | PI07 `responsible_person/static` | PI08 `current_status/current` | корректно |

Routing-only pass rate — 8/8 (100%). Полный strict paired rate — 5/8 (62,5%):
три responsibility-сценария всё ещё требуют clarification или иного evidence
поведения, не входящего в scope.

## 15. Повторяемость

Артефакт:

```text
data/test_runs/rag_architecture_v2/iteration_01_repeatability/20260728_130609/baseline.json
```

Результат — 12/12 passed:

| Case | Raw plan | Validated type/scope/boundary | Context IDs | Mode |
|---|---|---|---|---|
| T06 | нестабилен | стабильно 3/3 | стабильно 594, 592 | answer 3/3 |
| PI02 | стабилен | стабильно 3/3 | пустой 3/3 | no_answer 3/3 |
| PI05 | стабилен | стабильно 3/3 | стабильно 474, 475, 466 | answer 3/3 |
| PI06 | стабилен | стабильно 3/3 | пустой 3/3 | no_answer 3/3 |

Поле `all_available_dimensions_stable` у T06 равно `false` только потому, что
оно намеренно включает raw LLM dimensions. Все validated архитектурные
решения, context и mode стабильны.

## 16. Full pytest

Compileall:

```text
success
```

Full pytest:

```text
311 passed in 14.88s
```

Baseline: `273 passed`. Добавлено 38 test items; существующие тесты не
регрессировали. Live Ollama не входит в pytest.

## 17. Organization regression

Команда выполнена в retrieval-only режиме на первых 10 сценариях:

```text
8 PASS / 1 PARTIAL / 1 FAIL / 0 MANUAL_REVIEW
```

Baseline имел тот же результат `8/1/1`; ухудшения нет. Артефакт:

```text
data/test_runs/organization/iteration_01_query_plan_v2/20260728_122513/summary.json
```

## 18. Сравнение с baseline

| Метрика | Baseline | После этапа 1 | Изменение | Объяснение |
|---|---:|---:|---:|---|
| Full pytest | 273 | 311 | +38 | Новый deterministic test pack |
| Architecture strict passed | 0/32 | 12/32 | +12 | Несколько cases полностью прошли после корректного routing |
| Requested fact availability | not_available | 32/32 | +100 п.п. | Native QueryPlan v2 |
| Requested fact accuracy | not_available | 30/32 | новая | 93,75% live |
| Paired strict rate | 0% | 62,5% | +62,5 п.п. | 5/8 полных cases |
| Paired routing rate | not_available | 100% | новая | 8/8 type/boundary |
| Operational misroutes | 5 | 0 | -5 | Static fact type имеет приоритет |
| Missed operational | 3 | 0 | -3 | Current fact/scope распознаны |
| T06 routing | 1С | semantic | исправлено | Route 594 сохраняется в context |
| False clarification | 2 | 0 | -2 | Наблюдаемое изменение; clarification architecture не менялась |
| Missing clarification | baseline не зафиксирован в исходной таблице | 8 | — | Известный отдельный gap |
| Required recall @5/@10 | 72% / 72% | 80% / 80% | +8 п.п. | Live/query-plan variation; retrieval layer не менялся |
| Required chunks in context | 29,17% | 45,83% | +16,66 п.п. | Не приписывается изменению context selection |
| Evidence coverage | not_available | not_available | — | Вне scope |
| Multi-turn resolution | not_available | not_available | — | Вне scope |
| Validated repeat stability | not_available | 12/12 | новая | 4 cases × 3 |
| Organization smoke | 8/1/1 | 8/1/1 | 0 | Retrieval не ухудшен |

Случайные изменения final text, retrieval recall и context rate не считаются
доказательством качества QueryPlan v2.

## 19. Неисправленные сценарии

- T01: conversation history и `resolved_question`;
- T02/DF01: четыре правила не входят в context одновременно;
- T03: chunk 29 не находится;
- T04: gap именованного первоначального адресата недостаточно явно отражён;
- T05A: часть vacation evidence теряется в context;
- T08: structured chunks 437/439 не находятся;
- T09/OR01: raw domain intent и requested fact могут быть неверны;
- PI01/PI03/PI07, CL01/CL02, CV03–CV05: missing clarification;
- CV01/CV02/NG01: relation-aware organization evidence недостаточно.

## 20. Риски и ограничения

1. Deterministic признаки русскоязычные и лексические; полноценной
   морфологической/семантической нормализации subject нет.
2. `subject` нормализует регистр, пробелы и пунктуацию, но не является
   лемматизатором и не должен самостоятельно определять source boundary.
3. Raw LLM intent остаётся нестабильным; T06 демонстрирует это напрямую.
4. Validator корректирует только типовые противоречия fact/scope/boundary и не
   заменяет полноценный domain-intent classifier.
5. Historical безопасно не считается current lookup; историческая интеграция
   1С отсутствует.
6. Clarification architecture намеренно не менялась, поэтому восемь cases
   сохраняют missing clarification.
7. Native evidence decision, merged candidates, unsupported claims,
   `resolved_question` и ambiguity span остаются недоступны.
8. Fixture enum уточнён; baseline runtime artifacts сохранены неизменными и
   используются для сравнения.
9. В рабочем дереве присутствует отдельное изменение
   `docs/obsidian/00_INDEX.md`, появившееся во время live-прогона и не
   относящееся к этой итерации. Оно не должно включаться в будущий commit без
   отдельной проверки владельцем.
10. Ранее существовавшие untracked-файлы Stage 0 не менялись и не входят в
    состав итерации.

## 21. Условия перехода к этапу 2

1. Вручную подтвердить T06 trace и различение четырёх paired groups.
2. Подтвердить, что `requested_fact_type` accuracy 93,75% достаточна для
   фиксации текущего слоя, а T01/T09 остаются target failures.
3. Проверить и отдельно решить судьбу unrelated diff
   `docs/obsidian/00_INDEX.md`.
4. Зафиксировать только перечисленные scoped-файлы отдельным commit.
5. Следующую итерацию ограничить одним новым слоем; не смешивать clarification,
   retrieval/context и evidence fixes.

Предлагаемый commit message:

```text
refactor(rag): разделить предмет вопроса и тип запрашиваемого факта
```
