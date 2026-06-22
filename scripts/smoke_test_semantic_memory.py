"""Manual smoke test for LineHelper semantic MemoryStore."""

from __future__ import annotations

import sys
import gc
import time
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402


def main() -> None:
    with TemporaryDirectory(prefix="linehelper-memory-smoke-") as temp_dir:
        db_path = Path(temp_dir) / "memory.db"
        store = MemoryStore(str(db_path))
        store.ensure_schema()

        chunk_id = store.add_chunk(
            namespace="semantic",
            doc_type="reference",
            title="Smoke test: semantic memory",
            text=(
                "Artificial smoke marker linehelpersemanticmemory. "
                "Semantic memory stores searchable curated chunks."
            ),
            source="manual_smoke_test",
            section="semantic_memory_smoke",
            metadata={
                "topic": "semantic_memory",
                "test_type": "smoke",
            },
        )

        results = store.search_fts(
            "linehelpersemanticmemory",
            namespace="semantic",
            limit=5,
        )

        print(f"Created chunk id: {chunk_id}")
        print(f"Found results: {len(results)}")

        if not results:
            raise SystemExit("Semantic memory smoke test failed: no results found.")

        first_result = results[0]
        print(f"First title: {first_result['title']}")
        print(f"First source: {first_result['source']}")
        print(f"First metadata: {first_result['metadata']}")
        print("Semantic memory smoke test passed.")

        del store
        gc.collect()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
