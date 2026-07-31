from __future__ import annotations

import json
from typing import Any

from linehelper.llm.answer_generator import (
    RagAnswerGenerator,
    rag_answer_history_metadata,
)
from linehelper.rag.conversation_resolver import ConversationSession
from linehelper.rag.query_analyzer import QueryAnalyzer, fallback_query_plan
from linehelper.rag.retriever import RetrievedChunk


def test_t09_finalizes_procedure_before_retrieval_orchestration() -> None:
    analyzer = _analyzer(
        _response(
            intent="order_disposition",
            requested_fact_type="unknown",
            subject="распоряжения компании",
            answer_type="no_answer",
        )
    )
    retriever = _Retriever([_orders_policy_chunk()])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=_AnswerClient(),
        query_analyzer=analyzer,
    )

    result = generator.answer("Можно ли дать распоряжение устно?")

    assert result.query_plan is not None
    assert result.query_plan["initial_requested_fact_type"] == "unknown"
    assert result.query_plan["requested_fact_type"] == "procedure"
    assert result.query_plan["finalized_requested_fact_type"] == "procedure"
    assert result.query_plan["resolution_status"] == "resolved_from_structure"
    assert (
        "question_pattern:normative_action"
        in result.query_plan["matched_signals"]
    )
    assert result.query_plan["operational_lookup"] is False
    assert retriever.calls
    assert result.response_kind == "answer"


def test_existing_fact_types_remain_unchanged_for_core_regressions() -> None:
    cases = [
        (
            "Какие правила документооборота действуют?",
            "document_flow",
            "list",
            "правила документооборота",
        ),
        (
            "Как получить новое оборудование?",
            "equipment_it_request",
            "procedure",
            "новое оборудование",
        ),
        (
            "Как оформить отпуск и за сколько дней подать заявление?",
            "vacation",
            "procedure",
            "отпуск",
        ),
        (
            "Кто отвечает за отгрузку клиенту?",
            "roles_responsibility",
            "responsible_person",
            "отгрузка клиенту",
        ),
        (
            "Кто отвечает за внутренний документооборот?",
            "roles_responsibility",
            "responsible_person",
            "внутренний документооборот",
        ),
    ]

    for question, intent, fact_type, subject in cases:
        plan = _analyzer(
            _response(
                intent=intent,
                requested_fact_type=fact_type,
                subject=subject,
            )
        ).analyze(question)
        assert plan.requested_fact_type == fact_type
        assert plan.fact_type_resolution.initial_fact_type == fact_type
        assert plan.fact_type_resolution.resolved_fact_type == fact_type
        assert (
            plan.fact_type_resolution.resolution_status
            == "unchanged_explicit"
        )


def test_t04_or01_clarification_and_multi_turn_compatibility() -> None:
    t04 = _analyzer(
        _response(
            intent="business_trip",
            requested_fact_type="document_recipient",
            subject="заявление на командировку",
        )
    ).analyze("Кому подавать заявление на командировку?")
    or01 = _analyzer(
        _response(
            intent="order_disposition",
            requested_fact_type="procedure",
            subject="устное распоряжение",
        )
    ).analyze("Что делать после устного распоряжения?")
    clarification = fallback_query_plan("Как оформить КП?")

    assert t04.requested_fact_type == "document_recipient"
    assert or01.requested_fact_type == "procedure"
    assert clarification.clarification_action == "clarify"

    session = ConversationSession()
    generator = RagAnswerGenerator(
        retriever=_Retriever([]),
        llm_client=_AnswerClient(),
        query_analyzer=_FallbackAnalyzer(),
    )
    first = generator.answer("Как оформить КП?", history=session.messages)
    session.append_exchange(
        "Как оформить КП?",
        first.answer,
        assistant_metadata=rag_answer_history_metadata(first),
    )
    second = generator.answer(
        "Коммерческое предложение",
        history=session.messages,
    )

    assert second.resolved_question == "Как оформить коммерческое предложение?"
    assert second.query_plan is not None
    assert second.query_plan["requested_fact_type"] == "procedure"
    assert second.query_plan["validated_clarification_required"] is False


class _JsonAnalyzerClient:
    model = "fake-analyzer"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def chat(self, messages, **kwargs) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


class _FallbackAnalyzer:
    last_error: str | None = None

    def analyze(self, question: str):
        return fallback_query_plan(question)


class _Retriever:
    supports_retrieval_plan = False

    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[str] = []

    def retrieve(self, question: str, *, limit: int, candidate_limit: int):
        self.calls.append(question)
        return list(self.chunks)


class _AnswerClient:
    model = "fake-answer"

    def chat(self, messages) -> str:
        return "Подтверждённый ответ по найденному регламенту."


def _analyzer(payload: dict[str, Any]) -> QueryAnalyzer:
    return QueryAnalyzer(ollama_client=_JsonAnalyzerClient(payload))


def _response(
    *,
    intent: str,
    requested_fact_type: str,
    subject: str,
    answer_type: str = "general",
) -> dict[str, Any]:
    return {
        "intent": intent,
        "requested_fact_type": requested_fact_type,
        "temporal_scope": "static",
        "subject": subject,
        "normalized_question": subject,
        "query_expansions": [subject],
        "preferred_sources": [],
        "answer_type": answer_type,
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
        "notes": "requested fact type integration",
    }


def _orders_policy_chunk() -> RetrievedChunk:
    text = (
        "Отдавать только письменные распоряжения. Если руководитель отдаёт "
        "устное распоряжение, он при первой возможности оформляет его письменно."
    )
    return RetrievedChunk(
        chunk_id=67,
        title="ИП-0005 Распоряжения",
        source="data/raw_docs/ИП-0005 Распоряжения.pdf",
        section="Письменная форма и контроль",
        page=1,
        text=text,
        score=350.0,
        metadata={
            "doc_type": "orders_policy",
            "logical_unit_type": "policy_rule",
            "logical_unit_title": "Письменная форма распоряжения",
        },
        doc_type="orders_policy",
        final_score=350.0,
        matched_excerpt=text,
    )
