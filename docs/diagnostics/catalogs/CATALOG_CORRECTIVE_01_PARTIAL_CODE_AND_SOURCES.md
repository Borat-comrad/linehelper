# CATALOG_CORRECTIVE_01 — partial-code routing и catalog sources

## Итог

Статус: **PASS**.

Технические code-like запросы больше не передаются сразу в FTS. Exact, original/normalized prefix и guarded substring выполняются детерминированно по `parts.part_number_original`/`parts.part_number_normalized`. FTS используется только после пустых code-oriented стадий либо для явно текстового запроса. LLM не участвует в классификации, поиске, ranking или формировании catalog facts.

Catalog sources теперь являются отдельной structured сущностью и не маскируются fake RAG chunks. Streamlit и CLI больше не показывают для catalog route вводящее в заблуждение `Источники ответа (0)`/пустой общий список.

## Git preflight

- branch: `refactor/rag-architecture-v2`;
- HEAD: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`;
- `git diff --check`: clean, кроме информационных LF/CRLF warnings;
- commit/push: не выполнялись.

На preflight уже находился незакоммиченный diff предыдущей итерации Navigation 04:

- `.env.example`;
- `linehelper/catalogs/__init__.py`;
- `linehelper/catalogs/chat.py`;
- `linehelper/catalogs/models.py`;
- `linehelper/catalogs/search.py`;
- `linehelper/cli.py`;
- `linehelper/config.py`;
- `linehelper/llm/answer_generator.py`;
- `linehelper/ui/streamlit_app.py`;
- `docs/diagnostics/catalogs/CATALOG_NAVIGATION_04_REFERENCE_PAGE.md`;
- `linehelper/catalogs/navigation.py`;
- `tests/test_catalog_navigation.py`.

Navigation 04 не сбрасывался и не переименовывался в текущую работу. Текущая итерация точечно продолжает необходимые overlapping files; отдельные новые файлы и test changes перечислены ниже.

## Исходная проблема и root cause

До исправления `Найди в каталоге X44236` вызывал `CatalogSearch.search_parts()`.

`catalog_search_fts` индексирует не только original/normalized part number, но также part name, assembly code/name, equipment/model, machine, revision и hierarchy path. Поэтому FTS/BM25 обоснованно возвращал совпадения по другим полям. Кандидат `301134230120` не начинается с `X44236`, но попадал в top-5 через полнотекстовый контекст.

Это корректное поведение text search, но неверный routing для частичного технического кода.

## Code-like heuristic

Добавлен deterministic helper `is_part_number_like()`.

Он принимает короткие ASCII technical-code формы:

- digits-only длиной от четырёх символов;
- alphanumeric с цифрами и долей цифр не менее 40%;
- разделители whitespace, `.`, `_`, `/`, `-`;
- длину до 40 символов;
- normalization через уже существующий `normalize_part_number()`.

Отдельные длинные alphabetic words и natural-language phrases не считаются кодом. Поэтому `позиция 70 нижней части укупорщика` и `прижимное устройство` остаются текстовыми запросами.

Проверенные code-like примеры: `X44236`, `X44235100`, `20411616`, `0-529-90-026-7`, `0529900267`, `HTD1600-8M-50`, `X 44236`.

## Новый routing order

Для code-like input:

1. existing exact original/normalized lookup;
2. case-sensitive original prefix;
3. normalized prefix;
4. normalized substring только при длине normalized query не менее 6;
5. FTS fallback только при пустом результате всех предыдущих стадий.

Match types:

- `catalog_exact`;
- `catalog_normalized_exact`;
- `catalog_prefix`;
- `catalog_normalized_prefix`;
- `catalog_substring`;
- `catalog_fts`.

Они сохраняются в существующем query diagnostics JSON как `catalog_match_type`; analytics schema не менялась.

Prefix/substring candidates являются уникальными part numbers. Сортировка deterministic:

1. более короткое продолжение query;
2. normalized/original part number;
3. catalog, assembly, source page и source row order как stable context.

FTS candidates никогда не смешиваются с prefix/substring candidates. Original part number в результате не изменяется.

## Exact и partial responses

Exact match сохраняет authoritative flow `part -> all BOM usages -> assembly -> position -> quantity -> page -> catalog` и не вызывает candidate/FTS stages.

Prefix response явно сообщает `По частичному коду ...`; substring — `По фрагменту кода ...`. Только FTS fallback сообщает, что exact/partial отсутствуют и показаны полнотекстовые кандидаты.

Минимальная длина substring `6` выбрана как защита от широкого scan для `44`, `10`, `123`. Prefix stage также не запускается для normalized query короче четырёх символов.

## Catalog source attribution

Добавлена отдельная structured модель `CatalogSource`:

- part number;
- assembly;
- position;
- source/reference page;
- PDF filename;
- revision;
- machine number;
- catalog id.

Flow:

`CatalogPartResult -> CatalogChatOutcome.catalog_sources -> RagAnswer.catalog_sources -> CLI/Streamlit`.

`RagAnswer.sources` остаётся списком RAG sources и не заполняется фиктивными catalog chunks.

Streamlit:

- catalog result получает отдельный expander `Источники каталога (N)`;
- exact occurrence показывает PDF, BOM page, revision, machine, assembly и position;
- пустой `Источники ответа (0)` для catalog-only result не рендерится;
- если когда-либо catalog result одновременно получит настоящие RAG sources, они будут показаны отдельно как `Источники RAG`.

CLI аналогично выводит `Источники каталога`, а debug — `catalog match type`.

## Real Catalog Store smoke

### A. Exact `X44235100`

- route: `catalog_exact_lookup`;
- match type: `catalog_exact`;
- usages: 1;
- assembly: `X44236986`;
- position: `70`;
- quantity: `4.000 шт`;
- source page: `467`;
- FTS/candidate stages: not invoked (covered by deterministic test).

### B. Partial `X44236`

- route: `catalog_code_search`;
- match type: `catalog_prefix`;
- candidates: `X44236986` only;
- `301134230120`: absent.

### C. Normalized partial `x44236`

- route: `catalog_code_search`;
- match type: `catalog_normalized_prefix`;
- candidates: `X44236986` only.

Дополнительно `X-44236` и `X 44236` дали тот же normalized prefix result.

### D. Substring `442351`

- match type: `catalog_substring`;
- candidates: `X44235100`, `X44235103`, `X44235105`, `X44235108`.

### E. Text FTS `прижимное устройство`

- route: `catalog_text_search`;
- match type: `catalog_fts`;
- top candidates: `58803877S002`, `58803842S058`, `H29205010648`, `20411617`, `20394873`.

### F. Code miss `X99999`

- exact/prefix/normalized-prefix/substring: empty;
- FTS: empty;
- controlled `no_candidates`, без hallucination.

## Streamlit live smoke

Exact `X44235100`:

- exact answer rendered;
- `Источники каталога (1)` rendered;
- PDF `ETL_89401221_000400__Innofill_RU_05.pdf` rendered;
- `Страница BOM: 467`, `Ревизия: 05`, `Машина: 47592` rendered;
- `Источники ответа (0)` absent.

Partial `Найди в каталоге X44236`:

- partial-code label rendered;
- `X44236986` present;
- `301134230120` absent;
- catalog source block present;
- empty RAG source counter absent.

## Production changes текущей итерации

- `linehelper/catalogs/models.py`;
- `linehelper/catalogs/search.py`;
- `linehelper/catalogs/chat.py`;
- `linehelper/llm/answer_generator.py`;
- `linehelper/cli.py`;
- `linehelper/ui/streamlit_app.py`.

Tests/docs:

- `tests/test_catalog_corrective.py` — 8 новых test items;
- `tests/test_catalog_text_chat_integration.py` — обновлён прежний fragment-routing expectation;
- этот отчёт.

Parser/import/schema/indexing data, Catalog DB, QueryAnalyzer, EvidenceAssessor, ContextComposer и основной RAG не менялись.

## Tests

- targeted catalog/search/chat/UI/answer-generator: **107 passed**;
- full `pytest tests scripts`: **577 passed**;
- `python -m compileall linehelper tests scripts`: **PASS**.

Один `PytestCacheWarning` связан только с запретом записи `.pytest_cache`; test failures отсутствуют.

Не запускались отдельные live 71-case architecture, organization и Ollama packs.

## Ограничения

- code-like heuristic сознательно ориентирован на короткие ASCII technical codes; расширение на другие алфавиты требует реальных catalog examples;
- substring — literal normalized containment, без fuzzy/edit-distance/typo correction;
- partial/FTS source является representative BOM occurrence кандидата; все authoritative usages по-прежнему получаются только следующим exact lookup;
- FTS ranking/index schema не менялись.

