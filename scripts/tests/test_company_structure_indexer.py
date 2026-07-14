from __future__ import annotations

from pathlib import Path
import sqlite3

from linehelper.memory.memory_store import MemoryStore
from linehelper.organization.indexer import build_semantic_chunks, import_company_structure
from linehelper.organization.parser import parse_company_structure


SOURCE = Path("data/raw_docs/bvr_company_structure_instruction_v2 (2).txt")


def test_indexer_creates_semantic_chunks():
    parsed = parse_company_structure(SOURCE)
    chunks = build_semantic_chunks(parsed)

    assert chunks
    assert all(chunk.namespace == "semantic" for chunk in chunks)


def test_document_is_not_saved_as_one_big_chunk():
    parsed = parse_company_structure(SOURCE)
    chunks = build_semantic_chunks(parsed)

    assert len(chunks) > 20
    assert max(len(chunk.text) for chunk in chunks) < SOURCE.read_text(encoding="utf-8").__len__()


def test_chunks_keep_source_version_and_record_key():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if chunk.doc_type == "organization_unit")

    assert chunk.metadata["source_version"] == "2025-12-17"
    assert chunk.metadata["record_key"]


def test_employee_chunk_keeps_contacts():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if chunk.title == "Зиновкин Роман")

    assert chunk.metadata["phone"] == "+7 910 298-12-38"
    assert chunk.metadata["phone_normalized"] == "+79102981238"


def test_employee_chunk_keeps_additional_phone():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if chunk.title == "Овсянникова Екатерина")

    assert "+7 920 833-60-72" in chunk.metadata["additional_phones"]
    assert "+7 920 833-60-72" in chunk.text


def test_employee_chunk_keeps_email():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if chunk.title == "Амосова Екатерина")

    assert chunk.metadata["email"] == "ekaterinaamosova@serviceline.company"


def test_responsibility_route_contains_primary_contact():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if "Таможня" in chunk.title)

    assert chunk.metadata["primary_responsible"] == "Зиновкин Роман"
    assert chunk.metadata["phone"] == "+7 910 298-12-38"


def test_employee_with_multiple_roles_is_marked():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if chunk.title == "Хилько Юлия Александровна")

    assert chunk.metadata["has_multiple_roles"] is True


def test_vacant_position_has_no_fake_employee():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed) if chunk.doc_type == "organization_vacancy")

    assert "Назначенный исполнитель отсутствует" in chunk.text
    assert "ВАКАНСИЯ" not in chunk.metadata.get("employee_name", "")


def test_inactive_entity_keeps_inactive_status():
    parsed = parse_company_structure(SOURCE)
    chunk = next(
        chunk
        for chunk in build_semantic_chunks(parsed)
        if chunk.doc_type == "organization_status" and "Исполнительный совет" in chunk.title
    )

    assert chunk.metadata["status"] == "inactive"


def test_reimport_same_version_does_not_duplicate_chunks(tmp_path):
    db_path = tmp_path / "org.db"

    first = import_company_structure(source_path=SOURCE, db_path=db_path)
    second = import_company_structure(source_path=SOURCE, db_path=db_path)

    assert first.total_chunks_written == second.total_chunks_written
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM memory_chunks WHERE namespace = 'semantic'").fetchone()[0]
    assert count == first.total_chunks_written


def test_import_does_not_touch_episodic_or_other_semantic_sources(tmp_path):
    db_path = tmp_path / "org.db"
    store = MemoryStore(str(db_path))
    store.ensure_schema()
    store.add_chunk(namespace="episodic", text="episodic keepme", metadata={"knowledge_domain": "organization_structure", "source_version": "2025-12-17"})
    store.add_chunk(namespace="semantic", text="semantic other keepme", metadata={"knowledge_domain": "other", "source_version": "2025-12-17"})

    import_company_structure(source_path=SOURCE, db_path=db_path)

    assert store.search_fts("episodic", namespace="episodic")
    assert store.search_fts("other", namespace="semantic")


def test_exclude_contacts_builds_anonymized_chunks():
    parsed = parse_company_structure(SOURCE)
    chunk = next(chunk for chunk in build_semantic_chunks(parsed, exclude_contacts=True) if chunk.title == "Зиновкин Роман")

    assert chunk.metadata["phone"] is None
    assert chunk.metadata["phone_normalized"] is None
    assert "298-12-38" not in chunk.text


def test_dry_run_does_not_write_database(tmp_path):
    db_path = tmp_path / "dry.db"

    report = import_company_structure(source_path=SOURCE, db_path=db_path, dry_run=True)

    assert report.total_chunks_written == 0
    assert not db_path.exists()
