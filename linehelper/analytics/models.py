"""Validated data models stored in the analytics database."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


ANALYTICS_SCHEMA_VERSION = 1
FEEDBACK_RATINGS = frozenset({"positive", "negative"})
NEGATIVE_FEEDBACK_REASONS = frozenset(
    {
        "wrong_answer",
        "incomplete_answer",
        "wrong_sources",
        "document_not_found",
        "question_misunderstood",
        "too_slow",
        "other",
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class InteractionSource:
    position: int
    chunk_id: int | str
    source: str | None
    source_title: str | None
    section: str | None
    record_key: str | None
    is_supporting: bool
    is_rendered: bool
    requirement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InteractionRecord:
    interaction_id: str
    session_id: str | None
    user_id_hash: str | None
    created_at_utc: datetime
    original_question: str | None
    resolved_question: str | None
    intent: str | None
    initial_requested_fact_type: str | None
    finalized_requested_fact_type: str | None
    resolution_status: str | None
    response_kind: str | None
    answer_mode: str | None
    final_answer: str | None
    clarification_requested: bool
    clarification_reason: str | None
    retrieved_chunk_ids: tuple[int | str, ...] = ()
    selected_context_chunk_ids: tuple[int | str, ...] = ()
    supporting_chunk_ids: tuple[int | str, ...] = ()
    non_supporting_chunk_ids: tuple[int | str, ...] = ()
    rendered_source_chunk_ids: tuple[int | str, ...] = ()
    query_plan: dict[str, Any] = field(default_factory=dict)
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)
    context_diagnostics: dict[str, Any] = field(default_factory=dict)
    evidence_diagnostics: dict[str, Any] = field(default_factory=dict)
    answer_contract_diagnostics: dict[str, Any] = field(default_factory=dict)
    total_duration_ms: float | None = None
    query_analysis_duration_ms: float | None = None
    retrieval_duration_ms: float | None = None
    answer_generation_duration_ms: float | None = None
    error_type: str | None = None
    error_message: str | None = None
    answer_model: str | None = None
    analyzer_model: str | None = None
    app_version: str | None = None
    git_commit: str | None = None
    sources: tuple[InteractionSource, ...] = ()
    schema_version: int = ANALYTICS_SCHEMA_VERSION


@dataclass(frozen=True)
class InteractionFeedback:
    interaction_id: str
    rating: str
    reason: str | None = None
    comment: str | None = None
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    created_at_utc: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.rating not in FEEDBACK_RATINGS:
            raise ValueError(f"Unsupported feedback rating: {self.rating!r}")
        if self.rating == "negative" and self.reason not in NEGATIVE_FEEDBACK_REASONS:
            raise ValueError("Negative feedback requires a valid reason")
        if self.rating == "positive" and self.reason is not None:
            raise ValueError("Positive feedback must not include a negative reason")


@dataclass(frozen=True)
class CleanupResult:
    deleted_interactions: int
    cutoff_utc: datetime | None
    performed: bool = True
    error: str | None = None
