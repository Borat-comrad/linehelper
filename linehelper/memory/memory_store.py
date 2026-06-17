"""Minimal SQLite-based Memory Store for LineHelper."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryStore:
    """Small helper around the local SQLite memory database."""

    def __init__(self, db_path: str = "data/memory/linehelper_memory.db"):
        self.db_path = Path(db_path)

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

        created_at = datetime.now(timezone.utc).isoformat()
        metadata_json = None

        if metadata is not None:
            metadata_json = json.dumps(metadata, ensure_ascii=False)

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
                    text,
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

    def search_fts(
        self,
        query: str,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memory with SQLite FTS5 and return plain dictionaries."""

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
                bm25(memory_chunks_fts) AS score,
                memory_chunks.confidence
            FROM memory_chunks_fts
            JOIN memory_chunks ON memory_chunks_fts.rowid = memory_chunks.id
            WHERE memory_chunks_fts MATCH ?
        """
        params: list[Any] = [query]

        if namespace is not None:
            sql += " AND memory_chunks.namespace = ?"
            params.append(namespace)

        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()

        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
