# Catalog Integration 01 - exact lookup in the main LineHelper chat

## Existing architecture and integration point

Branch / base HEAD: `refactor/rag-architecture-v2` / `004b433`. The main user UI is `linehelper/ui/streamlit_app.py`; the supported command-line chat is `linehelper.cli chat`. Both instantiate `RagAnswerGenerator` and call the same `answer()` method. Before this change the flow was user -> Streamlit/CLI -> `RagAnswerGenerator` -> conversation resolver -> query analyzer -> semantic retriever -> evidence/answer contract -> local LLM or deterministic RAG response. There is no general tool registry. The existing 1C handling is an intent branch inside this orchestration flow and currently has no separate operational tool implementation.

The minimal integration point is therefore an early deterministic capability in `RagAnswerGenerator`, before conversation analysis and RAG. It is dependency-injected and returns `None` for unrelated questions, leaving every existing route unchanged.

After integration:

`user -> Streamlit/CLI -> RagAnswerGenerator -> CatalogChatService -> CatalogSearch.find_part_by_number -> CatalogStore/SQLite -> CatalogPartResult[] -> deterministic answer`

No direct catalog SQL or part-number normalization was added to the chat layer. The capability calls the existing tested exact/normalized API.

## Routing and response behavior

The route recognizes a narrow set of explicit phrases: `найди`, `деталь`, `артикул`, `код детали`, `part number`, `что известно о`, `где используется`, and `покажи деталь/информацию по`. A bare input routes only when the whole message looks like an alphanumeric part code, or an all-numeric code of at least eight digits. A number embedded in an ordinary sentence does not route; tests cover `Сколько дней было в 2026 году?` and `Найди регламент за 2024 год`.

The extracted identifier is passed unchanged to `CatalogSearch.find_part_by_number`; existing catalog normalization remains the sole normalization policy. Spaced KHS identifiers are preserved. All returned occurrences remain in the structured result. The deterministic text renders up to 20 occurrences and reports the total if more exist.

Found answers include only stored fields: original part number, name, assembly code/name, position, quantity/unit, physical BOM `source_page`, and nullable printed `reference_page`. A NULL reference is omitted and never replaced with source page. Not found returns an explicit exact-match miss and does not fall through to FTS or the LLM. Missing/corrupt Catalog DB returns a controlled `catalog_unavailable` answer; unrelated questions continue through the existing RAG flow.

## Configuration and observability

`LineHelperConfig.catalog_db_path` defaults to `data/catalogs/catalog_store.db` and can be overridden with `LINEHELPER_CATALOG_DB_PATH`; no absolute path is hard-coded. Existing interaction analytics remains the logging path. Query diagnostics record `catalog_identifier`, `catalog_result_count`, and `catalog_status`; application logging records invocation, found/not-found count, and controlled failure.

## Production changes

- `linehelper/catalogs/chat.py`: intent detection, controlled lookup, deterministic rendering.
- `linehelper/catalogs/models.py` and `search.py`: expose the already stored unit in `CatalogPartResult`.
- `linehelper/llm/answer_generator.py`: early catalog route and structured `catalog` diagnostics.
- `linehelper/config.py`, Streamlit entry point, and CLI: common Catalog DB configuration.
- `linehelper/analytics/serialization.py`: whitelist catalog route diagnostics.
- `.env.example`: optional Catalog DB override.

No PDF parser, import pipeline, BOM schema, normalization, FTS ranking, reference navigation, or existing RAG/1C behavior was changed.

## Tests

`tests/test_catalog_chat_integration.py` covers numeric and alphanumeric exact lookup, multiple occurrences, not found, distinct source/reference pages, NULL reference, unavailable DB, unrelated numeric questions, spaced identifiers, bare-code boundaries, and existing 1C routing. Existing Catalog Store and chat/router tests were also executed.

- Targeted Catalog chat + store: 18 passed.
- Existing answer/query/conversation/analytics/UI tests: 173 passed.
- Full project pytest: 530 passed in 26.02 seconds; one pre-existing `.pytest_cache` permission warning.

## Live chat smoke

Production entry point: `.venv/Scripts/python.exe -m linehelper.cli chat --debug <query>`. Every smoke used route `catalog_exact_lookup`, retrieval stage `catalog_exact_lookup`, zero RAG chunks, and no LLM prompt.

| Query | Status/count | Part / assembly | source_page | reference_page | User-facing fact |
|---|---:|---|---:|---:|---|
| `Найди 20411616` | found / 1 | 20411616 / 10307585, pos. 200 | 9 | 12 | `наполнитель_нижняя часть_`, 1.000 шт |
| `Что известно о X56767951?` | found / 1 | X56767951 / 20411616, pos. 10 | 13 | 14 | `опорное кольцо_комплектный_`, 1.000 шт |
| `Где используется 58803877S001?` | found / 1 | 58803877S001 / 20411617, pos. 10 | 67 | 68 | `прижимной элемент__`, 157.000 шт |
| `Покажи деталь X58884081` | found / 1 | X58884081 / 58866749S001, pos. 10 | 451 | 454 | `зажимной держатель__`, 1.000 шт |
| `Найди НЕСУЩЕСТВУЮЩИЙ_КОД` | not_found / 0 | - | - | - | Exact match explicitly not found |

The first four responses were checked against the current Catalog Store structured results. Interaction analytics successfully recorded all five smoke requests.

An additional production-generator smoke for `Где используется X58716070?` returned 73 structured occurrences. The result retained all 73 records; the answer rendered the first 20 and explicitly reported the 73-total limit, confirming that multiple usages are not collapsed to the first row.

## Worktree context and deliberate exclusions

The Catalog Pilot, Audit, and Correction files were already staged/uncommitted when this integration began; they are related predecessor work but are not newly recreated here. No unrelated existing production file was modified. This iteration intentionally does not add FTS/semantic/fuzzy/vector catalog retrieval, PDF opening, UI navigation, or a conversational catalog agent.

## Status

**PASS.** Compileall, targeted tests, relevant existing tests, full pytest, and live exact chat smoke are green. No Streamlit process was listening on ports 8500-8599 during the final check, so live verification used the supported production CLI entry point sharing the same generator as Streamlit. Commit and push were not performed.
