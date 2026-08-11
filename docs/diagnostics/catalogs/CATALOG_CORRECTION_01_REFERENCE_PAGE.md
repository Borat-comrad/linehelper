# Catalog Correction 01 - KHS printed reference page

The Innofill rev. 05 audit identified that `source_page` was preserved but the printed BOM column `Страница` was not. This correction adds `bom_items.reference_page INTEGER NULL` without changing the meaning or value of `source_page`.

## Data path

`fitz.Page.get_text("words")` reads the physical table layout. The parser finds position cells at the left of the BOM and integer cells in the rightmost `Страница` column, then associates them by matching vertical coordinate. The resulting `reference_page` is explicit in the parser BOM dict, staging `bom_items.json`, and the SQLite INSERT. `source_page` continues to be the parser loop's physical `page_no`.

The PDF reconnaissance found 358 printed values on 114 BOM pages. Every value is one integer (12 through 674); no empty textual value, multiple pages, range, or nonnumeric token occurred. Consequently the field is nullable `INTEGER`, with NULL meaning a blank printed cell.

## Migration and applied backfill

Schema version is now 2. `ensure_schema()` creates the new column for new databases and uses `PRAGMA table_info(bom_items)` plus `ALTER TABLE ... ADD COLUMN` for an old database. The check makes repeated initialization safe and leaves all existing columns and rows intact.

For the already imported checksum, the explicit `--backfill-reference-pages` action reparses staging and updates only `reference_page` by catalog checksum, assembly code, position, original part number, physical source page, and source row order. It updated 2,505 existing records, setting 358 values and 2,147 NULLs. The core-column SHA-256 before and after was identical: `d518e29f92c15f4888420d8e54fc28417431e6c092fd533ddf8dca568e66155c`.

## PDF-to-SQLite checks

Ten direct coordinate-level comparisons passed, including `10307585` / position 200 / PDF source 9 / reference 12; `20411616` / position 10 / PDF source 13 / reference 14; `20411617` / position 10 / source 67 / reference 68; and `58866749S001` / position 10 / source 451 / reference 454. Five blank-reference rows were verified as NULL, including assembly `20411616` positions 70, 71, 72, and 130 on source page 13.

## Result

**PASS.** Reference pages are now preserved separately, source pages are unchanged, migration is backward-compatible and idempotent, and no new extraction anomaly was found.
