# Итерация 07. Grounded Answer Contract

## 1. Исходная проблема

После этапа 6 `EvidenceDecision` уже отделял supporting chunks от шума, но
формат partial answer и список отображаемых источников оставались неявным
свойством ответа LLM. В частности, T04 был корректным native
`partial_answer`, однако strict checker не распознавал вариативную формулировку
об отсутствующих данных.

## 2. Место AnswerContract в pipeline

```text
EvidenceDecision
→ AnswerContractBuilder
→ GroundedAnswerContract
→ один LLM draft только по supporting evidence
→ AnswerContractValidator
→ GroundedAnswerRenderer
→ RagAnswer.answer + RagAnswer.sources
```

Дополнительный LLM-вызов не добавлен. `EvidencePlanner`,
`EvidenceAssessor`, retrieval и `ContextComposer` не изменялись.

## 3. Модели контракта и источников

`GroundedAnswerContract` фиксирует:

- `answer_mode`;
- supported и unsupported requirements;
- `allowed_chunk_ids`;
- упорядоченные `SourceEntry`;
- обязательные секции;
- deterministic rendering policy.

`SourceEntry` связывает chunk, source/title/section/record key с IDs
поддерживаемых requirements. Записи дедуплицируются по устойчивой
идентичности chunk, сохраняют порядок supporting chunks и не включают
non-supporting chunks.

`AnswerContractValidation` содержит только воспроизводимые структурные
результаты: violations, missing sections, запрещённые source references,
rendering actions и признак safe fallback. Семантический entailment текста
LLM не имитируется.

## 4. Режимы rendering

- `full_answer`: содержательный draft и детерминированные источники; секция
  отсутствующих данных не добавляется.
- `partial_answer`: содержательный draft по supporting evidence, затем
  единый блок `Нет данных по следующим пунктам:` из unsupported requirements,
  затем источники из `SourceEntry`.
- `insufficient_evidence`: обычная содержательная генерация не вызывается;
  renderer строит безопасное сообщение и список неподтверждённых требований.

Источники остаются отдельным структурированным полем `RagAnswer.sources`,
которое уже выводят CLI и UI. Блок источников не дублируется внутри
`RagAnswer.answer`.

## 5. Prompt и deterministic missing-information block

В prompt передаются только:

- answer mode;
- supported requirements;
- supporting excerpts;
- allowed chunk IDs и sources;
- запрет использовать внешние сведения, обсуждать неподтверждённые пункты и
  добавлять собственный список источников.

Unsupported requirements в prompt не передаются. Их описание использует
только renderer:

```text
Нет данных по следующим пунктам:
- <описание unsupported requirement>
```

Validator удаляет добавленный LLM source block. Пустой draft или явная ссылка
на запрещённый chunk/source приводит к безопасному deterministic fallback без
повторного запроса модели.

## 6. T04

Единый targeted live artifact:

```text
answer mode: partial_answer
supporting: [47]
non-supporting: [19]
supported requirement: related_submission_procedure
unsupported requirement: named_document_recipient
allowed/rendered sources: [47]
final sections: supported_answer, missing_information, sources
contract valid: true
fallback applied: false
strict status: passed
```

Именованный адресат не добавлен. Renderer детерминированно добавил:

```text
Нет данных по следующим пунктам:
- именованный первоначальный адресат документа
```

Fixture не менялся.

## 7. T03

```text
selected context: [44, 29, 39]
supporting: [29]
non-supporting: [44, 39]
allowed/rendered sources: [29]
answer mode: full_answer
strict status: passed
```

Chunks 44 и 39 сохранились в context/evidence diagnostics, но отсутствуют в
контракте разрешённых источников, prompt и rendered source entries.

## 8. Регрессии

| Case | Mode | Rendered source chunks | Status |
| --- | --- | --- | --- |
| T02 | `full_answer` | 72, 71, 73, 74 | passed |
| T05A | `full_answer` | 42, 43, 27 | passed |
| T06 | `full_answer` | 594, 514 | passed |
| T08 | `full_answer` | 437, 439 | passed |
| OR01 | `full_answer` | 70, 69, 67 | passed |

T08 не показывает non-supporting policy chunk 73 как источник. В targeted
artifact operational misroutes и false clarification равны нулю.

## 9. Тесты и test budget

- новых unit test items: 8;
- deterministic integration items этапа: 3 существующих orchestration tests
  расширены contract-инвариантами;
- новых fixture cases: 0.

Unit coverage: full, partial, insufficient, missing-information rendering,
source dedupe/order, исключение non-supporting chunks, validation/fallback,
diagnostics serialization.

Integration coverage: T04, T03 и агрегированная регрессия
T02/T05A/T06/T08/OR01.

Запуски:

```text
grounded contract + evidence + answer generator + harness: 102 passed
prompt/runner compatibility + scripts tests: 123 passed
```

Лимит 8 unit / 3 integration соблюдён.

## 10. Compileall и full pytest

```text
python -m compileall linehelper tests scripts
result: passed

pytest tests scripts/tests
result: 486 passed in 20.62s
```

## 11. Targeted live artifact

Единственный live-run:

```text
data/test_runs/rag_architecture_v2/iteration_07_targeted/
  20260731_111917/baseline.json
```

Результат: **7 passed / 0 failed / 0 blocked**.

Дополнительные показатели выбранных cases:

- requested fact accuracy: 7/7;
- required candidate recall @5/@10: 12/12;
- required chunks in context: 12/12;
- evidence coverage: 92,86%;
- operational misroutes: 0;
- missed operational: 0;
- generic no-answer при наличии evidence: 0.

Полный 71-case architecture pack, repeatability reruns и полный organization
pack не запускались согласно бюджету итерации.

## 12. Diagnostics

В `RagAnswer`, CLI debug и architecture artifact добавлены:

- `answer_contract`;
- `answer_mode`;
- supported/unsupported requirement IDs;
- `allowed_chunk_ids`;
- `rendered_source_entries`;
- `contract_validation`;
- `validation_violations`;
- `fallback_applied`;
- `final_answer_sections`.

Diagnostics содержат структурированные правила и результаты, без скрытого
chain-of-thought.

## 13. Ограничения

- Validator проверяет структуру и явные source identifiers, но не выполняет
  semantic entailment всего draft.
- Содержательная часть по supporting evidence остаётся формулировкой LLM.
  Renderer гарантирует единый missing-information block, но не удаляет
  семантически эквивалентную оговорку модели об отсутствии данных.
- Claim-level цитирование внутри каждого предложения не реализовано; доступна
  requirement-to-chunk атрибуция.
- Source authority, конфликтующие документы и T09
  (`requested_fact_type=unknown`) не менялись.
- UI-дизайн не менялся: он использует прежнее отдельное отображение
  `RagAnswer.sources`.
