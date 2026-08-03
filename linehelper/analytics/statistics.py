"""Deterministic aggregation for local interaction exports."""

from __future__ import annotations

from collections import Counter
import json
import math
import statistics
from typing import Any


def aggregate_interaction_stats(
    interactions: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate metadata without treating missing feedback as correctness."""
    latest_feedback = _latest_feedback(feedback)
    positive = sum(item.get("rating") == "positive" for item in latest_feedback.values())
    negative = sum(item.get("rating") == "negative" for item in latest_feedback.values())
    feedback_count = len(latest_feedback)
    total = len(interactions)
    durations = sorted(
        float(item["total_duration_ms"])
        for item in interactions
        if item.get("total_duration_ms") is not None
    )
    interaction_by_id = {
        str(item["interaction_id"]): item for item in interactions
    }
    negative_questions: Counter[str] = Counter()
    for interaction_id, item in latest_feedback.items():
        if item.get("rating") != "negative":
            continue
        question = interaction_by_id.get(interaction_id, {}).get("original_question")
        if question:
            negative_questions[str(question)] += 1

    rendered_sources: Counter[str] = Counter(
        str(item.get("source_title") or item.get("source") or item.get("chunk_id"))
        for item in sources
        if int(item.get("is_rendered") or 0) == 1
    )
    negative_reasons = Counter(
        str(item["reason"])
        for item in latest_feedback.values()
        if item.get("rating") == "negative" and item.get("reason")
    )

    return {
        "total_interactions": total,
        "interactions_with_feedback": feedback_count,
        "positive_feedback": positive,
        "negative_feedback": negative,
        "feedback_rate": _rate(feedback_count, total),
        "positive_rate": _rate(positive, feedback_count),
        "answer_mode_distribution": _distribution(interactions, "answer_mode"),
        "response_kind_distribution": _distribution(interactions, "response_kind"),
        "requested_fact_type_distribution": _distribution(
            interactions,
            "finalized_requested_fact_type",
        ),
        "intent_distribution": _distribution(interactions, "intent"),
        "negative_feedback_reasons": dict(sorted(negative_reasons.items())),
        "error_count": sum(bool(item.get("error_type")) for item in interactions),
        "timeout_count": sum(
            "timeout" in str(item.get("error_type") or "").casefold()
            for item in interactions
        ),
        "median_total_duration_ms": (
            round(statistics.median(durations), 3) if durations else None
        ),
        "p95_total_duration_ms": (
            round(durations[max(0, math.ceil(len(durations) * 0.95) - 1)], 3)
            if durations
            else None
        ),
        "generic_no_answer_with_evidence_count": sum(
            item.get("response_kind") == "no_answer"
            and bool(_json_list(item.get("retrieved_chunk_ids_json")))
            for item in interactions
        ),
        "insufficient_evidence_count": sum(
            item.get("answer_mode") == "insufficient_evidence"
            for item in interactions
        ),
        "partial_answer_count": sum(
            item.get("answer_mode") == "partial_answer"
            or item.get("response_kind") == "partial_answer"
            for item in interactions
        ),
        "top_rendered_sources": [
            {"source": name, "count": count}
            for name, count in rendered_sources.most_common(20)
        ],
        "top_negative_questions": [
            {"question": question, "count": count}
            for question, count in negative_questions.most_common(20)
        ],
        "feedback_interpretation": (
            "Feedback is subjective; missing negative feedback is not proof of correctness."
        ),
    }


def latest_feedback_rows(feedback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(_latest_feedback(feedback).values())


def _latest_feedback(feedback: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in sorted(
        feedback,
        key=lambda value: (
            str(value.get("created_at_utc") or ""),
            str(value.get("feedback_id") or ""),
        ),
    ):
        interaction_id = str(item.get("interaction_id") or "")
        if interaction_id:
            latest[interaction_id] = item
    return latest


def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    values = Counter(str(row.get(key) or "unknown") for row in rows)
    return dict(sorted(values.items()))


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
