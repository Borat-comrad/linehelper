"""Smoke test for semantic document ingestion."""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402
from scripts.ingest_semantic_documents import ingest_directory  # noqa: E402


def main() -> None:
    with TemporaryDirectory(prefix="linehelper-semantic-ingest-") as temp_dir:
        temp_path = Path(temp_dir)
        raw_dir = temp_path / "semantic_raw"
        db_path = temp_path / "semantic_memory.db"
        raw_dir.mkdir(parents=True)

        test_document = raw_dir / "demo_instruction.md"
        test_document.write_text(
            "# Demo instruction\n\n"
            "The unique semantic ingest marker is linehelpersemanticunique. "
            "This text proves that the document loader added a searchable chunk.",
            encoding="utf-8",
        )

        added_chunks = ingest_directory(raw_dir=raw_dir, db_path=db_path)

        store = MemoryStore(str(db_path))
        store.ensure_schema()
        results = store.search_fts(
            "linehelpersemanticunique",
            namespace="semantic",
            limit=5,
        )

        print(f"Added chunks: {added_chunks}")
        print(f"Found results: {len(results)}")

        for result in results:
            print(f"- {result['title']} | {result['source']} | {result['section']}")

        if added_chunks <= 0 or not results:
            print("Semantic ingest smoke test failed: loaded text was not found.")
            raise SystemExit(1)

        print("Semantic ingest smoke test passed.")

        # Windows can keep the SQLite file handle briefly after the last query.
        del store
        gc.collect()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
