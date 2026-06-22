"""Read-only RAG answer generation over semantic memory and local Ollama."""

from __future__ import annotations

import os
import re
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
MIN_GENERIC_CONTEXT_SCORE = 35.0
NO_ANSWER_MESSAGE = (
    "В базе знаний Serviceline нет ответа на этот вопрос. "
    "Похоже, вопрос не относится к корпоративным регламентам, инструкциям, "
    "оргструктуре или документообороту."
)
CLARIFY_KP_MESSAGE = (
    "Вы имеете в виду КП как коммерческое предложение или ЦКП как ценный "
    "конечный продукт компании? По КП как коммерческому предложению в semantic "
    "memory может не быть источников. Если вы имеете в виду ЦКП, я буду "
    "использовать ИП-0003 ЦКП SERVICELINE."
)

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

INTENT_ANCHOR_TERMS: dict[str, tuple[str, ...]] = {
    "company_identity": (
        "ип-0002",
        "цели и замыслы",
        "цель компании",
        "основная цель компании",
        "ип-0003",
        "цкп serviceline",
        "ценный конечный продукт",
        "комплексная услуга",
    ),
    "document_flow": (
        "ип-0006",
        "документооборот",
        "1с до",
        "1с документооборот",
        "согласование",
        "согласования",
        "инструкция согласования",
    ),
    "ckp": (
        "ип-0003",
        "цкп serviceline",
        "цкп",
        "ценный конечный продукт",
    ),
}

INTENT_PREFERRED_TERMS: dict[str, tuple[str, ...]] = {
    "company_identity": (
        "ип-0002",
        "цели и замыслы",
        "ип-0003",
        "цкп serviceline",
        "цель компании",
        "основная цель компании",
    ),
    "document_flow": (
        "ип-0006",
        "документооборот",
        "1с до",
        "согласования",
        "инструкция согласования",
    ),
    "ckp": (
        "ип-0003",
        "цкп serviceline",
        "ценный конечный продукт",
    ),
}

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
_KP_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])кп(?![0-9A-Za-zА-Яа-яЁё])",
    re.IGNORECASE,
)

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
class QueryIntent:
    name: str
    anchor_terms: tuple[str, ...] = ()
    preferred_terms: tuple[str, ...] = ()
    require_preferred_context: bool = False
    min_context_score: float = MIN_GENERIC_CONTEXT_SCORE


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
    diagnostic_candidates: list[RagSource]
    response_kind: str = "answer"


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
        clarification = should_ask_clarification(clean_question)
        if clarification is not None:
            return RagAnswer(
                question=clean_question,
                answer=clarification,
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
                diagnostic_candidates=[],
                response_kind="clarification",
            )

        intent = detect_query_intent(clean_question)
        chunks = self.retriever.retrieve(
            clean_question,
            limit=retrieval_limit,
            candidate_limit=candidate_limit,
        )
        context_chunks = select_context_chunks(
            clean_question,
            chunks,
            intent=intent,
            max_chunks=self.context_limit,
            score_ratio=self.context_score_ratio,
        )
        sources = [_source_from_chunk(chunk) for chunk in context_chunks]
        diagnostic_candidates = [
            _source_from_chunk(chunk)
            for chunk in chunks
            if chunk not in context_chunks
        ]

        if not context_chunks:
            return RagAnswer(
                question=clean_question,
                answer=_no_answer_message(intent),
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
                diagnostic_candidates=diagnostic_candidates,
                response_kind="no_answer",
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
            diagnostic_candidates=diagnostic_candidates,
        )


def detect_query_intent(question: str) -> QueryIntent:
    """Detect a simple transparent query intent without external models."""
    normalized = _normalize_for_match(question)

    if _contains_any(
        normalized,
        (
            "чем занимается компания",
            "что делает компания",
            "о компании",
            "цель компании",
            "serviceline",
            "сервислайн",
        ),
    ):
        return _intent("company_identity", require_preferred_context=True)

    if _contains_any(
        normalized,
        (
            "документооборот",
            "документоборот",
            "1с до",
            "1с документооборот",
            "согласование",
            "создать документ",
        ),
    ):
        return _intent("document_flow", require_preferred_context=True)

    tokens = set(_tokens(normalized))
    if "документ" in tokens or "документы" in tokens:
        return _intent("document_flow", require_preferred_context=True)

    if "цкп" in tokens:
        return _intent("ckp", require_preferred_context=True)

    if _contains_any(normalized, ANCHOR_TERMS["отпуск"]):
        return _intent("vacation", anchor_terms=ANCHOR_TERMS["отпуск"])

    if _contains_any(normalized, ANCHOR_TERMS["зрс"]):
        return _intent("zrs", anchor_terms=ANCHOR_TERMS["зрс"])

    return QueryIntent(name="unknown")


def should_ask_clarification(question: str) -> str | None:
    """Return a clarification answer for ambiguous short abbreviations."""
    normalized = _normalize_for_match(question)
    if "цкп" in normalized:
        return None
    if _KP_RE.search(normalized):
        return CLARIFY_KP_MESSAGE
    return None


def select_context_chunks(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    intent: QueryIntent | None = None,
    max_chunks: int = DEFAULT_CONTEXT_LIMIT,
    score_ratio: float = DEFAULT_CONTEXT_SCORE_RATIO,
) -> list[RetrievedChunk]:
    """Select a compact, high-confidence context for the LLM prompt."""
    if not chunks:
        return []

    max_chunks = max(1, max_chunks)
    score_ratio = max(0.0, min(score_ratio, 1.0))
    candidates = list(chunks)
    intent = intent or detect_query_intent(question)

    preferred = _chunks_matching_terms(candidates, intent.preferred_terms)
    if preferred:
        candidates = preferred
    elif intent.require_preferred_context:
        return []

    anchor_terms = _active_anchor_terms(question, intent=intent)
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

    if intent.name == "unknown" and candidates:
        candidates = [
            chunk
            for chunk in candidates
            if _chunk_score(chunk) >= intent.min_context_score
        ]

    return candidates[:max_chunks]


def _intent(
    name: str,
    *,
    anchor_terms: tuple[str, ...] = (),
    require_preferred_context: bool = False,
) -> QueryIntent:
    return QueryIntent(
        name=name,
        anchor_terms=anchor_terms or INTENT_ANCHOR_TERMS.get(name, ()),
        preferred_terms=INTENT_PREFERRED_TERMS.get(name, ()),
        require_preferred_context=require_preferred_context,
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


def _active_anchor_terms(question: str, *, intent: QueryIntent | None = None) -> tuple[str, ...]:
    normalized_question = _normalize_for_match(question)
    terms: list[str] = []

    for variants in ANCHOR_TERMS.values():
        if any(variant in normalized_question for variant in variants):
            terms.extend(variants)

    if intent is not None:
        terms.extend(intent.anchor_terms)

    return tuple(dict.fromkeys(terms))


def _chunk_contains_any_term(chunk: RetrievedChunk, terms: Sequence[str]) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                chunk.section,
                metadata.get("source_file"),
                metadata.get("logical_unit_title"),
                metadata.get("doc_type"),
                " ".join(str(tag) for tag in metadata.get("tags", [])),
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


def _chunks_matching_terms(
    chunks: Sequence[RetrievedChunk],
    terms: Sequence[str],
) -> list[RetrievedChunk]:
    if not terms:
        return []
    return [
        chunk
        for chunk in chunks
        if _chunk_contains_any_term(chunk, terms)
    ]


def _no_answer_message(intent: QueryIntent) -> str:
    if intent.name == "unknown":
        return NO_ANSWER_MESSAGE
    return "В базе знаний Serviceline не найдено достаточно релевантных источников для ответа на этот вопрос."


def _contains_any(value: str, needles: Sequence[str]) -> bool:
    return any(_normalize_for_match(needle) in value for needle in needles)


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_RE.findall(value)]


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
