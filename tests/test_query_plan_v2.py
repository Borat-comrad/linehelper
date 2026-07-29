from __future__ import annotations

import json
from typing import Any

import pytest

from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import (
    ALLOWED_REQUESTED_FACT_TYPES,
    ALLOWED_TEMPORAL_SCOPES,
    QueryAnalyzer,
    QueryPlan,
    build_query_analyzer_prompt,
    fallback_query_plan,
    validate_query_plan,
)
from linehelper.rag.retriever import RetrievedChunk


def test_query_plan_v2_enums_and_legacy_defaults() -> None:
    plan = _legacy_plan()

    assert {
        "definition",
        "procedure",
        "responsible_person",
        "primary_contact",
        "unit_head",
        "document_recipient",
        "list",
        "comparison",
        "current_status",
        "current_value",
        "price",
        "availability",
        "unknown",
    } == ALLOWED_REQUESTED_FACT_TYPES
    assert {"static", "current", "historical", "unknown"} == ALLOWED_TEMPORAL_SCOPES
    assert plan.requested_fact_type == "unknown"
    assert plan.temporal_scope == "unknown"
    assert plan.subject == ""
    assert plan.operational_lookup is False


def test_old_analyzer_json_without_v2_fields_is_safe() -> None:
    analyzer = QueryAnalyzer(
        ollama_client=JsonAnalyzerClient(
            {
                "intent": "unknown",
                "normalized_question": "Внутренний вопрос",
                "query_expansions": [],
                "preferred_sources": [],
                "answer_type": "general",
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.5,
                "notes": None,
            }
        )
    )

    plan = analyzer.analyze("Внутренний вопрос")

    assert plan.requested_fact_type == "unknown"
    assert plan.temporal_scope == "unknown"
    assert plan.subject == "внутренний вопрос"
    assert plan.operational_lookup is False


def test_invalid_v2_enum_values_are_visible_and_normalized() -> None:
    response = _response(
        intent="unknown",
        requested_fact_type="invented_fact",
        temporal_scope="tomorrow",
        subject="  Внутренний   вопрос ",
    )
    analyzer = QueryAnalyzer(ollama_client=JsonAnalyzerClient(response))

    plan = analyzer.analyze("Внутренний вопрос")

    assert plan.requested_fact_type == "unknown"
    assert plan.raw_requested_fact_type == "invented_fact"
    assert plan.temporal_scope == "unknown"
    assert plan.raw_temporal_scope == "tomorrow"
    assert plan.subject == "внутренний вопрос"
    assert "invalid_requested_fact_type" in plan.validation_reasons
    assert "invalid_temporal_scope" in plan.validation_reasons


def test_query_analyzer_prompt_declares_v2_dimensions() -> None:
    prompt = "\n".join(
        message["content"]
        for message in build_query_analyzer_prompt("Кто отвечает за заказ?")
    )

    assert "requested_fact_type" in prompt
    assert "temporal_scope" in prompt
    assert "subject" in prompt
    assert "сами по себе не делают вопрос операционным" in prompt


@pytest.mark.parametrize(
    ("question", "expected_fact_type"),
    [
        ("Кто отвечает за заказ?", "responsible_person"),
        ("Кто отвечает за отгрузку клиенту?", "responsible_person"),
        ("Кто ведёт склад?", "responsible_person"),
        ("Кто занимается оплатами?", "responsible_person"),
        ("К кому обратиться по доставке?", "primary_contact"),
        ("Кто главный по рабочим местам?", "unit_head"),
    ],
)
def test_responsibility_questions_are_static_semantic(
    question: str,
    expected_fact_type: str,
) -> None:
    plan = fallback_query_plan(question)

    assert plan.requested_fact_type == expected_fact_type
    assert plan.temporal_scope == "static"
    assert plan.operational_lookup is False
    assert plan.intent == "roles_responsibility"


@pytest.mark.parametrize(
    ("question", "expected_fact_type"),
    [
        ("Какой статус заказа №12345?", "current_status"),
        ("Отгружен ли уже заказ?", "current_status"),
        ("Что сейчас есть на складе?", "availability"),
        ("Оплачен ли счёт №123?", "current_status"),
        ("Какая текущая закупочная цена?", "price"),
        ("Когда поставщик отгрузит заказ?", "current_status"),
    ],
)
def test_current_operational_questions_route_to_one_c(
    question: str,
    expected_fact_type: str,
) -> None:
    plan = fallback_query_plan(question)

    assert plan.requested_fact_type == expected_fact_type
    assert plan.temporal_scope == "current"
    assert plan.operational_lookup is True
    assert plan.intent == "one_c_operational_lookup"


@pytest.mark.parametrize(
    "question",
    [
        "Как оформить командировку?",
        "Как получить новое оборудование?",
        "Как проходит оплата поставщику?",
        "Как оформить отпуск?",
    ],
)
def test_procedure_questions_are_not_operational(question: str) -> None:
    plan = fallback_query_plan(question)

    assert plan.requested_fact_type == "procedure"
    assert plan.temporal_scope == "static"
    assert plan.operational_lookup is False


@pytest.mark.parametrize(
    ("question", "expected_fact_type"),
    [
        ("Что такое ЦКП?", "definition"),
        ("Какие правила документооборота действуют?", "list"),
        ("Какие подразделения есть в компании?", "list"),
    ],
)
def test_definition_and_list_questions_are_not_operational(
    question: str,
    expected_fact_type: str,
) -> None:
    plan = fallback_query_plan(question)

    assert plan.requested_fact_type == expected_fact_type
    assert plan.temporal_scope == "static"
    assert plan.operational_lookup is False


@pytest.mark.parametrize(
    "question",
    [
        "Кто отвечает за склад?",
        "Кто отвечает за заказ №12345?",
        "Кто отвечает за отгрузку?",
        "Кто занимается оплатой счёта №123?",
        "Как проходит поставка клиенту?",
    ],
)
def test_operational_subject_words_do_not_override_static_fact_type(
    question: str,
) -> None:
    plan = fallback_query_plan(question)

    assert plan.requested_fact_type in {
        "responsible_person",
        "primary_contact",
        "unit_head",
        "procedure",
    }
    assert plan.operational_lookup is False
    assert plan.intent != "one_c_operational_lookup"


def test_historical_price_is_not_treated_as_current_lookup() -> None:
    plan = fallback_query_plan("Какая была цена в прошлом заказе?")

    assert plan.requested_fact_type == "price"
    assert plan.temporal_scope == "historical"
    assert plan.operational_lookup is False
    assert plan.operational_decision_reason == "historical_scope_not_current_lookup"


def test_fallback_query_plan_v2_is_deterministic() -> None:
    first = fallback_query_plan("Кто отвечает за отгрузку клиенту?")
    second = fallback_query_plan("Кто отвечает за отгрузку клиенту?")

    assert first.to_dict() == second.to_dict()
    assert "fallback_plan" in first.validation_reasons


def test_validator_corrects_wrong_operational_plan_for_responsibility() -> None:
    analyzer = QueryAnalyzer(
        ollama_client=JsonAnalyzerClient(
            _response(
                intent="one_c_operational_lookup",
                requested_fact_type="current_status",
                temporal_scope="current",
                subject="отгрузка клиенту",
            )
        )
    )

    plan = analyzer.analyze("Кто отвечает за отгрузку клиенту?")

    assert plan.raw_intent == "one_c_operational_lookup"
    assert plan.raw_requested_fact_type == "current_status"
    assert plan.intent == "roles_responsibility"
    assert plan.requested_fact_type == "responsible_person"
    assert plan.temporal_scope == "static"
    assert plan.operational_lookup is False
    assert "explicit_responsibility_question" in plan.validation_reasons
    assert "intent_corrected_to_roles_responsibility" not in plan.validation_reasons


def test_validator_corrects_wrong_responsibility_plan_for_current_status() -> None:
    analyzer = QueryAnalyzer(
        ollama_client=JsonAnalyzerClient(
            _response(
                intent="roles_responsibility",
                requested_fact_type="responsible_person",
                temporal_scope="static",
                subject="заказ №12345",
            )
        )
    )

    plan = analyzer.analyze("Какой статус заказа №12345?")

    assert plan.raw_intent == "roles_responsibility"
    assert plan.intent == "one_c_operational_lookup"
    assert plan.requested_fact_type == "current_status"
    assert plan.temporal_scope == "current"
    assert plan.operational_lookup is True
    assert "explicit_current_status_question" in plan.validation_reasons
    assert "intent_corrected_to_one_c_operational_lookup" in plan.validation_reasons


def test_validator_keeps_consistent_plan_without_correction_reason() -> None:
    analyzer = QueryAnalyzer(
        ollama_client=JsonAnalyzerClient(
            _response(
                intent="roles_responsibility",
                requested_fact_type="responsible_person",
                temporal_scope="static",
                subject="Отгрузка   клиенту",
            )
        )
    )

    plan = analyzer.analyze("Кто отвечает за отгрузку клиенту?")

    assert plan.intent == "roles_responsibility"
    assert plan.requested_fact_type == "responsible_person"
    assert plan.temporal_scope == "static"
    assert plan.subject == "отгрузка клиенту"
    assert plan.validation_reasons == []


def test_public_validator_upgrades_legacy_query_plan() -> None:
    validated = validate_query_plan(
        _legacy_plan(intent="one_c_operational_lookup"),
        "Кто отвечает за заказ?",
    )

    assert validated.requested_fact_type == "responsible_person"
    assert validated.temporal_scope == "static"
    assert validated.operational_lookup is False
    assert validated.intent == "roles_responsibility"


def test_deterministic_orchestration_keeps_semantic_retrieval_after_correction() -> None:
    analyzer = QueryAnalyzer(
        ollama_client=JsonAnalyzerClient(
            _response(
                intent="one_c_operational_lookup",
                requested_fact_type="current_status",
                temporal_scope="current",
                subject="отгрузка клиенту",
            )
        )
    )
    retriever = RecordingRetriever([_responsibility_chunk()])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=AnswerClient("Подтверждён ответственный по доставке."),
        query_analyzer=analyzer,
    )

    result = generator.answer("Кто отвечает за отгрузку клиенту?")

    assert retriever.questions
    assert result.response_kind == "answer"
    assert result.chunks_used == 1
    assert result.query_plan is not None
    assert result.query_plan["requested_fact_type"] == "responsible_person"
    assert result.query_plan["operational_lookup"] is False
    assert result.query_plan["raw_requested_fact_type"] == "current_status"
    assert result.query_plan["query_plan_validation_reasons"]


def test_deterministic_orchestration_applies_current_operational_boundary() -> None:
    analyzer = QueryAnalyzer(
        ollama_client=JsonAnalyzerClient(
            _response(
                intent="roles_responsibility",
                requested_fact_type="responsible_person",
                temporal_scope="static",
                subject="заказ №12345",
            )
        )
    )
    generator = RagAnswerGenerator(
        retriever=RecordingRetriever([_responsibility_chunk()]),
        llm_client=AnswerClient("unused"),
        query_analyzer=analyzer,
    )

    result = generator.answer("Какой статус заказа №12345?")

    assert result.response_kind == "no_answer"
    assert result.sources == []
    assert result.query_plan is not None
    assert result.query_plan["intent"] == "one_c_operational_lookup"
    assert result.query_plan["requested_fact_type"] == "current_status"
    assert result.query_plan["temporal_scope"] == "current"
    assert result.query_plan["operational_lookup"] is True
    assert (
        result.query_plan["operational_decision_reason"]
        == "current_operational_fact_type"
    )


def _response(
    *,
    intent: str,
    requested_fact_type: str,
    temporal_scope: str,
    subject: str,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "requested_fact_type": requested_fact_type,
        "temporal_scope": temporal_scope,
        "subject": subject,
        "normalized_question": subject,
        "query_expansions": [subject],
        "preferred_sources": [],
        "answer_type": "general",
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
        "notes": "test analyzer proposal",
    }


def _legacy_plan(*, intent: str = "unknown") -> QueryPlan:
    return QueryPlan(
        intent=intent,
        normalized_question="Внутренний вопрос",
        query_expansions=[],
        preferred_sources=[],
        answer_type="general",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.5,
        notes=None,
    )


class JsonAnalyzerClient:
    model = "fake-analyzer"

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return json.dumps(self.response, ensure_ascii=False)


class AnswerClient:
    model = "fake-answer"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.messages.append(messages)
        return self.answer


class RecordingRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.questions: list[str] = []

    def retrieve(self, question: str, **kwargs: Any) -> list[RetrievedChunk]:
        self.questions.append(question)
        return list(self.chunks)


def _responsibility_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=594,
        title="Маршрут: ЛОГИСТИКА И СКЛАД — Доставка клиенту",
        source="bvr_company_structure_instruction_v2 (2).txt",
        section="Логистика и склад",
        page=None,
        text=(
            "По вопросам функции Доставка клиенту следует обращаться к "
            "подтверждённому ответственному контакту."
        ),
        score=100.0,
        metadata={
            "doc_type": "responsibility_route",
            "knowledge_domain": "organization_structure",
            "record_key": (
                "responsibility_route:"
                "logistika_i_sklad_dostavka_klientu:2025-12-17"
            ),
        },
        doc_type="responsibility_route",
        base_score=100.0,
        rerank_score=20.0,
        final_score=120.0,
        matched_terms=["доставка", "клиенту"],
        matched_excerpt="Доставка клиенту.",
        selection_reasons=["deterministic fixture"],
    )
