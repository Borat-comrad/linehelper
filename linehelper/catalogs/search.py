"""Deterministic exact and FTS catalog search returning source-traceable records."""
from __future__ import annotations
import re
from .khs_etl_parser import normalize_part_number
from .models import CatalogPartResult
from .store import CatalogStore

_SELECT = "SELECT p.part_number_original,p.part_name,a.assembly_code,a.assembly_name,b.position,b.quantity,b.unit,e.model,c.machine_number,c.revision,b.source_page,b.reference_page"
_JOINS = " FROM bom_items b JOIN parts p ON p.part_id=b.part_id JOIN assemblies a ON a.assembly_id=b.assembly_id JOIN catalogs c ON c.catalog_id=a.catalog_id JOIN equipment e ON e.equipment_id=c.equipment_id"
_FTS_SCORE = "bm25(catalog_search_fts, 8.0, 7.0, 4.0, 2.0, 1.5, 1.0, 1.0, 1.0, 1.0, 0.0)"
class CatalogSearch:
    def __init__(self, store: CatalogStore): self.store=store
    @staticmethod
    def _result(row, score=None): return CatalogPartResult(row[0],row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8],row[9],row[10],row[11],score)
    def find_part_by_number(self, number: str):
        normalized=normalize_part_number(number)
        with self.store.connect() as con:
            rows=con.execute(_SELECT+_JOINS+" WHERE p.part_number_original=? OR p.part_number_normalized=? ORDER BY c.catalog_id,a.assembly_code,b.source_page,b.source_row_order",(number.strip(),normalized)).fetchall()
        return [self._result(r) for r in rows]
    def search_parts(self, query: str, limit: int=20):
        if limit <= 0:
            return []
        match_query = build_fts_match_query(query)
        if match_query is None:
            return []
        normalized = normalize_part_number(query)
        with self.store.connect() as con:
            rows=con.execute(_SELECT+f", {_FTS_SCORE}, CASE WHEN p.part_number_original=? OR p.part_number_normalized=? THEN 0 ELSE 1 END exact_code_rank"+_JOINS+f" JOIN catalog_search_fts ON catalog_search_fts.bom_item_id=b.bom_item_id WHERE catalog_search_fts MATCH ? ORDER BY exact_code_rank, {_FTS_SCORE}, p.part_number_original, a.assembly_code, b.source_page, b.source_row_order",(query.strip(),normalized,match_query)).fetchall()
        results = []
        seen_part_numbers = set()
        for row in rows:
            result = self._result(row,row[12])
            if result.part_number in seen_part_numbers:
                continue
            seen_part_numbers.add(result.part_number)
            results.append(result)
            if len(results) >= limit:
                break
        return results
    def find_assembly_parts(self, assembly_code: str):
        with self.store.connect() as con:
            rows=con.execute(_SELECT+_JOINS+" WHERE a.assembly_code=? ORDER BY b.source_page,b.source_row_order",(assembly_code,)).fetchall()
        return [self._result(r) for r in rows]


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
        and tokens[0].isascii()
        and tokens[0].isalnum()
        and any(char.isdigit() for char in tokens[0])
    ):
        return phrase + "*"
    return phrase
