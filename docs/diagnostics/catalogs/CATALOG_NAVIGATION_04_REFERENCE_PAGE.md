# CATALOG_NAVIGATION_04 — навигация к страницам исходного PDF

## 1. Итог

Статус: **PASS**.

Exact-result теперь содержит зарегистрированную идентичность исходного каталога и отдельные navigation targets для `source_page` и `reference_page`. CLI показывает документ и оба номера страниц. Streamlit безопасно рендерит только одну выбранную страницу на сервере и показывает её внутри приложения; `file:///`-ссылки и endpoint произвольного доступа к файловой системе не создавались.

Parser, импорт, schema BOM, exact normalization, FTS ranking и маршруты 1С не изменялись.

## 2. Git preflight

- branch: `refactor/rag-architecture-v2`;
- HEAD: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`;
- рабочее дерево до начала итерации: чистое;
- staged/unstaged/untracked до начала итерации: отсутствовали;
- commit/push: не выполнялись.

Все перечисленные ниже изменения возникли в рамках текущей итерации.

## 3. Исходная модель документа и страницы

Catalog Store уже хранил в `catalogs`:

- `catalog_id`;
- `source_filename`;
- `source_checksum`;
- `page_count`;
- metadata каталога и оборудования.

Связь occurrence с документом однозначна:

`bom_items.assembly_id -> assemblies.catalog_id -> catalogs.catalog_id`.

`catalog_pages` содержит trace по `catalog_id`, `page_number`, `page_type` и `assembly_id`, но отдельной production-модели `CatalogDocument`/`CatalogPage` до этой итерации не было. Полный локальный путь в БД намеренно не хранится.

Исходный файл пилота физически находится в:

`data/catalogs/sourse/volzhsky/ETL_89401221_000400__Innofill_RU_05.pdf`.

В репозитории сохраняется историческое имя директории `sourse`. Корень настраивается через `LINEHELPER_CATALOG_SOURCE_ROOT`; production-код не содержит абсолютного Windows-пути.

SHA-256 файла совпадает с `catalogs.source_checksum`:

`bd2e1fda3f8b65cf9cb80a8e0690394a8dba59613e28d72414bdafbf8f06d2b0`.

## 4. Нумерация страниц

В INNOFILL REV05:

- сохранённые `source_page` и `reference_page` — one-based номера физических страниц PDF;
- PDF содержит 679 физических страниц;
- page labels отсутствуют;
- смещения от титульных листов нет;
- проверенное правило: `pdf_index = page_number - 1`.

Правило независимо проверено по текстовому слою, footer page number и визуальному рендеру 20 страниц для 10 occurrence из начала, середины и конца документа. `source_page` открывает BOM-таблицу с нужной строкой, а `reference_page` — связанную страницу каталога/чертежа.

## 5. Новый resolver и structured model

Добавлен `linehelper.catalogs.navigation`:

- `CatalogDocumentIdentity` — `catalog_id`, filename, checksum, page count;
- `CatalogPageTarget` — document identity, `page_kind`, one-based page number, zero-based `pdf_index`, status и controlled error;
- `CatalogOccurrenceNavigation` — occurrence identity и отдельные `source`/`reference` targets;
- `CatalogPageLocator` — поиск зарегистрированного PDF внутри configured source root;
- `render_catalog_page_png()` — server-side рендер только одного уже проверенного target.

Статусы target:

- `available`;
- `unavailable`;
- `invalid_page`.

`CatalogPartResult` дополнен техническими полями `bom_item_id`, `catalog_id`, `source_filename`, `source_checksum`, `catalog_page_count`. `CatalogSearch` получает их через существующий structured SQL select; exact/FTS semantics и normalization не менялись.

## 6. Разрешение документа и безопасность

Resolver:

1. принимает document identity из БД, а не путь из пользовательского запроса;
2. запрещает filename с директориями (`../`, абсолютный путь и аналогичные варианты);
3. ищет filename только внутри configured root;
4. проверяет containment после `resolve()`, включая защиту от выхода через symlink;
5. сверяет SHA-256 с Catalog Store;
6. отклоняет неоднозначные совпадения;
7. сверяет реальный `page_count` с БД;
8. валидирует `1 <= page_number <= page_count`;
9. не сериализует локальный путь в `to_dict()` и не отдаёт его браузеру.

Отдельный file-serving endpoint не создавался, поэтому пользователь не может передать произвольный filesystem path. Streamlit получает server-side объект, созданный resolver, и рендерит PNG bytes только после повторной проверки root, PDF suffix и диапазона страницы.

## 7. Поведение ошибок и NULL

- отсутствующий source root/PDF, checksum mismatch, неоднозначность или нечитаемый PDF дают controlled `unavailable`;
- `page <= 0` или `page > page_count` дают `invalid_page`, исключение наружу не выходит;
- `reference_page = NULL` даёт `reference = None`;
- `source_page` при NULL reference остаётся доступен и никогда не подставляется вместо reference;
- отсутствие локального PDF не ломает exact lookup и остальные маршруты LineHelper.

## 8. Exact chat, CLI и Streamlit

Flow после интеграции:

`user -> RagAnswerGenerator -> CatalogChatService -> CatalogSearch.find_part_by_number() -> occurrence -> CatalogPageLocator -> structured navigation -> CLI/Streamlit`.

Exact chat сохраняет прежний authoritative ответ и добавляет:

- имя документа;
- source target для каждой occurrence;
- reference target только при наличии `reference_page`.

CLI smoke `Найди X56767951` вернул exact occurrence:

- part: `X56767951`;
- assembly: `20411616`;
- position: `10`;
- source page: `13`;
- reference page: `14`;
- document: `ETL_89401221_000400__Innofill_RU_05.pdf`.

Streamlit реализует page preview:

- expander `Страницы каталога`;
- отдельные действия `Открыть страницу BOM` и `Открыть связанную страницу`;
- рендер только выбранной страницы через PyMuPDF;
- для `X56767951` BOM preview показал страницу 13, reference preview — страницу 14;
- в браузерном live smoke PNG asset сменился при переходе 13 -> 14;
- для `X58716070` UI показал 73 occurrence, structured layer сохранил 73 независимых navigation context; UI намеренно показывает первые 20 карточек.

## 9. Multiple occurrences

Navigation строится для каждой BOM row отдельно и сохраняет её `bom_item_id`, assembly, position, source page и nullable reference page. Никакой target первой occurrence не переиспользуется для остальных.

Для `X58716070` проверено:

- exact usages: 73;
- navigation objects: 73;
- уникальные `bom_item_id`: 73;
- уникальные source/reference contexts: 73;
- все 73 reference отсутствуют и остаются `None`, source page не подставляется.

## 10. Проверка реального PDF

| Part number | Assembly | Position | Stored source | PDF source index | Stored reference | PDF reference index | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| 20411616 | 10307585 | 200 | 9 | 8 | 12 | 11 | PASS |
| X56767951 | 20411616 | 10 | 13 | 12 | 14 | 13 | PASS |
| 58803877S001 | 20411617 | 10 | 67 | 66 | 68 | 67 | PASS |
| 58831843S104 | 20411665 | 10 | 149 | 148 | 150 | 149 | PASS |
| 58857804S001 | X58857849 | 40 | 249 | 248 | 250 | 249 | PASS |
| 301067011930 | 59642651S101 | 20 | 349 | 348 | 350 | 349 | PASS |
| X58884081 | 58866749S001 | 10 | 451 | 450 | 454 | 453 | PASS |
| 58866862S008 | 58827364S002 | 60 | 561 | 560 | 562 | 561 | PASS |
| 58820733S028 | 58865726S046 | 10 | 647 | 646 | 648 | 647 | PASS |
| X07000923 | XFU 87213 | 20 | 673 | 672 | 674 | 673 | PASS |

Все 20 targets существуют, checksum/document identity совпадают, source и reference не смешиваются.

## 11. Изменённые production-файлы

- `.env.example`;
- `linehelper/catalogs/__init__.py`;
- `linehelper/catalogs/chat.py`;
- `linehelper/catalogs/models.py`;
- `linehelper/catalogs/navigation.py` (новый);
- `linehelper/catalogs/search.py`;
- `linehelper/cli.py`;
- `linehelper/config.py`;
- `linehelper/llm/answer_generator.py`;
- `linehelper/ui/streamlit_app.py`.

Production parser/import/store schema/indexing/normalization не изменялись.

## 12. Tests

Новый файл `tests/test_catalog_navigation.py` содержит 10 test items и проверяет:

- source target и one-based -> zero-based mapping;
- отдельный reference target;
- `source != reference`;
- NULL reference;
- missing PDF;
- invalid pages `0`, `-1`, `> page_count`;
- independent multiple occurrences;
- PNG render и отсутствие пути в public metadata;
- path traversal rejection;
- exact chat navigation regression.

Результаты:

- navigation + catalog exact/text/FTS: **57 passed**;
- relevant answer-generator/grounded/UI: **50 passed**;
- full project pytest (`tests scripts/tests`): **569 passed**, 1 non-functional `PytestCacheWarning` из-за запрета записи `.pytest_cache`;
- `python -m compileall linehelper tests scripts`: **PASS**.

Отдельный 71-case live architecture pack не запускался.

## 13. Known limitations

- verified numbering rule относится к INNOFILL REV05; перед регистрацией каталога с page-label offset правило требуется проверить;
- БД хранит переносимые filename/checksum, а локальный путь разрешается при выполнении; при нескольких checksum-identical копиях resolver намеренно возвращает ambiguous/unavailable;
- preview — одностраничный raster, без zoom/navigation viewer, OCR и анализа схем;
- UI показывает первые 20 occurrence-карточек, хотя structured result сохраняет все;
- PDF должен быть локально доступен под configured source root.

## 14. Следующий рекомендуемый шаг

Для следующего каталога добавить во время приёмочного аудита явную проверку numbering semantics. Если появится документ со смещением или нестандартными page labels, сохранить declarative mapping в catalog metadata и применять его в resolver, не вводя filename/page exceptions.

