---
title: Конфигурация и окружение
type: config
status: актуально по файлам
updated: 2026-06-18
related:
  - "[[11_Testing_Map]]"
  - "[[05_Memory_System]]"
  - "[[14_Tech_Debt]]"
  - "[[15_Roadmap]]"
---

# Конфигурация и окружение

## Как поднимается проект

По README:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/init_memory_db.py
```

## Зависимости

`requirements.txt`:

- `pymupdf`;
- `python-docx`;
- `pytest`.

`pymupdf` и `python-docx` пока не используются в коде загрузчика, потому что загрузчик документов не реализован.

## База данных

Дефолтный путь в `MemoryStore`:

```text
data/memory/linehelper_memory.db
```

Init-скрипт использует тот же путь через `PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"`.

## `.env`

`.gitignore` исключает:

```text
.env
*.env
```

Фактического `.env.example` в репозитории нет. Уточнить: какие переменные понадобятся для LLM, 1С и будущего UI.

## Локальные пути

| Путь | Назначение | Git |
| --- | --- | --- |
| `data/memory/` | SQLite-база памяти | `.db` игнорируется |
| `data/raw_docs/` | будущие исходные документы | есть `.gitkeep` |
| `.venv/` | локальное окружение | игнорируется |
| `.idea/` | настройки IDE | игнорируется |

## Ollama/LLM

В коде нет LLM-интеграции и Ollama. Если локальная LLM будет добавлена, нужно описать:

- модель;
- endpoint;
- лимиты контекста;
- правила передачи данных;
- fallback при недоступности модели.

## Что должно быть в `.gitignore`

Уже есть:

- virtualenv;
- Python cache;
- `.idea`;
- `.env`;
- архивы;
- Excel-файлы;
- `data/memory/*.db`;
- `.understand-anything/`;
- `.agents/`.

## Что нельзя коммитить

- `.env` и любые credentials.
- SQLite-БД с рабочей памятью.
- реальные документы клиентов.
- выгрузки из 1С.
- временные Excel-файлы.
- приватные ключи и токены.

## Связанные заметки

- Родительская тема: [[00_INDEX]]
- Память и путь к БД: [[05_Memory_System]]
- Проверки запуска: [[11_Testing_Map]]
- Конфигурационный техдолг: [[14_Tech_Debt#TD-009 Нет `.env.example`]]
- Roadmap окружения: [[15_Roadmap]]
