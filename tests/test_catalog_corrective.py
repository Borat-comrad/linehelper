from __future__ import annotations

from linehelper.catalogs.chat import CatalogChatService
from linehelper.catalogs.khs_etl_parser import normalize_part_number
from linehelper.catalogs.models import CatalogCodeSearchResult, CatalogPartResult
from linehelper.catalogs.search import CatalogSearch, is_part_number_like
from linehelper.catalogs.store import CatalogStore
from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.ui.streamlit_app import _should_render_rag_sources


def test_full_exact_code_stays_authoritative_without_candidate_or_fts():
    search = FakeSearch(exact_results=[_result("X44235100")], fail_on_candidates=True, fail_on_fts=True)

    outcome = CatalogChatService(search=search).lookup("Найди X44235100")

    assert outcome is not None
    assert outcome.status == "found"
    assert outcome.match_type == "catalog_exact"
    assert search.exact_calls == ["X44235100"]
    assert search.candidate_calls == []
    assert search.fts_calls == []


def test_code_like_heuristic_accepts_codes_and_rejects_natural_language():
    for value in (
        "X44236",
        "X44235100",
        "20411616",
        "0-529-90-026-7",
        "0529900267",
        "HTD1600-8M-50",
        "X 44236",
    ):
        assert is_part_number_like(value), value
    for value in (
        "верхняя часть наполнителя",
        "прижимное устройство",
        "вал нижней части укупорщика",
        "найди вал на укупорщике",
        "позиция 70 нижней части укупорщика",
    ):
        assert not is_part_number_like(value), value


def test_prefix_search_returns_only_real_code_prefixes(tmp_path):
    search = _catalog_search(tmp_path)

    result = search.search_part_number_candidates("X44236", limit=5)

    assert result.match_type == "catalog_prefix"
    assert [item.part_number for item in result.results] == [
        "X442360",
        "X44236123",
        "X44236986",
    ]
    assert "301134230120" not in {item.part_number for item in result.results}


def test_normalized_prefix_handles_case_hyphen_and_space(tmp_path):
    search = _catalog_search(tmp_path)

    for query in ("x44236", "X-44236", "X 44236"):
        result = search.search_part_number_candidates(query, limit=5)
        assert result.match_type == "catalog_normalized_prefix"
        assert [item.part_number for item in result.results] == [
            "X442360",
            "X44236123",
            "X44236986",
        ]


def test_long_substring_is_allowed_but_short_fragment_is_guarded(tmp_path):
    search = _catalog_search(tmp_path)

    substring = search.search_part_number_candidates("442351", limit=5)
    short = search.search_part_number_candidates("123", limit=5)

    assert substring.match_type == "catalog_substring"
    assert [item.part_number for item in substring.results] == [
        "X44235100",
        "X44235103",
        "X44235105",
    ]
    assert short.match_type is None
    assert short.results == ()


def test_public_chat_routes_long_numeric_fragment_but_not_short_fragment(tmp_path):
    service = CatalogChatService(search=_catalog_search(tmp_path))

    long_fragment = service.lookup("442351")
    short_fragment = service.lookup("44")

    assert long_fragment is not None
    assert long_fragment.match_type == "catalog_substring"
    assert long_fragment.results[0].part_number == "X44235100"
    assert short_fragment is None


def test_natural_text_query_remains_fts_only():
    search = FakeSearch(fts_results=[_result("NAME-1", name="прижимное устройство")])

    outcome = CatalogChatService(search=search).lookup(
        "Найди в каталоге прижимное устройство"
    )

    assert outcome is not None
    assert outcome.route == "catalog_text_search"
    assert outcome.match_type == "catalog_fts"
    assert search.exact_calls == []
    assert search.candidate_calls == []
    assert search.fts_calls == [("прижимное устройство", 5)]


def test_exact_result_has_catalog_sources_and_hides_empty_rag_counter():
    generator = RagAnswerGenerator(
        retriever=FailRetriever(),
        llm_client=FailClient(),
        catalog_chat=CatalogChatService(
            search=FakeSearch(exact_results=[_result("X44235100")])
        ),
    )

    answer = generator.answer("Найди X44235100")

    assert len(answer.catalog_sources) == 1
    source = answer.catalog_sources[0]
    assert source.source_filename == "catalog.pdf"
    assert source.source_page == 467
    assert source.revision == "05"
    assert source.machine_number == "47592"
    assert answer.catalog["catalog_sources"][0]["assembly_code"] == "X44236986"
    assert not _should_render_rag_sources(answer)


def test_partial_code_miss_uses_clearly_labeled_fts_fallback():
    search = FakeSearch(
        code_result=CatalogCodeSearchResult(match_type=None),
        fts_results=[_result("301134230120", name="подкладная шайба")],
    )

    outcome = CatalogChatService(search=search).lookup("Найди в каталоге X99999")

    assert outcome is not None
    assert outcome.route == "catalog_code_search"
    assert outcome.match_type == "catalog_fts"
    assert "Точных или частичных совпадений" in outcome.answer
    assert "кандидатов полнотекстового поиска" in outcome.answer
    assert search.exact_calls == ["X99999"]
    assert search.candidate_calls == [("X99999", 5)]
    assert search.fts_calls == [("X99999", 5)]


def _catalog_search(tmp_path):
    store = CatalogStore(tmp_path / "catalog.db")
    store.import_staging(_staging())
    return CatalogSearch(store)


def _staging():
    part_numbers = [
        ("X442360", "короткое продолжение"),
        ("X44236123", "лексикографически первый вариант"),
        ("X44236986", "нижняя часть укупорщика"),
        ("X44235100", "вал"),
        ("X44235103", "установочное гнездо"),
        ("X44235105", "зубчатое колесо"),
        ("301134230120", "подкладная шайба"),
    ]
    return {
        "catalog": {
            "document_type": "spare_parts_catalog",
            "title": "Corrective fixture",
            "source_filename": "catalog.pdf",
            "source_checksum": "corrective-fixture",
            "machine_number": "47592",
            "revision": "05",
            "revision_date": None,
            "language": "ru",
            "page_count": 1,
            "parser_version": "test",
        },
        "equipment": {
            "manufacturer": "KHS",
            "site": "test",
            "equipment_type": "Innofill",
            "model": "M",
            "machine_number": "47592",
            "aliases_json": [],
        },
        "assemblies": [
            {
                "assembly_code": "X44236986",
                "assembly_name": "нижняя часть укупорщика",
                "parent_assembly_code": None,
                "drawing_page": None,
                "parts_list_page": 1,
                "source_page_start": 1,
                "source_page_end": 1,
                "metadata_json": {},
            }
        ],
        "parts": [
            {
                "part_number_original": number,
                "part_number_normalized": normalize_part_number(number),
                "part_name": name,
                "manufacturer": "KHS",
                "metadata_json": {},
            }
            for number, name in part_numbers
        ],
        "bom_items": [
            {
                "assembly_code": "X44236986",
                "part_number_original": number,
                "position": str(index * 10),
                "quantity": "1.000",
                "unit": "шт",
                "description": name,
                "child_assembly_code": None,
                "source_page": 1,
                "reference_page": None,
                "source_row_order": index,
                "metadata_json": {},
            }
            for index, (number, name) in enumerate(part_numbers, 1)
        ],
        "pages": [
            {
                "page_number": 1,
                "page_type": "parts_list",
                "assembly_code": "X44236986",
                "text_quality": "good",
                "metadata_json": {},
            }
        ],
        "parser_warnings": [],
        "parser_errors": [],
    }


def _result(number, name="вал"):
    return CatalogPartResult(
        part_number=number,
        part_name=name,
        assembly_code="X44236986",
        assembly_name="нижняя часть укупорщика",
        position="70",
        quantity="4.000",
        unit="шт",
        equipment_model="Innofill",
        machine_number="47592",
        revision="05",
        source_page=467,
        reference_page=None,
        bom_item_id=1,
        catalog_id=1,
        source_filename="catalog.pdf",
        source_checksum="checksum",
        catalog_page_count=679,
    )


class FakeSearch:
    def __init__(
        self,
        *,
        exact_results=None,
        code_result=None,
        fts_results=None,
        fail_on_candidates=False,
        fail_on_fts=False,
    ):
        self.exact_results = list(exact_results or [])
        self.code_result = code_result or CatalogCodeSearchResult(match_type=None)
        self.fts_results = list(fts_results or [])
        self.fail_on_candidates = fail_on_candidates
        self.fail_on_fts = fail_on_fts
        self.exact_calls = []
        self.candidate_calls = []
        self.fts_calls = []

    def find_part_by_number(self, identifier):
        self.exact_calls.append(identifier)
        return self.exact_results

    def search_part_number_candidates(self, query, limit=5):
        if self.fail_on_candidates:
            raise AssertionError("candidate search must not run")
        self.candidate_calls.append((query, limit))
        return self.code_result

    def search_parts(self, query, limit=5):
        if self.fail_on_fts:
            raise AssertionError("FTS must not run")
        self.fts_calls.append((query, limit))
        return self.fts_results


class FailRetriever:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("RAG must not run")


class FailClient:
    model = "fake"

    def chat(self, messages):
        raise AssertionError("LLM must not run")
