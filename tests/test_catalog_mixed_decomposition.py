from __future__ import annotations

from linehelper.catalogs.chat import CatalogChatService
from linehelper.catalogs.models import CatalogPartResult
from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import QueryPlan
from linehelper.rag.retriever import RetrievedChunk
from linehelper.ui.streamlit_app import _should_render_rag_sources


COMPOUND_QUERY = "что есть по прижимному устройству и как его обслуживать"


def test_compound_catalog_and_procedure_query_returns_partial_mixed_answer():
    search = MixedSearch(results=_catalog_results())
    answer = _generator(
        search=search,
        retriever=StaticRetriever([]),
    ).answer(COMPOUND_QUERY)

    assert answer.response_kind == "partial_mixed_answer"
    assert answer.query_plan["source_route"] == "mixed"
    assert answer.query_plan["catalog_requirement_status"] == "found"
    assert answer.query_plan["corporate_requirement_status"] == "not_found"
    assert answer.query_plan["answer_mode"] == "partial_mixed"
    assert search.fts_calls[0][0] == "прижимному устройству"
    assert answer.query_plan["resolved_requirements"] == [
        {
            "requirement_id": "catalog_entity_information",
            "subject": "прижимное устройство",
            "source": "catalog",
            "status": "found",
        },
        {
            "requirement_id": "maintenance_procedure",
            "subject": "прижимное устройство",
            "source": "corporate",
            "status": "not_found",
        },
    ]
    assert "58803877S002" in answer.answer
    assert "процедуры обслуживания" in answer.answer
    assert "недостаточно данных" not in answer.answer.casefold()
    assert answer.catalog_sources
    assert answer.sources == []
    assert not _should_render_rag_sources(answer)


def test_compound_query_with_both_sources_returns_full_mixed_answer():
    answer = _generator(
        search=MixedSearch(results=_catalog_results()),
        retriever=StaticRetriever([_procedure_chunk()]),
        client=StaticClient("Обслуживание выполняют по инструкции. [1]"),
    ).answer(COMPOUND_QUERY)

    assert answer.response_kind == "mixed_answer"
    assert answer.query_plan["source_route"] == "mixed"
    assert answer.query_plan["catalog_requirement_status"] == "found"
    assert answer.query_plan["corporate_requirement_status"] == "found"
    assert answer.query_plan["answer_mode"] == "full_mixed"
    assert answer.catalog_sources
    assert answer.sources
    assert _should_render_rag_sources(answer)
    assert "Корпоративная база знаний" in answer.answer


def test_pure_catalog_query_remains_catalog_only():
    answer = _generator(search=MixedSearch(results=_catalog_results())).answer(
        "что ты знаешь про прижимное устройство"
    )

    assert answer.response_kind == "catalog_candidates"
    assert answer.query_plan["source_route"] == "catalog"


def test_corporate_procedure_query_remains_corporate():
    answer = _generator(
        search=MixedSearch(results=_catalog_results()),
        retriever=StaticRetriever([_corporate_chunk()]),
        analyzer=StaticAnalyzer(_plan(subject="устное распоряжение")),
        client=StaticClient("Устное распоряжение оформляют письменно. [1]"),
    ).answer("можно ли дать распоряжение устно")

    assert answer.query_plan["source_route"] == "corporate"
    assert answer.catalog is None
    assert answer.sources


def test_exact_code_route_remains_authoritative():
    search = MixedSearch(exact_results=[_catalog_result("X44235100", "вал")])
    answer = _generator(search=search).answer("X44235100")

    assert answer.response_kind == "catalog_exact"
    assert answer.query_plan["catalog_match_type"] == "catalog_exact"
    assert search.fts_calls == []


def test_compound_corporate_query_is_not_mixed_by_weak_catalog_match():
    search = MixedSearch(results=[_catalog_result("301134230120", "шайба")])
    probe = CatalogChatService(search=search).probe_natural_language(
        "кто отвечает за документооборот и как согласовать документ"
    )

    assert probe.source_route == "corporate"
    assert probe.outcome is None
    assert search.fts_calls == []


def test_procedure_found_and_catalog_missing_returns_reverse_partial_answer():
    answer = _generator(
        search=MixedSearch(results=[]),
        retriever=StaticRetriever([_procedure_chunk()]),
        client=StaticClient("Обслуживание выполняют по инструкции. [1]"),
    ).answer(COMPOUND_QUERY)

    assert answer.response_kind == "partial_mixed_answer"
    assert answer.query_plan["source_route"] == "mixed"
    assert answer.query_plan["catalog_requirement_status"] == "not_found"
    assert answer.query_plan["corporate_requirement_status"] == "found"
    assert answer.sources
    assert answer.catalog_sources == ()
    assert "Catalog Store не найдено" in answer.answer


def test_compound_pronoun_uses_the_extracted_catalog_subject():
    answer = _generator(
        search=MixedSearch(
            results=[_catalog_result("20411640", "верхняя часть наполнителя")]
            * 2
        ),
        retriever=StaticRetriever([]),
        analyzer=StaticAnalyzer(_plan(subject="наполнитель")),
    ).answer("что известно про наполнитель и как его обслуживать")

    assert answer.query_plan["source_route"] == "mixed"
    assert {
        item["subject"] for item in answer.query_plan["resolved_requirements"]
    } == {"наполнитель"}


def test_generalized_mixed_queries_preserve_catalog_subject_and_requirement():
    for question, subject, results in (
        (
            "какие детали входят в прижимное устройство и что с ним надо делать при обслуживании",
            "прижимное устройство",
            _catalog_results(),
        ),
        (
            "найди детали нижней части укупорщика и расскажи порядок обслуживания",
            "нижней части укупорщика",
            [
                CatalogPartResult(
                    **{
                        **_catalog_result("X44235100", "вал").__dict__,
                        "assembly_code": "X44236986",
                        "assembly_name": "нижняя часть укупорщика",
                    }
                ),
                CatalogPartResult(
                    **{
                        **_catalog_result("X44235103", "установочное гнездо").__dict__,
                        "assembly_code": "X44236986",
                        "assembly_name": "нижняя часть укупорщика",
                    }
                ),
            ],
        ),
    ):
        answer = _generator(
            search=MixedSearch(results=results),
            retriever=StaticRetriever([]),
        ).answer(question)

        assert answer.response_kind == "partial_mixed_answer"
        assert answer.query_plan["source_route"] == "mixed"
        assert answer.query_plan["catalog_subject"] == subject
        assert answer.query_plan["catalog_requirement_status"] == "found"
        assert answer.query_plan["corporate_requirement_status"] == "not_found"


def test_mixed_equipment_domain_guard_rejects_unrelated_corporate_evidence():
    catalog_results = [
        _catalog_result("20411640", "верхняя часть наполнителя"),
        _catalog_result("20411641", "трубопровод верхняя часть"),
    ]
    unrelated = _chunk(
        "В верхней части рабочего стола находится корзина входящих документов.",
        "рабочий стол сотрудника",
    )
    answer = _generator(
        search=MixedSearch(results=catalog_results),
        retriever=StaticRetriever([unrelated]),
        client=StaticClient("Нерелевантный ответ. [1]"),
    ).answer(
        "что стоит в верхней части наполнителя и есть ли инструкция по работе с этим узлом"
    )

    assert answer.response_kind == "partial_mixed_answer"
    assert answer.query_plan["source_route"] == "mixed"
    assert answer.query_plan["catalog_requirement_status"] == "found"
    assert answer.query_plan["corporate_requirement_status"] == "not_found"
    assert answer.sources == []
    assert answer.evidence["domain_consistency_checked"] is True
    assert answer.evidence["domain_consistency_passed"] is False


def _generator(*, search, retriever=None, analyzer=None, client=None):
    return RagAnswerGenerator(
        retriever=retriever or FailRetriever(),
        llm_client=client or FailClient(),
        query_analyzer=analyzer or StaticAnalyzer(_plan()),
        catalog_chat=CatalogChatService(search=search),
    )


def _plan(*, subject="прижимное устройство"):
    return QueryPlan(
        intent="equipment_it_request",
        normalized_question=COMPOUND_QUERY,
        query_expansions=[],
        preferred_sources=[],
        answer_type="procedure",
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="test",
        requested_fact_type="procedure",
        temporal_scope="static",
        subject=subject,
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


def _procedure_chunk():
    return _chunk(
        "Обслуживание прижимного устройства выполняют по утвержденной инструкции.",
        "прижимное устройство",
    )


def _corporate_chunk():
    return _chunk(
        "Устное распоряжение необходимо оформить в письменном виде.",
        "устное распоряжение",
    )


def _chunk(text, subject):
    return RetrievedChunk(
        chunk_id=1,
        title="Инструкция обслуживания",
        source="data/raw_docs/service.pdf",
        section="Обслуживание",
        page=1,
        text=text,
        score=1.0,
        metadata={
            "logical_unit_title": subject,
            "logical_unit_type": "procedure",
            "doc_type": "instruction",
        },
        doc_type="instruction",
        base_score=1.0,
        rerank_score=2.0,
        final_score=100.0,
        matched_terms=subject.split(),
        matched_excerpt=text,
        selection_reasons=["test"],
    )


class MixedSearch:
    def __init__(self, *, results=None, exact_results=None):
        self.results = list(results or [])
        self.exact_results = list(exact_results or [])
        self.fts_calls = []

    def find_part_by_number(self, identifier):
        return self.exact_results

    def search_parts(self, query, limit=5):
        self.fts_calls.append((query, limit))
        return self.results[:limit]

    def search_part_number_candidates(self, query, limit=5):
        raise AssertionError("mixed natural routing must not use code candidates")


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
