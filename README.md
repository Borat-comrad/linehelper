# LineHelper

LineHelper - локальный AI-агент для компании Serviceline.

Сейчас проект находится на этапе **MemoryStore MVP**: реализуется ядро долгосрочной локальной памяти, которое позже сможет использоваться оркестратором LineHelper.

## Что уже реализовано

В текущей версии есть локальная память на **SQLite + FTS5**. Она хранит текстовые фрагменты в таблице `memory_chunks` и поддерживает полнотекстовый поиск через FTS-индекс.

Память разделена на два namespace:

- `semantic` - стабильные корпоративные знания: регламенты, инструкции, оргструктура, ЦКП, ЗРС, документооборот.
- `episodic` - подтвержденный практический опыт КП, сделок, предпочтений клиентов и рабочих аргументов.

Класс `MemoryStore` уже поддерживает:

- `ensure_schema()` - создает папку базы и SQL-схему, если их еще нет.
- `add_chunk()` - добавляет один текстовый фрагмент в память.
- `search_fts()` - ищет фрагменты через SQLite FTS5.
- `save_experience()` - сохраняет подтвержденный опыт в `episodic`.
- `delete_chunk()` - удаляет фрагмент памяти по id.
- `expire_old_episodes()` - удаляет устаревшие записи из `episodic`.

Также реализованы:

- проверка допустимых namespace: `semantic` и `episodic`;
- сохранение и возврат `metadata`;
- SQLite-триггеры для синхронизации основной таблицы и FTS-индекса;
- pytest-тесты для MemoryStore.

## Что пока не входит в этот этап

Текущий этап еще **не включает**:

- загрузчик PDF/DOCX/TXT;
- автоматический chunking документов;
- embeddings;
- hybrid search;
- LLM;
- интеграцию с 1С;
- UI.

## Структура проекта

```text
data/
  raw_docs/
  memory/

linehelper/
  memory/
    __init__.py
    schema.py
    memory_store.py

scripts/
  init_memory_db.py
  smoke_test_semantic_memory.py
  tests/
    test_memory_store.py

requirements.txt
README.md
```

## Установка зависимостей

В Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Инициализация базы памяти

```powershell
python scripts/init_memory_db.py
```

Ожидаемый вывод:

```text
Memory DB initialized: data/memory/linehelper_memory.db
```

После запуска будет создана локальная база:

```text
data/memory/linehelper_memory.db
```

## Проверка базы

В PowerShell можно проверить, что файл базы создан:

```powershell
Test-Path data/memory/linehelper_memory.db
```

Ожидаемый результат:

```text
True
```

## Запуск тестов

Тесты MemoryStore находятся здесь:

```text
scripts/tests/test_memory_store.py
```

Команда запуска для Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest scripts\tests --basetemp .\.venv\pytest-tmp -p no:cacheprovider
```

На некоторых Windows-машинах pytest может не иметь доступа к системной Temp-папке. Поэтому временная папка тестов явно задается внутри `.venv`, которая уже игнорируется Git.

## Ручной smoke-тест semantic memory

Smoke-скрипт не является pytest-тестом. Это ручной диагностический сценарий, который проверяет простой end-to-end путь:

```text
создать схему -> добавить semantic chunk -> найти его через FTS -> вывести результат
```

Запуск:

```powershell
python scripts/smoke_test_semantic_memory.py
```

Ожидаемый результат:

```text
Semantic memory smoke test passed.
```

Smoke-скрипт пишет в рабочую локальную базу:

```text
data/memory/linehelper_memory.db
```

Это нормально, потому что сценарий предназначен для ручной диагностики, а не для автоматического тестового набора.

## Минимальный пример использования

```python
from linehelper.memory.memory_store import MemoryStore

store = MemoryStore()
store.ensure_schema()

store.add_chunk(
    namespace="semantic",
    doc_type="instruction",
    title="Согласование ЗРС",
    text="ЗРС нужно согласовать до передачи документов в бухгалтерию.",
    source="demo",
    section="approval",
)

results = store.search_fts("ЗРС", namespace="semantic")

for item in results:
    print(item["title"], item["score"])
```
