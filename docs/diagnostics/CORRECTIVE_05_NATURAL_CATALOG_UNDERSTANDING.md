# Corrective 05 — Natural Catalog Understanding and Clause Extraction

## 1. Status

**PASS**.

Главный gate выполнен: десять исходных `UNDERSTANDING` defects закрыты. В
единственном post-fix run возникла одна новая C01 UNDERSTANDING-регрессия; после
её локального исправления текущий итог — 0. A Catalog Natural вырос с 1/6 до
6/6, H Human Noise — с 1/4 до 4/4. Official Manager Natural Acceptance вырос
с 17/36 до 29/36.

После run был обнаружен и локально исправлен один новый C01 regression в
очистке entity. Acceptance повторно не запускался; исправление подтверждено
targeted test и отдельным public-flow smoke.

## 2. Branch / HEAD before

- Branch: `refactor/rag-architecture-v2`
- HEAD before/after: `f0b3bae981c91f03e9151713cfe944409227bb69`
- Pre-existing untracked files сохранены без отката:
  `docs/diagnostics/MANAGER_NATURAL_ACCEPTANCE_REPORT.md`,
  `docs/diagnostics/MANAGER_NATURAL_ACCEPTANCE_TRIAGE.md`,
  `scripts/run_manager_natural_acceptance.py`,
  `tests/test_manager_natural_acceptance.py`.
- Commit/push не выполнялись.

## 3. Scope

Изменён только deterministic understanding до retrieval:

- natural catalog subject;
- entity и assembly context;
- embedded code role extraction;
- mixed clause boundary;
- referent внутри одного сообщения;
- ограниченная нормализация технических словоформ;
- structured wrapper для явного `узел + code`;
- JSON diagnostics intermediate representation.

Новый LLM call не добавлен. В mixed flow существующий QueryAnalyzer получает
выделенную corporate/procedure clause вместо всей составной строки.

## 4. Baseline acceptance and taxonomy

```text
Official acceptance:          17/36 (47,22%)
Analytical valid behavior:    19/36 (52,78%)
UNDERSTANDING:                10
ROUTING:                       3
MIXED_COMPOSITION:             2
EVIDENCE_GUARD:                2
CATALOG_RETRIEVAL:             0
CORPORATE_RETRIEVAL:           0
```

Baseline artifact:
`data/test_runs/manager_natural_acceptance/20260812T174614+0300/`.

## 5. Ten baseline UNDERSTANDING defects

| Case | Expected intermediate representation | Before | Downstream effect | Root cause |
| --- | --- | --- | --- | --- |
| A01 | entity `вал`; context `нижняя часть укупорщика` | subject `вал для укупарщика`; procedure/corporate | catalog probe skipped | free-order entity/context and analyzer morphology loss |
| A02 | subject `прижимное устройство` | `перечисли комплектующие прижимного устройства` | weak catalog probe, corporate route | predicate/boilerplate pollution |
| A05 | code `X44235` | code embedded in natural FTS text | prefix path skipped | code role not extracted |
| A06 | code fragment `442351` | full phrase used as FTS text | substring path skipped | code role not extracted |
| C02 | catalog subject `прижимное устройство`; repair clause | subject retained `комплектующие`; mixed detected | catalog requirement not found | catalog clause not normalized |
| C04 | subject `нижняя часть укупорщика`; procedure clause | semicolon/tail stayed in catalog query | catalog requirement not found | clause boundary loss |
| E04 | catalog clause `узел КАДРЫ-001`; onboarding clause | no decomposition; intent `vacation` | catalog not requested, onboarding missed | corporate signal suppressed mixed extraction |
| H01 | entity `вал`; context `нижняя часть укупорщика` | polite wrapper stayed in probe | catalog result rejected | human-noise pollution |
| H03 | spoken `икс 44235` → `X44235` | full phrase used as text | prefix path skipped | spoken technical token not normalized |
| H04 | entity `вал`; context `нижняя часть укупорщика` | subject only assembly context | catalog probe skipped | inverted word order/punctuation |

## 6. Root-cause map

### U1 — Boilerplate/predicate pollution

- Cases: A02, C02.
- Shared mechanism: subject patterns did not remove output verbs and composition
  nouns such as `перечисли` and `комплектующие`.
- Fix: role-aware subject patterns plus content-term filtering.

### U2 — Entity / assembly-context loss

- Cases: A01, H01, H04.
- Shared mechanism: only two rigid entity/context word orders existed.
- Fix: bounded pattern family for relative clauses, polite wrappers, inverted
  `по <context>: <entity> ...` and entity `в/для/на` context.

### U3 — Clause-boundary loss

- Cases: C04, E04; also improves C02/E01/G03.
- Shared mechanism: splitter recognised only simple ` и ` and corporate terms
  could suppress catalog understanding before decomposition.
- Fix: split `и`, comma/semicolon plus `потом/затем`; preserve both clauses and
  resolve same-message referent.

### U4 — Embedded technical code loss

- Cases: A05, A06, H03.
- Shared mechanism: part-number extraction handled bare/explicit command payloads
  but not natural role wording.
- Fix: guarded patterns for `начало кода`, `в коде ... встречается` and spoken
  `икс`; output goes to the existing exact/prefix/substring pipeline.

## 7. Production files changed

- `linehelper/catalogs/chat.py`
- `linehelper/catalogs/models.py`
- `linehelper/llm/answer_generator.py`

Test file:

- `tests/test_catalog_natural_routing.py`

## 8. Subject extraction

- Natural predicates/output instructions are removed only in recognised roles.
- Meaningful technical tokens remain in the search query.
- Search query and normalized semantic subject are separate: FTS still receives
  the original word form, while requirements/diagnostics receive a canonical
  subject where supported.
- Example: `Перечисли комплектующие прижимного устройства` → FTS query
  `прижимного устройства`, subject `прижимное устройство`.

## 9. Entity and assembly context

- `вал в/для нижней части укупорщика` becomes entity `вал` plus assembly context
  `нижней части укупорщика`.
- Polite prefixes and inverted word order are supported.
- Generic preposition splitting has a content-term guard, so `что есть` and
  `проверь каталог` cannot become an entity.
- Relative `внизу/снизу` and `наверху/сверху` are conservatively normalized to
  `нижняя часть`/`верхняя часть`.

## 10. Clause extraction and referent resolution

- Diagnostics now carry `catalog_clause`, `procedure_clause`,
  `resolved_referent`, `understanding_pattern`, `normalization_applied`.
- Mixed clauses split on semantic procedural signals after `и`, comma or
  semicolon with `потом/затем`.
- Existing QueryAnalyzer is invoked once and receives only the corporate clause.
- Pronouns and bounded phrases (`его`, `с ним`, `этим узлом`, `этой частью`)
  are resolved only to the extracted subject of the preceding catalog clause.
- No general coreference engine or additional LLM call was introduced.

## 11. Morphology and human noise

- Small transparent word-form map covers catalog-domain forms of
  `прижимное устройство` and `узел`.
- Procedure detection uses bounded stems for maintenance, repair, replacement,
  instruction, calibration and rules.
- No global fuzzy/Levenshtein logic or heavy NLP dependency was added.
- Spoken `икс` is converted only inside an explicitly marked code phrase.

## 12. Routing impact

Routing was not rewritten. Corrected intermediate representation naturally
changed source decisions. A04 received one local structured extension:
`детали, входящие в узел <code>` now reaches existing
`catalog_assembly_contents` before RAG.

## 13. Before/after intermediate understanding

| Case | Before | After in post-fix run/current verification |
| --- | --- | --- |
| A01 | procedure; no probe | subject `вал нижней части укупорщика`; entity `вал`; context `нижней части укупорщика`; catalog |
| A02 | polluted subject; corporate | FTS query `прижимного устройства`; subject `прижимное устройство`; catalog |
| A05 | natural FTS text | identifier `X44235`; `catalog_prefix` |
| A06 | natural FTS text | identifier `442351`; `catalog_substring` |
| C02 | mixed but catalog not found | catalog subject `прижимное устройство`; repair clause separated; partial mixed |
| C04 | semicolon tail in subject | subject `нижней части укупорщика`; procedure clause separated; partial mixed |
| E04 | corporate-only, intent vacation | catalog clause `узел КАДРЫ-001`; procedure clause onboarding; mixed. Corporate evidence remains a separate residual. |
| H01 | polite wrapper in subject | entity `вал`; context `нижней части укупорщика`; catalog |
| H03 | spoken code in text | identifier `X44235`; catalog prefix |
| H04 | entity lost | entity `вал`; context `нижней части укупорщика`; catalog |

Corrected baseline UNDERSTANDING cases: **10/10**. Still incorrect by primary
UNDERSTANDING layer: **0**.

## 14. Explicitly unchanged

- KHS parser: unchanged.
- Catalog DB schema/content: unchanged.
- ingestion/import: unchanged.
- global FTS ranking: unchanged.
- Catalog Search/retrieval scoring: unchanged.
- Corporate Retrieval: unchanged.
- global EvidenceAssessor: unchanged.
- semantic memory: unchanged.
- 1C: unchanged.
- UI/API: unchanged.
- acceptance scenarios/expectations: unchanged.

## 15. Tests and regressions

### Targeted

`tests/test_catalog_natural_routing.py`, mixed, structured and corrective slices:
**50 passed**.

Covered system families: subject extraction, entity/context, mixed clause,
referent, morphology, human noise, embedded code, short-fragment guard,
structured priority and five corporate false-positive guards.

### Catalog regression

Catalog store, FTS reliability, chat, corrective, natural, mixed, structured and
navigation slice: **88 passed**.

### Structured regression

- H01–H07: **7/7** through `RagAnswerGenerator.answer` and real Catalog Store.
- Structured → corporate leakage: **0/7**.

### Corporate regression

Manager Acceptance group B: **5/5**.

Representative pre-routing guard:

- распоряжение устно;
- документооборот;
- ЗРС;
- цели Serviceline;
- новая должность.

False catalog interception: **0**.

## 16. Manager Natural Acceptance before/after

One post-fix run only:
`manager_natural_acceptance_20260813T143956+0300`.

| Group | Before | After |
| --- | ---: | ---: |
| A Catalog natural | 1/6 | **6/6** |
| B Corporate natural | 5/5 | **5/5** |
| C Mixed | 3/5 | **4/5** |
| D Ambiguous | 2/4 | **2/4** |
| E Partial answers | 1/4 | **2/4** |
| F NOT_FOUND | 3/4 | **3/4** |
| G Domain guard | 1/4 | **3/4** |
| H Human noise | 1/4 | **4/4** |
| **Official** | **17/36** | **29/36 (80,56%)** |

Post-run analytical valid behavior: **31/36 (86,11%)**, adding only D01/D04,
which prior triage classified as ambiguous and whose final prose remains safe.
This does not replace official 29/36.

The post-run artifact records C01 as FAIL because the initial patch left the
verb `Найди` in its entity string. This was fixed afterwards. Current-code
public-flow smoke confirms C01 as mixed, catalog FOUND, `X44235100`, corporate
NOT_FOUND, `partial_mixed`. The acceptance was intentionally not rerun.

## 17. Residual defects by layer

### Artifact result before the final C01 local fix

```text
UNDERSTANDING        1  (C01; runner ошибочно назвал CATALOG_RETRIEVAL)
ROUTING              0 primary residual
MIXED_COMPOSITION    1  (G04; runner назвал ROUTING)
EVIDENCE_GUARD       2  (E03, F04; runner labels them corporate retrieval)
CATALOG_RETRIEVAL    0
CORPORATE_RETRIEVAL  1  (E04)
AMBIGUITY            2  (D01, D04)
```

### Current code after targeted C01 fix

```text
UNDERSTANDING        0
ROUTING              0 primary residual
MIXED_COMPOSITION    1  G04
EVIDENCE_GUARD       2  E03, F04
CORPORATE_RETRIEVAL/
EVIDENCE MATCHING    1  E04
AMBIGUITY            2  D01, D04
```

No Catalog Retrieval engine defect was established.

## 18. Most important residual cases

1. **E03:** catalog facts correct; unrelated regulations still satisfy the
   lubrication requirement — evidence consistency.
2. **F04:** impossible teleportation procedure still receives structured FOUND
   from unrelated documents — evidence guard.
3. **G04:** named document and catalog clause are not composed as mixed — mixed
   composition/domain attribution.
4. **E04:** onboarding chunks are retrieved but rejected because subject
   matching treats `начало` and `начать` as different anchors.

These are outside Corrective 05 stop condition.

## 19. Full pytest / compileall / Git

- Root-level `python -m pytest` collection was blocked before tests by a
  pre-existing inaccessible `pytest-tmp` directory.
- Full configured roots `pytest tests scripts`: **638 passed in 22.51s**.
- `python -m compileall linehelper tests scripts`: **PASS**.
- `git diff --check`: **PASS**. Git only emitted CRLF and inaccessible ignored
  temp-directory warnings.

## 20. Known limitations and next corrective

- Natural parsing remains deliberately bounded, not a general Russian NLP
  engine.
- No typo-distance matching was added.
- D01/D04 still need clarification UX/expectation treatment.
- Evidence consistency and mixed source composition remain outside this scope.

Recommended next package:

**Corrective 06 — Mixed Composition and Evidence Consistency**

Scope: E03, E04, F04, G04 only, with separate evidence/domain gates. Do not
reopen Catalog FTS or the parser without new evidence.

## 21. Artifacts

- Baseline:
  `data/test_runs/manager_natural_acceptance/20260812T174614+0300/`
- Post-Corrective-05:
  `data/test_runs/manager_natural_acceptance/20260813T143956+0300/`
