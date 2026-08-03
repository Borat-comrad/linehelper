# Итерация 09. Release Candidate Acceptance

Итоговый статус: **NOT_READY**.

Детерминированный контур, все восемь обязательных core-сценариев и
organization smoke прошли. Release Candidate не принят из-за подтверждённого
нарушения границы supporting/non-supporting evidence в negative-сценарии MR08
и двух новых product-регрессий evidence-слоя, T07 и PR01. В рамках архитектурной
заморозки production-код не исправлялся.

## 1. Проверяемый commit и environment

| Параметр | Значение |
| --- | --- |
| Ветка | `refactor/rag-architecture-v2` |
| HEAD | `9f070afaa8b1bdf38e980ff1ab8a5ea5b4629df5` |
| Git status до проверок | чистый |
| `git diff --check` до проверок | без ошибок |
| Python | 3.12.13, `.venv` |
| SQLite | 3.50.4; `data/memory/linehelper_memory.db` доступна read-only |
| Ollama | доступна на `http://localhost:11434` |
| Answer model | `qwen2.5:14b` |
| Analyzer model | `qwen2.5:3b` |
| Runtime timeout | 180 секунд |
| Fixture | `tests/fixtures/rag_architecture_v2_cases.json`, schema 2.0 |
| Число cases | 71 |

Предыдущий полный artifact:

```text
data/test_runs/rag_architecture_v2/iteration_05/
20260730_113922/baseline.json
```

Проверяемый Release Candidate artifact:

```text
data/test_runs/rag_architecture_v2/iteration_09_release_candidate/
20260731_124644/baseline.json
```

Preflight полного runner завершился с `ok=true`: база существует, Ollama
ответила, обе запрошенные модели доступны. Artifact зафиксировал чистый Git
status и тот же HEAD.

## 2. Архитектура v2 на момент заморозки

Проверен интегрированный pipeline:

```text
user question
→ QueryAnalyzer
→ RequestedFactTypeResolver
→ finalized QueryPlan
→ Safe Clarification
→ Conversation Resolver
→ RetrievalPlan
→ Multi-stage Retrieval
→ ContextPlan / ContextComposer
→ EvidencePlan / EvidenceAssessor
→ GroundedAnswerContract
→ один LLM draft
→ deterministic validation
→ deterministic rendering
```

На этапе 09 production-код, fixture, prompt, документы и архитектурные правила
не менялись. Было разрешено создать только этот отчёт.

## 3. Compileall и full pytest

Выполнено по одному разу:

```text
.\.venv\Scripts\python.exe -m compileall linehelper tests scripts
success

.\.venv\Scripts\python.exe -m pytest tests scripts\tests \
  --basetemp .\.venv\pytest-tmp-rag-v2-release-candidate \
  -p no:cacheprovider
495 passed in 19.60s
```

Изменение относительно этапа 8: `0` test items, было и осталось `495 passed`.
Failures отсутствуют, поэтому условие запуска live architecture pack выполнено.

## 4. Полный 71-case architecture pack

Ровно один полный pack запущен одной командой и одним процессом:

```text
.\.venv\Scripts\python.exe scripts\run_rag_architecture_v2_baseline.py \
  --output-dir data\test_runs\rag_architecture_v2\iteration_09_release_candidate \
  --verbose
```

Итог:

| Статус | Cases |
| --- | ---: |
| Passed | 48 |
| Failed | 23 |
| Blocked | 0 |
| Not applicable | 0 |
| Всего | 71 |

Pack шёл с `2026-07-31 12:46:44 +03:00` до формирования artifact в
`13:48:52`, то есть 62 минуты 8 секунд. Повторный pack, repeatability и
post-fix pack не выполнялись.

Глобальные инварианты:

| Метрика | Результат |
| --- | ---: |
| Operational misroutes | 0 |
| Missed operational | 0 |
| False clarification | 0 |
| Missing clarification | 0 |
| Valid clarification recall | 12/12 — 100% |
| Missing-slot clarification accuracy | 7/7 — 100% |
| Multi-turn resolution | 13/13 — 100% |
| Resolved-question accuracy | 13/13 — 100% |
| Topic-change accuracy | 13/13 — 100% |
| Slot-inheritance accuracy | 13/13 — 100% |
| Stale-context leaks | 0 |
| Required candidate recall @5 | 39/41 — 95,12% |
| Required candidate recall @10 | 39/41 — 95,12% |
| Required chunks in context | 23/29 — 79,31% |
| Candidate provenance | 1536/1536 — 100% |
| Best-score dedupe | 823/823 — 100% |
| Evidence coverage, среднее | 54,46% по 56 оценённым cases |
| Requested fact type accuracy | 58/71 — 81,69% |
| Generic no-answer при наличии required retrieval evidence | 7 |
| Unsupported person answers | 0 |

Распределение contract mode:

| Answer mode | Cases |
| --- | ---: |
| `full_answer` | 29 |
| `partial_answer` | 3 |
| `insufficient_evidence` | 24 |
| недоступен для ранней ветки | 15 |

Фактические response kinds: 29 `answer`, 6 `partial_answer`, 24 `no_answer`,
12 `clarification`. Разница с contract mode объясняется ранними ветками и
многоходовыми cases, для которых native answer contract не создаётся.

RequestedFactTypeResolver:

- 48 планов — `unchanged_explicit`;
- 20 — `resolved_from_structure`;
- 1 — `ambiguous` (`SC_OR02`);
- 2 — `insufficient_signals` (`CR08`, `MR05`), `unknown` сохранён безопасно.

Из `unknown` в существующий конкретный тип преобразованы T02, T05A, T09,
OR01, DF01, SC_OR01, SC_OR05 и MR08. Явный draft type структурно уточнён в
T04, T05B, PI04, PI06, PI08, CL01, CV03, SC_APP01–SC_APP03, CR02 и CR03.
У failures SC_OR05, CR02 и MR08 итоговый fact type соответствует structural
signal; их подтверждённые причины находятся в последующих слоях. Системной
регрессии самого resolver не обнаружено, но `SC_OR02` остаётся неоднозначным.

## 5. Core cases

Все обязательные core-сценарии прошли strict evaluation:

| Case | Fact type / mode | Evidence trace | Статус |
| --- | --- | --- | --- |
| T02 | `list`, `full_answer` | supporting 72, 71, 73, 74; четыре правила | passed |
| T03 | `procedure`, `full_answer` | supporting 29; non-supporting 44, 39 | passed |
| T04 | `document_recipient`, `partial_answer` | supporting 47; non-supporting 19; named recipient остаётся unsupported | passed |
| T05A | `procedure`, `full_answer` | supporting 42, 43, 27; правило 14 дней сохранено | passed |
| T06 | `responsible_person`, `full_answer` | supporting 594, 514; policy/secondary 592 не поддерживает требование | passed |
| T08 | `responsible_person`, `full_answer` | supporting 437, 439; policy 73 не выведен как evidence | passed |
| T09 | `unknown → procedure`, `full_answer` | supporting 68, 70, 67; `normative_action` | passed |
| OR01 | `unknown → procedure`, `full_answer` | supporting 70, 69, 67 | passed |

Contract validation доступна и валидна для всех 56 cases, прошедших через
answer contract. Fallback не применялся, validation violations отсутствуют.

## 6. Сравнение с этапом 5

| Метрика | Этап 5 | Release Candidate | Изменение |
| --- | ---: | ---: | ---: |
| Strict status | 47/24/0 | 48/23/0 | +1 passed |
| T01–T09 pass rate | 8/10 | 9/10 | +1 |
| Requested fact accuracy | 55/71 — 77,46% | 58/71 — 81,69% | +3 correct, +4,23 п.п. |
| Required candidate recall @5 | 39/41 — 95,12% | 39/41 — 95,12% | без изменения |
| Required candidate recall @10 | 39/41 — 95,12% | 39/41 — 95,12% | без изменения |
| Required chunks in context | 23/29 — 79,31% | 23/29 — 79,31% | без изменения |
| Evidence coverage | not_available | 54,46% | native после этапа 6 |
| Generic no-answer при required retrieval evidence | 0 | 7 | +7; trace ниже |
| False / missing clarification | 0 / 0 | 0 / 0 | без изменения |
| Operational misroutes / missed | 0 / 0 | 0 / 0 | без изменения |
| Multi-turn resolution | 13/13 | 13/13 | без изменения |
| Blocked | 0 | 0 | без изменения |

Status changes:

| Изменение | Cases | Подтверждённый trace |
| --- | --- | --- |
| failed → passed | T04 | partial evidence и deterministic missing-information block |
| failed → passed | T09 | `unknown → procedure` по `normative_action` |
| failed → passed | SC_OR01 | `unknown → procedure` по `normative_action` |
| failed → passed | SC_APP01 | `procedure → document_recipient` |
| failed → passed | CR03 | resolved question и `document_recipient` согласованы |
| passed → failed | T07 | employee 577 в context, но evidence отвергнут целиком |
| passed → failed | PR01 | точный procedural chunk 47 в context, но evidence отвергнут целиком |
| passed → failed | CL02 | только strict mismatch `primary_contact`/`responsible_person`; clarification корректна |
| passed → failed | CV04 | только strict mismatch `primary_contact`/`responsible_person`; clarification корректна |

Runtime timeout отсутствовал, поэтому ни один status change не объясняется
timeout. Улучшения и регрессии выше не приписываются одному слою без trace.

## 7. Remaining failures

Каждый из 23 remaining failures классифицирован по первичному слою.

| Case | Категория | Краткий trace | MVP blocker | Следующее действие |
| --- | --- | --- | --- | --- |
| T07 | `evidence_gap` | employee 577 выбран, но весь context объявлен non-supporting; `no_answer` | да | разрешить составное/частичное responsibility evidence без выдумывания типа оборудования |
| CL02 | `strict_validator_wording` | корректное missing-subject clarification; `primary_contact` вместо ожидаемого `responsible_person` | нет | определить продуктовую эквивалентность соседних responsibility types |
| PR01 | `evidence_gap` | точная процедура командировки, chunk 47, выбрана, но `subject_matched_procedure_not_found` | да | исправить subject-to-procedure assessment на metadata/title совпадении |
| CV01 | `retrieval_gap` | запись Шкиренкова отсутствует в candidates/context | нет | добавить общий alias/function retrieval для рабочего компьютера |
| CV02 | `retrieval_gap` | запись Шкиренкова отсутствует в candidates/context | нет | улучшить lookup функции «рабочие места» без person-specific правила |
| CV04 | `strict_validator_wording` | clarification корректна; `primary_contact` вместо `responsible_person` | нет | унифицировать strict semantic compatibility |
| RP01 | `context_coverage_gap` | ответ сформирован, но required responsibility route не вошёл в context | нет | проверить responsibility coverage requirement composer |
| SC_OR02 | `query_analysis_gap` | resolver сохранил `unknown/ambiguous`, intent `roles_responsibility`; ожидался нормативный procedure | нет | уточнить conflict policy нормативных вопросов |
| SC_OR03 | `context_coverage_gap` | ответ есть, sibling rule 67 не вошёл в context | нет | включать обязательное правило письменной фиксации в policy coverage |
| SC_OR04 | `query_analysis_gap` | `procedure`, но intent `task_management`; chunk 67 отсутствует, evidence недостаточно | нет | отдельная corrective-проверка intent и policy coverage разговорного варианта |
| SC_OR05 | `context_coverage_gap` | fact type исправлен в `procedure`, ответ есть, chunk 67 отсутствует | нет | то же policy sibling coverage, без правила по тексту вопроса |
| SC_KP02 | `strict_validator_wording` | valid abbreviation clarification; `primary_contact` вместо `responsible_person` | нет | семантическая совместимость типов до strict comparison |
| SC_KP03 | `strict_validator_wording` | valid abbreviation clarification; `definition` вместо ожидаемого `unknown` | нет | не считать ранний draft type дефектом при корректном clarification action |
| SC_KP05 | `strict_validator_wording` | valid abbreviation clarification; `definition` вместо ожидаемого `unknown` | нет | то же |
| SC_DOC01 | `query_analysis_gap` | явный recipient-вопрос остался `procedure`, evidence недостаточно | нет | расширить structural recipient signals для кадровых оригиналов |
| SC_DOC03 | `query_analysis_gap` | recipient-вопрос остался `procedure`, evidence недостаточно | нет | расширить structural recipient signals для бухгалтерских документов |
| SC_APP04 | `query_analysis_gap` | «куда отправить заявление» осталось `procedure` | нет | согласовать application-recipient pattern с object qualifier |
| SC_APP05 | `strict_validator_wording` | предметное clarification корректно; `primary_contact` вместо `responsible_person` | нет | унифицировать responsibility types для strict checker |
| CR02 | `query_analysis_gap` | resolved fact `document_recipient`, но intent `task_management`; `no_answer` | нет | пересогласовать final fact type и intent после slot resolution |
| CR09 | `strict_validator_wording` | корректный answer, `definition` вместо ожидаемого `list` | нет | формализовать answer-shape compatibility definition/list |
| CR10 | `query_analysis_gap` | follow-up сохранил `procedure` вместо `document_recipient` | нет | повторно применять recipient structural signal к resolved question |
| CR11 | `evidence_gap` | `primary_contact`, отпускные procedure chunks полностью отвергнуты | нет | формализовать partial recipient answer или предметное missing slot |
| MR08 | `evidence_gap` | chunks 48/45/44 про приказы/договоры ошибочно считаются evidence процедуры отсутствующего документа; `full_answer` | **да** | отрицательный subject/evidence guard; режим должен быть partial/insufficient без этих sources |

MR08 — не только wording failure. Финальный текст осторожно говорит о
недостатке данных, но EvidenceDecision объявляет все три нерелевантных
процедурных chunks поддержкой `primary_procedure`, контракт выводит их как
разрешённые sources и устанавливает `full_answer`. Это прямое нарушение
проверяемой границы supporting/non-supporting evidence и самостоятельное
условие `NOT_READY`.

Метрика `generic_no_answer_when_evidence_exists=7` относится к T07, PR01,
CV05, SC_OR04, MR01, MR03 и MR07. В каждом raw retrieval содержит required
record, но EvidenceAssessor не разрешил ни одного supporting chunk. CV05,
MR01, MR03 и MR07 формально проходят свои retrieval-oriented expectations,
однако product-ответ остаётся `no_answer`; это не retrieval-регрессия и не
должно скрываться strict pass status.

## 8. Organization smoke

После полного architecture pack один раз выполнен тот же короткий
retrieval-only smoke на первых десяти scenarios:

```text
.\.venv\Scripts\python.exe scripts\run_organization_test_pack.py \
  --limit 10 \
  --retrieval-only \
  --output-dir data\test_runs\organization\iteration_09_release_candidate \
  --verbose
```

Artifact:

```text
data/test_runs/organization/iteration_09_release_candidate/
20260731_135022/summary.json
```

Результат: **8 PASS / 1 PARTIAL / 1 FAIL / 0 MANUAL_REVIEW**, полностью
совпадает с этапом 5. Runtime errors и zero-results отсутствуют, top-5 recall
равен 90%. Structured responsibility/organization core-сценарии T06 и T08
дополнительно прошли в полном architecture pack.

## 9. Latency и runtime

| Метрика | Результат |
| --- | ---: |
| Полное wall time pack | 62 мин 08 с |
| Retrieval p50 / p95 | 78 / 125 мс |
| End-to-end case median | 18,125 с |
| End-to-end case p95 | 135,360 с |
| End-to-end max | 182,922 с, T05A |
| Context chars median / max | 1891 / 5869 |
| Context chunks median / max | 3 / 4 |
| Supporting chunks | 76 по 56 evidence-evaluated cases; median 1 |
| Non-supporting chunks | 69 по 56 evidence-evaluated cases; median 1 |
| Ollama timeout | 0 |

Пять самых медленных cases: T05A — 182,922 с, MR06 — 141,734 с, DF01 —
136,204 с, MR08 — 135,360 с, T02 — 133,203 с. Runner отдельно сохраняет
retrieval latency и полное время case, но не разделяет orchestration,
Query Analyzer и LLM generation latency. Поэтому отдельные p50/p95 генерации
и orchestration имеют честный статус `not_available`.

## 10. Известные ограничения

1. Evidence assessment даёт как false negative (семь generic no-answer при
   найденном required record), так и false positive MR08.
2. Required candidate recall остаётся 39/41, context inclusion — 23/29;
   два retrieval и шесть context gaps не маскируются downstream-слоями.
3. Requested fact accuracy 58/71: большинство remaining mismatches относится
   к `primary_contact`/`responsible_person`, document recipient и answer shape.
4. Policy sibling 67 нестабилен в разговорных вариантах распоряжения, хотя
   T09 и OR01 проходят.
5. Native claim-to-evidence attribution для произвольных unsupported claims
   по-прежнему отсутствует; artifact честно содержит observability gap.
6. По указанию этапа repeatability и второй full pack не выполнялись.

## 11. Release recommendation

**NOT_READY**.

Положительные release-инварианты выполнены: compileall и 495 tests зелёные,
все восемь core-cases прошли, operational misroutes и false clarifications
равны нулю, multi-turn 13/13, blocked cases нет, organization smoke не
ухудшился.

Однако принятие запрещает нарушение supporting/non-supporting source boundary.
MR08 подтверждает такое нарушение. Дополнительно T07 и PR01 были passed на
этапе 5 и теперь возвращают `insufficient_evidence`, несмотря на релевантный
selected context. Эти defects требуют отдельной короткой corrective-итерации;
исправлять их во время замороженного acceptance-run нельзя.

## 12. Условия для merge

Перед повторной release acceptance необходимо:

1. Исправить только EvidenceAssessor subject matching, чтобы PR01 принимал
   точный procedural chunk 47, а T07 мог дать подтверждённую или частичную
   ответственность без выдумывания типа оборудования.
2. Перевести MR08 в `partial_answer` или `insufficient_evidence`; chunks
   приказов/договоров не должны поддерживать процедуру отсутствующего
   документа и не должны отображаться как supporting sources.
3. Добавить deterministic regressions на эти три invariants, не меняя fixture
   ради метрики.
4. Повторно выполнить compileall, full pytest, ровно один полный 71-case pack
   и organization smoke. Core, operational, clarification и conversation
   показатели не должны ухудшиться.

71/71 не является искусственным условием merge: strict wording и
документированные product limitations могут остаться, но evidence boundary и
новые product-регрессии должны быть устранены.

## 13. Следующие продуктовые задачи после MVP

После устранения release blockers, отдельными ограниченными задачами:

- согласовать продуктовую семантику `primary_contact` и
  `responsible_person` в validator и regression expectations;
- улучшить document-recipient распознавание для кадровых, бухгалтерских и
  application follow-up формулировок;
- добавить общий alias/function retrieval для «рабочего компьютера» и
  «рабочих мест»;
- стабилизировать coverage обязательного policy sibling о письменной
  фиксации распоряжений;
- добавить native claim-to-evidence attribution для диагностики unsupported
  claims.

Production-код на этапе 09 не изменялся. Создан только этот Markdown-отчёт;
runtime artifacts находятся в уже предусмотренных `data/test_runs` каталогах.
Commit и push не выполнялись.
