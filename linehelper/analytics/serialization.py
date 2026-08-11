"""Explicit whitelist serialization from a completed RAG answer."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from linehelper.analytics.config import AnalyticsConfig
from linehelper.analytics.models import InteractionRecord, InteractionSource, utc_now
from linehelper.analytics.sanitizer import sanitize_text

if TYPE_CHECKING:
    from linehelper.llm.answer_generator import RagAnswer


QUERY_PLAN_KEYS = (
    "intent",
    "raw_intent",
    "answer_type",
    "requested_fact_type",
    "initial_requested_fact_type",
    "finalized_requested_fact_type",
    "resolution_status",
    "matched_signals",
    "rejected_fact_types",
    "decision_reasons",
    "temporal_scope",
    "subject",
    "operational_lookup",
    "operational_decision_reason",
    "catalog_identifier",
    "catalog_result_count",
    "catalog_status",
    "raw_clarification_required",
    "validated_clarification_required",
    "validated_clarification_kind",
    "validated_ambiguity_span",
    "validated_missing_slots",
    "clarification_action",
    "clarification_validation_reasons",
)
RETRIEVAL_KEYS = (
    "retrieval_plan",
    "retrieval_stages",
    "stage_queries",
    "stage_filters",
    "stage_hit_counts",
    "candidate_count_before_dedupe",
    "candidate_count_after_dedupe",
    "duplicate_count",
    "candidate_order",
    "duration_ms",
)
CONTEXT_KEYS = (
    "context_plan",
    "context_requirements",
    "context_budget",
    "selected_context",
    "selected_context_reasons",
    "excluded_candidate_reasons",
    "coverage_required",
    "coverage_satisfied",
    "context_size",
)
EVIDENCE_KEYS = (
    "evidence_plan",
    "evidence_requirements",
    "evidence_decision",
    "answer_mode",
    "supporting_chunk_ids",
    "non_supporting_chunk_ids",
    "supported_requirements",
    "unsupported_requirements",
    "decision_reasons",
    "coverage_rate",
    "chunk_assessments",
)
ANSWER_CONTRACT_KEYS = (
    "answer_mode",
    "supported_requirements",
    "unsupported_requirements",
    "supported_requirement_ids",
    "unsupported_requirement_ids",
    "allowed_chunk_ids",
    "source_entries",
    "required_sections",
    "rendering_policy",
)


def interaction_record_from_answer(
    result: "RagAnswer",
    *,
    config: AnalyticsConfig,
    session_id: str | None,
    user_id: str | None,
    analyzer_model: str | None,
    app_version: str | None,
    git_commit: str | None,
    created_at_utc: datetime | None = None,
) -> InteractionRecord:
    query_plan = _mapping(result.query_plan)
    retrieval = _mapping(result.retrieval)
    context = _mapping(result.context)
    evidence = _mapping(result.evidence)
    answer_contract = _mapping(result.answer_contract)

    retrieved_ids = _candidate_ids(retrieval)
    selected_ids = _ids_from_items(context.get("selected_context"))
    supporting_ids = _ids(evidence.get("supporting_chunk_ids"))
    non_supporting_ids = _ids(evidence.get("non_supporting_chunk_ids"))
    rendered_ids = _ids(answer_contract.get("allowed_chunk_ids"))
    sources = _interaction_sources(
        context=context,
        answer_contract=answer_contract,
        supporting_ids=supporting_ids,
        rendered_ids=rendered_ids,
    )
    clarification_requested = bool(
        query_plan.get("validated_clarification_required")
        or result.response_kind == "clarification"
    )
    clarification_reason = _clarification_reason(query_plan)
    original_question = result.question if config.store_text else None
    final_answer = result.answer if config.store_text else None

    return InteractionRecord(
        interaction_id=str(uuid4()),
        session_id=session_id,
        user_id_hash=hash_user_id(user_id, config.user_hash_secret),
        created_at_utc=created_at_utc or utc_now(),
        original_question=sanitize_text(original_question, redact_pii=config.redact_pii),
        resolved_question=sanitize_text(
            result.resolved_question if config.store_text else None,
            redact_pii=config.redact_pii,
        ),
        intent=_optional_string(query_plan.get("intent")),
        initial_requested_fact_type=_optional_string(
            query_plan.get("initial_requested_fact_type")
        ),
        finalized_requested_fact_type=_optional_string(
            query_plan.get("finalized_requested_fact_type")
            or query_plan.get("requested_fact_type")
        ),
        resolution_status=_optional_string(query_plan.get("resolution_status")),
        response_kind=result.response_kind,
        answer_mode=_optional_string(
            evidence.get("answer_mode") or answer_contract.get("answer_mode")
        ),
        final_answer=sanitize_text(final_answer, redact_pii=config.redact_pii),
        clarification_requested=clarification_requested,
        clarification_reason=sanitize_text(
            clarification_reason,
            redact_pii=config.redact_pii,
        ),
        retrieved_chunk_ids=retrieved_ids,
        selected_context_chunk_ids=selected_ids,
        supporting_chunk_ids=supporting_ids,
        non_supporting_chunk_ids=non_supporting_ids,
        rendered_source_chunk_ids=rendered_ids,
        query_plan=_pick(query_plan, QUERY_PLAN_KEYS),
        retrieval_diagnostics=_pick(retrieval, RETRIEVAL_KEYS),
        context_diagnostics=_pick(context, CONTEXT_KEYS),
        evidence_diagnostics=_pick(evidence, EVIDENCE_KEYS),
        answer_contract_diagnostics=_pick(answer_contract, ANSWER_CONTRACT_KEYS),
        total_duration_ms=round(result.elapsed_seconds * 1000.0, 3),
        retrieval_duration_ms=_optional_float(retrieval.get("duration_ms")),
        answer_model=result.model,
        analyzer_model=analyzer_model,
        app_version=app_version,
        git_commit=git_commit,
        sources=sources,
    )


def hash_user_id(user_id: str | None, secret: str | None) -> str | None:
    if not user_id or not secret:
        return None
    return hmac.new(
        secret.encode("utf-8"),
        user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _interaction_sources(
    *,
    context: Mapping[str, Any],
    answer_contract: Mapping[str, Any],
    supporting_ids: tuple[int | str, ...],
    rendered_ids: tuple[int | str, ...],
) -> tuple[InteractionSource, ...]:
    supporting = {str(value) for value in supporting_ids}
    rendered = {str(value) for value in rendered_ids}
    ordered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    for item in answer_contract.get("source_entries") or []:
        if not isinstance(item, Mapping) or item.get("chunk_id") is None:
            continue
        key = str(item["chunk_id"])
        if key in by_id:
            continue
        normalized = {
            "chunk_id": item.get("chunk_id"),
            "source": item.get("source"),
            "source_title": item.get("source_title"),
            "section": item.get("section"),
            "record_key": item.get("record_key"),
            "requirement_ids": _string_tuple(item.get("supported_requirement_ids")),
        }
        by_id[key] = normalized
        ordered.append(normalized)

    for item in context.get("selected_context") or []:
        if not isinstance(item, Mapping) or item.get("chunk_id") is None:
            continue
        key = str(item["chunk_id"])
        if key in by_id:
            continue
        normalized = {
            "chunk_id": item.get("chunk_id"),
            "source": item.get("source"),
            "source_title": item.get("title"),
            "section": item.get("section"),
            "record_key": item.get("record_key"),
            "requirement_ids": (),
        }
        by_id[key] = normalized
        ordered.append(normalized)

    return tuple(
        InteractionSource(
            position=index,
            chunk_id=item["chunk_id"],
            source=_optional_string(item.get("source")),
            source_title=_optional_string(item.get("source_title")),
            section=_optional_string(item.get("section")),
            record_key=_optional_string(item.get("record_key")),
            is_supporting=str(item["chunk_id"]) in supporting,
            is_rendered=str(item["chunk_id"]) in rendered,
            requirement_ids=tuple(item["requirement_ids"]),
        )
        for index, item in enumerate(ordered)
    )


def _candidate_ids(retrieval: Mapping[str, Any]) -> tuple[int | str, ...]:
    values = retrieval.get("candidate_order")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    result: list[int | str] = []
    for item in values:
        value = item.get("chunk_id") if isinstance(item, Mapping) else item
        if isinstance(value, (int, str)) and value not in result:
            result.append(value)
    return tuple(result)


def _ids_from_items(value: Any) -> tuple[int | str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return _ids(
        [item.get("chunk_id") for item in value if isinstance(item, Mapping)]
    )


def _ids(value: Any) -> tuple[int | str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    result: list[int | str] = []
    for item in value:
        if isinstance(item, (int, str)) and item not in result:
            result.append(item)
    return tuple(result)


def _clarification_reason(query_plan: Mapping[str, Any]) -> str | None:
    values = query_plan.get("clarification_validation_reasons") or []
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        normalized = [str(value) for value in values if str(value).strip()]
        if normalized:
            return ", ".join(normalized)
    return _optional_string(query_plan.get("validated_clarification_kind"))


def _pick(source: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key not in source:
            continue
        value = _json_safe(source[key])
        if value is not None:
            result[key] = value
    return result


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_safe(item) for item in value]
    return str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
