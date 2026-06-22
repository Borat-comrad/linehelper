"""Smoke test for building RAG prompts from reranked semantic retrieval."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.rag.prompt_builder import build_rag_prompt  # noqa: E402
from linehelper.rag.retriever import RetrievedChunk, SemanticRetriever  # noqa: E402


DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
MAX_PROMPT_CHARS = 12_000
QUESTIONS = [
    "Что такое ЦКП компании Serviceline?",
    "Из чего состоит ЗРС?",
    "Как согласовать договор в документообороте?",
    "Как завести нового контрагента в 1С?",
    "Какие отделения есть в компании?",
    "Какие подразделения есть в коммерческом отделении?",
    "Что говорится про планирование на неделю?",
    "Как согласовать командировку?",
]


def main() -> int:
    _configure_stdout()

    if not DB_PATH.exists():
        print("Status: FAIL")
        print(f"Reason: active memory DB not found: {DB_PATH}")
        return 1

    semantic_count = _count_semantic_chunks(DB_PATH)
    if semantic_count <= 0:
        print("Status: FAIL")
        print("Reason: semantic chunks not found in active memory DB.")
        return 1

    print("=== RAG PROMPT SMOKE TEST ===")
    print(f"DB: {DB_PATH}")
    print(f"Semantic chunks: {semantic_count}")
    print()

    retriever = SemanticRetriever(DB_PATH)
    built = 0
    prompts_with_excerpts = 0
    oversized_prompts = 0
    empty_retrieval = 0
    errors = 0

    for index, question in enumerate(QUESTIONS, start=1):
        print(f"=== PROMPT {index}/{len(QUESTIONS)} ===")
        print(f"Question: {question}")

        try:
            chunks = retriever.retrieve(question, limit=3, candidate_limit=30)
            prompt = build_rag_prompt(question, chunks)
            _validate_prompt(prompt, question, chunks)
        except Exception as exc:
            errors += 1
            print(f"Status: ERROR ({type(exc).__name__}: {exc})")
            print()
            continue

        built += 1
        if not chunks:
            empty_retrieval += 1
        if "Релевантный фрагмент:" in prompt:
            prompts_with_excerpts += 1
        if len(prompt) > MAX_PROMPT_CHARS:
            oversized_prompts += 1

        print(f"Chunks found: {len(chunks)}")
        print(f"Prompt length: {len(prompt)}")
        print("Preview:")
        print(prompt[:1200])
        print()
        print("Sources:")
        for source_index, chunk in enumerate(chunks, start=1):
            logical_unit_title = chunk.metadata.get("logical_unit_title") or "-"
            print(
                f"[{source_index}] {chunk.title} | "
                f"{chunk.section or '-'} | "
                f"{logical_unit_title} | "
                f"page {_format_page(chunk.page)} | "
                f"{chunk.source}"
            )
        if not chunks:
            print("-")

        print(f"Status: {'REVIEW' if not chunks else 'OK'}")
        print()

    if errors or oversized_prompts:
        status = "FAIL"
        exit_code = 1
    elif empty_retrieval:
        status = "REVIEW"
        exit_code = 0
    else:
        status = "PASS"
        exit_code = 0

    print("=== RAG PROMPT SUMMARY ===")
    print(f"Prompts built: {built}")
    print(f"Prompts with excerpts: {prompts_with_excerpts}")
    print(f"Oversized prompts: {oversized_prompts}")
    print(f"Errors: {errors}")
    print(f"Status: {status}")

    return exit_code


def _validate_prompt(
    prompt: str,
    question: str,
    chunks: list[RetrievedChunk],
) -> None:
    if not prompt.strip():
        raise ValueError("prompt is empty")
    if question not in prompt:
        raise ValueError("prompt does not contain the question")
    if "Источники:" not in prompt:
        raise ValueError("prompt does not contain the sources block")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt is too long: {len(prompt)} chars")

    if chunks:
        for label in (
            "Название:",
            "Источник:",
            "Раздел:",
            "Страница:",
            "Тип документа:",
            "Смысловой блок:",
            "Релевантный фрагмент:",
        ):
            if label not in prompt:
                raise ValueError(f"prompt does not contain source label: {label}")

        for chunk in chunks:
            if chunk.title not in prompt:
                raise ValueError(f"prompt lost source title: {chunk.title}")
            if chunk.source not in prompt:
                raise ValueError(f"prompt lost source path: {chunk.source}")
            logical_unit_title = chunk.metadata.get("logical_unit_title")
            if logical_unit_title and str(logical_unit_title) not in prompt:
                raise ValueError(
                    f"prompt lost logical unit title: {logical_unit_title}"
                )
            if len(chunk.text) > 2800 and chunk.text in prompt:
                raise ValueError("prompt includes a full long noisy chunk")
    elif "Источники не найдены." not in prompt:
        raise ValueError("prompt does not explain that sources are missing")


def _count_semantic_chunks(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_chunks WHERE namespace = ?",
                ("semantic",),
            ).fetchone()[0]
        )


def _format_page(page: int | None) -> str:
    if page is None:
        return "-"

    return str(page)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
