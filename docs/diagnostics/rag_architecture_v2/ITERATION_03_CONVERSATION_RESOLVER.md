# Итерация 03. Conversation Resolver

## 1. Scope

Итерация добавляет отдельный deterministic conversation-resolution слой перед
Query Analyzer:

```text
original_question + bounded history + structured pending clarification
→ ConversationResolver
→ resolved_question
→ Query Analyzer
→ retrieval
```

Изменены только conversation models, orchestration API, передача session history
из Streamlit и интерактивного CLI, native diagnostics, architecture
runner/harness, fixture и тесты.

Не менялись QueryPlan v2 rules, safe clarification validator, retrieval, FTS,
expansions, scoring, boosts, deduplication, context selection, evidence gate,
answer prompt, MemoryStore, SQLite, ingestion, responsibility resolver, source
authority и vector search.

Исходный commit: `f86ae3c2bf579f466ddc5893a66cdde2001e939b`.
Ветка: `refactor/rag-architecture-v2`. Commit и push в рамках итерации не
выполнялись.

## 2. Исходный механизм

До этапа 3 conversation state существовал только на уровне отображения:

```text
Streamlit st.session_state.messages хранит сообщения
→ _handle_question() вызывает generator.answer(question)
→ RagAnswerGenerator получает только последнюю строку
→ Query Analyzer анализирует только последнюю строку
→ retrieval получает только последнюю строку
```

CLI поддерживал только one-shot `chat question` и не имел session history.
Architecture runner последовательно перебирал сообщения multi-turn case, но
каждый пользовательский turn вызывал как независимый `answer(question)`.

Следовательно, сохранённая UI-история не участвовала ни в анализе, ни в
retrieval. Это подтверждено участками вызова, а не предположением.

## 3. Потеря контекста до изменения

Для T01 второй turn обрабатывался как самостоятельный запрос:

```text
Как оформить КП?
→ valid clarification
→ Коммерческое предложение
→ отдельный query без predicate «как оформить»
→ возможный operational route
```

В едином final artifact этапа 2 это давало единственный
`operational_misroute_count=1`. Native `resolved_question` и
`multi_turn_resolution_rate` отсутствовали.

## 4. Модель ConversationContext

Добавлены immutable-модели:

```python
ConversationTurn(
    role="user|assistant",
    content="...",
    metadata={},
)

PendingClarification(
    source_question="Как оформить КП?",
    plan=ClarificationPlan(...),
)

ConversationContext(
    turns=(),
    pending_clarification=None,
    last_completed_subject=None,
)
```

История ограничена шестью последними turns. Pending clarification считается
активным только тогда, когда structured metadata находится у непосредственно
предшествующего assistant-turn. Произвольный текст ассистента не превращается в
conversation state.

## 5. Модель ResolvedQuery

Native `ResolvedQuery` содержит:

```text
original_question
resolved_question
is_follow_up
topic_changed
inherited_slots
resolution_kind
resolution_confidence
resolution_reasons
conversation_history_used
conversation_turns_used
pending_clarification_before
```

Допустимые `resolution_kind`:

```text
standalone
clarification_answer
missing_slot_answer
explicit_follow_up
topic_change
unresolved_follow_up
```

В текущей итерации `explicit_follow_up` зарезервирован моделью. Универсальное
местоименное разрешение намеренно не реализовано.

## 6. Structured clarification resolution

Для abbreviation/lexical ambiguity resolver:

1. получает `ambiguity_span` и `candidate_meanings` из validated
   `ClarificationPlan`;
2. сопоставляет ответ пользователя только с допустимым значением;
3. заменяет только неоднозначный span в исходном вопросе;
4. сохраняет predicate и остальную формулировку;
5. записывает slot вида `meaning_of_КП`.

Пример:

```text
Как оформить КП?
→ Коммерческое предложение
→ Как оформить коммерческое предложение?
```

Ответы сопоставляются регистронезависимо; поддерживаются безопасные сокращённые
формы кандидатов. Произвольное сообщение не принимается как значение.

## 7. Missing-slot resolution

Поддержаны проверяемые structured slots:

| Pending slot | Исходный вопрос | Ответ | Resolved question |
|---|---|---|---|
| `document_type` | Кому отдать документы? | Кадровые | Кому отдать кадровые документы? |
| `application_type` | Куда направить заявление? | На командировку | Куда направить заявление на командировку? |
| `subject` | Кто этим занимается? | Внутренним документооборотом | Кто занимается внутренним документооборотом? |

Композиция использует тип slot и исходный predicate. Значения `не знаю`,
`потом`, `неважно` и аналогичные не заполняют slot и не порождают выдуманный
resolved question.

## 8. Topic change

Явный новый predicate и конкретный новый subject имеют приоритет над pending
clarification:

```text
Как оформить КП?
→ Кто отвечает за таможню?
→ topic_changed=true
→ resolved_question совпадает с новым вопросом
→ inherited_slots=[]
```

Topic-change detector использует универсальные вопросительные конструкции, а
не список полных вопросов. В полном pack `topic_change_accuracy=13/13`; в эту
метрику входят также ожидаемые отрицательные значения.

## 9. Pending clarification lifecycle

Lifecycle:

```text
validated clarification
→ assistant metadata.pending_clarification
→ доступно только следующему user-turn
→ успешное заполнение slot или topic change
→ pending_clarification_after=null
```

При невалидном slot answer возвращается
`resolution_kind=unresolved_follow_up`; существующее предметное уточнение
повторяется, retrieval не запускается, значение не выдумывается. Pending state
сохраняется только для следующей попытки.

Новый диалог, успешное разрешение, явная смена темы и выход pending за
шестиходовое окно исключают повторное применение. Full pack:

```text
stale_context_leak_count=0
repeated_clarification_after_resolution=0
```

## 10. API RagAnswerGenerator

API расширен обратно совместимо:

```python
RagAnswerGenerator.answer(
    question,
    *,
    history=None,
    conversation_context=None,
    ...
)
```

`history=None` сохраняет старое standalone-поведение. После resolution одна и
та же `resolved_question` передаётся Query Analyzer, runtime intent и retriever.
`RagAnswer.question` остаётся исходной пользовательской строкой;
`RagAnswer.resolved_question` и `RagAnswer.conversation` содержат техническую
диагностику.

`rag_answer_history_metadata()` формирует компактное structured state для
следующего turn без копирования всего diagnostic result.

## 11. Streamlit integration

Streamlit:

- берёт snapshot history до текущего вызова `answer()`;
- не дублирует текущее user message;
- после успешного вызова сохраняет user и assistant turn вместе со structured
  metadata;
- не добавляет незавершённый exchange при ошибке генератора;
- очищает messages и pending state через «Новый диалог»;
- продолжает отображать исходный пользовательский текст;
- показывает resolved/conversation diagnostics только в технической панели.

Подготовка history, assistant message и reset вынесены в тестируемые helpers.
Существующий UI-дизайн и прочие Streamlit widgets не перерабатывались.

## 12. CLI integration

One-shot остаётся совместимым:

```text
linehelper chat "Кто отвечает за отгрузку?"
```

Добавлен opt-in interactive режим:

```text
linehelper chat --interactive
```

Он использует `ConversationSession`, сохраняет порядок turns и structured
clarification metadata в памяти процесса, поддерживает `/new` и `/exit`.
Между независимыми CLI-запусками история не сохраняется. Debug output включает
conversation diagnostics.

## 13. Diagnostics

В `RagAnswer`, CLI debug, Streamlit debug и architecture JSON доступны:

```text
original_question
resolved_question
conversation_history_used
conversation_turns_used
is_follow_up
topic_changed
resolution_kind
inherited_slots
resolution_confidence
resolution_reasons
pending_clarification_before
pending_clarification_after
```

Observability evidence plan, rejected chunks, source authority decision и
unsupported claims не добавлялась.

## 14. Изменённые файлы

| Файл | Назначение этапа 3 |
|---|---|
| `linehelper/rag/conversation_resolver.py` | новые conversation models, deterministic resolver, serialization и in-memory session |
| `linehelper/llm/answer_generator.py` | backward-compatible API, resolution перед analyzer/retrieval, lifecycle и native diagnostics |
| `linehelper/ui/streamlit_app.py` | bounded history и structured metadata в session state, reset helper |
| `linehelper/cli.py` | one-shot compatibility, interactive session и debug diagnostics |
| `scripts/rag_architecture_v2_harness.py` | conversation invariants, metrics и repeatability dimensions |
| `scripts/run_rag_architecture_v2_baseline.py` | реальное последовательное выполнение multi-turn cases и JSON diagnostics |
| `tests/fixtures/rag_architecture_v2_cases.json` | T01 expectations и CR01–CR12 |
| `tests/test_conversation_resolver.py` | deterministic resolver unit tests |
| `tests/test_conversation_integration.py` | orchestration, UI/CLI history helper tests |
| `tests/test_rag_architecture_v2_harness.py` | schema, metrics, repeatability и multi-turn runner tests |
| `docs/diagnostics/rag_architecture_v2/ITERATION_03_CONVERSATION_RESOLVER.md` | постоянный отчёт итерации |

## 15. Unit tests

Добавлено 26 unit/infrastructure test items:

- 23 parametrized items для `ConversationResolver`;
- 3 harness items для schema validation, conversation metrics и repeatability.

Покрыты standalone-вопросы, два значения КП, missing document/application/subject,
topic change, invalid answer, no history, stale state, history limit,
serialization, explicit context и in-memory session.

## 16. Integration tests

Добавлено 12 deterministic integration items:

- 11 orchestration/UI/CLI items в `tests/test_conversation_integration.py`;
- 1 тест architecture runner, доказывающий последовательное выполнение
  multi-turn case с history.

Fake analyzer/retriever подтверждают, что Query Analyzer и retriever получают
одинаковую resolved question, повторное КП/ЦКП clarification не возникает,
operational route не срабатывает и старый `answer(question)` остаётся рабочим.
Тесты не зависят от полного текста LLM-ответа.

Targeted final suite:

```text
322 passed in 1.04s
```

## 17. Architecture target pack

Единый полный artifact на окончательном коде и fixture:

```text
data/test_runs/rag_architecture_v2/iteration_03_final/20260729_125515/baseline.json
63 unique cases
36 passed / 27 failed / 0 blocked
```

Срез прежних 51 cases из тех же записей:

```text
29 passed / 22 failed / 0 blocked
```

Срез исходных 32 cases:

```text
22 passed / 10 failed / 0 blocked
```

Ключевые метрики:

| Метрика | Этап 3 |
|---|---:|
| False clarification | 0 |
| Missing clarification | 0 |
| Valid clarification recall | 12/12 (100%) |
| Missing-slot clarification accuracy | 7/7 (100%) |
| Operational misroutes | 0 |
| Missed operational | 0 |
| Requested fact accuracy, 63 | 47/63 (74,60%) |
| Requested fact accuracy, прежние 51 | 39/51 (76,47%) |
| Requested fact accuracy, исходные 32 | 31/32 (96,88%) |
| Paired responsibility/status | 8/8 (100%) |
| Required chunk recall @5 / @10 | 25/30 / 25/30 (83,33% / 83,33%) |
| Required chunks in context | 16/29 (55,17%) |
| Evidence coverage | `not_available` |

Strict score измеряет весь pipeline. Conversation-layer измеряется отдельно:

| Conversation metric | Результат |
|---|---:|
| Multi-turn resolution | 13/13 (100%) |
| Resolved question availability | 63/63 (100%) |
| Resolved question accuracy | 13/13 (100%) |
| Topic-change accuracy | 13/13 (100%) |
| Slot-inheritance accuracy | 13/13 (100%) |
| History used when required | 11/11 (100%) |
| History not used when not required | 2/2 (100%) |
| Stale context leaks | 0 |
| Repeated clarification after resolution | 0 |
| Conversation strict cases | 10/15 |

Пять conversation strict failures относятся к downstream классификации:

- CR02 — live intent один раз `task_management`, хотя validated
  `document_recipient` и resolution верны;
- CR03 — `procedure` вместо ожидаемого `document_recipient`;
- CR09 — `definition` вместо `list`;
- CR10 — `procedure` вместо `document_recipient`;
- CR11 — `primary_contact` вместо `responsible_person`.

Ожидания не ослаблялись ради strict score.

## 18. T01 trace до/после

До этапа 3:

```text
Коммерческое предложение
→ standalone
→ predicate «как оформить» потерян
→ operational misroute
```

После этапа 3, final live artifact:

```text
turn 1 original_question = "Как оформить КП?"
→ validated clarification.kind = abbreviation
→ ambiguity_span = "КП"
→ retrieval_started = false
→ pending clarification сохранён

turn 2 original_question = "Коммерческое предложение"
→ pending clarification найден
→ resolution_kind = clarification_answer
→ inherited_slots = ["meaning_of_КП"]
→ resolved_question = "Как оформить коммерческое предложение?"
→ requested_fact_type = procedure
→ temporal_scope = static
→ clarification_action = continue_retrieval
→ operational_lookup = false
→ retrieval_started = true
→ pending clarification очищен
→ response_kind = partial_answer
```

T01 теперь strict passed. Отсутствующая инструкция не выдумывается: текущий
downstream возвращает частичный/knowledge-gap ответ без evidence context.

## 19. Conversation cases

Fixture расширен с 51 до 63 cases:

| ID | Сценарий | Conversation result | Strict |
|---|---|---|---|
| CR01 | КП → коммерческое предложение | `clarification_answer`, resolved верен | passed |
| CR02 | документы → кадровые | `missing_slot_answer`, `document_type` | failed downstream intent |
| CR03 | заявление → на командировку | `missing_slot_answer`, `application_type` | failed downstream fact type |
| CR04 | этим → внутренним документооборотом | `missing_slot_answer`, `subject` | passed |
| CR05 | КП → кто отвечает за таможню | `topic_change`, slots очищены | passed |
| CR06 | «Коммерческое предложение» без history | `standalone`, predicate не выдуман | passed |
| CR07 | успешное КП → новый вопрос | stale state не применён | passed |
| CR08 | документы → «Не знаю» | `unresolved_follow_up`, clarification сохранён | passed |
| CR09 | КП → ценный конечный продукт | span заменён | failed downstream fact type |
| CR10 | документы → бухгалтерские | `document_type` заполнен | failed downstream fact type |
| CR11 | заявление → на отпуск | `application_type` заполнен | failed downstream fact type |
| CR12 | этим → рабочими местами | `subject` заполнен | passed |

Вместе с T01 и ранее существующими conversation-group cases общий pack содержит
15 conversation cases, из которых 10 strict passed. Все 13 cases с точными
conversation expectations прошли native invariants.

## 20. Повторяемость

Artifact:

```text
data/test_runs/rag_architecture_v2/iteration_03_repeatability/20260729_140646/baseline.json
4 cases × 3 repeats = 12 evaluations
8 strict passed / 4 failed / 0 blocked
```

| Диалог | Resolution | Fact / operational | Retrieval / mode | Стабильность |
|---|---|---|---|---|
| КП → коммерческое предложение | `clarification_answer`; `meaning_of_КП`; topic=false | `procedure`; false | true; `partial_answer` | 3/3 |
| документы → кадровые | `missing_slot_answer`; `document_type`; topic=false | `document_recipient`; false | true; `answer` | 3/3 |
| КП → кто отвечает за таможню | `topic_change`; slots=[]; topic=true | `responsible_person`; false | true; `answer` | 3/3 |
| заявление → на командировку | `missing_slot_answer`; `application_type`; topic=false | `procedure`; false | true; `answer` | 3/3 |

Resolved question, inherited slots, topic flag, requested fact, operational
decision, clarification action, retrieval_started, context IDs и response mode
стабильны во всех повторах.

Raw LLM plan менялся в CR02:

```text
raw intent: task_management / roles_responsibility / roles_responsibility
```

Deterministic validation сохранила `document_recipient` и не изменила
conversation/routing decision. В первых turns raw analyzer не запрашивал
уточнение, но deterministic safe clarification стабильно создавал abbreviation
или missing-slot plan; во вторых turns validated action стабильно равен
`continue_retrieval`.

## 21. Full pytest

Compileall:

```text
.\.venv\Scripts\python.exe -m compileall linehelper tests scripts
success
```

Full pytest:

```text
.\.venv\Scripts\python.exe -m pytest tests scripts\tests \
  --basetemp .\.venv\pytest-tmp-rag-v2-iteration-03-final \
  -p no:cacheprovider

407 passed in 14.74s
```

По сравнению с этапом 2 добавлено 38 test items: 26
unit/infrastructure и 12 deterministic integration.

## 22. Organization regression

Использован тот же retrieval-only режим и первые 10 вопросов:

```text
data/test_runs/organization/iteration_03_conversation_resolver/20260729_114113/summary.json
8 PASS / 1 PARTIAL / 1 FAIL
runtime_errors = 0
```

Результат полностью совпадает с этапами 0–2. Organization retrieval не
ухудшился.

## 23. Сравнение с предыдущими этапами

| Метрика | Этап 0 | Этап 1–2 | Этап 3 | Изменение этапа 3 |
|---|---:|---:|---:|---:|
| Исходные 32 cases | 0/32 | 21/32 | 22/32 | +1, T01 |
| Прежние 51 cases | — | 28/51 | 29/51 | +1, T01 |
| Полный pack | 0/32 | 28/51 | 36/63 | +12 CR cases |
| False clarification | 2 | 0 | 0 | без ухудшения |
| Missing clarification | not_available | 0 | 0 | без ухудшения |
| Valid clarification recall | not_available | 11/11 | 12/12 | +1 проверяемый case |
| Missing-slot clarification | not_available | 6/6 | 7/7 | +1 проверяемый case |
| Operational misroutes | 5 | 1 | 0 | -1, T01 |
| Missed operational | 3 | 0 | 0 | без ухудшения |
| Multi-turn resolution | not_available | not_available | 13/13 | native |
| Resolved question availability | not_available | not_available | 63/63 | native |
| Resolved question accuracy | not_available | not_available | 13/13 | native |
| Topic-change accuracy | not_available | not_available | 13/13 | native |
| Slot-inheritance accuracy | not_available | not_available | 13/13 | native |
| Stale context leaks | not_available | not_available | 0 | native |
| Requested fact accuracy, 32 | not_available | 30/32 | 31/32 | T01 resolved |
| Requested fact accuracy, 51 | not_available | 38/51 | 39/51 | T01 resolved |
| Paired routing | not_available | 8/8 | 8/8 | без ухудшения |
| Required recall @5/@10 | 72% / 72% | 83,33% / 83,33% | 83,33% / 83,33% | retrieval не менялся |
| Required chunks in context | 29,17% | 55,17% | 55,17% | context не менялся |
| Full pytest | 273 | 369 | 407 | +38 |
| Organization smoke | 8/1/1 | 8/1/1 | 8/1/1 | без ухудшения |

Случайные изменения final answer не считаются результатом этапа. Целевое
улучшение — восстановленный predicate, native resolution и устранённый
operational misroute T01.

## 24. Неисправленные T01–T09

| ID | Итог этапа 3 | Причина вне conversation scope |
|---|---|---|
| T01 | passed | — |
| T02 | failed | chunks 71, 73, 74 не входят в context |
| T03 | failed | chunk 29/нужный source не найден; generic no_answer |
| T04 | failed | answer/evidence plan не фиксирует отсутствие именованного адресата |
| T05A | failed | chunk 27/правило 14 дней не входит в context/answer |
| T05B | passed | — |
| T06 | passed | — |
| T07 | passed | — |
| T08 | failed | structured records 437/439 не найдены/не вошли в context |
| T09 | layer passed, strict failed | `requested_fact_type=unknown`, ожидается `procedure` |

Состояние T02, T03, T04, T05A, T08 и T09 не маскировалось изменениями
conversation expectations.

## 25. Риски и ограничения

1. Resolver детерминирован и намеренно ограничен structured clarification;
   общего entity/pronoun resolver пока нет.
2. Topic-change detector покрывает высокоуверенные русские вопросительные
   конструкции, но не все разговорные переходы.
3. Pending clarification действует только для следующего turn и в окне шести
   сообщений; длинные отложенные ответы считаются standalone.
4. CLI history хранится только в памяти процесса, Streamlit — только в текущей
   session state; persistence не добавлялась.
5. CR02 показывает nondeterminism raw LLM intent. Validated conversation и
   routing стабильны, но downstream final intent может различаться.
6. CR03, CR09–CR11 фиксируют неточности QueryPlan v2 для resolved
   формулировок; их нельзя исправлять в conversation layer.
7. Для standalone «Коммерческое предложение» без history выбран безопасный
   `definition/static`: resolver не придумывает predicate «как оформить».
8. Retrieval/context/evidence gaps T02, T03, T04, T05A и T08 остаются.
9. Evidence coverage и unsupported claims по-прежнему `not_available`.
10. Live pack длителен и зависит от локальной Ollama; deterministic pytest не
    запускает реальную модель.

## 26. Условия перехода к этапу 4

Переход допустим после ручной проверки:

1. воспроизводятся `407 passed`;
2. единый artifact содержит `36/63`, legacy-срез `29/51`, исходный срез
   `22/32`;
3. T01 strict passed и имеет ожидаемый resolved-question trace;
4. native conversation metrics равны 13/13, stale leaks и repeated
   clarification равны нулю;
5. operational misroutes равны нулю;
6. repeatability подтверждает стабильность 4/4 validated conversation
   decisions;
7. organization smoke остаётся `8 PASS / 1 PARTIAL / 1 FAIL`;
8. изменения retrieval/context/evidence слоёв отсутствуют;
9. этап 3 зафиксирован отдельным ручным commit.

Предлагаемый commit message:

```text
refactor(rag): добавить ConversationResolver и resolved question
```
