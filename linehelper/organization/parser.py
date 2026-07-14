"""Deterministic TXT parser for the Serviceline organization structure."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from linehelper.organization.models import (
    OrganizationStatus,
    OrganizationUnit,
    ParseResult,
    ResponsibilityRoute,
    SOURCE_VERSION,
    EmployeeRole,
)


PHONE_RE = re.compile(r"(?:\+7|8)[\s()/-]*\d{3}[\s()/-]*\d{3}[\s()/-]*\d{2}[\s()/-]*\d{2}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECTION_RE = re.compile(r"^РАЗДЕЛ\s+(\d+)\.\s+(.+)$")
SUBSECTION_RE = re.compile(r"^(\d+\.\d+)\s+(.+)$")


def parse_company_structure(source_path: str | Path) -> ParseResult:
    """Parse a company structure TXT file into structured objects."""
    path = Path(source_path)
    raw_text = path.read_text(encoding="utf-8")
    raw_text = _join_broken_emails(raw_text)
    lines = raw_text.splitlines()
    result = ParseResult(source_path=path, source_version=SOURCE_VERSION)

    sections = _top_sections(lines)
    result.units.extend(_parse_units(lines, sections, result.warnings))
    result.employee_roles.extend(_parse_employee_directory(lines, sections, result.warnings))
    employee_lookup = {role.employee_name: role for role in result.employee_roles}
    result.responsibility_routes.extend(
        _parse_contact_matrix(lines, sections, employee_lookup, result.warnings)
    )
    vacancies, inactive, combinations = _parse_status_notes(lines, sections, employee_lookup)
    result.vacancies.extend(vacancies)
    result.inactive_entities.extend(inactive)
    result.role_combinations.extend(combinations)
    _warn_suspicious_contacts(raw_text, result.warnings)

    return result


def normalize_phone(raw_value: str) -> tuple[str | None, str | None, str | None]:
    """Return (display, normalized, warning) for one phone-like value."""
    raw = " ".join(raw_value.split()).strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        normalized = "+7" + digits[1:]
    elif len(digits) == 11 and digits.startswith("7"):
        normalized = "+" + digits
    else:
        return raw or None, None, f"Suspicious phone kept as-is: {raw!r}"

    return format_phone(normalized), normalized, None


def format_phone(normalized: str) -> str:
    """Format a normalized +7XXXXXXXXXX phone for humans."""
    digits = re.sub(r"\D", "", normalized)
    return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"


def normalize_email(raw_value: str) -> tuple[str | None, str | None]:
    """Normalize one email or return warning while preserving uncertainty."""
    raw = raw_value.strip()
    compact = re.sub(r"\s+", "", raw).lower()
    if EMAIL_RE.fullmatch(compact):
        return compact, None
    if "@" in raw:
        return raw, f"Suspicious email kept as-is: {raw!r}"
    return None, None


def _join_broken_emails(text: str) -> str:
    return re.sub(
        r"([A-Za-z0-9._%+-]+)\s*\n\s*([A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        r"\1\2",
        text,
    )


def _top_sections(lines: list[str]) -> dict[int, tuple[int, int, str]]:
    starts: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line.strip())
        if match:
            starts.append((int(match.group(1)), index, match.group(2).strip()))

    sections: dict[int, tuple[int, int, str]] = {}
    for pos, (number, start, title) in enumerate(starts):
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(lines)
        sections[number] = (start, end, title)
    return sections


def _parse_units(
    lines: list[str],
    sections: dict[int, tuple[int, int, str]],
    warnings: list[str],
) -> list[OrganizationUnit]:
    units = [
        OrganizationUnit(
            unit_id="company",
            unit_type="company",
            name="Serviceline",
            number=None,
            parent_unit_id=None,
            head_name=None,
            head_status="active",
            ckp=_company_ckp(lines),
            responsibilities=[],
            source_section="Документ — ЦКП компании",
        )
    ]

    for section_number, (start, end, title) in sections.items():
        if 2 <= section_number <= 10:
            block = lines[start:end]
            division = _division_unit(section_number, title, block, warnings)
            units.append(division)
            units.extend(_child_units(block, section_number, division.unit_id, warnings))
        elif section_number == 1:
            units.extend(_leadership_units(lines[start:end], warnings))
    return units


def _company_ckp(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if line.strip() == "ЦКП КОМПАНИИ:":
            values: list[str] = []
            for value in lines[index + 1 :]:
                if not value.strip():
                    break
                values.append(value.strip())
            return " ".join(values)
    return None


def _division_unit(
    section_number: int,
    title: str,
    block: list[str],
    warnings: list[str],
) -> OrganizationUnit:
    number, name = _parse_numbered_name(title, default_number=str(section_number))
    head_name, head_status = _extract_head(block, warnings)
    return OrganizationUnit(
        unit_id=f"division_{_id_part(number)}",
        unit_type="division",
        name=_clean_unit_name(name),
        number=number,
        parent_unit_id="company",
        head_name=head_name,
        head_status=head_status,
        ckp=_extract_ckp(block),
        responsibilities=_extract_responsibilities(block),
        source_section=f"Раздел {section_number} — {title}",
    )


def _child_units(
    block: list[str],
    section_number: int,
    parent_unit_id: str,
    warnings: list[str],
) -> list[OrganizationUnit]:
    units: list[OrganizationUnit] = []
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(block):
        match = SUBSECTION_RE.match(line.strip())
        if match:
            starts.append((index, match.group(1), match.group(2).strip()))

    for pos, (start, subsection_number, title) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(block)
        subblock = block[start:end]
        unit_type = _unit_type_from_title(title)
        number, name = _parse_numbered_name(title, default_number=subsection_number)
        unit_id = f"{unit_type}_{_id_part(number)}"
        head_name, head_status = _extract_head(subblock, warnings)
        units.append(
            OrganizationUnit(
                unit_id=unit_id,
                unit_type=unit_type,
                name=_clean_unit_name(name),
                number=number,
                parent_unit_id=parent_unit_id,
                head_name=head_name,
                head_status=head_status,
                ckp=_extract_ckp(subblock),
                responsibilities=_extract_responsibilities(subblock),
                source_section=f"Раздел {subsection_number} — {title}",
            )
        )
        units.extend(_nested_units(subblock, unit_id, subsection_number))
    return units


def _leadership_units(block: list[str], warnings: list[str]) -> list[OrganizationUnit]:
    units: list[OrganizationUnit] = []
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(block):
        match = SUBSECTION_RE.match(line.strip())
        if match:
            starts.append((index, match.group(1), match.group(2).strip()))
    for pos, (start, number, title) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(block)
        subblock = block[start:end]
        unit_type = "council" if "СОВЕТ" in title.upper() else "office"
        head_name, head_status = _extract_head(subblock, warnings)
        units.append(
            OrganizationUnit(
                unit_id=f"{unit_type}_{_id_part(title)}",
                unit_type=unit_type,
                name=_clean_unit_name(title),
                number=number,
                parent_unit_id="company",
                head_name=head_name,
                head_status=head_status,
                ckp=_extract_ckp(subblock),
                responsibilities=_extract_responsibilities(subblock),
                source_section=f"Раздел {number} — {title}",
            )
        )
    return units


def _nested_units(block: list[str], parent_unit_id: str, source_number: str) -> list[OrganizationUnit]:
    units: list[OrganizationUnit] = []
    seen: set[str] = set()
    for line in block:
        stripped = line.strip()
        match = re.match(r"^(СЕКЦИЯ|СЕКТОР|ГРУППА|СЛУЖБА)\s+(.+?)(?::|→|$)", stripped, re.I)
        if not match:
            match = re.match(r"^-?\s*(Секция|Сектор|Служба)\s+(.+?)(?:→|$)", stripped)
        if not match:
            continue
        type_word = match.group(1).lower()
        unit_type = {
            "секция": "section",
            "сектор": "sector",
            "группа": "group",
            "служба": "service",
        }.get(type_word, "section")
        name = _clean_unit_name(match.group(2))
        if not name or name.lower().startswith("и ответственные"):
            continue
        unit_id = f"{unit_type}_{_id_part(parent_unit_id + '_' + name)}"
        if unit_id in seen:
            continue
        seen.add(unit_id)
        units.append(
            OrganizationUnit(
                unit_id=unit_id,
                unit_type=unit_type,
                name=name,
                number=None,
                parent_unit_id=parent_unit_id,
                head_name=None,
                head_status="active",
                ckp=None,
                responsibilities=[],
                source_section=f"Раздел {source_number} — {name}",
            )
        )
    return units


def _extract_head(block: list[str], warnings: list[str]) -> tuple[str | None, str]:
    for line in block:
        stripped = line.strip()
        if not re.match(r"^(Председатель|Сопредседатель|Руководитель|Начальник|И\.О\.|Зам\.)", stripped):
            continue
        if ":" not in stripped:
            continue
        label, value = stripped.split(":", 1)
        if "ВАКАНСИЯ" in value.upper():
            return None, "vacant"
        name, _, _, extra_warnings = _parse_name_and_contact(value)
        warnings.extend(extra_warnings)
        status = "acting" if "И.О." in label.upper() else "active"
        return name, status
    return None, "active"


def _extract_ckp(block: list[str]) -> str | None:
    for index, line in enumerate(block):
        stripped = line.strip()
        if not stripped.startswith("ЦКП:"):
            continue
        values = [stripped.removeprefix("ЦКП:").strip()]
        for value in block[index + 1 :]:
            if not value.strip():
                break
            next_stripped = value.strip()
            if re.match(r"^(Секции|Менеджеры|Персонал|Ответственности|СТАТУС|Начальник|Руководитель)", next_stripped):
                break
            if next_stripped.startswith("-") or "→" in next_stripped:
                break
            values.append(next_stripped)
        return " ".join(part for part in values if part)
    return None


def _extract_responsibilities(block: list[str]) -> list[str]:
    responsibilities: list[str] = []
    pending_topic: str | None = None
    for line in block:
        stripped = line.strip()
        if not stripped:
            pending_topic = None
            continue
        if "→" in stripped:
            left, right = stripped.split("→", 1)
            topic = _clean_bullet(left)
            responsible = " ".join(right.split())
            if topic:
                responsibilities.append(f"{topic}: {responsible}")
            pending_topic = None
            continue
        if stripped.startswith("-") and ":" in stripped:
            pending_topic = _clean_bullet(stripped).rstrip(":")
            continue
        if pending_topic and stripped.startswith("·") and "→" in stripped:
            left, right = stripped.split("→", 1)
            responsibilities.append(f"{pending_topic} / {_clean_bullet(left)}: {' '.join(right.split())}")
    return responsibilities


def _parse_employee_directory(
    lines: list[str],
    sections: dict[int, tuple[int, int, str]],
    warnings: list[str],
) -> list[EmployeeRole]:
    start, end, title = sections[11]
    roles: list[EmployeeRole] = []
    index = start + 1
    while index < end:
        line = lines[index]
        entry = re.match(r"^ {2}(\S[^:\n]*?)(?:\s*\(([^)]*)\))?\s*$", line)
        if not entry or entry.group(1).strip().startswith(("Роль", "Отдел")):
            index += 1
            continue

        name = entry.group(1).strip()
        contact = entry.group(2)
        role_lines: list[str] = []
        unit_name: str | None = None
        index += 1
        while index < end:
            current = lines[index]
            next_entry = re.match(r"^ {2}(\S[^:\n]*?)(?:\s*\(([^)]*)\))?\s*$", current)
            if next_entry and not next_entry.group(1).strip().startswith(("Роль", "Отдел")):
                break
            stripped = current.strip()
            if stripped.startswith("Роль:"):
                role_lines.append(stripped.removeprefix("Роль:").strip())
            elif stripped.startswith("Отдел:"):
                unit_name = stripped.removeprefix("Отдел:").strip()
            elif role_lines and stripped:
                role_lines.append(stripped)
            index += 1

        phone, phone_normalized, email, extra_warnings = _contact_from_value(contact or "")
        warnings.extend(extra_warnings)
        additional_phones = _additional_phones(contact or "", phone)
        role_text = " ".join(role_lines).strip()
        responsibilities = _split_responsibilities(role_text)
        roles.append(
            EmployeeRole(
                employee_name=name,
                role=role_text,
                unit_id=_unit_id_from_unit_name(unit_name),
                unit_name=unit_name,
                responsibilities=responsibilities,
                phone=phone,
                phone_normalized=phone_normalized,
                email=email,
                is_acting=_contains_acting(role_text),
                is_vacant=False,
                source_section=f"Раздел 11 — {title}",
                additional_phones=additional_phones,
            )
        )
    return roles


def _parse_contact_matrix(
    lines: list[str],
    sections: dict[int, tuple[int, int, str]],
    employee_lookup: dict[str, EmployeeRole],
    warnings: list[str],
) -> list[ResponsibilityRoute]:
    start, end, title = sections[12]
    routes: list[ResponsibilityRoute] = []
    current_category: str | None = None
    for line in lines[start + 1 : end]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and "→" not in stripped:
            current_category = stripped.rstrip(":")
            continue
        if "→" not in stripped:
            match = re.match(r"^([А-ЯЁA-Z][^+]+?)\s+((?:\+7|8).+)$", stripped)
            if match and current_category:
                topic = current_category.title()
                person_value = f"{match.group(1).strip()} ({match.group(2).strip()})"
            else:
                continue
        else:
            topic, person_value = (part.strip() for part in stripped.split("→", 1))
            if current_category:
                topic = f"{current_category}: {topic}"

        name, phone, email, extra_warnings = _parse_name_and_contact(person_value)
        warnings.extend(extra_warnings)
        role = employee_lookup.get(name)
        fallback_name, fallback_phone = _fallback_for_route(topic, employee_lookup)
        routes.append(
            ResponsibilityRoute(
                topic=topic,
                primary_responsible=name,
                primary_role=role.role if role else None,
                unit_name=role.unit_name if role else None,
                phone=phone or (role.phone if role else None),
                email=email or (role.email if role else None),
                fallback_responsible=fallback_name if fallback_name != name else None,
                fallback_phone=fallback_phone if fallback_name != name else None,
                notes="Маршрут извлечён из контактной матрицы.",
                source_section=f"Раздел 12 — {title}",
            )
        )
    return routes


def _parse_status_notes(
    lines: list[str],
    sections: dict[int, tuple[int, int, str]],
    employee_lookup: dict[str, EmployeeRole],
) -> tuple[list[OrganizationStatus], list[OrganizationStatus], list[OrganizationStatus]]:
    start, end, _ = sections[13]
    vacancies: list[OrganizationStatus] = []
    inactive: list[OrganizationStatus] = []
    combinations: list[OrganizationStatus] = []
    mode: str | None = None
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped.startswith("13.1"):
            mode = "vacancy"
            continue
        if stripped.startswith("13.2"):
            mode = "inactive"
            continue
        if stripped.startswith("13.3"):
            mode = "combination"
            continue
        if mode == "vacancy" and stripped.startswith("-"):
            entity_name = _clean_bullet(stripped)
            if not entity_name:
                continue
            vacancies.append(
                OrganizationStatus(
                    entity_name=entity_name,
                    status="vacant",
                    details="Назначенный исполнитель отсутствует.",
                    source_section="Раздел 13.1 — Незаполненные должности",
                )
            )
        elif mode == "inactive" and stripped.startswith("-"):
            name, _, details = stripped.partition("—")
            entity_name = _clean_bullet(name)
            if not entity_name:
                continue
            status = "inactive" if "НЕ АКТИВЕН" in stripped.upper() else "unstaffed"
            inactive.append(
                OrganizationStatus(
                    entity_name=entity_name,
                    status=status,
                    details=details.strip() or None,
                    source_section="Раздел 13.2 — Неактивные органы управления",
                )
            )
        elif mode == "combination" and "=" in stripped and not stripped.startswith("="):
            short_name, _, details = stripped.partition("=")
            full_name = _expand_short_name(short_name.strip(), employee_lookup)
            combinations.append(
                OrganizationStatus(
                    entity_name=full_name,
                    status="multiple_roles",
                    details=details.strip(),
                    source_section="Раздел 13.3 — Ключевые совмещения",
                )
            )
    return vacancies, inactive, combinations


def _parse_name_and_contact(value: str) -> tuple[str, str | None, str | None, list[str]]:
    warnings: list[str] = []
    first_value = value.split("/")[0].strip() if " / " in value else value.strip()
    phone, phone_normalized, email, contact_warnings = _contact_from_value(value)
    warnings.extend(contact_warnings)
    name = PHONE_RE.sub("", first_value)
    name = re.sub(r"\([^)]*@[^)]*\)", "", name)
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\s+", " ", name).strip(" ,-")
    return name, phone, email, warnings


def _contact_from_value(value: str) -> tuple[str | None, str | None, str | None, list[str]]:
    warnings: list[str] = []
    phone: str | None = None
    phone_normalized: str | None = None
    phone_match = PHONE_RE.search(value)
    if phone_match:
        phone, phone_normalized, warning = normalize_phone(phone_match.group(0))
        if warning:
            warnings.append(warning)

    email: str | None = None
    email_match = EMAIL_RE.search(value)
    if email_match:
        email, warning = normalize_email(email_match.group(0))
        if warning:
            warnings.append(warning)

    for suspicious in re.findall(r"\(([^)]*@[^)]*)\)", value):
        if not EMAIL_RE.fullmatch(re.sub(r"\s+", "", suspicious)):
            warnings.append(f"Suspicious email kept as-is: {suspicious!r}")

    return phone, phone_normalized, email, warnings


def _additional_phones(value: str, primary_phone: str | None) -> list[str]:
    phones: list[str] = []
    for match in PHONE_RE.finditer(value):
        phone, _, warning = normalize_phone(match.group(0))
        if warning or not phone or phone == primary_phone or phone in phones:
            continue
        phones.append(phone)
    return phones


def _warn_suspicious_contacts(text: str, warnings: list[str]) -> None:
    for value in re.findall(r"\(([^)]*@[^)]*)\)", text):
        compact = re.sub(r"\s+", "", value)
        if not EMAIL_RE.fullmatch(compact):
            warning = f"Suspicious email kept as-is: {value!r}"
            if warning not in warnings:
                warnings.append(warning)


def _parse_numbered_name(title: str, default_number: str) -> tuple[str, str]:
    title = title.strip()
    title = re.sub(r"^(ОТДЕЛЕНИЕ|ОТДЕЛ ПРОДАЖ|ОТДЕЛ|ГРУППА)\s+", "", title, flags=re.I)
    if "—" in title:
        number, name = (part.strip() for part in title.split("—", 1))
        return number, name
    return default_number, title


def _unit_type_from_title(title: str) -> str:
    upper = title.upper()
    if "ГРУППА" in upper:
        return "group"
    if "СЛУЖБ" in upper:
        return "service"
    if "СЕКЦ" in upper:
        return "section"
    if "СЕКТОР" in upper:
        return "sector"
    return "department"


def _clean_unit_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" :-")
    main, sep, suffix = value.partition("(")
    if main.strip().isupper():
        return f"{main.title().strip()} {sep}{suffix}".strip()
    return value


def _clean_bullet(value: str) -> str:
    return value.strip().lstrip("-·").strip()


def _split_responsibilities(role_text: str) -> list[str]:
    if not role_text:
        return []
    parts = re.split(r",|;", role_text)
    return [part.strip() for part in parts if part.strip()]


def _contains_acting(value: str) -> bool:
    upper = value.upper()
    return "И.О." in upper or "И.О" in upper


def _unit_id_from_unit_name(unit_name: str | None) -> str | None:
    if not unit_name:
        return None
    first = re.split(r"/|,", unit_name)[0].strip()
    if first in {"Топ", "Совет", "Адм.", "Адм"}:
        return None
    if first.endswith(("А", "Б", "В")) or re.fullmatch(r"\d+(?:\.\d+)?", first):
        prefix = "division" if first in {"1", "2", "3А", "3Б", "4А", "4Б", "4В", "5", "6"} else "department"
        return f"{prefix}_{_id_part(first)}"
    return None


def _fallback_for_route(
    topic: str,
    employee_lookup: dict[str, EmployeeRole],
) -> tuple[str | None, str | None]:
    upper = topic.upper()
    fallback_name: str | None = None
    if any(value in upper for value in ("ЛОГИСТИКА", "СКЛАД", "ТАМОЖ", "ДОСТАВ")):
        fallback_name = "Кистень Ольга"
    elif "ЗАКУПК" in upper or "IMETA" in upper or "KRONES" in upper:
        fallback_name = "Силаева Юлия"
    elif "ПРОДАЖ" in upper or "ЭФЕС" in upper or "БЕВ" in upper or "ОЛ-П" in upper:
        fallback_name = "Овсянникова Екатерина"
    elif "БУХГАЛТ" in upper or "ОПЛАТ" in upper or "НАЛОГ" in upper:
        fallback_name = "Томникова Дарья"
    elif "ТЕХНИЧ" in upper or "ПРОЕКТИР" in upper or "СЕРВИС" in upper:
        fallback_name = "Жиленков Павел"
    elif "ПЕРСОНАЛ" in upper or "КАДР" in upper or "НАЙМ" in upper:
        fallback_name = "Машоха Татьяна"

    if not fallback_name:
        return None, None
    role = employee_lookup.get(fallback_name)
    return fallback_name, role.phone if role else None


def _expand_short_name(short_name: str, employee_lookup: dict[str, EmployeeRole]) -> str:
    compact = short_name.replace(".", "").strip()
    parts = compact.split()
    if len(parts) < 2:
        return short_name
    surname = parts[0]
    initials = "".join(part[:1] for part in parts[1:])
    for name in employee_lookup:
        name_parts = name.split()
        if not name_parts or name_parts[0] != surname:
            continue
        if "".join(part[:1] for part in name_parts[1:]).startswith(initials):
            return name
    return short_name


def _id_part(value: str | None) -> str:
    if not value:
        return "unknown"
    translit = {
        "А": "a",
        "Б": "b",
        "В": "v",
        "а": "a",
        "б": "b",
        "в": "v",
    }
    value = "".join(translit.get(char, char) for char in value)
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def contact_counts(items: Iterable[EmployeeRole | ResponsibilityRoute]) -> tuple[int, int]:
    phones: set[str] = set()
    emails: set[str] = set()
    for item in items:
        phone = getattr(item, "phone", None)
        if phone:
            phones.add(phone)
        email = getattr(item, "email", None)
        if email:
            emails.add(email)
    return len(phones), len(emails)
