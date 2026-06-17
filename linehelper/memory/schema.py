"""SQLite schema for the local LineHelper Memory Store."""

MEMORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    doc_type TEXT,
    title TEXT,
    text TEXT NOT NULL,
    source TEXT,
    page INTEGER,
    section TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    priority REAL DEFAULT 1.0,
    confidence REAL DEFAULT 1.0,
    metadata_json TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_chunks_fts USING fts5(
    title,
    text,
    source,
    content='memory_chunks',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memory_chunks_ai
AFTER INSERT ON memory_chunks
BEGIN
    INSERT INTO memory_chunks_fts(rowid, title, text, source)
    VALUES (new.id, new.title, new.text, new.source);
END;

CREATE TRIGGER IF NOT EXISTS memory_chunks_ad
AFTER DELETE ON memory_chunks
BEGIN
    INSERT INTO memory_chunks_fts(memory_chunks_fts, rowid, title, text, source)
    VALUES ('delete', old.id, old.title, old.text, old.source);
END;

CREATE TRIGGER IF NOT EXISTS memory_chunks_au
AFTER UPDATE ON memory_chunks
BEGIN
    INSERT INTO memory_chunks_fts(memory_chunks_fts, rowid, title, text, source)
    VALUES ('delete', old.id, old.title, old.text, old.source);

    INSERT INTO memory_chunks_fts(rowid, title, text, source)
    VALUES (new.id, new.title, new.text, new.source);
END;
"""
