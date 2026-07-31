# Итерация 05. Context Composer

## Что было сломано

До этапа 5 `RagAnswerGenerator` выбирал context через
`select_context_chunks`: preferred-context gate, фильтрация по anchor terms,
score ratio и жёсткий `context_limit=3`.

Фактические причины:

- T02: четыре sibling chunks ИП-0006 находились, но `[:3]` отбрасывал четвёртое
  правило.
- T03: procedural chunk 29 находился на rank 3, однако intent
  `equipment_it_request` требовал preferred context, а preferred terms для него
  отсутствовали. Selector возвращал пустой список до evidence/answer слоя.
- отдельного budget по размеру на стадии selection не было; лимит 8000 символов
  применялся позднее внутри prompt builder.

## Что изменено

Добавлен отдельный `ContextPlanner` и детерминированный `ContextComposer`.
Существующий selector сохранён как совместимый источник безопасного base context
для responsibility/default и уже работающих procedure-сценариев.

Новый путь:

```text
retrieval candidates
→ legacy safe base selection
→ ContextPlan
→ coverage-first ContextComposer
→ bounded selected context
→ существующий context sufficiency/evidence/answer слой
```

Composer:

- сохраняет валидный base context;
- для list-вопросов добирает разные sibling logical units;
- для procedure-вопросов при пустом или неполном base context добирает
  различные procedural logical units без score-ratio cutoff;
- не занимает budget повторами одного coverage key;
- не меняет retrieval score или порядок candidates;
- сериализует причины выбора и исключения.

Изменены:

| Файл | Назначение |
| --- | --- |
| `linehelper/rag/context_composer.py` | модели плана/budget и coverage-aware composer |
| `linehelper/llm/answer_generator.py` | включение composer между retrieval и существующим context gate |
| `linehelper/cli.py` | debug-представление context plan, coverage и размера |
| `scripts/run_rag_architecture_v2_baseline.py` | native context diagnostics в JSON artifact |
| `tests/test_context_composer.py` | восемь unit test items |
| `tests/test_context_composer_integration.py` | три deterministic integration test items |

Retrieval, QueryPlan, clarification, conversation resolution, MemoryStore,
evidence gate и answer prompt не менялись.

## Модель ContextPlan

`ContextPlan` содержит:

- `requested_fact_type`;
- ограниченный `answer_shape`;
- `ContextRequirement[]`;
- `ContextBudget`;
- исходный `score_ratio` для diagnostics.

Поддерживаются формы:

```text
single_fact
procedure
list
responsibility
definition
comparison
default
```

Основные requirements:

- `distinct_sibling_logical_units`;
- `primary_procedure`;
- `primary_responsibility`;
- `primary_fact`.

## Правила coverage

List:

- релевантный seed берётся из safe base selection;
- sibling relation подтверждается retrieval provenance;
- coverage считается по устойчивому сочетанию `record_key` либо
  `source + logical_unit_type + logical_unit_title`;
- разные части одного правила не вытесняют разные правила.

Procedure:

- уже валидный base context сохраняется;
- при пустом base context выбираются разные procedural logical units в
  существующем candidate order;
- `procedure_lookup` provenance и `logical_unit_type=procedure` являются
  общими признаками, без правил по полному тексту вопроса или chunk ID.

Responsibility/default:

- сохраняется прежний осторожный selection и ordering;
- composer только применяет общий budget и diagnostics.

## Budget

По умолчанию:

- максимум 8000 оценочных символов context;
- абсолютный максимум 6 chunks;
- обычный план сохраняет настроенный limit 3;
- list-план может расшириться до 4 chunks для sibling coverage.

Оценка длинных chunks согласована с excerpt-поведением prompt builder.
Сначала помещается required coverage, затем supporting context. В финальном
architecture artifact:

- максимум: 4 chunks и 5585 оценочных символов;
- медиана: 3 chunks и 1891 символ;
- budget 8000 символов не превышен.

## T02

До:

```text
candidates: 72, 71, 73, 74
context: 72, 71, 73
```

После:

```text
answer_shape=list
coverage=distinct_sibling_logical_units
context: 72, 71, 73, 74
context_size: 4 chunks / 967 chars
status: passed
```

Все 4/4 правила найдены и вошли в context.

## T03

До:

```text
chunk 29: candidate rank 3
preferred-context gate: no preferred terms
context: []
response: generic no-answer
```

После:

```text
answer_shape=procedure
base context: empty
composer: distinct procedure coverage
context: 44, 29, 39
context_size: 3 chunks / 5181 chars
response mode: answer
status: passed
```

Required procedural chunk 29 входит в context. При этом соседние procedural
candidates 44 и 39 остаются шумом: их релевантность должен оценивать следующий
evidence слой, а не ContextComposer.

## T05A

Финальный context:

```text
42 — основная инструкция оформления отпуска
43 — продолжение инструкции
27 — правило подачи за 14 календарных дней
```

Размер: 3 chunks / 4732 chars. Required chunks 42 и 27 присутствуют, понятие
14 дней использовано, strict status — `passed`.

## Регрессии T06/T08

- T06: structured route 594 сохранён первым; 3 chunks / 1891 chars; `passed`.
- T08: structured records 437 и 439 сохранены; 3 chunks / 2083 chars;
  `passed`.
- Organization smoke: `8 PASS / 1 PARTIAL / 1 FAIL`, без ухудшения.

Первый полный run выявил побочную потерю policy rule 67 в OR01: ранняя версия
composer заменяла валидный base context только procedural logical units.
Исправление сохраняет base context и лишь добирает coverage в свободный budget.
Финальный OR01 context `70, 69, 67`, status `passed`.

## Тесты

Test budget соблюдён:

- 8 unit test items;
- 3 deterministic integration test items;
- fixture cases не добавлялись.

Unit coverage:

1. четыре разных sibling logical units;
2. procedure candidate ниже ratio;
3. character budget;
4. duplicate coverage;
5. structured responsibility;
6. пустые candidates;
7. deterministic ordering;
8. selection/exclusion diagnostics.

Integration coverage:

1. T02: 4/4 rules в orchestration context;
2. T03: lower-rank procedure проходит прежний preferred-context gate;
3. объединённая проверка T05A, T06, T08 и OR01 без увеличения числа test items.

Финальный targeted run:

```text
274 passed in 1.93s
```

## Full pytest

```text
compileall linehelper tests scripts: success
full pytest: 467 passed in 22.62s
```

## Architecture pack

Финальный единый artifact:

```text
data/test_runs/rag_architecture_v2/iteration_05/20260730_113922/baseline.json
47 passed / 24 failed / 0 blocked
```

Сравнение с этапом 4:

| Метрика | Этап 4 | Этап 5 | Изменение |
| --- | ---: | ---: | ---: |
| Strict architecture | 43/71 passed | 47/71 passed | +4, включая повторно доступный PR01 |
| Required recall @5 | 38/40 — 95,00% | 39/41 — 95,12% | без изменения retrieval-слоя |
| Required recall @10 | 38/40 — 95,00% | 39/41 — 95,12% | без изменения retrieval-слоя |
| Required chunks in context | 19/28 — 67,86% | 23/29 — 79,31% | +11,45 п.п. |
| Operational misroutes | 0 | 0 | без регрессии |
| False clarification | 0 | 0 | без регрессии |
| Multi-turn resolution | 13/13 | 13/13 | без регрессии |

Strict improvements, относящиеся к изменённому слою: T02, T03 и DF01.
PR01 был blocked в этапе 4 из-за Ollama timeout и прошёл в финальном run; это не
результат ContextComposer.

Отдельный live target artifact:

```text
data/test_runs/rag_architecture_v2/iteration_05_targets/20260730_094405/baseline.json
T02/T03/T05A/T06/T08: 5 passed / 0 failed / 0 blocked
required context: 10/10
```

Organization smoke:

```text
data/test_runs/organization/iteration_05_context_composer/
20260730_132342/summary.json
8 PASS / 1 PARTIAL / 1 FAIL
```

## Ограничения

- Composer повышает coverage, но не доказывает достаточность каждого chunk для
  конкретного утверждения.
- В T03 context вместе с required chunk 29 содержит procedural noise 44 и 39.
  Это подтверждает необходимость следующего evidence/partial-answer слоя.
- Character estimate не является точным tokenizer-based budget.
- Абсолютный максимум 6 chunks и 8000 символов может ограничить большие списки.
- Required candidate recall остаётся 39/41; два отсутствующих candidates нельзя
  исправлять context selection.
- Core T04 остаётся failed из-за partial-answer/evidence policy.
- Core T09 остаётся failed из-за `requested_fact_type=unknown`; QueryPlan вне
  scope этапа 5.
- Остальные 24 target failures не маскируются изменением expected values.
