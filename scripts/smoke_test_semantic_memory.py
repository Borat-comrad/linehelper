"""Manual smoke test for LineHelper semantic memory."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402


def main() -> None:
    store = MemoryStore()
    store.ensure_schema()

    chunk_id = store.add_chunk(
        namespace="semantic",
        doc_type="instruction",
        title="Smoke test: Структура ЗРС",
        text="ЗРС состоит из трех частей: ситуация, данные, решение.",
        source="manual_smoke_test",
        section="semantic_memory_smoke",
        metadata={
            "topic": "zrs",
            "test_type": "smoke",
        },
    )

    results = store.search_fts("ЗРС", namespace="semantic", limit=5)

    print(f"Created chunk id: {chunk_id}")
    print(f"Found results: {len(results)}")

    if not results:
        print("Semantic memory smoke test failed: no results found.")
        raise SystemExit(1)

    first_result = results[0]

    print(f"First title: {first_result['title']}")
    print(f"First text: {first_result['text']}")
    print(f"First source: {first_result['source']}")
    print(f"First metadata: {first_result['metadata']}")
    print("Semantic memory smoke test passed.")


if __name__ == "__main__":
    main()
