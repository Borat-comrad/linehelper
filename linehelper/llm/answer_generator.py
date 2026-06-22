"""Read-only RAG answer generation over semantic memory and local Ollama."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from linehelper.llm.ollama_client import OllamaClient, OllamaError
from linehelper.rag.prompt_builder import build_rag_prompt
from linehelper.rag.retriever import RetrievedChunk, SemanticRetriever


DEFAULT_RETRIEVAL_LIMIT = 5
DEFAULT_CANDIDATE_LIMIT = 30
DEFAULT_CONTEXT_LIMIT = 3
DEFAULT_CONTEXT_SCORE_RATIO = 0.65

ANCHOR_TERMS: dict[str, tuple[str, ...]] = {
    "отпуск": ("отпуск", "отпуска", "отпуске", "отпусков", "отпускной"),
    "зрс": ("зрс", "завершенная работа сотрудника"),
    "цкп": ("цкп", "ценный конечный продукт"),
    "командировка": ("командировка", "командировки", "командировку"),
    "договор": ("договор", "договора", "договоров", "договоре"),
    "распоряжение": (
        "распоряжение",
        "распоряжения",
        "распоряжений",
        "распоряжением",
    ),
}

SYSTEM_MESSAGE = (
    "Ты корпоративный помощник Serviceline. Отвечай только на русском языке. "
    "Отвечай только на основе предоставленных источников. Если в источниках "
    "недостаточно данных, честно скажи, что данных недостаточно. Не выдумывай "
    "факты. Не используй английский, китайский или корейский язык, если "
    "пользователь явно не просит перевод. Отвечай именно на вопрос пользователя, "
    "а не на похожую общую тему. Если пользователь спрашивает, что делать, "
    "давай практические шаги только из источников. Игнорируй источники, которые "
    "не относятся к предмету вопроса, и не используй случайное совпадение слов "
    "как основание для ответа. Если источники дают только частичный ответ, "
    "прямо скажи, чего в них нет. В конце кратко укажи, на какие источники "
    "опирался."
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
    context_limit: int
    context_score_ratio: float


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
        context_limit: int | None = None,
        context_score_ratio: float | None = None,
    ) -> None:
        self.retriever = retriever or SemanticRetriever(db_path)
        self.llm_client = llm_client or OllamaClient()
        self.context_limit = max(
            1,
            context_limit
            if context_limit is not None
            else _env_int("RAG_CONTEXT_LIMIT", DEFAULT_CONTEXT_LIMIT),
        )
        raw_score_ratio = (
            context_score_ratio
            if context_score_ratio is not None
            else _env_float("RAG_CONTEXT_SCORE_RATIO", DEFAULT_CONTEXT_SCORE_RATIO)
        )
        self.context_score_ratio = max(0.0, min(raw_score_ratio, 1.0))

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
        context_chunks = select_context_chunks(
            clean_question,
            chunks,
            max_chunks=self.context_limit,
            score_ratio=self.context_score_ratio,
        )
        sources = [_source_from_chunk(chunk) for chunk in context_chunks]

        if not context_chunks:
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
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
            )

        prompt = build_rag_prompt(clean_question, context_chunks)
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
            chunks_used=len(context_chunks),
            prompt_length=len(prompt),
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
            context_limit=self.context_limit,
            context_score_ratio=self.context_score_ratio,
        )


def select_context_chunks(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    max_chunks: int = DEFAULT_CONTEXT_LIMIT,
    score_ratio: float = DEFAULT_CONTEXT_SCORE_RATIO,
) -> list[RetrievedChunk]:
    """Select a compact, high-confidence context for the LLM prompt."""
    if not chunks:
        return []

    max_chunks = max(1, max_chunks)
    score_ratio = max(0.0, min(score_ratio, 1.0))
    candidates = list(chunks)

    anchor_terms = _active_anchor_terms(question)
    if anchor_terms:
        anchored = [
            chunk
            for chunk in candidates
            if _chunk_contains_any_term(chunk, anchor_terms)
        ]
        if anchored:
            candidates = anchored

    top_score = max(_chunk_score(chunk) for chunk in candidates)
    if top_score > 0 and score_ratio > 0:
        cutoff = top_score * score_ratio
        candidates = [
            chunk for chunk in candidates if _chunk_score(chunk) >= cutoff
        ]

    return candidates[:max_chunks]


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


def _active_anchor_terms(question: str) -> tuple[str, ...]:
    normalized_question = _normalize_for_match(question)
    terms: list[str] = []

    for variants in ANCHOR_TERMS.values():
        if any(variant in normalized_question for variant in variants):
            terms.extend(variants)

    return tuple(dict.fromkeys(terms))


def _chunk_contains_any_term(chunk: RetrievedChunk, terms: Sequence[str]) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.section,
                metadata.get("logical_unit_title"),
                chunk.text,
            )
        )
    )
    return any(term in haystack for term in terms)


def _chunk_score(chunk: RetrievedChunk) -> float:
    score = chunk.final_score if chunk.final_score is not None else chunk.score
    if score is None:
        return 0.0
    return float(score)


def _normalize_for_match(value: str) -> str:
    return value.lower().replace("ё", "е")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
