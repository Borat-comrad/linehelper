# CATALOG_SEARCH_02 — FTS reliability audit

Итоговый статус: **PASS**.

Дата проверки: 2026-08-11. Ветка: `refactor/rag-architecture-v2`. Исходный HEAD: `004b433` (`feat(analytics): добавить логирование взаимодействий и feedback`). Commit и push не выполнялись.

## 1. Preflight и происхождение изменений

В начале итерации `git diff --check` не выявил ошибок. В рабочем дереве уже находились незакоммиченные изменения предыдущих Catalog Pilot/Audit/Correction/Integration итераций:

```text
 M .env.example
M  .gitignore
A  docs/diagnostics/catalogs/CATALOG_AUDIT_01_INNOFILL_REV05_SEMANTIC_VALIDATION.md
A  docs/diagnostics/catalogs/CATALOG_CORRECTION_01_REFERENCE_PAGE.md
A  docs/diagnostics/catalogs/CATALOG_PILOT_01_INNOFILL_REV05_IMPORT.md
A  Фdocs/diagnostics/catalogs/KHS_ETL_INNOFILL_REV05_LAYOUT_ANALYSIS.md
 M linehelper/analytics/serialization.py
AM linehelper/catalogs/__init__.py
A  linehelper/catalogs/indexing.py
A  linehelper/catalogs/hs_etl_parser.py
AM linehelper/catalogs/models.py
AM linehelper/catalogs/search.py
A  linehelper/catalogs/store.py
A  linehelper/catalogs/validation.py
 M linehelper/cli.py
 M linehelper/config.py
 M linehelper/llm/answer_generator.py
 M linehelper/ui/streamlit_app.py
A  scripts/import_spare_parts_catalog.py
A  scripts/search_spare_parts_catalog.py
A  tests/test_catalog_store.py
?? docs/diagnostics/catalogs/CATALOG_INTEGRATION_01_EXACT_CHAT_LOOKUP.md
?? linehelper/catalogs/chat.py
?? tests/test_catalog_chat_integration.py
```

Эта итерация не присваивает себе происхождение перечисленных изменений и меняет только FTS query/ranking, диагностический режим CLI, целевые FTS-тесты и данный отчёт.

Фактические данные реальной DB при аудите: 1 catalog, 1 equipment, 282 assemblies, 1 506 parts, 2 505 BOM items, 679 catalog pages, 2 505 строк FTS.

## 2. FTS до изменений

Использовалась таблица `catalog_search_fts`, пересоздаваемая функцией `rebuild_search_index()`:

```sql
CREATE VIRTUAL TABLE catalog_search_fts USING fts5(
    part_number_original,
    part_number_normalized,
    part_name,
    assembly_code,
    assembly_name,
    equipment_model,
    machine_number,
    revision,
    hierarchy_path,
    bom_item_id UNINDEXED
)
```

Явный `tokenize=` не указан, поэтому применяется стандартный SQLite FTS5 tokenizer `unicode61`. Единицей строки индекса является BOM occurrence (`bom_item`), а не уникальная деталь. Индекс является полностью перестраиваемой проекцией структурированных таблиц.

До исправления `search_parts()`:

1. заменял `_` в пользовательском вводе на пробел;
2. заключал весь ввод в безопасную FTS-фразу;
3. выполнял `catalog_search_fts MATCH ?`;
4. сортировал только по `bm25(catalog_search_fts)`;
5. применял SQL `LIMIT` до устранения повторных BOM occurrences.

Exact lookup использовал общую `normalize_part_number()` и прямое сравнение с `part_number_original`/`part_number_normalized`. FTS не вызывал exact lookup и не менял его поведение.

## 3. Tokenization и normalization

`unicode61` сохраняет буквенно-цифровую последовательность одним токеном и разделяет токены пробелом и визуальными разделителями. Проверенные примеры:

| Исходная строка | FTS tokens |
|---|---|
| `58831836S001` | `58831836s001` |
| `XFH 20093` | `xfh`, `20093` |
| `XFH20093` | `xfh20093` |
| `123-456/78.9` | `123`, `456`, `78`, `9` |

В реальной DB найдено 28 part numbers с пробелами. Part numbers с `-`, `.`, `/` в текущем каталоге не найдены, поэтому их поведение подтверждено правилами tokenizer/query builder и synthetic tests, но не реальной выборкой REV05.

Normalization и FTS tokenization теперь явно разделены:

- canonical part normalization по-прежнему выполняет единственная существующая `normalize_part_number()`;
- `build_fts_match_query()` лишь безопасно преобразует ввод в `unicode61`-совместимую phrase/prefix query;
- compact normalized value используется в ranking, но новая normalization policy не добавлена.

Один ASCII alphanumeric token длиной не менее четырёх символов и содержащий цифру получает leading-prefix query, например `588318` → `"588318"*`. Многотокеновый текст остаётся phrase query. Это даёт поиск осмысленного начального фрагмента кода, но не является infix, fuzzy или edit-distance поиском.

## 4. Старый failing case и root cause

Код предыдущего smoke был зафиксирован в semantic audit: `58831836S001`.

| Параметр | Значение |
|---|---|
| original | `58831836S001` |
| normalized | `58831836S001` |
| FTS token | `58831836s001` |
| старый MATCH query | `"58831836S001"` |
| raw matches | 38 BOM rows |
| expected occurrences | 3 |
| deterministic raw BM25 ranks | 28, 29, 30 |
| expected raw score | `-5.806786` |

Первые пять результатов старого ranking были деталями узла с `assembly_code=58831836S001`, например `H20233077991`, `H20433022011`, `H29233010831`, `H29233010841`, `H29233010851`, со score `-5.940648`. Все они совпадали по коду узла, а не по собственному part number.

Причина промаха: не tokenizer, не отсутствие normalized кода и не устаревший индекс. Одинаково взвешенный BM25 ранжировал 38 BOM-строк; 34 строки узла с совпадающим assembly code вытесняли три точных part-number occurrences, после чего ранний `LIMIT 5` скрывал правильную деталь. Порядок внутри одинакового BM25 score ранее также не имел явного deterministic tie-break.

## 5. Минимальное исправление

Изменён только search layer:

- точное совпадение FTS-кандидата с original или normalized part number получает явный первый ranking tier;
- BM25 получил документированные веса полей: original code `8`, normalized code `7`, part name `4`, assembly code `2`, assembly name `1.5`, прочие текстовые поля `1`;
- добавлены deterministic tie-breaks;
- повторные BOM occurrences одного `part_number_original` сворачиваются в один ranked candidate после полного ранжирования, и только затем применяется пользовательский `limit`;
- `build_fts_match_query()` безопасно формирует phrase/prefix MATCH query;
- exact API `find_part_by_number()` не изменён.

Человеческая формулировка ranking policy: точный текстовый код детали сильнее любых совпадений по описанию или узлу; затем выигрывают совпадения в полях номера, потом в названии детали, затем в assembly metadata и прочем контексте. BM25 score остаётся относительным, меньшее (более отрицательное) значение лучше.

После исправления `58831836S001` стабильно занимает rank 1; оставшиеся top-5 являются ожидаемыми assembly-context кандидатами.

## 6. Семантика результата

Единица `search_parts()` теперь — уникальный кандидат part number. Для вывода сохраняется лучшая source-traceable BOM occurrence этого кандидата. Деталь, использованная 73 раза, не занимает 73 места ranked list.

Все usages не теряются: после выбора part number они получаются неизменённым `find_part_by_number()`, который возвращает все BOM occurrences. Это разделяет candidate search и authoritative usage lookup.

## 7. Реальная контрольная выборка

Выборка воспроизводима: seed `20260811`, данные выбирались непосредственно из `data/catalogs/catalog_store.db`. Категории в DB: 590 numeric, 382 X-prefix, 441 `S`-suffix, 28 spaced, 65 other, 0 punctuated.

### 7.1 Полные part numbers — 25/25 top-5, 25/25 top-1

| Query | Expected | Категория | Rank | Score | Top-5 |
|---|---|---:|---:|---:|---:|
| `301013203490` | `301013203490` | numeric | 1 | -15.126867 | yes |
| `301025000071` | `301025000071` | numeric | 1 | -14.995267 | yes |
| `301132172170` | `301132172170` | numeric | 1 | -11.258714 | yes |
| `301054615040` | `301054615040` | numeric | 1 | -13.486745 | yes |
| `301132170820` | `301132170820` | numeric | 1 | -11.534464 | yes |
| `X96999418` | `X96999418` | X prefix | 1 | -15.060780 | yes |
| `X58806108` | `X58806108` | X prefix | 1 | -15.227093 | yes |
| `X59922052` | `X59922052` | X prefix | 1 | -15.160129 | yes |
| `X44270760` | `X44270760` | X prefix | 1 | -15.160129 | yes |
| `X58844651` | `X58844651` | X prefix | 1 | -15.227093 | yes |
| `58826812S230` | `58826812S230` | S suffix | 1 | -11.738032 | yes |
| `58810092S004` | `58810092S004` | S suffix | 1 | -15.160129 | yes |
| `58866863S022` | `58866863S022` | S suffix | 1 | -12.187059 | yes |
| `58824829S118` | `58824829S118` | S suffix | 1 | -11.128264 | yes |
| `58865907S007` | `58865907S007` | S suffix | 1 | -12.028022 | yes |
| `XFH 25774` | `XFH 25774` | spaced | 1 | -14.269331 | yes |
| `XFH 20142` | `XFH 20142` | spaced | 1 | -14.269331 | yes |
| `XFU 85196` | `XFU 85196` | spaced | 1 | -14.159437 | yes |
| `XFH 25707` | `XFH 25707` | spaced | 1 | -14.159437 | yes |
| `XFH 24146` | `XFH 24146` | spaced | 1 | -13.338009 | yes |
| `H29299010041` | `H29299010041` | other | 1 | -15.126867 | yes |
| `H29233010891` | `H29233010891` | other | 1 | -13.486745 | yes |
| `301063806540` | `301063806540` | numeric | 1 | -15.160129 | yes |
| `58866749S001` | `58866749S001` | S suffix | 1 | -8.962457 | yes |
| `X44235105` | `X44235105` | X prefix | 1 | -15.060780 | yes |

Для тех же 25 кодов deterministic exact lookup: 25/25 найдено, все имели хотя бы одно usage.

### 7.2 Leading fragment search — 10/10 top-5, 10/10 top-1

Контрольные префиксы выбирались только там, где первые шесть normalized symbols уникальны в текущем наборе parts.

| Query | Expected | Rank | Top-5 |
|---|---|---:|---:|
| `599202` | `59920211S001` | 1 | yes |
| `204280` | `20428071` | 1 | yes |
| `301255` | `301255113040` | 1 | yes |
| `X44236` | `X44236986` | 1 | yes |
| `X05164` | `X05164370` | 1 | yes |
| `301204` | `301204610870` | 1 | yes |
| `596237` | `59623773S002` | 1 | yes |
| `X05224` | `X05224616` | 1 | yes |
| `H23514` | `H23514010711` | 1 | yes |
| `599229` | `59922967S002` | 1 | yes |

### 7.3 Name search — 10/10 top-5

| Query | Expected part | Rank | Top-5 |
|---|---|---:|---:|
| `подача вода` | `59642659S201` | 1 | yes |
| `клапан отбора` | `301067011930` | 2 | yes |
| `ящик управление` | `L29233010821` | 1 | yes |
| `пробка желтый` | `301991013940` | 1 | yes |
| `нажимный рычаг` | `X58811189` | 1 | yes |
| `газоанализатор кислорода` | `X96999553` | 1 | yes |
| `шаровой шарнир` | `301013203240` | 1 | yes |
| `зажимная деталь` | `X58802021` | 5 | yes |
| `редукция 10` | `X58701175` | 1 | yes |
| `запрос наличия` | `X58856811` | 1 | yes |

Не все name queries должны быть top-1: одинаковые слова объективно присутствуют в названиях нескольких деталей или assembly metadata. Важный результат — повышение веса part name не уничтожило контекстный поиск, и все source-derived expected candidates находятся в top-5.

### 7.4 Part numbers с пробелами

Пять реальных original codes проверены по original и normalized representation: `XFH 20093`/`XFH20093`, `XFH 20142`/`XFH20142`, `XFH 23395`/`XFH23395`, `XFH 24146`/`XFH24146`, `XFH 24694`/`XFH24694`. Все 10 запросов дали rank 1 и вернули исходный код с пробелом, без переписывания original value.

### 7.5 Negative cases — 10/10 без результатов

| Query | Result count |
|---|---:|
| `ZZZCATALOGABSENT01` | 0 |
| `QXQXQX999999` | 0 |
| `NO_SUCH_REDUCER_999` | 0 |
| `PART-NOT-IN-STORE` | 0 |
| `000000000000000000` | 0 |
| `XZZ999999999` | 0 |
| `phantom bearing 404` | 0 |
| `CATALOG_NEGATIVE_ALPHA` | 0 |
| `NO_SUCH_PART_777` | 0 |
| `UNLISTED/123/XYZ` | 0 |

## 8. Diagnostic CLI

`scripts/search_spare_parts_catalog.py` получил необязательный `--diagnostic`. Он показывает query и для каждого результата rank, original part number, name и score. Обычный JSON output сохранён.

Пример:

```powershell
.\.venv\Scripts\python.exe scripts\search_spare_parts_catalog.py 58831836S001 --limit 5 --diagnostic
```

## 9. Автоматические проверки

Добавлен `tests/test_catalog_fts_reliability.py` (10 test items):

- numeric/alphanumeric/X-prefix/spaced/normalized full codes;
- regression assembly-code collision `58831836S001`;
- exact-looking top rank;
- code prefix и name search;
- unknown query;
- candidate dedup при сохранении всех exact usages;
- безопасный MATCH builder;
- exact chat route с trap, запрещающим вызов FTS.

Результаты:

| Проверка | Результат |
|---|---|
| targeted FTS | 10 passed |
| `tests/test_catalog_store.py` | 7 passed |
| catalog chat + answer/contract regression | 57 passed |
| полный `pytest tests scripts/tests` | 540 passed |
| `python -m compileall linehelper tests scripts` | PASS |

Pytest сообщил одно инфраструктурное предупреждение: Windows отказал в записи `.pytest_cache`. Оно не связано с production code и не повлияло на 540 пройденных тестов. 71-case live architecture pack, organization pack и live LLM tests не запускались.

Exact chat integration не менялась. Специальный regression test подтверждает, что `CatalogChatService` вызывает только `find_part_by_number()` и не использует FTS как fallback.

## 10. Безопасные правила будущего использования

1. Известный пользователю part number всегда сначала обрабатывается deterministic `find_part_by_number()`.
2. FTS используется только для ranked candidate search по названию, ведущему фрагменту кода или комбинации признаков.
3. FTS candidate не является подтверждённым exact match; пользовательскому слою следует показывать original code и различать candidate search и exact lookup.
4. После выбора кандидата все BOM usages следует получать exact API, а не representative occurrence FTS.
5. Автоматический chat fallback exact → FTS в этой итерации не включён.
6. Нулевой FTS result не должен превращаться в выдуманную деталь.

## 11. Изменённые файлы

Production:

- `linehelper/catalogs/search.py` — safe query builder, explicit ranking policy, candidate dedup;
- `scripts/search_spare_parts_catalog.py` — диагностический вывод.

Tests/docs:

- `tests/test_catalog_fts_reliability.py`;
- `docs/diagnostics/catalogs/CATALOG_SEARCH_02_FTS_RELIABILITY.md`.

Не изменялись parser/import, BOM schema/data, `source_page`, `reference_page`, exact normalization, `CatalogChatService`, `RagAnswerGenerator`, FTS index schema/content и основной RAG retrieval.

## 12. Ограничения и следующий шаг

- Prefix matching поддерживает начало одного code-like token, но не произвольную середину кода, typo tolerance или fuzzy matching.
- FTS result содержит одну лучшую representative occurrence на original part number; все usages требуют exact follow-up.
- При очень большом количестве каталогов устранение duplicate occurrences стоит перенести из Python в SQL candidate projection; для текущего пилота максимум 2 505 FTS rows и поведение детерминировано.
- В REV05 отсутствуют part numbers с дефисами, точками и слэшами; перед импортом каталога с такими реальными кодами нужна дополнительная source-derived выборка.

Рекомендуемый следующий шаг: отдельная интеграция явного пользовательского text-search intent в чат с показом ranked candidates и обязательным exact follow-up после выбора. Автоматический fallback не следует включать без такого UX-контракта.

## 13. Заключение

Root cause старого 9/10 установлен и воспроизведён. Исправление минимально и не затрагивает verified exact path. Старый кейс теперь rank 1, реальные full-code запросы дали 25/25 top-1, fragment 10/10 top-1, name 10/10 top-5, negatives 10/10 empty, spaced original/normalized 10/10 top-1. Систематических необъяснённых anomalies не найдено. Итог: **PASS**.
