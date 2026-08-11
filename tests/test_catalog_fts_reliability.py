from __future__ import annotations

import pytest

from linehelper.catalogs.indexing import rebuild_search_index
from linehelper.catalogs.chat import CatalogChatService
from linehelper.catalogs.search import CatalogSearch, build_fts_match_query
from linehelper.catalogs.store import CatalogStore


@pytest.fixture
def catalog_search(tmp_path):
    store = CatalogStore(tmp_path / "catalog.db")
    store.import_staging(_staging())
    rebuild_search_index(store)
    return CatalogSearch(store)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("20411616", "20411616"),
        ("58831836S001", "58831836S001"),
        ("X56767951", "X56767951"),
        ("XFH 20093", "XFH 20093"),
        ("XFH20093", "XFH 20093"),
    ],
)
def test_full_part_numbers_rank_first(catalog_search, query, expected):
    assert catalog_search.search_parts(query, limit=5)[0].part_number == expected


def test_previous_assembly_collision_is_ranked_as_part_candidate(catalog_search):
    results = catalog_search.search_parts("58831836S001", limit=5)

    assert results[0].part_number == "58831836S001"
    assert sum(result.part_number == "58831836S001" for result in results) == 1


def test_code_fragment_and_name_search_remain_full_text(catalog_search):
    assert catalog_search.search_parts("X5676", limit=5)[0].part_number == "X56767951"
    assert catalog_search.search_parts("servo actuator", limit=5)[0].part_number == "NAME-1"
    assert catalog_search.search_parts("definitely_absent_term", limit=5) == []


def test_multiple_occurrences_are_candidates_in_fts_but_preserved_by_exact(catalog_search):
    fts_results = catalog_search.search_parts("58831836S001", limit=20)
    exact_results = catalog_search.find_part_by_number("58831836S001")

    assert [result.part_number for result in fts_results].count("58831836S001") == 1
    assert len(exact_results) == 2
    assert {result.assembly_code for result in exact_results} == {"ROOT", "OTHER"}


def test_match_query_is_safe_and_separates_tokenization_from_normalization():
    assert build_fts_match_query("588318") == '"588318"*'
    assert build_fts_match_query("XFH 20093") == '"XFH 20093"'
    assert build_fts_match_query("servo_actuator") == '"servo actuator"'
    assert build_fts_match_query('" OR *') == '"OR"'
    assert build_fts_match_query("___") is None


def test_exact_chat_path_does_not_fall_back_to_fts():
    class ExactOnlySearch:
        def find_part_by_number(self, part_number):
            assert part_number == "20411616"
            return []

        def search_parts(self, query, limit=20):
            raise AssertionError("chat exact route must not invoke FTS")

    outcome = CatalogChatService(search=ExactOnlySearch()).lookup("Найди 20411616")

    assert outcome is not None
    assert outcome.status == "not_found"


def _staging():
    assemblies = [
        _assembly("ROOT", 1),
        _assembly("58831836S001", 2),
        _assembly("OTHER", 3),
    ]
    parts = [
        _part("58831836S001", "регулятор давления"),
        _part("20411616", "numeric part"),
        _part("X56767951", "x prefix part"),
        _part("XFH 20093", "spaced part"),
        _part("NAME-1", "servo actuator valve"),
    ] + [_part(f"CHILD{i}", f"collision child {i}") for i in range(6)]
    bom_items = [
        _bom("ROOT", "58831836S001", "10", 1, 1),
        _bom("ROOT", "20411616", "20", 1, 2),
        _bom("ROOT", "X56767951", "30", 1, 3),
        _bom("ROOT", "XFH 20093", "40", 1, 4),
        _bom("ROOT", "NAME-1", "50", 1, 5),
        _bom("OTHER", "58831836S001", "60", 3, 1),
    ] + [
        _bom("58831836S001", f"CHILD{i}", str(100 + i), 2, i + 1)
        for i in range(6)
    ]
    return {
        "catalog": {
            "document_type": "spare_parts_catalog",
            "title": "FTS fixture",
            "source_filename": "fts.pdf",
            "source_checksum": "fts-reliability",
            "machine_number": "1",
            "revision": "05",
            "revision_date": None,
            "language": "ru",
            "page_count": 3,
            "parser_version": "test",
        },
        "equipment": {
            "manufacturer": "KHS",
            "site": "test",
            "equipment_type": "Innofill",
            "model": "M",
            "machine_number": "1",
            "aliases_json": [],
        },
        "assemblies": assemblies,
        "parts": parts,
        "bom_items": bom_items,
        "pages": [
            {
                "page_number": page,
                "page_type": "parts_list",
                "assembly_code": code,
                "text_quality": "good",
                "metadata_json": {},
            }
            for page, code in [(1, "ROOT"), (2, "58831836S001"), (3, "OTHER")]
        ],
        "parser_warnings": [],
        "parser_errors": [],
    }


def _assembly(code, page):
    return {
        "assembly_code": code,
        "assembly_name": f"Assembly {code}",
        "parent_assembly_code": None,
        "drawing_page": None,
        "parts_list_page": page,
        "source_page_start": page,
        "source_page_end": page,
        "metadata_json": {},
    }


def _part(number, name):
    from linehelper.catalogs.khs_etl_parser import normalize_part_number

    return {
        "part_number_original": number,
        "part_number_normalized": normalize_part_number(number),
        "part_name": name,
        "manufacturer": "KHS",
        "metadata_json": {},
    }


def _bom(assembly, part, position, page, order):
    return {
        "assembly_code": assembly,
        "part_number_original": part,
        "position": position,
        "quantity": "1.000",
        "unit": "шт",
        "description": None,
        "child_assembly_code": None,
        "source_page": page,
        "reference_page": None,
        "source_row_order": order,
        "metadata_json": {},
    }
