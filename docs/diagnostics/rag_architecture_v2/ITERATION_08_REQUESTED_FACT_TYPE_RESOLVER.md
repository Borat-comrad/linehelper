# Итерация 08. Requested Fact Type Resolver

## 1. Исходная причина T09

До изменения использован последний полный architecture artifact, содержащий
T09:

```text
data/test_runs/rag_architecture_v2/iteration_05/
  20260730_113922/baseline.json
```

Подтверждённый trace:

```text
question: Можно ли дать распоряжение устно?
raw intent: order_disposition
raw requested_fact_type: unknown
answer_type: no_answer
temporal_scope: static
subject: распоряжения компании
clarification: false / continue_retrieval
operational lookup: false
required policy chunk 67: найден и вошёл в context
strict failure: expected procedure, got unknown
```

Причина находилась до retrieval. Старый structural inference распознавал
формы `как оформить`, `что делать`, ответственность, список и operational
status, но не нормативную конструкцию `можно ли + действие`. Fallback по
`answer_type` также не сработал, потому что analyzer вернул `no_answer`.
Нормализация не теряла слова, конфликта типов не было.

## 2. Место resolver в pipeline

```text
QueryAnalyzer draft
→ QueryPlan validation
→ RequestedFactTypeResolver
→ finalized QueryPlan
→ Safe Clarification
→ retrieval и остальные существующие слои
```

Resolver не выполняет retrieval и не вызывает LLM.

## 3. Модель resolution

`RequestedFactTypeResolution` содержит:

- `initial_fact_type`;
- `resolved_fact_type`;
- `resolution_status`;
- `matched_signals`;
- `rejected_candidates`;
- `decision_reasons`.

Допустимые статусы:

- `unchanged_explicit`;
- `resolved_from_structure`;
- `resolved_from_intent`;
- `ambiguous`;
- `insufficient_signals`.

Существующее `requested_fact_type` остаётся backward-compatible финальным
типом. Initial/raw предложение analyzer сохраняется отдельно.

## 4. Порядок правил

1. Собираются сильные структурные признаки формы вопроса.
2. Несколько несовместимых типов дают `ambiguous` и сохраняют final
   `unknown`.
3. Совместимый explicit type сохраняется как `unchanged_explicit`.
4. Один сильный structural signal может исправить unknown или противоречивое
   предложение analyzer.
5. При отсутствии structural signal сохраняется допустимый explicit type.
6. Только для оставшегося unknown применяются ограниченные intent/answer-shape
   mappings.
7. При недостатке признаков сохраняется `unknown`.

Resolver использует существующий enum. Новых requested fact types не
добавлено.

Нормативное правило является общим:

```text
можно ли / допустимо ли / разрешено ли / нужно ли / обязательно ли
+
непустой subject
+
procedure/policy intent
→ procedure
```

В production-правилах нет case ID, chunk ID, полного текста T09 или названия
конкретного документа.

## 5. Поведение при конфликте

Пример `Кто отвечает и какой статус заказа?` одновременно даёт
`responsible_person` и `current_status`.

Результат:

```text
resolved_fact_type: unknown
resolution_status: ambiguous
rejected_candidates:
  - responsible_person
  - current_status
```

Resolver не выбирает произвольный тип и не создаёт собственный clarification
механизм.

## 6. T09 до/после

| Поле | До | После |
| --- | --- | --- |
| Initial type | `unknown` | `unknown` |
| Finalized type | отсутствовал / `unknown` | `procedure` |
| Resolution status | отсутствовал | `resolved_from_structure` |
| Matched signals | отсутствовали | `question_pattern:normative_action` |
| Decision reason | отсутствовала | `normative_action_question` |
| Clarification | false | false |
| Operational lookup | false | false |
| Strict status | failed | passed |

После resolver `RetrievalPlan.requested_fact_type=procedure`, выполняется
procedure stage, policy chunk 67 остаётся в context/evidence, а grounded answer
сообщает, что распоряжение должно быть письменным и устное следует оформить
письменно при первой возможности.

## 7. Регрессии

Единый targeted live artifact:

| Case | Initial | Finalized | Status resolution | Strict |
| --- | --- | --- | --- | --- |
| T02 | `unknown` | `list` | `resolved_from_structure` | passed |
| T03 | `procedure` | `procedure` | `unchanged_explicit` | passed |
| T04 | `primary_contact` | `document_recipient` | `resolved_from_structure` | passed |
| T05A | `unknown` | `procedure` | `resolved_from_structure` | passed |
| T06 | `responsible_person` | `responsible_person` | `unchanged_explicit` | passed |
| T08 | `responsible_person` | `responsible_person` | `unchanged_explicit` | passed |
| T09 | `unknown` | `procedure` | `resolved_from_structure` | passed |
| OR01 | `unknown` | `procedure` | `resolved_from_structure` | passed |

Итоговые regression-инварианты:

- false clarification: 0;
- missing clarification: 0;
- operational misroutes: 0;
- missed operational: 0;
- requested fact accuracy: 8/8;
- required candidates/context: 13/13;
- source attribution и answer contract не изменялись.

## 8. Diagnostics

В QueryPlan, CLI debug и architecture artifact добавлены:

- `initial_requested_fact_type`;
- `finalized_requested_fact_type`;
- `fact_type_resolution`;
- `resolution_status`;
- `matched_signals`;
- `rejected_fact_types`;
- fact-type `decision_reasons`.

Diagnostics содержат только структурированные результаты правил.

## 9. Тесты и budget

- новых unit test items: 6;
- новых deterministic integration test items: 3;
- новых fixture cases: 0.

Unit coverage:

1. explicit type сохраняется;
2. unknown разрешается по структуре;
3. unknown разрешается по intent;
4. конфликт остаётся ambiguous;
5. недостаточные сигналы сохраняют unknown;
6. diagnostics детерминированы и JSON-сериализуемы.

Integration coverage:

1. T09 finalization до retrieval;
2. агрегированная стабильность T02/T03/T05A/T06/T08;
3. T04/OR01, safe clarification и multi-turn compatibility.

Targeted tests:

```text
resolver + QueryPlan + QueryAnalyzer: 162 passed
clarification + conversation + answer/architecture compatibility: 184 passed
```

Test budget соблюдён.

## 10. Compileall и full pytest

```text
python -m compileall linehelper tests scripts
result: passed

pytest tests scripts/tests
result: 495 passed in 20.37s
```

## 11. Targeted live artifact

Единственный live-run:

```text
data/test_runs/rag_architecture_v2/iteration_08_targeted/
  20260731_120115/baseline.json
```

Результат: **8 passed / 0 failed / 0 blocked**.

Полный 71-case pack, repeatability reruns и organization pack не запускались.

## 12. Ограничения

- Resolver является компактным русскоязычным rule-based MVP, а не
  универсальным semantic parser.
- Entities принимаются API resolver, но текущий QueryPlan не предоставляет
  отдельное native entities-поле, поэтому на этой итерации передаётся пустая
  последовательность.
- `ambiguous` не создаёт новый clarification сам: он оставляет `unknown` для
  существующего безопасного pipeline.
- Расширение intent mappings требует отдельных regression cases, чтобы не
  превращать unknown в общий default.
- QueryPlan prompt, clarification policy, conversation, retrieval, context,
  evidence и grounded renderer не изменялись.
- Дополнительный LLM-вызов не добавлен.
