"""Data models for deterministic company structure parsing and indexing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SOURCE_VERSION = "2025-12-17"
KNOWLEDGE_DOMAIN = "organization_structure"

ALLOWED_UNIT_TYPES = frozenset(
    {
        "company",
        "council",
        "division",
        "department",
        "section",
        "sector",
        "group",
        "service",
        "office",
    }
)


@dataclass(frozen=True)
class OrganizationUnit:
    unit_id: str
    unit_type: str
    name: str
    number: str | None
    parent_unit_id: str | None
    head_name: str | None
    head_status: str
    ckp: str | None
    responsibilities: list[str]
    source_section: str


@dataclass(frozen=True)
class EmployeeRole:
    employee_name: str
    role: str
    unit_id: str | None
    unit_name: str | None
    responsibilities: list[str]
    phone: str | None
    phone_normalized: str | None
    email: str | None
    is_acting: bool
    is_vacant: bool
    source_section: str
    additional_phones: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResponsibilityRoute:
    topic: str
    primary_responsible: str
    primary_role: str | None
    unit_name: str | None
    phone: str | None
    email: str | None
    fallback_responsible: str | None
    fallback_phone: str | None
    notes: str | None
    source_section: str


@dataclass(frozen=True)
class OrganizationStatus:
    entity_name: str
    status: str
    details: str | None
    source_section: str


@dataclass(frozen=True)
class SemanticChunk:
    namespace: str
    doc_type: str
    title: str
    text: str
    source: str
    section: str
    metadata: dict[str, Any]


@dataclass
class ParseResult:
    source_path: Path
    source_version: str = SOURCE_VERSION
    units: list[OrganizationUnit] = field(default_factory=list)
    employee_roles: list[EmployeeRole] = field(default_factory=list)
    responsibility_routes: list[ResponsibilityRoute] = field(default_factory=list)
    vacancies: list[OrganizationStatus] = field(default_factory=list)
    inactive_entities: list[OrganizationStatus] = field(default_factory=list)
    role_combinations: list[OrganizationStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def phones_recognized(self) -> int:
        values = {
            role.phone_normalized or role.phone
            for role in self.employee_roles
            if role.phone or role.phone_normalized
        }
        for role in self.employee_roles:
            values.update(role.additional_phones)
        values.update(
            route.phone
            for route in self.responsibility_routes
            if route.phone
        )
        values.update(
            route.fallback_phone
            for route in self.responsibility_routes
            if route.fallback_phone
        )
        return len(values)

    @property
    def emails_recognized(self) -> int:
        return len({role.email for role in self.employee_roles if role.email})
