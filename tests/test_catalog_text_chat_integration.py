from __future__ import annotations

from pathlib import Path

import pytest

from linehelper.catalogs.chat import CatalogChatService, extract_catalog_text_search
from linehelper.catalogs.models import CatalogPartResult
from linehelper.llm.answer_generator import RagAnswerGenerator


def test_explicit_name_search_returns_structured_candidates():
    search = FakeCatalogSearch(
        text_results=[_result("X96999553", "газоанализатор кислорода")]
    )

    result = _generator(CatalogChatService(search=search)).answer(
        "Найди в каталоге газоанализатор кислорода"
    )

    assert search.text_calls == [("газоанализатор кислорода", 5)]
    assert search.exact_calls == []
    assert result.response_kind == "catalog_candidates"
    assert result.query_plan["intent"] == "catalog_text_search"
    assert result.catalog["route"] == "catalog_text_search"
    assert result.catalog["search_query"] == "газоанализатор кислорода"
    assert result.catalog["results"][0]["part_number"] == "X96999553"
    assert "кандидат" in result.answer.casefold()
    assert "Найди <код детали>" in result.answer


def test_candidate_mode_applies_top_five_and_never_claims_exact_match():
    search = FakeCatalogSearch(
        text_results=[_result(f"X{i:08d}", f"вариант {i}") for i in range(7)]
    )

    result = _generator(CatalogChatService(search=search)).answer(
        "Поищи в каталоге клапан"
    )

    assert result.catalog["result_count"] == 5
    assert len(result.catalog["results"]) == 5
    assert "показано кандидатов: 5" in result.answer.casefold()
    assert "не подтверждённые exact-совпадения" in result.answer
    assert not result.answer.startswith("Деталь:")


def test_leading_code_fragment_uses_fts_candidate_route():
    search = FakeCatalogSearch(text_results=[_result("X44236986", "подшипник")])

    result = _generator(CatalogChatService(search=search)).answer(
        "Найди в каталоге X44236"
    )

    assert search.text_calls == [("X44236", 5)]
    assert search.exact_calls == []
    assert result.catalog["results"][0]["part_number"] == "X44236986"


def test_exact_full_code_keeps_priority_and_never_calls_fts():
    search = FakeCatalogSearch(
        exact_results=[_result("X56767951", "точная деталь")],
        fail_on_text=True,
    )

    result = _generator(CatalogChatService(search=search)).answer("Найди X56767951")

    assert search.exact_calls == ["X56767951"]
    assert search.text_calls == []
    assert result.response_kind == "catalog_exact"
    assert result.catalog["route"] == "catalog_exact_lookup"


def test_exact_not_found_does_not_fall_back_to_fts():
    search = FakeCatalogSearch(exact_results=[], fail_on_text=True)

    result = _generator(CatalogChatService(search=search)).answer(
        "Найди НЕСУЩЕСТВУЮЩИЙ_ПОЛНЫЙ_КОД"
    )

    assert search.exact_calls == ["НЕСУЩЕСТВУЮЩИЙ_ПОЛНЫЙ_КОД"]
    assert search.text_calls == []
    assert result.response_kind == "catalog_not_found"
    assert "Точного совпадения" in result.answer


def test_explicit_text_search_zero_result_is_honest_and_does_not_use_rag():
    result = _generator(CatalogChatService(search=FakeCatalogSearch())).answer(
        "Найди в каталоге zzzcatalogabsent01"
    )

    assert result.response_kind == "catalog_search_not_found"
    assert result.catalog["status"] == "no_candidates"
    assert "ничего не найдено" in result.answer
    assert result.sources == []


def test_candidate_is_unique_then_exact_follow_up_returns_all_occurrences():
    occurrences = [
        _result("X96999553", "газоанализатор кислорода", assembly=f"A{i}")
        for i in range(3)
    ]
    search = FakeCatalogSearch(
        text_results=[occurrences[0]],
        exact_results=occurrences,
    )
    generator = _generator(CatalogChatService(search=search))

    candidates = generator.answer("Поиск по каталогу: газоанализатор кислорода")
    exact = generator.answer("Найди X96999553")

    assert candidates.catalog["result_count"] == 1
    assert exact.response_kind == "catalog_exact"
    assert exact.catalog["result_count"] == 3
    assert {item["assembly_code"] for item in exact.catalog["results"]} == {
        "A0",
        "A1",
        "A2",
    }


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Найди в каталоге клапан отбора", "клапан отбора"),
        ("Поищи в каталоге газоанализатор кислорода", "газоанализатор кислорода"),
        ("Поиск по каталогу: шаровой шарнир", "шаровой шарнир"),
        ("Найди деталь по описанию нажимный рычаг", "нажимный рычаг"),
        ("Поищи детали X44236", "X44236"),
    ],
)
def test_supported_russian_intents_extract_only_search_payload(question, expected):
    assert extract_catalog_text_search(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Как оформить КП?",
        "Что было в последнем письме?",
        "Сколько деталей заказали в 2025?",
        "Какие остатки в 1С?",
        "Какой статус заказа 12345 в 1С?",
    ],
)
def test_unrelated_and_one_c_requests_are_not_intercepted(question):
    search = FakeCatalogSearch(fail_on_text=True, fail_on_exact=True)

    assert CatalogChatService(search=search).lookup(question) is None
    assert search.text_calls == []
    assert search.exact_calls == []


def test_catalog_database_unavailable_is_controlled(tmp_path: Path):
    result = _generator(CatalogChatService(tmp_path / "missing.db")).answer(
        "Найди в каталоге клапан отбора"
    )

    assert result.response_kind == "catalog_unavailable"
    assert result.catalog["route"] == "catalog_text_search"
    assert result.catalog["status"] == "unavailable"
    assert "сейчас недоступен" in result.answer


def test_empty_explicit_search_requests_clarification_without_match_all():
    search = FakeCatalogSearch(fail_on_text=True)

    result = _generator(CatalogChatService(search=search)).answer(
        "Поиск по каталогу:"
    )

    assert search.text_calls == []
    assert result.response_kind == "catalog_search_clarification"
    assert result.catalog["status"] == "clarification"
    assert "Укажите название" in result.answer


def _generator(catalog_chat):
    return RagAnswerGenerator(
        retriever=FailRetriever(),
        llm_client=FailClient(),
        catalog_chat=catalog_chat,
    )


def _result(number, name, *, assembly="A1"):
    return CatalogPartResult(
        part_number=number,
        part_name=name,
        assembly_code=assembly,
        assembly_name="Тестовый узел",
        position="10",
        quantity="1.000",
        unit="шт",
        equipment_model="Innofill",
        machine_number="47592",
        revision="05",
        source_page=13,
        reference_page=None,
        search_score=-1.0,
    )


class FakeCatalogSearch:
    def __init__(
        self,
        *,
        text_results=None,
        exact_results=None,
        fail_on_text=False,
        fail_on_exact=False,
    ):
        self.text_results = list(text_results or [])
        self.exact_results = list(exact_results or [])
        self.fail_on_text = fail_on_text
        self.fail_on_exact = fail_on_exact
        self.text_calls = []
        self.exact_calls = []

    def search_parts(self, query, limit=5):
        if self.fail_on_text:
            raise AssertionError("FTS must not be called")
        self.text_calls.append((query, limit))
        return self.text_results

    def find_part_by_number(self, identifier):
        if self.fail_on_exact:
            raise AssertionError("exact lookup must not be called")
        self.exact_calls.append(identifier)
        return self.exact_results


class FailRetriever:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("RAG retrieval must not run for catalog routes")


class FailClient:
    model = "fake"

    def chat(self, messages):
        raise AssertionError("LLM must not run for catalog routes")
