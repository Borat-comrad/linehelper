# Catalog Corrective 04B — natural, mixed и domain guard

## Статус и baseline

- Статус: PASS.
- Ветка: `refactor/rag-architecture-v2`.
- HEAD до и после работ: `63ceb4d532ecaf4704eb540eb48bb0e0a04c1e67`.
- Commit/push не выполнялись.
- Рабочее дерево до начала было уже изменено предыдущими Catalog Corrective,
  Navigation 04, Evaluation 01 и Structured Routing 04. Эти изменения не
  откатывались и не присваиваются 04B.
- Baseline Evaluation 01: 37 вопросов, PASS 29, PARTIAL 2, FAIL 6,
  routing accuracy 83,87%, false catalog routes 0.

До production-изменений B04, D04, D06, E03, E04 и E05 были по одному разу
воспроизведены через публичный `RagAnswerGenerator.answer()`.

## Root causes

| Кейс | Наблюдение до исправления | Root cause |
|---|---|---|
| B04 `442351` | `corporate`, catalog result 0 | Bare numeric extraction принимал только 8+ цифр, хотя Catalog Search уже безопасно разрешал substring от 6 normalized characters. |
| D04 | probe query `входят прижимное устройство`, coverage 0,667 | Служебный глагол `входят` оставался в предметной части. |
| D06 | probe query `каталоге вал нижней части укупорщика`, запрос ушёл в corporate | Boilerplate не удалялся; entity `вал` и assembly context не были представлены отдельно. |
| E03 | catalog probe не был выполнен | Mixed detection сравнивал только полные словоформы и не распознал `обслуживании`; catalog clause не отделялся от procedure clause. |
| E04 | coverage 0,5, catalog requirement lost | Аналогично: `обслуживания` и `расскажи порядок` оставались в Catalog FTS query. |
| E05 | unrelated chunks про оргсхему/корзины/рабочий стол стали supporting | Catalog clause не выделялся; после этого corporate evidence не проходило дополнительную положительную проверку связи с equipment subject. |

## Изменения 04B

### Long numeric fragment

Chat extraction использует существующий `MIN_CODE_SUBSTRING_LENGTH = 6` и
существующую normalization/search pipeline. Поэтому `442351` проходит exact →
prefix → normalized prefix → normalized substring и находит `X44235100`.
`44` и `123` не инициируют широкий substring scan. FTS не является первым
путём для code-like fragment.

### Natural subject extraction

Добавлена небольшая детерминированная структура `NaturalCatalogQuery`, которая
хранит subject, entity terms, assembly context и mixed signal. Поддерживаемые
общие формы включают `какие детали входят в X`, `что входит в X`, `найди детали
X`, `есть ли Y для X`, `покажи Y в X`, `что есть по X` и `что стоит в X`.

- D04: subject = `прижимное устройство`.
- D06: entity = `вал`; assembly context = `нижней части укупорщика`.
- D06 candidates проходят оба условия: entity совпадает в part fields и context
  совпадает в assembly fields. На реальной DB результат — только `X44235100`.

### Mixed decomposition

Procedural intent определяется общими stems (`обслуж*`, `ремонт*`, `замен*`,
`настро*`, `процедур*`, `инструкц*`). При conjunction catalog clause берётся до
первой procedural clause. Referent сохраняется как subject catalog clause,
поэтому E03/E04 формируют независимые catalog и maintenance requirements.

Если catalog найден, а supporting procedure evidence отсутствует, возвращается
`partial_mixed`; catalog facts и sources сохраняются, generic insufficient answer
не заменяет найденные знания.

### Domain-consistency guard

Guard находится в composition layer `RagAnswerGenerator`, после обычного
`EvidenceAssessor`, и применяется только к mixed запросу с уже подтверждённым
Catalog Store outcome. Глобальная evidence semantics не менялась.

Из catalog subject берутся содержательные lexical stems; generic слова (`часть`,
`узел`, `деталь`, `устройство`, directional adjectives) не являются достаточным
якорем. Supporting corporate chunk обязан иметь положительный token/stem anchor
с equipment subject. Это не blacklist документов. Для E05 chunks про рабочий
стол, корзины и оргсхему не содержат subject anchor `наполнитель` и исключаются
из supporting sources.

Diagnostics дополнены: `catalog_subject`, `catalog_entity_terms`,
`catalog_assembly_context`, `domain_consistency_checked`,
`domain_consistency_passed`, `domain_consistency_reason`.

## Public-flow smoke после исправления

| Кейс | Route / mode | Результат |
|---|---|---|
| B04 | `catalog`, `catalog_substring` | `X44235100`, `X44235103`, `X44235105`, `X44235108`; `X44235100` первый. |
| D04 | `catalog`, catalog candidates | subject `прижимное устройство`, 5 реальных structured positions. |
| D06 | `catalog`, catalog candidates | entity `вал`, context `нижней части укупорщика`, только `X44235100`, assembly `X44236986`, position 70. |
| E03 | `mixed`, `partial_mixed` | Catalog FOUND (5), procedure NOT_FOUND, corporate sources 0. |
| E04 | `mixed`, `partial_mixed` | Catalog FOUND (5), procedure NOT_FOUND, corporate sources 0. |
| E05 | `mixed`, `partial_mixed` | Catalog FOUND (5); corporate NOT_FOUND after domain guard; unrelated corporate sources 0. |

E05 diagnostics: `domain_consistency_checked=true`,
`domain_consistency_passed=false`, reason
`corporate_evidence_lacks_catalog_subject_anchor`.

## Regression

- Structured H01–H07: 7/7.
- Structured → corporate leakage: 0/7.
- Exact `X44235100`: catalog exact.
- Prefix `X44236`: catalog code candidates.
- FTS `прижимное устройство`: catalog candidates.
- `можно ли дать распоряжение устно`: corporate, 3 corporate sources.
- `кто отвечает за документооборот`: corporate, 3 corporate sources.
- False catalog interception в regression smoke: 0.

## Tests

- Targeted corrective tests: 27 passed.
- Catalog/structured/navigation/presentation regression slice: 109 passed.
- `python -m compileall linehelper tests scripts`: PASS.
- `pytest tests scripts`: 628 passed in 25.80s.
- Full Evaluation 02, 71-case pack и organization pack не запускались.

## Изменённые в 04B production-файлы

- `linehelper/catalogs/chat.py`
- `linehelper/catalogs/models.py`
- `linehelper/llm/answer_generator.py`

Целевые тесты 04B добавлены в уже существовавшие untracked test-файлы:

- `tests/test_catalog_corrective.py`
- `tests/test_catalog_natural_routing.py`
- `tests/test_catalog_mixed_decomposition.py`

Parser, Catalog DB/schema/content, global FTS ranking, core EvidenceAssessor и
1C flow не менялись.

## Ограничения

Natural extraction остаётся консервативным rule-based MVP, а не общим русским
морфологическим parser. Domain guard требует предметный lexical anchor; при
корректном документе, который описывает оборудование только через местоимение
или незафиксированный alias, он предпочтёт безопасный NOT_FOUND. Это осознанный
trade-off против ложного технического ответа. Следующий шаг — полный Evaluation
02 по ранее зафиксированному acceptance gate.
