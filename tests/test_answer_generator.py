from __future__ import annotations

import pytest

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator
from linehelper.llm.ollama_client import OllamaEmptyResponseError
from linehelper.rag.retriever import RetrievedChunk


def test_empty_question_is_rejected() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([]),
        llm_client=FakeClient("ok"),
    )

    with pytest.raises(ValueError):
        generator.answer("   ")


def test_answer_generator_uses_retriever_prompt_and_sources() -> None:
    chunk = _chunk()
    retriever = FakeRetriever([chunk])
    client = FakeClient("Ответ на основе источников.")
    generator = RagAnswerGenerator(retriever=retriever, llm_client=client)

    result = generator.answer("Что такое тест?", retrieval_limit=1, candidate_limit=7)

    assert retriever.calls == [("Что такое тест?", 1, 7)]
    assert client.messages
    assert "Что такое тест?" in client.messages[-1]["content"]
    assert "Тестовый раздел" in client.messages[-1]["content"]
    assert result.answer == "Ответ на основе источников."
    assert result.chunks_used == 1
    assert result.prompt_length > 0
    assert result.sources[0].title == "Тестовый документ"
    assert result.sources[0].logical_unit_title == "Тестовый смысловой блок"
    assert result.sources[0].matched_excerpt == "релевантный фрагмент"


def test_empty_retrieval_does_not_call_llm() -> None:
    client = FakeClient("unused")
    generator = RagAnswerGenerator(retriever=FakeRetriever([]), llm_client=client)

    result = generator.answer("Нет ли ответа?")

    assert result.answer == "В базе знаний не найдено релевантных источников."
    assert result.sources == []
    assert result.chunks_used == 0
    assert result.prompt_length == 0
    assert client.messages == []


def test_llm_error_is_wrapped() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([_chunk()]),
        llm_client=ErrorClient(),
    )

    with pytest.raises(RagAnswerError, match="empty"):
        generator.answer("Что случилось?")


def test_empty_llm_answer_is_error() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([_chunk()]),
        llm_client=FakeClient("   "),
    )

    with pytest.raises(RagAnswerError, match="empty answer"):
        generator.answer("Что случилось?")


class FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, int, int]] = []

    def retrieve(self, question: str, *, limit: int, candidate_limit: int):
        self.calls.append((question, limit, candidate_limit))
        return self.chunks


class FakeClient:
    model = "fake-model"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.messages: list[dict[str, str]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return self.answer


class ErrorClient:
    model = "fake-model"

    def chat(self, messages: list[dict[str, str]]) -> str:
        raise OllamaEmptyResponseError("empty")


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1,
        title="Тестовый документ",
        source="data/raw_docs/test.pdf",
        section="Тестовый раздел",
        page=3,
        text="Тестовый текст для prompt builder.",
        score=1.0,
        metadata={
            "logical_unit_title": "Тестовый смысловой блок",
            "logical_unit_type": "policy_rule",
            "doc_type": "test_doc",
        },
        doc_type="test_doc",
        base_score=1.0,
        rerank_score=2.0,
        final_score=3.0,
        matched_terms=["тест"],
        matched_excerpt="релевантный фрагмент",
        selection_reasons=["test"],
    )
