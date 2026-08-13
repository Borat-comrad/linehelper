# Manager Natural Acceptance — triage 19 failures

## 1. Executive summary

Ручной triage выполнен по исходному run
`manager_natural_acceptance_20260812T174614+0300`. Для каждого из 19 FAIL
проверены scenario expectation, полный runtime result, QueryPlan, извлечённый
subject/context, catalog и corporate evidence, rejected evidence, answer mode и
финальный ответ. Acceptance expectations и production-код не изменялись.

Контрольный повтор через тот же public runtime:
`manager_natural_acceptance_20260813T125812+0300`. Он воспроизвёл исходный
результат без расхождений по case IDs или группам: 17 PASS, 19 FAIL, 47,22%.

Итог triage:

| Категория | Количество | Cases |
| --- | ---: | --- |
| `CONFIRMED_PRODUCTION_DEFECT` | 17 | A01, A02, A04, A05, A06, C02, C04, E01, E03, E04, F04, G01, G03, G04, H01, H03, H04 |
| `BAD_EXPECTATION` | 0 | — |
| `AMBIGUOUS_QUERY` | 2 | D01, D04 |
| `DATA_LIMITATION` | 0 | — |

Главный системный кластер — не Catalog FTS: в большинстве случаев Catalog
Search получает уже потерянный или загрязнённый subject. Второй независимый
кластер — принятие корпоративных chunks без положительной предметной связи.

## 2. Branch / HEAD

- Branch: `refactor/rag-architecture-v2`
- HEAD: `f0b3bae981c91f03e9151713cfe944409227bb69`
- HEAD subject: `fix(catalogs): улучшить разбор естественных и смешанных запросов`
- До triage в working tree уже находились untracked acceptance artifacts:
  `scripts/run_manager_natural_acceptance.py`,
  `tests/test_manager_natural_acceptance.py`,
  `docs/diagnostics/MANAGER_NATURAL_ACCEPTANCE_REPORT.md`.
- Текущая итерация добавляет только этот диагностический отчёт.
- `linehelper/`, Catalog DB и acceptance expectations не изменялись.

## 3. Scope and evidence

Основной artifact:

`data/test_runs/manager_natural_acceptance/20260812T174614+0300/results.json`

Контрольный artifact:

`data/test_runs/manager_natural_acceptance/20260813T125812+0300/results.json`

Для проверки intended behavior также прочитаны существующие regression tests:

- `tests/test_catalog_corrective.py` — bare six-character substring и code guards;
- `tests/test_catalog_structured_routing.py` — structured node/part/position routes;
- `tests/test_catalog_natural_routing.py` — canonical natural subject/context;
- `tests/test_catalog_mixed_decomposition.py` — canonical mixed decomposition;
- `tests/test_answer_generator.py` — corporate onboarding evidence.

Triage не вызывал отдельные retrieval helpers и не менял результаты run.

## 4. Original acceptance result

| Группа | PASS | Total |
| --- | ---: | ---: |
| A Catalog natural | 1 | 6 |
| B Corporate natural | 5 | 5 |
| C Mixed | 3 | 5 |
| D Ambiguous | 2 | 4 |
| E Partial answers | 1 | 4 |
| F NOT_FOUND | 3 | 4 |
| G Domain guard | 1 | 4 |
| H Human noise | 1 | 4 |
| **Total** | **17** | **36** |

Official success rate: **47,22%**. Этот показатель не пересчитан и не
заменён triage-метрикой.

## 5. Triage methodology

`CONFIRMED_PRODUCTION_DEFECT` назначался только при однозначном обязательном
сегменте запроса и воспроизводимой ошибке в runtime trace. Первичный слой
выбирался по самой ранней точке причинной цепочки:

- неверный subject/code/context до retrieval → `UNDERSTANDING`;
- правильный смысл, но неправильный выбор источника → `ROUTING`;
- отдельные clauses распознаны не полностью → `MIXED_COMPOSITION`;
- нерелевантный chunk помечен supporting при правильном route →
  `EVIDENCE_GUARD`.

Если Catalog Search получил неверный probe query, последующий zero-result не
считался доказательством дефекта `CATALOG_RETRIEVAL`.

## 6. Confirmed defects by layer

| Подтверждённый слой | Count | Cases |
| --- | ---: | --- |
| `UNDERSTANDING` | 10 | A01, A02, A05, A06, C02, C04, E04, H01, H03, H04 |
| `ROUTING` | 3 | A04, E01, G03 |
| `MIXED_COMPOSITION` | 2 | G01, G04 |
| `EVIDENCE_GUARD` | 2 | E03, F04 |
| `CATALOG_RETRIEVAL` | 0 | — |
| `CORPORATE_RETRIEVAL` | 0 | — |
| `FINAL_ANSWER` | 0 | — |
| `DATA` | 0 | — |

Первоначальные labels `CATALOG_RETRIEVAL` для C02/C04 сняты: route был mixed,
но probe получил загрязнённые queries. Первоначальные labels
`CORPORATE_RETRIEVAL` для E03/F04 сняты: retrieval вернул слабые candidates,
однако подтверждённая ошибка произошла при объявлении их supporting evidence.

## 7. Routing defect taxonomy

Подтверждённых дефектов, у которых **первичный** слой — `ROUTING`: 3.

| Подтип | Count | Cases |
| --- | ---: | --- |
| `catalog_expected__corporate_actual` | 1 | A04 |
| `mixed_expected__corporate_actual` | 2 | E01, G03 |
| `catalog_expected__mixed_actual` | 0 | — |
| `mixed_expected__catalog_actual` | 0 | — |
| `corporate_expected__catalog_actual` | 0 | — |
| `negative_query_overrouted` | 0 | — |

Наблюдаемый wrong-source route встречается шире — 13 раз: восемь
`catalog → corporate` и пять `mixed → corporate`. Но десять из них имеют более
раннюю причину в UNDERSTANDING либо MIXED_COMPOSITION и поэтому не включены в
первичный routing count.

## 8. Detailed review

### A01

**Query:** `Подбери вал, который установлен в нижней части укупорщика.`

**Original expected:** catalog; catalog FOUND; corporate not requested; entity
`X44235100`; catalog answer.

**Actual:** QueryPlan ошибочно выбрал `intent=equipment_it_request`,
`requested_fact_type=procedure`; normalized question исказил `укупорщика` в
`укупарщика`; subject стал `вал для укупарщика`. Catalog probe не выполнялся,
route=corporate. Corporate candidates 46/29/49 были отклонены; final mode
`not_found`; ответ — generic insufficient procedure.

**Observed mismatch:** однозначная деталь и assembly context потеряны до
маршрутизации.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** запрос содержит и entity `вал`, и конкретный контекст узла;
Catalog Store имеет подтверждённый `X44235100`. Ошибка предшествует retrieval.

**Action / priority:** `FIX_PRODUCTION`, P1.

### A02

**Query:** `Перечисли комплектующие прижимного устройства.`

**Original expected:** catalog FOUND; `58803877S002`; catalog answer.

**Actual:** route=corporate, но catalog probe выполнялся с subject
`перечисли комплектующие прижимного устройства`; entity/context не выделены;
field coverage 0,5, coherent results 0. Catalog и corporate states — not found,
final `not_found`.

**Observed mismatch:** служебные слова и тип выдачи остались частью FTS probe,
а предмет `прижимное устройство` не стал самостоятельным subject.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** это естественная, но однозначная просьба о составе узла. Zero
result не доказывает дефект FTS, потому что query сформирован неверно.

**Action / priority:** `FIX_PRODUCTION`, P1.

### A04

**Query:** `Мне нужны детали, входящие в узел 20411617.`

**Original expected:** catalog assembly contents; `58803877S002`; corporate not
requested; catalog answer.

**Actual:** normalized question корректно содержит `узел 20411617`, но
structured intent не сработал; catalog subject стал
`нужны входящие узел 20411617`. Route=corporate. В supporting попали
`Регламент по письменной коммуникации` (совпадение с «Входящие»),
`Исполнительный совет` и `Совет по качеству`; response mode
`corporate_found`, хотя final text сообщает недостаток данных.

**Observed mismatch:** явный assembly code не получил обязательный structured
priority и дошёл до corporate RAG.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `ROUTING`;
subtype `catalog_expected__corporate_actual`.

**Reasoning:** сам код и слово `узел` распознаны в QueryPlan, поэтому первичная
ошибка — не FTS и не данные, а пропуск deterministic structured route.

**Action / priority:** `FIX_PRODUCTION`, P0.

### A05

**Query:** `Покажи варианты деталей с началом кода X44235.`

**Original expected:** catalog prefix/normalized-prefix/substring candidates,
включая `X44235100`.

**Actual:** code token не извлечён; subject=`документы компании`, catalog probe
query=`варианты деталей началом кода x44235`; route=corporate, both states not
found, final `not_found`.

**Observed mismatch:** embedded technical code не передан в существующий
code-oriented search path.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** `X44235` однозначно обозначен как начало кода. Bare prefix path
уже существует; не сработало извлечение из фразы.

**Action / priority:** `FIX_PRODUCTION`, P1.

### A06

**Query:** `Поищи деталь, в коде которой встречается 442351.`

**Original expected:** `catalog_substring`; `X44235100`.

**Actual:** subject=`детали с номером 442351`, но catalog probe query содержит
`поищи коде которой встречается 442351`; code-like token не маршрутизирован в
normalized substring. Route=corporate, result not found.

**Observed mismatch:** длинный допустимый code fragment извлечён как общий текст.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** существующий regression подтверждает bare `442351` →
`X44235100`; дефект находится в natural wrapper extraction.

**Action / priority:** `FIX_PRODUCTION`, P1.

### C02

**Query:** `Покажи комплектующие прижимного устройства и уточни, имеется ли порядок его ремонта.`

**Original expected:** mixed; catalog FOUND (`58803877S002`); corporate
FOUND/NOT_FOUND; full/partial mixed.

**Actual:** route=mixed и requirements созданы, но catalog subject остался
`комплектующие прижимного устройства`; field coverage 0,667, coherent results
0. Corporate chunks 29/44/16 отклонены. Оба requirements not found/pending;
final `not_found`.

**Observed mismatch:** mixed detection сработал, но catalog clause не очищен до
предмета `прижимное устройство`.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** route и absence корпоративной процедуры разумны; catalog failure
вызван probe formulation, а не доказанным дефектом search index.

**Action / priority:** `FIX_PRODUCTION`, P1.

### C04

**Query:** `Перечисли детали нижней части укупорщика; потом проверь инструкцию по их обслуживанию.`

**Original expected:** mixed; catalog FOUND (`X44236986`); partial/full mixed.

**Actual:** route=mixed, однако semicolon не разделил clauses: catalog subject
`перечисли нижней части укупорщика потом проверь`; entity/context пусты,
coverage 0,5, coherent 0. Catalog/corporate not found, final `not_found`.

**Observed mismatch:** punctuation и discourse marker попали в catalog query.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** canonical mixed route существует; retrieval не получил
адекватный subject.

**Action / priority:** `FIX_PRODUCTION`, P1.

### D01

**Query:** `Что есть по этому валу?`

**Original expected:** safe/corporate; catalog not requested/not found;
corporate not found; clarification/not found.

**Actual:** referent не разрешён; subject=`вал`, catalog subject=`этому валу`.
Corporate retrieval вернул `Валучева Екатерина`, `Чистякова Валерия` и
`Отдел 9 — Учёта`, диагностически помеченные supporting; mode
`corporate_found`. Финальный текст, однако, прямо сообщает, что найденные данные
о сотрудниках не содержат информации о «вале».

**Observed mismatch:** structured mode/source attribution расходятся с
семантически безопасным финальным отказом.

**Triage:** `AMBIGUOUS_QUERY`; layer `AMBIGUITY`.

**Reasoning:** без conversation state невозможно установить, какой вал имеется
в виду. Система не выбрала случайную catalog entity и не выдумала факт; final
answer безопасно обозначил отсутствие ответа. Сопоставление фамилий следует
считать диагностическим риском evidence attribution, но оно не превращает
неразрешимый запрос в однозначный production requirement.

**Action / priority:** `KEEP_AS_AMBIGUOUS_CASE`, N/A.

### D04

**Query:** `Нужна информация по детали.`

**Original expected:** safe/corporate; catalog not requested/not found;
corporate not found; clarification/not found.

**Actual:** предмет отсутствует; analyzer дал subject=`детали компании`, catalog
subject=`нужна`. Corporate candidates: `Регламент по письменной коммуникации`,
`Навигатор команды_ServiceLine`, `Исполнительный совет`; mode
`corporate_found`, но final answer — `В найденных источниках недостаточно данных.`

**Observed mismatch:** internal FOUND не согласован с безопасным final response;
единственную catalog entity выбрать невозможно.

**Triage:** `AMBIGUOUS_QUERY`; layer `AMBIGUITY`.

**Reasoning:** запрос не содержит ни кода, ни названия, ни assembly context.
Production не галлюцинирует деталь; controlled insufficiency является
допустимым поведением, хотя clarification была бы полезнее.

**Action / priority:** `KEEP_AS_AMBIGUOUS_CASE`, N/A.

### E01

**Query:** `Что есть в прижимном устройстве и предусмотрена ли процедура его калибровки?`

**Original expected:** mixed; catalog FOUND (`58803877S002`); corporate not
found; `partial_mixed`.

**Actual:** subject=`прижимное устройство` извлечён корректно, но requirements
не созданы, catalog probe не выполнен, route=corporate. Corporate chunks
39/29/46 отклонены; final generic `not_found`.

**Observed mismatch:** правильный equipment subject не получил mixed route, и
доступная catalog часть полностью потеряна.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `ROUTING`;
subtype `mixed_expected__corporate_actual`.

**Reasoning:** это не data limitation: отсутствие процедуры допустимо, но
catalog requirement однозначен и удовлетворим.

**Action / priority:** `FIX_PRODUCTION`, P1.

### E03

**Query:** `Найди вал нижней части укупорщика и скажи, есть ли регламент его смазки.`

**Original expected:** mixed; catalog FOUND (`X44235100`); corporate not found;
`partial_mixed`.

**Actual:** mixed route и catalog часть корректны: `X44235100`, assembly
`X44236986`, position 70. Corporate requirement ошибочно помечен FOUND на
chunks `Регламент по должностным папкам`, `Регламент по использованию оргсхемы`
и `Календарь`; mode=`full_mixed`. Финальный corporate раздел рассказывает о
событии «весь день» в календаре, а не о смазке вала.

**Observed mismatch:** нерелевантные corporate candidates объявлены supporting,
несмотря на отсутствие предметной связи.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `EVIDENCE_GUARD`.

**Reasoning:** route и catalog retrieval верны. Ошибка происходит при validation
corporate evidence и загрязняет final answer.

**Action / priority:** `FIX_PRODUCTION`, P0.

### E04

**Query:** `Проверь каталог на узел КАДРЫ-001 и напомни правила начала работы в новой должности.`

**Original expected:** mixed; catalog not found; corporate found; reverse
`partial_mixed` with onboarding evidence.

**Actual:** analyzer выбрал `intent=vacation`; decomposition отсутствует;
catalog not requested, route=corporate. Релевантные onboarding chunks 31/33/36
были найдены, но отклонены из-за subject mismatch (`начал` против форм слов
`работа/новая/должность`). Final generic not found.

**Observed mismatch:** две явные clauses не представлены requirements; кроме
того, analyzer ошибочно классифицировал onboarding как vacation.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** B05 и regression fixtures подтверждают наличие onboarding data;
это не data limitation. Самая ранняя ошибка — неверное понимание/decomposition.

**Action / priority:** `FIX_PRODUCTION`, P1.

### F04

**Query:** `Есть ли утверждённая процедура телепортации сотрудников между офисами?`

**Original expected:** corporate; catalog not requested; corporate not found;
final not found, без слабосвязанного evidence.

**Actual:** route=corporate корректен. Retrieval дал общие chunks
`Мероприятия`, `Работа с задачами`, `Цели и замыслы Serviceline`. Evidence plan
создал generic `primary_fact` с пустыми subject criteria и признал все три
supporting; state/mode=`corporate_found`. Final text всё же сообщает недостаток
данных.

**Observed mismatch:** отсутствующая процедура получила ложный structured FOUND
и вводящие в заблуждение sources.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `EVIDENCE_GUARD`.

**Reasoning:** route верен и ожидаемого знания нет; retrieval candidates сами по
себе допустимы, но evidence guard обязан был отклонить их по предмету.

**Action / priority:** `FIX_PRODUCTION`, P1.

### G01

**Query:** `Что стоит в верхней части наполнителя и есть ли порядок работы с рабочим столом этого узла?`

**Original expected:** mixed; catalog FOUND (`20411640`); corporate not found;
partial mixed, без desktop evidence.

**Actual:** analyzer сохранил только subject=`рабочий стол`; catalog requirement
не создан и probe не выполнен. Route=corporate; chunks 24/29/44 отклонены;
final not found.

**Observed mismatch:** однозначная первая clause о составе наполнителя потеряна
при композиции со второй, более неоднозначной clause.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `MIXED_COMPOSITION`.

**Reasoning:** даже если выражение «рабочий стол этого узла» требует уточнения,
это не оправдывает потерю полностью удовлетворимой catalog части.

**Action / priority:** `FIX_PRODUCTION`, P1.

### G03

**Query:** `Что находится в нижней части укупорщика и есть ли общий порядок работы с этой частью?`

**Original expected:** mixed; catalog FOUND (`X44236986`); corporate not found;
partial mixed.

**Actual:** subject=`нижняя часть укупорщика` корректен, но requirements не
созданы и catalog probe не выполнен. Route=corporate; chunks 29/30/44 отклонены;
final not found.

**Observed mismatch:** корректно извлечённый catalog subject не влияет на source
route.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `ROUTING`;
subtype `mixed_expected__corporate_actual`.

**Reasoning:** absence корпоративной процедуры допустима; потеря catalog facts —
нет.

**Action / priority:** `FIX_PRODUCTION`, P1.

### G04

**Query:** `Покажи состав верхней части наполнителя и найди документ «Рабочий стол и работа с отчётами» для этого узла.`

**Original expected:** mixed; catalog FOUND (`20411640`); corporate not found;
partial mixed; named desktop document запрещён как equipment instruction.

**Actual:** обе clauses слиты в один subject; catalog probe с длинной фразой дал
0, route=corporate. Corporate retrieval нашёл три chunks именно из дословно
названного документа `Рабочий стол и работа с отчетами`; mode
`corporate_found`. Final answer пересказывает компоновку desktop widgets и
полностью теряет catalog clause.

**Observed mismatch:** clear catalog requirement потерян; corporate document
показан без объяснения, что он относится к 1С:Документообороту, а не к узлу.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `MIXED_COMPOSITION`.

**Reasoning:** runner label `irrelevant_corporate_evidence_accepted` не является
первичным: документ релевантен его дословному названию. Ошибка начинается с
потери mixed decomposition и предметной привязки `для этого узла`.

**Action / priority:** `FIX_PRODUCTION`, P1.

### H01

**Query:** `Подскажите, пожалуйста: вал в нижней части укупорщика есть?`

**Original expected:** catalog FOUND; `X44235100`; no corporate.

**Actual:** polite/noise tokens остались в catalog subject
`подскажите пожалуйста вал нижней части укупорщика`; analyzer subject стал
`внутренний устройство (укупорщик)`. Probe coverage 0,667, coherent 0;
route=corporate, final not found.

**Observed mismatch:** core entity/context присутствуют, но не выделены из
вежливой оболочки.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** это реалистичный и однозначный менеджерский вопрос, не typo/fuzzy
edge case.

**Action / priority:** `FIX_PRODUCTION`, P1.

### H03

**Query:** `Поищи, пожалуйста, икс 44235 — начало кода детали.`

**Original expected:** catalog partial code; `X44235100`.

**Actual:** spoken `икс` и пять цифр не преобразованы в technical token; catalog
subject содержит всю фразу, result 0, route=corporate, final not found.

**Observed mismatch:** phonetic letter form и punctuation не нормализованы в
`X44235`.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** запрос явно сообщает, что это начало кода. Это целевое улучшение
understanding, а не FTS defect; текущий bare-fragment guard сам по себе работает
по документированным правилам.

**Action / priority:** `FIX_PRODUCTION`, P2.

### H04

**Query:** `По нижней части укупорщика: вал там какой стоит?`

**Original expected:** catalog FOUND; `X44235100`; no corporate.

**Actual:** normalized question сохраняет смысл, но subject содержит только
`нижняя часть укупорщика`; entity `вал` не выделена. Catalog probe не выполнен,
route=corporate; chunks 46/49/47 отклонены; final not found.

**Observed mismatch:** инверсия порядка и двоеточие приводят к потере entity.

**Triage:** `CONFIRMED_PRODUCTION_DEFECT`; layer `UNDERSTANDING`.

**Reasoning:** assembly context и part name однозначны; отсутствие результата не
обусловлено данными.

**Action / priority:** `FIX_PRODUCTION`, P1.

## 9. F04 / G04 evidence analysis

### F04 causal chain

1. Route `corporate` правильный.
2. Retrieval evidence уже не подтверждает subject «телепортация сотрудников
   между офисами»; это общие документы с совпадениями по словам
   `процедура/сотрудники`.
3. Evidence guard должен был отклонить candidates, но `primary_fact` не имел
   subject criteria и автоматически пометил их supporting.
4. Primary error — `EVIDENCE_GUARD`, не ROUTING.
5. Final prose не утверждает телепортацию, но structured mode и displayed
   corporate sources загрязнены. Это реальный source-attribution defect.

### G04 causal chain

1. Route `corporate` неполон: запрос содержит явную catalog clause и должен быть
   decomposed как mixed.
2. Corporate retrieval нашёл точно названный пользователем документ; на этапе
   поиска это не нерелевантный result.
3. Domain consistency не проверялась, потому что mixed route не был создан.
4. Primary error — `MIXED_COMPOSITION`. Runner label об irrelevant evidence
   вторичен и частично вызван слишком жёстким expectation.
5. Final answer фактически относится к найденному документу, но загрязнён в
   смысле пользовательской задачи: он подаёт desktop material без оговорки о
   другом domain и полностью теряет состав наполнителя.

## 10. Recommended expectation changes

Acceptance expectations в коде не изменены. Рекомендуется обсудить три case:

| Case | Current expectation | Recommended expectation | Reason |
| --- | --- | --- | --- |
| D01 | Только clarification/not_found и corporate not_found | Оценивать безопасное отсутствие разрешённого referent; clarification предпочитать, но не требовать конкретного route. Одновременно отдельно требовать, чтобы фамилии не становились displayed supporting sources. | Без conversation context «этот вал» неразрешим; final answer не выбрал сущность. |
| D04 | Только clarification/not_found и corporate not_found | Разрешить controlled insufficiency как эквивалент clarification, независимо от внутреннего branch; по-прежнему запрещать factual/source claims о случайной детали. | В запросе нет идентификатора или предмета; production не обязан угадывать. |
| G04 | Corporate NOT_FOUND и запрет документа, который пользователь назвал дословно | Ожидать mixed: catalog FOUND плюс corporate FOUND для названного документа, но требовать явного указания, что документ не является инструкцией для узла. | Retrieval exact-title документа разумен; настоящий defect — потеря catalog clause и domain attribution. |

Count: **3** (`D01`, `D04`, `G04`). Это рекомендации, не выполненные изменения.

## 11. Data limitations

Среди 19 FAIL нет кейса, где единственной причиной является отсутствие данных:

- отсутствие процедур калибровки/смазки/телепортации само по себе ожидаемо;
  defect возникает только когда catalog часть теряется либо unrelated evidence
  объявляется FOUND;
- onboarding knowledge физически присутствует в corpus, что подтверждается B05
  и regression fixtures;
- catalog entities из A/C/G/H присутствуют в Catalog Store и находятся
  canonical запросами.

## 12. Ambiguous queries

D01 и D04 не дают достаточного referent. Их не следует включать в corrective
как доказательство обязательного catalog/corporate route. Полезное дальнейшее
улучшение — clarification UX и запрет отображения слабых sources, но это нужно
оценивать отдельными, явно сформулированными acceptance criteria.

## 13. Corrective priorities

Только подтверждённые production defects:

| Priority | Count | Cases |
| --- | ---: | --- |
| P0 | 2 | A04, E03 |
| P1 | 14 | A01, A02, A05, A06, C02, C04, E01, E04, F04, G01, G03, G04, H01, H04 |
| P2 | 1 | H03 |

- **P0 A04:** явный `узел + code` доходит до corporate и принимает unrelated
  sources.
- **P0 E03:** catalog факт корректен, но ответ дополняется ложной corporate
  «процедурой».

## 14. Analytical valid behavior baseline

Эта метрика не заменяет official acceptance. В valid behavior включены 17
официальных PASS и два ambiguous cases D01/D04, где final prose не угадывает
сущность и честно говорит о недостатке данных.

```text
Official acceptance:          17/36 (47,22%)
Analytical valid behavior:    19/36 (52,78%)
Confirmed production defects: 17/36 (47,22%)
```

G04 не добавлен в valid baseline: несмотря на рекомендацию изменить corporate
expectation, production всё равно потерял однозначную catalog clause.

## 15. Recommended next corrective package

Следующий пакет следует ограничить крупнейшим единым слоем:

**Corrective 05 — Natural Catalog Understanding and Clause Extraction**

Scope только подтверждённых UNDERSTANDING defects:

- entity/assembly extraction из natural wrappers: A01, A02, H01, H04;
- embedded prefix/substring code extraction: A05, A06, H03;
- очистка/decomposition catalog clause: C02, C04;
- compound intent/subject preservation для E04.

Не включать в этот пакет A04/E01/G03 routing, E03/F04 evidence guard или
G01/G04 mixed composition: это отдельные причины и требуют отдельных
acceptance gates.

## 16. Verification

- Control acceptance run:
  `manager_natural_acceptance_20260813T125812+0300` — **17/36**, те же 19 FAIL.
- Production tests не запускались: production и test-only utilities в triage
  не изменялись; пользователь разрешил не использовать full pytest в этом
  случае.
- `git diff --check` выполняется после создания отчёта.
- Commit/push не выполнялись.
