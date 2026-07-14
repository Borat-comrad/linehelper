"""Smoke test for the runtime RAG path with Query Analyzer."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator  # noqa: E402


QUESTIONS = [
    "какие отделы есть в компании?",
    "из каких подразделений состоит компания?",
    "чем занимается компания?",
    "что такое цкп?",
    "что такое кп?",
    "коммерческое предложение",
    "я потерял документ что делать",
    "как получить новый ноутбук?",
    "я опоздал на работу",
    "какой статус заказа",
    "найди цену детали",
    "сколько маленьких утят после бега есть хотят?",
]


def main() -> int:
    _configure_stdout()

    db_path = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
    if not db_path.exists():
        print("Status: FAIL")
        print(f"Reason: active memory DB not found: {db_path}")
        return 1

    generator = RagAnswerGenerator(db_path=db_path)
    errors: list[str] = []

    print("=== LOCAL RAG QUERY ANALYZER RUNTIME SMOKE ===")
    print(f"DB: {db_path}")
    print(f"Answer model: {generator.llm_client.model}")
    print(f"Analyzer model: {os.getenv('OLLAMA_ANALYZER_MODEL') or os.getenv('OLLAMA_MODEL') or '-'}")
    print()
    print(
        _format_row(
            [
                "question",
                "response_kind",
                "query_plan.intent",
                "query_plan.answer_type",
                "sources_count",
                "top_sources",
                "fallback_used",
            ]
        )
    )
    print(_format_row(["-" * 30, "-" * 16, "-" * 24, "-" * 18, "-" * 13, "-" * 34, "-" * 13]))

    for question in QUESTIONS:
        try:
            result = generator.answer(question)
        except RagAnswerError as exc:
            errors.append(f"{question!r}: RAG answer error: {exc}")
            print(_format_error_row(question, f"RagAnswerError: {exc}"))
            continue
        except Exception as exc:
            errors.append(f"{question!r}: runtime error: {type(exc).__name__}: {exc}")
            print(_format_error_row(question, f"{type(exc).__name__}: {exc}"))
            continue

        query_plan = result.query_plan if isinstance(result.query_plan, dict) else {}
        top_sources = [source.title for source in result.sources[:3]]
        print(
            _format_row(
                [
                    question,
                    result.response_kind,
                    str(query_plan.get("intent", "-")),
                    str(query_plan.get("answer_type", "-")),
                    str(len(result.sources)),
                    ", ".join(top_sources) or "-",
                    str(bool(query_plan.get("fallback_used", False))),
                ]
            )
        )
        errors.extend(_validate_result(question, result, query_plan))

    print()
    if errors:
        print("Expectation errors:")
        for error in errors:
            print(f"- {error}")
        print("Status: FAIL")
        return 1

    print("Status: PASS")
    return 0


def _validate_result(question: str, result: Any, query_plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not query_plan or query_plan.get("enabled") is not True:
        errors.append(f"{question!r}: Query Analyzer query_plan is absent")
        return errors

    if question == "какие отделы есть в компании?" and query_plan.get("intent") != "org_structure":
        errors.append(
            f"{question!r}: intent {query_plan.get('intent')!r}, expected 'org_structure'"
        )

    if question == "что такое кп?" and result.response_kind != "clarification":
        errors.append(
            f"{question!r}: response_kind {result.response_kind!r}, expected 'clarification'"
        )

    if question == "сколько маленьких утят после бега есть хотят?" and result.sources:
        errors.append(f"{question!r}: off-topic question returned answer sources")

    if query_plan.get("intent") == "one_c_operational_lookup" and result.sources:
        errors.append(
            f"{question!r}: one_c_operational_lookup returned semantic answer sources"
        )

    return errors


def _format_error_row(question: str, error: str) -> str:
    return _format_row([question, "ERROR", "-", "-", "-", error, "-"])


def _format_row(values: list[str]) -> str:
    widths = [42, 18, 28, 22, 13, 42, 13]
    return " | ".join(
        _clip(value, width).ljust(width)
        for value, width in zip(values, widths, strict=True)
    )


def _clip(value: str, width: int) -> str:
    clean_value = value.replace("\n", " ").strip()
    if len(clean_value) <= width:
        return clean_value
    return clean_value[: max(0, width - 3)] + "..."


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
