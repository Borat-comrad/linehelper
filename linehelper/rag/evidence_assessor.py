"""Deterministic evidence planning and assessment for selected RAG context."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
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
        "формирование",
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

_NEGATIVE_SUBJECT_PATTERNS = (
    re.compile(
        r"\b(?:котор\w+\s+)?нет\s+в\s+"
        r"(?:баз\w*|материал\w*|источник\w*)\b"
    ),
    re.compile(
        r"\b(?:отсутств\w*|не\s+существ\w*|не\s+найден\w*)\b"
    ),
)

_RESPONSIBILITY_PAYLOAD_PATTERNS = (
    re.compile(r"\bкто\s+отвечает\s+за\s+(?P<subject>.+)$"),
    re.compile(r"\bкто\s+занимается\s+(?P<subject>.+)$"),
    re.compile(r"\bкто\s+ведет\s+(?P<subject>.+)$"),
    re.compile(r"\bкто\s+руководит\s+(?P<subject>.+)$"),
    re.compile(r"\bкто\s+главный\s+по\s+(?P<subject>.+)$"),
    re.compile(
        r"\bк\s+кому\s+(?:обратиться|обращаться)\s+по\s+"
        r"(?P<subject>.+)$"
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
    subject_criteria: tuple[str, ...] = ()
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
            "subject_criteria": list(self.subject_criteria),
            "supported": self.supported,
            "supporting_chunk_ids": list(self.supporting_chunk_ids),
            "assessment_reasons": list(self.assessment_reasons),
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class ChunkEvidenceAssessment:
    """Structured, reproducible reasons for one selected-context chunk."""

    chunk_id: int | str
    subject_match_source: tuple[str, ...]
    matched_subject_terms: tuple[str, ...]
    rejected_subject_terms: tuple[str, ...]
    logical_unit_match: bool
    structured_metadata_match: bool
    negative_subject_guard: bool
    requirement_ids: tuple[str, ...]
    support_decision: str
    decision_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "subject_match_source": list(self.subject_match_source),
            "matched_subject_terms": list(self.matched_subject_terms),
            "rejected_subject_terms": list(self.rejected_subject_terms),
            "logical_unit_match": self.logical_unit_match,
            "structured_metadata_match": self.structured_metadata_match,
            "negative_subject_guard": self.negative_subject_guard,
            "requirement_ids": list(self.requirement_ids),
            "support_decision": self.support_decision,
            "decision_reasons": list(self.decision_reasons),
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
    chunk_assessments: tuple[ChunkEvidenceAssessment, ...] = ()

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
            "chunk_assessments": [
                assessment.to_dict()
                for assessment in self.chunk_assessments
            ],
        }


@dataclass(frozen=True)
class _SubjectMatch:
    matched: bool
    sources: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    rejected_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RequirementChunkAssessment:
    supported: bool
    subject_match: _SubjectMatch = field(
        default_factory=lambda: _SubjectMatch(matched=False)
    )
    logical_unit_match: bool = False
    structured_metadata_match: bool = False
    negative_subject_guard: bool = False
    decision_reasons: tuple[str, ...] = ()


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
            subject_components = _responsibility_subject_components(
                question
            )
            if len(subject_components) > 1:
                requirements = (
                    EvidenceRequirement(
                        requirement_id="responsible_identity",
                        requirement_type=(
                            "structured_responsibility_identity"
                        ),
                        description=(
                            "ответственный сотрудник или роль по "
                            "подтверждённой части предмета"
                        ),
                        required=False,
                        expected_coverage_key="primary_responsibility",
                        subject_criteria=subject_components,
                    ),
                    EvidenceRequirement(
                        requirement_id="complete_responsibility_scope",
                        requirement_type=(
                            "structured_responsibility_scope"
                        ),
                        description=(
                            "полное подтверждение всех частей предмета "
                            "ответственности"
                        ),
                        expected_coverage_key="responsibility_scope",
                        subject_criteria=subject_components,
                    ),
                )
                allow_partial = True
            else:
                requirements = (
                    EvidenceRequirement(
                        requirement_id="primary_responsibility",
                        requirement_type="structured_responsibility",
                        description=(
                            "структурированная ответственность по "
                            "предмету вопроса"
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
        chunk_diagnostics: dict[str, dict[str, Any]] = {
            _stable_chunk_key(chunk): {
                "subject_match_source": [],
                "matched_subject_terms": [],
                "rejected_subject_terms": [],
                "logical_unit_match": False,
                "structured_metadata_match": False,
                "negative_subject_guard": False,
                "requirement_ids": [],
                "decision_reasons": [],
            }
            for chunk in chunks
        }

        for requirement in plan.evidence_requirements:
            matching: list[RetrievedChunk] = []
            for chunk in chunks:
                chunk_assessment = _assess_requirement_support(
                    requirement,
                    chunk,
                    question=question,
                    subject=subject,
                    answer_shape=plan.answer_shape,
                )
                diagnostic = chunk_diagnostics[_stable_chunk_key(chunk)]
                _extend_unique(
                    diagnostic["subject_match_source"],
                    chunk_assessment.subject_match.sources,
                )
                _extend_unique(
                    diagnostic["matched_subject_terms"],
                    chunk_assessment.subject_match.matched_terms,
                )
                _extend_unique(
                    diagnostic["rejected_subject_terms"],
                    chunk_assessment.subject_match.rejected_terms,
                )
                diagnostic["logical_unit_match"] = bool(
                    diagnostic["logical_unit_match"]
                    or chunk_assessment.logical_unit_match
                )
                diagnostic["structured_metadata_match"] = bool(
                    diagnostic["structured_metadata_match"]
                    or chunk_assessment.structured_metadata_match
                )
                diagnostic["negative_subject_guard"] = bool(
                    diagnostic["negative_subject_guard"]
                    or chunk_assessment.negative_subject_guard
                )
                _extend_unique(
                    diagnostic["decision_reasons"],
                    tuple(
                        f"{requirement.requirement_id}:{reason}"
                        for reason in chunk_assessment.decision_reasons
                    ),
                )
                if chunk_assessment.supported:
                    matching.append(chunk)
                    _extend_unique(
                        diagnostic["requirement_ids"],
                        (requirement.requirement_id,),
                    )
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
        chunk_assessments = tuple(
            ChunkEvidenceAssessment(
                chunk_id=_chunk_diagnostic_id(chunk),
                subject_match_source=tuple(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "subject_match_source"
                    ]
                ),
                matched_subject_terms=tuple(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "matched_subject_terms"
                    ]
                ),
                rejected_subject_terms=tuple(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "rejected_subject_terms"
                    ]
                ),
                logical_unit_match=bool(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "logical_unit_match"
                    ]
                ),
                structured_metadata_match=bool(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "structured_metadata_match"
                    ]
                ),
                negative_subject_guard=bool(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "negative_subject_guard"
                    ]
                ),
                requirement_ids=tuple(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "requirement_ids"
                    ]
                ),
                support_decision=(
                    "supporting"
                    if _stable_chunk_key(chunk) in supporting_keys
                    else "non_supporting"
                ),
                decision_reasons=tuple(
                    chunk_diagnostics[_stable_chunk_key(chunk)][
                        "decision_reasons"
                    ]
                ),
            )
            for chunk in chunks
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
            chunk_assessments=chunk_assessments,
        )


def _assess_requirement_support(
    requirement: EvidenceRequirement,
    chunk: RetrievedChunk,
    *,
    question: str,
    subject: str,
    answer_shape: str,
) -> _RequirementChunkAssessment:
    requirement_type = requirement.requirement_type
    criteria = requirement.subject_criteria or (subject,)

    if requirement_type == "named_recipient":
        structured_match = _is_named_recipient_record(chunk)
        subject_match = _match_subjects(
            chunk,
            criteria,
            strong_only=False,
            literal=False,
            match_all=True,
        )
        return _requirement_assessment(
            supported=structured_match and subject_match.matched,
            subject_match=subject_match,
            structured_metadata_match=structured_match,
        )

    if requirement_type in {"procedure_context", "procedure"}:
        logical_match = _is_procedural(chunk)
        subject_match = _match_subjects(
            chunk,
            criteria,
            strong_only=True,
            literal=False,
            match_all=True,
        )
        negative_guard = _negative_subject_guard(question, chunk)
        return _requirement_assessment(
            supported=(
                logical_match
                and subject_match.matched
                and not negative_guard
            ),
            subject_match=subject_match,
            logical_unit_match=logical_match,
            structured_metadata_match=bool(subject_match.sources),
            negative_subject_guard=negative_guard,
        )

    if requirement_type == "structured_responsibility_identity":
        structured_match = _is_structured_responsibility(chunk)
        subject_match = _match_subjects(
            chunk,
            criteria,
            strong_only=False,
            literal=True,
            match_all=False,
        )
        return _requirement_assessment(
            supported=structured_match and subject_match.matched,
            subject_match=subject_match,
            structured_metadata_match=structured_match,
        )

    if requirement_type == "structured_responsibility_scope":
        structured_match = _is_structured_responsibility(chunk)
        subject_match = _match_subjects(
            chunk,
            criteria,
            strong_only=False,
            literal=True,
            match_all=True,
        )
        return _requirement_assessment(
            supported=structured_match and subject_match.matched,
            subject_match=subject_match,
            structured_metadata_match=structured_match,
        )

    if requirement_type == "deadline":
        supported = bool(_DEADLINE_RE.search(_chunk_weak_haystack(chunk)))
        return _RequirementChunkAssessment(
            supported=supported,
            decision_reasons=(
                "deadline_rule_matched"
                if supported
                else "deadline_rule_not_found",
            ),
        )

    if requirement_type == "structured_responsibility":
        structured_match = _is_structured_responsibility(chunk)
        subject_match = _match_subjects(
            chunk,
            criteria,
            strong_only=False,
            literal=False,
            match_all=True,
        )
        return _requirement_assessment(
            supported=structured_match and subject_match.matched,
            subject_match=subject_match,
            structured_metadata_match=structured_match,
        )

    if requirement_type == "distinct_list_items":
        supported = bool(_coverage_key(chunk))
        return _RequirementChunkAssessment(
            supported=supported,
            decision_reasons=(
                "distinct_coverage_key_matched"
                if supported
                else "distinct_coverage_key_missing",
            ),
        )

    if requirement_type == "primary_fact":
        return _RequirementChunkAssessment(
            supported=True,
            decision_reasons=("primary_fact_available",),
        )

    if answer_shape in {"comparison", "default", "single_fact"}:
        return _RequirementChunkAssessment(
            supported=True,
            decision_reasons=("answer_shape_accepts_primary_fact",),
        )

    subject_match = _match_subjects(
        chunk,
        (subject or question,),
        strong_only=False,
        literal=False,
        match_all=True,
    )
    return _requirement_assessment(
        supported=subject_match.matched,
        subject_match=subject_match,
    )


def _requirement_assessment(
    *,
    supported: bool,
    subject_match: _SubjectMatch | None = None,
    logical_unit_match: bool = False,
    structured_metadata_match: bool = False,
    negative_subject_guard: bool = False,
) -> _RequirementChunkAssessment:
    match = subject_match or _SubjectMatch(matched=False)
    reasons: list[str] = []
    if logical_unit_match:
        reasons.append("logical_unit_matched")
    if structured_metadata_match:
        reasons.append("structured_metadata_matched")
    if match.matched:
        reasons.append("subject_matched")
    elif match.rejected_terms:
        reasons.append("subject_mismatch")
    if negative_subject_guard:
        reasons.append("negative_subject_guard")
    reasons.append("supporting" if supported else "non_supporting")
    return _RequirementChunkAssessment(
        supported=supported,
        subject_match=match,
        logical_unit_match=logical_unit_match,
        structured_metadata_match=structured_metadata_match,
        negative_subject_guard=negative_subject_guard,
        decision_reasons=tuple(reasons),
    )


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


def _match_subjects(
    chunk: RetrievedChunk,
    subjects: Sequence[str],
    *,
    strong_only: bool,
    literal: bool,
    match_all: bool,
) -> _SubjectMatch:
    matches = [
        _match_subject(
            chunk,
            subject,
            strong_only=strong_only,
            literal=literal,
        )
        for subject in subjects
        if str(subject or "").strip()
    ]
    if not matches:
        return _SubjectMatch(matched=False)

    matched = (
        all(item.matched for item in matches)
        if match_all
        else any(item.matched for item in matches)
    )
    return _SubjectMatch(
        matched=matched,
        sources=tuple(
            _unique_values(
                value
                for item in matches
                for value in item.sources
            )
        ),
        matched_terms=tuple(
            _unique_values(
                value
                for item in matches
                for value in item.matched_terms
            )
        ),
        rejected_terms=tuple(
            _unique_values(
                value
                for item in matches
                for value in item.rejected_terms
            )
        ),
    )


def _match_subject(
    chunk: RetrievedChunk,
    subject: str,
    *,
    strong_only: bool,
    literal: bool,
) -> _SubjectMatch:
    concept_groups = (
        _literal_subject_groups(subject)
        if literal
        else _subject_concept_groups(subject)
    )
    if not concept_groups:
        return _SubjectMatch(matched=False)

    strong_fields = _chunk_strong_fields(chunk)
    weak_fields = (
        {}
        if strong_only
        else {
            "matched_excerpt": chunk.matched_excerpt,
            "text": chunk.text,
        }
    )
    sources: list[str] = []
    matched_terms: list[str] = []
    rejected_terms: list[str] = []

    for variants in concept_groups:
        matched_variant: str | None = None
        matched_source: str | None = None
        for field_name, field_value in strong_fields.items():
            normalized_value = _normalize(field_value)
            matched_variant = next(
                (
                    _normalize(variant)
                    for variant in variants
                    if _normalize(variant) in normalized_value
                ),
                None,
            )
            if matched_variant:
                matched_source = field_name
                break
        if matched_variant is None:
            for field_name, field_value in weak_fields.items():
                normalized_value = _normalize(field_value)
                matched_variant = next(
                    (
                        _normalize(variant)
                        for variant in variants
                        if _normalize(variant) in normalized_value
                    ),
                    None,
                )
                if matched_variant:
                    matched_source = field_name
                    break
        if matched_variant is None:
            rejected_terms.append(_normalize(variants[0]))
            continue
        matched_terms.append(matched_variant)
        if matched_source:
            sources.append(matched_source)

    return _SubjectMatch(
        matched=not rejected_terms,
        sources=tuple(_unique_values(sources)),
        matched_terms=tuple(_unique_values(matched_terms)),
        rejected_terms=tuple(_unique_values(rejected_terms)),
    )


def _literal_subject_groups(subject: str) -> tuple[tuple[str, ...], ...]:
    normalized = _normalize(subject)
    groups: list[tuple[str, ...]] = []
    for token in _TOKEN_RE.findall(normalized):
        if token in _GENERIC_SUBJECT_TERMS:
            continue
        if len(token) < 5 and not any(char.isdigit() for char in token):
            continue
        groups.append((_term_root(token),))
    return tuple(_unique_values(groups))


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
    return _normalize(
        " ".join(
            str(value or "") for value in _chunk_strong_fields(chunk).values()
        )
    )


def _chunk_strong_fields(chunk: RetrievedChunk) -> dict[str, Any]:
    metadata = chunk.metadata or {}
    return {
        "title": chunk.title,
        "source": chunk.source,
        "section": chunk.section,
        "doc_type": chunk.doc_type,
        "metadata.source_file": metadata.get("source_file"),
        "metadata.logical_unit_title": metadata.get(
            "logical_unit_title"
        ),
        "metadata.logical_unit_type": metadata.get(
            "logical_unit_type"
        ),
        "metadata.record_key": metadata.get("record_key"),
        "metadata.topic": metadata.get("topic"),
        "metadata.unit_name": metadata.get("unit_name"),
        "metadata.tags": " ".join(
            str(tag) for tag in metadata.get("tags", [])
        ),
    }


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


def _negative_subject_guard(
    question: str,
    chunk: RetrievedChunk,
) -> bool:
    normalized_question = _normalize(question)
    if not any(
        pattern.search(normalized_question)
        for pattern in _NEGATIVE_SUBJECT_PATTERNS
    ):
        return False
    chunk_haystack = (
        f"{_chunk_strong_haystack(chunk)} {_chunk_weak_haystack(chunk)}"
    )
    return not any(
        pattern.search(chunk_haystack)
        for pattern in _NEGATIVE_SUBJECT_PATTERNS
    )


def _responsibility_subject_components(question: str) -> tuple[str, ...]:
    normalized = _normalize(question)
    payload: str | None = None
    for pattern in _RESPONSIBILITY_PAYLOAD_PATTERNS:
        match = pattern.search(normalized)
        if match:
            payload = match.group("subject")
            break
    if not payload:
        return ()
    components = [
        component.strip()
        for component in re.split(
            r"\s+(?:и|или)\s+|,\s*",
            payload,
        )
        if component.strip()
    ]
    return tuple(_unique_values(components)) if len(components) > 1 else ()


def _extend_unique(target: list[Any], values: Sequence[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _unique_values(values: Iterable[Any]) -> list[Any]:
    unique: list[Any] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


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
        "structured_responsibility_identity": (
            "structured_responsible_identity_matched"
        ),
        "structured_responsibility_scope": (
            "complete_responsibility_scope_matched"
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
        "structured_responsibility_identity": (
            "structured_responsible_identity_not_found"
        ),
        "structured_responsibility_scope": (
            "complete_responsibility_scope_not_found"
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
