from __future__ import annotations

from typing import Any

from linehelper.cli import _parse_args
from linehelper.llm.answer_generator import (
    RagAnswerGenerator,
    rag_answer_history_metadata,
)
from linehelper.rag.conversation_resolver import ConversationSession
from linehelper.rag.query_analyzer import QueryPlan, fallback_query_plan
from linehelper.ui.streamlit_app import _history_for_answer


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_error: str | None = None

    def analyze(self, question: str) -> QueryPlan:
        self.calls.append(question)
        return fallback_query_plan(question)


class RecordingRetriever:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def retrieve(self, question: str, **kwargs: Any) -> list:
        self.calls.append(question)
        return []


class FakeLlm:
    model = "fake-model"

    def chat(self, messages: list[dict[str, str]]) -> str:
        return "Ответ на основе тестового контекста."


def _generator() -> tuple[RagAnswerGenerator, RecordingAnalyzer, RecordingRetriever]:
    analyzer = RecordingAnalyzer()
    retriever = RecordingRetriever()
    return (
        RagAnswerGenerator(
            retriever=retriever,
            llm_client=FakeLlm(),
            query_analyzer=analyzer,
        ),
        analyzer,
        retriever,
    )


def _record(
    session: ConversationSession,
    question: str,
    result,
) -> None:
    session.append_exchange(
        question,
        result.answer,
        assistant_metadata=rag_answer_history_metadata(result),
    )


def test_t01_second_turn_reaches_analyzer_and_retriever_as_resolved_question() -> None:
    generator, analyzer, retriever = _generator()
    session = ConversationSession()

    first = generator.answer("Как оформить КП?", history=session.messages)
    _record(session, "Как оформить КП?", first)
    second = generator.answer(
        "Коммерческое предложение",
        history=session.messages,
    )

    assert first.response_kind == "clarification"
    assert second.resolved_question == "Как оформить коммерческое предложение?"
    assert analyzer.calls[-1] == "Как оформить коммерческое предложение?"
    assert retriever.calls[0] == "Как оформить коммерческое предложение?"
    assert second.query_plan["validated_clarification_required"] is False
    assert second.query_plan["operational_lookup"] is False


def test_missing_document_follow_up_builds_standalone_retrieval_question() -> None:
    generator, analyzer, retriever = _generator()
    session = ConversationSession()

    first = generator.answer("Кому отдать документы?")
    _record(session, "Кому отдать документы?", first)
    second = generator.answer("Кадровые", history=session.messages)

    assert second.resolved_question == "Кому отдать кадровые документы?"
    assert analyzer.calls[-1] == second.resolved_question
    assert retriever.calls[0] == second.resolved_question
    assert second.conversation["inherited_slots"] == ["document_type"]


def test_missing_application_follow_up_is_resolved_before_analysis() -> None:
    generator, analyzer, _ = _generator()
    session = ConversationSession()

    first = generator.answer("Куда направить заявление?")
    _record(session, "Куда направить заявление?", first)
    second = generator.answer("На командировку", history=session.messages)

    assert second.resolved_question == "Куда направить заявление на командировку?"
    assert analyzer.calls[-1] == second.resolved_question
    assert second.conversation["resolution_kind"] == "missing_slot_answer"


def test_topic_change_does_not_inherit_pending_slot() -> None:
    generator, analyzer, _ = _generator()
    session = ConversationSession()

    first = generator.answer("Как оформить КП?")
    _record(session, "Как оформить КП?", first)
    second = generator.answer(
        "Кто отвечает за таможню?",
        history=session.messages,
    )

    assert second.resolved_question == "Кто отвечает за таможню?"
    assert second.conversation["topic_changed"] is True
    assert second.conversation["inherited_slots"] == []
    assert analyzer.calls[-1] == "Кто отвечает за таможню?"


def test_pending_state_is_cleared_after_successful_resolution() -> None:
    generator, _, _ = _generator()
    session = ConversationSession()

    first = generator.answer("Как оформить КП?")
    _record(session, "Как оформить КП?", first)
    second = generator.answer("Коммерческое предложение", history=session.messages)
    _record(session, "Коммерческое предложение", second)
    third = generator.answer(
        "Кто отвечает за рабочие места?",
        history=session.messages,
    )

    assert second.conversation["pending_clarification_after"] is None
    assert third.conversation["resolution_kind"] == "standalone"
    assert third.conversation["inherited_slots"] == []


def test_invalid_slot_answer_repeats_existing_structured_clarification() -> None:
    generator, analyzer, retriever = _generator()
    session = ConversationSession()

    first = generator.answer("Кому отдать документы?")
    _record(session, "Кому отдать документы?", first)
    calls_before = len(analyzer.calls)
    second = generator.answer("Не знаю", history=session.messages)

    assert second.response_kind == "clarification"
    assert second.resolved_question == "Не знаю"
    assert second.conversation["resolution_kind"] == "unresolved_follow_up"
    assert second.conversation["pending_clarification_after"] is not None
    assert len(analyzer.calls) == calls_before
    assert retriever.calls == []


def test_old_one_question_api_remains_standalone() -> None:
    generator, analyzer, _ = _generator()

    result = generator.answer("Можно ли дать распоряжение устно?")

    assert analyzer.calls == ["Можно ли дать распоряжение устно?"]
    assert result.question == "Можно ли дать распоряжение устно?"
    assert result.resolved_question == result.question
    assert result.conversation["conversation_history_used"] is False


def test_no_history_phrase_is_not_composed_with_unknown_predicate() -> None:
    generator, analyzer, _ = _generator()

    result = generator.answer("Коммерческое предложение")

    assert result.resolved_question == "Коммерческое предложение"
    assert analyzer.calls == ["Коммерческое предложение"]


def test_result_diagnostics_keep_original_and_resolved_questions() -> None:
    generator, _, _ = _generator()
    session = ConversationSession()

    first = generator.answer("Как оформить КП?")
    _record(session, "Как оформить КП?", first)
    second = generator.answer("Коммерческое предложение", history=session.messages)

    assert second.question == "Коммерческое предложение"
    assert second.conversation["original_question"] == "Коммерческое предложение"
    assert (
        second.conversation["resolved_question"]
        == "Как оформить коммерческое предложение?"
    )
    assert second.conversation["is_follow_up"] is True


def test_ui_history_helper_does_not_duplicate_current_turn() -> None:
    messages = [
        {"role": "user", "content": "Как оформить КП?"},
        {
            "role": "assistant",
            "content": "Уточните КП.",
            "metadata": {"pending_clarification": {"structured": True}},
            "result": object(),
        },
    ]

    history = _history_for_answer(messages)

    assert len(history) == 2
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert all("result" not in message for message in history)
    assert history[1]["metadata"]["pending_clarification"] == {
        "structured": True
    }


def test_cli_chat_keeps_one_shot_and_supports_interactive_session() -> None:
    one_shot = _parse_args(["chat", "Кто отвечает за отгрузку?"])
    interactive = _parse_args(["chat", "--interactive"])

    assert one_shot.question == "Кто отвечает за отгрузку?"
    assert one_shot.interactive is False
    assert interactive.question is None
    assert interactive.interactive is True
