"""Quality smoke test for semantic FTS retrieval over the active memory DB."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import TypedDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.rag.retriever import (  # noqa: E402
    RetrievedChunk,
    SemanticRetriever,
    format_retrieval_result,
)


DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"


class RetrievalCase(TypedDict, total=False):
    question: str
    expected_title_contains: list[str]
    expected_section_contains: list[str]
    expected_top_n: int
    known_weak: bool


CASES: list[RetrievalCase] = [
    {
        "question": "Что такое ЦКП компании Serviceline?",
        "expected_title_contains": ["ИП-0003 ЦКП SERVICELINE"],
        "expected_top_n": 3,
    },
    {
        "question": "Что означает профессиональный подбор в ЦКП?",
        "expected_title_contains": ["ИП-0003 ЦКП SERVICELINE"],
        "expected_section_contains": ["Профессиональный подбор"],
        "expected_top_n": 3,
    },
    {
        "question": "Из чего состоит ЗРС?",
        "expected_title_contains": ["ИП-0004 Структура ЗРС"],
        "expected_section_contains": ["Что такое ЗРС"],
        "expected_top_n": 3,
    },
    {
        "question": "Что такое ситуация, данные и решение в ЗРС?",
        "expected_title_contains": ["ИП-0004 Структура ЗРС"],
        "expected_section_contains": ["Что такое ЗРС"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие обязанности у подчинённого при составлении ЗРС?",
        "expected_title_contains": ["ИП-0004 Структура ЗРС"],
        "expected_section_contains": ["Обязанности подчинённого"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие правила есть для распоряжений?",
        "expected_title_contains": ["ИП-0005 Распоряжения"],
        "expected_top_n": 3,
    },
    {
        "question": "Как оформить распоряжение?",
        "expected_title_contains": ["ИП-0005 Распоряжения"],
        "expected_top_n": 3,
    },
    {
        "question": "Что делать сотруднику при начале работы в новой должности?",
        "expected_title_contains": ["Инструкция - Как начать работу в новой должности"],
        "expected_top_n": 3,
    },
    {
        "question": "Как согласовать договор в документообороте?",
        "expected_title_contains": [
            "Инструкция Согласования договоров",
            "Как в ДО  завести договор",
        ],
        "expected_top_n": 3,
    },
    {
        "question": "Как завести нового контрагента в 1С?",
        "expected_title_contains": ["Инструкция как завести нового контрагента"],
        "expected_top_n": 3,
    },
    {
        "question": "Как направить задачу подчиненному через 1С Документооборот?",
        "expected_title_contains": ["Инструкция Направление задач подчиненным"],
        "expected_top_n": 3,
    },
    {
        "question": "Как согласовать командировку?",
        "expected_title_contains": ["Инструкция Согласования командировки"],
        "expected_top_n": 3,
    },
    {
        "question": "Что такое письменная коммуникация?",
        "expected_title_contains": ["Регламент по письменной коммуникации"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие подразделения есть в коммерческом отделении?",
        "expected_title_contains": ["2026-03-03_Оргсхема", "Оргсхема"],
        "expected_section_contains": ["Коммерческое отделение"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие отделения есть в компании?",
        "expected_title_contains": ["2026-03-03_Оргсхема", "Оргсхема"],
        "expected_section_contains": ["Обзор оргсхемы компании"],
        "expected_top_n": 3,
    },
    {
        "question": "Что относится к коммерческому направлению?",
        "expected_title_contains": ["2026-03-03_Оргсхема", "Оргсхема"],
        "expected_section_contains": ["Коммерческое отделение"],
        "expected_top_n": 3,
    },
    {
        "question": "Какой ЦКП у коммерческого направления?",
        "expected_title_contains": ["2026-03-03_Оргсхема", "Оргсхема"],
        "expected_section_contains": ["Коммерческое отделение"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие отделы есть в отделении закупки и отделении логистики?",
        "expected_title_contains": ["2026-03-03_Оргсхема", "Оргсхема"],
        "expected_section_contains": ["Отделение закупки", "Отделение логистики"],
        "expected_top_n": 3,
    },
    {
        "question": "Что говорится про планирование на неделю?",
        "expected_title_contains": ["Регламент по планированию на неделю"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие обязанности у сотрудника при планировании на неделю?",
        "expected_title_contains": ["Регламент по планированию на неделю"],
        "expected_section_contains": ["Обязанности сотрудника при недельном планировании"],
        "expected_top_n": 3,
    },
    {
        "question": "Зачем нужны планы на неделю?",
        "expected_title_contains": ["Регламент по планированию на неделю"],
        "expected_section_contains": ["Зачем нужны планы на неделю"],
        "expected_top_n": 3,
    },
    {
        "question": "Как составлять план на неделю?",
        "expected_title_contains": ["Регламент по планированию на неделю"],
        "expected_section_contains": ["Форма плана на неделю"],
        "expected_top_n": 3,
    },
    {
        "question": "Какие ошибки бывают при планировании на неделю?",
        "expected_title_contains": ["Регламент по планированию на неделю"],
        "expected_section_contains": ["Типовые ошибки сотрудника при планировании"],
        "expected_top_n": 3,
    },
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

    print("=== SEMANTIC RETRIEVAL QUALITY SMOKE TEST ===")
    print(f"DB: {DB_PATH}")
    print(f"Semantic chunks: {semantic_count}")
    print()

    retriever = SemanticRetriever(DB_PATH)
    summary = {
        "with_results": 0,
        "empty": 0,
        "top1": 0,
        "top3": 0,
        "top5": 0,
        "weak": 0,
        "known_weak": 0,
    }

    for index, case in enumerate(CASES, start=1):
        question = case["question"]
        expected_top_n = case.get("expected_top_n", 3)
        limit = max(5, expected_top_n)
        chunks = retriever.retrieve(question, limit=limit, candidate_limit=30)
        case_status, matched_rank = _case_status(case, chunks)

        _update_summary(summary, case_status, has_results=bool(chunks))
        print(f"=== QUERY {index}/{len(CASES)} ===")
        print(f"Question: {question}")
        print(f"Expected: {_format_expectation(case)}")
        print()

        if chunks:
            for rank, chunk in enumerate(chunks[:limit], start=1):
                relevance_status = (
                    case_status
                    if matched_rank == rank
                    else ("CANDIDATE" if rank <= expected_top_n else "EXTRA")
                )
                print(
                    format_retrieval_result(
                        chunk,
                        max_chars=450,
                        rank=rank,
                        relevance_status=relevance_status,
                    )
                )
                print()
        else:
            print("No chunks found.")
            print()

        print(f"Case status: {case_status}")
        print()

    if summary["empty"] > 0:
        final_status = "FAIL"
        exit_code = 1
    elif summary["weak"] > 0:
        final_status = "REVIEW"
        exit_code = 0
    else:
        final_status = "PASS"
        exit_code = 0

    print("=== SUMMARY ===")
    print(f"Queries total: {len(CASES)}")
    print(f"With results: {summary['with_results']}")
    print(f"Empty: {summary['empty']}")
    print(f"Relevant TOP1: {summary['top1']}")
    print(f"Relevant TOP3: {summary['top3']}")
    print(f"Relevant TOP5: {summary['top5']}")
    print(f"Weak: {summary['weak']}")
    print(f"Known weak: {summary['known_weak']}")
    print(f"Final status: {final_status}")

    return exit_code


def _case_status(
    case: RetrievalCase,
    chunks: list[RetrievedChunk],
) -> tuple[str, int | None]:
    if not chunks:
        return "EMPTY", None

    expected_top_n = case.get("expected_top_n", 3)
    for rank, chunk in enumerate(chunks, start=1):
        if _matches_case(case, chunk):
            if rank == 1:
                return "RELEVANT_TOP1", rank
            if rank <= 3:
                return "RELEVANT_TOP3", rank
            if rank <= 5:
                return "RELEVANT_TOP5", rank
            if rank <= expected_top_n:
                return f"RELEVANT_TOP{expected_top_n}", rank
            return ("KNOWN_WEAK" if case.get("known_weak") else "WEAK"), rank

    return ("KNOWN_WEAK" if case.get("known_weak") else "WEAK"), None


def _matches_case(case: RetrievalCase, chunk: RetrievedChunk) -> bool:
    title_needles = case.get("expected_title_contains", [])
    section_needles = case.get("expected_section_contains", [])

    title_ok = not title_needles or _contains_any(chunk.title, title_needles)
    section_text = chunk.section or ""
    section_ok = not section_needles or _contains_any(section_text, section_needles)

    return title_ok and section_ok


def _contains_any(value: str, needles: list[str]) -> bool:
    value_folded = value.casefold()
    return any(needle.casefold() in value_folded for needle in needles)


def _format_expectation(case: RetrievalCase) -> str:
    parts = []
    if case.get("expected_title_contains"):
        parts.append(
            "title contains one of "
            + ", ".join(repr(item) for item in case["expected_title_contains"])
        )
    if case.get("expected_section_contains"):
        parts.append(
            "section contains one of "
            + ", ".join(repr(item) for item in case["expected_section_contains"])
        )
    parts.append(f"in TOP {case.get('expected_top_n', 3)}")
    if case.get("known_weak"):
        parts.append("known weak case")
    return "; ".join(parts)


def _update_summary(summary: dict[str, int], case_status: str, *, has_results: bool) -> None:
    if has_results:
        summary["with_results"] += 1
    if case_status == "EMPTY":
        summary["empty"] += 1
    elif case_status == "RELEVANT_TOP1":
        summary["top1"] += 1
    elif case_status == "RELEVANT_TOP3":
        summary["top3"] += 1
    elif case_status == "RELEVANT_TOP5":
        summary["top5"] += 1
    elif case_status == "KNOWN_WEAK":
        summary["known_weak"] += 1
    elif case_status == "WEAK":
        summary["weak"] += 1


def _count_semantic_chunks(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_chunks WHERE namespace = ?",
                ("semantic",),
            ).fetchone()[0]
        )


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
