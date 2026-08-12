from __future__ import annotations

from decimal import Decimal

import pytest

from linehelper.catalogs.chat import format_catalog_answer, format_natural_catalog_candidates
from linehelper.catalogs.models import CatalogPartResult
from linehelper.catalogs.presentation import format_catalog_quantity


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.000", "1"),
        ("3.000", "3"),
        ("157.000", "157"),
        (Decimal("1.500"), "1,5"),
        (0.250, "0,25"),
    ],
)
def test_format_catalog_quantity_for_russian_ui(value, expected):
    assert format_catalog_quantity(value) == expected


def test_catalog_answers_use_specification_terminology_and_quantity():
    result = _result(quantity="3.000")

    for answer in (
        format_catalog_answer((result,)),
        format_natural_catalog_candidates("прижимное устройство", (result,)),
    ):
        assert (
            "Позиция: 20 · Количество по спецификации: 3 шт. · "
            "Страница спецификации: 67"
        ) in answer
        assert "Страница BOM" not in answer
        assert "Количество: 3.000" not in answer


def test_fractional_quantity_is_not_rounded_in_catalog_answer():
    answer = format_catalog_answer((_result(quantity="1.500"),))

    assert "Количество по спецификации: 1,5 шт." in answer


def _result(*, quantity):
    return CatalogPartResult(
        part_number="58803877S002",
        part_name="прижимное устройство",
        assembly_code="20411617",
        assembly_name="прижимное устройство",
        position="20",
        quantity=quantity,
        unit="шт",
        equipment_model="Innofill",
        machine_number="47592",
        revision="05",
        source_page=67,
        reference_page=74,
    )
