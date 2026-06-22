"""RAG prompt builder for future local model calls."""

from __future__ import annotations

from collections.abc import Sequence

from linehelper.rag.retriever import RetrievedChunk, build_matched_excerpt


LONG_CHUNK_THRESHOLD = 2800


def build_rag_prompt(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    max_context_chars: int = 8000,
) -> str:
    """Build a Russian RAG prompt from a question and retrieved chunks."""
    sources_text = _format_sources(chunks, max_context_chars=max_context_chars)

    return "\n".join(
        [
            "Ты - локальный корпоративный помощник Serviceline.",
            "",
            "Ответь на вопрос пользователя только на основе источников ниже.",
            'Если в источниках нет ответа, прямо скажи: "В найденных источниках недостаточно данных".',
            "Не выдумывай факты и не добавляй информацию вне источников.",
            "Не используй знания вне контекста.",
            "Отвечай именно на вопрос пользователя, а не на похожую общую тему.",
            "Игнорируй источник, если он не относится к предмету вопроса.",
            "Не используй случайное совпадение слов как основание для ответа.",
            "Если вопрос о компании в целом, опирайся на источники про цели, замыслы и ЦКП, а не на случайные функции отдельных подразделений.",
            "Если вопрос о документообороте, используй документы и инструкции про Документооборот, 1С ДО и согласование документов.",
            "Если пользователь спрашивает, что делать, давай практические шаги только из источников.",
            "Если источники дают только частичный ответ, прямо скажи, чего в них нет.",
            "Если источники противоречат друг другу, укажи на это.",
            "В конце ответа дай список источников.",
            "",
            "Вопрос пользователя:",
            question.strip(),
            "",
            "Источники:",
            sources_text,
            "",
            "Требования к ответу:",
            "1. Дай краткий и понятный ответ.",
            "2. Сначала проверь, что источники относятся к предмету вопроса.",
            "3. Не подменяй общий вопрос о компании описанием отдельного отдела.",
            "4. Не подменяй вопрос о документообороте общими должностными инструкциями.",
            "5. Если есть порядок действий - оформи его по шагам.",
            "6. В конце укажи источники.",
        ]
    )


def _format_sources(
    chunks: Sequence[RetrievedChunk],
    *,
    max_context_chars: int,
) -> str:
    if not chunks:
        return "Источники не найдены."

    remaining_chars = max(0, max_context_chars)
    blocks: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        content = _source_content_for_prompt(chunk)
        if remaining_chars <= 0:
            content = "[Контекст обрезан из-за лимита длины prompt.]"
        elif len(content) > remaining_chars:
            content = content[:remaining_chars].rstrip() + "..."
            remaining_chars = 0
        else:
            remaining_chars -= len(content)

        blocks.append(_format_source_block(index, chunk, content))

    return "\n\n".join(blocks)


def _format_source_block(index: int, chunk: RetrievedChunk, content: str) -> str:
    metadata = chunk.metadata
    logical_unit_title = _metadata_value(metadata, "logical_unit_title")
    logical_unit_type = _metadata_value(metadata, "logical_unit_type")
    doc_type = chunk.doc_type or _metadata_value(metadata, "doc_type") or "-"

    lines = [
        f"[{index}]",
        f"Название: {chunk.title}",
        f"Источник: {chunk.source}",
        f"Раздел: {chunk.section or '-'}",
        f"Страница: {_format_page(chunk.page)}",
        f"Тип документа: {doc_type}",
        f"Смысловой блок: {logical_unit_title or '-'}",
        f"Тип смыслового блока: {logical_unit_type or '-'}",
    ]

    if _uses_excerpt(chunk):
        lines.extend(
            [
                "Формат контекста: фрагмент из длинного источника.",
                "Релевантный фрагмент:",
                content,
            ]
        )
    else:
        lines.extend(
            [
                "Формат контекста: полный текст короткого chunk.",
                "Релевантный фрагмент:",
                chunk.matched_excerpt or build_matched_excerpt(
                    chunk.text,
                    chunk.matched_terms or [],
                    max_chars=450,
                ),
                "Полный текст:",
                content,
            ]
        )

    return "\n".join(lines)


def _source_content_for_prompt(chunk: RetrievedChunk) -> str:
    text = " ".join(chunk.text.split())
    excerpt = chunk.matched_excerpt or build_matched_excerpt(
        text,
        chunk.matched_terms or [],
        max_chars=700,
    )

    if _uses_excerpt(chunk):
        return excerpt

    return text


def _uses_excerpt(chunk: RetrievedChunk) -> bool:
    return len(chunk.text) > LONG_CHUNK_THRESHOLD and bool(
        chunk.matched_excerpt or chunk.matched_terms
    )


def _metadata_value(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    text = str(value).strip()
    return text or None


def _format_page(page: int | None) -> str:
    if page is None:
        return "-"

    return str(page)
