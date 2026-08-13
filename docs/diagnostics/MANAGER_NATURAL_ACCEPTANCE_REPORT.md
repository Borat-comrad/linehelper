# Manager Natural Language Acceptance Pack

## 1. Контрольная точка

- Branch: `refactor/rag-architecture-v2`
- HEAD: `f0b3bae981c91f03e9151713cfe944409227bb69`
- Runtime: штатный `RagAnswerGenerator.answer()` с настроенными Catalog Store,
  corporate memory и interaction logger.
- Run ID: `manager_natural_acceptance_20260812T174614+0300`
- Conversation history: не использовалась; каждый вопрос независим.
- Interaction analytics: 36/36 interactions записаны, feedback events: 0.
- Production-код в `linehelper/` не изменялся.
- Catalog DB, corporate DB, ingestion и 1С не изменялись.
- Commit/push не выполнялись.

Созданы:

- `scripts/run_manager_natural_acceptance.py`
- `tests/test_manager_natural_acceptance.py`
- `docs/diagnostics/MANAGER_NATURAL_ACCEPTANCE_REPORT.md`

Machine-readable артефакты находятся в игнорируемом Git каталоге:

- `data/test_runs/manager_natural_acceptance/20260812T174614+0300/results.json`
- `data/test_runs/manager_natural_acceptance/20260812T174614+0300/summary.json`
- `data/test_runs/manager_natural_acceptance/20260812T174614+0300/failures.csv`
- `data/test_runs/manager_natural_acceptance/20260812T174614+0300/analytics.db`

## 2. Методика

Pack содержит 36 новых естественных запросов менеджера, не являющихся копией
Evaluation 01. Для каждого сохранены expected contract, фактический route,
QueryPlan, extracted subject/entity/context, Catalog Store results, corporate
sources, rejected evidence, answer mode, final answer, latency и interaction ID.

Оценка детерминированная и не сравнивает final answer дословно. PASS требует
правильный source route, состояния catalog/corporate requirements, ожидаемые
structured entities, отсутствие запрещённого evidence и корректный answer mode.

После единственного live run исправлена только ошибка evaluator precedence:
явные `catalog_not_found`/`catalog_search_not_found` сначала ошибочно получали
generic mode `catalog`. Существующий `results.json` был regrade без повторного
runtime-вызова. Production-результаты не изменялись.

## 3. Результат

| Группа | Сценариев | PASS | FAIL |
|---|---:|---:|---:|
| A — Catalog natural language | 6 | 1 | 5 |
| B — Corporate natural language | 5 | 5 | 0 |
| C — Mixed catalog + corporate | 5 | 3 | 2 |
| D — Ambiguous requests | 4 | 2 | 2 |
| E — Partial answers | 4 | 1 | 3 |
| F — NOT_FOUND | 4 | 3 | 1 |
| G — Domain guard | 4 | 1 | 3 |
| H — Human noise/paraphrases | 4 | 1 | 3 |
| **Итого** | **36** | **17** | **19** |

Technical success rate: **47,22%**.

Corporate natural language — сильная сторона baseline: 5/5. Прямые честные
Catalog NOT_FOUND — 3/3. Основной разрыв качества находится между узкими
поддержанными формами Catalog Corrective и более свободными менеджерскими
перефразировками.

## 4. Failures

### A — Catalog natural language

**A01 — UNDERSTANDING**

- Query: `Подбери вал, который установлен в нижней части укупорщика.`
- Expected: catalog FOUND, `X44235100`, subject/entity содержит вал и контекст
  нижней части укупорщика.
- Actual: corporate NOT_FOUND; analyzer subject `вал для укупарщика`; catalog не
  вызван.
- Причина: свободная конструкция `подбери ... который установлен` не попала в
  deterministic natural extraction.

**A02 — ROUTING**

- Query: `Перечисли комплектующие прижимного устройства.`
- Expected: catalog FOUND, `58803877S002`.
- Actual: corporate NOT_FOUND; probe subject сохранил boilerplate
  `перечисли комплектующие` и отклонил результаты.

**A04 — ROUTING**

- Query: `Мне нужны детали, входящие в узел 20411617.`
- Expected: catalog assembly contents, `58803877S002`.
- Actual: corporate FOUND на нерелевантных документах; catalog subject
  `нужны входящие узел 20411617`, catalog NOT_FOUND.

**A05 — ROUTING**

- Query: `Покажи варианты деталей с началом кода X44235.`
- Expected: catalog partial-code candidates, включая `X44235100`.
- Actual: corporate NOT_FOUND; code-like token не был выделен из естественной
  оболочки.

**A06 — ROUTING**

- Query: `Поищи деталь, в коде которой встречается 442351.`
- Expected: `catalog_substring`, `X44235100`.
- Actual: corporate NOT_FOUND; длинный numeric fragment работает в bare форме,
  но не извлекается из этой фразы.

### C — Mixed

**C02 — CATALOG_RETRIEVAL**

- Query: `Покажи комплектующие прижимного устройства и уточни, имеется ли порядок его ремонта.`
- Expected: catalog FOUND + corporate NOT_FOUND/FIELD-DEPENDENT → partial_mixed.
- Actual: route mixed, но catalog subject `комплектующие прижимного устройства`
  не прошёл strong catalog probe; итог generic NOT_FOUND.

**C04 — CATALOG_RETRIEVAL**

- Query: `Перечисли детали нижней части укупорщика; потом проверь инструкцию по их обслуживанию.`
- Expected: catalog FOUND + procedure checked.
- Actual: route mixed, subject загрязнён `перечисли ... потом проверь`; catalog
  requirement потерян, generic NOT_FOUND.

### D — Ambiguous

**D01 — CORPORATE_RETRIEVAL**

- Query: `Что есть по этому валу?`
- Expected: clarification или controlled NOT_FOUND без evidence.
- Actual: corporate FOUND с employee records `Валучева Екатерина`,
  `Чистякова Валерия`, `Отдел 9 — Учёта`; final answer признаёт нехватку сведений,
  но sources приняты как supporting.

**D04 — CORPORATE_RETRIEVAL**

- Query: `Нужна информация по детали.`
- Expected: clarification/NOT_FOUND.
- Actual: corporate FOUND с communication/org documents, хотя конкретная деталь
  не задана.

### E — Partial answers

**E01 — ROUTING**

- Query: `Что есть в прижимном устройстве и предусмотрена ли процедура его калибровки?`
- Expected: mixed, catalog FOUND, calibration procedure NOT_FOUND, partial_mixed.
- Actual: corporate NOT_FOUND; mixed/catalog requirements не созданы.

**E03 — CORPORATE_RETRIEVAL**

- Query: `Найди вал нижней части укупорщика и скажи, есть ли регламент его смазки.`
- Expected: catalog FOUND, lubrication procedure NOT_FOUND, partial_mixed.
- Actual: full_mixed; unrelated corporate sources `Регламент по должностным
  папкам`, `Регламент по использованию оргсхемы`, `Календарь` приняты как FOUND.
  Domain guard прошёл из-за общих lexical anchors, не подтверждающих смазку.

**E04 — ROUTING**

- Query: `Проверь каталог на узел КАДРЫ-001 и напомни правила начала работы в новой должности.`
- Expected: catalog NOT_FOUND + corporate FOUND → reverse partial_mixed.
- Actual: corporate NOT_FOUND; compound requirements не decomposed, известная
  corporate часть потеряна.

### F — NOT_FOUND

**F04 — CORPORATE_RETRIEVAL**

- Query: `Есть ли утверждённая процедура телепортации сотрудников между офисами?`
- Expected: honest corporate NOT_FOUND.
- Actual: corporate FOUND с документами `Мероприятия`, `Работа с задачами`,
  `ИП-0002 Цели и замыслы компании Serviceline`. Ответ при этом говорит, что
  данных недостаточно. Это ложный FOUND/source attribution.

### G — Domain guard

**G01 — ROUTING**

- Query: `Что стоит в верхней части наполнителя и есть ли порядок работы с рабочим столом этого узла?`
- Expected: mixed, catalog FOUND, unrelated desktop evidence rejected.
- Actual: corporate NOT_FOUND; analyzer выбрал subject `рабочий стол`, catalog
  clause потерян до domain guard.

**G03 — ROUTING**

- Query: `Что находится в нижней части укупорщика и есть ли общий порядок работы с этой частью?`
- Expected: mixed/partial_mixed с catalog FOUND.
- Actual: corporate NOT_FOUND; natural form `что находится` не создала catalog
  requirement.

**G04 — ROUTING**

- Query: `Покажи состав верхней части наполнителя и найди документ «Рабочий стол и работа с отчётами» для этого узла.`
- Expected: mixed, catalog FOUND, named but domain-inconsistent desktop document
  не использовать как инструкцию узла.
- Actual: corporate FOUND; три chunks `Рабочий стол и работа с отчетами`, catalog
  NOT_FOUND. Final answer описывает компоновку desktop widgets.

### H — Human noise

**H01 — ROUTING**

- Query: `Подскажите, пожалуйста: вал в нижней части укупорщика есть?`
- Expected: catalog FOUND, `X44235100`.
- Actual: polite boilerplate остался в subject, catalog probe отклонён, corporate
  NOT_FOUND.

**H03 — ROUTING**

- Query: `Поищи, пожалуйста, икс 44235 — начало кода детали.`
- Expected: normalized partial code, `X44235100`.
- Actual: spoken `икс` и оболочка не преобразованы в code candidate; corporate
  NOT_FOUND.

**H04 — UNDERSTANDING**

- Query: `По нижней части укупорщика: вал там какой стоит?`
- Expected: catalog FOUND, `X44235100`.
- Actual: subject reduced to assembly only; entity/word-order extraction не
  сработали, corporate NOT_FOUND.

## 5. Leakage, evidence и answer modes

- Corporate leakage в успешно catalog-routed responses: **0**.
- Catalog leakage в corporate responses: **0**.
- Явно hallucinated/irrelevant accepted evidence cases: **2** (`F04`, `G04`).
- Дополнительные questionable supporting-evidence cases: `D01`, `D04`, `E03`,
  где route/mode стали FOUND без требуемой предметной поддержки.
- Неправильные NOT_FOUND: catalog FOUND был потерян в A01/A02/A04/A05/A06,
  C02/C04, E01, G01/G03, H01/H03/H04.
- Неправильные mixed/partial_mixed: C02, C04, E01, E03, E04, G01, G03, G04.
- Все три deterministic Catalog NOT_FOUND (`F01–F03`) корректны и не содержат
  выдуманных сущностей.

## 6. Failure distribution

| Вероятный слой | Количество |
|---|---:|
| ROUTING | 11 |
| CORPORATE_RETRIEVAL | 4 |
| UNDERSTANDING | 2 |
| CATALOG_RETRIEVAL | 2 |

Наиболее частый симптом — `wrong_answer_mode` (19), но первичный root layer чаще
source routing/extraction: `wrong_source_route` встречается в 13 случаях.

## 7. Проверки

- Runner tests: **5 passed**.
- Полный `python -m pytest`: **633 passed in 25.77s**.
- `python -m compileall linehelper`: **PASS**.

## 8. Рекомендации следующего corrective-пакета

1. Обобщить natural catalog clause extraction на action verbs, polite
   boilerplate, word order и embedded code tokens; валидировать прежде всего
   A/H и не расширять FTS глобально.
2. Разделить смешанные requirements до catalog probe для пунктуации и более
   широких procedural форм; сохранять успешную половину запроса независимо.
3. Усилить subject/requirement consistency corporate evidence: отдельное
   подтверждение не только equipment subject, но и запрошенного procedural
   действия (`смазка`, `калибровка`); ambiguous queries должны безопасно
   завершаться без случайных employee/general-policy sources.

Ни одна рекомендация в этой итерации не реализована.
