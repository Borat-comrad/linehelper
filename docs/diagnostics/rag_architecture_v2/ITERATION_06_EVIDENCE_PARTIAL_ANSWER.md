# Итерация 06. Evidence Assessment и Partial Answer

## 1. Исходная проблема

После этапа 5 нужные chunks доходили до `selected context`, но прежний
`has_sufficient_context` принимал решение почти над контекстом целиком. Он не
разделял требования ответа, не отличал поддерживающие chunks от шума и не имел
native-режима частичного ответа.

Подтверждённые traces до изменения:

- T04: chunks 47 и 19 проходили общий gate, хотя они не называли
  первоначального адресата заявления;
- T03: chunks 44, 29 и 39 вместе передавались в answer prompt, хотя основной
  процедурный ответ подтверждал только chunk 29.

Retrieval и `ContextComposer` в этой итерации не изменялись.

## 2. Архитектура evidence layer

Новый участок pipeline:

```text
ContextComposer.selected
→ EvidencePlanner
→ EvidencePlan
→ EvidenceAssessor
→ EvidenceDecision
→ full_answer / partial_answer / insufficient_evidence
→ answer prompt только с supporting chunks
```

Assessment полностью детерминирован. Дополнительный LLM-вызов не добавлен.
Используются `requested_fact_type`, `answer_shape`, subject, тип logical unit,
structured record metadata, source/title/section, предметные anchor terms и
структура вопроса.

## 3. EvidencePlan и EvidenceDecision

`EvidencePlan` содержит:

- тип запрашиваемого факта и форму ответа;
- минимальный набор `EvidenceRequirement`;
- допустимость partial answer;
- минимальное число подтверждённых требований;
- диагностические subject/intent metadata.

`EvidenceRequirement` хранит идентификатор, тип, описание, обязательность,
coverage key, результат assessment, supporting chunk IDs и структурированные
причины поддержки или отклонения.

`EvidenceDecision` содержит:

- `full_answer`, `partial_answer` или `insufficient_evidence`;
- поддержанные и неподдержанные требования;
- supporting и non-supporting chunks в исходном порядке selected context;
- детерминированные decision reasons;
- coverage rate без псевдоточной confidence-оценки.

Native diagnostics доступны в `RagAnswer`, CLI debug и architecture artifact.

## 4. Правила full / partial / insufficient

- `full_answer`: все обязательные evidence requirements подтверждены и есть
  supporting chunks.
- `partial_answer`: обязательная часть неполна, но разрешён partial mode и
  подтверждено хотя бы одно полезное требование.
- `insufficient_evidence`: минимальное полезное покрытие отсутствует.

Для простых фактов сохранена прежняя безопасная политика. Для процедур
проверяются предмет и устойчивые признаки процедурного/нормативного материала.
Для ответственности требуется structured record с совпадающим предметом. Для
списков сохраняются различные logical units. Запрос срока создаёт отдельное
deadline requirement.

При partial mode prompt получает:

- подтверждённые требования;
- неподтверждённые требования;
- только разрешённые evidence chunks;
- запрет дополнять пробелы предположениями;
- порядок «сначала полезная подтверждённая часть, затем отсутствующие сведения».

## 5. T04

В едином targeted live artifact:

```text
selected context: [47, 19]
supporting: [47]
non-supporting: [19]
supported: related_submission_procedure
unsupported: named_document_recipient
answer mode: partial_answer
response kind: partial_answer
```

Ответ не выдумал сотрудника и отдельно сообщил, что конкретный адресат в
материалах отсутствует. Strict target отметил case как failed только потому,
что фраза «недостаточно данных» не совпала ни с одной из трёх буквальных
альтернатив fixture: «не указан», «не назван», «нет данных». Fixture и prompt
после live-run не подгонялись под эту метрику. Инвариант evidence-слоя выполнен.

## 6. T03 и procedural noise

Live trace:

```text
selected context: [44, 29, 39]
supporting: [29]
non-supporting: [44, 39]
answer mode: full_answer
sources exposed to answer generation: [29]
strict status: passed
```

Chunks 44 и 39 сохранены в context diagnostics, но не переданы как evidence
sources и не стали основанием для дополнительных утверждений.

## 7. Регрессии T02 / T05A / T06 / T08 / OR01

| Case | Mode | Supporting | Non-supporting | Status |
| --- | --- | --- | --- | --- |
| T02 | `full_answer` | 72, 71, 73, 74 | — | passed |
| T05A | `full_answer` | 42, 43, 27 | — | passed |
| T06 | `full_answer` | 594, 514 | 592 | passed |
| T08 | `full_answer` | 437, 439 | 73 | passed |
| OR01 | `full_answer` | 70, 69, 67 | — | passed |

Для T02 сохранены 4/4 rules. Для T05A отдельное deadline requirement
подтверждено правилом о 14 календарных днях. Для T08 policy chunk не использован
как доказательство персональной ответственности. Operational misroutes и false
clarification в targeted artifact равны нулю.

## 8. Test budget

- новых unit test items: 8;
- новых deterministic integration test items: 3;
- новых fixture cases: 0.

Лимит этапа соблюдён.

Unit coverage:

1. full decision;
2. partial decision;
3. insufficient decision;
4. supporting/non-supporting chunks;
5. deterministic ordering;
6. duplicate evidence и requirement coverage;
7. structured responsibility;
8. diagnostics/serialization.

Integration coverage:

1. T04 partial orchestration;
2. T03 required evidence и procedural noise;
3. агрегированная регрессия T02/T05A/T06/T08/OR01.

## 9. Targeted tests

- evidence unit/integration и compatibility postfix: 12 passed;
- answer generator, context composer и architecture harness: 105 passed;
- answer generator, safe clarification и architecture harness после postfix:
  139 passed.

Тесты не сравнивают полный текст LLM-ответа.

## 10. Compileall и full pytest

```text
python -m compileall linehelper tests scripts
result: passed

pytest tests scripts/tests
result: 478 passed in 19.48s
```

До этапа было 467 tests; добавлено ровно 11 items.

## 11. Targeted live artifact

Единственный live-run этапа:

```text
data/test_runs/rag_architecture_v2/iteration_06_targeted/
  20260731_101307/baseline.json
```

Результат: 6 passed / 1 failed / 0 blocked. Единственный strict failure — T04,
описанный выше; native partial-answer invariant выполнен.

Дополнительные показатели выбранных cases:

- required candidate recall @5/@10: 12/12 — 100%;
- required chunks in context: 12/12 — 100%;
- evidence coverage: 92,86%;
- requested fact accuracy: 7/7;
- operational misroutes: 0;
- missed operational: 0;
- generic no-answer при наличии evidence: 0.

## 12. Ограничения

- Evidence vocabulary является компактным MVP и не заменяет универсальный
  semantic entailment.
- Claim-level attribution и автоматический поиск unsupported claims в готовом
  тексте ответа ещё отсутствуют.
- Partial-answer wording остаётся вариативным свойством локальной LLM; strict
  lexical fixture может отклонить семантически эквивалентную формулировку.
- T09 и его `requested_fact_type=unknown` не исправлялись: это вне scope.
- Source authority, конфликтующие источники и typed responsibility policy не
  менялись.
- Полный 71-case live architecture pack и полный organization pack не
  запускались согласно бюджету итерации.

