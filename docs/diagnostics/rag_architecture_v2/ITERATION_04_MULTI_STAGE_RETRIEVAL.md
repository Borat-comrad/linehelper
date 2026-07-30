# Итерация 04. Multi-Stage Retrieval

## 1. Scope

Итерация ограничена retrieval-слоем: планирование нескольких независимых
поисковых стадий, metadata-aware read-only lookup, агрегация кандидатов,
дедупликация и provenance. QueryPlan v2, Safe Clarification, Conversation
Resolver, context selection, evidence gate, prompts, ingestion и схема SQLite
не менялись.

Исходный commit:

```text
67ee8f0c393ebd8791f7e6f2145627ea594a4ddb
refactor(rag): добавить ConversationResolver и resolved question
```

Runtime:

```text
branch: refactor/rag-architecture-v2
Python: 3.12.13
OS: Windows 11
answer model: qwen2.5:14b
analyzer model: qwen2.5:3b
DB: data/memory/linehelper_memory.db
```

## 2. Исходный retrieval

До изменения фактический путь выглядел так:

```text
resolved question
→ _query_plan_retrieval_queries
→ SemanticRetriever.retrieve для каждого query
→ несколько внутренних FTS expressions
→ first-hit dedupe внутри одного retrieve
→ rerank/top-N
→ повторный first-hit dedupe в RagAnswerGenerator
→ organization boosts
→ существующий context selection
```

Один и тот же chunk мог быть найден несколькими запросами, но сохранялся
первый экземпляр, а не лучший. Query, stage, raw rank и raw score попадания не
сохранялись.

`MemoryStore.search_fts()` передавал выражение через `_prepare_fts_query`.
Он удалял `*`, поэтому сформированное retriever выражение `оборудован*`
становилось точным токеном `оборудован` и не совпадало с
`оборудовании`. Это подтверждённая причина отсутствия chunk 29 в T03.

Для T08 точный FTS по `внутренний документооборот` находил structured records
437/439, но ранние policy hits и first-hit dedupe не позволяли сохранить
subject-focused organization results в итоговом top-10.

## 3. Подтверждённые причины T03 и T08

### T03

В production DB существует:

```text
source:
data/raw_docs/2026-19-06 Инструкция Заявка в 1 отд-е
(построение) хоз.часть.pdf

logical_unit_type: procedure
chunk: 29
```

Прямой безопасный prefix FTS:

```text
оборудован* OR имуществ* OR заявк*
```

находит chunk 29. Старый путь терял prefix semantics при sanitization.

### T08

В production DB существуют устойчивые записи:

```text
organization_unit:division_1:2025-12-17
organization_unit:department_2:2025-12-17
```

Обе содержат функцию внутреннего документооборота и сотрудников Шкиренкова
Романа и Малахову Марию. Старый общий поиск поднимал policy-документ, но не
сохранял typed organization hits в top-10.

## 4. RetrievalPlan

Добавлены immutable native-модели:

```text
RetrievalPlan
RetrievalStage
RetrievalStageHit
RetrievalObservation
RetrievalCandidate
CandidateAggregation
RetrievalResult
```

`RetrievalPlan` хранит original/resolved question, requested fact type,
subject, intent, exact entities, preferred sources, operational flag,
planning reasons и ordered stages.

Planner использует полный `resolved_question`. Короткий follow-up после этапа
3 не передаётся в retrieval как самостоятельная строка.

## 5. Retrieval stages

Закрытый набор стадий:

```text
exact_identifier
exact_entity
exact_subject
fts_resolved_question
fts_normalized_question
fts_query_expansion
procedure_lookup
organization_function_lookup
preferred_source_lookup
sibling_lookup
```

Operational QueryPlan и pending clarification возвращают план без semantic
stages. Не все стадии запускаются для каждого вопроса.

## 6. Exact identifier retrieval

Детерминированно извлекаются:

- ИП-коды;
- номера отделов и отделений;
- имена корпоративных форм;
- бренды;
- фамилии.

Границы слова, регистр и пунктуация покрыты тестами. Exact lookup дополняет,
но не заменяет resolved-question FTS.

В live pack:

- `СЗ_Командировка` → required source rank 1;
- `отдел 12Б` → `organization_unit:department_12b:2025-12-17` rank 1;
- `ИП-0005` → policy chunks ranks 1–5.

Exact identifier recall: `3/3`.

## 7. Procedure retrieval

Для `requested_fact_type=procedure` добавляется metadata-aware prefix stage:

```text
subject
+
заявка / инструкция / порядок / согласование
+
logical_unit_type=procedure
```

Это универсальная связь типа факта с procedural metadata, а не mapping
вопроса на chunk 29.

T03 required chunk 29 теперь находится на rank 3 через
`procedure_lookup`. MR01 и MR07 находят его на ranks 5 и 2.

Procedure recall полного pack: `21/22` (`95,45%`). Единственный miss в
procedure-tag subset — CV01, где ожидается organization contact Шкиренкова,
а не procedural chunk. Второй общий recall miss — CV02; оба описаны в разделе
рисков. Blocked records исключаются из знаменателя.

## 8. Organization function retrieval

Для:

```text
responsible_person
primary_contact
unit_head
document_recipient
```

subject используется в отдельной metadata-aware стадии по существующим:

```text
employee_role
organization_unit
responsibility_route
role_combination
```

Generic analyzer expansions остаются дополнительными и не заменяют subject.

T08 structured keys получены на ranks 3/4. MR02 получает те же keys на
ranks 1/2. Organization function recall новых целевых cases: `5/5`.

## 9. Sibling retrieval

Sibling lookup выполняется по устойчивой metadata-связи:

- одинаковый source;
- одинаковый `logical_unit_type`;
- для процедур — совместимый logical unit title.

Числовая близость chunk IDs не используется.

T02 и MR06 сохраняют четыре policy rules:

```text
1. Официальная переписка
2. Кадровые документы
3. Внутреннее взаимодействие
4. Передача оригиналов документов
```

Sibling recall: `8/8`. Context selector по-прежнему берёт первые три; это
граница этапа 5.

## 10. Candidate model

`RetrievalCandidate` содержит:

- исходный `RetrievedChunk`;
- final score;
- best raw score;
- все stage hits;
- все matched queries;
- match reasons;
- metadata matches;
- retrieval-level adjustments.

Публичный legacy API `SemanticRetriever.retrieve()` сохранён.

## 11. Provenance

Для каждого кандидата в native diagnostics доступны:

```text
stage
query
raw_score
rank
stage_weight
scored_value
best_raw_score
final_score
metadata_matches
source
record_key
doc_type
knowledge_domain
```

В полном pack provenance доступен для `1506/1506` кандидатов (`100%`).

## 12. Deduplication

Устойчивая identity выбирается в порядке:

```text
record_key
→ content_hash
→ chunk_id
→ source/title/section fingerprint
```

Aggregator:

1. сохраняет один candidate;
2. сохраняет все stage hits и matched queries;
3. выбирает лучший scored hit, а не первый;
4. применяет candidate limit после merge;
5. использует deterministic tie-break.

Полный pack:

```text
duplicate observations before merge: 1296
duplicate candidates after merge: 0
best-score checks: 781/781
```

## 13. Scoring и ordering

Named stage weights:

| Stage | Weight |
|---|---:|
| exact_identifier | 220 |
| organization_function_lookup | 200 |
| exact_entity | 180 |
| procedure_lookup | 180 |
| exact_subject | 140 |
| preferred_source_lookup | 100 |
| fts_resolved_question | 80 |
| fts_normalized_question | 70 |
| fts_query_expansion | 30 |
| sibling_lookup | 20 |

FTS/rerank components имеют явные caps, metadata match даёт отдельный
небольшой bonus. В scoring нет question strings, фамилий или chunk IDs.

## 14. Metadata limitations

Не у всех legacy chunks есть `record_key`, `knowledge_domain` и одинаково
подробный logical-unit metadata. Для них identity использует content hash,
chunk ID или textual fingerprint.

`logical_unit_type=procedure` достаточно широк: stage поднимает несколько
процедур, а не только наиболее предметную. Это повышает recall, но оставляет
precision/context selection следующему слою.

Ни schema migration, ни reingestion не выполнялись.

## 15. Observability

Native diagnostics добавлены в `RagAnswer`, CLI debug и architecture runner:

```text
retrieval_plan
retrieval_stages
stage_queries
stage_filters
stage_hit_counts
candidate_count_before_dedupe
candidate_count_after_dedupe
duplicate_count
candidate_provenance
candidate_stage_hits
candidate_matched_queries
candidate_best_raw_score
candidate_final_score
candidate_order
best_score_dedupe_correct/checks
retrieval_duration_ms
```

Evidence coverage и unsupported claim attribution по-прежнему недоступны и
не добавлялись в рамках retrieval-итерации.

## 16. Изменённые файлы

| Файл | Назначение |
|---|---|
| `linehelper/rag/retriever.py` | RetrievalPlan, stages, aggregator, provenance, dedupe |
| `linehelper/memory/memory_store.py` | read-only structured prefix/metadata APIs |
| `linehelper/llm/answer_generator.py` | native plan execution и diagnostics |
| `linehelper/cli.py` | retrieval debug output |
| `scripts/run_rag_architecture_v2_baseline.py` | serialization native retrieval diagnostics |
| `scripts/rag_architecture_v2_harness.py` | stage/rank evaluation, retrieval metrics, repeatability |
| `tests/fixtures/rag_architecture_v2_cases.json` | MR01–MR08 и stable retrieval invariants |
| `tests/test_multi_stage_retrieval.py` | planner/aggregator/integration coverage |
| `scripts/tests/test_memory_store.py` | read-only metadata API tests |
| `tests/test_answer_generator.py` | native orchestration tests |
| `tests/test_rag_architecture_v2_harness.py` | fixture/metrics/repeatability tests |
| `docs/diagnostics/rag_architecture_v2/ITERATION_04_MULTI_STAGE_RETRIEVAL.md` | отчёт |

## 17. Unit tests

Добавлено 28 unit/infrastructure test items:

- RetrievalPlanner и выбор стадий;
- exact entity extraction и word boundaries;
- CandidateAggregator;
- best-score dedupe;
- deterministic tie-breaking;
- fixture validation;
- stage/rank evaluation;
- provenance/dedupe/latency metrics;
- retrieval repeatability dimensions.

## 18. Integration tests

Добавлен 21 deterministic integration test item:

- тестовая SQLite с реальным MemoryStore и SemanticRetriever;
- T03 procedure lookup;
- T08 structured function lookup;
- T02 sibling lookup;
- T06/T07 exact subject regressions;
- exact form/policy/unit;
- operational no-mix;
- public legacy retriever API;
- RagAnswerGenerator с native retrieval plan;
- JSON diagnostics/provenance.

Тесты не сравнивают полный LLM answer text.

Targeted suite:

```text
409 passed
```

## 19. Architecture target pack

Единый полный artifact:

```text
data/test_runs/rag_architecture_v2/iteration_04/
20260729_153146/baseline.json
```

Результат:

```text
71 cases
43 passed
27 failed
1 blocked
0 not_applicable
```

Новые MR cases: `7/8`; MR08 остаётся failed на final answer/evidence, при этом
его procedure stage выполняется корректно.

Единственный blocked case:

```text
PR01
RagAnswerError: Ollama did not answer within 180 seconds.
```

Это live dependency timeout, а не unit/retrieval defect. Другие 70 cases и
отдельный repeatability run продолжили работу.

## 20. T03 trace до/после

До:

```text
resolved question
→ equipment/procedure QueryPlan
→ prefix marker удалён FTS sanitizer
→ chunk 29 отсутствует в top-10
→ пустой context
→ generic no-answer
```

После:

```text
Как получить новое оборудование?
→ requested_fact_type=procedure
→ procedure_lookup:
   "получение нового оборудования заявка инструкция порядок согласование"
→ chunk 29, rank 3
→ stage hits: procedure_lookup + sibling_lookup
→ context=[]
→ no_answer
```

Обязательный retrieval invariant исправлен. Context/final behavior сознательно
не исправлялись.

## 21. T08 trace до/после

До:

```text
subject=внутренний документооборот
→ общий policy FTS
→ structured records отсутствуют в candidates
```

После:

```text
subject=внутренний документооборот компании
→ organization_function_lookup
→ organization_unit:division_1:2025-12-17, rank 3
→ organization_unit:department_2:2025-12-17, rank 4
→ context: 437, 439, 73
→ strict case passed
```

## 22. T02 candidate trace

Все четыре required rules находятся на ranks 1–4 и имеют sibling provenance.
Context стабильно содержит 72/71/73, но не 74. Strict T02 поэтому остаётся
failed. Потеря локализована после retrieval, в context selection.

## 23. Повторяемость

Отдельный artifact:

```text
data/test_runs/rag_architecture_v2/iteration_04_repeatability/
20260729_170548/baseline.json
```

По три live-повтора выполнены для T02, T03, T06 и T08. Для всех четырёх:

```text
all_available_dimensions_stable = true
retrieval stages = stable
stage queries = stable
required candidate presence/rank = stable
candidate order top-10 = stable
context IDs = stable
requested_fact_type = stable
operational decision = stable
response mode = stable
```

Ranks:

| Case | Required rank(s), все 3 повтора |
|---|---|
| T02 | 1, 2, 3, 4 |
| T03 | chunk 29: 3 |
| T06 | primary route: 2 |
| T08 | structured keys: 3, 4 |

## 24. Full pytest

```text
compileall linehelper tests scripts: success
full pytest: 456 passed in 15.88s
```

Этап 3: 407 passed. Добавлено 49 test items.

## 25. Organization regression

Тот же retrieval-only режим, первые 10 questions:

```text
data/test_runs/organization/iteration_04_multi_stage_retrieval/
20260729_172340/summary.json

8 PASS / 1 PARTIAL / 1 FAIL
runtime_errors = 0
```

Результат полностью совпадает с этапом 3.

## 26. Сравнение с этапом 3

| Метрика | Этап 3 | Этап 4 | Изменение | Причина |
|---|---:|---:|---:|---|
| Full pack | 36/63 | 43/71, 1 blocked | +8 cases | MR01–MR08 |
| Старые 63 | 36 passed | 36 passed, 26 failed, 1 blocked | strict без роста | live analyzer drift + PR01 timeout |
| Original 32 | 22/32 | 23 passed, 8 failed, 1 blocked | +1 passed | T08 |
| Required recall @5 | 25/30 (83,33%) | 38/40 (95%) | +11,67 п.п. | stages + aggregation |
| Required recall @10 | 25/30 (83,33%) | 38/40 (95%) | +11,67 п.п. | stages + aggregation |
| Context inclusion | 16/29 (55,17%) | 19/28 (67,86%) | несопоставимый denominator | selector не менялся |
| Procedure recall | not_available | 21/22 (95,45%) | native | procedure stage |
| Organization function recall | not_available | 5/5 | native | typed doc filters |
| Sibling recall | not_available | 8/8 | native | metadata siblings |
| Provenance coverage | not_available | 1506/1506 | native | candidate model |
| Best-score dedupe | not_available | 781/781 | native | aggregator |
| Multi-turn resolution | 13/13 | 13/13 | без ухудшения | resolved question used |
| False clarification | 0 | 0 | без ухудшения | layer untouched |
| Operational misroutes | 0 | 0 | без ухудшения | operational plans skip semantic |
| Missed operational | 0 | 0 | без ухудшения | layer untouched |
| Full pytest | 407 | 456 | +49 | stage-4 tests |
| Organization smoke | 8/1/1 | 8/1/1 | без ухудшения | legacy retrieval compatible |

Stage-4 context rate не интерпретируется как исправление context selection:
знаменатель изменился из-за fixture и одного blocked case, а candidate ordering
может косвенно менять вход selector.

## 27. Неисправленные T01–T09

- T01: conversation resolution работает; инструкции по КП в KB по-прежнему
  нет.
- T02: четыре candidates есть, context содержит только три.
- T03: required candidate есть на rank 3, но context пуст.
- T04: остаётся evidence/partial-answer problem.
- T05A: в этом run прошёл; context/evidence слой специально не менялся.
- T06: прошёл, primary responsibility route сохранён.
- T07: прошёл, рабочие места не вытеснены generic routes.
- T08: retrieval и strict case прошли.
- T09: retrieval работает, но requested_fact_type остаётся `unknown`.

## 28. Риски и ограничения

1. CV01/CV02 не находят `employee:shkirenkov_roman:2025-12-17` в top-10.
   Это два оставшихся required candidate misses. Compound T07 при этом
   находит запись на rank 2. Нужна следующая общая итерация organization
   relation/authority resolution, а не question-specific boost.
2. Procedure stage намеренно recall-oriented и поднимает несколько
   procedural documents. Precision должен улучшаться в context/evidence
   слое, не скрытым hard filter.
3. Sibling lookup опирается на качество существующего metadata. У legacy
   chunks metadata неполон.
4. PR01 получил единичный Ollama timeout 180 s. Repeatability run не имел
   blocks.
5. Candidate pool может содержать до 30 элементов; latency production DB
   остаётся `p50=78 ms`, `p95=125 ms`, но другие размеры DB требуют
   повторного измерения.
6. MR08 показывает, что наличие процедурных candidates ещё не гарантирует
   корректный knowledge-gap answer. Это evidence-gate concern.
7. Source authority, typed responsibility relations и claim-to-evidence
   attribution не реализованы этим этапом.

## 29. Условия перехода к этапу 5

Переход допустим после ручного review этого отчёта и artifacts:

1. принять native RetrievalPlan и provenance contract;
2. подтвердить T03 rank 3 и T08 ranks 3/4;
3. подтвердить T02 sibling pool;
4. принять сохранённый CV01/CV02 recall gap как отдельную будущую работу;
5. зафиксировать stage 4 отдельным commit;
6. на этапе 5 менять только context selection/coverage, используя
   candidate provenance и не возвращаясь к question-specific retrieval
   правилам.

Предлагаемый commit message:

```text
refactor(rag): добавить многоступенчатый retrieval и provenance кандидатов
```
