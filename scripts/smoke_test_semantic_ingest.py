"""Smoke test for semantic document ingestion on artificial documents."""

from __future__ import annotations

import gc
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402
from scripts.ingest_semantic_documents import ingest_directory  # noqa: E402


def fetch_rows(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT namespace, doc_type, source, section, text, metadata_json
            FROM memory_chunks
            ORDER BY id
            """
        ).fetchall()


def main() -> None:
    with TemporaryDirectory(prefix="linehelper-semantic-ingest-") as temp_dir:
        temp_path = Path(temp_dir)
        raw_dir = temp_path / "raw_docs"
        db_path = temp_path / "semantic_memory.db"
        raw_dir.mkdir(parents=True)

        markdown_document = raw_dir / "ИП-0004 Структура ЗРС.md"
        markdown_document.write_text(
            "# Структура ЗРС\n\n"
            "ЗРС состоит из ситуации, данных и решения. "
            "Уникальный маркер: linehelpersemanticmarkdown.\n\n"
            "## Обязанности подчиненного\n\n"
            "Подчиненный готовит заявку целиком и сохраняет контекст.\n\n"
            "1. Описать ситуацию.\n"
            "2. Добавить данные.\n"
            "3. Предложить решение.\n",
            encoding="utf-8",
        )

        text_document = raw_dir / "ИП-0005 Распоряжения.txt"
        text_document.write_text(
            "РАСПОРЯЖЕНИЯ\n\n"
            "Распоряжение должно быть письменным. "
            "Уникальный маркер: linehelpersemantictext.\n\n"
            "ПРИМЕР РАСПОРЯЖЕНИЯ\n\n"
            "Сотруднику поручается подготовить отчет.\n"
            "1. Проверить входящие данные.\n"
            "2. Передать результат руководителю.\n",
            encoding="utf-8",
        )

        first_summary = ingest_directory(raw_dir=raw_dir, db_path=db_path)
        second_summary = ingest_directory(raw_dir=raw_dir, db_path=db_path)

        store = MemoryStore(str(db_path))
        store.ensure_schema()
        md_results = store.search_fts(
            "linehelpersemanticmarkdown",
            namespace="semantic",
            limit=5,
        )
        txt_results = store.search_fts(
            "linehelpersemantictext",
            namespace="semantic",
            limit=5,
        )

        rows = fetch_rows(db_path)
        metadata_values = [__import__("json").loads(row["metadata_json"]) for row in rows]

        print(f"First added chunks: {first_summary['chunks_added']}")
        print(f"Second added chunks: {second_summary['chunks_added']}")
        print(f"Second duplicates skipped: {second_summary['duplicates_skipped']}")
        print(f"Found markdown results: {len(md_results)}")
        print(f"Found text results: {len(txt_results)}")

        if first_summary["chunks_added"] <= 0:
            raise SystemExit("Semantic ingest smoke test failed: no chunks were added.")
        if second_summary["chunks_added"] != 0 or second_summary["duplicates_skipped"] <= 0:
            raise SystemExit("Semantic ingest smoke test failed: duplicate protection did not work.")
        if not md_results or not txt_results:
            raise SystemExit("Semantic ingest smoke test failed: loaded text was not found.")
        if any(row["namespace"] != "semantic" for row in rows):
            raise SystemExit("Semantic ingest smoke test failed: non-semantic namespace found.")
        if any(metadata.get("namespace") != "semantic" for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: metadata namespace is not semantic.")
        if any(row["namespace"] == row["doc_type"] for row in rows):
            raise SystemExit("Semantic ingest smoke test failed: doc_type was used as namespace.")
        if not all(metadata.get("doc_type") for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: doc_type missing from metadata.")
        if not all(metadata.get("source_file") for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: source_file missing from metadata.")
        if not all(metadata.get("section_path") for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: section_path missing from metadata.")
        if not all(metadata.get("content_hash") for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: content_hash missing from metadata.")
        if not any("Структура ЗРС" in metadata["section_path"] for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: markdown heading path was not preserved.")
        if not any("РАСПОРЯЖЕНИЯ" in metadata["section_path"] for metadata in metadata_values):
            raise SystemExit("Semantic ingest smoke test failed: TXT heading path was not preserved.")
        if not any("1. Описать ситуацию" in row["text"] and "3. Предложить решение" in row["text"] for row in rows):
            raise SystemExit("Semantic ingest smoke test failed: list was split unexpectedly.")

        print("Semantic ingest smoke test passed.")

        # Windows can keep the SQLite file handle briefly after the last query.
        del store
        gc.collect()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
