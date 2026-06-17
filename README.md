# LineHelper

LineHelper - локальный AI-агент для компании Serviceline.

На текущем шаге реализован первый минимальный каркас модуля памяти LineHelper Memory Store:

- `semantic` - стабильные корпоративные знания: регламенты, инструкции, оргструктура, ЦКП, ЗРС, документооборот.
- `episodic` - подтвержденный практический опыт КП и сделок: успешные КП, причины отказов, аргументы, предпочтения клиентов.
- Локальная SQLite-база с полнотекстовым поиском FTS5.

В этом MVP-шаге не используются embeddings, LLM, веб-фреймворки, внешние API, PostgreSQL или векторная база.

## Инициализация базы памяти

```bash
python scripts/init_memory_db.py
```

Ожидаемый вывод:

```text
Memory DB initialized: data/memory/linehelper_memory.db
```

После запуска будет создана база:

```text
data/memory/linehelper_memory.db
```

## Проверка

В PowerShell можно проверить, что файл базы создан:

```powershell
Test-Path data/memory/linehelper_memory.db
```

Ожидаемый результат:

```text
True
```

## Минимальный пример использования

```python
from linehelper.memory.memory_store import MemoryStore

store = MemoryStore()

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

## Текущая структура

```text
data/
  raw_docs/
  memory/
linehelper/
  memory/
    schema.py
    memory_store.py
scripts/
  init_memory_db.py
requirements.txt
README.md
```
