"""Small, serialisable models used by the KHS ETL catalog pilot."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class CatalogPartResult:
    part_number: str
    part_name: str | None
    assembly_code: str
    assembly_name: str | None
    position: str
    quantity: str | None
    unit: str | None
    equipment_model: str | None
    machine_number: str | None
    revision: str | None
    source_page: int
    reference_page: int | None
    search_score: float | None = None
    bom_item_id: int | None = None
    catalog_id: int | None = None
    source_filename: str | None = None
    source_checksum: str | None = None
    catalog_page_count: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CatalogAssemblyResult:
    """Exact assembly identity, separate from its ordered BOM occurrences."""

    assembly_code: str
    assembly_name: str | None
    equipment_model: str | None
    machine_number: str | None
    revision: str | None
    parts_list_page: int | None
    source_page_start: int
    source_page_end: int
    bom_item_count: int
    catalog_id: int
    source_filename: str
    source_checksum: str
    catalog_page_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


CatalogCodeMatchType = Literal[
    "catalog_prefix",
    "catalog_normalized_prefix",
    "catalog_substring",
]


@dataclass(frozen=True)
class CatalogCodeSearchResult:
    """Unique part-number candidates from one deterministic code stage."""

    match_type: CatalogCodeMatchType | None
    results: tuple[CatalogPartResult, ...] = ()


@dataclass(frozen=True)
class CatalogSource:
    """Structured catalog evidence kept separate from RAG chunk sources."""

    part_number: str
    assembly_code: str
    position: str
    source_page: int
    reference_page: int | None
    source_filename: str | None
    revision: str | None
    machine_number: str | None
    catalog_id: int | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SourceRequirement:
    """One independently satisfiable need in a cross-source question."""

    requirement_id: str
    subject: str
    source: Literal["catalog", "corporate"]
    status: Literal["pending", "found", "not_found"] = "pending"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CatalogProbeDiagnostics:
    """Conservative natural-language probe result used by source routing."""

    performed: bool
    query: str
    result_count: int
    top_score: float | None
    top_field_coverage: float
    coherent_result_count: int
    source_route: Literal["catalog", "corporate", "mixed"]
    outcome: object | None
    resolved_requirements: tuple[SourceRequirement, ...] = ()
    catalog_subject: str | None = None
    catalog_entity_terms: tuple[str, ...] = ()
    catalog_assembly_context: str | None = None
