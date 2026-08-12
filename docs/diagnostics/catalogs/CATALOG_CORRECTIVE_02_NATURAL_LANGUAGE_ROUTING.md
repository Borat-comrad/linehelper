# CATALOG_CORRECTIVE_02 — Natural-language catalog routing

## Итог

Статус: **PASS**.

Обычный chat flow LineHelper теперь выполняет консервативный deterministic
Catalog FTS probe для коротких предметных запросов. Сильный catalog match
возвращает structured BOM-кандидатов до запуска corporate RAG. Exact и
code-like пути остались приоритетными. Слабое одиночное FTS-совпадение не
перехватывает корпоративный запрос.

Commit и push не выполнялись.

## Git preflight

- Branch: `refactor/rag-architecture-v2`
- HEAD: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`
- HEAD subject: `feat(catalogs): add catalog search and chat integration`
- `git diff --check`: ошибок whitespace нет; Git сообщает только ожидаемые
  предупреждения о будущем LF → CRLF в рабочей копии.

До начала Corrective 02 рабочее дерево уже содержало незакоммиченные изменения
Navigation 04 и Corrective 01. Они сохранены и не присвоены этой итерации:

- `.env.example`
- `linehelper/catalogs/__init__.py`
- изменения navigation/source rendering в `linehelper/catalogs/chat.py`,
  `models.py`, `search.py`, `cli.py`, `answer_generator.py`,
  `streamlit_app.py`, `config.py`;
- `linehelper/catalogs/navigation.py`;
- `tests/test_catalog_corrective.py`;
- `tests/test_catalog_navigation.py`;
- `tests/test_catalog_text_chat_integration.py`;
- отчёты `CATALOG_CORRECTIVE_01_PARTIAL_CODE_AND_SOURCES.md` и
  `CATALOG_NAVIGATION_04_REFERENCE_PAGE.md`.

Текущая итерация меняла только natural-language routing внутри уже изменённых
`chat.py`, `models.py`, `search.py`, `answer_generator.py`, `cli.py`, добавила
`tests/test_catalog_natural_routing.py` и этот отчёт.

## Диагностика исходной проблемы

Запрос проверен через публичный CLI, использующий тот же
`RagAnswerGenerator`, что и Streamlit:

```text
что ты знаешь про прижимное устройство
```

Flow до исправления:

```text
Streamlit / CLI
  → RagAnswerGenerator
  → CatalogChatService.lookup(): None
  → QueryAnalyzer
  → corporate retrieval
  → EvidenceAssessor: insufficient_evidence
  → generic no-answer
```

Фактическая исходная диагностика:

- QueryAnalyzer intent: `equipment_it_request`;
- raw intent: `equipment_it_request`;
- requested fact type: `procedure`;
- subject: `программное обеспечение компании`;
- normalized question: `что ты знаешь про программное обеспечение компании?`;
- catalog detection: отсутствовала;
- CatalogSearch: не вызывался;
- corporate retrieval: 81 кандидатов до дедупликации, 50 после;
- выбранные retrieval stages относились к procedure/exact/FTS corporate RAG;
- EvidenceAssessor: `insufficient_evidence`, supporting chunks = 0;
- финал: общий ответ о недостатке данных и пустой RAG source block.

Root cause: до QueryAnalyzer существовала только маршрутизация exact/code-like
и явных catalog-команд. Natural-language catalog candidate probe отсутствовал.
Поэтому ошибочная классификация QueryAnalyzer становилась окончательной, хотя
Catalog Store имел сильные структурированные совпадения.

## Новый flow

```text
user message
  → RagAnswerGenerator
  → CatalogChatService.lookup()
      exact / normalized exact / prefix / substring / explicit catalog text
  → если явного маршрута нет: probe_natural_language()
  → CatalogSearch.search_parts()
  → lexical field-coverage gate
  ├─ strong catalog → structured catalog answer
  ├─ strong mixed → corporate retrieval + catalog result
  └─ weak/absent → unchanged corporate RAG
```

Exact и code-like lookup выполняются до natural probe. Их normalization,
ordering и authoritative usages не менялись.

## Source routing

Поддерживаются диагностические значения:

- `catalog` — сильные structured matches, процедурной составляющей нет;
- `corporate` — probe не нужен, пуст или недостаточно согласован;
- `mixed` — запрос одновременно содержит предмет каталога и явный сигнал
  обслуживания/ремонта/замены/настройки/инструкции.

Для каждого probe сохраняются в существующем `query_plan` diagnostics JSON:

- `source_route`;
- `catalog_probe_performed`;
- `catalog_probe_result_count`;
- `catalog_match_type`;
- `catalog_top_score`;
- `catalog_top_field_coverage`;
- `catalog_probe_coherent_results`;
- `corporate_evidence_available`.

Отдельная analytics schema не создавалась.

## Catalog probe rules

Probe применяется только к коротким запросам: от 1 до 6 содержательных
терминов длиной не менее 3 символов. Служебные слова (`что`, `знаешь`,
`покажи`, `какие`, `есть`, `детали` и т. п.) удаляются детерминированно.

FTS сначала получает полную очищенную фразу. Если из-за русской флексии
phrase search не дал результатов, выполняется ограниченный term-prefix probe.
Для этого используется небольшой общий suffix stemmer; конкретные названия
деталей, assemblies, термины Innofill и part numbers не хардкодируются.

Результат оценивается не только BM25. Проверяется буквальное покрытие терминов
в structured fields:

- `part_name`;
- `assembly_code`;
- `assembly_name`;
- `equipment_model`.

Strong match требует coverage ≥ 0.75 и дополнительно:

- минимум два согласованных результата; либо
- полное покрытие многословного запроса одним structured result.

Одиночное совпадение по одному слову не маршрутизирует запрос в каталог.
BM25 score сохраняется только как диагностика и не трактуется как процент
уверенности.

## False-positive protection

Запросы с явной корпоративной семантикой ответственности, документооборота,
распоряжений, регламентов и аналогичных процедур не запускают natural catalog
probe, если одновременно нет mixed equipment-maintenance signal.

Автоматический тест отдельно подтверждает, что одиночный FTS result
`шайба` с полным односоставным lexical coverage остаётся `corporate`, а не
становится catalog route.

## Fallback и mixed behavior

- Strong `catalog`: corporate retrieval и LLM не запускаются.
- Weak/empty probe: существующий corporate flow идёт без изменений.
- Strong `mixed`: сохраняются catalog candidates и запускается corporate
  retrieval.
- Corporate evidence supported: ответ содержит отдельные catalog и corporate
  секции и оба вида sources.
- Corporate evidence insufficient: structured catalog result возвращается
  вместо generic insufficient-evidence, а corporate diagnostics сохраняются.

LLM не выбирает catalog part numbers и не меняет BOM facts. Для чистого
catalog route ответ рендерится детерминированно без LLM. В mixed route LLM
используется только существующим corporate grounded rendering.

## Current example: before / after

До:

```text
source_route: corporate
requested_fact_type: procedure
CatalogSearch called: no
corporate evidence: insufficient
answer: В найденных источниках недостаточно данных...
```

После:

```text
query: прижимное устройство
source_route: catalog
catalog_probe_performed: true
catalog_probe_result_count: 5
catalog_match_type: catalog_fts
catalog_top_field_coverage: 1.0
corporate_evidence_available: false
```

Первые реальные candidates:

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

Ответ явно ограничен BOM-фактами и сообщает, что функциональное назначение
узла каталог не описывает.

## Real smoke через публичный chat flow

| Query | Source route | Catalog | Corporate evidence | Answer mode |
|---|---|---:|---|---|
| `что ты знаешь про прижимное устройство` | catalog | 5 | false | catalog candidates |
| `найди прижимное устройство` | catalog | 5 | false | catalog candidates |
| `верхняя часть наполнителя` | catalog | 5 | false | catalog candidates |
| `X44235100` | catalog exact | 1 usage | false | authoritative exact |
| `кто отвечает за документооборот` | corporate | probe skipped | true | grounded corporate answer |
| `можно ли дать распоряжение устно` | corporate | probe skipped | true | grounded corporate answer |

Детали regression smoke:

- `верхняя часть наполнителя`: top candidate `20411640`; остальные candidates
  принадлежат assembly `20411640`, BOM pages 9/81.
- `X44235100`: exact, assembly `X44236986`, position 70, quantity 4 шт,
  BOM page 467; FTS probe не выполнялся.
- `кто отвечает за документооборот`: intent `roles_responsibility`,
  requested fact type `responsible_person`, 2 supporting corporate sources.
- `можно ли дать распоряжение устно`: intent `order_disposition`, requested
  fact type `procedure`, 3 supporting corporate sources.

## Streamlit smoke

В production Streamlit UI отправлен:

```text
что ты знаешь про прижимное устройство
```

Проверено:

- без специальной catalog-команды показаны 5 structured BOM candidates;
- показан отдельный блок `Источники каталога (5)`;
- для каждого источника видны PDF, BOM page, revision и machine;
- `Источники ответа (0)` / пустой RAG source counter отсутствует;
- RAG chunks не создавались искусственно.

## Production changes Corrective 02

- `linehelper/catalogs/models.py` — structured `CatalogProbeDiagnostics`;
- `linehelper/catalogs/chat.py` — deterministic natural probe, lexical gate,
  Russian inflection fallback и structured catalog candidate renderer;
- `linehelper/catalogs/search.py` — безопасный Unicode single-token prefix
  MATCH для term-prefix probe;
- `linehelper/llm/answer_generator.py` — catalog/corporate/mixed routing,
  insufficient-evidence fallback и diagnostics;
- `linehelper/cli.py` — вывод source-route diagnostics в debug mode.

Parser, Catalog Store schema, DB, MemoryStore, 1C flow, EvidenceAssessor и
ContextComposer не менялись.

## Tests

Добавлен `tests/test_catalog_natural_routing.py`: 7 test items, покрывающих:

1. natural catalog phrases;
2. exact-code priority;
3. corporate protected route;
4. weak single-result rejection;
5. corporate-insufficient catalog fallback;
6. structured catalog sources/no empty RAG counter;
7. mixed catalog + corporate evidence.

Targeted catalog/search/chat/RAG/UI suite:

```text
122 passed in 1.67s
```

Full project suite:

```text
pytest tests scripts -q
584 passed in 24.94s
```

Compile:

```text
python -m compileall linehelper tests scripts
PASS
```

Оба pytest-запуска показали только `PytestCacheWarning`: процесс не смог
создать `.pytest_cache` из-за WinError 5. Это не test failure и не изменение
production behavior.

71-case live architecture pack, organization pack и Ollama live pack не
запускались.

## Known limitations

- Это консервативная lexical routing policy, не semantic classifier.
- Probe ограничен короткими запросами; длинный сложный вопрос остаётся
  corporate/mixed responsibility будущего source router.
- Русская нормализация намеренно минимальна и не является полноценным
  морфологическим анализатором.
- Catalog candidate answer содержит только подтверждённые BOM facts и не
  объясняет назначение оборудования без отдельного corporate evidence.
- Mixed route реализован без новой agent architecture и без изменения 1C.

## Recommendation

Следующий шаг — собрать interaction analytics по `source_route`, coverage и
ручному feedback, затем калибровать только общие thresholds/route signals на
реальных запросах. Не добавлять названия конкретных деталей в routing rules.

Рекомендуемый commit message:

```text
fix(catalogs): подключить естественные запросы к поиску по каталогам
```
