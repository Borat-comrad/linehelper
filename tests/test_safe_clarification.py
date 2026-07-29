from __future__ import annotations

import json

import pytest

from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import (
    ClarificationPlan,
    QueryPlan,
    fallback_query_plan,
    parse_query_plan_response,
    validate_query_plan,
)
from linehelper.rag.retriever import RetrievedChunk


class FakeAnalyzer:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan
        self.calls: list[str] = []
        self.last_error: str | None = None

    def analyze(self, question: str) -> QueryPlan:
        self.calls.append(question)
        return self.plan


class RecordingRetriever:
    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self.calls: list[str] = []
        self.chunks = chunks or []

    def retrieve(self, question: str, *, limit: int, candidate_limit: int):
        self.calls.append(question)
        return list(self.chunks)


class FakeLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        return "Ответ не используется в тесте архитектурного решения."


def _plan(
    question: str,
    *,
    intent: str = "order_disposition",
    requested_fact_type: str = "procedure",
    temporal_scope: str = "static",
    subject: str = "распоряжение",
    raw_clarification: ClarificationPlan | None = None,
    needs_clarification: bool = False,
    clarification_question: str | None = None,
    answer_type: str = "general",
) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        normalized_question=question,
        query_expansions=[question],
        preferred_sources=[],
        answer_type=answer_type,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        confidence=0.8,
        notes="deterministic safe clarification test",
        requested_fact_type=requested_fact_type,
        temporal_scope=temporal_scope,
        subject=subject,
        raw_clarification=raw_clarification,
    )


@pytest.mark.parametrize(
    "question",
    [
        "Как оформить КП?",
        "Что является КП отдела?",
        "КП уже готово?",
        "Кто готовит КП?",
        "Что входит в КП?",
    ],
)
def test_valid_kp_ambiguity_is_structured(question: str) -> None:
    plan = fallback_query_plan(question)

    assert plan.clarification_action == "clarify"
    assert plan.clarification.required is True
    assert plan.clarification.kind == "abbreviation"
    assert plan.clarification.ambiguity_span == "КП"
    assert len(plan.clarification.candidate_meanings) >= 2
    assert plan.clarification.question


@pytest.mark.parametrize(
    "question",
    [
        "Можно ли дать распоряжение устно?",
        "Как правильно оформить распоряжение?",
        "Кто отвечает за внутренний документооборот?",
        "Кто отвечает за отгрузку клиенту?",
        "Как оформить командировку?",
        "Как получить новое оборудование?",
        "Какие правила документооборота действуют?",
        "Можно ли отдавать распоряжения устно?",
        "Руководитель может дать устное распоряжение?",
        "Нужно ли письменно фиксировать распоряжение?",
        "Что делать после устного распоряжения?",
        "Допустимо ли распоряжение без письменной фиксации?",
    ],
)
def test_clear_questions_continue_retrieval(question: str) -> None:
    plan = fallback_query_plan(question)

    assert plan.clarification_action == "continue_retrieval"
    assert plan.clarification.required is False
    assert plan.needs_clarification is False


@pytest.mark.parametrize(
    ("question", "kind", "slot"),
    [
        ("Кому отдать документы?", "missing_document_type", "document_type"),
        ("Кому отдать бумаги?", "missing_document_type", "document_type"),
        ("Кто этим занимается?", "missing_subject", "subject"),
        ("Куда направить заявление?", "missing_document_type", "application_type"),
        (
            "Кто согласует моё заявление?",
            "missing_document_type",
            "application_type",
        ),
    ],
)
def test_missing_required_slot_is_structured(
    question: str,
    kind: str,
    slot: str,
) -> None:
    plan = fallback_query_plan(question)

    assert plan.clarification_action == "clarify"
    assert plan.clarification.kind == kind
    assert slot in plan.clarification.missing_slots
    assert plan.clarification.question


@pytest.mark.parametrize(
    "question",
    [
        "Кому подать заявление на командировку?",
        "Кому подать заявление на отпуск?",
        "Куда отправить заявление на новое оборудование?",
        "Куда передать оригиналы кадровых документов?",
        "Кому направить бухгалтерские документы?",
    ],
)
def test_present_required_slot_prevents_clarification(question: str) -> None:
    assert fallback_query_plan(question).clarification_action == "continue_retrieval"


@pytest.mark.parametrize(
    "raw",
    [
        ClarificationPlan(required=True, kind="abbreviation"),
        ClarificationPlan(
            required=True,
            kind="abbreviation",
            ambiguity_span="КП",
            candidate_meanings=["коммерческое предложение", "ценный конечный продукт"],
            question="КП или ЦКП?",
        ),
        ClarificationPlan(
            required=True,
            kind="abbreviation",
            ambiguity_span="распоряжение",
            candidate_meanings=["приказ"],
            question="Какое значение?",
        ),
        ClarificationPlan(
            required=True,
            kind="abbreviation",
            ambiguity_span="распоряжение",
            candidate_meanings=["приказ", "приказ"],
            question="Какое значение?",
        ),
        ClarificationPlan(
            required=True,
            kind="missing_document_type",
            missing_slots=[],
            question="Какие документы?",
        ),
        ClarificationPlan(
            required=True,
            kind="missing_document_type",
            missing_slots=["document_type"],
            question="Уточните вопрос",
        ),
        ClarificationPlan(
            required=True,
            kind="unknown_kind",
            question="Уточните вопрос",
        ),
        ClarificationPlan(
            required=True,
            kind="abbreviation",
            ambiguity_span="КП",
            candidate_meanings=["коммерческое предложение", "ценный конечный продукт"],
            question="КП или ЦКП?",
        ),
    ],
)
def test_invalid_raw_clarification_is_rejected_for_clear_question(
    raw: ClarificationPlan,
) -> None:
    plan = validate_query_plan(
        _plan(
            "Можно ли дать распоряжение устно?",
            raw_clarification=raw,
            needs_clarification=True,
            clarification_question=raw.question,
            answer_type="clarification",
        ),
        "Можно ли дать распоряжение устно?",
    )

    assert plan.clarification_action == "continue_retrieval"
    assert plan.needs_clarification is False
    assert plan.answer_type != "clarification"
    assert "invalid_clarification_rejected" in plan.clarification_validation_reasons


@pytest.mark.parametrize("question", ["КП", "кп", "КП?", "«КП»"])
def test_kp_registry_respects_case_and_punctuation(question: str) -> None:
    plan = fallback_query_plan(question)

    assert plan.clarification_action == "clarify"
    assert plan.clarification.ambiguity_span == "КП"


@pytest.mark.parametrize(
    "question",
    [
        "КПИ — это университет?",
        "Аббревиатура АКПП относится к автомобилю.",
        "Это строка кпроекту без отдельного сокращения.",
        "Что означает СКП?",
    ],
)
def test_kp_registry_respects_word_boundaries(question: str) -> None:
    assert fallback_query_plan(question).clarification_action == "continue_retrieval"


def test_old_json_without_structured_clarification_is_safe() -> None:
    payload = {
        "intent": "order_disposition",
        "normalized_question": "Можно ли дать распоряжение устно?",
        "query_expansions": ["распоряжение устно"],
        "preferred_sources": [],
        "answer_type": "clarification",
        "needs_clarification": True,
        "clarification_question": None,
        "confidence": 0.5,
        "notes": "legacy analyzer output",
    }

    plan = parse_query_plan_response(
        json.dumps(payload, ensure_ascii=False),
        "Можно ли дать распоряжение устно?",
    )

    assert plan.raw_clarification is not None
    assert plan.raw_clarification.required is True
    assert plan.clarification_action == "continue_retrieval"
    assert plan.needs_clarification is False


def test_structured_clarification_json_is_parsed_and_validated() -> None:
    payload = {
        "intent": "unknown",
        "normalized_question": "Что означает SLA?",
        "query_expansions": ["SLA"],
        "preferred_sources": [],
        "answer_type": "clarification",
        "needs_clarification": True,
        "clarification_question": "SLA — срок реакции или уровень сервиса?",
        "clarification": {
            "required": True,
            "kind": "abbreviation",
            "ambiguity_span": "SLA",
            "candidate_meanings": ["срок реакции", "уровень сервиса"],
            "missing_slots": [],
            "question": "SLA — срок реакции или уровень сервиса?",
            "confidence": 0.7,
        },
        "confidence": 0.7,
        "notes": "structured analyzer output",
    }

    plan = parse_query_plan_response(
        json.dumps(payload, ensure_ascii=False),
        "Что означает SLA?",
    )

    assert plan.raw_clarification is not None
    assert plan.raw_clarification.kind == "abbreviation"
    assert plan.clarification_action == "clarify"
    assert plan.clarification.candidate_meanings == [
        "срок реакции",
        "уровень сервиса",
    ]


def test_integration_wrong_kp_clarification_for_t09_is_rejected_and_retrieves() -> None:
    raw = ClarificationPlan(
        required=True,
        kind="abbreviation",
        ambiguity_span="КП",
        candidate_meanings=["коммерческое предложение", "ценный конечный продукт"],
        question="КП или ЦКП?",
    )
    retriever = RecordingRetriever(
        [
            RetrievedChunk(
                chunk_id=67,
                title="ИП-0005 Распоряжения",
                source="ИП-0005 Распоряжения",
                section="Письменная форма",
                page=None,
                text=(
                    "Распоряжения должны быть письменными. Устное распоряжение "
                    "оформляется письменно при первой возможности."
                ),
                score=100.0,
                metadata={},
                final_score=100.0,
            )
        ]
    )
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(
            _plan(
                "Можно ли дать распоряжение устно?",
                raw_clarification=raw,
                needs_clarification=True,
                clarification_question=raw.question,
                answer_type="clarification",
            )
        ),
    )

    result = generator.answer("Можно ли дать распоряжение устно?")

    assert result.response_kind != "clarification"
    assert retriever.calls
    assert any(source.title == "ИП-0005 Распоряжения" for source in result.sources)
    assert result.query_plan is not None
    assert result.query_plan["raw_clarification_required"] is True
    assert result.query_plan["validated_clarification_required"] is False
    assert result.query_plan["clarification_action"] == "continue_retrieval"


def test_integration_missing_raw_question_is_rejected_and_retrieves() -> None:
    retriever = RecordingRetriever()
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(
            _plan(
                "Можно ли дать распоряжение устно?",
                raw_clarification=ClarificationPlan(
                    required=True,
                    kind="abbreviation",
                    ambiguity_span="распоряжение",
                    candidate_meanings=["приказ", "указание"],
                ),
                needs_clarification=True,
                answer_type="clarification",
            )
        ),
    )

    result = generator.answer("Можно ли дать распоряжение устно?")

    assert result.response_kind != "clarification"
    assert retriever.calls


def test_integration_valid_kp_clarification_stops_retrieval() -> None:
    retriever = RecordingRetriever()
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(
            _plan(
                "Как оформить КП?",
                intent="ambiguous_abbreviation",
                requested_fact_type="procedure",
                subject="кп",
                raw_clarification=ClarificationPlan(
                    required=True,
                    kind="abbreviation",
                    ambiguity_span="КП",
                    candidate_meanings=[
                        "коммерческое предложение",
                        "ценный конечный продукт",
                    ],
                    question=(
                        "Вы имеете в виду коммерческое предложение "
                        "или ценный конечный продукт?"
                    ),
                ),
                needs_clarification=True,
                answer_type="clarification",
            )
        ),
    )

    result = generator.answer("Как оформить КП?")

    assert result.response_kind == "clarification"
    assert retriever.calls == []


def test_integration_registry_repairs_missed_kp_ambiguity() -> None:
    retriever = RecordingRetriever()
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(
            _plan(
                "Как оформить КП?",
                intent="kp_commercial_offer",
                requested_fact_type="procedure",
                subject="кп",
            )
        ),
    )

    result = generator.answer("Как оформить КП?")

    assert result.response_kind == "clarification"
    assert retriever.calls == []
    assert result.query_plan is not None
    assert result.query_plan["raw_clarification_required"] is False
    assert result.query_plan["validated_clarification_required"] is True


def test_integration_accepted_ambiguity_defers_operational_routing() -> None:
    retriever = RecordingRetriever()
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(
            _plan(
                "КП уже согласовано?",
                intent="one_c_operational_lookup",
                requested_fact_type="current_value",
                temporal_scope="current",
                subject="кп",
            )
        ),
    )

    result = generator.answer("КП уже согласовано?")
    diagnostics = result.query_plan or {}

    assert result.response_kind == "clarification"
    assert retriever.calls == []
    assert diagnostics["intent"] == "ambiguous_abbreviation"
    assert diagnostics["operational_lookup"] is False
    assert (
        diagnostics["operational_decision_reason"]
        == "clarification_required_before_routing"
    )


@pytest.mark.parametrize(
    ("question", "clarifies"),
    [
        ("Кому отдать документы?", True),
        ("Кому подать заявление на командировку?", False),
        ("Кто этим занимается?", True),
        ("Кто отвечает за внутренний документооборот?", False),
        ("Какой статус заказа №12345?", False),
    ],
)
def test_integration_missing_slot_and_clear_boundaries(
    question: str,
    clarifies: bool,
) -> None:
    retriever = RecordingRetriever()
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(fallback_query_plan(question)),
    )

    result = generator.answer(question)

    assert (result.response_kind == "clarification") is clarifies
    assert bool(retriever.calls) is (not clarifies)
    if clarifies:
        assert (result.query_plan or {})["intent"] == "unknown"


def test_integration_diagnostics_keep_raw_and_validated_decisions() -> None:
    raw = ClarificationPlan(
        required=True,
        kind="abbreviation",
        ambiguity_span="КП",
        candidate_meanings=["коммерческое предложение", "ценный конечный продукт"],
        question="КП или ЦКП?",
    )
    generator = RagAnswerGenerator(
        retriever=RecordingRetriever(),
        llm_client=FakeLlm(),
        query_analyzer=FakeAnalyzer(
            _plan(
                "Кто отвечает за отгрузку клиенту?",
                intent="roles_responsibility",
                requested_fact_type="responsible_person",
                subject="отгрузка клиенту",
                raw_clarification=raw,
                needs_clarification=True,
                clarification_question=raw.question,
            )
        ),
    )

    result = generator.answer("Кто отвечает за отгрузку клиенту?")
    diagnostics = result.query_plan or {}

    assert diagnostics["raw_clarification_kind"] == "abbreviation"
    assert diagnostics["raw_ambiguity_span"] == "КП"
    assert diagnostics["validated_clarification_kind"] == "none"
    assert diagnostics["clarification_action"] == "continue_retrieval"
    assert diagnostics["clarification_validation_reasons"]
