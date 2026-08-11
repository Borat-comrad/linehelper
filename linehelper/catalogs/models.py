"""Small, serialisable models used by the KHS ETL catalog pilot."""

from __future__ import annotations

from dataclasses import asdict, dataclass


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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
