---
title: Онбординг нового разработчика
type: onboarding
status: актуально по текущему проекту
updated: 2026-06-18
related:
  - "[[00_INDEX]]"
  - "[[03_Module_Map]]"
  - "[[11_Testing_Map]]"
---

# Онбординг нового разработчика

## Понять проект за 30 минут

1. Прочитать `README.md`.
2. Открыть [[00_INDEX]].
3. Посмотреть [[05_Memory_System]].
4. Прочитать `linehelper/memory/memory_store.py`.
5. Прочитать `linehelper/memory/schema.py`.
6. Посмотреть тесты в `scripts/tests/test_memory_store.py`.

## Какие файлы читать первыми

| Путь | Почему |
| --- | --- |
| `README.md` | состояние проекта и команды запуска |
| `linehelper/memory/memory_store.py` | основной код |
| `linehelper/memory/schema.py` | структура БД |
| `scripts/tests/test_memory_store.py` | ожидаемое поведение |
| `scripts/init_memory_db.py` | создание БД |
| `scripts/smoke_test_semantic_memory.py` | ручной e2e для semantic memory |

## Как запустить

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/init_memory_db.py
```

## Как проверить

```powershell
.\.venv\Scripts\python.exe -m pytest scripts\tests --basetemp .\.venv\pytest-tmp -p no:cacheprovider
python scripts/smoke_test_semantic_memory.py
```

## Как безопасно вносить изменения

- Не коммитить `.env`, `.db`, документы клиентов и выгрузки.
- Не менять схему без плана миграции.
- Для новой логики писать тест до или вместе с кодом.
- Не сохранять реальные чувствительные данные в тесты.
- Разделять semantic, episodic и оперативные данные.

## Как не сломать память

- Всегда валидировать namespace.
- Не обходить `MemoryStore` прямыми SQL-записями без причины.
- Проверять FTS-поиск после изменения схемы.
- Не сохранять неподтвержденный опыт в `episodic`.
- Для удаления учитывать FTS-триггеры.

## Как оформлять новые решения

1. Добавить запись в [[16_Decision_Log]].
2. Обновить связанную заметку.
3. Если появился долг, добавить пункт в [[14_Tech_Debt]].
4. Если изменился сценарий, обновить [[04_Data_Flow]] или [[20_Project_Graph]].

