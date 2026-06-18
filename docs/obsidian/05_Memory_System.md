---
title: Система памяти
type: subsystem
status: реализовано частично
updated: 2026-06-18
related:
  - "[[03_Module_Map]]"
  - "[[07_RAG_Search]]"
  - "[[09_Document_Loading]]"
  - "[[10_Commercial_Proposal_Flow]]"
  - "[[14_Tech_Debt]]"
---

# Система памяти

## Зачем нужна память

Память нужна, чтобы LineHelper мог находить корпоративные знания и учитывать подтвержденный практический опыт. Сейчас это ядро реализовано как локальный SQLite Memory Store.

## Semantic и episodic

| Namespace | Назначение | Что хранить | Статус |
| --- | --- | --- | --- |
| `semantic` | Стабильные знания | инструкции, регламенты, оргструктура, документы | storage готов, загрузка TODO |
| `episodic` | Подтвержденный опыт | успешные КП, сделки, рабочие аргументы | `save_experience()` готов частично |

## Что нельзя сохранять

- Пароли, токены, приватные ключи.
- Реальные учетные данные.
- Неподтвержденные персональные или коммерческие детали.
- Операционные данные из 1С как "память" без явного правила.
- Черновики КП как успешный опыт без подтверждения пользователя.

## Текущая реализация

Основной файл: `linehelper/memory/memory_store.py`.

Схема: `linehelper/memory/schema.py`.

Таблица `memory_chunks`:

| Поле | Роль |
| --- | --- |
| `id` | первичный ключ |
| `namespace` | `semantic` или `episodic` |
| `doc_type` | тип документа или записи |
| `title` | заголовок chunk |
| `text` | текст chunk |
| `source` | источник |
| `page` | страница, если есть |
| `section` | раздел |
| `created_at` | дата создания |
| `expires_at` | дата истечения |
| `priority` | приоритет |
| `confidence` | уверенность |
| `metadata_json` | произвольные metadata в JSON |

FTS-таблица `memory_chunks_fts` индексирует `title`, `text`, `source`. Синхронизация выполняется SQLite-триггерами `memory_chunks_ai`, `memory_chunks_ad`, `memory_chunks_au`.

## TTL episodic memory

`save_experience()` по умолчанию ставит `ttl_days=90` и записывает `expires_at`. `expire_old_episodes()` удаляет только записи `namespace = 'episodic'`, у которых `expires_at <= now`. Semantic-записи не удаляются этим методом.

## Приоритет semantic над episodic

В коде пока нет логики ранжирования между semantic и episodic, кроме полей `priority` и `confidence`. Правило "semantic важнее episodic" должно быть реализовано на уровне будущего оркестратора или search/rerank слоя.

## FTS/BM25/vector/hybrid search

- FTS5 реализован.
- `search_fts()` использует `MATCH`.
- В результат возвращается `bm25(memory_chunks_fts) AS score`.
- Embeddings не реализованы.
- Vector search не реализован.
- Hybrid search не реализован.
- Rerank не реализован.

## Ограничения

- Нет миграций схемы.
- Нет нормализации metadata.
- Нет soft delete.
- Нет проверки размера chunk.
- Нет санитайзера чувствительных данных.
- Нет отдельного слоя доступа к БД для транзакций нескольких операций.
- Нет политик "что можно запомнить".
- Нет автоматического rebuild FTS-индекса.

## Возможная миграция

Гипотеза: после MVP можно оставить SQLite как metadata store, а embeddings перенести в Qdrant или другую vector DB. В этом варианте `memory_chunks.id` может стать стабильным идентификатором для связи metadata, текста и vector-представления. См. [[15_Roadmap]].

## Связанные заметки

- Родительская тема: [[02_Architecture_Map]]
- Фактические модули: [[03_Module_Map]]
- Поиск: [[07_RAG_Search]]
- Загрузка документов: [[09_Document_Loading]]
- КП и episodic memory: [[10_Commercial_Proposal_Flow]]
- Техдолги: [[14_Tech_Debt]]
