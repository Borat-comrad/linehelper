# Catalog Store Pilot 01: KHS Innofill rev. 05

## Scope and source

This pilot imports exactly one native-text PDF: `data/catalogs/sourse/volzhsky/ETL_89401221_000400__Innofill_RU_05.pdf`. The directory is spelled `sourse` in the supplied source path. The parser found 679 PDF pages, KHS as manufacturer, site `Волжский`, machine 47592, model `Innofill машина для розлива в бутылки DMG VF PET 160/15 RL`, and revision `05 / 21.05.2019` from PDF content (not filename fallback).

## Result

The actual staging run passed the structural quality gate and was committed once to `data/catalogs/catalog_store.db`.

| Measure | Value |
|---|---:|
| Pages with usable text | 676 |
| Pages without sufficient text | 3 |
| Assemblies | 282 |
| BOM rows | 2,505 |
| Valid BOM rows | 2,505 |
| Unique part numbers | 1,506 |
| Structural errors | 0 |
| Parser warnings | 0 |

The staging directory is `data/catalogs/staging/bd2e1fda3f8b65cf/`. It contains `catalog.json`, `assemblies.json`, `parts.json`, `bom_items.json`, `pages.json`, and `validation_report.json`; it is intentionally ignored by Git.

## Design

`linehelper.catalogs` is independent of `linehelper.memory` and the RAG path. SQLite schema version 1 provides `equipment`, `catalogs`, `assemblies`, `parts`, `bom_items`, and `catalog_pages`; `catalog_metadata` holds the version. BOM source ordering and source page are persisted. Original part numbers are immutable; deterministic NFKC/trim/uppercase/compact normalization is a separate lookup representation.

The importer always writes and validates staging before it can commit. Errors include invalid source pages, missing part references, unknown assemblies, and a completely unusable BOM. Ambiguous/unsupported hierarchy and duplicate source rows are warnings. A checksum unique constraint rejects duplicate imports atomically. FTS5 is a removable projection rebuilt solely from the normalized source tables.

## Traceability and smoke checks

An exact lookup returns every usage rather than selecting one. Text search returns structured BOM results. Each result joins BOM item -> assembly -> catalog/equipment and includes `source_page`; for example, the page-13 row position 10 / part `X56767951` traces to assembly `20411616`, catalog revision 05, machine 47592, PDF page 13.

Search smoke uses values read from the staged data, plus a nonexistent code. The expected non-existent lookup returns no rows. No LLM, embeddings, UI integration, QueryAnalyzer, RAG retrieval, or MemoryStore changes are involved.

## Limits and next catalog

The parser is intentionally KHS ETL-specific. It does not interpret drawings, OCR low-text pages, infer unknown parents, recognize supersession, or merge parts by name. A next KHS ETL catalog needs a new layout analysis and a staging-only validation run; if its recurring table/footer form differs, it should receive an explicit parser strategy before commit.
