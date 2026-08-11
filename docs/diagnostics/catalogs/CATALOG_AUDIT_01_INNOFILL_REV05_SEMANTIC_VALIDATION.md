# Catalog Audit 01 - independent semantic validation, KHS Innofill rev. 05

## Scope and evidence

- Branch / HEAD: `refactor/rag-architecture-v2` / `004b433`.
- PDF actually present at `data/catalogs/sourse/volzhsky/ETL_89401221_000400__Innofill_RU_05.pdf`; the requested `source/` spelling is absent. This is an existing path spelling issue, not a second source document.
- PDF SHA-256: `bd2e1fda3f8b65cf9cb80a8e0690394a8dba59613e28d72414bdafbf8f06d2b0`.
- DB: `data/catalogs/catalog_store.db`, 1,777,664 bytes at audit time. Catalog checksum equals the PDF checksum.

This audit did not call `KHSETLCatalogParser`, staging JSON, parser validation, or parser tests to obtain expected BOM content. It read the native PDF text layer directly with PyMuPDF, parsed the recurring table grammar independently for the selected pages, and visually inspected representative table/drawing/low-text pages. SQLite was queried separately.

## Direct SQL inventory

| Table / measure | Actual SQL count |
|---|---:|
| `catalogs` | 1 |
| `equipment` | 1 |
| `assemblies` | 282 |
| `parts` | 1,506 |
| `bom_items` | 2,505 |
| `catalog_pages` | 679 |

The six inspected source tables are `equipment`, `catalogs`, `assemblies`, `parts`, `bom_items`, and `catalog_pages`. Their schema has the expected foreign keys and uniqueness constraints, but those facts were not accepted as semantic proof.

## Sampling method

The fixed sample uses source-page targets spanning the beginning, first quarter, middle, third quarter, and end. It checks first/middle/last independently read rows of every assembly, then adds deterministic extras with seed `20260810`, for 50 rows total.

Assemblies and independently counted PDF / DB BOM rows:

| Assembly | First BOM page | PDF | DB | Difference |
|---|---:|---:|---:|---:|
| `10307585` | 9 | 22 | 22 | 0 |
| `20411616` | 13 | 15 | 15 | 0 |
| `20411617` | 67 | 9 | 9 | 0 |
| `58831843S104` | 151 | 19 | 19 | 0 |
| `58831896S500` | 201 | 5 | 5 | 0 |
| `58857804S001` | 251 | 2 | 2 | 0 |
| `58885289S403` | 301 | 5 | 5 | 0 |
| `301067011930` | 351 | 1 | 1 | 0 |
| `X59922872` | 401 | 8 | 8 | 0 |
| `58866749S001` | 451 | 20 | 20 | 0 |
| `58865897S017` | 499 | 24 | 24 | 0 |
| `58865726S021` | 551 | 9 | 9 | 0 |
| `58865897S037` | 601 | 4 | 4 | 0 |
| `58826812S454` | 651 | 6 | 6 | 0 |
| `X07000923` | 675 | 1 | 1 | 0 |

Total independently counted rows: 150. No false-positive DB row and no false-negative PDF row was found in these assembly-boundary checks. The sample includes short/long numeric codes, `X...` and `H...` alphanumeric codes, quantities 2, 3, 8, 23, 76, 96, and 157, and repeated parts across assemblies.

## 50 row checks

All rows below are `PASS` for independently read assembly code/name, position, literal original part number, Russian name, quantity, unit, source page, and membership in the assembly. `source_page` is the PDF page containing the BOM row.

| # | Assembly | PDF page | Position | Part number | Quantity | Result |
|---:|---|---:|---:|---|---:|---|
| 1 | 10307585 | 9 | 200 | 20411616 | 1.000 | PASS |
| 2 | 10307585 | 9 | 1650 | 20414979 | 1.000 | PASS |
| 3 | 10307585 | 11 | 2900 | 20411672 | 1.000 | PASS |
| 4 | 20411616 | 13 | 10 | X56767951 | 1.000 | PASS |
| 5 | 20411616 | 13 | 72 | 58801534S005 | 1.000 | PASS |
| 6 | 20411616 | 13 | 130 | 59695318S001 | 1.000 | PASS |
| 7 | 20411617 | 67 | 10 | 58803877S001 | 157.000 | PASS |
| 8 | 20411617 | 67 | 60 | H29205010648 | 157.000 | PASS |
| 9 | 20411617 | 67 | 380 | 58803131S003 | 1.000 | PASS |
| 10 | 58831843S104 | 151 | 10 | 58831835S002 | 1.000 | PASS |
| 11 | 58831843S104 | 151 | 80 | 58831836S001 | 1.000 | PASS |
| 12 | 58831843S104 | 153 | 190 | 58831842S002 | 1.000 | PASS |
| 13 | 58831896S500 | 201 | 40 | 301132173270 | 2.000 | PASS |
| 14 | 58831896S500 | 201 | 80 | 301132172270 | 1.000 | PASS |
| 15 | 58831896S500 | 201 | 110 | 301262456880 | 1.000 | PASS |
| 16 | 58857804S001 | 251 | 10 | 301054615040 | 1.000 | PASS |
| 17 | 58857804S001 | 251 | 20 | X58856061 | 1.000 | PASS |
| 18 | 58885289S403 | 301 | 10 | 301067013971 | 1.000 | PASS |
| 19 | 58885289S403 | 301 | 60 | 301067019450 | 1.000 | PASS |
| 20 | 58885289S403 | 301 | 120 | 301132171420 | 2.000 | PASS |
| 21 | 301067011930 | 351 | 10 | 301999964233 | 1.000 | PASS |
| 22 | X59922872 | 401 | 10 | X59922005 | 1.000 | PASS |
| 23 | X59922872 | 401 | 50 | 301132691080 | 3.000 | PASS |
| 24 | X59922872 | 401 | 90 | 301148061821 | 1.000 | PASS |
| 25 | 58866749S001 | 451 | 10 | X58884081 | 1.000 | PASS |
| 26 | 58866749S001 | 451 | 90 | 301055111870 | 1.000 | PASS |
| 27 | 58866749S001 | 453 | 180 | 301186361480 | 3.000 | PASS |
| 28 | 58865897S017 | 499 | 10 | 58884271S027 | 1.000 | PASS |
| 29 | 58865897S017 | 499 | 92 | 58865291S003 | 8.000 | PASS |
| 30 | 58865897S017 | 501 | 142 | 301133170150 | 2.000 | PASS |
| 31 | 58865726S021 | 551 | 2 | 301991013900 | 2.000 | PASS |
| 32 | 58865726S021 | 551 | 20 | 58819849S311 | 1.000 | PASS |
| 33 | 58865726S021 | 551 | 51 | 301137273220 | 3.000 | PASS |
| 34 | 58865897S037 | 601 | 130 | 58868270S108 | 1.000 | PASS |
| 35 | 58865897S037 | 601 | 141 | X58716070 | 2.000 | PASS |
| 36 | 58865897S037 | 601 | 142 | 301133170150 | 2.000 | PASS |
| 37 | 58826812S454 | 651 | 1 | X58716070 | 1.000 | PASS |
| 38 | 58826812S454 | 651 | 10 | 58821778S115 | 1.000 | PASS |
| 39 | 58826812S454 | 651 | 30 | 301023100600 | 2.000 | PASS |
| 40 | X07000923 | 675 | 10 | X58899772 | 1.000 | PASS |
| 41 | 58865726S021 | 551 | 30 | 58819849S311 | 1.000 | PASS |
| 42 | 58831843S104 | 151 | 15 | 58831836S001 | 1.000 | PASS |
| 43 | X59922872 | 401 | 40 | 301024012600 | 1.000 | PASS |
| 44 | 10307585 | 11 | 2600 | 20412573 | 1.000 | PASS |
| 45 | 58885289S403 | 301 | 90 | 301053150502 | 1.000 | PASS |
| 46 | 58866749S001 | 451 | 110 | 301023301067 | 1.000 | PASS |
| 47 | 58865897S017 | 501 | 140 | 301991013900 | 2.000 | PASS |
| 48 | 10307585 | 9 | 1400 | X59638179 | 1.000 | PASS |
| 49 | 58865897S017 | 499 | 100 | 301132172170 | 8.000 | PASS |
| 50 | 58866749S001 | 451 | 20 | X44240701 | 1.000 | PASS |

## Accuracy and completeness

| Metric | Result |
|---|---:|
| Full core-row accuracy (50 / 50) | 100% |
| Assembly code/name accuracy | 50 / 50 (100%) |
| Position accuracy | 50 / 50 (100%) |
| Original part-number accuracy | 50 / 50 (100%) |
| Part-name accuracy | 50 / 50 (100%) |
| Quantity accuracy | 50 / 50 (100%) |
| Unit accuracy | 50 / 50 (100%) |
| Source-page accuracy | 50 / 50 (100%) |
| Assembly completeness (15 / 15) | 150 / 150 rows; all equal |
| False positives / false negatives in boundary sample | 0 / 0 |

Repeated usage was also checked: `301133170150` and `X58716070` each have 73 usages; `58819849S311` has 30. Sampled usages on pages 499/501, 551, 601, and 651 match their PDF assembly, position, and quantity. Original codes with significant spaces (for example `XFH 20093`) remain literally stored; normalized lookup is separate.

## Important limitation: printed reference page is lost

The source table has a rightmost printed `Страница` reference column. Visual inspection of page 13 confirms populated examples (for example the row at position 10 points to page 14). The current `bom_items` schema and its `metadata_json` retain no per-row reference-page value. Therefore the audit does **not** claim reference-page fidelity: it is absent rather than wrong. `source_page` is present and correct, so every stored BOM row remains traceable to the page that contains it.

This is a systematic completeness gap, with a clear corrective iteration: add a nullable `reference_page` (or documented metadata representation), independently extract only the table's rightmost reference column, backfill by reimporting this one catalog, then rerun this audit. No corrective code was made during this audit.

## Poor, unknown, and mixed pages

The three `poor` pages are 5, 676, and 678. Page 5 is an instruction-page continuation (`E-02`); pages 676 and 678 are visually blank. They contain no BOM or assembly data, so no technical data is lost.

Page types from SQL: drawing 334, parts_list 332, front_matter 3, mixed 3, unknown 7. Unknown pages 1, 2, 4, 5, 6, 676, and 678 are cover/revision/instructions/blank pages, not missed BOM. Mixed pages 7, 413, and 491 were inspected: page 7 documents the KHS table format; pages 413 and 491 are empty table templates carrying assembly footer information but no BOM rows. They do not hide unimported BOM rows.

## Anomalies

- 28 part numbers contain spaces; PDF examples prove this is valid KHS notation, not corruption.
- 52 repeated positions occur within an assembly. Example `X58834950` repeats the same BOM entries on PDF pages 89 and 91; both source pages visibly contain the same specification, so their separate source-context rows are correct.
- One assembly (`20000001`, page 7) has no BOM rows because it is an explanatory example in front matter, not a technical assembly. Forty-one assemblies have one row; sampled one-row cases agree with PDF.
- No BOM quantity or description is empty; no original code is shorter than five or longer than 20 characters.
- Generic names map to many codes (for example fasteners); this is expected and does not drive part identity.

## FTS smoke

For ten sampled parts, deterministic exact original and normalized lookup returned the correct structured usage in all 10 cases. FTS part-number search returned the matching part for 9 of 10; `58831836S001` did not return within the five-result FTS result set despite exact lookup succeeding. Part-name and assembly-name FTS smoke was checked on source-derived names/codes and returned structured source-traceable records. This is a secondary-index ranking/tokenization issue, not a source-data error.

## Verdict

**PASS_WITH_MINOR_ISSUES.** The independently sampled core technical import is trustworthy for exact part/BOM lookup, quantities, assembly boundaries, reuse, and source-page traceability. There is no sampled evidence of a systematic extraction error or material BOM loss. It should not yet be described as a complete transfer of every printed catalog attribute because row-level reference pages are systematically omitted. Parser correction is recommended before using reference-page navigation as a product feature; it is not required to trust the current store as a structured source for the verified core fields.
