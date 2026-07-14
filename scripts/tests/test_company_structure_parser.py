from __future__ import annotations

from pathlib import Path

from linehelper.organization.parser import (
    normalize_email,
    normalize_phone,
    parse_company_structure,
)


SOURCE = Path("data/raw_docs/bvr_company_structure_instruction_v2 (2).txt")


def test_parser_finds_top_level_divisions():
    result = parse_company_structure(SOURCE)
    divisions = {unit.number: unit.name for unit in result.units if unit.unit_type == "division"}

    assert {"1", "2", "3А", "3Б", "4А", "4Б", "4В", "5", "6"} <= set(divisions)


def test_parser_extracts_division_4a_and_head():
    result = parse_company_structure(SOURCE)
    division = next(unit for unit in result.units if unit.unit_id == "division_4a")

    assert division.name == "Закупки"
    assert division.head_name == "Силаева Юлия"


def test_parser_extracts_department_10b_and_head():
    result = parse_company_structure(SOURCE)
    department = next(unit for unit in result.units if unit.unit_id == "department_10b")

    assert "Подготовки" in department.name
    assert department.head_name == "Зиновкин Роман"


def test_parser_extracts_employee_with_one_role():
    result = parse_company_structure(SOURCE)
    employee = next(role for role in result.employee_roles if role.employee_name == "Амосова Екатерина")

    assert employee.role == "Менеджер продаж ЭФЕС"
    assert employee.email == "ekaterinaamosova@serviceline.company"


def test_parser_extracts_employee_with_multiple_roles():
    result = parse_company_structure(SOURCE)
    employee = next(role for role in result.employee_roles if role.employee_name == "Хилько Юлия Александровна")

    assert "Исполнительный директор" in employee.responsibilities
    assert "финансовый директор" in employee.responsibilities


def test_parser_extracts_and_normalizes_phone():
    result = parse_company_structure(SOURCE)
    employee = next(role for role in result.employee_roles if role.employee_name == "Зиновкин Роман")

    assert employee.phone == "+7 910 298-12-38"
    assert employee.phone_normalized == "+79102981238"


def test_phone_normalization_converts_russian_eight_prefix():
    display, normalized, warning = normalize_phone("8 910 298 12 38")

    assert display == "+7 910 298-12-38"
    assert normalized == "+79102981238"
    assert warning is None


def test_email_normalization_joins_line_break():
    email, warning = normalize_email("Person.Name@\nServiceline.Company")

    assert email == "person.name@serviceline.company"
    assert warning is None


def test_parser_extracts_vacant_position():
    result = parse_company_structure(SOURCE)

    assert any("Зам. директора по коммерции" in vacancy.entity_name for vacancy in result.vacancies)


def test_parser_extracts_inactive_entity():
    result = parse_company_structure(SOURCE)

    assert any(
        entity.entity_name == "Исполнительный совет (Совет директоров)"
        and entity.status == "inactive"
        for entity in result.inactive_entities
    )


def test_parser_extracts_contact_route():
    result = parse_company_structure(SOURCE)
    route = next(route for route in result.responsibility_routes if "Таможня" in route.topic)

    assert route.primary_responsible == "Зиновкин Роман"
    assert route.phone == "+7 910 298-12-38"


def test_parser_handles_extra_spaces_in_normalizers():
    display, normalized, warning = normalize_phone(" +7   910   298  12 38 ")

    assert display == "+7 910 298-12-38"
    assert normalized == "+79102981238"
    assert warning is None


def test_parser_preserves_cyrillic():
    result = parse_company_structure(SOURCE)

    assert any(role.employee_name == "Прокошина Елена Дмитриевна" for role in result.employee_roles)


def test_parser_warns_for_suspicious_contact():
    result = parse_company_structure(SOURCE)

    assert any("Suspicious email" in warning for warning in result.warnings)


def test_parser_does_not_invent_missing_contact():
    result = parse_company_structure(SOURCE)
    employee = next(role for role in result.employee_roles if role.employee_name == "Ронис Вячеслав")

    assert employee.phone is None
    assert employee.email is None


def test_parser_extracts_role_combination():
    result = parse_company_structure(SOURCE)

    assert any(item.entity_name == "Зиновкин Роман" for item in result.role_combinations)
