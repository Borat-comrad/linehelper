# Catalog Corrective 04 — structured catalog intent routing

## Статус

PASS. Явные запросы `узел / деталь / позиция / состав узла` обрабатываются
детерминированно до natural probe, QueryAnalyzer и corporate RAG.

Branch: `refactor/rag-architecture-v2`  
HEAD на старте: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`

Рабочее дерево уже содержало незакоммиченные изменения предыдущих Catalog
итераций. Они не откатывались. Production-файлы, затронутые именно этой
corrective iteration:

- `linehelper/catalogs/models.py`;
- `linehelper/catalogs/search.py`;
- `linehelper/catalogs/chat.py`;
- `linehelper/llm/answer_generator.py`.

Дополнительно добавлены `tests/test_catalog_structured_routing.py`, семь cases
для следующей evaluation в `scripts/evaluate_catalog_user_flow.py` и gate tests.

## Root cause

`CatalogChatService.lookup()` умел распознавать part-number и явный FTS
request. `CatalogSearch.find_assembly_parts()` уже существовал, но assembly и
assembly-scoped position не были chat intents. Поэтому `Узел: 20411617` не
получал deterministic outcome и доходил до corporate RAG.

## Новый deterministic order

1. `extract_structured_catalog_intent()`;
2. exact assembly / part / assembly-position lookup;
3. bare-code dual entity lookup;
4. прежний exact/normalized/partial code path;
5. explicit/natural Catalog FTS;
6. mixed/corporate RAG.

## Structured routes

| Intent | Route | Search API |
| --- | --- | --- |
| `узел CODE` | `catalog_assembly_exact` | `find_assemblies_by_code` + `find_assembly_parts` |
| `состав/что входит ...` | `catalog_assembly_contents` | exact assembly + ordered BOM |
| `позиция POS узла CODE` | `catalog_bom_position` | `find_assembly_position(CODE, POS)` |
| `деталь/где используется CODE` | `catalog_part_exact` | existing `find_part_by_number` |
| ambiguous bare code | `catalog_entity_ambiguity` | assembly exact + part exact |

Позиция всегда scoped по `assembly_code`; глобального поиска по position нет.
Part normalization остаётся в существующем Catalog Search. FTS и LLM не
участвуют в structured exact routes.

## Real Catalog Store smoke

| Query | Route | Result | Corporate sources |
| --- | --- | --- | ---: |
| `Узел: 20411617` | `catalog_assembly_exact` | 1 assembly, 9 BOM rows | 0 |
| `что входит в узел 20411617` | `catalog_assembly_contents` | 9 BOM rows | 0 |
| `состав узла 20411617` | `catalog_assembly_contents` | 9 BOM rows | 0 |
| `позиция 20 узла 20411617` | `catalog_bom_position` | `58803877S002`, qty 3, page 67, ref 74 | 0 |
| `деталь X44235100` | `catalog_part_exact` | assembly `X44236986`, pos 70, qty 4, page 467 | 0 |
| `где используется X44235100` | `catalog_part_exact` | all usages (1 in current DB) | 0 |
| `20411617` | `catalog_entity_ambiguity` | assembly + child/part usage in `10307585`, pos 300 | 0 |

Каждый result содержит только свой structured retrieval stage; corporate
retrieval не запускается.

## Ambiguous bare code

`20411617` существует одновременно как assembly и как part/child occurrence.
Ответ теперь показывает обе роли и предлагает явное уточнение. Неambiguous
голый part number сохраняет прежний `catalog_exact_lookup`, чтобы не менять
существующий exact contract.

## Tests

- Structured targeted tests: 16 passed (до добавления всех phrasing variants).
- Catalog/chat regression slice: 111 passed.
- Full `pytest tests scripts`: 623 passed.
- `python -m compileall linehelper tests scripts`: PASS.
- Единственное предупреждение: pytest не смог создать `.pytest_cache` из-за
  WinError 5; test outcomes не затронуты.

## Следующая evaluation

Завершённый 37-case Evaluation 01 не изменён и не перезапущен. В runner добавлен
отдельный `--case-set next`, который добавляет H01–H07 и считает acceptance gate:

- FAIL ≤ 2;
- routing accuracy ≥ 93%;
- natural catalog ≥ 5/6;
- mixed ≥ 4/5;
- corporate regression = 5/5;
- false catalog routes = 0;
- structured catalog intents = 7/7;
- structured catalog → corporate leakage = 0.

## Scope

Parser, Catalog DB/schema/data, FTS ranking, part normalization, QueryAnalyzer,
EvidenceAssessor, corporate retrieval и 1C не изменялись. Commit/push не
выполнялись.
