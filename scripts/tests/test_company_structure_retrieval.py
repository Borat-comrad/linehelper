from __future__ import annotations

from pathlib import Path

import pytest

from linehelper.memory.memory_store import MemoryStore
from linehelper.organization.indexer import import_company_structure


SOURCE = Path("data/raw_docs/bvr_company_structure_instruction_v2 (2).txt")


@pytest.fixture()
def indexed_store(tmp_path) -> MemoryStore:
    db_path = tmp_path / "retrieval.db"
    import_company_structure(source_path=SOURCE, db_path=db_path)
    return MemoryStore(str(db_path))


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Какие отделения есть в компании?", "Оргструктура Serviceline"),
        ("Кто отвечает за логистику?", "Общая логистика"),
        ("Кто занимается таможенным оформлением?", "Зиновкин Роман"),
        ("Кто руководит закупками?", "Силаева Юлия"),
        ("Куда обратиться по кадровому учёту?", "Прокошина Елена Дмитриевна"),
        ("Кто отвечает за проектирование?", "Проектирование"),
        ("Кто занимается продажами ЭФЕС?", "Вербицкая Ирина"),
        ("Кто отвечает за закупки KRONES?", "Карачурин Денис"),
        ("Кто занимается складом?", "Кобозев Дмитрий"),
        ("Кто отвечает за доставку клиенту?", "Симонова Татьяна"),
        ("Какие должности совмещает Хилько Юлия?", "Хилько Юлия Александровна"),
        ("Кто руководит отделом подготовки?", "Зиновкин Роман"),
        ("Какие подразделения входят в логистику?", "Отделение 4Б"),
        ("Какие должности вакантны?", "Назначенный исполнитель отсутствует"),
        ("Активен ли исполнительный совет?", "Исполнительный совет"),
        ("Куда направить вопрос по оплатам?", "Хилько Юлия Александровна"),
        ("Кто отвечает за сервисные проекты?", "Ронис Вячеслав"),
        ("Какой телефон у ответственного за таможню?", "Зиновкин Роман"),
        ("Дай контакт руководителя закупок.", "Силаева Юлия"),
        ("Кому написать по закупкам IMETA?", "Фаюстова Ирина"),
        ("Как связаться с ответственным за кадровый учёт?", "Прокошина Елена Дмитриевна"),
        ("Кто отвечает за склад и какой у него номер?", "Кобозев Дмитрий"),
        ("Дай контакт по продажам ЭФЕС.", "Вербицкая Ирина"),
        ("К кому обратиться по техническому проектированию?", "Васильев Анатолий"),
    ],
)
def test_organization_queries_find_relevant_chunks(indexed_store: MemoryStore, query: str, expected: str):
    results = indexed_store.search_fts(query, namespace="semantic", limit=5)

    haystack = "\n".join(
        f"{result['title']}\n{result['text']}\n{result['metadata']}"
        for result in results
    )
    assert results
    assert expected in haystack
