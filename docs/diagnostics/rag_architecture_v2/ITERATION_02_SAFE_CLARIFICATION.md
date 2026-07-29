# Итерация 02. Safe Clarification

## 1. Scope

Итерация меняет только слой принятия решения об уточнении:

- structured clarification в `QueryPlan`;
- parsing и deterministic validation;
- раннюю ветку `clarify / continue_retrieval`;
- native diagnostics, CLI и architecture harness;
- test fixture, unit/integration tests и диагностические runners.

Conversation history, `resolved_question`, retrieval, FTS, scoring, boosts,
context selection, evidence gate, answer prompt, MemoryStore, SQLite, chunks,
ingestion и responsibility resolver не менялись.

Исходный commit: `bf29d6e2c5d82ea9cbd6c0d1b77ea54719dca78b`.
Этап 1 находился в незакоммиченном рабочем дереве; этап 2 выполнен поверх него.
Commit и push не выполнялись.

## 2. Исходный механизм

До изменения путь был следующим:

```text
LLM возвращает needs_clarification
→ parser/sanitizer сохраняет legacy-поля
→ RagAnswerGenerator._query_plan_clarification()
→ при наличии clarification_question возвращает его
→ иначе при answer_type=clarification подставляет CLARIFY_KP_MESSAGE
→ RagAnswerGenerator.answer() возвращает clarification до retrieval
```

Дополнительно `should_ask_clarification()` независимо распознавал `КП`.

Гипотеза «любое невалидное уточнение превращается в КП/ЦКП» подтвердилась
частично: generic fallback срабатывал не для любого плана, а для сочетания
`needs_clarification=true`, пустого `clarification_question` и
`answer_type=clarification`. Проверки span, кандидатов и missing slots не было.

## 3. Подтверждённая причина ложных уточнений

Причина состояла из трёх частей:

1. Решение LLM принималось без отдельной structural validation.
2. Legacy-флаг не доказывал ни неоднозначность, ни отсутствие обязательного slot.
3. Пустой вопрос мог быть заменён доменно несвязанным сообщением про КП/ЦКП,
   после чего retrieval намеренно не запускался.

Сохранённый artifact этапа 1 фактически показывает
`false_clarification_count=0`, а не 2 из входной точки сравнения. При этом он
показывает `missing_clarification_count=8`: четыре объективных missing-slot
кейса и четыре ясных responsibility-вопроса, ошибочно помеченных в fixture как
требующие уточнения. Ожидания последних исправлены по семантике, а не ради
метрики:

- «Кто отвечает за заказ?»;
- «Кто отвечает за отгрузку?»;
- «Кто занимается оплатами?»;
- «Кто выпускает заказ клиенту?».

## 4. Новая модель ClarificationPlan

Добавлены immutable-модели:

```python
ClarificationPlan(
    required=False,
    kind="none",
    ambiguity_span=None,
    candidate_meanings=[],
    missing_slots=[],
    question=None,
    confidence=0.0,
)

ClarificationDecision(
    raw=ClarificationPlan(),
    validated=ClarificationPlan(),
    action="continue_retrieval",
    validation_reasons=[],
)
```

`QueryPlan` хранит validated plan, raw analyzer proposal, action и validation
reasons. Legacy-поля `needs_clarification`, `clarification_question` и
`answer_type` сохранены и синхронизируются с validated decision.

## 5. Clarification kinds

Допустимый закрытый набор:

```text
none
abbreviation
lexical_ambiguity
missing_subject
missing_object
missing_document_type
missing_scope
missing_required_slot
```

Неизвестный kind не останавливает retrieval.

## 6. Deterministic validator

`validate_clarification(question, query_plan)`:

1. сохраняет raw proposal;
2. проверяет registry ambiguity и объективные missing-slot признаки;
3. проверяет word-boundary span, два различных значения и предметный вопрос;
4. отклоняет неполные, generic и несвязанные proposals;
5. возвращает `clarify` или `continue_retrieval` с причинами;
6. не меняет intent/fact type при отклонённом clarification.

Если clarification принят, маршрутизация откладывается до заполнения slot:

```text
abbreviation/lexical ambiguity → intent=ambiguous_abbreviation
missing slot                   → intent=unknown pending slot
operational_lookup             → false
operational_decision_reason    → clarification_required_before_routing
```

Raw intent и остальные raw analyzer fields остаются в diagnostics. Это устраняет
противоречие «retrieval остановлен уточнением, но latent plan уже показывает
1С routing».

## 7. Registry неоднозначностей

Registry декларативный и содержит только подтверждённое сокращение:

```python
{
    "КП": (
        "коммерческое предложение",
        "ценный конечный продукт",
    ),
}
```

Сопоставление регистронезависимое, с границами слова. Проверены `КП`, `кп`,
`КП?`, `«КП»` и отрицательные последовательности `КПИ`, `АКПП`, `СКП`,
`кпроекту`.

Явное «КП как коммерческое предложение» остаётся разрешённым смыслом и не
уточняется.

## 8. Поведение invalid clarification

Удалён `CLARIFY_KP_MESSAGE` и ранний fallback:

```text
invalid raw clarification
→ validated.required=false
→ action=continue_retrieval
→ normal retrieval
```

Deterministic tests подтверждают rejection `12/12` для неполных/ложных
proposals. Orchestration tests подтверждают запуск retrieval после rejection
`3/3`. В live pack raw LLM ни разу не предложил invalid clarification, поэтому
live-метрики `invalid_clarification_rejection_rate` и
`retrieval_started_after_rejected_clarification` честно равны
`not_available`, а не нулю.

## 9. Связь с QueryPlan v2

Validator получает уже нормализованные `requested_fact_type`,
`temporal_scope`, `subject` и `intent`, но не принимает их как достаточное
доказательство ambiguity.

- Ясный predicate + конкретный subject продолжают retrieval.
- Generic `документы/бумаги` требуют `document_type`.
- Местоименное «этим» требует `subject`.
- Неконкретизированное заявление требует `application_type`.
- Указанные «отпуск», «командировка», «новое оборудование» заполняют slot и
  запрещают уточнение.

Для «Кто согласует моё заявление?» выбран missing `application_type`: без
history ответственный зависит от вида заявления. Это статическое missing-slot
решение, а не запрос текущего статуса.

## 10. Observability

В native `RagAnswer.query_plan`, CLI debug и JSON artifacts доступны:

```text
raw_clarification_required
validated_clarification_required
raw_clarification_kind
validated_clarification_kind
raw_ambiguity_span
validated_ambiguity_span
raw_candidate_meanings
validated_candidate_meanings
raw_missing_slots
validated_missing_slots
raw_clarification_question
validated_clarification_question
clarification_action
clarification_validation_reasons
```

Architecture runner дополнительно пишет `retrieval_started`. Observability
conversation resolution, evidence plan, rejected chunks и unsupported claims
не добавлялась.

## 11. Изменённые файлы

| Файл | Назначение этапа 2 |
|---|---|
| `linehelper/rag/query_analyzer.py` | `ClarificationPlan`, parser, registry, validator и routing deferral |
| `linehelper/llm/answer_generator.py` | единая validation для fake/live analyzer, безопасная ранняя ветка и diagnostics |
| `linehelper/cli.py` | вывод raw/validated clarification diagnostics |
| `scripts/rag_architecture_v2_harness.py` | structured checks, metrics и repeatability dimensions |
| `scripts/run_rag_architecture_v2_baseline.py` | native clarification JSON и `retrieval_started` |
| `scripts/run_organization_test_pack.py` | удалён независимый legacy clarification fallback |
| `tests/fixtures/rag_architecture_v2_cases.json` | 19 соседних кейсов и объективные missing-slot expectations |
| `tests/test_safe_clarification.py` | 45 unit + 11 deterministic integration test items |
| `tests/test_answer_generator.py` | обновлён diagnostic contract и КП invariant |
| `tests/test_rag_architecture_v2_harness.py` | 2 новых metric tests и structured fixture checks |

`docs/obsidian/00_INDEX.md` уже имел несвязанный diff `0---` и не относится к
этапу 2. Ранее существовавшие untracked audit/probe-файлы не изменялись и не
добавлялись автоматически.

## 12. Unit tests

Добавлено 47 unit test items:

- 45 в `tests/test_safe_clarification.py`;
- 2 для clarification metrics в architecture harness.

Покрыты enum/defaults, structured и legacy JSON, registry, word boundaries,
valid ambiguity, clear questions, missing slots, invalid plans, duplicate
meanings и generic questions.

## 13. Integration tests

Добавлено 11 deterministic orchestration items с fake analyzer/retriever/LLM и
реальными validator + `RagAnswerGenerator`.

Проверено:

- ложный КП/ЦКП proposal для T09 отклоняется;
- пустой raw question отклоняется;
- retrieval после rejection запускается;
- chunk ИП-0005 может войти в sources;
- валидный КП останавливает retrieval;
- пропущенный LLM ambiguity восстанавливается registry;
- missing slots останавливают retrieval;
- конкретизированные и operational questions продолжают pipeline;
- accepted ambiguity откладывает latent operational routing;
- raw и validated diagnostics не смешиваются.

Targeted suite:

```text
280 passed in 0.64s
```

## 14. Architecture target pack

Перед checkpoint выполнен один полный live run окончательного кода: 32 исходных
и 19 новых соседних кейсов запущены в одном процессе и сохранены без ручной
подмены записей:

```text
data/test_runs/rag_architecture_v2/iteration_02_final/20260729_095741/baseline.json
28 passed / 23 failed / 0 blocked
```

Artifact содержит ровно 51 evaluation для 51 уникального case. Для исходных 32
кейсов, агрегированных из тех же записей:

```text
21 passed / 11 failed / 0 blocked
```

Ключевые метрики единого artifact:

| Метрика | Результат |
|---|---:|
| False clarification | 0 |
| Missing clarification | 0 |
| Valid clarification recall | 11/11 (100%) |
| Missing-slot accuracy | 6/6 (100%) |
| Operational misroutes | 1 |
| Missed operational | 0 |
| Requested fact accuracy, 51 | 38/51 (74,51%) |
| Requested fact accuracy, исходные 32 | 30/32 (93,75%) |
| Paired responsibility/status routing | 8/8 (100%) |
| Required chunk recall @5 / @10 | 25/30 / 25/30 (83,33% / 83,33%) |
| Required chunks in context | 16/29 (55,17%) |
| Evidence coverage | `not_available` |

Единственный operational misroute — второй ход T01, относящийся к будущему
conversation resolver. Strict failures новых clarification-кейсов относятся к
неточностям `requested_fact_type` этапа 1; все 11/11 clarification decisions
верны.

История проверки сохранена: первоначально отчёт использовал полный pre-fix run
и частичный post-fix run. Старые artifacts не удалены, но перед checkpoint их
составная оценка полностью заменена результатом единого финального запуска.

## 15. T09 trace до/после

Live trace:

```text
raw clarification.required=false
→ validated clarification.required=false
→ action=continue_retrieval
→ retrieval_started=true
→ raw candidates: chunks 68, 67, 66 ИП-0005 Распоряжения
→ context: chunks 68, 67, 66
→ sources: ИП-0005 Распоряжения
→ response_kind=answer
```

Ответ подтвердил письменную форму и необходимость оформить устное распоряжение
письменно. Strict status остаётся `failed` только потому, что stage-1
`requested_fact_type` равен `unknown`, а fixture ожидает `procedure`.

Отдельный deterministic integration trace принудительно подаёт raw
КП/ЦКП-clarification:

```text
raw.required=true, span=КП (КП отсутствует в T09)
→ ambiguity_span_not_in_question
→ invalid_clarification_rejected
→ continue_retrieval
→ ИП-0005 выбран как source
```

## 16. T01 первый ход

Live первый ход:

```text
question contains standalone КП
→ raw LLM clarification.required=false
→ deterministic registry confirms ambiguity
→ kind=abbreviation
→ span=КП
→ candidate meanings:
   - коммерческое предложение
   - ценный конечный продукт
→ action=clarify
→ retrieval_started=false
→ response_kind=clarification
```

Второй ход остаётся неразрешённым и в одном live run ушёл в operational intent.
Это conversation resolution этапа 3; production resolver не менялся.

## 17. RP01

`RP01` — «К кому обращаться по доставке клиенту?» — объективно содержит
predicate и конкретный subject.

Live результат:

```text
requested_fact_type=primary_contact
intent=roles_responsibility
validated_clarification_required=false
action=continue_retrieval
retrieval_started=true
status=passed
```

## 18. Missing-slot scenarios

Единый final live artifact:

| Вопрос | Kind / slot | Action | Retrieval |
|---|---|---|---|
| Кому отдать документы? | `missing_document_type / document_type` | clarify | нет |
| Кому отдать бумаги? | `missing_document_type / document_type` | clarify | нет |
| Кто этим занимается? | `missing_subject / subject` | clarify | нет |
| Куда направить заявление? | `missing_document_type / application_type` | clarify | нет |
| Кто согласует моё заявление? | `missing_document_type / application_type` | clarify | нет |

Квалифицированные кадровые/бухгалтерские документы, ЭДО, внутренний
документооборот, отпуск, командировка и новое оборудование не уточняются.

Live missing-slot accuracy: `6/6` (в fixture два дублирующих conversation-style
контроля считаются отдельными regression cases).

## 19. Повторяемость

Artifact:

```text
data/test_runs/rag_architecture_v2/iteration_02_final_repeatability/20260729_105408/baseline.json
```

По три повтора:

| Case / вопрос | Raw required | Validated / kind / slot | Action / retrieval | Fact type | Operational | Mode | Stable |
|---|---|---|---|---|---|---|---|
| T09, устное распоряжение | false 3/3 | false / none | continue / true 3/3 | unknown 3/3 | false 3/3 | answer 3/3 | да |
| SC_KP01, оформить КП | false 3/3 | true / abbreviation / КП | clarify / false 3/3 | procedure 3/3 | false 3/3 | clarification 3/3 | да |
| CL01, отдать документы | false 3/3 | true / missing_document_type / document_type | clarify / false 3/3 | document_recipient 3/3 | false 3/3 | clarification 3/3 | да |
| T08, внутренний документооборот | false 3/3 | false / none | continue / true 3/3 | responsible_person 3/3 | false 3/3 | answer 3/3 | да |

`all_available_dimensions_stable=true` для всех четырёх cases. Context и
validated intent также стабильны.

## 20. Full pytest

Compileall:

```text
success
```

Final full pytest:

```text
369 passed in 14.72s
```

Этап 1: `311 passed`. Добавлено 58 test items: 47 unit и 11 integration.
Live Ollama не входит в pytest.

## 21. Organization regression

Команда выполнена в том же retrieval-only режиме на первых 10 сценариях:

```text
8 PASS / 1 PARTIAL / 1 FAIL / 0 MANUAL_REVIEW
```

Результат совпадает с этапами 0 и 1. Artifact:

```text
data/test_runs/organization/iteration_02_final/20260729_110511/summary.json
```

## 22. Сравнение с baseline и этапом 1

| Метрика | Baseline 0 | Этап 1 | Этап 2, единый final run | Изменение этапа 2 |
|---|---:|---:|---:|---:|
| Исходные architecture cases | 0/32 | 12/32 | 21/32 | +9 passed |
| Расширенный pack | — | — | 28/51 | 19 новых neighbors |
| False clarification | 2 (входная точка) | 0 в сохранённом artifact | 0 | без false clarification |
| Missing clarification | — | 8 | 0 | -8 |
| Valid clarification recall | not_available | not_available | 11/11 | native |
| Missing-slot accuracy | not_available | not_available | 6/6 | native |
| Invalid rejection | not_available | not_available | 12/12 deterministic; live N/A | native validator |
| Retrieval after rejection | not_available | not_available | 3/3 deterministic; live N/A | normal pipeline |
| Operational misroutes | 5 | 0 | 1 | T01 second turn, stage 3 |
| Missed operational | 3 | 0 | 0 | без ухудшения |
| Requested fact accuracy (32) | not_available | 30/32 | 30/32 | без изменения слоя |
| Requested fact accuracy (51) | not_available | — | 38/51 | расширенный pack |
| Paired routing | not_available | 8/8 | 8/8 | без ухудшения |
| Required recall @5/@10 | 72% / 72% | 80% / 80% | 83,33% / 83,33% | live variation; retrieval не менялся |
| Required chunks in context | 29,17% | 45,83% | 55,17% | live variation; context selection не менялся |
| Full pytest | 273 | 311 | 369 | +58 |
| Organization smoke | 8/1/1 | 8/1/1 | 8/1/1 | без ухудшения |

Расхождение по false clarification зафиксировано явно: входной prompt называет
2, но сохранённый artifact этапа 1 содержит 0 false и 8 missing
clarifications. Этап 2 даёт 0 false по обеим интерпретациям и устраняет все
объективные missing clarifications.

## 23. Неисправленные T01–T09

| ID | Итог | Причина вне clarification scope |
|---|---|---|
| T01 | failed | второй ход не использует conversation history; fact/intent/op route неверны |
| T02 | failed | chunks 71, 73, 74 не входят в context |
| T03 | failed | chunk 29 не найден, generic no_answer |
| T04 | failed | ответ не фиксирует отсутствие именованного адресата |
| T05A | failed | context/14-day concept coverage |
| T05B | passed | — |
| T06 | passed | — |
| T07 | passed | — |
| T08 | failed | structured records 437/439 не найдены/не вошли в context |
| T09 | layer passed, strict failed | requested_fact_type остаётся `unknown` |

Не менялись retrieval, context, answer/evidence и conversation layers, поэтому
эти failures не маскировались.

## 24. Риски и ограничения

1. Registry пока содержит только подтверждённое `КП`; новые сокращения требуют
   декларативного добавления и тестов.
2. Missing-slot detector использует универсальные русские языковые конструкции,
   но не является полноценным slot-filling parser.
3. Live LLM не предложил invalid clarification в этом run; rejection rate в
   live-метриках остаётся `not_available`. Safety доказан deterministic tests.
4. `requested_fact_type` для ряда соседних формулировок остаётся неточным
   (`procedure/definition/primary_contact`); это QueryPlan v2 follow-up, не
   clarification.
5. T01 second turn требует conversation resolver этапа 3.
6. Финальный 51-case artifact снят одним полным запуском окончательного кода.
   Ранее использованная составная оценка сохранена только как история и больше
   не является источником итоговых метрик.
7. Evidence coverage и unsupported claims по-прежнему не наблюдаемы.
8. HEAD остаётся baseline commit, потому что изменения этапа 1 и 2 намеренно не
   коммитились автоматически.
9. Несвязанный diff `docs/obsidian/00_INDEX.md` сохранён и не включён в scope.

## 25. Условия перехода к этапу 3

Этап 2 готов к ручной проверке при следующих условиях:

1. подтверждён structured clarification contract;
2. принято объективное исправление expectations ясных responsibility cases;
3. воспроизводятся `369 passed`;
4. live artifacts подтверждают `false=0`, recall `11/11`, slots `6/6`;
5. organization smoke остаётся `8/1/1`;
6. единый final architecture artifact воспроизводит `28/51`, а исходный
   поднабор — `21/32`;
7. после ручного commit этап 3 ограничивается conversation history и
   `resolved_question`, не повторяя clarification logic.

Предлагаемый commit message:

```text
refactor(rag): добавить QueryPlan v2 и безопасные уточнения
```
