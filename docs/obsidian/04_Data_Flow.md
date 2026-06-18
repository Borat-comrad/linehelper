---
title: Потоки данных
type: data-flow
status: текущие потоки частично реализованы
updated: 2026-06-18
related:
  - "[[05_Memory_System]]"
  - "[[07_RAG_Search]]"
  - "[[10_Commercial_Proposal_Flow]]"
---

# Потоки данных

## Текущий реализованный поток: запись и поиск chunk

```mermaid
sequenceDiagram
    participant Caller as Скрипт или тест
    participant Store as MemoryStore
    participant DB as SQLite
    participant FTS as FTS5

    Caller->>Store: ensure_schema()
    Store->>DB: CREATE TABLE / CREATE VIRTUAL TABLE / TRIGGER
    Caller->>Store: add_chunk(namespace, text, metadata)
    Store->>DB: INSERT INTO memory_chunks
    DB->>FTS: триггер синхронизирует индекс
    Caller->>Store: search_fts(query, namespace)
    Store->>FTS: MATCH query
    Store->>DB: JOIN memory_chunks
    Store-->>Caller: list[dict]
```

## Запрос пользователя -> ответ

Текущий статус: TODO. В коде нет UI, intent processing, LLM и оркестратора.

Целевой поток:

```mermaid
sequenceDiagram
    participant User as Пользователь
    participant UI as UI
    participant O as Оркестратор
    participant M as MemoryStore
    participant C as 1С
    participant LLM as LLM

    User->>UI: вопрос или задача
    UI->>O: текст + контекст
    O->>O: intent processing
    O->>M: поиск в памяти
    O->>C: запрос актуальных данных при необходимости
    O->>LLM: сбор ответа с источниками
    LLM-->>O: черновик ответа
    O-->>UI: ответ и ссылки на источники
```

## Загрузка документа -> semantic memory

Текущий статус: TODO. В `requirements.txt` есть `pymupdf` и `python-docx`, но модулей загрузки, парсинга и chunking нет.

```mermaid
sequenceDiagram
    participant User as Пользователь
    participant Loader as Загрузчик документов
    participant Chunker as Chunker
    participant Store as MemoryStore

    User->>Loader: PDF/DOCX/TXT
    Loader->>Loader: извлечение текста
    Loader->>Chunker: текст + metadata
    Chunker->>Store: add_chunk(namespace="semantic")
    Store-->>Chunker: chunk_id
```

## Успешное КП -> episodic memory

Фактически реализован метод `save_experience()`, но нет полноценного КП-flow и подтверждения пользователем.

```mermaid
sequenceDiagram
    participant Manager as Менеджер
    participant Proposal as КП flow
    participant Store as MemoryStore

    Manager->>Proposal: подтверждает успешный результат
    Proposal->>Store: save_experience(summary, client, item_code, result)
    Store->>Store: expires_at = now + ttl_days
    Store-->>Proposal: id episodic-записи
```

## Запрос к 1С -> оперативные данные

Текущий статус: TODO. Важное правило: данные из 1С считаются актуальными операционными данными, а не памятью.

```mermaid
sequenceDiagram
    participant O as Оркестратор
    participant C as 1С
    participant Answer as Ответ пользователю

    O->>C: запрос цены/остатка/статуса
    C-->>O: актуальные данные
    O->>Answer: использует данные в ответе
    Note over O,C: Не сохранять в semantic/episodic без отдельного правила
```

