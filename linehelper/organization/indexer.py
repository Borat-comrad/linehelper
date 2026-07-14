"""Build and import semantic organization chunks into MemoryStore."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re

from linehelper.memory.memory_store import MemoryStore
from linehelper.organization.models import (
    KNOWLEDGE_DOMAIN,
    SOURCE_VERSION,
    EmployeeRole,
    OrganizationStatus,
    OrganizationUnit,
    ParseResult,
    ResponsibilityRoute,
    SemanticChunk,
)
from linehelper.organization.parser import parse_company_structure


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_PATH = PROJECT_ROOT / "data" / "raw_docs" / "bvr_company_structure_instruction_v2 (2).txt"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
NAMESPACE = "semantic"


@dataclass
class OrganizationImportReport:
    source_file: Path
    source_version: str
    units_indexed: int
    employees_indexed: int
    routes_indexed: int
    vacancies_indexed: int
    inactive_entities_indexed: int
    role_combinations_indexed: int
    phones_recognized: int
    emails_recognized: int
    total_chunks_written: int
    warnings: list[str] = field(default_factory=list)
    chunks_by_type: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    examples: list[SemanticChunk] = field(default_factory=list)


def build_semantic_chunks(
    parsed: ParseResult,
    *,
    exclude_contacts: bool = False,
) -> list[SemanticChunk]:
    """Build semantic chunks from parsed organization data."""
    source = _relative_source(parsed.source_path)
    source_file = parsed.source_path.name
    children = _children_by_parent(parsed.units)
    chunks: list[SemanticChunk] = []

    chunks.append(_overview_chunk(parsed, source=source, source_file=source_file))
    for unit in parsed.units:
        if unit.unit_type == "company":
            continue
        chunks.append(
            _unit_chunk(
                unit,
                children=children.get(unit.unit_id, []),
                source=source,
                source_file=source_file,
            )
        )

    combinations_by_employee = {
        combination.entity_name: combination
        for combination in parsed.role_combinations
    }
    for role in parsed.employee_roles:
        chunks.append(
            _employee_chunk(
                role,
                combination=combinations_by_employee.get(role.employee_name),
                source=source,
                source_file=source_file,
                exclude_contacts=exclude_contacts,
            )
        )

    for route in parsed.responsibility_routes:
        chunks.append(
            _route_chunk(
                route,
                source=source,
                source_file=source_file,
                exclude_contacts=exclude_contacts,
            )
        )

    for vacancy in parsed.vacancies:
        chunks.append(_status_chunk(vacancy, "organization_vacancy", source, source_file))
    for inactive in parsed.inactive_entities:
        chunks.append(_status_chunk(inactive, "organization_status", source, source_file))
    for combination in parsed.role_combinations:
        chunks.append(_status_chunk(combination, "role_combination", source, source_file))

    return chunks


def import_company_structure(
    *,
    source_path: str | Path = DEFAULT_SOURCE_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    replace_version: bool = True,
    dry_run: bool = False,
    exclude_contacts: bool = False,
) -> OrganizationImportReport:
    """Parse, chunk, and optionally write organization knowledge to MemoryStore."""
    parsed = parse_company_structure(source_path)
    chunks = build_semantic_chunks(parsed, exclude_contacts=exclude_contacts)
    chunks_by_type = dict(Counter(chunk.doc_type for chunk in chunks))
    written = 0

    if not dry_run:
        store = MemoryStore(str(db_path))
        store.ensure_schema()
        if replace_version:
            store.delete_chunks_by_metadata(
                namespace=NAMESPACE,
                metadata_filters={
                    "knowledge_domain": KNOWLEDGE_DOMAIN,
                    "source_file": Path(source_path).name,
                    "source_version": SOURCE_VERSION,
                },
            )
        for chunk in chunks:
            store.add_chunk(
                namespace=chunk.namespace,
                doc_type=chunk.doc_type,
                title=chunk.title,
                text=chunk.text,
                source=chunk.source,
                section=chunk.section,
                metadata=chunk.metadata,
            )
            written += 1

    return OrganizationImportReport(
        source_file=Path(source_path),
        source_version=SOURCE_VERSION,
        units_indexed=sum(1 for chunk in chunks if chunk.doc_type == "organization_unit"),
        employees_indexed=sum(1 for chunk in chunks if chunk.doc_type == "employee_role"),
        routes_indexed=sum(1 for chunk in chunks if chunk.doc_type == "responsibility_route"),
        vacancies_indexed=sum(1 for chunk in chunks if chunk.doc_type == "organization_vacancy"),
        inactive_entities_indexed=sum(1 for chunk in chunks if chunk.doc_type == "organization_status"),
        role_combinations_indexed=sum(1 for chunk in chunks if chunk.doc_type == "role_combination"),
        phones_recognized=parsed.phones_recognized,
        emails_recognized=parsed.emails_recognized,
        total_chunks_written=written if not dry_run else 0,
        warnings=parsed.warnings,
        chunks_by_type=chunks_by_type,
        dry_run=dry_run,
        examples=chunks[:5],
    )


def _overview_chunk(parsed: ParseResult, *, source: str, source_file: str) -> SemanticChunk:
    text = """Компания Serviceline включает следующие основные отделения:
1 — административное;
2 — коммерческое;
3А — бухгалтерия;
3Б — финансы;
4А — закупки;
4Б — логистика;
4В — техническое;
5 — квалификация;
6 — развитие.

Поисковые формулировки: Какие подразделения есть в компании? Какие отделения есть в компании? структура компании Serviceline."""
    metadata = _base_metadata(
        source_file=source_file,
        record_key=f"organization_overview:serviceline:{SOURCE_VERSION}",
        contains_contact_data=False,
        entity_type="organization_overview",
        organization="Serviceline",
    )
    return SemanticChunk(
        namespace=NAMESPACE,
        doc_type="organization_overview",
        title="Оргструктура Serviceline",
        text=text,
        source=source,
        section="Документ — обзор организации",
        metadata=metadata,
    )


def _unit_chunk(
    unit: OrganizationUnit,
    *,
    children: list[OrganizationUnit],
    source: str,
    source_file: str,
) -> SemanticChunk:
    lines = [f"{_unit_label(unit)}."]
    if unit.head_status == "vacant":
        lines.append("Руководитель: должность вакантна, назначенный исполнитель отсутствует.")
    elif unit.head_name:
        lines.append(f"Руководитель: {unit.head_name}.")

    if children:
        lines.append("В состав входят:")
        for child in children:
            child_number = f" {child.number}" if child.number else ""
            lines.append(f"- {child.unit_type}{child_number} — {child.name};")

    if unit.ckp:
        lines.append(f"ЦКП: {unit.ckp}.")
    if unit.responsibilities:
        lines.append("Основные направления:")
        lines.extend(f"- {item};" for item in unit.responsibilities[:12])
    aliases = _aliases_for(unit.name, unit.number, unit.head_name, " ".join(unit.responsibilities))
    if unit.unit_id == "division_4b":
        aliases = f"{aliases} Какие подразделения входят в логистику? структура логистики".strip()
    if aliases:
        lines.append(f"Поисковые алиасы: {aliases}.")

    metadata = _base_metadata(
        source_file=source_file,
        record_key=f"organization_unit:{unit.unit_id}:{SOURCE_VERSION}",
        contains_contact_data=False,
        entity_type="organization_unit",
        unit_id=unit.unit_id,
        unit_type=unit.unit_type,
        unit_number=unit.number,
        unit_name=unit.name,
        parent_unit_id=unit.parent_unit_id,
        head_name=unit.head_name,
        status="vacant" if unit.head_status == "vacant" else "active",
    )
    return SemanticChunk(
        namespace=NAMESPACE,
        doc_type="organization_unit",
        title=_unit_label(unit),
        text="\n\n".join(lines),
        source=source,
        section=unit.source_section,
        metadata=metadata,
    )


def _employee_chunk(
    role: EmployeeRole,
    *,
    combination: OrganizationStatus | None,
    source: str,
    source_file: str,
    exclude_contacts: bool,
) -> SemanticChunk:
    lines = [f"{role.employee_name}.", "", "Должности:", f"- {role.role or 'роль указана в справочнике сотрудников'}."]
    if role.unit_name:
        lines.append(f"Подразделение: {role.unit_name}.")
    if role.responsibilities:
        lines.append("Зоны ответственности:")
        lines.extend(f"- {item};" for item in role.responsibilities)
    if combination and combination.details:
        lines.append(f"Совмещение ролей: {combination.details}.")
    if not exclude_contacts:
        if role.phone:
            lines.append(f"Рабочий телефон: {role.phone}.")
        for additional_phone in role.additional_phones:
            lines.append(f"Дополнительный рабочий телефон: {additional_phone}.")
        if role.email:
            lines.append(f"Email: {role.email}.")
        if not role.phone and not role.email:
            lines.append("Контактные данные в источнике не указаны.")
    else:
        lines.append("Контактные данные исключены из обезличенного импорта.")
    aliases = _aliases_for(role.role, role.unit_name, role.employee_name, " ".join(role.responsibilities))
    if aliases:
        lines.append(f"Поисковые алиасы: {aliases}.")

    contains_contact = bool(role.phone or role.email) and not exclude_contacts
    metadata = _base_metadata(
        source_file=source_file,
        record_key=f"employee:{_slug(role.employee_name)}:{SOURCE_VERSION}",
        contains_contact_data=contains_contact,
        entity_type="employee",
        employee_name=role.employee_name,
        unit_ids=[role.unit_id] if role.unit_id else [],
        phone=None if exclude_contacts else role.phone,
        phone_normalized=None if exclude_contacts else role.phone_normalized,
        additional_phones=[] if exclude_contacts else role.additional_phones,
        email=None if exclude_contacts else role.email,
        has_multiple_roles=bool(combination) or len(role.responsibilities) > 1,
    )
    return SemanticChunk(
        namespace=NAMESPACE,
        doc_type="employee_role",
        title=role.employee_name,
        text="\n".join(lines),
        source=source,
        section=role.source_section,
        metadata=metadata,
    )


def _route_chunk(
    route: ResponsibilityRoute,
    *,
    source: str,
    source_file: str,
    exclude_contacts: bool,
) -> SemanticChunk:
    lines = [
        f"По вопросам {route.topic} следует обращаться к {route.primary_responsible}"
        + (f", {route.primary_role}." if route.primary_role else ".")
    ]
    if route.unit_name:
        lines.append(f"Подразделение: {route.unit_name}.")
    if not exclude_contacts:
        if route.phone:
            lines.append(f"Телефон: {route.phone}.")
        if route.email:
            lines.append(f"Email: {route.email}.")
        if not route.phone and not route.email:
            lines.append("Ответственный сотрудник определён, но контактные данные в источнике не указаны.")
    else:
        lines.append("Контактные данные исключены из обезличенного импорта.")
    if route.fallback_responsible:
        lines.append(
            f"Резервный или вышестоящий ответственный: {route.fallback_responsible}"
            + (f", телефон {route.fallback_phone}." if route.fallback_phone and not exclude_contacts else ".")
        )
    aliases = " ".join(
        part for part in (
            _aliases_for(_route_detail(route.topic), route.unit_name, route.primary_responsible),
            _route_question_aliases(route.topic),
        )
        if part
    )
    lines.append(f"Связанные запросы: {aliases}.")

    contains_contact = bool(route.phone or route.email or route.fallback_phone) and not exclude_contacts
    metadata = _base_metadata(
        source_file=source_file,
        record_key=f"responsibility_route:{_slug(route.topic)}:{SOURCE_VERSION}",
        contains_contact_data=contains_contact,
        entity_type="responsibility_route",
        topic=route.topic,
        primary_responsible=route.primary_responsible,
        phone=None if exclude_contacts else route.phone,
        email=None if exclude_contacts else route.email,
        fallback_responsible=route.fallback_responsible,
        fallback_phone=None if exclude_contacts else route.fallback_phone,
    )
    return SemanticChunk(
        namespace=NAMESPACE,
        doc_type="responsibility_route",
        title=f"Маршрут: {route.topic}",
        text="\n\n".join(lines),
        source=source,
        section=route.source_section,
        metadata=metadata,
    )


def _status_chunk(
    status: OrganizationStatus,
    doc_type: str,
    source: str,
    source_file: str,
) -> SemanticChunk:
    if doc_type == "organization_vacancy":
        text = (
            f"Должность {status.entity_name} является вакантной по состоянию на версию "
            "организационной структуры от 17.12.2025.\n\n"
            "Назначенный исполнитель отсутствует.\n\n"
            "Поисковые запросы: Какие должности вакантны? вакантные должности вакансии незаполненные должности."
        )
    elif doc_type == "role_combination":
        text = (
            f"{status.entity_name} совмещает следующие роли: {status.details}.\n\n"
            f"Поисковые запросы: Какие должности совмещает {status.entity_name}? совмещения Хилько Юлия."
        )
    else:
        text = (
            f"{status.entity_name}: статус {status.status}."
            + (f"\n\nДетали: {status.details}." if status.details else "")
            + "\n\nПоисковые запросы: Активен ли исполнительный совет? неактивные органы управления."
        )

    metadata = _base_metadata(
        source_file=source_file,
        record_key=f"{doc_type}:{_slug(status.entity_name)}:{SOURCE_VERSION}",
        contains_contact_data=False,
        entity_type=doc_type,
        entity_name=status.entity_name,
        status=status.status,
    )
    return SemanticChunk(
        namespace=NAMESPACE,
        doc_type=doc_type,
        title=status.entity_name,
        text=text,
        source=source,
        section=status.source_section,
        metadata=metadata,
    )


def _base_metadata(*, source_file: str, record_key: str, contains_contact_data: bool, **extra: Any) -> dict[str, Any]:
    metadata = {
        "knowledge_domain": KNOWLEDGE_DOMAIN,
        "source_file": source_file,
        "source_version": SOURCE_VERSION,
        "record_key": record_key,
        "contains_contact_data": contains_contact_data,
    }
    metadata.update(extra)
    return metadata


def _children_by_parent(units: list[OrganizationUnit]) -> dict[str, list[OrganizationUnit]]:
    children: dict[str, list[OrganizationUnit]] = defaultdict(list)
    for unit in units:
        if unit.parent_unit_id:
            children[unit.parent_unit_id].append(unit)
    return children


def _unit_label(unit: OrganizationUnit) -> str:
    number = f" {unit.number}" if unit.number else ""
    label = {
        "division": "Отделение",
        "department": "Отдел",
        "section": "Секция",
        "sector": "Сектор",
        "group": "Группа",
        "service": "Служба",
        "council": "Совет",
        "office": "Орган управления",
    }.get(unit.unit_type, unit.unit_type)
    return f"{label}{number} — {unit.name}"


def _aliases_for(*values: str | None) -> str:
    text = " ".join(value or "" for value in values).lower()
    aliases: list[str] = []
    if "закуп" in text or "imeta" in text or "krones" in text:
        aliases.extend(
            [
                "закупки, закуп, поставщики, заказы, руководитель закупок",
                "закупки IMETA, закупки KRONES, KHS, HEUFT, SIDEL, SMI",
            ]
        )
        if "руководитель" in text or "силаева" in text:
            aliases.append("Кто руководит закупками? Дай контакт руководителя закупок.")
    if "логист" in text:
        aliases.extend(
            [
                "логистика, доставка, таможня, растаможка",
                "Кто отвечает за логистику?",
            ]
        )
    if "тамож" in text:
        aliases.append(
            "таможня, таможенное оформление, таможенным оформлением, растаможка, таможенные платежи, "
            "Кто занимается таможенным оформлением? Какой телефон у ответственного за таможню?"
        )
    if "подготов" in text and "зиновкин" in text:
        aliases.append("Кто руководит отделом подготовки?")
    if ("склад" in text or "хранен" in text or "инвентар" in text) and "складских остатков" not in text:
        aliases.append("склад, хранение, комплектация, инвентаризация, Кто занимается складом?")
        aliases.append("Кто отвечает за склад и какой у него номер?")
    if "доставка клиент" in text:
        aliases.append("доставка клиенту, отгрузка клиенту, Кто отвечает за доставку клиенту?")
    elif "отгруз" in text:
        aliases.append("отгрузка, комплектование отправки клиенту")
    if "бухгалтер" in text or "оплат" in text or "налог" in text or "банк" in text:
        aliases.append("бухгалтерия, счета, оплаты, документы, налоги, Куда направить вопрос по оплатам?")
    if "кадр" in text or "персонал" in text or "найм" in text:
        aliases.append("кадры, персонал, найм, трудовые документы, кадровый учет, кадровый учёт")
        if "кадров" in text or "прокошина" in text:
            aliases.append("Куда обратиться по кадровому учёту? Как связаться с ответственным за кадровый учёт?")
    if "проектир" in text or "моделир" in text:
        aliases.append("технический отдел, проектирование, моделирование, Кто отвечает за проектирование?")
        aliases.append("К кому обратиться по техническому проектированию?")
    if "сервис" in text:
        aliases.append("сервисные проекты, сервисное обслуживание, Кто отвечает за сервисные проекты?")
    if "эфес" in text or "efes" in text:
        aliases.append("ЭФЕС, AB InBev Efes, продажи ЭФЕС.")
    if "бев" in text or "beveridge" in text:
        aliases.append("БЕВ-Ж, Beveridge, продажи Беверидж")
    if "ол-п" in text or "олимп" in text:
        aliases.append("ОЛ-П, Олимп, продажи Олимп")
    if "хилько" in text:
        aliases.append("Какие должности совмещает Хилько Юлия? совмещения Хилько")
    return " ".join(dict.fromkeys(aliases))


def _route_question_aliases(topic: str) -> str:
    upper = topic.upper()
    detail_upper = _route_detail(topic).upper()
    aliases: list[str] = []
    if "IMETA" in upper:
        aliases.append("Кому написать по закупкам IMETA?")
    if "KRONES" in upper:
        aliases.append("Кто отвечает за закупки KRONES?")
    if "ЭФЕС" in upper or "EFES" in upper:
        aliases.append("К кому обратиться по продажам ЭФЕС? Кто занимается продажами ЭФЕС? Дай контакт по продажам ЭФЕС.")
    if "ТАМОЖ" in upper:
        aliases.append("Кто занимается таможенным оформлением? Какой телефон у ответственного за таможню?")
    if "КАДРОВ" in upper:
        aliases.append("Куда обратиться по кадровому учёту? Как связаться с ответственным за кадровый учёт?")
    if "СКЛАД" in detail_upper:
        aliases.append("Кто занимается складом? Кто отвечает за склад и какой у него номер?")
    if "ДОСТАВКА КЛИЕНТУ" in detail_upper:
        aliases.append("Кто отвечает за доставку клиенту?")
    if "ПРОЕКТИРОВАНИЕ" in detail_upper:
        aliases.append("Кто отвечает за проектирование? К кому обратиться по техническому проектированию?")
    if "СЕРВИС" in detail_upper:
        aliases.append("Кто отвечает за сервисные проекты?")
    if "ФИНАНС" in detail_upper or "ОПЛАТ" in detail_upper:
        aliases.append("Куда направить вопрос по оплатам?")
    return " ".join(aliases)


def _route_detail(topic: str) -> str:
    return topic.split(":", 1)[1].strip() if ":" in topic else topic


def _relative_source(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _slug(value: str | None) -> str:
    if not value:
        return "unknown"
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    lowered = value.lower()
    converted = "".join(translit.get(char, char) for char in lowered)
    converted = re.sub(r"[^a-z0-9]+", "_", converted)
    return converted.strip("_") or "unknown"
