"""Deterministic finalization of the requested fact type."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence


ALLOWED_REQUESTED_FACT_TYPES = frozenset(
    {
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
    }
)
RESOLUTION_STATUSES = frozenset(
    {
        "unchanged_explicit",
        "resolved_from_structure",
        "resolved_from_intent",
        "ambiguous",
        "insufficient_signals",
    }
)
_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_PROCEDURAL_OR_POLICY_INTENTS = frozenset(
    {
        "business_trip",
        "contract_approval",
        "document_flow",
        "equipment_it_request",
        "order_disposition",
        "task_management",
        "vacation",
        "weekly_planning",
        "written_communication",
        "zrs_approval",
    }
)
_INTENT_FACT_TYPES: Mapping[str, str] = {
    "roles_responsibility": "responsible_person",
    "one_c_operational_lookup": "current_value",
    "company_ckp": "definition",
    "company_identity": "definition",
    "zrs_definition": "definition",
}
_ANSWER_SHAPE_FACT_TYPES: Mapping[str, str] = {
    "definition": "definition",
    "procedure": "procedure",
    "list": "list",
    "comparison": "comparison",
}


@dataclass(frozen=True)
class RequestedFactTypeResolution:
    """One reproducible fact-type finalization decision."""

    initial_fact_type: str = "unknown"
    resolved_fact_type: str = "unknown"
    resolution_status: str = "insufficient_signals"
    matched_signals: tuple[str, ...] = ()
    rejected_candidates: tuple[str, ...] = ()
    decision_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.initial_fact_type not in ALLOWED_REQUESTED_FACT_TYPES:
            raise ValueError(
                f"Unsupported initial fact type: {self.initial_fact_type!r}"
            )
        if self.resolved_fact_type not in ALLOWED_REQUESTED_FACT_TYPES:
            raise ValueError(
                f"Unsupported resolved fact type: {self.resolved_fact_type!r}"
            )
        if self.resolution_status not in RESOLUTION_STATUSES:
            raise ValueError(
                f"Unsupported resolution status: {self.resolution_status!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_fact_type": self.initial_fact_type,
            "resolved_fact_type": self.resolved_fact_type,
            "resolution_status": self.resolution_status,
            "matched_signals": list(self.matched_signals),
            "rejected_candidates": list(self.rejected_candidates),
            "decision_reasons": list(self.decision_reasons),
        }


class RequestedFactTypeResolver:
    """Finalize a draft fact type without retrieval or another LLM call."""

    def resolve(
        self,
        *,
        normalized_question: str,
        intent: str,
        subject: str,
        entities: Sequence[str] = (),
        answer_shape: str,
        draft_requested_fact_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> RequestedFactTypeResolution:
        del entities, metadata  # Reserved structured inputs for compatible growth.
        question = _normalize(normalized_question)
        initial = (
            draft_requested_fact_type
            if draft_requested_fact_type in ALLOWED_REQUESTED_FACT_TYPES
            else "unknown"
        )
        structural = _structural_candidates(
            question,
            intent=intent,
            subject=subject,
        )
        structural_types = _ordered_unique(
            candidate.fact_type for candidate in structural
        )
        structural_signals = tuple(
            candidate.signal for candidate in structural
        )

        if len(structural_types) > 1:
            return RequestedFactTypeResolution(
                initial_fact_type=initial,
                resolved_fact_type="unknown",
                resolution_status="ambiguous",
                matched_signals=structural_signals,
                rejected_candidates=tuple(structural_types),
                decision_reasons=("conflicting_structural_fact_types",),
            )

        if structural_types:
            resolved = structural_types[0]
            if initial == resolved:
                return RequestedFactTypeResolution(
                    initial_fact_type=initial,
                    resolved_fact_type=resolved,
                    resolution_status="unchanged_explicit",
                    matched_signals=structural_signals,
                    decision_reasons=("explicit_fact_type_matches_structure",),
                )
            rejected = (initial,) if initial != "unknown" else ()
            return RequestedFactTypeResolution(
                initial_fact_type=initial,
                resolved_fact_type=resolved,
                resolution_status="resolved_from_structure",
                matched_signals=structural_signals,
                rejected_candidates=rejected,
                decision_reasons=(
                    _primary_structure_reason(structural),
                ),
            )

        if initial != "unknown":
            return RequestedFactTypeResolution(
                initial_fact_type=initial,
                resolved_fact_type=initial,
                resolution_status="unchanged_explicit",
                matched_signals=(f"explicit_fact_type:{initial}",),
                decision_reasons=("explicit_fact_type_preserved",),
            )

        intent_candidates = _intent_candidates(
            intent=intent,
            subject=subject,
            answer_shape=answer_shape,
        )
        intent_types = _ordered_unique(
            candidate.fact_type for candidate in intent_candidates
        )
        intent_signals = tuple(
            candidate.signal for candidate in intent_candidates
        )
        if len(intent_types) > 1:
            return RequestedFactTypeResolution(
                initial_fact_type=initial,
                resolved_fact_type="unknown",
                resolution_status="ambiguous",
                matched_signals=intent_signals,
                rejected_candidates=tuple(intent_types),
                decision_reasons=("conflicting_intent_fact_types",),
            )
        if intent_types:
            return RequestedFactTypeResolution(
                initial_fact_type=initial,
                resolved_fact_type=intent_types[0],
                resolution_status="resolved_from_intent",
                matched_signals=intent_signals,
                decision_reasons=("intent_or_answer_shape_fact_type",),
            )

        return RequestedFactTypeResolution(
            initial_fact_type=initial,
            resolved_fact_type="unknown",
            resolution_status="insufficient_signals",
            matched_signals=(),
            rejected_candidates=(),
            decision_reasons=("no_unambiguous_fact_type_signal",),
        )


@dataclass(frozen=True)
class _Candidate:
    fact_type: str
    signal: str
    reason: str


def _structural_candidates(
    question: str,
    *,
    intent: str,
    subject: str,
) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []

    if _contains_any(
        question,
        (
            "кто руководит",
            "кто главный",
            "кто начальник",
            "руководитель какого",
        ),
    ):
        _add_candidate(
            candidates,
            "unit_head",
            "question_pattern:unit_head",
            "explicit_unit_head_question",
        )
    if _contains_any(
        question,
        (
            "к кому обратиться",
            "к кому обращаться",
            "к кому идти",
            "кто контактное лицо",
            "контактное лицо",
        ),
    ):
        _add_candidate(
            candidates,
            "primary_contact",
            "question_pattern:primary_contact",
            "explicit_primary_contact_question",
        )
    if _responsibility_question(question):
        _add_candidate(
            candidates,
            "responsible_person",
            "question_pattern:responsibility",
            "explicit_responsibility_question",
        )
    if _contains_any(
        question,
        (
            "кому подавать",
            "кому подать",
            "кому отдать",
            "куда подавать",
            "куда подать",
            "куда отдать",
            "куда направить",
        ),
    ):
        _add_candidate(
            candidates,
            "document_recipient",
            "question_pattern:document_recipient",
            "explicit_document_recipient_question",
        )
    if _procedure_question(question):
        _add_candidate(
            candidates,
            "procedure",
            "question_pattern:procedure",
            "explicit_procedure_question",
        )
    if (
        _normative_action_question(question)
        and intent in _PROCEDURAL_OR_POLICY_INTENTS
        and bool(_normalize(subject))
    ):
        _add_candidate(
            candidates,
            "procedure",
            "question_pattern:normative_action",
            "normative_action_question",
        )
    if _contains_any(question, ("что такое", "что означает", "что значит")):
        _add_candidate(
            candidates,
            "definition",
            "question_pattern:definition",
            "explicit_definition_question",
        )
    if _contains_any(
        question,
        (
            "чем отличается",
            "сравни",
            "разница между",
            "одно и то же",
            "то же самое",
        ),
    ):
        _add_candidate(
            candidates,
            "comparison",
            "question_pattern:comparison",
            "explicit_comparison_question",
        )
    if _contains_any(
        question,
        (
            "какие правила",
            "какие подразделения",
            "какие отделы",
            "перечисли",
            "список ",
            "что у нас по ",
        ),
    ):
        _add_candidate(
            candidates,
            "list",
            "question_pattern:list",
            "explicit_list_question",
        )
    if _current_status_question(question):
        _add_candidate(
            candidates,
            "current_status",
            "question_pattern:current_status",
            "explicit_current_status_question",
        )
    if _availability_question(question):
        _add_candidate(
            candidates,
            "availability",
            "question_pattern:availability",
            "explicit_availability_question",
        )
    if _price_question(question):
        _add_candidate(
            candidates,
            "price",
            "question_pattern:price",
            "explicit_price_question",
        )
    specific_current_types = {
        candidate.fact_type
        for candidate in candidates
        if candidate.fact_type
        in {"current_status", "availability", "price"}
    }
    if not specific_current_types and _current_value_question(question):
        _add_candidate(
            candidates,
            "current_value",
            "question_pattern:current_value",
            "explicit_current_value_question",
        )

    return tuple(candidates)


def _intent_candidates(
    *,
    intent: str,
    subject: str,
    answer_shape: str,
) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    answer_shape_type = _ANSWER_SHAPE_FACT_TYPES.get(answer_shape)
    if answer_shape_type:
        _add_candidate(
            candidates,
            answer_shape_type,
            f"answer_shape:{answer_shape}",
            "answer_shape_fact_type",
        )
    intent_type = _INTENT_FACT_TYPES.get(intent)
    if intent_type:
        _add_candidate(
            candidates,
            intent_type,
            f"intent:{intent}",
            "intent_fact_type",
        )
    if (
        intent in _PROCEDURAL_OR_POLICY_INTENTS
        and _normalize(subject)
        and answer_shape not in {"definition", "list", "comparison"}
    ):
        _add_candidate(
            candidates,
            "procedure",
            f"intent:{intent}",
            "procedural_intent_with_subject",
        )
    return tuple(candidates)


def _responsibility_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "кто отвечает",
            "кто занимается",
            "кто ведет",
            "чья ответственность",
            "кто выпускает",
            "какой отдел занимается",
        ),
    )


def _procedure_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "как оформить",
            "как получить",
            "как проходит",
            "как согласовать",
            "как создать",
            "как подать",
            "как завести",
            "как запросить",
            "как установить",
            "что делать",
            "порядок ",
        ),
    )


def _normative_action_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "можно ли",
            "допустимо ли",
            "разрешено ли",
            "нужно ли",
            "обязательно ли",
            "следует ли",
            "требуется ли",
        ),
    )


def _current_status_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "какой статус",
            "статус заказа",
            "на каком этапе",
            "отгружен ли",
            "отгрузили ли",
            "оплачен ли",
            "согласуют мое уже поданное",
            "когда поставщик отгрузит",
            "какая отгрузка по заказу",
        ),
    )


def _availability_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "что сейчас есть",
            "есть ли остатки",
            "остатки на складе",
            "в наличии",
            "наличие ",
            "покажи остатки",
        ),
    )


def _price_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "текущая цена",
            "текущую цену",
            "какая цена",
            "найди цену",
            "цена",
            "цену",
            "стоимость детали",
            "сколько сейчас стоит",
        ),
    )


def _current_value_question(question: str) -> bool:
    if _contains_any(
        question,
        (
            "найди контрагента",
            "найди деталь",
            "покажи счета",
            "покажи номенклатуру",
        ),
    ):
        return True
    return (
        _contains_any(question, ("сейчас", "текущ", "на данный момент"))
        and _contains_any(
            question,
            (
                "заказ",
                "склад",
                "отгруз",
                "оплат",
                "счет",
                "постав",
                "контрагент",
                "номенклатур",
                "цен",
                "остат",
            ),
        )
    )


def _add_candidate(
    candidates: list[_Candidate],
    fact_type: str,
    signal: str,
    reason: str,
) -> None:
    item = _Candidate(fact_type=fact_type, signal=signal, reason=reason)
    if item not in candidates:
        candidates.append(item)


def _primary_structure_reason(candidates: Collection[_Candidate]) -> str:
    for candidate in candidates:
        return candidate.reason
    return "resolved_from_structure"


def _ordered_unique(values: Collection[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _contains_any(value: str, terms: Collection[str]) -> bool:
    return any(term in value for term in terms)


def _normalize(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").casefold().replace("ё", "е")))
