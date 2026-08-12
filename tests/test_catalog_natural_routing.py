from __future__ import annotations

from linehelper.catalogs.chat import CatalogChatService
from linehelper.catalogs.models import CatalogPartResult
from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import QueryPlan
from linehelper.rag.retriever import RetrievedChunk
from linehelper.ui.streamlit_app import _should_render_rag_sources


def test_natural_equipment_phrases_use_catalog_probe():
    for question in (
        "что ты знаешь про прижимное устройство",
        "прижимное устройство",
        "найди прижимное устройство",
    ):
        search = ProbeSearch(strong_results=_catalog_results())
        generator = _generator(CatalogChatService(search=search))

        answer = generator.answer(question)

        assert answer.response_kind == "catalog_candidates"
        assert answer.query_plan["source_route"] == "catalog"
        assert answer.query_plan["catalog_probe_performed"] is True
        assert answer.query_plan["catalog_probe_result_count"] == 3
        assert answer.query_plan["catalog_match_type"] == "catalog_fts"
        assert answer.query_plan["corporate_evidence_available"] is False
        assert "58803877S002" in answer.answer
        assert search.fts_calls


def test_exact_code_route_is_unchanged_and_does_not_probe():
    search = ProbeSearch(exact_results=[_catalog_result("X44235100", "вал")])
    service = CatalogChatService(search=search)

    outcome = service.lookup("X44235100")

    assert outcome is not None
    assert outcome.status == "found"
    assert outcome.match_type == "catalog_exact"
    assert search.fts_calls == []


def test_corporate_query_is_not_intercepted_by_catalog_probe():
    search = ProbeSearch(strong_results=_catalog_results())
    catalog = CatalogChatService(search=search)

    probe = catalog.probe_natural_language("кто отвечает за документооборот")

    assert probe.performed is False
    assert probe.source_route == "corporate"
    assert probe.outcome is None
    assert search.fts_calls == []


def test_weak_random_fts_match_does_not_intercept_corporate_query():
    weak = [_catalog_result("301134230120", "шайба")]
    search = ProbeSearch(strong_results=weak)
    catalog = CatalogChatService(search=search)

    probe = catalog.probe_natural_language("шайба")

    assert probe.performed is True
    assert probe.result_count == 1
    assert probe.top_field_coverage == 1.0
    assert probe.source_route == "corporate"
    assert probe.outcome is None


def test_corporate_insufficient_with_strong_mixed_probe_returns_partial_mixed():
    catalog = CatalogChatService(search=ProbeSearch(strong_results=_catalog_results()))
    generator = _generator(
        catalog,
        retriever=StaticRetriever([]),
        analyzer=StaticAnalyzer(_plan("procedure", "procedure")),
    )

    answer = generator.answer("обслуживание прижимного устройства")

    assert answer.response_kind == "partial_mixed_answer"
    assert answer.query_plan["source_route"] == "mixed"
    assert answer.query_plan["catalog_probe_performed"] is True
    assert answer.query_plan["corporate_evidence_available"] is False
    assert answer.query_plan["catalog_requirement_status"] == "found"
    assert answer.query_plan["corporate_requirement_status"] == "not_found"
    assert answer.retrieval["corporate"] is not None
    assert "недостаточно данных" not in answer.answer.casefold()


def test_catalog_answer_has_structured_sources_and_no_empty_rag_counter():
    answer = _generator(
        CatalogChatService(search=ProbeSearch(strong_results=_catalog_results()))
    ).answer("что ты знаешь про прижимное устройство")

    assert len(answer.catalog_sources) == 3
    assert answer.catalog_sources[0].source_filename == "catalog.pdf"
    assert answer.catalog_sources[0].source_page == 67
    assert answer.sources == []
    assert not _should_render_rag_sources(answer)


def test_strong_mixed_probe_preserves_catalog_and_corporate_evidence():
    catalog = CatalogChatService(search=ProbeSearch(strong_results=_catalog_results()))
    corporate_chunk = _chunk(
        "Обслуживание прижимного устройства выполняют по утвержденной инструкции."
    )
    generator = _generator(
        catalog,
        retriever=StaticRetriever([corporate_chunk]),
        analyzer=StaticAnalyzer(_plan("procedure", "procedure")),
        client=StaticClient("Обслуживание выполняют по утвержденной инструкции. [1]"),
    )

    answer = generator.answer("обслуживание прижимного устройства")

    assert answer.response_kind == "mixed_answer"
    assert answer.query_plan["source_route"] == "mixed"
    assert answer.query_plan["corporate_evidence_available"] is True
    assert answer.catalog_sources
    assert answer.sources
    assert "Корпоративная база знаний" in answer.answer


def test_natural_subject_and_entity_context_are_extracted_deterministically():
    catalog = CatalogChatService(search=ContextSearch())

    contents = catalog.probe_natural_language(
        "какие детали входят в прижимное устройство"
    )
    contextual = catalog.probe_natural_language(
        "есть ли в каталоге вал для нижней части укупорщика"
    )

    assert contents.source_route == "catalog"
    assert contents.catalog_subject == "прижимное устройство"
    assert contents.outcome is not None
    assert contextual.source_route == "catalog"
    assert contextual.catalog_entity_terms == ("вал",)
    assert contextual.catalog_assembly_context == "нижней части укупорщика"
    assert [result.part_number for result in contextual.outcome.results] == [
        "X44235100"
    ]


def _generator(
    catalog,
    *,
    retriever=None,
    analyzer=None,
    client=None,
):
    return RagAnswerGenerator(
        retriever=retriever or FailRetriever(),
        llm_client=client or FailClient(),
        query_analyzer=analyzer,
        catalog_chat=catalog,
    )


def _catalog_results():
    return [
        _catalog_result("58803877S002", "прижимное устройство"),
        _catalog_result("58803842S058", "предохранитель прижимное устройство"),
        _catalog_result("20411617", "прижимное устройство"),
    ]


def _catalog_result(number, name):
    return CatalogPartResult(
        part_number=number,
        part_name=name,
        assembly_code="20411617",
        assembly_name="прижимное устройство",
        position="10",
        quantity="1.000",
        unit="шт",
        equipment_model="Innofill",
        machine_number="47592",
        revision="05",
        source_page=67,
        reference_page=68,
        search_score=-6.7,
        bom_item_id=1,
        catalog_id=1,
        source_filename="catalog.pdf",
        source_checksum="checksum",
        catalog_page_count=679,
    )


class ProbeSearch:
    def __init__(self, *, strong_results=None, exact_results=None):
        self.strong_results = list(strong_results or [])
        self.exact_results = list(exact_results or [])
        self.fts_calls = []

    def find_part_by_number(self, identifier):
        return self.exact_results

    def search_parts(self, query, limit=5):
        self.fts_calls.append((query, limit))
        return self.strong_results[:limit]

    def search_part_number_candidates(self, query, limit=5):
        raise AssertionError("natural-language routing must not use code candidates")


class ContextSearch(ProbeSearch):
    def __init__(self):
        super().__init__(strong_results=_catalog_results())

    def search_parts(self, query, limit=5):
        self.fts_calls.append((query, limit))
        if "нижней" in query:
            return [
                _context_result("X44235100", "вал"),
                _context_result("X44230956", "подшипник"),
            ][:limit]
        return self.strong_results[:limit]


def _context_result(number, name):
    result = _catalog_result(number, name)
    return CatalogPartResult(
        **{
            **result.__dict__,
            "assembly_code": "X44236986",
            "assembly_name": "нижняя часть укупорщика",
        }
    )


class FailRetriever:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("corporate retrieval must not run")


class StaticRetriever:
    def __init__(self, chunks):
        self.chunks = chunks

    def retrieve(self, *args, **kwargs):
        return self.chunks


class FailClient:
    model = "fake"

    def chat(self, messages):
        raise AssertionError("LLM must not run")


class StaticClient:
    model = "fake"

    def __init__(self, answer):
        self.answer = answer

    def chat(self, messages):
        return self.answer


class StaticAnalyzer:
    def __init__(self, plan):
        self.plan = plan

    def analyze(self, question):
        return self.plan


def _plan(intent, fact_type):
    return QueryPlan(
        intent=intent,
        normalized_question="обслуживание прижимного устройства",
        query_expansions=[],
        preferred_sources=[],
        answer_type="procedure",
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="test",
        requested_fact_type=fact_type,
        temporal_scope="static",
        subject="прижимное устройство",
    )


def _chunk(text):
    return RetrievedChunk(
        chunk_id=1,
        title="Инструкция обслуживания",
        source="data/raw_docs/service.pdf",
        section="Обслуживание",
        page=1,
        text=text,
        score=1.0,
        metadata={
            "logical_unit_title": "Обслуживание прижимного устройства",
            "logical_unit_type": "procedure",
            "doc_type": "instruction",
        },
        doc_type="instruction",
        base_score=1.0,
        rerank_score=2.0,
        final_score=100.0,
        matched_terms=["обслуживание", "прижимное", "устройство"],
        matched_excerpt=text,
        selection_reasons=["test"],
    )
