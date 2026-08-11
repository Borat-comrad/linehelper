# CATALOG_INTEGRATION_03 — явный текстовый поиск каталога из чата

Итоговый статус: **PASS**.

Дата проверки: 2026-08-11. Commit и push не выполнялись.

## 1. Git preflight

Фактическое состояние проверено до изменения файлов:

- branch: `refactor/rag-architecture-v2`;
- HEAD: `004b43372713ef750f0ed37e5951bd14c127811b`;
- HEAD subject: `feat(analytics): добавить логирование взаимодействий и feedback`.

Таким образом, указанное в предыдущем отчёте значение `004b433` действительно осталось актуальным. Новый Catalog commit в текущем working tree отсутствовал.

Staged до этой итерации:

```text
.gitignore
docs/diagnostics/catalogs/CATALOG_AUDIT_01_INNOFILL_REV05_SEMANTIC_VALIDATION.md
docs/diagnostics/catalogs/CATALOG_CORRECTION_01_REFERENCE_PAGE.md
docs/diagnostics/catalogs/CATALOG_PILOT_01_INNOFILL_REV05_IMPORT.md
docs/diagnostics/catalogs/KHS_ETL_INNOFILL_REV05_LAYOUT_ANALYSIS.md
linehelper/catalogs/__init__.py
linehelper/catalogs/indexing.py
linehelper/catalogs/khs_etl_parser.py
linehelper/catalogs/models.py
linehelper/catalogs/search.py
linehelper/catalogs/store.py
linehelper/catalogs/validation.py
scripts/import_spare_parts_catalog.py
scripts/search_spare_parts_catalog.py
tests/test_catalog_store.py
```

Unstaged до этой итерации:

```text
.env.example
linehelper/analytics/serialization.py
linehelper/catalogs/__init__.py
linehelper/catalogs/models.py
linehelper/catalogs/search.py
linehelper/cli.py
linehelper/config.py
linehelper/llm/answer_generator.py
linehelper/ui/streamlit_app.py
scripts/search_spare_parts_catalog.py
```

Untracked до этой итерации:

```text
docs/diagnostics/catalogs/CATALOG_INTEGRATION_01_EXACT_CHAT_LOOKUP.md
docs/diagnostics/catalogs/CATALOG_SEARCH_02_FTS_RELIABILITY.md
linehelper/catalogs/chat.py
tests/test_catalog_chat_integration.py
tests/test_catalog_fts_reliability.py
```

Это незакоммиченные результаты предыдущих Catalog Pilot/Audit/Correction/Exact Integration/FTS Reliability итераций и сопутствующие integration changes. Baseline списки сохранены; reset/restore/checkout не выполнялись. Текущая итерация изменила только два уже существовавших production-файла и добавила отдельные test/report files, поэтому provenance diff остаётся определимым.

## 2. Chat architecture до изменения

Поток пользовательского сообщения:

```text
Streamlit или CLI
  → RagAnswerGenerator.answer()
  → CatalogChatService.lookup()
  → если exact intent найден: CatalogSearch.find_part_by_number()
  → иначе ConversationResolver / QueryAnalyzer / RAG / 1C flow
  → RagAnswer
  → универсальный renderer Streamlit или CLI
```

`CatalogChatService` распознавал exact-команды регулярными выражениями (`Найди`, `Что известно о`, `Где используется`, `Покажи деталь`) и узким правилом bare-code. Решение о каталоге принималось в `RagAnswerGenerator` до conversation resolver, QueryAnalyzer, retrieval и LLM. Structured `CatalogChatOutcome` преобразовывался в `RagAnswer.catalog`, а `response_kind` различал exact found/not-found/unavailable.

Streamlit хранит сообщения и metadata в `st.session_state`, но это состояние предназначено для истории и RAG clarification. Отдельного catalog candidate selection state не было. CLI interactive mode также хранит только общую историю. Streamlit и CLI уже отображали любой `result.answer` без специального catalog renderer; technical UI показывает `result.catalog/query_plan`.

## 3. Точка интеграции

Расширен существующий `CatalogChatService`, новый router/tool/service не создавался. Порядок внутри `lookup()`:

1. существующий exact extraction;
2. если exact не распознан — deterministic explicit catalog text-search extraction;
3. если ни один catalog intent не распознан — вернуть `None` и продолжить прежний RAG/1C flow.

FTS route вызывает только существующий `CatalogSearch.search_parts(query, limit=5)`. SQL, MATCH builder, normalization и BM25 ranking в chat layer не дублируются.

## 4. Exact vs FTS contract

| Пользовательский сценарий | Route | Метод | Семантика |
|---|---|---|---|
| `Найди X56767951` | `catalog_exact_lookup` | `find_part_by_number()` | authoritative occurrences |
| `Найди НЕСУЩЕСТВУЮЩИЙ_ПОЛНЫЙ_КОД` | `catalog_exact_lookup` | `find_part_by_number()` | exact not found, без FTS fallback |
| `Найди в каталоге X44236` | `catalog_text_search` | `search_parts(..., limit=5)` | ranked candidates |
| `Найди в каталоге клапан отбора` | `catalog_text_search` | `search_parts(..., limit=5)` | ranked candidates |

Exact lookup остаётся первым и authoritative. Exact miss никогда не вызывает FTS автоматически. Text search всегда маркирует результат как кандидатов и не объявляет первый candidate подтверждённой деталью.

## 5. Поддерживаемые explicit FTS intents

- `найди в каталоге ...`;
- `поищи в каталоге ...`;
- `поиск по каталогу: ...`;
- `найди деталь по описанию ...`;
- `поищи детали ...`.

Intent определяется локальными deterministic regex, без QueryAnalyzer и LLM. Свободные вопросы без этих формулировок не перехватываются.

## 6. Query extraction

Из команды удаляется только фиксированный intent prefix, необязательное двоеточие, крайние пробелы и терминальная пунктуация. Внутренние слова запроса сохраняются.

```text
Найди в каталоге газоанализатор кислорода
→ газоанализатор кислорода

Поиск по каталогу: шаровой шарнир
→ шаровой шарнир

Поищи детали X44236
→ X44236
```

Пустая команда `Поиск по каталогу:` не вызывает MATCH и возвращает controlled clarification с просьбой указать название, признаки или фрагмент кода.

## 7. Flow после интеграции

```text
user
  → RagAnswerGenerator
  → CatalogChatService
  → exact extraction (имеет приоритет)
  → explicit text-search extraction
  → CatalogSearch.search_parts(query, limit=5)
  → ranked unique part-number candidates
  → CatalogChatOutcome
  → RagAnswer(response_kind=catalog_candidates)
  → candidate list в CLI/Streamlit
```

Candidate list содержит original part number и stored part name. BM25 score пользователю не показывается и не преобразуется в псевдопроцент.

Structured response содержит:

- `route=catalog_text_search`;
- `search_query`;
- `status=candidates|no_candidates|clarification|unavailable`;
- `result_count` возвращённых кандидатов;
- structured `results`.

`result_count` означает размер показанного top-5, а не дорогой COUNT всех возможных совпадений.

## 8. Candidate UX и exact follow-up

Ответ прямо говорит:

```text
По запросу «...» показано кандидатов: N.
...
Это кандидаты полнотекстового поиска, а не подтверждённые exact-совпадения.
Чтобы получить все вхождения и данные спецификации, отправьте: «Найди <код детали>».
```

Conversation state machine не добавлялась. Пользователь копирует выбранный original code в следующее сообщение. Это сообщение попадает в прежний exact route и получает все BOM occurrences через `find_part_by_number()`.

FTS candidate остаётся уникальным по part number. Representative BOM occurrence используется только как structured context кандидата; exact follow-up не теряет остальные usages.

## 9. Production changes

- `linehelper/catalogs/chat.py`:
  - explicit FTS intent/extraction;
  - вызов existing `search_parts(limit=5)`;
  - candidate/zero/empty/unavailable responses;
  - dynamic exact/text route metadata;
  - candidate rendering без score.
- `linehelper/llm/answer_generator.py`:
  - mapping text-search statuses в отдельные `response_kind`;
  - structured diagnostics `catalog_query`, `catalog_route`, retrieval stage.

Не изменялись `linehelper/catalogs/search.py`, FTS ranking/index, parser/import/schema, UI, CLI, QueryAnalyzer, conversation resolver и 1C architecture.

Streamlit skill использован для проверки chat/session-state контракта. Существующий UI уже универсально отображает candidate answer и technical `catalog` dict, поэтому изменение `streamlit_app.py` не потребовалось.

## 10. Tests

Добавлен `tests/test_catalog_text_chat_integration.py`: 19 test cases/items, покрывающих:

- explicit name search и очищенный query;
- несколько кандидатов и top-5;
- leading code fragment;
- exact priority;
- exact miss без FTS fallback;
- zero-result без RAG/LLM;
- unique FTS candidate и multi-occurrence exact follow-up;
- пять русских intent-фраз;
- unrelated и 1C non-interception;
- missing DB;
- empty explicit query.

Fail fakes запрещают незаметный вызов RAG/LLM, exact или FTS в неправильных тестовых маршрутах.

## 11. Live CLI smoke

Проверка выполнена через production entry point `python -m linehelper.cli chat --debug` и реальную `data/catalogs/catalog_store.db`.

| Query | Detected route | Extracted/catalog query | Invoked method | Count | Фактический результат |
|---|---|---|---|---:|---|
| `Найди в каталоге газоанализатор кислорода` | `catalog_text_search` | `газоанализатор кислорода` | `search_parts(..., 5)` | 1 | top `X96999553` |
| `Найди в каталоге нажимный рычаг` | `catalog_text_search` | `нажимный рычаг` | `search_parts(..., 5)` | 1 | top `X58811189` |
| `Найди в каталоге шаровой шарнир` | `catalog_text_search` | `шаровой шарнир` | `search_parts(..., 5)` | 1 | top `301013203240` |
| `Найди в каталоге X44236` | `catalog_text_search` | `X44236` | `search_parts(..., 5)` | 5 | top `X44236986` |
| `Найди X56767951` | `catalog_exact_lookup` | identifier `X56767951` | `find_part_by_number()` | 1 usage | exact found |
| `Найди НЕСУЩЕСТВУЮЩИЙ_ПОЛНЫЙ_КОД` | `catalog_exact_lookup` | identifier unchanged | `find_part_by_number()` | 0 | exact not found, FTS не вызван |
| `Найди в каталоге zzzcatalogabsent01` | `catalog_text_search` | `zzzcatalogabsent01` | `search_parts(..., 5)` | 0 | honest `no_candidates` |
| `Как оформить КП?` | `ambiguous_abbreviation` | — | Catalog не вызван | — | прежнее уточнение КП |
| `Какие остатки в 1С?` | `one_c_operational_lookup` | — | Catalog не вызван | — | прежний operational route, insufficient data |
| `Найди X96999553` | `catalog_exact_lookup` | identifier `X96999553` | `find_part_by_number()` | 1 usage | authoritative exact follow-up |

Для каждого catalog smoke `model=catalog-store`, `retrieval_stages` содержит соответствующий exact/text route; semantic RAG и LLM не запускались.

Streamlit-процесс на портах 8500–8599 во время проверки не работал. Сервер без отдельного запроса пользователя не запускался. UI использует тот же `RagAnswerGenerator`, который проверен production CLI и UI-related unit tests.

## 12. Regression results

| Проверка | Результат |
|---|---:|
| новые text-chat tests | 19 passed |
| catalog text + FTS + store + exact integration | 47 passed |
| RagAnswerGenerator + answer contract + UI components | 50 passed |
| полный `pytest tests scripts/tests` | 559 passed |
| `python -m compileall linehelper tests scripts` | PASS |

FTS reliability regression осталась зелёной: старый ranking case, full/fragment/name/spaced semantics и exact non-regression проходят. Existing exact chat tests проходят. Полный 71-case live architecture pack, organization pack и live LLM pack не запускались.

Pytest выдал одно инфраструктурное предупреждение: Windows отказал в записи `.pytest_cache`. Все 559 tests завершились успешно.

## 13. RAG/1C routing regression

Catalog integration остаётся ранним, но узким deterministic capability. `Как оформить КП?`, письмо, запрос с годом и 1C-oriented формулировки без explicit catalog prefix возвращают `None` из `CatalogChatService` и продолжают прежний flow. Live 1C smoke сохранил `intent=one_c_operational_lookup` и `operational_lookup=True`.

Missing Catalog DB даёт controlled `catalog_unavailable` только для распознанного catalog intent. Unrelated RAG/1C flow не требует Catalog DB.

## 14. Known limitations

- Candidate selection не имеет conversational state; follow-up выполняется явной командой с кодом.
- Показаны максимум пять кандидатов, total count не вычисляется.
- FTS поддерживает существующую leading-prefix/name semantics, но не typo/fuzzy/infix search.
- Candidate содержит representative occurrence; все usages доступны только через exact follow-up.
- `part_name` выводится в том виде, в котором хранится в verified Catalog Store, включая исходные подчёркивания или усечённую формулировку каталога. Presentation cleanup не входит в эту итерацию.

## 15. Сознательно не реализовано

- automatic exact → FTS fallback;
- принятие первого candidate за подтверждённую деталь;
- state machine выбора кандидата;
- LLM intent/ranking;
- fuzzy/typo/vector/embedding search;
- PDF/reference-page navigation;
- parser/import/schema/ranking changes;
- новый UI или отдельная catalog page;
- изменения QueryAnalyzer, RAG retrieval или 1C tools.

## 16. Рекомендованный следующий шаг

Следующая отдельная итерация может добавить UX выбора кандидата кнопкой/номером в Streamlit. Такой шаг должен хранить выбранный original part number в per-session state и всё равно выполнять authoritative `find_part_by_number()`, а не использовать representative FTS occurrence как итоговые BOM data.

## 17. Заключение

Explicit catalog text search доступен через основной chat entry point, использует только существующий verified FTS API и возвращает явно обозначенный ranked candidate list. Exact route сохранил приоритет; exact miss не запускает FTS; zero/unavailable контролируемы; unrelated RAG и 1C не перехватываются; exact follow-up сохраняет все usages. Итог: **PASS**.
