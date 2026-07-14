"""Smoke test for organization structure import and FTS retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402
from linehelper.organization.indexer import DEFAULT_SOURCE_PATH, import_company_structure  # noqa: E402


SMOKE_DB = PROJECT_ROOT / "data" / "memory" / "company_structure_smoke.db"

QUERIES = [
    "кто отвечает за таможню",
    "телефон ответственного за таможню",
    "руководитель закупок",
    "контакт руководителя закупок",
    "кадровый учет",
    "проектирование и моделирование",
    "продажи ЭФЕС",
    "закупки IMETA",
    "склад",
    "доставка клиенту",
    "вакантные должности",
    "исполнительный совет",
    "совмещения Хилько",
    "структура логистики",
    "сервисные проекты",
]


def main() -> None:
    SMOKE_DB.parent.mkdir(parents=True, exist_ok=True)
    if SMOKE_DB.exists():
        SMOKE_DB.unlink()

    report = import_company_structure(source_path=DEFAULT_SOURCE_PATH, db_path=SMOKE_DB)
    print(f"Smoke DB: {SMOKE_DB.relative_to(PROJECT_ROOT)}")
    print(f"Chunks written: {report.total_chunks_written}")
    print(f"Phones recognized: {report.phones_recognized}")
    print(f"Emails recognized: {report.emails_recognized}")
    print()

    store = MemoryStore(str(SMOKE_DB))
    failures: list[str] = []
    for question in QUERIES:
        results = store.search_fts(question, namespace="semantic", limit=3)
        print(f"QUESTION: {question}")
        if not results:
            print("NO RESULTS")
            failures.append(question)
            print()
            continue
        first = results[0]
        print(f"TITLE: {first['title']}")
        print(f"TEXT:\n{first['text']}")
        print(f"SOURCE: {first['source']}")
        print(f"METADATA: {json.dumps(first['metadata'], ensure_ascii=False, sort_keys=True)}")
        print(f"SCORE: {first['score']}")
        has_phone = bool(first["metadata"].get("phone") or first["metadata"].get("fallback_phone"))
        has_email = bool(first["metadata"].get("email"))
        print(f"CONTACTS_EXTRACTED: phone={has_phone} email={has_email}")
        print()

    if failures:
        print("Critical smoke queries without results:")
        for question in failures:
            print(f"  - {question}")
        raise SystemExit(1)

    print("Company structure smoke test passed.")


if __name__ == "__main__":
    main()
