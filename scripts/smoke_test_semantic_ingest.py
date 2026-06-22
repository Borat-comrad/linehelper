"""Smoke test for curated semantic ingest on artificial documents."""

from __future__ import annotations

import gc
import json
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402
from scripts.ingest_semantic_documents import (  # noqa: E402
    import_curated_chunks,
    validate_curated_chunks,
)


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


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    with TemporaryDirectory(prefix="linehelper-curated-ingest-") as temp_dir:
        temp_path = Path(temp_dir)
        raw_dir = temp_path / "raw_docs"
        curated_path = temp_path / "curated_chunks.jsonl"
        db_path = temp_path / "semantic_memory.db"
        raw_dir.mkdir(parents=True)

        source = raw_dir / "artificial_policy.txt"
        source.write_text(
            "Artificial semantic memory source for curated ingest smoke test.",
            encoding="utf-8",
        )

        rows = [
            {
                "namespace": "semantic",
                "doc_type": "zrs_policy",
                "title": "Artificial ZRS Policy",
                "source_file": source.name,
                "relative_path": str(source),
                "section": "Artificial responsibilities",
                "section_path": "Artificial ZRS Policy > Artificial responsibilities",
                "logical_unit_type": "procedure",
                "logical_unit_title": "Artificial curated procedure",
                "text": (
                    "Artificial curated marker linehelpersemanticcurated. "
                    "The subordinate describes the situation, adds the data, "
                    "and proposes the decision in one preserved procedure."
                ),
                "page_start": 1,
                "page_end": 1,
                "part_index": 1,
                "part_count": 1,
                "tags": ["artificial", "zrs"],
                "notes": "",
            },
            {
                "namespace": "semantic",
                "doc_type": "orders_policy",
                "title": "Artificial Order Policy",
                "source_file": source.name,
                "relative_path": str(source),
                "section": "Artificial order control",
                "section_path": "Artificial Order Policy > Artificial order control",
                "logical_unit_type": "policy_rule",
                "logical_unit_title": "Artificial order result rule",
                "text": (
                    "Artificial order marker linehelpersemanticorder. "
                    "A written order has one responsible executor, a deadline, "
                    "an expected result, and evidence for completion control."
                ),
                "page_start": 1,
                "page_end": 1,
                "part_index": 1,
                "part_count": 1,
                "tags": ["artificial", "orders"],
                "notes": "",
            },
        ]
        write_jsonl(curated_path, rows)

        validation = validate_curated_chunks(curated_path, raw_docs_dir=raw_dir)
        if not validation.ok:
            raise SystemExit(f"Curated ingest smoke test failed validation: {validation.errors}")

        first_report = import_curated_chunks(
            curated_path=curated_path,
            db_path=db_path,
            raw_docs_dir=raw_dir,
        )
        second_report = import_curated_chunks(
            curated_path=curated_path,
            db_path=db_path,
            raw_docs_dir=raw_dir,
        )

        store = MemoryStore(str(db_path))
        store.ensure_schema()
        curated_results = store.search_fts(
            "linehelpersemanticcurated",
            namespace="semantic",
            limit=5,
        )
        order_results = store.search_fts(
            "linehelpersemanticorder",
            namespace="semantic",
            limit=5,
        )

        rows = fetch_rows(db_path)
        metadata_values = [json.loads(row["metadata_json"]) for row in rows]

        print(f"Validated chunks: {len(validation.chunks)}")
        print(f"First added chunks: {first_report['chunks_added']}")
        print(f"Second added chunks: {second_report['chunks_added']}")
        print(f"Second duplicates skipped: {second_report['duplicates_skipped']}")
        print(f"Found curated results: {len(curated_results)}")
        print(f"Found order results: {len(order_results)}")

        if first_report["chunks_added"] != 2:
            raise SystemExit("Curated ingest smoke test failed: expected two imported chunks.")
        if second_report["chunks_added"] != 0 or second_report["duplicates_skipped"] != 2:
            raise SystemExit("Curated ingest smoke test failed: duplicate protection did not work.")
        if not curated_results or not order_results:
            raise SystemExit("Curated ingest smoke test failed: loaded text was not found.")
        if any(row["namespace"] != "semantic" for row in rows):
            raise SystemExit("Curated ingest smoke test failed: non-semantic namespace found.")
        if any(metadata.get("namespace") != "semantic" for metadata in metadata_values):
            raise SystemExit("Curated ingest smoke test failed: metadata namespace is not semantic.")
        if not all(metadata.get("chunking_strategy") == "llm_curated" for metadata in metadata_values):
            raise SystemExit("Curated ingest smoke test failed: chunking strategy metadata missing.")
        if not all(metadata.get("loader_version") == "curated_semantic_v1" for metadata in metadata_values):
            raise SystemExit("Curated ingest smoke test failed: loader version metadata missing.")
        if not all(metadata.get("content_hash") for metadata in metadata_values):
            raise SystemExit("Curated ingest smoke test failed: content_hash missing.")

        print("Curated semantic ingest smoke test passed.")

        del store
        gc.collect()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
