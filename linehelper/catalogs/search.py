"""Deterministic exact, code-oriented and FTS catalog search."""

from __future__ import annotations

import re

from .khs_etl_parser import normalize_part_number
from .models import CatalogAssemblyResult, CatalogCodeSearchResult, CatalogPartResult
from .store import CatalogStore


MIN_CODE_PREFIX_LENGTH = 4
MIN_CODE_SUBSTRING_LENGTH = 6

_SELECT = (
    "SELECT p.part_number_original,p.part_name,a.assembly_code,a.assembly_name,"
    "b.position,b.quantity,b.unit,e.model,c.machine_number,c.revision,"
    "b.source_page,b.reference_page,b.bom_item_id,c.catalog_id,"
    "c.source_filename,c.source_checksum,c.page_count"
)
_JOINS = (
    " FROM bom_items b"
    " JOIN parts p ON p.part_id=b.part_id"
    " JOIN assemblies a ON a.assembly_id=b.assembly_id"
    " JOIN catalogs c ON c.catalog_id=a.catalog_id"
    " JOIN equipment e ON e.equipment_id=c.equipment_id"
)
_FTS_SCORE = (
    "bm25(catalog_search_fts, 8.0, 7.0, 4.0, 2.0, 1.5, 1.0, 1.0, 1.0, 1.0, 0.0)"
)
_CODE_QUERY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s._/-]{2,39}$")


class CatalogSearch:
    def __init__(self, store: CatalogStore):
        self.store = store

    @staticmethod
    def _result(row, score=None):
        return CatalogPartResult(
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11],
            score,
            row[12],
            row[13],
            row[14],
            row[15],
            row[16],
        )

    def find_part_by_number(self, number: str):
        normalized = normalize_part_number(number)
        with self.store.connect() as con:
            rows = con.execute(
                _SELECT
                + _JOINS
                + " WHERE p.part_number_original=? OR p.part_number_normalized=?"
                " ORDER BY c.catalog_id,a.assembly_code,b.source_page,b.source_row_order",
                (number.strip(), normalized),
            ).fetchall()
        return [self._result(row) for row in rows]

    def search_part_number_candidates(
        self,
        query: str,
        limit: int = 20,
    ) -> CatalogCodeSearchResult:
        """Search prefix stages first, then a guarded normalized substring."""
        clean = " ".join(query.strip().split())
        normalized = normalize_part_number(clean)
        if (
            limit <= 0
            or not is_part_number_like(clean)
            or len(normalized) < MIN_CODE_PREFIX_LENGTH
        ):
            return CatalogCodeSearchResult(match_type=None)

        with self.store.connect() as con:
            original_rows = con.execute(
                _SELECT
                + _JOINS
                + " WHERE substr(p.part_number_original,1,length(?))=?"
                " AND p.part_number_original<>?"
                " ORDER BY length(p.part_number_original)-length(?),"
                " p.part_number_original,c.catalog_id,a.assembly_code,"
                " b.source_page,b.source_row_order",
                (clean, clean, clean, clean),
            ).fetchall()
            original = self._unique_candidates(original_rows, limit)
            if original:
                return CatalogCodeSearchResult("catalog_prefix", original)

            normalized_rows = con.execute(
                _SELECT
                + _JOINS
                + " WHERE substr(p.part_number_normalized,1,length(?))=?"
                " AND p.part_number_normalized<>?"
                " ORDER BY length(p.part_number_normalized)-length(?),"
                " p.part_number_normalized,p.part_number_original,c.catalog_id,"
                " a.assembly_code,b.source_page,b.source_row_order",
                (normalized, normalized, normalized, normalized),
            ).fetchall()
            normalized_prefix = self._unique_candidates(normalized_rows, limit)
            if normalized_prefix:
                return CatalogCodeSearchResult(
                    "catalog_normalized_prefix",
                    normalized_prefix,
                )

            if len(normalized) < MIN_CODE_SUBSTRING_LENGTH:
                return CatalogCodeSearchResult(match_type=None)
            substring_rows = con.execute(
                _SELECT
                + _JOINS
                + " WHERE instr(p.part_number_normalized,?)>0"
                " AND p.part_number_normalized<>?"
                " ORDER BY instr(p.part_number_normalized,?),"
                " length(p.part_number_normalized)-length(?),"
                " p.part_number_normalized,p.part_number_original,c.catalog_id,"
                " a.assembly_code,b.source_page,b.source_row_order",
                (normalized, normalized, normalized, normalized),
            ).fetchall()
        substring = self._unique_candidates(substring_rows, limit)
        if substring:
            return CatalogCodeSearchResult("catalog_substring", substring)
        return CatalogCodeSearchResult(match_type=None)

    def search_parts(self, query: str, limit: int = 20):
        if limit <= 0:
            return []
        match_query = build_fts_match_query(query)
        if match_query is None:
            return []
        normalized = normalize_part_number(query)
        with self.store.connect() as con:
            rows = con.execute(
                _SELECT
                + f", {_FTS_SCORE}, CASE WHEN p.part_number_original=?"
                " OR p.part_number_normalized=? THEN 0 ELSE 1 END exact_code_rank"
                + _JOINS
                + " JOIN catalog_search_fts"
                " ON catalog_search_fts.bom_item_id=b.bom_item_id"
                " WHERE catalog_search_fts MATCH ?"
                f" ORDER BY exact_code_rank, {_FTS_SCORE},"
                " p.part_number_original,a.assembly_code,b.source_page,b.source_row_order",
                (query.strip(), normalized, match_query),
            ).fetchall()
        results = []
        seen_part_numbers = set()
        for row in rows:
            result = self._result(row, row[17])
            if result.part_number in seen_part_numbers:
                continue
            seen_part_numbers.add(result.part_number)
            results.append(result)
            if len(results) >= limit:
                break
        return results

    def find_assembly_parts(self, assembly_code: str):
        with self.store.connect() as con:
            rows = con.execute(
                _SELECT
                + _JOINS
                + " WHERE a.assembly_code=? COLLATE NOCASE"
                " ORDER BY b.source_page,b.source_row_order",
                (assembly_code,),
            ).fetchall()
        return [self._result(row) for row in rows]

    def find_assemblies_by_code(self, assembly_code: str):
        """Return exact assembly identities without treating a code as a part."""
        clean = assembly_code.strip()
        with self.store.connect() as con:
            rows = con.execute(
                "SELECT a.assembly_code,a.assembly_name,e.model,c.machine_number,"
                "c.revision,a.parts_list_page,a.source_page_start,a.source_page_end,"
                "(SELECT COUNT(*) FROM bom_items b WHERE b.assembly_id=a.assembly_id),"
                "c.catalog_id,c.source_filename,c.source_checksum,c.page_count"
                " FROM assemblies a"
                " JOIN catalogs c ON c.catalog_id=a.catalog_id"
                " JOIN equipment e ON e.equipment_id=c.equipment_id"
                " WHERE a.assembly_code=? COLLATE NOCASE"
                " ORDER BY c.catalog_id,a.assembly_id",
                (clean,),
            ).fetchall()
        return [CatalogAssemblyResult(*row) for row in rows]

    def find_assembly_position(self, assembly_code: str, position: str):
        """Return BOM rows for a position scoped to one exact assembly code."""
        with self.store.connect() as con:
            rows = con.execute(
                _SELECT
                + _JOINS
                + " WHERE a.assembly_code=? COLLATE NOCASE AND b.position=?"
                " ORDER BY c.catalog_id,b.source_page,b.source_row_order",
                (assembly_code.strip(), position.strip()),
            ).fetchall()
        return [self._result(row) for row in rows]

    def _unique_candidates(self, rows, limit: int) -> tuple[CatalogPartResult, ...]:
        results = []
        seen_part_numbers = set()
        for row in rows:
            result = self._result(row)
            if result.part_number in seen_part_numbers:
                continue
            seen_part_numbers.add(result.part_number)
            results.append(result)
            if len(results) >= limit:
                break
        return tuple(results)


def is_part_number_like(query: str) -> bool:
    """Recognize short technical-code forms without treating prose as a code."""
    clean = " ".join(query.strip().split())
    if not _CODE_QUERY.fullmatch(clean):
        return False
    normalized = normalize_part_number(clean)
    if len(normalized) < MIN_CODE_PREFIX_LENGTH or not any(
        char.isdigit() for char in normalized
    ):
        return False
    if normalized.isdecimal():
        return True

    digit_ratio = sum(char.isdigit() for char in normalized) / len(normalized)
    if digit_ratio < 0.4:
        return False
    separated_tokens = re.split(r"[\s._/-]+", clean)
    if any(token.isalpha() and len(token) >= 4 for token in separated_tokens):
        return False
    return any(char.isalpha() for char in normalized)


def build_fts_match_query(query: str) -> str | None:
    """Build a safe unicode61 phrase, with prefix matching for code-like tokens."""
    tokens = re.findall(r"[^\W_]+", query.replace("_", " "), re.UNICODE)
    if not tokens:
        return None
    escaped = " ".join(token.replace('"', '""') for token in tokens)
    phrase = f'"{escaped}"'
    if (
        len(tokens) == 1
        and len(tokens[0]) >= 4
        and tokens[0].isalnum()
    ):
        return phrase + "*"
    return phrase
