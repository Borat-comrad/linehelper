"""Smoke test for the experimental LLM Query Analyzer."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.rag.query_analyzer import KNOWN_SOURCE_TITLES, QueryAnalyzer  # noqa: E402


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

EXPECTED_PLANS = {
    "чем занимается компания?": {
        "intent": "company_identity",
    },
    "что такое кп?": {
        "intent": "ambiguous_abbreviation",
        "answer_type": "clarification",
    },
    "я потерял документ что делать": {
        "intent": "document_loss",
        "forbidden_answer_types": {"procedure"},
        "preferred_sources": [],
    },
    "как получить новый ноутбук?": {
        "intent": "equipment_it_request",
        "forbidden_answer_types": {"procedure"},
        "preferred_sources": [],
    },
}


def main() -> int:
    _configure_stdout()

    analyzer = QueryAnalyzer()
    fallback_notice_printed = False
    errors: list[str] = []

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
        errors.extend(_validate_expectation(question, plan))

    print()
    if errors:
        print("Expectation errors:")
        for error in errors:
            print(f"- {error}")
        print("Status: FAIL")
        return 1

    print("Status: PASS")
    return 0


def _validate_expectation(question: str, plan) -> list[str]:
    expected = EXPECTED_PLANS.get(question)
    if expected is None:
        return []

    errors = []
    unknown_sources = [
        source for source in plan.preferred_sources if source not in KNOWN_SOURCE_TITLES
    ]
    if unknown_sources:
        errors.append(f"{question!r}: unknown preferred_sources {unknown_sources!r}")
    if expected.get("intent") and plan.intent != expected["intent"]:
        errors.append(
            f"{question!r}: intent {plan.intent!r}, expected {expected['intent']!r}"
        )
    if expected.get("answer_type") and plan.answer_type != expected["answer_type"]:
        errors.append(
            f"{question!r}: answer_type {plan.answer_type!r}, "
            f"expected {expected['answer_type']!r}"
        )
    forbidden_answer_types = expected.get("forbidden_answer_types", set())
    if plan.answer_type in forbidden_answer_types:
        errors.append(f"{question!r}: forbidden answer_type {plan.answer_type!r}")
    if "preferred_sources" in expected and plan.preferred_sources != expected["preferred_sources"]:
        errors.append(
            f"{question!r}: preferred_sources {plan.preferred_sources!r}, "
            f"expected {expected['preferred_sources']!r}"
        )
    return errors


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
