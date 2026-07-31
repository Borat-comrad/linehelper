# Итерация 10. Evidence Boundary Corrective

Статус: **завершена успешно**.

Исправлен только слой evidence planning/assessment. Retrieval,
ContextComposer, GroundedAnswerContract, prompt, fixtures и корпоративные
документы не изменялись.

## 1. Причины T07, PR01 и MR08 до изменения

### T07

В Release Candidate artifact:

- question/resolved question: `Кто отвечает за рабочие места и оборудование?`;
- intent: `roles_responsibility`;
- requested fact type: `responsible_person`;
- subject: `отдел рабочих мест и оборудования`;
- EvidencePlanner создавал один монолитный requirement
  `primary_responsibility`;
- selected context: 487, 577, 485;
- все три chunks были non-supporting;
- rejection: `structured_responsibility_not_found`;
- result: `insufficient_evidence`, generic no-answer.

Chunk 577 имеет `doc_type=employee_role`, record key
`employee:shkirenkov_roman:2025-12-17` и прямо подтверждает ответственность за
рабочие места. Он не подтверждает произвольный тип оборудования. Монолитное
требование не позволяло сохранить подтверждённую identity-часть и отдельно
отметить неполное покрытие qualifier.

### PR01

- question: `Как оформить командировку?`;
- subject: `формирование командировки`;
- selected context: 47, 24, 19;
- chunk 47: `logical_unit_type=procedure`, title и section содержат
  «Согласование командировки», source title — инструкция командировки;
- все chunks были non-supporting;
- rejection: `subject_matched_procedure_not_found`.

Subject matcher требовал совпадения всех извлечённых anchor groups. Предметный
anchor «командировка» совпадал с устойчивыми metadata chunk 47, но общее
процессное слово «формирование» ошибочно становилось вторым обязательным
предметным anchor и ломало итоговый `all(...)`.

### MR08

- question: `Как оформить документ, которого нет в базе?`;
- QueryPlan subject: `документооборот`;
- selected context: 48, 45, 44;
- chunks относятся к согласованию приказов и договоров;
- все три имеют `logical_unit_type=procedure` и общий термин
  «документооборот» в metadata;
- все три ошибочно стали supporting;
- mode: `full_answer`, chunks попали в allowed/rendered sources.

EvidenceAssessor использовал суженный subject и не проверял отрицательное
ограничение исходного вопроса «которого нет в базе». Поэтому общий procedural
type и общий domain-term ошибочно считались достаточной поддержкой основной
процедуры.

## 2. Изменённые правила evidence

Изменён только `linehelper/rag/evidence_assessor.py`:

1. Subject match теперь возвращает структурированный результат, а не только
   boolean.
2. Metadata/title/section/logical-unit поля проверяются отдельно от chunk text.
3. Для procedure требуется и procedural признак, и subject match.
4. Отрицательное ограничение исходного вопроса может запретить procedural
   support.
5. Составной responsibility-вопрос получает два минимальных requirements:
   identity и полное покрытие scope.
6. Порядок supporting/non-supporting chunks сохраняет исходный порядок
   selected context.

Новый LLM-вызов не добавлен.

## 3. Positive subject matching

Поддерживаемые источники subject match:

- `title`;
- `source`;
- `section`;
- `doc_type`;
- `metadata.source_file`;
- `metadata.logical_unit_title`;
- `metadata.logical_unit_type`;
- `metadata.record_key`;
- `metadata.topic`;
- `metadata.unit_name`;
- `metadata.tags`;
- text/excerpt для тех requirements, где слабое содержательное совпадение
  допустимо.

Общие процессные слова, включая «формирование», не становятся обязательным
предметным anchor. Для PR01 title/section chunk 47 дают устойчивое совпадение
по предмету командировки; procedural metadata подтверждает тип знания.

## 4. Negative subject guard

Если исходный вопрос содержит явное ограничение отсутствия объекта в базе,
материалах или источниках, обычный procedural chunk не поддерживает
`primary_procedure` только на основании общего domain-term.

Guard снимается только если сам evidence отражает соответствующее условие
отсутствия. В MR08 chunks приказов и договоров этого условия не содержат,
поэтому остаются non-supporting.

Это общее правило по структуре вопроса; case ID, chunk ID, конкретный документ
и буквальная тестовая строка в production-коде не используются.

## 5. Составное responsibility evidence

Для явно составного responsibility payload EvidencePlanner создаёт:

- `responsible_identity` — полезная identity/role по подтверждённой части
  предмета;
- `complete_responsibility_scope` — полное покрытие всех частей subject.

Если identity подтверждена, а полный scope нет, применяется `partial_answer`.
LLM получает только supporting chunks, а deterministic renderer добавляет
отсутствующий requirement. Неподтверждённый qualifier не считается полным
scope и не заполняется предположением.

## 6. T07 до/после

| Параметр | До | После |
| --- | --- | --- |
| Mode | `insufficient_evidence` | `partial_answer` |
| Supported requirements | нет | `responsible_identity` |
| Unsupported requirements | `primary_responsibility` | `complete_responsibility_scope` |
| Chunk 577 | non-supporting | supporting |
| Response | generic no-answer | подтверждён Шкиренков по рабочим местам, scope отдельно помечен неполным |
| Strict status | failed | passed |

Targeted live diagnostics сохранили порядок selected context. Chunks 487, 577
и 485 поддерживают identity requirement по разным подтверждённым частям
составного subject; ни один не поддерживает полный scope. Финальный draft
использовал подтверждение chunk 577 и не приписал Шкиренкову произвольный тип
оборудования.

## 7. PR01 до/после

| Параметр | До | После |
| --- | --- | --- |
| Mode | `insufficient_evidence` | `full_answer` |
| Supporting chunks | нет | 47 |
| Non-supporting chunks | 47, 24, 19 | 24, 19 |
| Subject match source | отсутствовал | `title` chunk 47 |
| Response | generic no-answer | grounded answer по процедуре командировки |
| Strict status | failed | passed |

Rendered sources содержат только chunk 47.

## 8. MR08 до/после

| Параметр | До | После |
| --- | --- | --- |
| Mode | `full_answer` | `insufficient_evidence` |
| Supporting chunks | 48, 45, 44 | нет |
| Non-supporting chunks | нет | 48, 45, 44 |
| Negative guard | отсутствовал | `true` для всех трёх chunks |
| Allowed/rendered sources | 48, 45, 44 | пусто |
| Response | answer | deterministic no-answer |
| Strict status | failed | passed |

Система не описывает согласование приказа или договора как процедуру
отсутствующего документа.

## 9. Регрессии core-сценариев

Единый targeted live artifact: **11 passed / 0 failed / 0 blocked**.

| Case | Mode | Supporting | Non-supporting | Rendered sources | Status |
| --- | --- | --- | --- | --- | --- |
| T02 | `full_answer` | 72, 71, 73, 74 | — | 72, 71, 73, 74 | passed |
| T03 | `full_answer` | 29 | 44, 39 | 29 | passed |
| T04 | `partial_answer` | 47 | 19 | 47 | passed |
| T05A | `full_answer` | 42, 43, 27 | — | 42, 43, 27 | passed |
| T06 | `full_answer` | 594, 514 | 592 | 594, 514 | passed |
| T07 | `partial_answer` | 487, 577, 485 | — | 487, 577, 485 | passed |
| T08 | `full_answer` | 437, 439 | 73 | 437, 439 | passed |
| T09 | `full_answer` | 68, 70, 67 | — | 68, 70, 67 | passed |
| PR01 | `full_answer` | 47 | 24, 19 | 47 | passed |
| OR01 | `full_answer` | 70, 69, 67 | — | 70, 69, 67 | passed |
| MR08 | `insufficient_evidence` | — | 48, 45, 44 | — | passed |

Дополнительные artifact metrics:

- requested fact accuracy: 11/11;
- required candidate recall @5/@10: 15/15;
- required chunks in context: 15/15;
- generic no-answer при наличии required retrieval evidence: 0;
- operational misroutes: 0;
- missed operational: 0;
- false clarification: 0;
- unsupported person answers: 0.

## 10. Test budget

| Тип | Лимит | Добавлено |
| --- | ---: | ---: |
| Unit test items | 6 | 4 |
| Deterministic integration items | 3 | 2 |
| Fixture cases | 0 | 0 |

Unit coverage:

1. procedure subject через metadata/title;
2. generic procedure без subject match;
3. negative subject guard;
4. compound responsibility partial coverage и unsupported qualifier.

Существующие ordering и serialization tests расширены новыми diagnostics без
создания дополнительных test items.

Integration coverage:

1. PR01 metadata-matched procedure;
2. MR08 negative boundary;
3. существующий агрегированный core regression item расширен T07.

## 11. Tests и compileall

Targeted evidence/contract/answer-generator suite:

```text
63 passed in 0.26s
```

Compileall:

```text
.\.venv\Scripts\python.exe -m compileall linehelper tests scripts
success
```

Full pytest:

```text
501 passed in 20.72s
```

До итерации было 495 tests; прирост ровно 6 items соответствует бюджету.

## 12. Targeted live artifact

```text
data/test_runs/rag_architecture_v2/
iteration_10_evidence_boundary_targets/
20260731_145752/baseline.json
```

Runner выполнен один раз одним процессом только для T07, PR01, MR08,
T02, T03, T04, T05A, T06, T08, T09 и OR01.

## 13. Diagnostics

Для каждого assessed chunk теперь сериализуются:

- `subject_match_source`;
- `matched_subject_terms`;
- `rejected_subject_terms`;
- `logical_unit_match`;
- `structured_metadata_match`;
- `negative_subject_guard`;
- `requirement_ids`;
- `support_decision`;
- `decision_reasons`.

Это структурированные результаты правил, а не скрытый chain-of-thought.

## 14. Известные ограничения

1. Полный 71-case pack намеренно не запускался. Повторная Release Candidate
   Acceptance должна проверить глобальные status changes.
2. Исправлены только T07, PR01 и MR08; остальные 20 failures этапа 9 вне
   scope.
3. Compound scope считается полным, только если один selected chunk покрывает
   все компоненты; collective coverage несколькими chunks пока остаётся
   partial.
4. Negative guard использует ограниченный декларативный набор явных
   конструкций отсутствия и не является универсальным semantic negation
   parser.
5. T07 identity sources включают несколько structured records, каждое из
   которых совпадает минимум с одной частью составного subject. Полный scope
   при этом остаётся unsupported; tightening source set возможно только после
   отдельного product-решения о relation semantics.

## 15. Проверка scope

Production-файл изменён один:

```text
linehelper/rag/evidence_assessor.py
```

Тестовые файлы:

```text
tests/test_evidence_assessor.py
tests/test_evidence_integration.py
```

QueryAnalyzer, RequestedFactTypeResolver, clarification, ConversationResolver,
retrieval, ContextComposer, GroundedAnswerContract, validator, renderer,
prompt, MemoryStore, ingestion, fixture и документы не менялись.

Полный 71-case pack, repeatability и organization pack не запускались.
Commit и push не выполнялись.
