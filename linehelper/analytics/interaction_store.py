"""SQLite persistence isolated from LineHelper semantic memory."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from linehelper.analytics.models import (
    ANALYTICS_SCHEMA_VERSION,
    CleanupResult,
    InteractionFeedback,
    InteractionRecord,
)


class InteractionStore:
    """Short-transaction SQLite store with an idempotent versioned schema."""

    def __init__(self, db_path: Path, *, busy_timeout_ms: int = 1000) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS analytics_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interactions (
                    interaction_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    user_id_hash TEXT,
                    created_at_utc TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    original_question TEXT,
                    resolved_question TEXT,
                    intent TEXT,
                    initial_requested_fact_type TEXT,
                    finalized_requested_fact_type TEXT,
                    resolution_status TEXT,
                    response_kind TEXT,
                    answer_mode TEXT,
                    final_answer TEXT,
                    clarification_requested INTEGER NOT NULL DEFAULT 0,
                    clarification_reason TEXT,
                    retrieved_chunk_ids_json TEXT NOT NULL,
                    selected_context_chunk_ids_json TEXT NOT NULL,
                    supporting_chunk_ids_json TEXT NOT NULL,
                    non_supporting_chunk_ids_json TEXT NOT NULL,
                    rendered_source_chunk_ids_json TEXT NOT NULL,
                    query_plan_json TEXT NOT NULL,
                    retrieval_diagnostics_json TEXT NOT NULL,
                    context_diagnostics_json TEXT NOT NULL,
                    evidence_diagnostics_json TEXT NOT NULL,
                    answer_contract_diagnostics_json TEXT NOT NULL,
                    total_duration_ms REAL,
                    query_analysis_duration_ms REAL,
                    retrieval_duration_ms REAL,
                    answer_generation_duration_ms REAL,
                    error_type TEXT,
                    error_message TEXT,
                    answer_model TEXT,
                    analyzer_model TEXT,
                    app_version TEXT,
                    git_commit TEXT
                );

                CREATE TABLE IF NOT EXISTS interaction_sources (
                    interaction_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    source TEXT,
                    source_title TEXT,
                    section TEXT,
                    record_key TEXT,
                    is_supporting INTEGER NOT NULL,
                    is_rendered INTEGER NOT NULL,
                    requirement_ids_json TEXT NOT NULL,
                    PRIMARY KEY (interaction_id, chunk_id),
                    FOREIGN KEY (interaction_id)
                        REFERENCES interactions(interaction_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS interaction_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    interaction_id TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK (rating IN ('positive', 'negative')),
                    reason TEXT,
                    comment TEXT,
                    FOREIGN KEY (interaction_id)
                        REFERENCES interactions(interaction_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_interactions_created_at
                    ON interactions(created_at_utc);
                CREATE INDEX IF NOT EXISTS idx_interactions_session
                    ON interactions(session_id);
                CREATE INDEX IF NOT EXISTS idx_interactions_answer_mode
                    ON interactions(answer_mode);
                CREATE INDEX IF NOT EXISTS idx_feedback_interaction_created
                    ON interaction_feedback(interaction_id, created_at_utc);
                CREATE INDEX IF NOT EXISTS idx_feedback_rating
                    ON interaction_feedback(rating);
                """
            )
            current = connection.execute(
                "SELECT value FROM analytics_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if current is not None and int(current[0]) != ANALYTICS_SCHEMA_VERSION:
                raise RuntimeError(
                    "Unsupported interaction analytics schema version: "
                    f"{current[0]}"
                )
            connection.execute(
                """
                INSERT INTO analytics_metadata(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(ANALYTICS_SCHEMA_VERSION),),
            )

    def record_interaction(self, record: InteractionRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO interactions (
                    interaction_id, session_id, user_id_hash, created_at_utc,
                    schema_version, original_question, resolved_question,
                    intent, initial_requested_fact_type,
                    finalized_requested_fact_type, resolution_status,
                    response_kind, answer_mode, final_answer,
                    clarification_requested, clarification_reason,
                    retrieved_chunk_ids_json,
                    selected_context_chunk_ids_json,
                    supporting_chunk_ids_json,
                    non_supporting_chunk_ids_json,
                    rendered_source_chunk_ids_json,
                    query_plan_json, retrieval_diagnostics_json,
                    context_diagnostics_json, evidence_diagnostics_json,
                    answer_contract_diagnostics_json, total_duration_ms,
                    query_analysis_duration_ms, retrieval_duration_ms,
                    answer_generation_duration_ms, error_type, error_message,
                    answer_model, analyzer_model, app_version, git_commit
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    record.interaction_id,
                    record.session_id,
                    record.user_id_hash,
                    _utc_iso(record.created_at_utc),
                    record.schema_version,
                    record.original_question,
                    record.resolved_question,
                    record.intent,
                    record.initial_requested_fact_type,
                    record.finalized_requested_fact_type,
                    record.resolution_status,
                    record.response_kind,
                    record.answer_mode,
                    record.final_answer,
                    int(record.clarification_requested),
                    record.clarification_reason,
                    _dump_json(record.retrieved_chunk_ids),
                    _dump_json(record.selected_context_chunk_ids),
                    _dump_json(record.supporting_chunk_ids),
                    _dump_json(record.non_supporting_chunk_ids),
                    _dump_json(record.rendered_source_chunk_ids),
                    _dump_json(record.query_plan),
                    _dump_json(record.retrieval_diagnostics),
                    _dump_json(record.context_diagnostics),
                    _dump_json(record.evidence_diagnostics),
                    _dump_json(record.answer_contract_diagnostics),
                    record.total_duration_ms,
                    record.query_analysis_duration_ms,
                    record.retrieval_duration_ms,
                    record.answer_generation_duration_ms,
                    record.error_type,
                    record.error_message,
                    record.answer_model,
                    record.analyzer_model,
                    record.app_version,
                    record.git_commit,
                ),
            )
            connection.executemany(
                """
                INSERT INTO interaction_sources (
                    interaction_id, position, chunk_id, source, source_title,
                    section, record_key, is_supporting, is_rendered,
                    requirement_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.interaction_id,
                        source.position,
                        str(source.chunk_id),
                        source.source,
                        source.source_title,
                        source.section,
                        source.record_key,
                        int(source.is_supporting),
                        int(source.is_rendered),
                        _dump_json(source.requirement_ids),
                    )
                    for source in record.sources
                ],
            )

    def record_feedback(self, feedback: InteractionFeedback) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO interaction_feedback (
                    feedback_id, interaction_id, created_at_utc,
                    rating, reason, comment
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback.feedback_id,
                    feedback.interaction_id,
                    _utc_iso(feedback.created_at_utc),
                    feedback.rating,
                    feedback.reason,
                    feedback.comment,
                ),
            )

    def cleanup_expired(
        self,
        *,
        retention_days: int,
        cutoff: datetime | None = None,
    ) -> CleanupResult:
        if retention_days <= 0 and cutoff is None:
            return CleanupResult(
                deleted_interactions=0,
                cutoff_utc=None,
                performed=False,
            )
        cutoff_utc = cutoff or datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM interactions WHERE created_at_utc < ?",
                (_utc_iso(cutoff_utc),),
            )
        return CleanupResult(
            deleted_interactions=max(0, int(cursor.rowcount)),
            cutoff_utc=cutoff_utc,
        )

    def cleanup_if_due(
        self,
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> CleanupResult:
        if retention_days <= 0:
            return CleanupResult(0, None, performed=False)
        current = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM analytics_metadata WHERE key = 'last_cleanup_utc'"
            ).fetchone()
            if row is not None:
                previous = datetime.fromisoformat(str(row[0]))
                if current - previous < timedelta(days=1):
                    return CleanupResult(0, None, performed=False)
            cutoff = current - timedelta(days=retention_days)
            cursor = connection.execute(
                "DELETE FROM interactions WHERE created_at_utc < ?",
                (_utc_iso(cutoff),),
            )
            connection.execute(
                """
                INSERT INTO analytics_metadata(key, value)
                VALUES ('last_cleanup_utc', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (_utc_iso(current),),
            )
        return CleanupResult(max(0, int(cursor.rowcount)), cutoff)

    def fetch_rows(
        self,
        table: str,
        *,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if table not in {"interactions", "interaction_feedback", "interaction_sources"}:
            raise ValueError(f"Unsupported analytics table: {table}")
        clauses: list[str] = []
        params: list[str] = []
        timestamp_column = "created_at_utc" if table != "interaction_sources" else None
        if timestamp_column and from_utc is not None:
            clauses.append(f"{timestamp_column} >= ?")
            params.append(_utc_iso(from_utc))
        if timestamp_column and to_utc is not None:
            clauses.append(f"{timestamp_column} < ?")
            params.append(_utc_iso(to_utc))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order_by = (
            " ORDER BY created_at_utc, rowid"
            if timestamp_column
            else " ORDER BY interaction_id, position"
        )
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table}{where}{order_by}",  # noqa: S608 - allowlisted table
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_iso(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat()
