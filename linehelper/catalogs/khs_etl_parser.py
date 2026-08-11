"""Parser for the repeating text-layer layout used by KHS ETL spare-part reports."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import fitz


PARSER_VERSION = "khs-etl-1.1"
_POSITION = re.compile(r"^\d{1,4}$")
_QUANTITY = re.compile(r"^\d+(?:[,.]\d+)?$")
_ASSEMBLY = re.compile(r"^((?:[A-Z]{2,3}\s+\d+)|(?:X?\d{5,}[A-Z0-9-]*))\s+(.+)$")


def normalize_part_number(value: str) -> str:
    """Keep an original code intact while making exact lookup deterministic."""
    import unicodedata

    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    return re.sub(r"[\s._/-]+", "", normalized)


class KHSETLCatalogParser:
    """KHS-specific parser. It deliberately relies on recurring labels, not page numbers."""

    def parse(self, source: Path, site: str) -> dict[str, Any]:
        source = Path(source)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        document = fitz.open(source)
        page_texts = [page.get_text("text") for page in document]
        first_text = "\n".join(page_texts[:3])
        machine = self._match(first_text, r"(?:Номер машины|Machine number):\s*\n?\s*(\S+)")
        revision, revision_date = self._revision(first_text)
        model = self._match(first_text, r"Type:\s*\n?(.+?)\nRevision:")
        assemblies: dict[str, dict[str, Any]] = {}
        parts: dict[str, dict[str, Any]] = {}
        bom_items: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        warnings: list[str] = []

        for page_no, text in enumerate(page_texts, 1):
            reference_pages = self._reference_pages(document[page_no - 1])
            current, parent = self._footer_assemblies(text)
            rows = self._rows(text, reference_pages)
            page_type = self._page_type(text, rows)
            text_quality = "good" if len(text.strip()) >= 80 else "poor"
            pages.append({"page_number": page_no, "page_type": page_type,
                          "assembly_code": current["assembly_code"] if current else None,
                          "text_quality": text_quality, "metadata_json": {}})
            if not current:
                continue
            code = current["assembly_code"]
            entry = assemblies.setdefault(code, {**current, "parent_assembly_code": None,
                "drawing_page": None, "parts_list_page": None, "source_page_start": page_no,
                "source_page_end": page_no, "metadata_json": {}})
            entry["source_page_start"] = min(entry["source_page_start"], page_no)
            entry["source_page_end"] = max(entry["source_page_end"], page_no)
            if page_type == "drawing" and entry["drawing_page"] is None:
                entry["drawing_page"] = page_no
            if rows and entry["parts_list_page"] is None:
                entry["parts_list_page"] = page_no
            if parent:
                existing_parent = entry["parent_assembly_code"]
                if existing_parent and existing_parent != parent["assembly_code"]:
                    warnings.append(f"assembly {code}: ambiguous parent candidates {existing_parent}, {parent['assembly_code']}")
                    entry["parent_assembly_code"] = None
                elif not existing_parent:
                    entry["parent_assembly_code"] = parent["assembly_code"]
            for order, row in enumerate(rows, 1):
                part = parts.setdefault(row["part_number_original"], {
                    "part_number_original": row["part_number_original"],
                    "part_number_normalized": normalize_part_number(row["part_number_original"]),
                    "part_name": row["part_name"], "manufacturer": "KHS", "metadata_json": {},
                })
                if not part["part_name"] and row["part_name"]:
                    part["part_name"] = row["part_name"]
                bom_items.append({"assembly_code": code, "part_number_original": row["part_number_original"],
                    "position": row["position"], "quantity": row["quantity"], "unit": row["unit"],
                    "description": row["part_name"], "child_assembly_code": None, "source_page": page_no,
                    "reference_page": row["reference_page"],
                    "source_row_order": order, "metadata_json": {"german_name": row["german_name"]}})

        # A footer parent is accepted only when it is another objectively documented assembly.
        for item in assemblies.values():
            parent = item["parent_assembly_code"]
            if parent and parent not in assemblies:
                warnings.append(f"assembly {item['assembly_code']}: parent {parent} has no assembly page")
                item["parent_assembly_code"] = None
        return {"catalog": {"document_type": "spare_parts_catalog", "title": "Innofill spare parts report",
            "source_filename": source.name, "source_checksum": checksum, "machine_number": machine,
            "revision": revision, "revision_date": revision_date, "language": "ru", "page_count": len(document),
            "parser_version": PARSER_VERSION, "metadata_json": {"revision_source": "PDF page 2/3"}},
            "equipment": {"manufacturer": "KHS", "site": site, "equipment_type": "Innofill", "model": model,
            "machine_number": machine, "aliases_json": []}, "assemblies": list(assemblies.values()),
            "parts": list(parts.values()), "bom_items": bom_items, "pages": pages,
            "parser_warnings": warnings, "parser_errors": []}

    @staticmethod
    def _match(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text, re.S)
        return match.group(1).strip() if match else None

    @staticmethod
    def _revision(text: str) -> tuple[str | None, str | None]:
        match = re.search(r"Revision:\s*\n?([0-9]+)\s*/\s*([0-9.]+)", text)
        return (match.group(1), match.group(2)) if match else (None, None)

    def _footer_assemblies(self, text: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
        section = text.rsplit("Узел", 1)
        if len(section) != 2:
            return None, None
        lines = [line.strip() for line in section[1].splitlines() if line.strip()]
        lines = [line for line in lines if not line.startswith("KHS-V02") and not line.startswith("Страница") and not line.isdigit()]
        found = []
        for line in lines[:2]:
            match = _ASSEMBLY.match(line)
            if match:
                name = match.group(2).strip()
                # The root footer uses the catalog code followed by the revision only.
                found.append({"assembly_code": match.group(1).strip(), "assembly_name": None if re.fullmatch(r"\d{1,2}", name) else name})
        return (found[0] if found else None, found[1] if len(found) > 1 else None)

    @staticmethod
    def _reference_pages(page: fitz.Page) -> list[int | None]:
        """Read numeric values from the printed rightmost `Страница` column."""
        positions: list[float] = []
        references: list[tuple[float, int]] = []
        for x0, y0, _x1, _y1, value, *_ in page.get_text("words"):
            if not 68 < y0 < 530:
                continue
            if 25 < x0 < 75 and _POSITION.fullmatch(value):
                positions.append(y0)
            elif x0 > 780 and value.isdecimal():
                references.append((y0, int(value)))
        return [next((value for y, value in references if abs(y - position_y) < 4), None) for position_y in positions]

    def _rows(self, text: str, reference_pages: list[int | None] | None = None) -> list[dict[str, Any]]:
        body = text.split("Машина", 1)[0]
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        rows: list[dict[str, str]] = []
        i = 0
        row_index = 0
        while i + 3 < len(lines):
            if not _POSITION.fullmatch(lines[i]) or not self._part_number(lines[i + 1]):
                i += 1; continue
            quantity_index = next((j for j in range(i + 2, min(len(lines), i + 10)) if _QUANTITY.fullmatch(lines[j])), None)
            if quantity_index is None or quantity_index + 1 >= len(lines) or not lines[quantity_index + 1].startswith("штук"):
                i += 1; continue
            description = " ".join(lines[i + 2:quantity_index]).strip()
            german = ""
            end = quantity_index + 2
            while end < len(lines) and not (_POSITION.fullmatch(lines[end]) and end + 1 < len(lines) and self._part_number(lines[end + 1])):
                if lines[end].endswith("Stück") or lines[end] == "Stück":
                    end += 1; break
                german += (" " if german else "") + lines[end]
                end += 1
            rows.append({"position": lines[i], "part_number_original": lines[i + 1], "part_name": description,
                         "quantity": lines[quantity_index].replace(",", "."), "unit": "шт", "german_name": german,
                         "reference_page": reference_pages[row_index] if reference_pages and row_index < len(reference_pages) else None})
            row_index += 1
            i = end
        return rows

    @staticmethod
    def _part_number(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9 ./-]{2,}", value)) and any(char.isdigit() for char in value)

    @staticmethod
    def _page_type(text: str, rows: list[dict[str, str]]) -> str:
        if rows:
            return "parts_list"
        if "По техническим причинам эта страница остается пустой" in text:
            return "drawing"
        if "Узел" in text:
            return "mixed"
        if "список запасных частей" in text or "Spare part report" in text:
            return "front_matter"
        return "unknown"
