from __future__ import annotations

import pytest

from linehelper.catalogs.chat import (
    CatalogChatService,
    StructuredCatalogIntent,
    extract_structured_catalog_intent,
)
from linehelper.catalogs.khs_etl_parser import normalize_part_number
from linehelper.catalogs.search import CatalogSearch
from linehelper.catalogs.store import CatalogStore
from linehelper.llm.answer_generator import RagAnswerGenerator


@pytest.fixture()
def generator(tmp_path):
    store = CatalogStore(tmp_path / "catalog.db")
    store.import_staging(_staging())
    return RagAnswerGenerator(
        retriever=FailRetriever(),
        llm_client=FailClient(),
        catalog_chat=CatalogChatService(search=CatalogSearch(store)),
    )


@pytest.mark.parametrize(
    "question,expected_route",
    [
        ("Узел: 20411617", "catalog_assembly_exact"),
        ("что входит в узел 20411617", "catalog_assembly_contents"),
        ("состав узла 20411617", "catalog_assembly_contents"),
        ("позиция 20 узла 20411617", "catalog_bom_position"),
        ("деталь X44235100", "catalog_part_exact"),
        ("где используется X44235100", "catalog_part_exact"),
        ("20411617", "catalog_entity_ambiguity"),
    ],
)
def test_structured_catalog_intents_never_reach_corporate_rag(
    generator,
    question,
    expected_route,
):
    result = generator.answer(question)

    assert result.response_kind == expected_route
    assert result.query_plan["source_route"] == "catalog"
    assert result.query_plan["catalog_route"] == expected_route
    assert result.retrieval["retrieval_stages"] == [expected_route]
    assert result.sources == []


def test_assembly_contents_preserve_order_and_structured_facts(generator):
    result = generator.answer("состав узла 20411617")

    assert result.catalog["assemblies"][0]["assembly_name"] == "прижимное устройство"
    assert [item["position"] for item in result.catalog["results"]] == ["10", "20"]
    assert result.catalog["results"][1]["part_number"] == "58803877S002"
    assert "Количество по спецификации: 3 шт." in result.answer
    assert "Страница спецификации: 67" in result.answer


def test_position_is_scoped_to_assembly(generator):
    result = generator.answer("позиция 20 узла 20411617")

    assert result.catalog["position"] == "20"
    assert [item["part_number"] for item in result.catalog["results"]] == [
        "58803877S002"
    ]
    assert result.catalog["results"][0]["assembly_code"] == "20411617"


def test_bare_code_keeps_assembly_and_part_roles(generator):
    result = generator.answer("20411617")

    assert result.catalog["assembly_count"] == 1
    assert result.catalog["result_count"] == 1
    assert result.catalog["results"][0]["assembly_code"] == "10307585"
    assert "найдено несколько значений" in result.answer.casefold()
    assert "узел 20411617" in result.answer
    assert "деталь/подузел 20411617" in result.answer


@pytest.mark.parametrize(
    "question,expected",
    [
        ("узел 20411617", StructuredCatalogIntent("assembly_exact", "20411617")),
        ("покажи узел 20411617", StructuredCatalogIntent("assembly_exact", "20411617")),
        ("какие детали входят в узел 20411617", StructuredCatalogIntent("assembly_contents", "20411617")),
        ("покажи спецификацию узла 20411617", StructuredCatalogIntent("assembly_contents", "20411617")),
        ("что стоит в позиции 20 узла 20411617", StructuredCatalogIntent("bom_position", "20411617", "20")),
        ("узел 20411617 позиция 20", StructuredCatalogIntent("bom_position", "20411617", "20")),
        ("код детали X44235100", StructuredCatalogIntent("part_exact", "X44235100")),
        ("покажи X44235100", StructuredCatalogIntent("part_exact", "X44235100")),
    ],
)
def test_supported_structured_phrasings(question, expected):
    assert extract_structured_catalog_intent(question) == expected


class FailRetriever:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("structured catalog intent must not use corporate retrieval")


class FailClient:
    model = "fake"

    def chat(self, messages):
        raise AssertionError("structured catalog intent must not call the LLM")


def _staging():
    parts = (
        ("58803877S001", "прижимной элемент", "157.000", "10", 68),
        ("58803877S002", "прижимное устройство", "3.000", "20", 74),
        ("20411617", "прижимное устройство", "1.000", "300", 66),
        ("X44235100", "вал", "4.000", "70", None),
    )
    assemblies = (
        ("20411617", "прижимное устройство", 67),
        ("10307585", "машина", 9),
        ("X44236986", "нижняя часть укупорщика", 467),
    )
    bom_items = []
    for index, (number, name, quantity, position, reference) in enumerate(parts, 1):
        assembly_code = (
            "20411617"
            if index <= 2
            else "10307585"
            if number == "20411617"
            else "X44236986"
        )
        source_page = 67 if index <= 2 else 9 if number == "20411617" else 467
        bom_items.append(
            {
                "assembly_code": assembly_code,
                "part_number_original": number,
                "position": position,
                "quantity": quantity,
                "unit": "шт",
                "description": name,
                "child_assembly_code": "20411617" if number == "20411617" else None,
                "source_page": source_page,
                "reference_page": reference,
                "source_row_order": index,
                "metadata_json": {},
            }
        )
    return {
        "catalog": {
            "document_type": "spare_parts_catalog",
            "title": "Structured routing fixture",
            "source_filename": "catalog.pdf",
            "source_checksum": "structured-routing-fixture",
            "machine_number": "47592",
            "revision": "05",
            "revision_date": None,
            "language": "ru",
            "page_count": 500,
            "parser_version": "test",
        },
        "equipment": {
            "manufacturer": "KHS",
            "site": "test",
            "equipment_type": "Innofill",
            "model": "Innofill",
            "machine_number": "47592",
            "aliases_json": [],
        },
        "assemblies": [
            {
                "assembly_code": code,
                "assembly_name": name,
                "parent_assembly_code": None,
                "drawing_page": None,
                "parts_list_page": page,
                "source_page_start": page - 1,
                "source_page_end": page,
                "metadata_json": {},
            }
            for code, name, page in assemblies
        ],
        "parts": [
            {
                "part_number_original": number,
                "part_number_normalized": normalize_part_number(number),
                "part_name": name,
                "manufacturer": "KHS",
                "metadata_json": {},
            }
            for number, name, *_ in parts
        ],
        "bom_items": bom_items,
        "pages": [],
        "parser_warnings": [],
        "parser_errors": [],
    }
