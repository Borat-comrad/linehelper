---
title: Карта тестирования
type: testing
status: частично реализовано
updated: 2026-06-18
related:
  - "[[03_Module_Map]]"
  - "[[14_Tech_Debt]]"
  - "[[05_Memory_System]]"
  - "[[15_Roadmap]]"
---

# Карта тестирования

## Найденные тесты

Файл: `scripts/tests/test_memory_store.py`.

Проверяется:

- создание схемы и БД;
- добавление semantic chunk;
- поиск semantic chunk через FTS;
- фильтрация поиска по namespace;
- ошибка на неверный namespace;
- ошибка на пустой текст;
- сохранение и возврат metadata;
- удаление chunk из поиска;
- сохранение episodic proposal experience;
- удаление устаревших episodic chunk без удаления semantic chunk.

## Как запускать

Из README:

```powershell
.\.venv\Scripts\python.exe -m pytest scripts\tests --basetemp .\.venv\pytest-tmp -p no:cacheprovider
```

Причина явного `--basetemp`: на некоторых Windows-машинах pytest может не иметь доступа к системной Temp-папке.

## Smoke tests

Файл: `scripts/smoke_test_semantic_memory.py`.

Проверяет ручной end-to-end путь:

```text
создать схему -> добавить semantic chunk -> найти через FTS -> вывести результат
```

Запуск:

```powershell
python scripts/smoke_test_semantic_memory.py
```

Ожидаемый результат из README:

```text
Semantic memory smoke test passed.
```

## Непокрытые критичные сценарии

- Загрузка PDF/DOCX/TXT.
- Chunking.
- Миграции схемы.
- Большие объемы данных.
- Нормализация и безопасность metadata.
- Поиск по русскому языку на реальных документах.
- Rerank и source citation.
- Интеграция с 1С.
- UI и оркестратор.
- Политика сохранения episodic memory.

## Минимальный набор тестов для MVP

1. Unit-тесты `MemoryStore`.
2. Тесты схемы и FTS-триггеров.
3. Тест загрузки одного TXT/DOCX/PDF после появления loader.
4. Тест reindex одного документа.
5. Тест, что 1С-данные не сохраняются в память автоматически.
6. Тест подтверждения перед `save_experience()`.
7. Smoke-test полного сценария: документ -> память -> поиск -> ответ с источником.

## Связанные заметки

- Родительская тема: [[03_Module_Map]]
- Тестируемая подсистема: [[05_Memory_System]]
- Техдолг тестов: [[14_Tech_Debt#TD-008 Нет end-to-end тестов]]
- Roadmap тестирования: [[15_Roadmap]]
- Архитектурные решения: [[16_Decision_Log]]
