# CATALOG_CORRECTIVE_03 — decomposition составных Catalog + RAG запросов

## Итог

Статус: **PASS**.

Составной запрос теперь раскладывается на независимо удовлетворяемые
source requirements. Если Catalog Store нашёл объект, а корпоративная база не
подтвердила процедуру, LineHelper возвращает partial mixed answer с реальными
BOM-фактами и явным сообщением об отсутствующей процедуре — без общего отказа.

Commit и push не выполнялись.

## Git preflight

- Branch: `refactor/rag-architecture-v2`
- HEAD: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`
- HEAD subject: `feat(catalogs): add catalog search and chat integration`
- Рабочее дерево до Corrective 03 уже содержало незакоммиченные Navigation 04,
  Corrective 01 и Corrective 02. Reset/restore/checkout не выполнялись.

Изменения этой итерации находятся в уже изменённых ранее файлах:

- `linehelper/catalogs/models.py`;
- `linehelper/catalogs/chat.py`;
- `linehelper/llm/answer_generator.py`;
- `linehelper/cli.py`;
- `linehelper/ui/streamlit_app.py`;
- `tests/test_catalog_natural_routing.py`;
- новый `tests/test_catalog_mixed_decomposition.py`;
- этот отчёт.

Parser, Catalog Store schema/DB, BOM data, QueryAnalyzer, EvidenceAssessor,
ContextComposer и 1C integration не менялись.

## Исходная диагностика

Исходный запрос прогнан через публичный CLI, использующий тот же
`RagAnswerGenerator`, что и Streamlit:

```text
что есть по прижимному устройству и как его обслуживать
```

Фактический старый flow:

```text
CatalogChatService.lookup(): None
Catalog natural probe: performed, 5 FTS results
probe query: "прижимному устройству как его"
field coverage: 0.5
strong catalog outcome: no
source_route: corporate
QueryAnalyzer requested_fact_type: procedure
corporate retrieval
EvidenceDecision: insufficient_evidence
generic no-answer
```

Полная исходная трасса:

- normalized question:
  `Что есть по прижимному устройству и как его обслуживать?`;
- intent/raw intent: `equipment_it_request`;
- requested fact type: `procedure`;
- subject: `прижимное устройство`;
- query expansions:
  `что такое прижимное устройство`,
  `как обслуживать прижимное устройство`;
- catalog probe performed: `true`;
- catalog result count: `5`;
- top score: `-5.818996279863384`;
- top structured-field coverage: `0.5`;
- coherent catalog results: `0`;
- source route: `corporate`;
- corporate retrieval stages: `procedure_lookup`, `exact_subject`,
  FTS по resolved/normalized/expansion queries, `sibling_lookup`;
- retrieval candidates: 50 before / 37 after dedupe;
- context: 3 chunks / 5184 chars;
- EvidenceDecision: `insufficient_evidence`;
- supported requirements: none;
- unsupported requirement: `primary_procedure`;
- answer mode: `insufficient_evidence`.

Точная точка потери: procedural boilerplate `как его` оставался частью Catalog
probe query. Catalog FTS возвращал правильные записи, но лишние слова снижали
literal field coverage с 1.0 до 0.5. Probe сохранял только diagnostics и
`outcome=None`. Затем единственный `requested_fact_type=procedure` определял
corporate evidence plan, поэтому найденные catalog candidates уже не были
доступны answer stage.

## Decomposition информационных потребностей

Введена минимальная structured модель `SourceRequirement`. Она не заменяет
QueryPlan и не выполняет анализ сама. Catalog probe создаёт межисточниковые
requirements, а существующий QueryAnalyzer нормализует subject:

```text
catalog_entity_information
  subject = прижимное устройство
  source = catalog

maintenance_procedure
  subject = прижимное устройство
  source = corporate
```

Для текущего реального запроса итоговые статусы:

```json
[
  {
    "requirement_id": "catalog_entity_information",
    "subject": "прижимное устройство",
    "source": "catalog",
    "status": "found"
  },
  {
    "requirement_id": "maintenance_procedure",
    "subject": "прижимное устройство",
    "source": "corporate",
    "status": "not_found"
  }
]
```

`requested_fact_type=procedure` по-прежнему управляет только существующей
корпоративной retrieval/evidence цепочкой и больше не перекрывает catalog
requirement.

## Subject extraction и местоимения

Catalog probe детерминированно исключает из предметной части общий procedural
boilerplate: вопросительные слова, conjunction context, обслуживание, ремонт,
замену, настройку и соответствующие referential pronouns. Это не список
названий assemblies/parts и не привязка к Innofill.

Текущий запрос даёт FTS query:

```text
прижимному устройству
```

После QueryAnalyzer requirement subject нормализуется до:

```text
прижимное устройство
```

Normalization QueryAnalyzer принимается только если не добавляет concepts,
которых не было в probe query. Это защищает catalog subject от LLM-добавлений.

Проверен аналог:

```text
что известно про наполнитель и как его обслуживать
```

Результат: `source_route=mixed`, Catalog FTS query `наполнитель`, 5 реальных
matches, catalog requirement `found`, procedure requirement `not_found`.
Referential pronoun относится к извлечённому предмету, а не становится частью
Catalog FTS query.

## Новый mixed flow

```text
user question
  → exact / explicit catalog routing
  → natural catalog probe + source requirements
  → QueryAnalyzer (один существующий вызов)
  → corporate retrieval for procedure requirement
  → existing EvidenceAssessor
  ├─ catalog found + procedure found
  │    → full mixed answer
  ├─ catalog found + procedure missing
  │    → partial mixed answer + catalog sources
  ├─ catalog missing + procedure found
  │    → partial mixed answer + document sources
  └─ both missing
       → insufficient evidence
```

Дополнительного LLM-вызова для decomposition или Catalog Search нет. Второго
answer-generation call нет. Catalog part numbers и BOM facts рендерятся
детерминированно.

## Partial mixed behavior

Реальный результат текущего запроса:

```text
source_route: mixed
catalog_probe_performed: true
catalog_result_count: 5
catalog_top_field_coverage: 1.0
catalog_coherent_results: 5
catalog_requirement_status: found
corporate_requirement_status: not_found
answer_mode: partial_mixed
EvidenceDecision: insufficient_evidence (только procedure requirement)
```

Catalog section содержит:

1. `58803877S002` — прижимное устройство; assembly `20411617`, position 20,
   quantity 3 шт, BOM page 67;
2. `58803842S058` — предохранитель/прижимное устройство; assembly `20411617`,
   position 40, quantity 1 шт, BOM page 67;
3. `H29205010648` — труба/прижимное устройство; assembly `20411617`,
   position 60, quantity 157 шт, BOM page 67;
4. `20411617` — прижимное устройство; assembly `10307585`, position 300,
   quantity 1 шт, BOM page 9;
5. `20394873` — инструмент/прижимное устройство; assembly `20411672`,
   position 10, quantity 1 шт, BOM page 659.

После structured facts выводится:

```text
В доступной базе документов не найдено подтверждённой процедуры обслуживания
этого устройства.
```

Generic `В найденных источниках недостаточно данных для ответа` не выводится.

Обратный случай покрыт automated integration fixture: Catalog Store не нашёл
объект, corporate procedure подтверждена. Возвращается grounded procedure с
document sources и явное сообщение, что сведения о составе/деталях Catalog
Store не найдены.

## Full mixed behavior

Synthetic grounded fixture подтверждает:

- `source_route=mixed`;
- catalog requirement `found`;
- corporate requirement `found`;
- `answer_mode=full_mixed`;
- response kind `mixed_answer`;
- catalog section содержит только structured Catalog Store facts;
- corporate section создаётся существующим grounded rendering;
- один набор `catalog_sources` и отдельный набор document/RAG `sources`.

## Source attribution и Streamlit

Для mixed answer UI различает:

- `Источники каталога`;
- `Источники документов`.

Если corporate evidence отсутствует, пустой document/RAG expander вообще не
рендерится. Fake RAG chunks не создаются.

Live Streamlit smoke текущего запроса подтвердил:

- 5 structured BOM candidates;
- сообщение об отсутствующей процедуре;
- `Источники каталога (5)` с PDF, BOM/reference pages, revision и machine;
- нет `Источники ответа (0)`;
- нет пустого блока `Источники документов`.

CLI также печатает оба блока независимо, когда full mixed answer содержит оба
вида provenance.

## Diagnostics

В существующий `query_plan` diagnostics JSON добавлены/сохранены:

- `source_route`;
- `catalog_probe_performed`;
- `catalog_probe_result_count` / `catalog_result_count`;
- `catalog_match_type`;
- `catalog_top_score`;
- `catalog_top_field_coverage`;
- `catalog_probe_coherent_results`;
- `catalog_requirement_status`;
- `corporate_requirement_status`;
- `resolved_requirements`;
- `answer_mode`;
- `corporate_evidence_available`.

Analytics schema не менялась.

## Real smoke

| Query | Route | Catalog | Corporate | Mode |
|---|---|---:|---|---|
| `что есть по прижимному устройству и как его обслуживать` | mixed | 5 / found | not found | partial mixed |
| `что ты знаешь про прижимное устройство` | catalog | 5 / found | not requested | catalog candidates |
| `прижимное устройство` | catalog | 5 / found | not requested | catalog candidates |
| `можно ли дать распоряжение устно` | corporate | not requested | 3 supporting chunks | full answer |
| `X44235100` | catalog exact | 1 usage | not requested | authoritative exact |

Corporate regression сохранил intent `order_disposition`, requested fact type
`procedure`, EvidenceDecision `full_answer`, supporting chunks 68/70/67.

Exact regression сохранил `catalog_exact`; FTS/natural probe не выполнялся.

## Tests

Добавлено не более разрешённого бюджета: 8 test items в
`tests/test_catalog_mixed_decomposition.py`. Они покрывают:

1. текущий compound query → mixed;
2. Catalog found + corporate missing → partial mixed;
3. catalog sources и отсутствие пустого RAG counter;
4. full mixed both-found;
5. pure catalog / corporate / exact regressions;
6. weak corporate compound guard;
7. обратный partial mixed;
8. pronoun/subject resolution для `наполнитель`.

Также обновлена одна ожидаемая семантика Corrective 02: maintenance query при
отсутствующей procedure теперь `partial_mixed_answer`, а не catalog-only.

Targeted catalog/chat/RAG/UI tests:

```text
130 passed in 1.67s
```

Full project tests:

```text
pytest tests scripts -q
592 passed in 25.64s
```

Compile:

```text
python -m compileall linehelper tests scripts
PASS
```

Pytest показал только существующий `PytestCacheWarning`: WinError 5 при
создании `.pytest_cache`. Test failures отсутствуют.

71-case architecture pack и organization pack не запускались.

## Известные ограничения

- Decomposition намеренно ограничена сочетанием catalog entity information и
  maintenance/procedure document need; это не универсальный multi-intent NLP.
- Procedural signal определяется консервативно и детерминированно; сложные
  длинные формулировки могут остаться corporate.
- QueryPlan всё ещё имеет один `requested_fact_type`; независимые
  межисточниковые requirements хранятся в routing diagnostics. Это минимальное
  изменение без перестройки analyzer/evidence architecture.
- Catalog Store сообщает BOM-факты, но не функциональное назначение узла.
- Реальный Innofill smoke не нашёл процедуры обслуживания в corporate memory;
  full mixed и обратный partial mixed проверены синтетическими grounded fixtures.

## Рекомендация

Следующим шагом собирать interaction feedback для `mixed` и расширять только
общие procedural/decomposition patterns. Не добавлять названия конкретных
узлов, деталей или каталогов в routing rules.

Рекомендуемый commit message:

```text
fix(catalogs): поддержать составные catalog и RAG запросы
```
