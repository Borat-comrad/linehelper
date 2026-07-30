"""Minimal SQLite-based Memory Store for LineHelper."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from linehelper.memory.schema import MEMORY_SCHEMA_SQL


ALLOWED_NAMESPACES = frozenset({"semantic", "episodic"})
ALLOWED_FTS_MATCH_MODES = frozenset({"all", "any", "prefix_any"})
ALLOWED_METADATA_FILTERS = frozenset(
    {
        "knowledge_domain",
        "logical_unit_title",
        "logical_unit_type",
        "record_key",
        "source_file",
        "source_version",
    }
)


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


def _prepare_fts_query(query: str) -> str:
    """Convert a user query into a conservative FTS5 MATCH expression."""
    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", query)
    return " ".join(tokens)


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

        fts_query = _prepare_fts_query(query)
        if not fts_query:
            return []

        params: list[Any] = [fts_query]

        if namespace is not None:
            sql += " AND memory_chunks.namespace = ?"
            params.append(namespace)

        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()

        return [self._row_to_result(row) for row in rows]

    def search_fts_terms(
        self,
        terms: Sequence[str],
        *,
        match_mode: str = "all",
        namespace: str | None = None,
        limit: int = 5,
        doc_types: Sequence[str] | None = None,
        metadata_filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search FTS with a structured, internally generated MATCH expression.

        Unlike ``search_fts``, this read-only API preserves safe prefix queries
        needed by retrieval planning. Callers provide terms, not raw FTS
        syntax, so operators cannot be injected through user text.
        """
        if match_mode not in ALLOWED_FTS_MATCH_MODES:
            allowed = ", ".join(sorted(ALLOWED_FTS_MATCH_MODES))
            raise ValueError(
                f"Invalid FTS match mode: {match_mode!r}. Allowed modes: {allowed}"
            )
        if namespace is not None:
            _validate_namespace(namespace)
        _validate_limit(limit)
        _validate_metadata_filters(metadata_filters)

        fts_query = _structured_fts_query(terms, match_mode=match_mode)
        if not fts_query:
            return []

        sql = _search_select_sql() + " WHERE memory_chunks_fts MATCH ?"
        params: list[Any] = [fts_query]
        sql, params = _append_read_filters(
            sql,
            params,
            namespace=namespace,
            doc_types=doc_types,
            metadata_filters=metadata_filters,
        )
        sql += " ORDER BY score, memory_chunks.id LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()

        return [self._row_to_result(row) for row in rows]

    def search_chunks_by_metadata(
        self,
        *,
        namespace: str | None = None,
        source: str | None = None,
        doc_types: Sequence[str] | None = None,
        metadata_filters: Mapping[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return chunks through exact read-only metadata/source filters."""
        if namespace is not None:
            _validate_namespace(namespace)
        _validate_limit(limit)
        _validate_metadata_filters(metadata_filters)
        if (
            namespace is None
            and not source
            and not doc_types
            and not metadata_filters
        ):
            raise ValueError("at least one metadata/source filter is required")

        sql = _chunk_select_sql() + " WHERE 1 = 1"
        params: list[Any] = []
        sql, params = _append_read_filters(
            sql,
            params,
            namespace=namespace,
            doc_types=doc_types,
            metadata_filters=metadata_filters,
        )
        if source:
            sql += " AND memory_chunks.source = ?"
            params.append(source)
        sql += " ORDER BY memory_chunks.id LIMIT ?"
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

    def delete_chunks_by_metadata(
        self,
        *,
        namespace: str,
        metadata_filters: dict[str, Any],
    ) -> int:
        """Delete chunks in one namespace whose metadata contains all filters."""
        _validate_namespace(namespace)
        if not metadata_filters:
            raise ValueError("metadata_filters must not be empty")

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, metadata_json
                FROM memory_chunks
                WHERE namespace = ?
                """,
                (namespace,),
            ).fetchall()

            chunk_ids: list[int] = []
            for row in rows:
                metadata = _metadata_from_json(row["metadata_json"])
                if all(metadata.get(key) == value for key, value in metadata_filters.items()):
                    chunk_ids.append(int(row["id"]))

            if not chunk_ids:
                return 0

            cursor = connection.executemany(
                "DELETE FROM memory_chunks WHERE id = ?",
                [(chunk_id,) for chunk_id in chunk_ids],
            )
            connection.commit()

            return cursor.rowcount

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


def _search_select_sql() -> str:
    return """
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
    """


def _chunk_select_sql() -> str:
    return """
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
            NULL AS score
        FROM memory_chunks
    """


def _append_read_filters(
    sql: str,
    params: list[Any],
    *,
    namespace: str | None,
    doc_types: Sequence[str] | None,
    metadata_filters: Mapping[str, Any] | None,
) -> tuple[str, list[Any]]:
    if namespace is not None:
        sql += " AND memory_chunks.namespace = ?"
        params.append(namespace)

    clean_doc_types = tuple(
        dict.fromkeys(str(value).strip() for value in doc_types or () if str(value).strip())
    )
    if clean_doc_types:
        placeholders = ", ".join("?" for _ in clean_doc_types)
        sql += f" AND memory_chunks.doc_type IN ({placeholders})"
        params.extend(clean_doc_types)

    for key, value in (metadata_filters or {}).items():
        path = f"$.{key}"
        values = (
            tuple(value)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            else (value,)
        )
        values = tuple(item for item in values if item is not None)
        if not values:
            sql += " AND 0 = 1"
            continue
        if len(values) == 1:
            sql += " AND json_extract(memory_chunks.metadata_json, ?) = ?"
            params.extend((path, values[0]))
            continue
        placeholders = ", ".join("?" for _ in values)
        sql += (
            " AND json_extract(memory_chunks.metadata_json, ?) "
            f"IN ({placeholders})"
        )
        params.append(path)
        params.extend(values)

    return sql, params


def _validate_metadata_filters(
    metadata_filters: Mapping[str, Any] | None,
) -> None:
    unknown = set(metadata_filters or {}).difference(ALLOWED_METADATA_FILTERS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unsupported metadata filter(s): {names}")


def _structured_fts_query(
    terms: Sequence[str],
    *,
    match_mode: str,
) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё_]+", str(term)):
            key = token.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            tokens.append(token)
    if not tokens:
        return ""

    if match_mode == "prefix_any":
        expressions = [
            f"{token}*" if len(token) >= 3 else f'"{token}"'
            for token in tokens
        ]
        return " OR ".join(expressions)

    expressions = [f'"{token}"' for token in tokens]
    if match_mode == "any":
        return " OR ".join(expressions)
    return " ".join(expressions)
