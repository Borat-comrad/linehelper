from __future__ import annotations

from linehelper.rag.prompt_builder import build_rag_prompt
from linehelper.rag.retriever import (
    RetrievedChunk,
    build_fts_queries,
    build_matched_excerpt,
    expand_query_terms,
    normalize_question,
)


def test_build_matched_excerpt_returns_context_around_term() -> None:
    text = (
        "Начальный шум про бухгалтерию и отделения. " * 20
        + "Планирование рабочих задач на неделю является обязанностью сотрудника. "
        + "Финальный текст."
    )

    excerpt = build_matched_excerpt(
        text,
        ["планирование на неделю", "планирование"],
        max_chars=180,
    )

    assert "Планирование рабочих задач" in excerpt
    assert not excerpt.startswith("Начальный шум")


def test_query_normalization_handles_russian_punctuation() -> None:
    question = 'Что такое "ЗРС": ситуация, данные и решение?'

    normalized = normalize_question(question)
    queries = build_fts_queries(question)

    assert normalized == "Что такое ЗРС ситуация данные и решение"
    assert queries
    assert all("?" not in query and ":" not in query for query in queries)


def test_domain_expansions_for_zrs_ckp_and_planning() -> None:
    zrs = expand_query_terms("Из чего состоит ЗРС?")
    ckp = expand_query_terms("Что такое ЦКП?")
    planning = expand_query_terms("Что говорится про планирование на неделю?")

    assert "завершенная работа сотрудника" in zrs
    assert "ценный конечный продукт" in ckp
    assert "планирование на неделю" in planning


def test_prompt_builder_uses_excerpt_for_long_chunk() -> None:
    chunk = RetrievedChunk(
        chunk_id=1,
        title="Регламент по планированию на неделю",
        source="data/raw_docs/Регламент по планированию на неделю.pdf",
        section="Планирование рабочих задач",
        page=1,
        text=(
            "Шумный длинный фрагмент. " * 200
            + "Планирование рабочих задач на неделю является обязанностью."
        ),
        score=-1.0,
        metadata={
            "doc_type": "planning_policy",
            "logical_unit_title": "Правила планирования на неделю",
            "logical_unit_type": "policy_rule",
        },
        doc_type="planning_policy",
        matched_terms=["планирование на неделю"],
        matched_excerpt=(
            "...Планирование рабочих задач на неделю является обязанностью."
        ),
    )

    prompt = build_rag_prompt("Что говорится про планирование на неделю?", [chunk])

    assert "Релевантный фрагмент:" in prompt
    assert "Формат контекста: фрагмент из длинного источника." in prompt
    assert "Правила планирования на неделю" in prompt
    assert chunk.text not in prompt
