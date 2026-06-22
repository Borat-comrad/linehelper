"""Read-only RAG answer generation over semantic memory and local Ollama."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from linehelper.llm.ollama_client import OllamaClient, OllamaError
from linehelper.rag.prompt_builder import build_rag_prompt
from linehelper.rag.retriever import RetrievedChunk, SemanticRetriever


DEFAULT_RETRIEVAL_LIMIT = 5
DEFAULT_CANDIDATE_LIMIT = 30

SYSTEM_MESSAGE = (
    "Ты корпоративный помощник Serviceline. Отвечай только на русском языке. "
    "Отвечай только на основе предоставленных источников. Если в источниках "
    "недостаточно данных, честно скажи, что данных недостаточно. Не выдумывай "
    "факты. Не используй английский, китайский или корейский язык, если "
    "пользователь явно не просит перевод. В конце кратко укажи, на какие "
    "источники опирался."
)


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Return assistant answer for chat messages."""


@dataclass(frozen=True)
class RagSource:
    title: str
    source: str
    section: str | None
    page: int | None
    logical_unit_title: str | None
    score: float | None
    matched_excerpt: str


@dataclass(frozen=True)
class RagAnswer:
    question: str
    answer: str
    model: str
    sources: list[RagSource]
    chunks_used: int
    prompt_length: int
    elapsed_seconds: float
    retrieval_limit: int
    candidate_limit: int


class RagAnswerError(RuntimeError):
    """Raised when the read-only RAG answer flow cannot complete."""


class RagAnswerGenerator:
    """Connect semantic retrieval, prompt building and local Ollama chat."""

    def __init__(
        self,
        *,
        retriever: SemanticRetriever | None = None,
        llm_client: ChatClient | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.retriever = retriever or SemanticRetriever(db_path)
        self.llm_client = llm_client or OllamaClient()

    def answer(
        self,
        question: str,
        *,
        retrieval_limit: int = DEFAULT_RETRIEVAL_LIMIT,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> RagAnswer:
        """Build an answer using semantic memory as read-only context."""
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("question must not be empty")

        started_at = time.monotonic()
        chunks = self.retriever.retrieve(
            clean_question,
            limit=retrieval_limit,
            candidate_limit=candidate_limit,
        )
        sources = [_source_from_chunk(chunk) for chunk in chunks]

        if not chunks:
            return RagAnswer(
                question=clean_question,
                answer="В базе знаний не найдено релевантных источников.",
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
            )

        prompt = build_rag_prompt(clean_question, chunks)
        messages = [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]

        try:
            answer_text = self.llm_client.chat(messages)
        except OllamaError as exc:
            raise RagAnswerError(str(exc)) from exc

        if not answer_text.strip():
            raise RagAnswerError("LLM returned an empty answer.")

        return RagAnswer(
            question=clean_question,
            answer=answer_text.strip(),
            model=self.llm_client.model,
            sources=sources,
            chunks_used=len(chunks),
            prompt_length=len(prompt),
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
        )


def _source_from_chunk(chunk: RetrievedChunk) -> RagSource:
    metadata = chunk.metadata or {}
    logical_unit_title = metadata.get("logical_unit_title")

    return RagSource(
        title=chunk.title,
        source=chunk.source,
        section=chunk.section,
        page=chunk.page,
        logical_unit_title=str(logical_unit_title) if logical_unit_title else None,
        score=chunk.final_score if chunk.final_score is not None else chunk.score,
        matched_excerpt=chunk.matched_excerpt,
    )
