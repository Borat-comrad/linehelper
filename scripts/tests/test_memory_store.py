from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from linehelper.memory.memory_store import MemoryStore


def make_store(tmp_path) -> MemoryStore:
    return MemoryStore(str(tmp_path / "linehelper_memory.db"))


def test_ensure_schema_creates_database_and_allows_insert(tmp_path):
    db_path = tmp_path / "memory" / "linehelper_memory.db"
    store = MemoryStore(str(db_path))

    store.ensure_schema()
    chunk_id = store.add_chunk(namespace="semantic", text="Schema smoke test")

    assert db_path.exists()
    assert isinstance(chunk_id, int)


def test_add_chunk_adds_semantic_chunk(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()

    chunk_id = store.add_chunk(
        namespace="semantic",
        text="Stable corporate instruction",
    )

    assert isinstance(chunk_id, int)
    assert chunk_id > 0


def test_search_fts_finds_added_semantic_chunk(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()
    expected_text = "ЗРС состоит из ситуации, данных и решения."
    store.add_chunk(namespace="semantic", text=expected_text)

    results = store.search_fts("ЗРС", namespace="semantic")

    assert len(results) == 1
    assert expected_text in results[0]["text"]


def test_search_fts_filters_by_namespace(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()
    store.add_chunk(
        namespace="semantic",
        text="sharedtoken semantic corporate rule",
    )
    store.add_chunk(
        namespace="episodic",
        text="sharedtoken practical sales experience",
    )

    semantic_results = store.search_fts("sharedtoken", namespace="semantic")
    episodic_results = store.search_fts("sharedtoken", namespace="episodic")

    assert semantic_results
    assert all(result["namespace"] == "semantic" for result in semantic_results)
    assert episodic_results
    assert all(result["namespace"] == "episodic" for result in episodic_results)


def test_add_chunk_rejects_invalid_namespace(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()

    with pytest.raises(ValueError):
        store.add_chunk(namespace="sematic", text="Typo in namespace")


@pytest.mark.parametrize("text", ["", "   "])
def test_add_chunk_rejects_empty_text(tmp_path, text):
    store = make_store(tmp_path)
    store.ensure_schema()

    with pytest.raises(ValueError):
        store.add_chunk(namespace="semantic", text=text)


def test_metadata_is_saved_and_returned(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()
    store.add_chunk(
        namespace="semantic",
        text="ЗРС metadata example",
        metadata={
            "topic": "zrs",
            "document_code": "ИП-0004",
        },
    )

    results = store.search_fts("metadata", namespace="semantic")

    assert results[0]["metadata"]["topic"] == "zrs"
    assert results[0]["metadata"]["document_code"] == "ИП-0004"


def test_delete_chunk_removes_record_from_search(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()
    chunk_id = store.add_chunk(
        namespace="semantic",
        text="deletable unique memory chunk",
    )

    deleted = store.delete_chunk(chunk_id)

    assert deleted is True
    assert store.search_fts("deletable", namespace="semantic") == []


def test_save_experience_stores_episodic_proposal_experience(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()

    store.save_experience(
        client="Тестовый клиент",
        item_code="0-905-21-102-2",
        result="accepted",
        summary="Клиент принял КП после аргумента по сроку поставки.",
    )

    results = store.search_fts("поставки", namespace="episodic")

    assert len(results) == 1
    result = results[0]
    assert result["namespace"] == "episodic"
    assert result["doc_type"] == "proposal_experience"
    assert result["metadata"]["client"] == "Тестовый клиент"
    assert result["metadata"]["item_code"] == "0-905-21-102-2"
    assert result["metadata"]["result"] == "accepted"
    assert result["expires_at"]


def test_expire_old_episodes_deletes_only_expired_episodic_chunks(tmp_path):
    store = make_store(tmp_path)
    store.ensure_schema()
    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=1)).isoformat()

    store.add_chunk(
        namespace="episodic",
        text="expired episodic chunk",
        expires_at=past,
    )
    store.add_chunk(
        namespace="episodic",
        text="future episodic chunk",
        expires_at=future,
    )
    store.add_chunk(
        namespace="semantic",
        text="expired semantic chunk",
        expires_at=past,
    )

    deleted_count = store.expire_old_episodes()

    assert deleted_count == 1
    assert store.search_fts("expired", namespace="episodic") == []
    assert len(store.search_fts("future", namespace="episodic")) == 1
    assert len(store.search_fts("expired", namespace="semantic")) == 1
