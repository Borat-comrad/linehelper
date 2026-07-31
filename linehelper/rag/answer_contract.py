"""Grounded answer contract, validation and deterministic rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from linehelper.rag.evidence_assessor import EvidenceDecision
from linehelper.rag.retriever import RetrievedChunk


ANSWER_MODES = frozenset(
    {
        "full_answer",
        "partial_answer",
        "insufficient_evidence",
    }
)
MISSING_INFORMATION_HEADING = "Нет данных по следующим пунктам:"
_SOURCE_BLOCK_RE = re.compile(
    r"(?:\n\s*){1,}(?:источники ответа|источники|источник)\s*:\s*(?:\n|.)*\Z",
    re.IGNORECASE,
)
_SOURCE_LINE_RE = re.compile(
    r"(?im)^\s*(?:источник|source)\s*:\s*(?P<value>.+?)\s*$"
)
_CHUNK_REFERENCE_RE = re.compile(
    r"(?i)\b(?:chunk|чанк)\s*#?\s*(?P<value>[0-9]+)\b"
)


@dataclass(frozen=True)
class AnswerRequirementEntry:
    """One supported or unsupported requirement exposed by the contract."""

    requirement_id: str
    description: str
    supporting_chunk_ids: tuple[int | str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "description": self.description,
            "supporting_chunk_ids": list(self.supporting_chunk_ids),
        }


@dataclass(frozen=True)
class SourceEntry:
    """One allowed evidence source in supporting-chunk order."""

    chunk_id: int | str
    source: str
    source_title: str
    section: str | None
    record_key: str | None
    supported_requirement_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source": self.source,
            "source_title": self.source_title,
            "section": self.section,
            "record_key": self.record_key,
            "supported_requirement_ids": list(
                self.supported_requirement_ids
            ),
        }


@dataclass(frozen=True)
class GroundedAnswerContract:
    """Deterministic contract between evidence assessment and final output."""

    answer_mode: str
    supported_requirements: tuple[AnswerRequirementEntry, ...]
    unsupported_requirements: tuple[AnswerRequirementEntry, ...]
    allowed_chunk_ids: tuple[int | str, ...]
    source_entries: tuple[SourceEntry, ...]
    required_sections: tuple[str, ...]
    rendering_policy: dict[str, Any]
    diagnostics_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.answer_mode not in ANSWER_MODES:
            raise ValueError(
                f"Unsupported grounded answer mode: {self.answer_mode!r}"
            )

    @property
    def supported_requirement_ids(self) -> tuple[str, ...]:
        return tuple(
            requirement.requirement_id
            for requirement in self.supported_requirements
        )

    @property
    def unsupported_requirement_ids(self) -> tuple[str, ...]:
        return tuple(
            requirement.requirement_id
            for requirement in self.unsupported_requirements
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return only facts the LLM may use for the supported draft."""
        return {
            "answer_mode": self.answer_mode,
            "supported_requirements": [
                requirement.to_dict()
                for requirement in self.supported_requirements
            ],
            "allowed_chunk_ids": list(self.allowed_chunk_ids),
            "allowed_sources": [
                {
                    "chunk_id": source.chunk_id,
                    "source_title": source.source_title,
                    "section": source.section,
                    "supported_requirement_ids": list(
                        source.supported_requirement_ids
                    ),
                }
                for source in self.source_entries
            ],
            "draft_policy": {
                "supported_content_only": True,
                "render_missing_information": False,
                "render_sources": False,
                "allow_external_knowledge": False,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": True,
            "answer_mode": self.answer_mode,
            "supported_requirements": [
                requirement.to_dict()
                for requirement in self.supported_requirements
            ],
            "unsupported_requirements": [
                requirement.to_dict()
                for requirement in self.unsupported_requirements
            ],
            "supported_requirement_ids": list(
                self.supported_requirement_ids
            ),
            "unsupported_requirement_ids": list(
                self.unsupported_requirement_ids
            ),
            "allowed_chunk_ids": list(self.allowed_chunk_ids),
            "source_entries": [
                source.to_dict() for source in self.source_entries
            ],
            "required_sections": list(self.required_sections),
            "rendering_policy": dict(self.rendering_policy),
            "diagnostics_metadata": dict(self.diagnostics_metadata),
        }


@dataclass(frozen=True)
class AnswerContractValidation:
    """Reproducible structural validation of one LLM draft."""

    valid: bool
    violations: tuple[str, ...]
    missing_sections: tuple[str, ...]
    disallowed_source_references: tuple[str, ...]
    rendering_actions: tuple[str, ...]
    fallback_applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": True,
            "valid": self.valid,
            "violations": list(self.violations),
            "missing_sections": list(self.missing_sections),
            "disallowed_source_references": list(
                self.disallowed_source_references
            ),
            "rendering_actions": list(self.rendering_actions),
            "fallback_applied": self.fallback_applied,
        }


@dataclass(frozen=True)
class GroundedAnswerRender:
    """Final answer text plus deterministic output sections and sources."""

    answer: str
    source_entries: tuple[SourceEntry, ...]
    final_answer_sections: tuple[str, ...]
    validation: AnswerContractValidation

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "rendered_source_entries": [
                source.to_dict() for source in self.source_entries
            ],
            "final_answer_sections": list(self.final_answer_sections),
            "contract_validation": self.validation.to_dict(),
        }


class AnswerContractBuilder:
    """Build a grounded output contract without changing evidence rules."""

    def build(
        self,
        evidence_decision: EvidenceDecision,
    ) -> GroundedAnswerContract:
        assessed = evidence_decision.assessed_requirements
        supported = tuple(
            AnswerRequirementEntry(
                requirement_id=requirement.requirement_id,
                description=requirement.description,
                supporting_chunk_ids=requirement.supporting_chunk_ids,
            )
            for requirement in assessed
            if requirement.supported
        )
        unsupported = tuple(
            AnswerRequirementEntry(
                requirement_id=requirement.requirement_id,
                description=requirement.description,
                supporting_chunk_ids=(),
            )
            for requirement in assessed
            if not requirement.supported
        )
        source_entries = _source_entries(
            evidence_decision.supporting_chunks,
            supported,
        )

        if evidence_decision.mode == "partial_answer":
            required_sections = (
                "supported_answer",
                "missing_information",
                "sources",
            )
        elif evidence_decision.mode == "insufficient_evidence":
            required_sections = ("missing_information",)
        else:
            required_sections = ("supported_answer", "sources")

        return GroundedAnswerContract(
            answer_mode=evidence_decision.mode,
            supported_requirements=supported,
            unsupported_requirements=unsupported,
            allowed_chunk_ids=tuple(
                source.chunk_id for source in source_entries
            ),
            source_entries=source_entries,
            required_sections=required_sections,
            rendering_policy={
                "llm_renders_supported_answer_only": True,
                "missing_information_is_deterministic": True,
                "sources_are_deterministic": True,
                "deduplicate_sources_by_chunk": True,
                "preserve_supporting_chunk_order": True,
            },
            diagnostics_metadata={
                "evidence_decision_reasons": list(
                    evidence_decision.decision_reasons
                ),
                "non_supporting_chunk_ids": [
                    _chunk_diagnostic_id(chunk)
                    for chunk in evidence_decision.non_supporting_chunks
                ],
            },
        )


class AnswerContractValidator:
    """Validate only reproducible draft and contract properties."""

    def validate(
        self,
        draft: str,
        contract: GroundedAnswerContract,
    ) -> AnswerContractValidation:
        clean_draft = _strip_llm_source_block(draft)
        violations: list[str] = []
        missing_sections: list[str] = []
        actions: list[str] = []
        disallowed = _disallowed_source_references(draft, contract)

        if (
            contract.answer_mode in {"full_answer", "partial_answer"}
            and not clean_draft
        ):
            violations.append("empty_supported_answer_draft")
            missing_sections.append("supported_answer")
        if (
            contract.answer_mode
            in {"partial_answer", "insufficient_evidence"}
            and not contract.unsupported_requirements
        ):
            violations.append("missing_unsupported_requirements")
            missing_sections.append("missing_information")
        if (
            contract.answer_mode in {"full_answer", "partial_answer"}
            and not contract.source_entries
        ):
            violations.append("missing_allowed_sources")
            missing_sections.append("sources")
        if disallowed:
            violations.append("disallowed_source_reference")
        if _SOURCE_BLOCK_RE.search(draft.strip()):
            actions.append("remove_llm_source_block")
        if contract.answer_mode in {
            "partial_answer",
            "insufficient_evidence",
        }:
            actions.append("render_missing_information")
        if contract.source_entries:
            actions.append("render_allowed_sources")

        valid = not violations and not missing_sections and not disallowed
        fallback_applied = not valid
        if fallback_applied:
            actions.append("use_safe_supported_fallback")

        return AnswerContractValidation(
            valid=valid,
            violations=tuple(dict.fromkeys(violations)),
            missing_sections=tuple(dict.fromkeys(missing_sections)),
            disallowed_source_references=tuple(disallowed),
            rendering_actions=tuple(dict.fromkeys(actions)),
            fallback_applied=fallback_applied,
        )


class GroundedAnswerRenderer:
    """Render deterministic missing-information and source sections."""

    def render(
        self,
        draft: str,
        contract: GroundedAnswerContract,
        validation: AnswerContractValidation,
    ) -> GroundedAnswerRender:
        clean_draft = _strip_llm_source_block(draft)
        sections: list[str] = []

        if contract.answer_mode == "insufficient_evidence":
            content = (
                "В найденных источниках недостаточно данных для ответа."
            )
        elif validation.fallback_applied:
            content = (
                "Подтверждающие материалы найдены, но безопасно "
                "сформировать содержательную часть ответа не удалось."
            )
        else:
            content = clean_draft

        answer_parts: list[str] = []
        if content:
            answer_parts.append(content)
            if contract.answer_mode != "insufficient_evidence":
                sections.append("supported_answer")

        if contract.answer_mode in {
            "partial_answer",
            "insufficient_evidence",
        }:
            answer_parts.append(
                _render_missing_information(
                    contract.unsupported_requirements
                )
            )
            sections.append("missing_information")

        if contract.source_entries:
            sections.append("sources")

        return GroundedAnswerRender(
            answer="\n\n".join(
                part for part in answer_parts if part.strip()
            ).strip(),
            source_entries=contract.source_entries,
            final_answer_sections=tuple(sections),
            validation=validation,
        )


def _source_entries(
    chunks: tuple[RetrievedChunk, ...],
    requirements: tuple[AnswerRequirementEntry, ...],
) -> tuple[SourceEntry, ...]:
    result: list[SourceEntry] = []
    seen: set[str] = set()

    for chunk in chunks:
        stable_key = _stable_chunk_key(chunk)
        if stable_key in seen:
            continue
        seen.add(stable_key)
        chunk_id = _chunk_diagnostic_id(chunk)
        requirement_ids = tuple(
            requirement.requirement_id
            for requirement in requirements
            if _same_identifier(
                chunk_id,
                requirement.supporting_chunk_ids,
            )
        )
        metadata = chunk.metadata or {}
        result.append(
            SourceEntry(
                chunk_id=chunk_id,
                source=chunk.source,
                source_title=chunk.title,
                section=chunk.section,
                record_key=_optional_text(metadata.get("record_key")),
                supported_requirement_ids=requirement_ids,
            )
        )
    return tuple(result)


def _disallowed_source_references(
    draft: str,
    contract: GroundedAnswerContract,
) -> list[str]:
    allowed_chunk_ids = {
        str(value).casefold() for value in contract.allowed_chunk_ids
    }
    allowed_names = {
        _normalize_source_reference(value)
        for source in contract.source_entries
        for value in (
            source.source,
            source.source_title,
            source.section,
        )
        if value
    }
    disallowed: list[str] = []

    for match in _CHUNK_REFERENCE_RE.finditer(draft):
        value = match.group("value").strip()
        if value.casefold() not in allowed_chunk_ids:
            disallowed.append(match.group(0).strip())
    for match in _SOURCE_LINE_RE.finditer(draft):
        value = match.group("value").strip()
        normalized = _normalize_source_reference(value)
        if not any(
            allowed in normalized or normalized in allowed
            for allowed in allowed_names
        ):
            disallowed.append(value)

    return list(dict.fromkeys(disallowed))


def _render_missing_information(
    requirements: tuple[AnswerRequirementEntry, ...],
) -> str:
    descriptions = [
        requirement.description.strip()
        for requirement in requirements
        if requirement.description.strip()
    ]
    if not descriptions:
        descriptions = ["сведения, необходимые для полного ответа"]
    bullets = "\n".join(f"- {description}" for description in descriptions)
    return f"{MISSING_INFORMATION_HEADING}\n{bullets}"


def _strip_llm_source_block(draft: str) -> str:
    return _SOURCE_BLOCK_RE.sub("", str(draft or "").strip()).rstrip()


def _same_identifier(
    chunk_id: int | str,
    values: tuple[int | str, ...],
) -> bool:
    normalized = str(chunk_id)
    return any(str(value) == normalized for value in values)


def _chunk_diagnostic_id(chunk: RetrievedChunk) -> int | str:
    if chunk.chunk_id is not None:
        return chunk.chunk_id
    return _stable_chunk_key(chunk)


def _stable_chunk_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    record_key = _optional_text(metadata.get("record_key"))
    if record_key:
        return f"record_key:{record_key}"
    if chunk.chunk_id is not None:
        return f"chunk_id:{chunk.chunk_id}|source:{chunk.source}"
    content_hash = _optional_text(metadata.get("content_hash"))
    if content_hash:
        return f"content_hash:{content_hash}"
    return f"source:{chunk.source}|section:{chunk.section or ''}"


def _normalize_source_reference(value: str) -> str:
    return " ".join(str(value).casefold().replace("\\", "/").split())


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
