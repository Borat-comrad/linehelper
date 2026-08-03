# Итерация 11. Release Candidate Re-Acceptance

Итоговый статус: **READY_WITH_KNOWN_LIMITATIONS**.

Повторная единая приёмка подтвердила, что три release-блокера этапа 9
устранены: T07, PR01 и MR08 прошли strict evaluation и соблюдают границу
supporting/non-supporting evidence. Все восемь core-сценариев прошли,
operational misroutes и false clarifications отсутствуют, multi-turn resolution
сохранился на уровне 13/13, organization smoke не ухудшился. Оставшиеся 20
strict failures относятся к документированным non-blocking gaps и не требуют
71/71 для merge.

## 1. Проверяемый commit и environment

| Параметр | Значение |
| --- | --- |
| Ветка | `refactor/rag-architecture-v2` |
| HEAD | `8e86f8b7cb6f3668cf0fecf3b9278c3935093abb` |
| Production/test diff до проверок | отсутствует; `git diff --check` чист |
| Несвязанное состояние до проверок | ранее существовавший untracked `ITERATION_09_RELEASE_CANDIDATE_ACCEPTANCE.md` |
| Python | 3.12.13, `.venv` |
| SQLite | 3.50.4; `data/memory/linehelper_memory.db` доступна read-only |
| Ollama | доступна на `http://localhost:11434` |
| Answer model | `qwen2.5:14b` |
| Analyzer model | `qwen2.5:3b` |
| Runtime timeout | 180 секунд |
| Fixture | `tests/fixtures/rag_architecture_v2_cases.json`, schema 2.0 |
| Число cases | 71 |

Предыдущий Release Candidate artifact:

```text
data/test_runs/rag_architecture_v2/iteration_09_release_candidate/
20260731_124644/baseline.json
```

Targeted corrective artifact этапа 10:

```text
data/test_runs/rag_architecture_v2/iteration_10_evidence_boundary_targets/
20260731_145752/baseline.json
```

Проверяемый Release Candidate artifact:

```text
data/test_runs/rag_architecture_v2/iteration_11_release_candidate/
20260731_160820/baseline.json
```

Preflight runner завершился с `ok=true`: база существует, Ollama ответила,
обе запрошенные модели доступны. Artifact зафиксировал тот же HEAD. Старый
untracked Markdown-отчёт этапа 9 не меняет исполняемый код, поэтому условие
остановки по незакоммиченным production-изменениям не сработало.

Архитектура на момент заморозки:

```text
user question
→ QueryAnalyzer
→ RequestedFactTypeResolver
→ finalized QueryPlan
→ Safe Clarification
→ Conversation Resolver
→ RetrievalPlan / Multi-stage Retrieval
→ ContextPlan / ContextComposer
→ EvidencePlan / EvidenceAssessor
→ GroundedAnswerContract
→ один LLM draft
→ deterministic validation / rendering
```

На этапе 11 production-код, tests, fixture, prompt, документы и
архитектурные правила не изменялись. Создан только этот отчёт.

## 2. Deterministic verification

Выполнено по одному разу:

```text
.\.venv\Scripts\python.exe -m compileall linehelper tests scripts
success

.\.venv\Scripts\python.exe -m pytest tests scripts\tests \
  --basetemp .\.venv\pytest-tmp-rag-v2-release-candidate-2 \
  -p no:cacheprovider
501 passed in 19.80s
```

Failures отсутствуют. Количество test items не изменилось относительно
corrective-этапа 10: 501 → 501.

## 3. Full architecture result

Ровно один полный pack запущен одной командой и одним процессом:

```text
.\.venv\Scripts\python.exe \
  scripts\run_rag_architecture_v2_baseline.py \
  --output-dir data\test_runs\rag_architecture_v2\iteration_11_release_candidate \
  --verbose
```

| Статус | Cases |
| --- | ---: |
| Passed | 51 |
| Failed | 20 |
| Blocked | 0 |
| Not applicable | 0 |
| Всего | 71 |

Pack выполнялся с `2026-07-31 16:08:20 +03:00` до `17:13:23`, то есть
65 минут 03 секунды. Повторный pack, repeatability, post-fix pack и отдельные
live-runs после результата не выполнялись.

Глобальные инварианты:

| Метрика | Результат |
| --- | ---: |
| Operational misroutes / missed operational | 0 / 0 |
| False / missing clarification | 0 / 0 |
| Valid clarification recall | 12/12 — 100% |
| Missing-slot clarification accuracy | 7/7 — 100% |
| Multi-turn resolution | 13/13 — 100% |
| Resolved-question / topic-change / slot-inheritance accuracy | 13/13 по каждой метрике |
| Stale-context leaks / repeated clarification | 0 / 0 |
| Required candidate recall @5 / @10 | 39/41 — 95,12% / 39/41 — 95,12% |
| Required chunks in context | 23/29 — 79,31% |
| Candidate provenance | 1536/1536 — 100% |
| Best-score dedupe | 823/823 — 100% |
| Evidence coverage, среднее | 55,36% по 56 cases |
| Requested fact type accuracy | 58/71 — 81,69% |
| Generic no-answer при required retrieval evidence | 5 |
| Unsupported person answers | 0 |
| Contract violations / fallback | 0 / 0 |

Распределение native answer mode: 29 `full_answer`, 4 `partial_answer`,
23 `insufficient_evidence`, 15 early-branch cases без контракта. Фактические
response kinds: 29 `answer`, 7 `partial_answer`, 23 `no_answer`,
12 `clarification`.

RequestedFactTypeResolver сохранил ту же картину, что и на этапе 9:
48 `unchanged_explicit`, 20 `resolved_from_structure`, один `ambiguous`
(`SC_OR02`) и два `insufficient_signals` (`CR08`, `MR05`). Системной регрессии
resolver не обнаружено.

## 4. T07, PR01 и MR08

| Case | Итоговый trace | Результат |
| --- | --- | --- |
| T07 | selected/supporting/rendered 487, 577, 485; supported `responsible_identity`; unsupported `complete_responsibility_scope`; `partial_answer` | passed |
| PR01 | selected 47, 24, 19; supporting/rendered только 47; non-supporting 24, 19; `full_answer` | passed |
| MR08 | selected 48, 45, 44; supporting/rendered отсутствуют; все три non-supporting; `insufficient_evidence` | passed |

T07 не приписывает сотруднику неподтверждённый тип оборудования. Все три
отображаемых source entries структурированно связаны с requirement
`responsible_identity`: подразделение/отдел рабочих мест и employee record.
Diagnostics каждого chunk содержат structured metadata match и subject match,
но не подтверждают `complete_responsibility_scope`. Таким образом, расширенное
matching не превратило частичное покрытие в полный scope.

PR01 принимает устойчивое metadata/title совпадение процедуры командировки,
не использует chunks 24 и 19 и не возвращает generic no-answer. MR08 применяет
negative subject guard: процедуры других документов не становятся evidence и
не попадают в allowed/rendered sources.

Нарушений обязательных invariants этих трёх cases не обнаружено.

## 5. Core cases

Все восемь core-сценариев прошли strict evaluation:

| Case | Fact type / mode | Evidence/source boundary | Статус |
| --- | --- | --- | --- |
| T02 | `list`, `full_answer` | supporting/rendered 72, 71, 73, 74; 4/4 rules | passed |
| T03 | `procedure`, `full_answer` | supporting/rendered 29; noise 44, 39 non-supporting | passed |
| T04 | `document_recipient`, `partial_answer` | supporting/rendered 47; recipient unsupported; 19 non-supporting | passed |
| T05A | `procedure`, `full_answer` | supporting/rendered 42, 43, 27; deadline 14 дней | passed |
| T06 | `responsible_person`, `full_answer` | supporting/rendered 594, 514; 592 non-supporting | passed |
| T08 | `responsible_person`, `full_answer` | supporting/rendered 437, 439; policy 73 non-supporting | passed |
| T09 | `unknown → procedure`, `full_answer` | supporting/rendered 68, 70, 67 | passed |
| OR01 | `unknown → procedure`, `full_answer` | supporting/rendered 70, 69, 67 | passed |

## 6. Evidence false-positive audit

Сравнение всех 71 records с artifact этапа 9 показало ровно три изменения
answer mode, supporting chunks и rendered sources:

1. T07: `insufficient_evidence → partial_answer`, sources `[] → 487/577/485`;
2. PR01: `insufficient_evidence → full_answer`, sources `[] → 47`;
3. MR08: `full_answer → insufficient_evidence`, sources `48/45/44 → []`.

В остальных 68 cases supporting и rendered source sets не изменились. Нет
cases, где ранее insufficient evidence стало full answer, кроме целевого PR01;
там единственный source 47 точно соответствует процедуре командировки. Нет
non-supporting chunk, ставшего rendered source вне T07/PR01. T07 остаётся
partial, а полный responsibility scope не подтверждён. Unsupported person
answers, contract violations и renderer fallback равны нулю.

Итог false-positive audit: **новых evidence/source false positives не
обнаружено**.

## 7. Сравнение с этапом 9

| Метрика | Этап 9 | Этап 11 | Изменение |
| --- | ---: | ---: | ---: |
| Strict passed / failed / blocked | 48 / 23 / 0 | 51 / 20 / 0 | +3 passed |
| Status changes | — | T07, PR01, MR08: failed → passed | только corrective targets |
| Operational misroutes / missed | 0 / 0 | 0 / 0 | без изменения |
| False / missing clarification | 0 / 0 | 0 / 0 | без изменения |
| Multi-turn resolution | 13/13 | 13/13 | без изменения |
| Requested fact accuracy | 58/71 — 81,69% | 58/71 — 81,69% | без изменения |
| Candidate recall @5 / @10 | 95,12% / 95,12% | 95,12% / 95,12% | без изменения |
| Required chunks in context | 23/29 — 79,31% | 23/29 — 79,31% | без изменения |
| Evidence coverage | 54,46% | 55,36% | +0,90 п.п. |
| Generic no-answer с required retrieval evidence | 7 | 5 | −2: T07 и PR01 исключены |
| Answer modes full / partial / insufficient | 29 / 3 / 24 | 29 / 4 / 23 | корректировка T07/MR08/PR01 |
| Supporting / non-supporting chunks | 76 / 69 | 77 / 68 | целевые изменения boundary |

Новых failed или blocked cases нет. Runtime timeout отсутствовал. Ни одно
изменение не приписано слою без trace: единственные status/source changes
совпадают с тремя targeted corrective cases.

Оставшиеся generic no-answer cases при наличии required raw retrieval
evidence: `CV05`, `SC_OR04`, `MR01`, `MR03`, `MR07`. CV05 и MR01/MR03/MR07
проходят retrieval-oriented fixture expectations, но product-ответ остаётся
`no_answer`; SC_OR04 также остаётся strict-failed. Эти gaps документированы и
не являются новыми регрессиями этапа 10.

## 8. Remaining failures

Все 20 remaining failures классифицированы по первичному слою. Release
blockers среди них не выявлены.

| Case | Категория | Краткий trace | Blocker | Следующее действие |
| --- | --- | --- | --- | --- |
| CL02 | `strict_validator_wording` | корректное missing-subject clarification; `primary_contact` вместо `responsible_person` | нет | определить продуктовую эквивалентность соседних responsibility types |
| CV01 | `retrieval_gap` | employee record Шкиренкова отсутствует в candidates/context | нет | общий alias/function retrieval для рабочего компьютера |
| CV02 | `retrieval_gap` | employee record Шкиренкова отсутствует в candidates/context | нет | улучшить lookup функции «рабочие места» без person-specific правила |
| CV04 | `strict_validator_wording` | clarification корректна; `primary_contact` вместо `responsible_person` | нет | унифицировать strict semantic compatibility |
| RP01 | `context_coverage_gap` | правильный route найден, но required route не вошёл в context | нет | responsibility coverage requirement для composer |
| SC_OR02 | `query_analysis_gap` | resolver сохранил `unknown/ambiguous`, intent responsibility вместо procedure | нет | уточнить conflict policy нормативных вопросов |
| SC_OR03 | `context_coverage_gap` | ответ есть, обязательный policy sibling 67 не вошёл в context | нет | стабилизировать policy sibling coverage |
| SC_OR04 | `query_analysis_gap` | intent `task_management`; selected policy chunks отвергнуты, `no_answer` | нет | отдельно проверить intent и evidence для разговорного варианта |
| SC_OR05 | `context_coverage_gap` | procedure/answer есть, sibling 67 отсутствует в context | нет | стабилизировать policy sibling coverage |
| SC_KP02 | `strict_validator_wording` | valid КП clarification; `primary_contact` вместо `responsible_person` | нет | семантическая совместимость типов |
| SC_KP03 | `strict_validator_wording` | valid clarification; draft `definition` вместо fixture `unknown` | нет | не считать ранний type дефектом при корректном clarification action |
| SC_KP05 | `strict_validator_wording` | valid clarification; draft `definition` вместо fixture `unknown` | нет | то же |
| SC_DOC01 | `query_analysis_gap` | recipient-вопрос остался `procedure`; evidence insufficient | нет | structural recipient signals для кадровых оригиналов |
| SC_DOC03 | `query_analysis_gap` | recipient-вопрос остался `procedure`; evidence insufficient | нет | structural recipient signals для бухгалтерских документов |
| SC_APP04 | `query_analysis_gap` | «куда отправить заявление» осталось `procedure` | нет | application-recipient pattern с object qualifier |
| SC_APP05 | `strict_validator_wording` | предметное clarification; `primary_contact` вместо `responsible_person` | нет | унифицировать responsibility types |
| CR02 | `query_analysis_gap` | resolved fact `document_recipient`, intent `task_management`, `no_answer` | нет | пересогласовать intent после slot resolution |
| CR09 | `strict_validator_wording` | корректный answer, `definition` вместо ожидаемого `list` | нет | answer-shape compatibility definition/list |
| CR10 | `query_analysis_gap` | follow-up сохранил `procedure` вместо `document_recipient` | нет | повторно применять recipient signals к resolved question |
| CR11 | `evidence_gap` | отпускные procedure chunks не подтверждают запрошенного согласующего | нет | формализовать partial recipient evidence или missing slot |

## 9. Organization smoke

После полного architecture pack один раз выполнен короткий retrieval-only
smoke на первых десяти scenarios:

```text
.\.venv\Scripts\python.exe scripts\run_organization_test_pack.py \
  --limit 10 \
  --retrieval-only \
  --output-dir data\test_runs\organization\iteration_11_release_candidate \
  --verbose
```

Artifact:

```text
data/test_runs/organization/iteration_11_release_candidate/
20260731_171338/summary.json
```

Результат: **8 PASS / 1 PARTIAL / 1 FAIL / 0 MANUAL_REVIEW**. Все десять
scenario statuses побайтно по смыслу совпадают с этапом 9; status changes нет.
Top-5 recall — 90%, zero results, runtime errors и hallucination counters — 0.
Structured responsibility/organization core T06 и T08 дополнительно прошли в
полном architecture pack.

## 10. Latency и runtime

| Метрика | Результат |
| --- | ---: |
| Full pack wall time | 65 мин 03 с |
| Retrieval p50 / p95 / max | 63 / 125 / 172 мс |
| End-to-end median / p95 | 18,140 / 141,828 с |
| End-to-end max | 182,203 с, T05A |
| Context chars median / max | 1891 / 5869 |
| Context chunks median / max | 3 / 4 |
| Supporting / non-supporting chunks | 77 / 68 по 56 evidence-evaluated cases |
| Ollama timeout / blocked | 0 / 0 |

Пять самых медленных cases: T05A — 182,203 с; PR01 — 159,359 с; MR06 —
143,047 с; DF01 — 141,828 с; T02 — 131,703 с. Runner отдельно сохраняет
retrieval latency и полную длительность case, но не разделяет Query Analyzer,
orchestration и LLM generation. Поэтому отдельные latency этих фаз имеют
честный статус `not_available`.

## 11. Known limitations

1. Required candidate recall остаётся 39/41, context inclusion — 23/29;
   два retrieval и шесть context gaps не маскируются downstream-слоями.
2. Requested fact accuracy остаётся 58/71. Основные gaps — соседние
   `primary_contact`/`responsible_person`, document recipient и answer shape.
3. Пять generic no-answer cases сохраняются при наличии required raw retrieval
   evidence; они не включают исправленные T07 и PR01.
4. Policy sibling 67 нестабилен в разговорных вариантах распоряжения, хотя T09
   и OR01 проходят.
5. Native claim-to-evidence attribution для произвольных unsupported claims
   отсутствует; artifact явно сохраняет этот observability gap.
6. Full pack сохраняет высокую LLM latency: p95 полного case около 142 секунд.
7. По правилам acceptance repeatability и второй full pack не выполнялись.

## 12. Итоговый release status

**READY_WITH_KNOWN_LIMITATIONS**.

Детерминированный контур зелёный: compileall и 501 tests прошли. T07, PR01,
MR08 и восемь core cases прошли; supporting/non-supporting source boundary
соблюдена. Operational misroutes, missed operational, false clarification,
stale-context leaks, unsupported person answers, contract violations и
fallback равны нулю. Multi-turn resolution — 13/13. Organization smoke не
ухудшился.

Оставшиеся failures не являются новыми critical regressions: status changes
относительно этапа 9 ограничены тремя исправленными cases. Они относятся к
retrieval/context/query-analysis/evidence gaps и strict semantic expectations,
которые можно развивать после MVP без нарушения уже проверенных release
invariants.

## 13. Условия merge

1. Merge должен использовать проверенный HEAD
   `8e86f8b7cb6f3668cf0fecf3b9278c3935093abb` и не включать runtime artifacts.
2. Этот приёмочный отчёт можно зафиксировать отдельным report-only commit.
3. Известные ограничения из разделов 8 и 11 должны быть отражены в release
   notes или issue tracker; 71/71 не является условием merge.
4. При изменении production-кода после этого acceptance требуется новая
   пропорциональная проверка затронутого слоя.

Рекомендуемый report-only commit:

```text
docs(rag): зафиксировать приёмку architecture v2
```

## 14. Ближайшие продуктовые задачи после MVP

- согласовать семантику `primary_contact` и `responsible_person` в strict
  expectations;
- улучшить document-recipient resolution для кадровых, бухгалтерских и
  application follow-up запросов;
- улучшить alias/function retrieval для рабочих компьютеров и рабочих мест;
- стабилизировать включение policy sibling 67 для разговорных вариантов;
- добавить native claim-to-evidence attribution;
- отдельно рассмотреть LLM latency и timeout budget без изменения
  архитектурных invariants.

Production-код на этапе 11 не изменялся. Выполнен ровно один полный 71-case
pack и один organization smoke. Repeatability и второй full pack не
выполнялись. Commit и push на этапе 11 не выполнялись.
