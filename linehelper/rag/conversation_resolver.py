"""Deterministic conversation resolution before Query Analyzer.

The resolver is deliberately limited to structured clarification state and
high-confidence topic changes. It does not attempt general entity resolution
or use an LLM.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from linehelper.rag.query_analyzer import ClarificationPlan


MAX_CONVERSATION_TURNS = 6
RESOLUTION_KINDS = frozenset(
    {
        "standalone",
        "clarification_answer",
        "missing_slot_answer",
        "explicit_follow_up",
        "topic_change",
        "unresolved_follow_up",
    }
)

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
_EXPLICIT_QUESTION_RE = re.compile(
    r"^(?:а\s+|и\s+)?(?:"
    r"кто|что|где|куда|кому|когда|как|какой|какая|какие|какое|"
    r"можно\s+ли|нужно\s+ли|надо\s+ли|допустимо\s+ли|"
    r"отгружен\s+ли|оплачен\s+ли|есть\s+ли"
    r")\b",
    re.IGNORECASE,
)
_INVALID_SLOT_ANSWERS = frozenset(
    {
        "не знаю",
        "не уверен",
        "не уверена",
        "неважно",
        "не важно",
        "потом",
        "позже",
        "без разницы",
        "отмена",
        "отменить",
        "никакие",
        "никакой",
    }
)


@dataclass(frozen=True)
class ConversationTurn:
    """One bounded history turn used by the resolver."""

    role: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingClarification:
    """Structured clarification that may be consumed by the next user turn."""

    source_question: str
    plan: ClarificationPlan


@dataclass(frozen=True)
class ConversationContext:
    """Bounded conversation state available before the current user turn."""

    turns: tuple[ConversationTurn, ...] = ()
    pending_clarification: PendingClarification | None = None
    last_completed_subject: str | None = None


@dataclass(frozen=True)
class ResolvedQuery:
    """Standalone question and diagnostics produced before query analysis."""

    original_question: str
    resolved_question: str
    is_follow_up: bool = False
    topic_changed: bool = False
    inherited_slots: tuple[str, ...] = ()
    resolution_kind: str = "standalone"
    resolution_confidence: float = 1.0
    resolution_reasons: tuple[str, ...] = ()
    conversation_history_used: bool = False
    conversation_turns_used: int = 0
    pending_clarification_before: PendingClarification | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe native diagnostics."""
        result = asdict(self)
        result["inherited_slots"] = list(self.inherited_slots)
        result["resolution_reasons"] = list(self.resolution_reasons)
        result["pending_clarification_before"] = serialize_pending_clarification(
            self.pending_clarification_before
        )
        return result


class ConversationResolver:
    """Resolve only provable structured follow-ups and explicit topic changes."""

    def __init__(self, *, max_turns: int = MAX_CONVERSATION_TURNS) -> None:
        self.max_turns = max(1, int(max_turns))

    def resolve(
        self,
        question: str,
        *,
        history: Sequence[ConversationTurn | Mapping[str, Any]] | None = None,
        conversation_context: ConversationContext | None = None,
    ) -> ResolvedQuery:
        """Return a standalone question without inventing missing slot values."""
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("question must not be empty")

        context = conversation_context or conversation_context_from_history(
            history,
            max_turns=self.max_turns,
        )
        pending = context.pending_clarification
        if pending is None:
            return ResolvedQuery(
                original_question=clean_question,
                resolved_question=clean_question,
                resolution_reasons=("no_active_pending_clarification",),
            )

        turns_used = len(context.turns)
        if _is_explicit_topic_change(clean_question):
            return ResolvedQuery(
                original_question=clean_question,
                resolved_question=clean_question,
                topic_changed=True,
                resolution_kind="topic_change",
                resolution_confidence=1.0,
                resolution_reasons=("explicit_new_predicate_and_subject",),
                conversation_history_used=True,
                conversation_turns_used=turns_used,
                pending_clarification_before=pending,
            )

        resolved = _resolve_pending_clarification(clean_question, pending)
        if resolved is not None:
            resolved_question, inherited_slot, reason = resolved
            kind = (
                "clarification_answer"
                if pending.plan.kind in {"abbreviation", "lexical_ambiguity"}
                else "missing_slot_answer"
            )
            return ResolvedQuery(
                original_question=clean_question,
                resolved_question=resolved_question,
                is_follow_up=True,
                inherited_slots=(inherited_slot,),
                resolution_kind=kind,
                resolution_confidence=1.0,
                resolution_reasons=(reason,),
                conversation_history_used=True,
                conversation_turns_used=turns_used,
                pending_clarification_before=pending,
            )

        return ResolvedQuery(
            original_question=clean_question,
            resolved_question=clean_question,
            is_follow_up=True,
            resolution_kind="unresolved_follow_up",
            resolution_confidence=1.0,
            resolution_reasons=("answer_does_not_fill_pending_slot",),
            conversation_history_used=True,
            conversation_turns_used=turns_used,
            pending_clarification_before=pending,
        )


class ConversationSession:
    """In-memory helper for an interactive UI or CLI session."""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Return a defensive copy in original order."""
        return [_copy_message(message) for message in self._messages]

    def append_exchange(
        self,
        user_content: str,
        assistant_content: str,
        *,
        assistant_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Store a completed exchange; failed calls should not invoke this."""
        self._messages.append({"role": "user", "content": user_content})
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_content,
        }
        if assistant_metadata:
            assistant["metadata"] = dict(assistant_metadata)
        self._messages.append(assistant)

    def reset(self) -> None:
        """Start a new independent dialog."""
        self._messages.clear()


def conversation_context_from_history(
    history: Sequence[ConversationTurn | Mapping[str, Any]] | None,
    *,
    max_turns: int = MAX_CONVERSATION_TURNS,
) -> ConversationContext:
    """Normalize and bound history, accepting only an immediately pending state."""
    if not history:
        return ConversationContext()

    normalized = tuple(
        turn
        for item in history[-max(1, int(max_turns)) :]
        if (turn := _coerce_turn(item)) is not None
    )
    if not normalized:
        return ConversationContext()

    pending: PendingClarification | None = None
    last = normalized[-1]
    if last.role == "assistant":
        pending = _pending_from_metadata(last.metadata)

    last_subject: str | None = None
    for turn in reversed(normalized):
        subject = turn.metadata.get("subject")
        if isinstance(subject, str) and subject.strip():
            last_subject = subject.strip()
            break

    return ConversationContext(
        turns=normalized,
        pending_clarification=pending,
        last_completed_subject=last_subject,
    )


def pending_clarification_from_plan(
    source_question: str,
    plan: ClarificationPlan | None,
) -> PendingClarification | None:
    """Create pending state only from a validated required clarification."""
    if plan is None or not plan.required or not plan.question:
        return None
    return PendingClarification(source_question=source_question, plan=plan)


def serialize_pending_clarification(
    pending: PendingClarification | None,
) -> dict[str, Any] | None:
    """Return a stable JSON-safe representation for history metadata."""
    if pending is None:
        return None
    return {
        "source_question": pending.source_question,
        "plan": asdict(pending.plan),
    }


def deserialize_pending_clarification(value: Any) -> PendingClarification | None:
    """Parse pending state defensively from UI/CLI history metadata."""
    if isinstance(value, PendingClarification):
        return value
    if not isinstance(value, Mapping):
        return None
    source_question = value.get("source_question")
    raw_plan = value.get("plan")
    if not isinstance(source_question, str) or not source_question.strip():
        return None
    from linehelper.rag.query_analyzer import ClarificationPlan

    if isinstance(raw_plan, ClarificationPlan):
        plan = raw_plan
    elif isinstance(raw_plan, Mapping):
        try:
            plan = ClarificationPlan(
                required=bool(raw_plan.get("required", False)),
                kind=str(raw_plan.get("kind") or "none"),
                ambiguity_span=_optional_text(raw_plan.get("ambiguity_span")),
                candidate_meanings=_text_list(raw_plan.get("candidate_meanings")),
                missing_slots=_text_list(raw_plan.get("missing_slots")),
                question=_optional_text(raw_plan.get("question")),
                confidence=_confidence(raw_plan.get("confidence")),
            )
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not plan.required or not plan.question:
        return None
    return PendingClarification(source_question=source_question.strip(), plan=plan)


def conversation_diagnostics(
    resolved: ResolvedQuery,
    *,
    pending_after: PendingClarification | None,
) -> dict[str, Any]:
    """Build native result diagnostics, including state before and after."""
    return {
        "original_question": resolved.original_question,
        "resolved_question": resolved.resolved_question,
        "conversation_history_used": resolved.conversation_history_used,
        "conversation_turns_used": resolved.conversation_turns_used,
        "is_follow_up": resolved.is_follow_up,
        "topic_changed": resolved.topic_changed,
        "resolution_kind": resolved.resolution_kind,
        "inherited_slots": list(resolved.inherited_slots),
        "resolution_confidence": resolved.resolution_confidence,
        "resolution_reasons": list(resolved.resolution_reasons),
        "pending_clarification_before": serialize_pending_clarification(
            resolved.pending_clarification_before
        ),
        "pending_clarification_after": serialize_pending_clarification(
            pending_after
        ),
    }


def assistant_history_metadata(
    *,
    conversation: Mapping[str, Any] | None,
    query_plan: Mapping[str, Any] | None,
    response_kind: str,
) -> dict[str, Any]:
    """Return compact structured metadata for the next resolver turn."""
    conversation_data = dict(conversation or {})
    query_plan_data = dict(query_plan or {})
    return {
        "pending_clarification": conversation_data.get(
            "pending_clarification_after"
        ),
        "resolved_question": conversation_data.get("resolved_question"),
        "subject": query_plan_data.get("subject"),
        "response_kind": response_kind,
    }


def _resolve_pending_clarification(
    answer: str,
    pending: PendingClarification,
) -> tuple[str, str, str] | None:
    if _is_invalid_slot_answer(answer):
        return None
    plan = pending.plan
    if plan.kind in {"abbreviation", "lexical_ambiguity"}:
        meaning = _match_candidate_meaning(answer, plan.candidate_meanings)
        if meaning is None or not plan.ambiguity_span:
            return None
        replaced = re.sub(
            rf"(?<!\w){re.escape(plan.ambiguity_span)}(?!\w)",
            meaning.casefold(),
            pending.source_question,
            count=1,
            flags=re.IGNORECASE,
        )
        if replaced == pending.source_question:
            return None
        return (
            _ensure_question(replaced),
            f"meaning_of_{plan.ambiguity_span}",
            "matched_clarification_candidate",
        )

    slots = [slot for slot in plan.missing_slots if slot.strip()]
    if not slots:
        return None
    slot = slots[0]
    resolved = _fill_missing_slot(pending.source_question, answer, slot)
    if resolved is None:
        return None
    return resolved, slot, "filled_structured_missing_slot"


def _fill_missing_slot(source_question: str, answer: str, slot: str) -> str | None:
    clean_answer = _clean_answer_phrase(answer)
    if not clean_answer:
        return None
    lowered_answer = _lower_initial(clean_answer)

    if slot == "document_type":
        if _normalize(clean_answer) in {"документ", "документы", "бумаги"}:
            return None
        replacement = (
            lowered_answer
            if re.search(r"\b(?:документ|бумаг)\w*\b", lowered_answer)
            else f"{lowered_answer} документы"
        )
        resolved, count = re.subn(
            r"\b(?:документ|бумаг)\w*\b",
            replacement,
            source_question,
            count=1,
            flags=re.IGNORECASE,
        )
        return _ensure_question(resolved) if count else None

    if slot == "application_type":
        if _normalize(clean_answer) in {"заявление", "заявления"}:
            return None
        suffix = lowered_answer
        if re.search(r"\bзаявлен\w*\b", suffix, re.IGNORECASE):
            suffix = re.sub(
                r"^.*?\bзаявлен\w*\b",
                "",
                suffix,
                count=1,
                flags=re.IGNORECASE,
            ).strip()
        if not suffix:
            return None
        resolved, count = re.subn(
            r"\bзаявлен\w*\b",
            lambda match: f"{match.group(0)} {suffix}",
            source_question,
            count=1,
            flags=re.IGNORECASE,
        )
        return _ensure_question(resolved) if count else None

    if slot == "subject":
        if re.search(
            r"\bкто\s+(?:этим|это)\s+занимается\b",
            source_question,
            re.IGNORECASE,
        ):
            resolved = re.sub(
                r"\bкто\s+(?:этим|это)\s+занимается\b",
                f"Кто занимается {lowered_answer}",
                source_question,
                count=1,
                flags=re.IGNORECASE,
            )
            return _ensure_question(resolved)
        resolved, count = re.subn(
            r"\b(?:этим|это|этого)\b",
            lowered_answer,
            source_question,
            count=1,
            flags=re.IGNORECASE,
        )
        return _ensure_question(resolved) if count else None

    return _ensure_question(f"{source_question.rstrip(' ?')} — {lowered_answer}")


def _match_candidate_meaning(
    answer: str,
    candidate_meanings: Sequence[str],
) -> str | None:
    normalized_answer = _normalize_candidate_answer(answer)
    if not normalized_answer:
        return None
    matches: list[str] = []
    for candidate in candidate_meanings:
        normalized_candidate = _normalize(candidate)
        if not normalized_candidate:
            continue
        if normalized_answer == normalized_candidate:
            matches.append(candidate.strip())
            continue
        answer_tokens = _WORD_RE.findall(normalized_answer)
        if (
            len(normalized_answer) >= 4
            and answer_tokens
            and (
                normalized_answer in normalized_candidate
                or normalized_candidate in normalized_answer
            )
        ):
            matches.append(candidate.strip())
    unique = {value.casefold(): value for value in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _is_explicit_topic_change(question: str) -> bool:
    return _EXPLICIT_QUESTION_RE.search(_normalize(question)) is not None


def _is_invalid_slot_answer(answer: str) -> bool:
    normalized = _normalize(answer)
    if not normalized or normalized in _INVALID_SLOT_ANSWERS:
        return True
    if normalized.startswith("не знаю") or normalized.startswith("потом"):
        return True
    return not any(character.isalpha() for character in normalized)


def _normalize_candidate_answer(value: str) -> str:
    normalized = _normalize(value)
    normalized = re.sub(
        r"^(?:это|имею\s+в\s+виду|я\s+про|речь\s+про|про)\s+",
        "",
        normalized,
    )
    return normalized.strip()


def _clean_answer_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n?!.,;:«»\"")).strip()


def _lower_initial(value: str) -> str:
    return value[:1].lower() + value[1:] if value else value


def _ensure_question(value: str) -> str:
    clean = re.sub(r"\s+", " ", value.strip())
    clean = re.sub(r"\s+([?!.,;:])", r"\1", clean)
    if clean.endswith("?"):
        return clean
    return clean.rstrip("!.") + "?"


def _normalize(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[?!.,;:«»\"'()]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _coerce_turn(value: ConversationTurn | Mapping[str, Any]) -> ConversationTurn | None:
    if isinstance(value, ConversationTurn):
        return value
    if not isinstance(value, Mapping):
        return None
    role = str(value.get("role") or "").strip()
    content = str(value.get("content") or "").strip()
    if role not in {"user", "assistant", "system"} or not content:
        return None
    metadata = value.get("metadata")
    normalized_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    legacy_result = value.get("result")
    if legacy_result is not None and "conversation" not in normalized_metadata:
        legacy_conversation = getattr(legacy_result, "conversation", None)
        if isinstance(legacy_conversation, Mapping):
            normalized_metadata["conversation"] = dict(legacy_conversation)
    return ConversationTurn(
        role=role,
        content=content,
        metadata=normalized_metadata,
    )


def _pending_from_metadata(metadata: Mapping[str, Any]) -> PendingClarification | None:
    pending = deserialize_pending_clarification(metadata.get("pending_clarification"))
    if pending is not None:
        return pending
    conversation = metadata.get("conversation")
    if isinstance(conversation, Mapping):
        return deserialize_pending_clarification(
            conversation.get("pending_clarification_after")
        )
    return None


def _copy_message(message: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(message)
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        result["metadata"] = dict(metadata)
    return result


def _optional_text(value: Any) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0
