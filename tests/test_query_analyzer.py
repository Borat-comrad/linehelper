from __future__ import annotations

import json

import pytest

from linehelper.rag.query_analyzer import (
    QueryAnalyzer,
    build_query_analyzer_prompt,
)


ORG_STRUCTURE_RESPONSE = {
    "intent": "org_structure",
    "normalized_question": "Какие подразделения есть в компании?",
    "query_expansions": ["оргсхема компании", "структура компании"],
    "preferred_sources": ["2026-03-03_Оргсхема _ Компании"],
    "answer_type": "list",
    "needs_clarification": False,
    "clarification_question": None,
    "confidence": 0.9,
    "notes": "Вопрос про оргструктуру.",
}


def test_valid_json_returns_query_plan() -> None:
    client = FakeOllamaClient(json.dumps(ORG_STRUCTURE_RESPONSE, ensure_ascii=False))
    analyzer = QueryAnalyzer(ollama_client=client, model="fake-model")

    plan = analyzer.analyze("какие отделы есть в компании?")

    assert plan.intent == "org_structure"
    assert plan.answer_type == "list"
    assert "2026-03-03_Оргсхема _ Компании" in plan.preferred_sources
    assert client.kwargs["temperature"] == 0
    assert client.kwargs["model"] == "fake-model"


def test_markdown_json_block_is_parsed() -> None:
    response = "```json\n" + json.dumps(ORG_STRUCTURE_RESPONSE, ensure_ascii=False) + "\n```"
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient(response))

    plan = analyzer.analyze("какие отделы есть в компании?")

    assert plan.intent == "org_structure"
    assert plan.answer_type == "list"


def test_company_activity_overrides_wrong_org_structure_intent() -> None:
    client = FakeOllamaClient(json.dumps(ORG_STRUCTURE_RESPONSE, ensure_ascii=False))
    analyzer = QueryAnalyzer(ollama_client=client)

    plan = analyzer.analyze("чем занимается компания?")

    assert plan.intent == "company_identity"
    assert plan.answer_type == "definition"
    assert "ИП-0002 Цели и замыслы компании Serviceline" in plan.preferred_sources


def test_preferred_sources_outside_allowlist_are_removed() -> None:
    response = {
        **ORG_STRUCTURE_RESPONSE,
        "preferred_sources": [
            "2026-03-03_Оргсхема _ Компании",
            "2026-03-03_ИТ_Заявки на приобретение ноутбука",
            "invented source",
        ],
    }
    analyzer = QueryAnalyzer(
        ollama_client=FakeOllamaClient(json.dumps(response, ensure_ascii=False))
    )

    plan = analyzer.analyze("какие отделы есть в компании?")

    assert plan.preferred_sources == ["2026-03-03_Оргсхема _ Компании"]


def test_document_loss_procedure_is_downgraded() -> None:
    response = {
        "intent": "document_loss",
        "normalized_question": "Что делать, если потерян документ?",
        "query_expansions": ["потерян документ"],
        "preferred_sources": ["invented document-loss source"],
        "answer_type": "procedure",
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
        "notes": "Модель ошибочно выбрала procedure.",
    }
    analyzer = QueryAnalyzer(
        ollama_client=FakeOllamaClient(json.dumps(response, ensure_ascii=False))
    )

    plan = analyzer.analyze("я потерял документ что делать")

    assert plan.intent == "document_loss"
    assert plan.answer_type == "partial_answer"
    assert plan.preferred_sources == []


def test_equipment_request_removes_procedure_and_fake_it_source() -> None:
    response = {
        "intent": "equipment_it_request",
        "normalized_question": "Как получить новый ноутбук?",
        "query_expansions": ["получить ноутбук", "IT-заявка"],
        "preferred_sources": ["2026-03-03_ИТ_Заявки на приобретение ноутбука"],
        "answer_type": "procedure",
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.95,
        "notes": "Модель выдумала IT-source.",
    }
    analyzer = QueryAnalyzer(
        ollama_client=FakeOllamaClient(json.dumps(response, ensure_ascii=False))
    )

    plan = analyzer.analyze("как получить новый ноутбук?")

    assert plan.intent == "equipment_it_request"
    assert plan.answer_type == "general"
    assert plan.preferred_sources == []


def test_invalid_json_falls_back_without_exception() -> None:
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient("not json"))

    plan = analyzer.analyze("из каких подразделений состоит компания?")

    assert plan.intent == "org_structure"
    assert plan.answer_type == "list"
    assert analyzer.last_error is not None


def test_ckp_uses_valuable_final_product_meaning() -> None:
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient("not json"))

    plan = analyzer.analyze("что такое цкп?")
    messages = build_query_analyzer_prompt("что такое цкп?")
    prompt_text = "\n".join(message["content"] for message in messages)

    assert plan.intent == "company_ckp"
    assert "ценный конечный продукт" in prompt_text
    assert "ценный конечный продукт" in " ".join(plan.query_expansions + [plan.notes or ""])
    assert "центр комплексных предложений" not in " ".join(
        plan.query_expansions + [plan.notes or ""]
    ).casefold()


def test_ambiguous_kp_requires_clarification() -> None:
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient("not json"))

    plan = analyzer.analyze("что такое кп?")

    assert plan.intent == "ambiguous_abbreviation"
    assert plan.answer_type == "clarification"
    assert plan.needs_clarification is True
    assert plan.clarification_question


def test_commercial_offer_is_not_ambiguous_kp() -> None:
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient("not json"))

    plan = analyzer.analyze("коммерческое предложение")

    assert plan.intent == "kp_commercial_offer"
    assert plan.answer_type in {"partial_answer", "general"}


def test_org_structure_fallback_when_ollama_fails() -> None:
    analyzer = QueryAnalyzer(ollama_client=ErrorOllamaClient())

    plan = analyzer.analyze("из каких подразделений состоит компания?")

    assert plan.intent == "org_structure"


def test_document_loss_fallback() -> None:
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient("not json"))

    plan = analyzer.analyze("я потерял документ что делать")

    assert plan.intent == "document_loss"
    assert plan.answer_type in {"no_answer", "partial_answer"}


def test_off_topic_fallback() -> None:
    analyzer = QueryAnalyzer(ollama_client=FakeOllamaClient("not json"))

    plan = analyzer.analyze("как приготовить борщ?")

    assert plan.intent == "off_topic"
    assert plan.answer_type == "no_answer"


def test_unknown_schema_values_are_normalized() -> None:
    response = {
        "intent": "made_up",
        "normalized_question": "x",
        "query_expansions": "not a list",
        "preferred_sources": None,
        "answer_type": "made_up",
        "needs_clarification": "false",
        "clarification_question": "",
        "confidence": 3,
        "notes": None,
    }
    analyzer = QueryAnalyzer(
        ollama_client=FakeOllamaClient(json.dumps(response, ensure_ascii=False))
    )

    plan = analyzer.analyze("неизвестный вопрос")

    assert plan.intent == "unknown"
    assert plan.answer_type == "general"
    assert plan.query_expansions == []
    assert plan.preferred_sources == []
    assert plan.confidence == 1.0


class FakeOllamaClient:
    model = "fake-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[dict[str, str]] = []
        self.kwargs = {}

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        self.messages = messages
        self.kwargs = kwargs
        return self.content


class ErrorOllamaClient:
    model = "fake-model"

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        raise RuntimeError("ollama is unavailable")
