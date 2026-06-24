"""Exploratory smoke/probe suite for the experimental Query Analyzer."""

from __future__ import annotations

from collections import Counter
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.rag.query_analyzer import KNOWN_SOURCE_TITLES, QueryAnalyzer  # noqa: E402


QUESTIONS = [
    # Short vague questions
    "договор",
    "отпуск",
    "задача",
    "командировка",
    "документ",
    "кп",
    "цкп",
    "зрс",
    "статистика",
    "оргсхема",
    # Natural messy user questions
    "я вообще не понимаю какой документ мне создавать",
    "мне сказали оформить это через документооборот что делать",
    "куда мне обратиться по договору",
    "мне надо что-то подписать в 1С ДО",
    "я не понял кто отвечает за закупки",
    "кто занимается логистикой",
    "где посмотреть структуру компании",
    "я новый сотрудник и не понимаю с чего начать",
    "меня поставили на новую должность что делать",
    # Comparison questions
    "взять задачу в работу это то же самое что взять отпуск?",
    "распоряжение и задача это одно и то же?",
    "КП и ЦКП это одно и то же?",
    "договор и приказ это один тип документа?",
    "командировка это отпуск?",
    # Negative / absent policy questions
    "я потерял документ",
    "я потерял пропуск",
    "я забыл пароль",
    "я опоздал на работу",
    "я заболел и не вышел",
    "мне нужен новый ноутбук",
    "мне нужен доступ к 1С",
    # Commercial workflow questions
    "как сделать КП клиенту",
    "как подготовить коммерческое предложение",
    "что включить в коммерческое предложение",
    "какой опыт КП у клиента",
    "сформируй КП по заявке",
    # 1C / operational future questions
    "найди цену детали",
    "есть ли остатки на складе",
    "какой статус заказа",
    "найди контрагента",
    "покажи счета клиента",
]

CRITICAL_FLAGS = {"CKP_BAD_EXPANSION", "OFF_TOPIC_WITH_SOURCE", "UNKNOWN_SOURCE", "CRASH"}
COMPATIBLE_SOURCES_BY_INTENT = {
    "company_identity": {
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
    },
    "company_ckp": {"ИП-0003 ЦКП SERVICELINE"},
    "org_structure": {"2026-03-03_Оргсхема _ Компании"},
    "roles_responsibility": {"2026-03-03_Оргсхема _ Компании"},
    "zrs_definition": {"ИП-0004 Структура ЗРС"},
    "zrs_approval": {
        "ИП-0004 Структура ЗРС",
        "Инструкция Согласования ЗРС в Документообороте",
    },
    "document_flow": {"ИП-0006 Документооборот"},
    "contract_approval": {
        "ИП-0006 Документооборот",
        "Инструкция Согласования договоров в Документообороте",
    },
    "business_trip": {"Инструкция Согласования командировки в Документообороте"},
    "order_disposition": {"ИП-0005 Распоряжения"},
    "written_communication": {"Регламент по письменной коммуникации"},
    "weekly_planning": {"Регламент по планированию на неделю"},
    "vacation": set(),
    "statistics_kpi": set(),
    "onboarding_position": set(),
}


def main() -> int:
    _configure_stdout()

    analyzer = QueryAnalyzer()
    rows = []
    warnings: list[tuple[str, list[str]]] = []
    by_intent: Counter[str] = Counter()
    by_answer_type: Counter[str] = Counter()

    print("=== QUERY ANALYZER EXPLORATORY SMOKE ===")
    print(f"Model: {analyzer.model}")
    print()
    print(
        _format_row(
            [
                "question",
                "intent",
                "answer_type",
                "preferred_sources",
                "confidence",
                "flags",
            ]
        )
    )
    print(_format_row(["-" * 28, "-" * 20, "-" * 14, "-" * 30, "-" * 10, "-" * 34]))

    for question in QUESTIONS:
        try:
            plan = analyzer.analyze(question)
            flags = _flags_for_plan(question, plan)
            by_intent[plan.intent] += 1
            by_answer_type[plan.answer_type] += 1
            row = [
                question,
                plan.intent,
                plan.answer_type,
                ", ".join(plan.preferred_sources),
                f"{plan.confidence:.2f}",
                ", ".join(flags),
            ]
        except Exception as exc:
            flags = ["CRASH"]
            row = [question, "-", "-", "-", "-", f"CRASH: {type(exc).__name__}: {exc}"]

        rows.append((question, flags))
        if flags:
            warnings.append((question, flags))
        print(_format_row(row))

    critical = [
        (question, flags)
        for question, flags in rows
        if any(flag in CRITICAL_FLAGS for flag in flags)
    ]

    print()
    print(f"Total questions: {len(QUESTIONS)}")
    print(f"Warnings: {len(warnings)}")
    print("By intent:")
    _print_counter(by_intent)
    print("By answer_type:")
    _print_counter(by_answer_type)
    print("Top warning examples:")
    if warnings:
        for question, flags in warnings[:10]:
            print(f"- {question}: {', '.join(flags)}")
    else:
        print("-")

    status = "FAIL" if critical else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    print(f"Status: {status}")
    return 1 if critical else 0


def _flags_for_plan(question: str, plan: Any) -> list[str]:
    flags: list[str] = []
    normalized_question = _normalize(question)
    serialized = json.dumps(plan.to_dict(), ensure_ascii=False).casefold()

    unknown_sources = [
        source for source in plan.preferred_sources if source not in KNOWN_SOURCE_TITLES
    ]
    if unknown_sources:
        flags.append("UNKNOWN_SOURCE")

    if plan.confidence < 0.6:
        flags.append("LOW_CONFIDENCE")

    if plan.intent not in {"off_topic", "unknown"} and not plan.query_expansions:
        flags.append("EMPTY_EXPANSIONS_FOR_KNOWN_INTENT")

    if (
        plan.answer_type == "procedure"
        and not plan.preferred_sources
        and plan.intent in {"equipment_it_request", "document_loss", "kp_commercial_offer"}
    ):
        flags.append("PROCEDURE_WITHOUT_SOURCE")

    if plan.intent == "off_topic" and plan.preferred_sources:
        flags.append("OFF_TOPIC_WITH_SOURCE")

    if _has_incompatible_source(plan):
        flags.append("POSSIBLE_WRONG_INTENT")

    if "центр комплексных предложений" in serialized:
        flags.append("CKP_BAD_EXPANSION")

    if (
        _contains_any(
            normalized_question,
            ("чем занимается", "что делает компания", "цель компании"),
        )
        and plan.intent == "org_structure"
    ):
        flags.append("POSSIBLE_WRONG_INTENT")

    if (
        _contains_any(
            normalized_question,
            ("отдел", "подраздел", "оргсхем", "структура компании"),
        )
        and plan.intent not in {"org_structure", "roles_responsibility"}
    ):
        flags.append("POSSIBLE_WRONG_INTENT")

    if _contains_bare_kp(normalized_question) and plan.intent != "ambiguous_abbreviation":
        flags.append("POSSIBLE_WRONG_INTENT")

    if "цкп" in normalized_question and plan.intent != "company_ckp":
        flags.append("POSSIBLE_WRONG_INTENT")

    if (
        _contains_any(
            normalized_question,
            (
                "найди цену",
                "остатки",
                "статус заказа",
                "найди контрагента",
                "счета клиента",
            ),
        )
        and plan.intent
        in {
            "document_flow",
            "contract_approval",
            "order_disposition",
            "business_trip",
            "vacation",
        }
    ):
        flags.append("POSSIBLE_WRONG_INTENT")

    if (
        _contains_any(
            normalized_question,
            ("опоздал", "опоздание", "заболел", "не вышел", "потерял пропуск"),
        )
        and plan.intent
        in {"vacation", "document_loss", "document_flow", "contract_approval"}
    ):
        flags.append("POSSIBLE_WRONG_INTENT")

    if _looks_like_removed_source_case(normalized_question, plan):
        flags.append("FAKE_SOURCE_REMOVED")

    return list(dict.fromkeys(flags))


def _has_incompatible_source(plan: Any) -> bool:
    if not plan.preferred_sources:
        return False
    compatible_sources = COMPATIBLE_SOURCES_BY_INTENT.get(plan.intent)
    if compatible_sources is None:
        return False
    return not set(plan.preferred_sources).issubset(compatible_sources)


def _looks_like_removed_source_case(question: str, plan: Any) -> bool:
    if plan.preferred_sources:
        return False
    if plan.intent in {"equipment_it_request", "document_loss"}:
        return True
    return plan.intent == "kp_commercial_offer" and _contains_any(
        question,
        ("кп", "коммерческое предложение"),
    )


def _contains_bare_kp(question: str) -> bool:
    if "цкп" in question:
        return False
    if "коммерческое предложение" in question:
        return False
    return any(token == "кп" for token in question.replace("?", " ").split())


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _normalize(value: str) -> str:
    return value.casefold().replace("ё", "е")


def _print_counter(counter: Counter[str]) -> None:
    if not counter:
        print("-")
        return
    for key, count in counter.most_common():
        print(f"- {key}: {count}")


def _format_row(values: list[str]) -> str:
    widths = [34, 24, 16, 36, 10, 42]
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
