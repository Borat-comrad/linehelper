# Catalog Evaluation 01 — пользовательский прогон и аналитика качества

## Статус

**EVALUATION COMPLETE — NEEDS_CORRECTION**

Единственный прогон фиксированного набора из 37 вопросов завершён. Production
answer path не изменялся. Строгий результат: 29 PASS, 2 PARTIAL, 6 FAIL.
Технический success rate `(PASS + PARTIAL) / total` — **83,78%**; строгий
PASS rate — **78,38%**.

## Git preflight

- Branch: `refactor/rag-architecture-v2`
- HEAD: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`
- Исходный commit: `63ceb4d feat(catalogs): add catalog search and chat integration`
- `git diff --check`: ошибок whitespace не обнаружено; PowerShell показал только
  предупреждения о будущем преобразовании LF в CRLF.

До Evaluation 01 рабочее дерево уже содержало изменения предыдущих Catalog
итераций. Они не откатывались, не присваивались текущей задаче и не менялись
ради исправления результатов:

- modified: `.env.example`, `linehelper/catalogs/__init__.py`,
  `linehelper/catalogs/chat.py`, `linehelper/catalogs/models.py`,
  `linehelper/catalogs/search.py`, `linehelper/cli.py`, `linehelper/config.py`,
  `linehelper/llm/answer_generator.py`, `linehelper/ui/streamlit_app.py`,
  `tests/test_catalog_chat_integration.py`,
  `tests/test_catalog_text_chat_integration.py`;
- untracked: четыре предыдущих Catalog-отчёта,
  `linehelper/catalogs/navigation.py`, `linehelper/catalogs/presentation.py` и
  пять наборов Catalog corrective/navigation/presentation tests.

Файлы текущей Evaluation 01:

- `scripts/evaluate_catalog_user_flow.py` — reusable evaluation runner;
- `tests/test_catalog_evaluation_runner.py` — targeted tests runner/grading;
- этот отчёт.

Это не production answer path.

## Проверка окружения

Catalog Store: `data/catalogs/catalog_store.db`, файл существует.

Фактические SQL counts:

| Сущность | Count |
| --- | ---: |
| catalogs | 1 |
| equipment | 1 |
| assemblies | 282 |
| bom_items | 2 505 |
| parts | 1 506 |
| catalog_pages | 679 |

Каталог: `ETL_89401221_000400__Innofill_RU_05.pdf`, revision `05`, machine
`47592`, page count `679`.

Interaction Analytics включена. Ollama доступен, модели `qwen2.5:14b` и
`qwen2.5:3b` доступны.

## Методика

Run ID:

`catalog_evaluation_01_20260812T145307+0300`

Все 37 вопросов прошли ровно один раз через
`RagAnswerGenerator.answer()` — тот же high-level flow, который создают CLI и
Streamlit. Runner не вызывает напрямую `CatalogSearch`, exact/FTS helpers,
`QueryAnalyzer` или `EvidenceAssessor`.

Все interactions имеют общий `session_id`, но выполнялись без conversation
history. Это сохраняет единую analytics session и одновременно проверяет
G04/G05 как запросы без референта, а не как follow-up к предыдущему вопросу.

Analytics изолирована в `data/analytics/catalog_evaluation_01.db`. Evaluation
не создавала 👍/👎 и не писала записи в `interaction_feedback`.

Grading сначала выполнен детерминированно по route, response/match type,
structured catalog results, sources и строгим фактам exact cases. Затем
проведена содержательная проверка non-PASS cases и релевантности E05 sources.
Semantic ошибки не исправлялись, вопросы повторно не запускались.

## Общий итог

```text
PASS: 29
PARTIAL: 2
FAIL: 6
Strict PASS rate: 78.38%
Technical success rate: 83.78% ((PASS + PARTIAL) / 37)
```

PARTIAL:

- A05: честный not-found текст, но неверный diagnostic
  `response_kind=catalog_unavailable` вместо not-found;
- F05: corporate route сохранён, но supporting evidence не найден и возвращён
  generic insufficient answer.

FAIL: B04, D04, D06, E03, E04, E05.

## Routing

Safe/ambiguous cases не включены в denominator routing accuracy, потому что
для них допустимо несколько безопасных маршрутов.

```text
Routing accuracy: 26/31 = 83.87%
False catalog routes: 0
Missed catalog routes: 5
Catalog expected/actual: 18/21 = 85.71%
Corporate expected/actual: 5/5 = 100%
Mixed expected/actual: 3/5 = 60%
```

Отсутствуют false catalog interceptions корпоративных вопросов. Основная
ошибка — обратная: уверенное catalog evidence не доходит до результата из-за
неполного extraction предметной части запроса.

## Catalog quality

```text
Existing exact codes: 4/4 PASS
Exact not-found user behavior: 1/1 honest, diagnostic mode PARTIAL
Prefix/normalized prefix: 3/3 PASS
Meaningful substring: 0/1 FAIL
Short-fragment guard: 1/1 PASS
Text search: 6/6 PASS
Natural-language catalog: 4/6 PASS
```

Exact A01 полностью совпал с ожидаемыми фактами: `X44235100`, assembly
`X44236986`, position `70`, quantity `4.000` (rendered `4 шт.`), specification
page `467`. A02–A04 также вернули exact structured occurrences и catalog
sources. A05 не выдумал деталь и не показал FTS-кандидата.

B01–B03 вернули только `X44236986`; посторонний `301134230120` отсутствует.
B04 (`442351`) не распознан как допустимый six-digit code fragment, ушёл в
corporate flow и не нашёл `X44235100`. B05 (`44`) не запустил широкий substring
lookup и безопасно завершился insufficient response.

Все C01–C06 дали релевантные FTS candidates. Например C05 первым вернул
`H29205010648 — труба_прижимное устройство_`, C06 — `20394873 —
инструмент_прижимное устройство_`. Part numbers, assembly, position, quantity
и specification page пришли из structured Catalog Store.

D04 сохранил в probe payload служебное слово `входят`, снизив field coverage до
`0.667`; D06 включил `каталоге` в предметный запрос и не принял пять найденных
probe candidates (coverage `0.8`). Оба завершились corporate no-answer.

## Mixed quality

```text
Mixed route accuracy: 3/5 = 60%
Requirements preserved with correct partial answer: 2/5 = 40%
Lost catalog requirement: 2 cases (E03, E04)
Mixed route but wrong catalog/corporate evidence: 1 case (E05)
```

E01 и E02 корректно разложены на catalog entity + maintenance procedure и
вернули `partial_mixed`: catalog facts сохранены, неподтверждённая процедура
явно отмечена как отсутствующая.

E03 вообще не выполнил содержательный catalog probe. E04 выполнил probe по
строке с procedural boilerplate, получил coverage `0.5`, затем потерял catalog
requirement. Оба вернули generic corporate insufficient answer.

E05 выбрал `mixed`, но catalog probe дал 0. Corporate retrieval ошибочно
интерпретировал «верхнюю часть наполнителя» как «верхнюю часть/корзину рабочего
стола» и построил ответ на документах про оргсхему, письменную коммуникацию и
рабочий стол. Это не источник по оборудованию и классифицировано как
`source_incorrect`.

## Corporate regression

```text
Route preserved: 5/5
PASS: 4
PARTIAL: 1
False catalog interception: 0
```

F01–F04 дали grounded corporate answers с тремя rendered sources каждый.
F05 остался на corporate route, но supporting evidence для onboarding новой
должности не прошёл evidence gate; результат — honest insufficient answer без
источников. Это PARTIAL, а не routing FAIL.

## Ambiguous handling

```text
Safe: 5/5
Overconfident: 0/5
```

G01/G02 показали явно обозначенные candidate lists, что допустимо условиями
набора. G03/G04/G05 не выбрали произвольный BOM или referent и завершились
insufficient response. G05 диагностически имеет `source_route=mixed`, но не
выдал фактического ответа и поэтому считается безопасным.

## Источники

```text
Responses requiring sources: 26
With structured/rendered sources: 26
Correct: 25 (96.15%)
Missing: 0
Incorrect: 1 (E05)
Misleading empty catalog blocks: 0
Catalog results with catalog sources: 21/21
```

Catalog sources в runtime artifact содержат PDF, assembly, position,
specification page, revision и machine. Пустой RAG counter не подменял catalog
sources. E05 — единственная ошибка provenance: sources существуют, но не
относятся к техническому предмету вопроса. F05 без supporting evidence не
включён в denominator grounded responses, так как система честно не дала
фактического ответа.

## Latency

| Actual route | Count | Median | p95 |
| --- | ---: | ---: | ---: |
| catalog | 20 | 0 ms | 16 ms |
| corporate | 13 | 5 094 ms | 152 328 ms |
| mixed | 4 | 4 156.5 ms | 140 531 ms |

Catalog values быстрее 1 ms округлены public result до `0 ms`, потому что
`elapsed_seconds` хранится с точностью до 0.001 s. Полный wall-clock evaluation
занял около 716.9 s. Самые медленные случаи: F03 — 152.3 s, E05 — 140.5 s,
F04 — 120.4 s, F01 — 112.8 s, F02 — 108.7 s.

## Failure Pareto

Причины multi-label: один case может иметь несколько причин, поэтому доля
считается от 27 reason assignments, а не от 8 non-PASS cases.

| Причина | Количество | Доля ошибок | Примеры case_id |
| --- | ---: | ---: | --- |
| `catalog_not_found` | 6 | 22.22% | B04, D04, D06, E03, E04, E05 |
| `missed_catalog_route` | 5 | 18.52% | B04, D04, D06, E03, E04 |
| `wrong_source_route` | 5 | 18.52% | B04, D04, D06, E03, E04 |
| `wrong_answer_mode` | 3 | 11.11% | A05, E03, E04 |
| `wrong_catalog_match_type` | 3 | 11.11% | B04, D04, D06 |
| `lost_mixed_requirement` | 2 | 7.41% | E03, E04 |
| `generic_insufficient` | 1 | 3.70% | F05 |
| `source_incorrect` | 1 | 3.70% | E05 |
| `source_missing` | 1 | 3.70% | F05 |

TOP-3 measured degradation signals are `catalog_not_found`,
`missed_catalog_route`, and `wrong_source_route`. They are correlated symptoms
of subject extraction/decomposition failures, not three independent engines.

## Case matrix

`Probe/result` distinguishes a weak probe count from candidates actually
returned to the user.

| Case | Expected | Actual | Match / mode | Probe/result | Grade | Причина |
| --- | --- | --- | --- | ---: | --- | --- |
| A01 | catalog | catalog | exact | 1/1 | PASS | — |
| A02 | catalog | catalog | exact | 1/1 | PASS | — |
| A03 | catalog | catalog | exact | 1/1 | PASS | — |
| A04 | catalog | catalog | exact | 1/1 | PASS | — |
| A05 | catalog | catalog | unavailable diagnostic | 0/0 | PARTIAL | wrong_answer_mode |
| B01 | catalog | catalog | prefix | 1/1 | PASS | — |
| B02 | catalog | catalog | normalized prefix | 1/1 | PASS | — |
| B03 | catalog | catalog | normalized prefix | 1/1 | PASS | — |
| B04 | catalog | corporate | insufficient | 0/0 | FAIL | missed route, not found |
| B05 | safe | corporate | insufficient | 0/0 | PASS | safe short fragment |
| C01 | catalog | catalog | FTS candidates | 5/5 | PASS | — |
| C02 | catalog | catalog | FTS candidates | 5/5 | PASS | — |
| C03 | catalog | catalog | FTS candidates | 5/5 | PASS | — |
| C04 | catalog | catalog | FTS candidates | 5/5 | PASS | — |
| C05 | catalog | catalog | FTS candidates | 5/5 | PASS | — |
| C06 | catalog | catalog | FTS candidates | 5/5 | PASS | — |
| D01 | catalog | catalog | natural FTS | 5/5 | PASS | — |
| D02 | catalog | catalog | natural FTS | 5/5 | PASS | — |
| D03 | catalog | catalog | natural FTS | 5/5 | PASS | — |
| D04 | catalog | corporate | insufficient | 5/0 | FAIL | weak subject extraction |
| D05 | catalog | catalog | natural FTS | 5/5 | PASS | — |
| D06 | catalog | corporate | insufficient | 5/0 | FAIL | weak subject extraction |
| E01 | mixed | mixed | partial_mixed | 5/5 | PASS | — |
| E02 | mixed | mixed | partial_mixed | 5/5 | PASS | — |
| E03 | mixed | corporate | insufficient | 0/0 | FAIL | lost catalog requirement |
| E04 | mixed | corporate | insufficient | 5/0 | FAIL | lost catalog requirement |
| E05 | mixed | mixed | partial_mixed | 0/0 | FAIL | catalog miss + wrong corporate source |
| F01 | corporate | corporate | full answer | 0/0 | PASS | — |
| F02 | corporate | corporate | full answer | 0/0 | PASS | — |
| F03 | corporate | corporate | full answer | 0/0 | PASS | — |
| F04 | corporate | corporate | full answer | 0/0 | PASS | — |
| F05 | corporate | corporate | insufficient | 0/0 | PARTIAL | no supporting source |
| G01 | safe | catalog | candidate list | 5/5 | PASS | controlled, not exact |
| G02 | safe | catalog | candidate list | 5/5 | PASS | controlled, not exact |
| G03 | safe | corporate | insufficient | 1/0 | PASS | no arbitrary BOM |
| G04 | safe | corporate | insufficient | 0/0 | PASS | no referent invented |
| G05 | safe | mixed | insufficient | 0/0 | PASS | no referent invented |

Полные question, final answer, structured results, source payloads, diagnostics,
chunk IDs, interaction IDs и durations сохранены в `results.json`.

## Interaction Analytics

Изолированная evaluation DB:

```text
interactions logged: 37
feedback events: 0
runtime errors: 0
timeouts: 0
```

Существующий exporter `scripts/export_interaction_stats.py` использован без
параллельного analytics framework. Exporter видит 37 interactions и 0 feedback.

Ограничение текущей production analytics serialization: catalog-specific
`source_route`, probe coverage/match diagnostics и structured catalog sources
не входят в текущий whitelist/`interaction_sources`. Поэтому детальная
Catalog-аналитика этого прогона сохранена runner-ом из returned `RagAnswer` в
evaluation artifacts; production analytics schema и answer path не менялись.

Общая production analytics DB анализировалась отдельно и не смешивалась с
evaluation:

```text
total interactions in shared DB: 75
raw feedback events: 4
interactions with latest feedback: 3
latest positive: 1
latest negative: 2
```

Два negative events относятся к одному interaction, поэтому existing exporter
считает три interactions с latest feedback. Эти реальные feedback events не
использовались как synthetic grades текущих 37 вопросов.

## Artifacts

- `data/test_runs/catalog_evaluation_01/20260812T145307+0300/results.json`
- `data/test_runs/catalog_evaluation_01/20260812T145307+0300/summary.json`
- `data/test_runs/catalog_evaluation_01/20260812T145307+0300/failures.csv`
- `data/test_runs/catalog_evaluation_01/20260812T145307+0300/analytics_summary.json`
- `data/test_runs/catalog_evaluation_01/20260812T145307+0300/analytics_export/`
- `data/test_runs/catalog_evaluation_01/20260812T145307+0300/real_user_analytics_summary.json`

`data/test_runs/` и `*.db` игнорируются Git.

## Проверка evaluation helper

- `pytest tests/test_catalog_evaluation_runner.py -q`: 5 passed; один
  `PytestCacheWarning` из-за отсутствия прав создать `.pytest_cache`, тесты не
  затронуты.
- `python -m compileall scripts`: PASS.
- Полный project pytest не запускался согласно policy для evaluation-only
  итерации без production changes.

## Recommendations — не более трёх

1. Исправить deterministic extraction предметной части и mixed decomposition
   для конструкций `входят в ...`, `есть ли в каталоге ...`, `что с ним надо
   делать ...`, `расскажи порядок ...`, `есть ли инструкция ...`. Это покрывает
   пять главных catalog/mixed промахов D04, D06, E03, E04, E05.
2. Отдельно скорректировать code-like policy для достаточно длинного чисто
   цифрового fragment `442351`, сохранив safe guard для `44`; одновременно
   исправить diagnostic mapping `no_candidates → catalog_not_found` для A05.
3. Добавить domain-consistency guard для corporate evidence в mixed equipment
   queries и затем расширить analytics whitelist catalog route/probe/source
   полями. Сначала исключить E05-подобную подмену оборудования «корзиной рабочего
   стола», затем сделать такую ошибку наблюдаемой без evaluation runner.

## Scope confirmation

- Catalog parser: не изменён.
- Catalog Store / DB: не изменены и не переимпортировались.
- FTS/ranking: не изменены.
- Source routing / QueryAnalyzer / EvidenceAssessor / RAG: не изменены.
- Fake user feedback: не создавался.
- Commit/push: не выполнялись.
