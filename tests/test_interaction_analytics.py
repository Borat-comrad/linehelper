from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from linehelper.analytics.config import AnalyticsConfig
from linehelper.analytics.interaction_logger import (
    NullInteractionLogger,
    create_interaction_logger,
)
from linehelper.analytics.interaction_store import InteractionStore
from linehelper.analytics.models import (
    ANALYTICS_SCHEMA_VERSION,
    InteractionFeedback,
    InteractionRecord,
    InteractionSource,
)
from linehelper.analytics.sanitizer import sanitize_text
from linehelper.analytics.serialization import interaction_record_from_answer
from linehelper.analytics.statistics import aggregate_interaction_stats
from linehelper.llm.answer_generator import RagAnswer


def test_interaction_schema_creation_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "analytics" / "interactions.db"
    store = InteractionStore(db_path)

    store.ensure_schema()
    store.ensure_schema()

    import sqlite3

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = connection.execute(
            "SELECT value FROM analytics_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert {"interactions", "interaction_sources", "interaction_feedback"} <= tables
    assert version == str(ANALYTICS_SCHEMA_VERSION)


def test_interaction_and_sources_are_persisted_in_order(tmp_path) -> None:
    store = InteractionStore(tmp_path / "analytics.db")
    store.ensure_schema()
    record = _record(
        "interaction-1",
        sources=(
            InteractionSource(0, 29, "a.pdf", "A", "S1", None, True, True, ("r1",)),
            InteractionSource(1, 44, "b.pdf", "B", "S2", None, False, False),
        ),
    )

    store.record_interaction(record)

    interactions = store.fetch_rows("interactions")
    sources = store.fetch_rows("interaction_sources")
    assert interactions[0]["interaction_id"] == "interaction-1"
    assert json.loads(interactions[0]["supporting_chunk_ids_json"]) == [29]
    assert [row["chunk_id"] for row in sources] == ["29", "44"]
    assert [row["is_rendered"] for row in sources] == [1, 0]


def test_feedback_events_are_append_only(tmp_path) -> None:
    store = InteractionStore(tmp_path / "analytics.db")
    store.ensure_schema()
    store.record_interaction(_record("interaction-1"))

    store.record_feedback(InteractionFeedback("interaction-1", "positive"))
    store.record_feedback(
        InteractionFeedback(
            "interaction-1",
            "negative",
            reason="incomplete_answer",
            comment="Не хватает срока",
        )
    )

    rows = store.fetch_rows("interaction_feedback")
    assert [row["rating"] for row in rows] == ["positive", "negative"]
    assert rows[1]["reason"] == "incomplete_answer"


def test_retention_cleanup_cascades_sources_and_feedback(tmp_path) -> None:
    store = InteractionStore(tmp_path / "analytics.db")
    store.ensure_schema()
    now = datetime.now(timezone.utc)
    store.record_interaction(
        _record(
            "old",
            created_at_utc=now - timedelta(days=100),
            sources=(InteractionSource(0, 1, None, None, None, None, True, True),),
        )
    )
    store.record_feedback(InteractionFeedback("old", "positive"))
    store.record_interaction(_record("new", created_at_utc=now))

    cleanup = store.cleanup_expired(retention_days=90, cutoff=now - timedelta(days=90))

    assert cleanup.deleted_interactions == 1
    assert [row["interaction_id"] for row in store.fetch_rows("interactions")] == ["new"]
    assert store.fetch_rows("interaction_sources") == []
    assert store.fetch_rows("interaction_feedback") == []


def test_disabled_logger_does_not_create_database(tmp_path) -> None:
    config = _config(tmp_path / "disabled.db", enabled=False)

    logger = create_interaction_logger(config)

    assert isinstance(logger, NullInteractionLogger)
    assert logger.record_interaction(_record("disabled")) is None
    assert not config.db_path.exists()


def test_sanitizer_redacts_pii_and_explicit_secrets() -> None:
    value = (
        "mail manager@example.com, phone +7 (999) 123-45-67, "
        "token=abc123 password:secret api_key=qwerty Authorization=Bearer-value"
    )

    sanitized = sanitize_text(value)

    assert sanitized is not None
    assert "manager@example.com" not in sanitized
    assert "+7 (999) 123-45-67" not in sanitized
    assert "abc123" not in sanitized
    assert "secret" not in sanitized
    assert sanitized.count("[REDACTED]") == 4


def test_statistics_use_latest_feedback_and_available_latency() -> None:
    interactions = [
        {
            "interaction_id": "i1",
            "original_question": "Как оформить отпуск?",
            "answer_mode": "partial_answer",
            "response_kind": "partial_answer",
            "finalized_requested_fact_type": "procedure",
            "intent": "vacation",
            "total_duration_ms": 100.0,
            "retrieved_chunk_ids_json": "[42]",
            "error_type": None,
        },
        {
            "interaction_id": "i2",
            "original_question": None,
            "answer_mode": "insufficient_evidence",
            "response_kind": "no_answer",
            "finalized_requested_fact_type": "unknown",
            "intent": "unknown",
            "total_duration_ms": 300.0,
            "retrieved_chunk_ids_json": "[]",
            "error_type": "OllamaTimeoutError",
        },
    ]
    feedback = [
        {"feedback_id": "a", "interaction_id": "i1", "created_at_utc": "2026-01-01T00:00:00+00:00", "rating": "positive", "reason": None},
        {"feedback_id": "b", "interaction_id": "i1", "created_at_utc": "2026-01-02T00:00:00+00:00", "rating": "negative", "reason": "incomplete_answer"},
    ]
    sources = [{"source_title": "Отпуск", "is_rendered": 1, "chunk_id": "42"}]

    summary = aggregate_interaction_stats(interactions, feedback, sources)

    assert summary["total_interactions"] == 2
    assert summary["negative_feedback"] == 1
    assert summary["positive_feedback"] == 0
    assert summary["median_total_duration_ms"] == 200.0
    assert summary["timeout_count"] == 1
    assert summary["top_rendered_sources"] == [{"source": "Отпуск", "count": 1}]


def test_answer_serialization_is_whitelisted_and_source_ordered(tmp_path) -> None:
    config = _config(tmp_path / "analytics.db", store_text=True, secret="hash-key")
    result = _rag_answer()

    record = interaction_record_from_answer(
        result,
        config=config,
        session_id="session-1",
        user_id="manager@example.com",
        analyzer_model="analyzer",
        app_version="1.2.3",
        git_commit="abc",
    )
    metadata_only = interaction_record_from_answer(
        result,
        config=_config(tmp_path / "metadata.db", store_text=False),
        session_id=None,
        user_id="raw-user",
        analyzer_model=None,
        app_version=None,
        git_commit=None,
    )

    assert record.original_question == "Контакт [EMAIL]"
    assert record.final_answer == "Позвоните [PHONE]"
    assert record.user_id_hash is not None and "manager" not in record.user_id_hash
    assert "secret_prompt" not in record.query_plan
    assert [source.chunk_id for source in record.sources] == [29, 44]
    assert [source.is_rendered for source in record.sources] == [True, False]
    assert metadata_only.original_question is None
    assert metadata_only.final_answer is None
    assert metadata_only.user_id_hash is None


def _config(
    db_path,
    *,
    enabled: bool = True,
    store_text: bool = True,
    secret: str | None = None,
) -> AnalyticsConfig:
    return AnalyticsConfig(
        enabled=enabled,
        db_path=db_path,
        retention_days=90,
        store_text=store_text,
        redact_pii=True,
        queue_size=20,
        user_hash_secret=secret,
    )


def _record(
    interaction_id: str,
    *,
    created_at_utc: datetime | None = None,
    sources: tuple[InteractionSource, ...] = (),
) -> InteractionRecord:
    return InteractionRecord(
        interaction_id=interaction_id,
        session_id="session",
        user_id_hash=None,
        created_at_utc=created_at_utc or datetime.now(timezone.utc),
        original_question="Вопрос",
        resolved_question="Вопрос",
        intent="unknown",
        initial_requested_fact_type="unknown",
        finalized_requested_fact_type="unknown",
        resolution_status="insufficient_signals",
        response_kind="no_answer",
        answer_mode="insufficient_evidence",
        final_answer="Ответ",
        clarification_requested=False,
        clarification_reason=None,
        supporting_chunk_ids=tuple(
            source.chunk_id for source in sources if source.is_supporting
        ),
        rendered_source_chunk_ids=tuple(
            source.chunk_id for source in sources if source.is_rendered
        ),
        sources=sources,
    )


def _rag_answer() -> RagAnswer:
    return RagAnswer(
        question="Контакт manager@example.com",
        resolved_question="Контакт manager@example.com",
        answer="Позвоните +7 (999) 123-45-67",
        model="answer-model",
        sources=[],
        chunks_used=1,
        prompt_length=10,
        elapsed_seconds=0.2,
        retrieval_limit=5,
        candidate_limit=30,
        context_limit=3,
        context_score_ratio=0.65,
        diagnostic_candidates=[],
        response_kind="answer",
        query_plan={
            "intent": "equipment_it_request",
            "initial_requested_fact_type": "procedure",
            "finalized_requested_fact_type": "procedure",
            "resolution_status": "unchanged_explicit",
            "secret_prompt": "must not persist",
        },
        retrieval={"candidate_order": [29, 44], "duration_ms": 12.0, "raw_prompt": "omit"},
        context={
            "selected_context": [
                {"chunk_id": 29, "source": "a.pdf", "title": "A", "section": "S1"},
                {"chunk_id": 44, "source": "b.pdf", "title": "B", "section": "S2"},
            ],
        },
        evidence={
            "answer_mode": "full_answer",
            "supporting_chunk_ids": [29],
            "non_supporting_chunk_ids": [44],
        },
        answer_contract={
            "answer_mode": "full_answer",
            "allowed_chunk_ids": [29],
            "source_entries": [
                {
                    "chunk_id": 29,
                    "source": "a.pdf",
                    "source_title": "A",
                    "section": "S1",
                    "record_key": None,
                    "supported_requirement_ids": ["primary_procedure"],
                }
            ],
        },
    )
