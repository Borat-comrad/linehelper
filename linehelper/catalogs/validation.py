"""Validation of staging data before a catalog is allowed into the store."""

from __future__ import annotations

from collections import Counter
from typing import Any


def validate_staging(staging: dict[str, Any]) -> dict[str, Any]:
    assemblies = {item["assembly_code"] for item in staging["assemblies"]}
    parts = {item["part_number_original"] for item in staging["parts"]}
    bom = staging["bom_items"]
    pages = staging["pages"]
    page_count = staging["catalog"]["page_count"]
    errors: list[str] = list(staging.get("parser_errors", []))
    warnings: list[str] = list(staging.get("parser_warnings", []))
    invalid = 0
    duplicates = 0
    seen = set()
    for row in bom:
        valid = True
        if row["assembly_code"] not in assemblies:
            errors.append(f"BOM references unknown assembly {row['assembly_code']}"); valid = False
        if row["part_number_original"] not in parts or not row["part_number_original"].strip():
            errors.append("BOM references missing or empty part number"); valid = False
        if not 1 <= row["source_page"] <= page_count:
            errors.append(f"BOM has invalid source page {row['source_page']}"); valid = False
        key = (row["assembly_code"], row["position"], row["part_number_original"], row["source_page"], row["source_row_order"])
        if key in seen:
            duplicates += 1; warnings.append(f"duplicate BOM source row {key}")
        seen.add(key)
        if not valid: invalid += 1
    if bom and invalid == len(bom):
        errors.append("parser did not produce a usable BOM row")
    part_counts = Counter(row["part_number_original"] for row in bom)
    return {"page_count": page_count, "pages_with_text": sum(p["text_quality"] == "good" for p in pages),
        "pages_without_text": sum(p["text_quality"] != "good" for p in pages), "assemblies_detected": len(assemblies),
        "assemblies_with_code": len(assemblies), "assemblies_without_code": 0, "bom_rows_detected": len(bom),
        "bom_rows_valid": len(bom) - invalid, "bom_rows_invalid": invalid, "unique_part_numbers": len(parts),
        "duplicate_part_numbers": sum(n - 1 for n in part_counts.values() if n > 1),
        "rows_without_position": sum(not row["position"] for row in bom),
        "rows_without_part_number": sum(not row["part_number_original"] for row in bom),
        "rows_without_name": sum(not row["description"] for row in bom),
        "rows_without_quantity": sum(row["quantity"] is None for row in bom),
        "unresolved_child_assembly_links": 0, "orphan_bom_items": invalid,
        "duplicate_bom_rows": duplicates, "page_pairing_warnings": [],
        "parser_warnings": warnings, "parser_errors": errors, "quality_gate": "pass" if not errors else "fail"}
