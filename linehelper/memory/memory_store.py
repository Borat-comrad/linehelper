"""Minimal SQLite-based Memory Store for LineHelper."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from linehelper.memory.schema import MEMORY_SCHEMA_SQL


ALLOWED_NAMESPACES = frozenset({"semantic", "episodic"})


def _utc_now_iso() -> str:
    """Return current UTC datetime in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _validate_namespace(namespace: str) -> None:
    """Validate memory namespace."""
    if namespace not in ALLOWED_NAMESPACES:
        allowed = ", ".join(sorted(ALLOWED_NAMESPACES))
        raise ValueError(
            f"Invalid namespace: {namespace!r}. "
            f"Allowed namespaces: {allowed}"
        )


def _validate_limit(limit: int) -> None:
    """Validate search result limit."""
    if limit <= 0:
        raise ValueError("limit must be greater than 0")


def _metadata_to_json(metadata: dict[str, Any] | None) -> str | None:
    """Serialize metadata dict to JSON string."""
    if metadata is None:
        return None

    return json.dumps(metadata, ensure_ascii=False)


def _metadata_from_json(metadata_json: str | None) -> dict[str, Any]:
    """Deserialize metadata JSON string to dict."""
    if not metadata_json:
        return {}

    try:
        value = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}

    if isinstance(value, dict):
        return value

    return {}


class MemoryStore:
    """Small helper around the local SQLite memory database."""

    def __init__(self, db_path: str = "data/memory/linehelper_memory.db"):
        self.db_path = Path(db_path)

    def ensure_schema(self) -> None:
        """Create database directory and schema if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            connection.executescript(MEMORY_SCHEMA_SQL)
            connection.commit()

    def add_chunk(
        self,
        namespace: str,
        text: str,
        doc_type: str | None = None,
        title: str | None = None,
        source: str | None = None,
        page: int | None = None,
        section: str | None = None,
        expires_at: str | None = None,
        priority: float = 1.0,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Add one text chunk to memory and return its database id."""
        _validate_namespace(namespace)

        if not text or not text.strip():
            raise ValueError("text must not be empty")

        created_at = _utc_now_iso()
        metadata_json = _metadata_to_json(metadata)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_chunks (
                    namespace,
                    doc_type,
                    title,
                    text,
                    source,
                    page,
                    section,
                    created_at,
                    expires_at,
                    priority,
                    confidence,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    namespace,
                    doc_type,
                    title,
                    text.strip(),
                    source,
                    page,
                    section,
                    created_at,
                    expires_at,
                    priority,
                    confidence,
                    metadata_json,
                ),
            )
            connection.commit()

            return int(cursor.lastrowid)

    def save_experience(
        self,
        *,
        summary: str,
        client: str | None = None,
        item_code: str | None = None,
        result: str | None = None,
        title: str | None = None,
        ttl_days: int = 90,
        confidence: float = 1.0,
        priority: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Save confirmed practical experience into episodic memory."""
        if ttl_days <= 0:
            raise ValueError("ttl_days must be greater than 0")

        expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()

        experience_metadata: dict[str, Any] = {
            "client": client,
            "item_code": item_code,
            "result": result,
            "ttl_days": ttl_days,
        }

        if metadata:
            experience_metadata.update(metadata)

        experience_title = title or self._build_experience_title(
            client=client,
            item_code=item_code,
            result=result,
        )

        return self.add_chunk(
            namespace="episodic",
            doc_type="proposal_experience",
            title=experience_title,
            text=summary,
            source="confirmed_experience",
            expires_at=expires_at,
            priority=priority,
            confidence=confidence,
            metadata=experience_metadata,
        )

    def search_fts(
        self,
        query: str,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memory with SQLite FTS5 and return plain dictionaries."""
        if not query or not query.strip():
            return []

        if namespace is not None:
            _validate_namespace(namespace)

        _validate_limit(limit)

        sql = """
            SELECT
                memory_chunks.id,
                memory_chunks.namespace,
                memory_chunks.doc_type,
                memory_chunks.title,
                memory_chunks.text,
                memory_chunks.source,
                memory_chunks.page,
                memory_chunks.section,
                memory_chunks.created_at,
                memory_chunks.expires_at,
                memory_chunks.priority,
                memory_chunks.confidence,
                memory_chunks.metadata_json,
                bm25(memory_chunks_fts) AS score
            FROM memory_chunks_fts
            JOIN memory_chunks
                ON memory_chunks_fts.rowid = memory_chunks.id
            WHERE memory_chunks_fts MATCH ?
        """

        params: list[Any] = [query.strip()]

        if namespace is not None:
            sql += " AND memory_chunks.namespace = ?"
            params.append(namespace)

        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()

        return [self._row_to_result(row) for row in rows]

    def delete_chunk(self, chunk_id: int) -> bool:
        """Delete one memory chunk by id. Return True if a row was deleted."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memory_chunks WHERE id = ?",
                (chunk_id,),
            )
            connection.commit()

            return cursor.rowcount > 0

    def expire_old_episodes(self) -> int:
        """Delete expired episodic memory chunks and return deleted count."""
        now = _utc_now_iso()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM memory_chunks
                WHERE namespace = 'episodic'
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (now,),
            )
            connection.commit()

            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
        """Convert SQLite row to stable search result dict."""
        return {
            "id": row["id"],
            "namespace": row["namespace"],
            "doc_type": row["doc_type"],
            "title": row["title"],
            "text": row["text"],
            "source": row["source"],
            "page": row["page"],
            "section": row["section"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "priority": row["priority"],
            "confidence": row["confidence"],
            "score": row["score"],
            "metadata": _metadata_from_json(row["metadata_json"]),
        }

    @staticmethod
    def _build_experience_title(
        *,
        client: str | None,
        item_code: str | None,
        result: str | None,
    ) -> str:
        parts = ["Confirmed experience"]

        if client:
            parts.append(f"client={client}")

        if item_code:
            parts.append(f"item={item_code}")

        if result:
            parts.append(f"result={result}")

        return " | ".join(parts)