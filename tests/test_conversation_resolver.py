from __future__ import annotations

import pytest

from linehelper.rag.conversation_resolver import (
    ConversationContext,
    ConversationResolver,
    ConversationSession,
    PendingClarification,
    conversation_context_from_history,
    deserialize_pending_clarification,
    serialize_pending_clarification,
)
from linehelper.rag.query_analyzer import ClarificationPlan


def _pending_history(
    source_question: str,
    plan: ClarificationPlan,
) -> list[dict]:
    pending = PendingClarification(source_question=source_question, plan=plan)
    return [
        {"role": "user", "content": source_question},
        {
            "role": "assistant",
            "content": plan.question or "Уточните.",
            "metadata": {
                "pending_clarification": serialize_pending_clarification(pending)
            },
        },
    ]


def _kp_plan() -> ClarificationPlan:
    return ClarificationPlan(
        required=True,
        kind="abbreviation",
        ambiguity_span="КП",
        candidate_meanings=[
            "коммерческое предложение",
            "ценный конечный продукт",
        ],
        question=(
            "Вы имеете в виду КП как коммерческое предложение "
            "или как ценный конечный продукт?"
        ),
        confidence=1.0,
    )


def _missing_plan(slot: str, *, kind: str = "missing_document_type") -> ClarificationPlan:
    return ClarificationPlan(
        required=True,
        kind=kind,
        missing_slots=[slot],
        question="Уточните недостающий параметр.",
        confidence=1.0,
    )


@pytest.mark.parametrize(
    "question",
    [
        "Как оформить отпуск?",
        "Кто отвечает за отгрузку клиенту?",
        "Можно ли дать распоряжение устно?",
    ],
)
def test_standalone_question_is_not_rewritten(question: str) -> None:
    resolved = ConversationResolver().resolve(question)

    assert resolved.resolved_question == question
    assert resolved.is_follow_up is False
    assert resolved.resolution_kind == "standalone"
    assert resolved.conversation_history_used is False


@pytest.mark.parametrize(
    ("source", "answer", "expected"),
    [
        (
            "Как оформить КП?",
            "Коммерческое предложение",
            "Как оформить коммерческое предложение?",
        ),
        (
            "Что входит в КП?",
            "Ценный конечный продукт",
            "Что входит в ценный конечный продукт?",
        ),
        (
            "Кто готовит КП?",
            "Коммерческое",
            "Кто готовит коммерческое предложение?",
        ),
    ],
)
def test_abbreviation_answer_replaces_only_ambiguity_span(
    source: str,
    answer: str,
    expected: str,
) -> None:
    resolved = ConversationResolver().resolve(
        answer,
        history=_pending_history(source, _kp_plan()),
    )

    assert resolved.resolved_question == expected
    assert resolved.resolution_kind == "clarification_answer"
    assert resolved.inherited_slots == ("meaning_of_КП",)
    assert resolved.topic_changed is False


@pytest.mark.parametrize(
    ("source", "answer", "expected"),
    [
        (
            "Кому отдать документы?",
            "Кадровые",
            "Кому отдать кадровые документы?",
        ),
        (
            "Куда передать документы?",
            "Бухгалтерские",
            "Куда передать бухгалтерские документы?",
        ),
        (
            "Кому отдать бумаги?",
            "Оригиналы кадровых документов",
            "Кому отдать оригиналы кадровых документов?",
        ),
    ],
)
def test_missing_document_type_is_filled(
    source: str,
    answer: str,
    expected: str,
) -> None:
    resolved = ConversationResolver().resolve(
        answer,
        history=_pending_history(source, _missing_plan("document_type")),
    )

    assert resolved.resolved_question == expected
    assert resolved.resolution_kind == "missing_slot_answer"
    assert resolved.inherited_slots == ("document_type",)


@pytest.mark.parametrize(
    ("source", "answer", "expected"),
    [
        (
            "Куда направить заявление?",
            "На командировку",
            "Куда направить заявление на командировку?",
        ),
        (
            "Кто согласует заявление?",
            "На отпуск",
            "Кто согласует заявление на отпуск?",
        ),
    ],
)
def test_missing_application_type_is_filled(
    source: str,
    answer: str,
    expected: str,
) -> None:
    resolved = ConversationResolver().resolve(
        answer,
        history=_pending_history(source, _missing_plan("application_type")),
    )

    assert resolved.resolved_question == expected
    assert resolved.inherited_slots == ("application_type",)


def test_missing_subject_builds_standalone_question() -> None:
    resolved = ConversationResolver().resolve(
        "Внутренним документооборотом",
        history=_pending_history(
            "Кто этим занимается?",
            _missing_plan("subject", kind="missing_subject"),
        ),
    )

    assert (
        resolved.resolved_question
        == "Кто занимается внутренним документооборотом?"
    )
    assert resolved.inherited_slots == ("subject",)


@pytest.mark.parametrize(
    ("source", "plan", "question"),
    [
        ("Как оформить КП?", _kp_plan(), "А кто отвечает за таможню?"),
        (
            "Кому отдать документы?",
            _missing_plan("document_type"),
            "Какой статус заказа №123?",
        ),
        (
            "Куда направить заявление?",
            _missing_plan("application_type"),
            "Что сейчас есть на складе?",
        ),
    ],
)
def test_explicit_new_question_clears_pending_context(
    source: str,
    plan: ClarificationPlan,
    question: str,
) -> None:
    resolved = ConversationResolver().resolve(
        question,
        history=_pending_history(source, plan),
    )

    assert resolved.resolved_question == question
    assert resolved.topic_changed is True
    assert resolved.is_follow_up is False
    assert resolved.inherited_slots == ()
    assert resolved.resolution_kind == "topic_change"


@pytest.mark.parametrize(
    ("source", "plan", "answer"),
    [
        ("Как оформить КП?", _kp_plan(), "Не знаю"),
        ("Кому отдать документы?", _missing_plan("document_type"), "Потом"),
    ],
)
def test_invalid_slot_answer_is_not_invented(
    source: str,
    plan: ClarificationPlan,
    answer: str,
) -> None:
    resolved = ConversationResolver().resolve(
        answer,
        history=_pending_history(source, plan),
    )

    assert resolved.resolved_question == answer
    assert resolved.resolution_kind == "unresolved_follow_up"
    assert resolved.inherited_slots == ()


def test_no_history_does_not_invent_predicate() -> None:
    resolved = ConversationResolver().resolve("Коммерческое предложение")

    assert resolved.resolved_question == "Коммерческое предложение"
    assert resolved.resolution_kind == "standalone"


def test_pending_state_is_active_only_when_last_turn_is_structured_clarification() -> None:
    history = _pending_history("Как оформить КП?", _kp_plan())
    history.extend(
        [
            {"role": "user", "content": "Коммерческое предложение"},
            {
                "role": "assistant",
                "content": "В базе нет полной инструкции.",
                "metadata": {"pending_clarification": None},
            },
        ]
    )

    resolved = ConversationResolver().resolve(
        "Кто отвечает за рабочие места?",
        history=history,
    )

    assert resolved.resolution_kind == "standalone"
    assert resolved.inherited_slots == ()
    assert resolved.conversation_history_used is False


def test_old_pending_state_outside_history_window_is_ignored() -> None:
    history = _pending_history("Как оформить КП?", _kp_plan())
    for index in range(4):
        history.extend(
            [
                {"role": "user", "content": f"Новый вопрос {index}?"},
                {
                    "role": "assistant",
                    "content": f"Ответ {index}",
                    "metadata": {"pending_clarification": None},
                },
            ]
        )

    context = conversation_context_from_history(history, max_turns=4)
    resolved = ConversationResolver(max_turns=4).resolve(
        "Коммерческое предложение",
        conversation_context=context,
    )

    assert context.pending_clarification is None
    assert resolved.resolution_kind == "standalone"


def test_pending_serialization_round_trip() -> None:
    pending = PendingClarification("Как оформить КП?", _kp_plan())

    restored = deserialize_pending_clarification(
        serialize_pending_clarification(pending)
    )

    assert restored == pending


def test_conversation_session_preserves_order_and_resets() -> None:
    session = ConversationSession()

    session.append_exchange(
        "Как оформить КП?",
        "Уточните значение КП.",
        assistant_metadata={"pending_clarification": {"test": True}},
    )

    assert [message["role"] for message in session.messages] == [
        "user",
        "assistant",
    ]
    assert session.messages[1]["metadata"]["pending_clarification"] == {
        "test": True
    }

    session.reset()
    assert session.messages == []


def test_explicit_conversation_context_is_supported() -> None:
    context = ConversationContext(
        pending_clarification=PendingClarification(
            "Как оформить КП?",
            _kp_plan(),
        )
    )

    resolved = ConversationResolver().resolve(
        "Коммерческое предложение",
        conversation_context=context,
    )

    assert resolved.resolved_question == "Как оформить коммерческое предложение?"
