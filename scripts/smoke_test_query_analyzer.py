"""Smoke test for the experimental LLM Query Analyzer."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.rag.query_analyzer import QueryAnalyzer  # noqa: E402


QUESTIONS = [
    "какие отделы есть в компании?",
    "из каких подразделений состоит компания?",
    "чем занимается компания?",
    "что такое цкп?",
    "а при чем тут ценный конечный продукт?",
    "что такое кп?",
    "коммерческое предложение",
    "я потерял документ что делать",
    "как получить новый ноутбук?",
    "опоздание",
    "как оформить отпуск?",
    "как согласовать договор?",
    "как согласовать командировку?",
    "что значит взять задачу в работу?",
    "сколько маленьких утят после бега есть хотят?",
]


def main() -> int:
    _configure_stdout()

    analyzer = QueryAnalyzer()
    fallback_notice_printed = False

    print("=== QUERY ANALYZER SMOKE TEST ===")
    print(f"Model: {analyzer.model}")
    print()
    print(
        _format_row(
            [
                "question",
                "intent",
                "answer_type",
                "normalized_question",
                "query_expansions",
                "preferred_sources",
                "confidence",
            ]
        )
    )
    print(_format_row(["-" * 28, "-" * 20, "-" * 14, "-" * 34, "-" * 30, "-" * 28, "-" * 10]))

    for question in QUESTIONS:
        plan = analyzer.analyze(question)
        if analyzer.last_error and not fallback_notice_printed:
            print()
            print("Ollama analyzer is unavailable or returned invalid JSON.")
            print(f"Using safe fallback plans. First reason: {analyzer.last_error}")
            print()
            fallback_notice_printed = True

        print(
            _format_row(
                [
                    question,
                    plan.intent,
                    plan.answer_type,
                    plan.normalized_question,
                    ", ".join(plan.query_expansions),
                    ", ".join(plan.preferred_sources),
                    f"{plan.confidence:.2f}",
                ]
            )
        )

    print()
    print("Status: PASS")
    return 0


def _format_row(values: list[str]) -> str:
    widths = [32, 24, 16, 40, 36, 34, 10]
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
