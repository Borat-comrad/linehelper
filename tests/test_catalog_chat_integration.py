from __future__ import annotations

from pathlib import Path

import pytest

from linehelper.catalogs.chat import CatalogChatService, extract_part_number_lookup
from linehelper.catalogs.models import CatalogPartResult
from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import QueryPlan


@pytest.mark.parametrize(
    "question,identifier",
    [
        ("Найди 20411616", "20411616"),
        ("Найди X56767951", "X56767951"),
    ],
)
def test_exact_numeric_and_alphanumeric_use_catalog_search(question, identifier):
    search = FakeCatalogSearch([_result(part_number=identifier)])
    result = _generator(CatalogChatService(search=search)).answer(question)

    assert search.calls == [identifier]
    assert result.response_kind == "catalog_exact"
    assert result.catalog["result_count"] == 1
    assert identifier in result.answer
    assert result.query_plan["intent"] == "catalog_exact_lookup"


def test_multiple_occurrences_are_preserved_and_rendered():
    search = FakeCatalogSearch(
        [
            _result(assembly_code="A1", position="10", source_page=13),
            _result(assembly_code="A2", position="20", source_page=99),
        ]
    )
    result = _generator(CatalogChatService(search=search)).answer(
        "Где используется 58803877S001?"
    )

    assert result.catalog["result_count"] == 2
    assert len(result.catalog["results"]) == 2
    assert "Найдено вхождений: 2" in result.answer
    assert "Узел: A1" in result.answer and "Узел: A2" in result.answer


def test_not_found_is_deterministic_and_does_not_call_llm():
    client = FailClient()
    result = _generator(CatalogChatService(search=FakeCatalogSearch([])), client).answer(
        "Найди НЕСУЩЕСТВУЮЩИЙ_КОД"
    )

    assert result.response_kind == "catalog_not_found"
    assert result.catalog["status"] == "not_found"
    assert "Точного совпадения" in result.answer
    assert client.calls == 0


def test_source_and_reference_pages_remain_distinct():
    result = _generator(
        CatalogChatService(
            search=FakeCatalogSearch([_result(source_page=9, reference_page=12)])
        )
    ).answer("Найди 20411616")

    item = result.catalog["results"][0]
    assert item["source_page"] == 9
    assert item["reference_page"] == 12
    assert "Страница спецификации: 9" in result.answer
    assert "Связанная страница: 12" in result.answer


def test_null_reference_page_is_not_replaced_with_source_page():
    result = _generator(
        CatalogChatService(
            search=FakeCatalogSearch([_result(source_page=13, reference_page=None)])
        )
    ).answer("Покажи деталь X56767951")

    assert result.catalog["results"][0]["reference_page"] is None
    assert "Страница спецификации: 13" in result.answer
    assert "Связанная страница" not in result.answer


def test_missing_catalog_database_is_controlled(tmp_path):
    result = _generator(CatalogChatService(tmp_path / "missing.db")).answer(
        "Найди 20411616"
    )

    assert result.response_kind == "catalog_unavailable"
    assert result.catalog["status"] == "unavailable"
    assert "сейчас недоступен" in result.answer


def test_unrelated_numbers_do_not_route_to_catalog():
    search = FakeCatalogSearch([])
    service = CatalogChatService(search=search)

    assert service.lookup("Сколько дней было в 2026 году?") is None
    assert service.lookup("Найди регламент за 2024 год") is None
    assert search.calls == []


def test_spaced_part_number_is_passed_unchanged_to_existing_search():
    search = FakeCatalogSearch([_result(part_number="XFH 20093")])
    _generator(CatalogChatService(search=search)).answer("Найди XFH 20093")

    assert search.calls == ["XFH 20093"]


def test_existing_operational_route_is_unchanged_when_catalog_does_not_match():
    catalog = NoMatchCatalogChat()
    generator = RagAnswerGenerator(
        retriever=EmptyRetriever(),
        llm_client=FailClient(),
        query_analyzer=StaticQueryAnalyzer(
            QueryPlan(
                intent="one_c_operational_lookup",
                normalized_question="Какой статус заказа в 1С?",
                query_expansions=[],
                preferred_sources=[],
                answer_type="partial_answer",
                needs_clarification=False,
                clarification_question=None,
                confidence=1.0,
                notes="regression",
                operational_lookup=True,
            )
        ),
        catalog_chat=catalog,
    )

    result = generator.answer("Какой статус заказа 12345 в 1С?")

    assert catalog.questions == ["Какой статус заказа 12345 в 1С?"]
    assert result.query_plan["intent"] == "one_c_operational_lookup"
    assert result.response_kind == "no_answer"


def test_bare_code_detection_is_narrow():
    assert extract_part_number_lookup("20411616") == "20411616"
    assert extract_part_number_lookup("2026") is None
    assert extract_part_number_lookup("В отчете 20411616 строк") is None


def _generator(catalog_chat, client=None):
    return RagAnswerGenerator(
        retriever=FailRetriever(),
        llm_client=client or FailClient(),
        catalog_chat=catalog_chat,
    )


def _result(
    *,
    part_number="58803877S001",
    assembly_code="10307585",
    position="200",
    source_page=9,
    reference_page=12,
):
    return CatalogPartResult(
        part_number=part_number,
        part_name="Тестовая деталь",
        assembly_code=assembly_code,
        assembly_name="Тестовый узел",
        position=position,
        quantity="2.000",
        unit="шт",
        equipment_model="Innofill",
        machine_number="47592",
        revision="05",
        source_page=source_page,
        reference_page=reference_page,
    )


class FakeCatalogSearch:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def find_part_by_number(self, identifier):
        self.calls.append(identifier)
        return self.results


class FailRetriever:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("RAG retrieval must not run for catalog exact lookup")


class EmptyRetriever:
    def retrieve(self, *args, **kwargs):
        return []


class FailClient:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        raise AssertionError("LLM must not run")


class StaticQueryAnalyzer:
    def __init__(self, plan):
        self.plan = plan

    def analyze(self, question):
        return self.plan


class NoMatchCatalogChat:
    def __init__(self):
        self.questions = []

    def lookup(self, question):
        self.questions.append(question)
        return None
