from __future__ import annotations

import json

from linehelper.rag.requested_fact_type_resolver import (
    RequestedFactTypeResolver,
)


def test_explicit_fact_type_remains_unchanged() -> None:
    resolution = _resolve(
        "Кто отвечает за доставку?",
        intent="roles_responsibility",
        subject="доставка",
        draft="responsible_person",
    )

    assert resolution.initial_fact_type == "responsible_person"
    assert resolution.resolved_fact_type == "responsible_person"
    assert resolution.resolution_status == "unchanged_explicit"


def test_unknown_is_resolved_from_structural_signals() -> None:
    resolution = _resolve(
        "Допустимо ли согласовывать договор устно?",
        intent="contract_approval",
        subject="согласование договора",
    )

    assert resolution.resolved_fact_type == "procedure"
    assert resolution.resolution_status == "resolved_from_structure"
    assert "question_pattern:normative_action" in resolution.matched_signals


def test_unknown_is_resolved_from_intent() -> None:
    resolution = _resolve(
        "Нужен владелец этого процесса",
        intent="roles_responsibility",
        subject="процесс",
    )

    assert resolution.resolved_fact_type == "responsible_person"
    assert resolution.resolution_status == "resolved_from_intent"
    assert resolution.matched_signals == ("intent:roles_responsibility",)


def test_conflicting_fact_types_remain_ambiguous() -> None:
    resolution = _resolve(
        "Кто отвечает и какой статус заказа?",
        intent="one_c_operational_lookup",
        subject="заказ",
    )

    assert resolution.resolved_fact_type == "unknown"
    assert resolution.resolution_status == "ambiguous"
    assert set(resolution.rejected_candidates) == {
        "responsible_person",
        "current_status",
    }


def test_insufficient_signals_preserve_unknown() -> None:
    resolution = _resolve(
        "Корпоративный вопрос",
        intent="unknown",
        subject="корпоративный вопрос",
    )

    assert resolution.resolved_fact_type == "unknown"
    assert resolution.resolution_status == "insufficient_signals"
    assert resolution.matched_signals == ()


def test_resolution_diagnostics_are_deterministic_and_serializable() -> None:
    resolver = RequestedFactTypeResolver()
    kwargs = {
        "normalized_question": "Какие правила работы действуют?",
        "intent": "document_flow",
        "subject": "правила работы",
        "entities": (),
        "answer_shape": "general",
        "draft_requested_fact_type": "unknown",
        "metadata": {"source": "unit_test"},
    }

    first = resolver.resolve(**kwargs).to_dict()
    second = resolver.resolve(**kwargs).to_dict()
    payload = json.loads(json.dumps(first, ensure_ascii=False))

    assert first == second
    assert payload["resolved_fact_type"] == "list"
    assert payload["resolution_status"] == "resolved_from_structure"
    assert payload["decision_reasons"] == ["explicit_list_question"]


def _resolve(
    question: str,
    *,
    intent: str,
    subject: str,
    draft: str = "unknown",
):
    return RequestedFactTypeResolver().resolve(
        normalized_question=question,
        intent=intent,
        subject=subject,
        entities=(),
        answer_shape="general",
        draft_requested_fact_type=draft,
        metadata={},
    )
