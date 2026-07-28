# Baseline архитектуры RAG v2

Дата: 2026-07-28  
Этап: 0 — baseline и architectural regression pack  
Статус: baseline снят, production-код не менялся

## 1. Git и runtime

| Поле | Значение |
|---|---|
| Ветка | `refactor/rag-architecture-v2` |
| Исходный commit | `cc0510bbf0aa1d5b94e31b9eee952fa130a94f08` |
| Исходный production tree | `HEAD:linehelper = 13de37fff5040e2b7ffc0f6ebcb2ec93dd40b265` |
| Production diff после этапа 0 | пустой: `git diff --name-only -- linehelper` ничего не вернул |
| Основной live run | `data/test_runs/rag_architecture_v2/20260728_103341/baseline.json` |
| Retrieval-only run | `data/test_runs/rag_architecture_v2/20260728_103120/baseline.json` |
| Repeatability run | `data/test_runs/rag_architecture_v2/20260728_110252/baseline.json` |

До этапа 0 в рабочем дереве уже находились три untracked test/diagnostic-only
файла:

- `docs/diagnostics/TEST_FAILURES_ROOT_CAUSE_AUDIT.md`;
- `scripts/run_autonomous_runtime_probe.py`;
- `tests/test_autonomous_runtime_probe.py`.

Они относятся к предыдущей диагностической работе, production-код не меняют и
на этом этапе не редактировались. Новые baseline-файлы отделимы от них по путям.

## 2. Использованная модель и окружение

| Поле | Значение |
|---|---|
| Python | `3.12.13` |
| ОС | Windows 11, build 22621 |
| Answer model | `qwen2.5:14b` |
| Query Analyzer model | `qwen2.5:3b` |
| Ollama URL | `http://localhost:11434` |
| Ollama preflight | OK; обе модели доступны |
| Memory DB | `data/memory/linehelper_memory.db` |
| Live mode | `full_pipeline` |
| Retrieval limit | 5 на один production retrieval query |
| Candidate limit | 30 |

Runner использовал production entrypoint `RagAnswerGenerator.answer()`.
Test-only wrapper только записывал вызовы и результаты retriever; Query Analyzer,
validation, retrieval, rerank, context selection, evidence gate и answer logic не
дублировались.

## 3. Состав test pack

Машиночитаемый fixture:
`tests/fixtures/rag_architecture_v2_cases.json`.

| Срез | Количество |
|---|---:|
| Всего сценариев | 32 |
| Core T01–T09, включая T05A/T05B | 10 |
| Парные responsibility/status | 8 |
| Дополнительные clarification boundary | 2 |
| Procedure/recipient/status | 2 |
| Разговорные формулировки | 5 |
| Перефразирование/отрицательные/operational neighbor | 5 |
| Multi-turn | 1 |

Fixture хранит целевые архитектурные ожидания, а не подогнанное текущее
поведение. Для evidence используются stable `record_key` или `chunk_id`; source
используется как fallback identity, если стабильного идентификатора нет.

## 4. Методика оценки

Проверялись:

- QueryPlan intent и final mode;
- clarification и operational routing;
- retrieval queries и raw candidates;
- обязательные chunk IDs/record keys в retrieval;
- обязательные chunk IDs/record keys в context;
- sources и ограниченные answer concepts;
- запрещённые сотрудники;
- multi-turn decisions;
- repeatability архитектурных решений.

Exact final text не является критерием. Answer concepts проверяются как группы
допустимых смысловых признаков. Поля без production observability записываются
как `{"available": false, "reason": "..."}`.

Статусы target pack:

- `passed` — все наблюдаемые и обязательные целевые инварианты выполнены;
- `failed` — нарушен хотя бы один целевой инвариант или обязательный invariant
  пока не наблюдаем;
- `blocked` — live dependency недоступна;
- `not_applicable` — invariant явно неприменим.

Строгий target status всех сценариев сейчас `failed`, потому что production
QueryPlan не имеет native `requested_fact_type`. Это намеренно: baseline не
скрывает архитектурный пробел. Детальные checks показывают, какие существующие
слои при этом работают.

Метрика recall считает один stable chunk identity один раз. Source не увеличивает
denominator, если уже задан `chunk_id` или `record_key`.

## 5. Результат полного pytest

| Состояние | Результат |
|---|---|
| До baseline-файлов | `241 passed in 14.90s` |
| После baseline-файлов | `273 passed in 14.53s` |
| Добавлено тестов harness/runner | 32 |
| Регрессии существующих тестов | 0 |
| Реальная Ollama внутри pytest | не запускалась |
| Compileall | успешно |

Команда:

```powershell
.\.venv\Scripts\python.exe -m pytest tests scripts\tests `
  --basetemp .\.venv\pytest-tmp-rag-v2-baseline-20260728-final `
  -p no:cacheprovider
```

Organization retrieval regression:

- короткий общий срез: 8 PASS / 1 PARTIAL / 1 FAIL;
- рабочие места: PASS;
- доставка клиенту: PARTIAL;
- внутренний документооборот: FAIL.

Production behavior не менялся; результаты являются контрольным срезом, а не
post-fix улучшением.

## 6. Результаты T01–T09

В таблице T05A и T05B являются двумя контролируемыми заменами отсутствующего
исходного вопроса T05.

| ID | Expected invariant | Actual | Status | Evidence |
|---|---|---|---|---|
| T01 | 1-й ход уточняет `КП`; 2-й восстанавливает `Как оформить коммерческое предложение?`; повторного уточнения нет | 1-й ход: `ambiguous_abbreviation/clarification`; 2-й: `kp_commercial_offer/partial_answer`; history в production не передаётся, `resolved_question` и `ambiguity_span` недоступны | failed | turn checks: 7 passed / 3 failed; context пуст; repeat 3/3 стабилен |
| T02 | Все четыре правила chunks 71–74 найдены, входят в context и покрываются ответом | Raw retrieval содержит 71, 72, 73, 74; context содержит только 72 | failed | 12 checks passed / 4 failed; потери 71, 73, 74 на context selection |
| T03 | Найдена процедура chunk 29; не общий no_answer | Chunk 29 отсутствует в production retrieval; context пуст; `equipment_it_request/no_answer` | failed | required source и chunk 29 отсутствуют уже до context |
| T04 | Procedure chunk 47; частичный ответ без выдуманного named recipient | Chunk 47 найден и вошёл в context; `business_trip/answer`; ответ не сделал knowledge gap адресата достаточно явным | failed | 10 checks passed / 2 failed; source корректен |
| T05A | Vacation procedure и правило не позднее 14 календарных дней | Raw retrieval содержит 27, 42, 43; context содержит 42 и 43, но не 27; 14 дней не покрыты ответом | failed | потеря chunk 27 на context selection |
| T05B | Текущий статус — operational; статические правила допустимы дополнительно | `vacation/answer`, operational routing=false; context 42, 43 | failed | operational boundary не сработал |
| T06 | Responsibility, semantic organization retrieval, route 594/record key, не 1С | Raw содержит правильные chunks 477 и 594; intent стал `one_c_operational_lookup`, context обнулён, `no_answer` | failed | 3 checks passed / 10 failed; misroute после retrieval |
| T07 | Шкиренков по рабочим местам; оборудование не обобщать; partial допустим | Context содержит correct employee 577 и два нерелевантных routes 599/592; `roles_responsibility/answer` | failed | 9 checks passed / 1 failed; native fact type отсутствует; noise остаётся |
| T08 | Structured records 437/439; Шкиренков и Малахова; не кадровый документооборот | Intent `roles_responsibility`, но retrieval не содержит 437/439; context содержит только chunk 72 кадровой переписки | failed | 7 checks passed / 8 failed; relation retrieval и context неверны |
| T09 | Нет clarification; `order_disposition`; chunk 67; письменная форма | Intent корректный, но `clarification=true`; retrieval не запускался; context пуст | failed | 3 checks passed / 10 failed; ложное clarification блокирует evidence |

Core pass rate: 0/10 target cases. Это ожидаемый строгий baseline до QueryPlan v2,
а не pytest failure.

## 7. Парные intent-сценарии

| Пара | Responsibility/static | Operational | Различение |
|---|---|---|---|
| Заказ | PI01 ошибочно `one_c_operational_lookup/no_answer` | PI02 корректно operational/no_answer | failed |
| Отгрузка | PI03 ошибочно operational/no_answer | PI04 корректно operational/no_answer | failed |
| Склад | PI05 ошибочно operational/no_answer | PI06 корректно operational/no_answer | failed |
| Оплаты | PI07 `roles_responsibility/answer`, но без требуемого уточнения и с org-context | PI08 корректно operational/no_answer | failed |

Paired target pass rate: 0/8. Шесть из первых восьми парных прогонов
схлопнулись в один `one_c_operational_lookup/no_answer`, независимо от вопроса об
ответственном или текущем состоянии.

Соседние наблюдения:

- CV05 «Кто выпускает заказ клиенту?» ошибочно operational;
- CV01 «К кому идти за новым компьютером?» — semantic `no_answer`;
- OP01 «Есть ли новый компьютер в наличии?» ошибочно классифицирован как
  `equipment_it_request`, а не current inventory.

## 8. Многошаговые сценарии

T01 запускает оба user-turn последовательно, но production API получает только
текущую строку. В первый ход retrieval не вызывается из-за корректного
clarification. Во второй ход передаётся только `Коммерческое предложение`;
предикат `Как оформить` не восстанавливается.

Фактический `resolved_question` нельзя извлечь из runtime, поэтому
`multi_turn_resolution_rate = not_available`, а не 0.

Repeatability T01:

- turn 1 intent: `ambiguous_abbreviation` — 3/3;
- turn 1 clarification: true — 3/3;
- turn 2 intent: `kp_commercial_offer` — 3/3;
- turn 2 final mode: `partial_answer` — 3/3;
- context: пуст — 3/3.

## 9. Clarification

| Метрика | Значение |
|---|---:|
| False clarification | 2 |
| Missing clarification | 8 |

False clarification:

- T09 — правильный intent `order_disposition`, но clarification остановил
  retrieval;
- RP01 «К кому обращаться по доставке клиенту?» — ошибочный
  `kp_commercial_offer/clarification`.

Missing clarification включает вопросы без достаточного референта или scope:
PI01, PI03, PI07, CL01, CL02, CV03, CV04 и CV05.

Примеры:

- «Кому отдать документы?» → `task_management/answer`;
- «Кто этим занимается?» → `roles_responsibility/answer`;
- «Кому отдать бумаги?» → `task_management/answer`.

## 10. Operational boundary

`operational_misroute_count = 5`: T06, PI01, PI03, PI05 и CV05 были направлены
в operational lookup, хотя спрашивали об ответственности.

Дополнительно три фактических operational-вопроса не получили operational
routing:

- T05B — статус уже поданного отпуска;
- PR02 — этап текущей командировки;
- OP01 — наличие компьютера сейчас.

Вопросы с явным номером или явным текущим состоянием PI02, PI04, PI06 и PI08
стабильно распознаны как operational.

## 11. Retrieval recall

| Режим | Recall@5 | Recall@10 |
|---|---:|---:|
| Retrieval-only | 20/25 = 80% | 20/25 = 80% |
| Full production pipeline | 18/25 = 72% | 18/25 = 72% |

Full pipeline меняет retrieval queries через QueryPlan, поэтому теряет два
stable evidence identities относительно прямого retrieval-only среза.

Ключевые наблюдения:

- T02: chunks 71–74 найдены;
- T03: chunk 29 не найден;
- T06: route 594 найден, несмотря на будущий operational misroute;
- T07: employee 577 найден;
- T08: structured chunks 437/439 не найдены;
- T09: retrieval не запущен из-за clarification.

В full pipeline каждый production retrieval call имеет limit=5, поэтому @10 не
добавляет глубины и совпадает с @5. Это ограничение текущего baseline runner и
production invocation, а не доказательство отсутствия кандидатов на позициях
6–10.

## 12. Context inclusion

`required_chunk_in_context_rate = 7/24 = 29,17%`.

Подтверждённые точки потери:

- T02: raw 71–74 → context только 72;
- T05A: raw содержит 27 → context только 42/43;
- T06: raw содержит route 594 → context пуст из-за operational policy;
- T08: required structured chunks отсутствуют уже в retrieval, context содержит
  кадровый chunk 72;
- T09: context не строится из-за clarification.

T07 сохраняет required employee 577, но добавляет нерелевантные routes 599 и
592, поэтому наличие одного правильного chunk ещё не означает достаточный
evidence set.

## 13. Evidence coverage

| Метрика | Значение |
|---|---|
| Evidence coverage rate | `not_available` |
| Generic no_answer при наличии required evidence | 3 |
| Unsupported person answer count | 0 |

Три generic no_answer с required evidence в raw retrieval: T06, PI05 и CV05.

`unsupported_person_answer_count = 0` означает только, что запрещённые имена из
fixture не появились в финальных ответах наблюдаемых сценариев. Production не
возвращает claim-to-evidence attribution, поэтому это не является полной
проверкой всех неподтверждённых утверждений.

Evidence decision, coverage subclaims и причины исключения chunks production
runtime не отдаёт. Их нельзя честно восстановить из final answer.

## 14. Повторяемость

Для T01, T06, T08 и T09 выполнено по три повтора. Дословный final text не
сравнивался.

| Case | Intent | Clarification | Operational routing | Context chunks | Final mode | Итог |
|---|---|---|---|---|---|---|
| T01 | stable | stable | stable | stable empty | stable `partial_answer` | stable |
| T06 | stable `one_c_operational_lookup` | stable false | stable true | stable empty | stable `no_answer` | стабильно ошибочно |
| T08 | stable `roles_responsibility` | stable false | stable false | stable `[72]` | stable `answer` | стабильно ошибочный evidence |
| T09 | stable `order_disposition` | stable true | stable false | stable empty | stable `clarification` | стабильно ошибочно |

В этом запуске все доступные dimensions стабильны 3/3. Это не отменяет
недетерминированность, найденную предыдущим аудитом: три повтора — минимальная,
а не статистически достаточная выборка.

## 15. Недоступная observability

| Поле | Причина отсутствия | Где должно появиться | Предлагаемая итерация |
|---|---|---|---|
| `resolved_question` | `RagAnswerGenerator.answer()` принимает одну строку без history | conversation resolver до Query Analyzer | этап 1 |
| `requested_fact_type` | Current QueryPlan содержит intent/answer_type, но не тип требуемого факта | QueryPlan v2 | этап 1 |
| `ambiguity_span` | QueryPlan diagnostics не содержит доказанный фрагмент неоднозначности | Query Analyzer validation | этап 1 |
| Native operational decision | Сейчас выводится только derived flag по intent | validated QueryPlan/runtime boundary | этап 1 |
| `merged_candidates` | RagAnswer не отдаёт post-merge sequence | после `_retrieve_with_query_plan` | будущая retrieval iteration |
| Причина исключения chunk | Context selection возвращает только результат | context selector diagnostics | будущая context iteration |
| `evidence_decision` | Evidence gate отдаёт только итоговый режим | evidence gate result object | будущая evidence iteration |
| `unsupported_claims` | Нет claim-to-evidence attribution | answer planner/evidence validator | будущая answer iteration |

Production-код на этапе 0 не менялся ради появления этих полей.

## 16. Подтверждённые фундаментальные причины

1. **Нет conversation-aware resolved question.** T01 теряет предикат первого
   хода; история не входит в API `answer()`.
2. **Предмет и тип запрашиваемого факта смешаны.** T06 и responsibility/status
   pairs показывают лексический приоритет «заказ/отгрузка/склад» над вопросом
   «кто отвечает».
3. **Clarification не привязан к доказанной неоднозначности.** T09 и RP01
   получают ложное clarification; короткие вопросы без scope, наоборот, часто
   не уточняются.
4. **Retrieval и context не обеспечивают coverage sibling chunks.** T02
   находит четыре правила, но оставляет одно; T05A теряет deadline chunk.
5. **Relation-aware organization retrieval недостаточен.** T08 не находит
   structured function records; T07 смешивает правильного сотрудника с
   нерелевантными routes.
6. **Нет явного partial-answer/evidence contract.** T04 не фиксирует достаточно
   явно отсутствие named recipient; T05B и PR02 не разделяют static procedure и
   current status.
7. **Metadata/source contract неоднороден.** T03 procedure chunk 29 не
   поднимается по equipment request, а документ остаётся общим `reference`.

Причины подтверждают предыдущий
`docs/diagnostics/TEST_FAILURES_ROOT_CAUSE_AUDIT.md`; специальные исключения под
32 формулировки не требуются.

## 17. Известные ограничения baseline

1. Все strict target cases failed из-за обязательного, пока отсутствующего
   `requested_fact_type`; для анализа нужно читать detail checks, а не только
   верхнеуровневый pass rate.
2. T05A/T05B — контролируемые сценарии, а не восстановленный исходный вопрос
   старого скриншота.
3. Numeric chunk IDs зависят от текущего snapshot DB; `record_key` используется
   там, где он существует.
4. `selected_context` восстанавливает metadata best-effort join по
   RagSource/candidate, потому что RagSource не содержит chunk ID.
5. Clarification и operational boundary частично derived из `response_kind` и
   intent, а не native decision objects.
6. Full-pipeline recall@10 равен @5 из-за production retrieval limit=5 на
   каждый query.
7. Evidence coverage и multi-turn resolution недоступны.
8. Три повтора проверяют минимальную воспроизводимость, но не оценивают полное
   распределение LLM-вариативности.
9. Runtime outputs могут содержать изменяемые контактные данные и находятся
   только в игнорируемом `data/test_runs/`; в Git они не добавляются.
10. Один прерванный технический запуск `20260728_103141` не содержит
    `baseline.json` и не использован в метриках или выводах.
11. Organization runner при двух запусках в одну секунду объединил два
    targeted results в один timestamp directory; итоговые статусы проверены по
    scenario IDs.

Риски следующей итерации:

- исправить ranking, но оставить неверный requested fact type;
- увеличить context limit и добавить больше нерелевантных routes;
- создать словарь точных вопросов или фамилий вместо typed contract;
- загрязнить новый вопрос старой history без detector продолжения;
- начать считать derived diagnostics native observability;
- скрыть improvement или regression бессрочным `xfail`.

## 18. Условия перехода к этапу 1

Переход к QueryPlan v2 допустим после ручной проверки:

- fixture T01–T09 и соседних scenarios согласован;
- denominator recall/context принят;
- full pytest повторно воспроизводится зелёным;
- full live baseline и repeatability artifacts доступны локально;
- all current target failures видимы и не перенесены в обычный pytest;
- production diff остаётся пустым;
- этап 1 ограничен QueryPlan v2 и разделением предмета вопроса от типа
  запрашиваемого факта;
- изменения этапа 0 коммитятся только после ручного подтверждения.

Сравнение до/после этапа 0:

| Показатель | До | После |
|---|---:|---:|
| Production tree | `13de37f…` | `13de37f…` |
| Full pytest | 241 passed | 273 passed |
| Harness tests | 0 | 32 passed |
| Machine-readable architecture cases | 0 | 32 |
| Full live target evaluations | отсутствовали | 32 |
| Repeat evaluations | отсутствовали | 12 |
| Production-файлы изменены | нет | нет |

Предлагаемый commit message:

```text
test(rag): зафиксировать baseline архитектурных регрессий
```
