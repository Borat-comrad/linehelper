"""Deterministic evidence planning and assessment for selected RAG context."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from linehelper.rag.retriever import RetrievedChunk

if TYPE_CHECKING:
    from linehelper.rag.context_composer import ContextPlan
    from linehelper.rag.query_analyzer import QueryPlan


EVIDENCE_MODES = frozenset(
    {
        "full_answer",
        "partial_answer",
        "insufficient_evidence",
    }
)
_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
_DEADLINE_RE = re.compile(
    r"\b\d+\s+(?:календарн\w*\s+|рабоч\w*\s+)?(?:дн\w*|сут\w*)\b",
    re.IGNORECASE,
)
_GENERIC_SUBJECT_TERMS = frozenset(
    {
        "вопрос",
        "взять",
        "главный",
        "делать",
        "действует",
        "действуют",
        "для",
        "заявление",
        "как",
        "кто",
        "кому",
        "компания",
        "компании",
        "можно",
        "на",
        "надо",
        "нового",
        "новое",
        "новый",
        "нужно",
        "оформить",
        "оформление",
        "под",
        "получить",
        "получение",
        "после",
        "правила",
        "правило",
        "процедура",
        "работает",
        "работать",
        "сделать",
        "что",
        "хочу",
    }
)
_PROCEDURAL_LOGICAL_TYPES = frozenset(
    {
        "procedure",
        "policy_rule",
        "example",
        "reference",
    }
)
_STRUCTURED_RESPONSIBILITY_TYPES = frozenset(
    {
        "document_recipient",
        "employee_role",
        "organization_unit",
        "primary_contact",
        "responsibility_route",
        "role_combination",
        "unit_head",
    }
)

# Declarative concept vocabulary used only for subject-to-evidence compatibility.
# It does not map questions to answers, sources, records or chunk identifiers.
_SUBJECT_CONCEPTS: tuple[
    tuple[tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (
        ("оборудован", "компьютер", "имуще", "рабоч мест"),
        (
            "оборудован",
            "компьютер",
            "имуще",
            "рабоч мест",
            "хоз часть",
            "хозчаст",
            "хозяйствен",
        ),
    ),
    (
        ("отгруз", "доставк"),
        ("отгруз", "доставк"),
    ),
    (
        ("документооборот", "документоборот"),
        ("документооборот", "документоборот"),
    ),
    (
        ("закуп",),
        ("закуп",),
    ),
)


@dataclass(frozen=True)
class EvidenceRequirement:
    """One minimal answer requirement and its deterministic assessment."""

    requirement_id: str
    requirement_type: str
    description: str
    required: bool = True
    expected_coverage_key: str | None = None
    supported: bool = False
    supporting_chunk_ids: tuple[int | str, ...] = ()
    assessment_reasons: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "requirement_type": self.requirement_type,
            "description": self.description,
            "required": self.required,
            "expected_coverage_key": self.expected_coverage_key,
            "supported": self.supported,
            "supporting_chunk_ids": list(self.supporting_chunk_ids),
            "assessment_reasons": list(self.assessment_reasons),
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class EvidencePlan:
    """Question-shaped evidence requirements evaluated over selected context."""

    requested_fact_type: str
    answer_shape: str
    evidence_requirements: tuple[EvidenceRequirement, ...]
    allow_partial_answer: bool
    minimum_supported_requirements: int
    diagnostics_metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_fact_type": self.requested_fact_type,
            "answer_shape": self.answer_shape,
            "evidence_requirements": [
                requirement.to_dict()
                for requirement in self.evidence_requirements
            ],
            "allow_partial_answer": self.allow_partial_answer,
            "minimum_supported_requirements": (
                self.minimum_supported_requirements
            ),
            "diagnostics_metadata": dict(self.diagnostics_metadata),
        }


@dataclass(frozen=True)
class EvidenceDecision:
    """Evidence mode plus supporting and non-supporting selected chunks."""

    mode: str
    plan: EvidencePlan
    assessed_requirements: tuple[EvidenceRequirement, ...]
    supported_requirements: tuple[str, ...]
    unsupported_requirements: tuple[str, ...]
    supporting_chunks: tuple[RetrievedChunk, ...]
    non_supporting_chunks: tuple[RetrievedChunk, ...]
    decision_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode not in EVIDENCE_MODES:
            raise ValueError(f"Unsupported evidence mode: {self.mode!r}")

    @property
    def coverage_rate(self) -> float:
        total = len(self.assessed_requirements)
        if total == 0:
            return 0.0
        return round(len(self.supported_requirements) / total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": True,
            "evidence_plan": self.plan.to_dict(),
            "evidence_requirements": [
                requirement.to_dict()
                for requirement in self.assessed_requirements
            ],
            "evidence_decision": {
                "mode": self.mode,
                "decision_reasons": list(self.decision_reasons),
            },
            "answer_mode": self.mode,
            "mode": self.mode,
            "supporting_chunk_ids": [
                _chunk_diagnostic_id(chunk)
                for chunk in self.supporting_chunks
            ],
            "non_supporting_chunk_ids": [
                _chunk_diagnostic_id(chunk)
                for chunk in self.non_supporting_chunks
            ],
            "supported_requirements": list(self.supported_requirements),
            "unsupported_requirements": list(
                self.unsupported_requirements
            ),
            "decision_reasons": list(self.decision_reasons),
            "coverage_rate": self.coverage_rate,
        }


class EvidencePlanner:
    """Build a compact evidence plan from QueryPlan and ContextPlan."""

    def build(
        self,
        question: str,
        *,
        query_plan: QueryPlan | None,
        context_plan: ContextPlan,
    ) -> EvidencePlan:
        requested_fact_type = str(
            getattr(
                query_plan,
                "requested_fact_type",
                context_plan.requested_fact_type,
            )
            or "unknown"
        ).strip().lower()
        answer_shape = (
            "comparison"
            if _is_explicit_comparison(question)
            else context_plan.answer_shape
        )
        subject = str(getattr(query_plan, "subject", "") or "").strip()
        intent = str(getattr(query_plan, "intent", "unknown") or "unknown")

        if requested_fact_type == "document_recipient":
            requirements = (
                EvidenceRequirement(
                    requirement_id="named_document_recipient",
                    requirement_type="named_recipient",
                    description=(
                        "именованный первоначальный адресат документа"
                    ),
                    expected_coverage_key="primary_responsibility",
                ),
                EvidenceRequirement(
                    requirement_id="related_submission_procedure",
                    requirement_type="procedure_context",
                    description=(
                        "подтверждённая процедура создания или подачи документа"
                    ),
                    required=False,
                    expected_coverage_key="related_procedure",
                ),
            )
            allow_partial = True
        elif answer_shape == "list":
            requirements = (
                EvidenceRequirement(
                    requirement_id="requested_list",
                    requirement_type="distinct_list_items",
                    description="различные элементы запрошенного списка",
                    expected_coverage_key="distinct_sibling_logical_units",
                ),
            )
            allow_partial = False
        elif answer_shape == "procedure":
            items = [
                EvidenceRequirement(
                    requirement_id="primary_procedure",
                    requirement_type="procedure",
                    description="основная процедура по предмету вопроса",
                    expected_coverage_key="primary_procedure",
                )
            ]
            if _asks_for_deadline(question):
                items.append(
                    EvidenceRequirement(
                        requirement_id="procedure_deadline",
                        requirement_type="deadline",
                        description=(
                            "срок выполнения или подачи, запрошенный пользователем"
                        ),
                        expected_coverage_key="procedure_deadline",
                    )
                )
            requirements = tuple(items)
            allow_partial = len(requirements) > 1
        elif answer_shape == "responsibility":
            requirements = (
                EvidenceRequirement(
                    requirement_id="primary_responsibility",
                    requirement_type="structured_responsibility",
                    description=(
                        "структурированная ответственность по предмету вопроса"
                    ),
                    expected_coverage_key="primary_responsibility",
                ),
            )
            allow_partial = False
        else:
            requirements = (
                EvidenceRequirement(
                    requirement_id="primary_fact",
                    requirement_type="primary_fact",
                    description="основной факт по предмету вопроса",
                    expected_coverage_key="primary_fact",
                ),
            )
            allow_partial = False

        return EvidencePlan(
            requested_fact_type=requested_fact_type,
            answer_shape=answer_shape,
            evidence_requirements=requirements,
            allow_partial_answer=allow_partial,
            minimum_supported_requirements=1,
            diagnostics_metadata={
                "subject": subject,
                "intent": intent,
            },
        )


class EvidenceAssessor:
    """Evaluate selected context without an additional LLM call."""

    def assess(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        *,
        plan: EvidencePlan,
    ) -> EvidenceDecision:
        subject = str(plan.diagnostics_metadata.get("subject") or "")
        assessed: list[EvidenceRequirement] = []
        supporting_keys: set[str] = set()

        for requirement in plan.evidence_requirements:
            matching = [
                chunk
                for chunk in chunks
                if _supports_requirement(
                    requirement,
                    chunk,
                    question=question,
                    subject=subject,
                    answer_shape=plan.answer_shape,
                )
            ]
            if matching:
                supporting_keys.update(
                    _stable_chunk_key(chunk) for chunk in matching
                )
                assessed.append(
                    replace(
                        requirement,
                        supported=True,
                        supporting_chunk_ids=tuple(
                            _chunk_diagnostic_id(chunk)
                            for chunk in matching
                        ),
                        assessment_reasons=(
                            _support_reason(requirement.requirement_type),
                        ),
                        rejection_reasons=(),
                    )
                )
            else:
                assessed.append(
                    replace(
                        requirement,
                        supported=False,
                        supporting_chunk_ids=(),
                        assessment_reasons=(),
                        rejection_reasons=(
                            _rejection_reason(requirement.requirement_type),
                        ),
                    )
                )

        supported = tuple(
            requirement.requirement_id
            for requirement in assessed
            if requirement.supported
        )
        unsupported = tuple(
            requirement.requirement_id
            for requirement in assessed
            if not requirement.supported
        )
        supporting_chunks = tuple(
            chunk
            for chunk in chunks
            if _stable_chunk_key(chunk) in supporting_keys
        )
        non_supporting_chunks = tuple(
            chunk
            for chunk in chunks
            if _stable_chunk_key(chunk) not in supporting_keys
        )
        required = [
            requirement for requirement in assessed if requirement.required
        ]
        required_supported = all(
            requirement.supported for requirement in required
        )

        if required_supported and supporting_chunks:
            mode = "full_answer"
            decision_reasons = ("all_required_evidence_supported",)
        elif (
            plan.allow_partial_answer
            and len(supported) >= plan.minimum_supported_requirements
            and supporting_chunks
        ):
            mode = "partial_answer"
            decision_reasons = (
                "required_evidence_incomplete",
                "useful_supported_evidence_available",
            )
        else:
            mode = "insufficient_evidence"
            decision_reasons = (
                "minimum_evidence_requirements_not_met",
            )

        return EvidenceDecision(
            mode=mode,
            plan=plan,
            assessed_requirements=tuple(assessed),
            supported_requirements=supported,
            unsupported_requirements=unsupported,
            supporting_chunks=supporting_chunks,
            non_supporting_chunks=non_supporting_chunks,
            decision_reasons=decision_reasons,
        )


def _supports_requirement(
    requirement: EvidenceRequirement,
    chunk: RetrievedChunk,
    *,
    question: str,
    subject: str,
    answer_shape: str,
) -> bool:
    requirement_type = requirement.requirement_type
    if requirement_type == "named_recipient":
        return _is_named_recipient_record(chunk) and _supports_subject(
            chunk,
            subject,
            strong_only=False,
        )
    if requirement_type == "procedure_context":
        return _is_procedural(chunk) and _supports_subject(
            chunk,
            subject,
            strong_only=True,
        )
    if requirement_type == "procedure":
        return _is_procedural(chunk) and _supports_subject(
            chunk,
            subject,
            strong_only=True,
        )
    if requirement_type == "deadline":
        return bool(_DEADLINE_RE.search(_chunk_weak_haystack(chunk)))
    if requirement_type == "structured_responsibility":
        return _is_structured_responsibility(chunk) and _supports_subject(
            chunk,
            subject,
            strong_only=False,
        )
    if requirement_type == "distinct_list_items":
        return bool(_coverage_key(chunk))
    if requirement_type == "primary_fact":
        return True
    if answer_shape in {"comparison", "default", "single_fact"}:
        return True
    return _supports_subject(chunk, subject or question, strong_only=False)


def _is_procedural(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    logical_type = _normalize(metadata.get("logical_unit_type"))
    strong_haystack = _chunk_strong_haystack(chunk)
    return (
        logical_type in _PROCEDURAL_LOGICAL_TYPES
        or "retrieval stage procedure_lookup"
        in (chunk.selection_reasons or [])
        or any(
            marker in strong_haystack
            for marker in (
                "инструкц",
                "порядок",
                "правил",
                "регламент",
            )
        )
        or bool(re.search(r"\bип\s+\d", strong_haystack))
    )


def _is_structured_responsibility(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    values = {
        _normalize(metadata.get("entity_type")),
        _normalize(metadata.get("logical_unit_type")),
        _normalize(chunk.doc_type),
    }
    record_key = _normalize(metadata.get("record_key"))
    return bool(values & _STRUCTURED_RESPONSIBILITY_TYPES) or any(
        record_key.startswith(prefix)
        for prefix in (
            "responsibility_route:",
            "organization_unit:",
            "employee_role:",
            "role_combination:",
        )
    )


def _is_named_recipient_record(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    entity_type = _normalize(metadata.get("entity_type"))
    record_key = _normalize(metadata.get("record_key"))
    return entity_type in {
        "document_recipient",
        "primary_contact",
        "responsibility_route",
    } or record_key.startswith(
        (
            "document_recipient:",
            "primary_contact:",
            "responsibility_route:",
        )
    )


def _supports_subject(
    chunk: RetrievedChunk,
    subject: str,
    *,
    strong_only: bool,
) -> bool:
    concept_groups = _subject_concept_groups(subject)
    if not concept_groups:
        return True
    haystack = _chunk_strong_haystack(chunk)
    if not strong_only:
        haystack = f"{haystack} {_chunk_weak_haystack(chunk)}"
    return all(
        any(_normalize(variant) in haystack for variant in variants)
        for variants in concept_groups
    )


def _subject_concept_groups(subject: str) -> tuple[tuple[str, ...], ...]:
    normalized = _normalize(subject)
    groups: list[tuple[str, ...]] = []
    covered_tokens: set[str] = set()

    for triggers, evidence_variants in _SUBJECT_CONCEPTS:
        matching_triggers = [
            trigger for trigger in triggers if _normalize(trigger) in normalized
        ]
        if not matching_triggers:
            continue
        groups.append(tuple(_normalize(value) for value in evidence_variants))
        for token in _TOKEN_RE.findall(normalized):
            if any(_normalize(trigger) in token for trigger in matching_triggers):
                covered_tokens.add(token)

    for token in _TOKEN_RE.findall(normalized):
        if token in covered_tokens or token in _GENERIC_SUBJECT_TERMS:
            continue
        if len(token) < 5 and not any(char.isdigit() for char in token):
            continue
        groups.append((_term_root(token),))

    unique: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        if group in seen:
            continue
        seen.add(group)
        unique.append(group)
    return tuple(unique)


def _term_root(token: str) -> str:
    if len(token) <= 6:
        return token[:5]
    return token[: max(5, len(token) - 3)]


def _chunk_strong_haystack(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    return _normalize(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                chunk.section,
                chunk.doc_type,
                metadata.get("source_file"),
                metadata.get("logical_unit_title"),
                metadata.get("logical_unit_type"),
                metadata.get("record_key"),
                metadata.get("topic"),
                metadata.get("unit_name"),
                " ".join(
                    str(tag) for tag in metadata.get("tags", [])
                ),
            )
        )
    )


def _chunk_weak_haystack(chunk: RetrievedChunk) -> str:
    return _normalize(
        " ".join(
            str(value or "")
            for value in (
                chunk.matched_excerpt,
                chunk.text,
            )
        )
    )


def _has_sibling_provenance(chunk: RetrievedChunk) -> bool:
    return "retrieval stage sibling_lookup" in (
        chunk.selection_reasons or []
    )


def _asks_for_deadline(question: str) -> bool:
    normalized = _normalize(question)
    return any(
        phrase in normalized
        for phrase in (
            "за сколько",
            "какой срок",
            "срок подачи",
            "когда подать",
            "не позднее",
        )
    )


def _is_explicit_comparison(question: str) -> bool:
    normalized = _normalize(question)
    return any(
        marker in normalized
        for marker in (
            "это то же самое",
            "чем отличается",
            "одно и то же",
            "это отпуск",
        )
    )


def _support_reason(requirement_type: str) -> str:
    return {
        "deadline": "subject_matched_deadline_rule",
        "distinct_list_items": "distinct_logical_unit_supported",
        "named_recipient": "structured_recipient_record_matched",
        "procedure": "subject_matched_procedural_record",
        "procedure_context": "related_procedure_supported",
        "structured_responsibility": (
            "structured_responsibility_subject_matched"
        ),
    }.get(requirement_type, "subject_matched_evidence")


def _rejection_reason(requirement_type: str) -> str:
    return {
        "deadline": "requested_deadline_not_found",
        "distinct_list_items": "requested_list_items_not_found",
        "named_recipient": "named_initial_recipient_not_found",
        "procedure": "subject_matched_procedure_not_found",
        "procedure_context": "related_procedure_not_found",
        "structured_responsibility": (
            "structured_responsibility_not_found"
        ),
    }.get(requirement_type, "primary_fact_not_found")


def _coverage_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    record_key = str(metadata.get("record_key") or "").strip()
    if record_key:
        return f"record_key:{record_key}"
    title = str(metadata.get("logical_unit_title") or "").strip()
    section = str(chunk.section or "").strip()
    return f"{chunk.source}|{title or section}"


def _chunk_diagnostic_id(chunk: RetrievedChunk) -> int | str:
    if chunk.chunk_id is not None:
        return chunk.chunk_id
    return _stable_chunk_key(chunk)


def _stable_chunk_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    record_key = str(metadata.get("record_key") or "").strip()
    if record_key:
        return f"record_key:{record_key}"
    if chunk.chunk_id is not None:
        return f"chunk_id:{chunk.chunk_id}|source:{chunk.source}"
    content_hash = str(metadata.get("content_hash") or "").strip()
    if content_hash:
        return f"content_hash:{content_hash}"
    return f"source:{chunk.source}|section:{chunk.section or ''}"


def _normalize(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(_TOKEN_RE.findall(text))
