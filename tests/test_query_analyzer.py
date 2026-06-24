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


@pytest.mark.parametrize(
    "question",
    [
        "какие отделы есть в компании?",
        "из каких подразделений состоит компания?",
        "какие отделения есть в компании?",
        "как устроена компания?",
        "из чего состоит структура компании?",
        "какая организационная структура компании?",
        "что входит в структуру компании?",
        "перечисли подразделения компании",
        "какие есть службы и отделы?",
    ],
)
def test_strict_org_structure_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "org_structure"
    assert plan.answer_type == "list"
    assert "2026-03-03_Оргсхема _ Компании" in plan.preferred_sources


@pytest.mark.parametrize(
    "question",
    [
        "чем занимается компания?",
        "что делает компания?",
        "какая основная цель компании?",
        "какой бизнес у Serviceline?",
        "в чем смысл деятельности компании?",
        "для чего существует компания?",
        "что такое Serviceline?",
        "расскажи кратко о компании",
    ],
)
def test_strict_company_identity_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "company_identity"
    assert plan.intent != "org_structure"
    assert plan.answer_type in {"definition", "general"}
    assert {
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
    }.intersection(plan.preferred_sources)


@pytest.mark.parametrize(
    "question",
    [
        "что такое цкп?",
        "какой цкп компании?",
        "что значит ценный конечный продукт?",
        "а при чем тут ценный конечный продукт?",
        "почему ЦКП важен?",
        "какой главный продукт компании?",
    ],
)
def test_strict_company_ckp_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "company_ckp"
    assert plan.answer_type in {"definition", "general"}
    assert "ИП-0003 ЦКП SERVICELINE" in plan.preferred_sources
    assert "центр комплексных предложений" not in json.dumps(
        plan.to_dict(),
        ensure_ascii=False,
    ).casefold()


@pytest.mark.parametrize("question", ["что такое кп?", "кп", "что значит КП?"])
def test_strict_ambiguous_kp_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "ambiguous_abbreviation"
    assert plan.answer_type == "clarification"
    assert plan.needs_clarification is True


@pytest.mark.parametrize(
    "question",
    [
        "коммерческое предложение",
        "КП как коммерческое предложение",
        "как составить коммерческое предложение?",
        "подготовить КП клиенту",
    ],
)
def test_strict_commercial_offer_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "kp_commercial_offer"
    assert plan.answer_type in {"partial_answer", "general", "procedure"}
    assert all(source in _KNOWN_TEST_SOURCES for source in plan.preferred_sources)


@pytest.mark.parametrize(
    "question",
    [
        "как работает документооборот?",
        "какие правила документооборота действуют?",
        "как создать документ в 1С ДО?",
        "что такое 1С документооборот?",
        "как согласовать документ?",
    ],
)
def test_strict_document_flow_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "document_flow"
    assert "ИП-0006 Документооборот" in plan.preferred_sources


@pytest.mark.parametrize(
    "question",
    [
        "как согласовать договор?",
        "как завести договор в документообороте?",
        "что делать с договором?",
        "договор нужно провести через 1С ДО?",
        "кто согласует договор?",
    ],
)
def test_strict_contract_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "contract_approval"
    assert plan.answer_type in {"procedure", "general"}


@pytest.mark.parametrize(
    "question",
    [
        "хочу взять отпуск",
        "как оформить отпуск?",
        "мне нужно перенести отпуск",
        "заявление на отпуск",
        "где оформить отпуск в документообороте?",
    ],
)
def test_strict_vacation_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "vacation"
    assert plan.answer_type == "procedure"


@pytest.mark.parametrize(
    "question",
    [
        "как согласовать командировку?",
        "как оформить командировку?",
        "мне нужно ехать в командировку",
        "служебное задание на командировку",
        "приказ о командировке",
    ],
)
def test_strict_business_trip_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "business_trip"
    assert plan.answer_type in {"procedure", "general"}


@pytest.mark.parametrize(
    "question",
    [
        "что значит взять задачу в работу?",
        "как взять задачу в работу?",
        "как направить задачу подчиненному?",
        "как поставить задачу в 1С ДО?",
        "что делать с новой задачей?",
    ],
)
def test_strict_task_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "task_management"
    assert plan.answer_type in {"definition", "procedure", "general"}


@pytest.mark.parametrize(
    "question",
    [
        "как оформить распоряжение?",
        "что должно быть в распоряжении?",
        "как дать распоряжение сотруднику?",
        "как контролировать распоряжение?",
        "распоряжение нужно писать письменно?",
    ],
)
def test_strict_order_disposition_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "order_disposition"
    assert plan.answer_type in {"procedure", "general"}


@pytest.mark.parametrize(
    "question",
    [
        "что такое ЗРС?",
        "из чего состоит ЗРС?",
        "зачем нужна ЗРС?",
        "структура ЗРС",
        "ситуация данные решение",
    ],
)
def test_strict_zrs_definition_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "zrs_definition"
    assert "ИП-0004 Структура ЗРС" in plan.preferred_sources


@pytest.mark.parametrize(
    "question",
    [
        "как согласовать ЗРС?",
        "как оформить ЗРС в документообороте?",
        "создать ЗРС в 1С ДО",
    ],
)
def test_strict_zrs_approval_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent in {"zrs_approval", "zrs_definition"}
    assert plan.intent != "off_topic"


@pytest.mark.parametrize(
    "question",
    [
        "я потерял документ что делать",
        "потерял оригинал документа",
        "не могу найти документ",
        "пропал документ в 1С ДО",
    ],
)
def test_strict_document_loss_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "document_loss"
    assert plan.answer_type in {"partial_answer", "no_answer", "general"}
    assert plan.answer_type != "procedure"
    assert plan.preferred_sources == []


@pytest.mark.parametrize(
    "question",
    [
        "как получить новый ноутбук?",
        "мне нужен рабочий ноутбук",
        "сломался компьютер",
        "как запросить доступ?",
        "как установить программу?",
    ],
)
def test_strict_equipment_it_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent == "equipment_it_request"
    assert plan.answer_type in {"partial_answer", "no_answer", "general"}
    assert plan.answer_type != "procedure"
    assert all(source in _KNOWN_TEST_SOURCES for source in plan.preferred_sources)


@pytest.mark.parametrize(
    "question",
    [
        "как приготовить борщ?",
        "какая погода завтра?",
        "сколько маленьких утят после бега есть хотят?",
        "напиши стих про кота",
        "какой курс доллара?",
        "кто выиграл вчера матч?",
    ],
)
def test_strict_off_topic_questions(question: str) -> None:
    plan = _fallback_plan(question)

    assert plan.intent in {"off_topic", "unknown"}
    assert plan.answer_type == "no_answer"
    assert plan.preferred_sources == []


_KNOWN_TEST_SOURCES = {
    "2026-03-03_Оргсхема _ Компании",
    "ИП-0002 Цели и замыслы компании Serviceline",
    "ИП-0003 ЦКП SERVICELINE",
    "ИП-0004 Структура ЗРС",
    "ИП-0005 Распоряжения",
    "ИП-0006 Документооборот",
    "Инструкция Согласования договоров в Документообороте",
    "Инструкция Согласования командировки в Документообороте",
    "Инструкция Согласования ЗРС в Документообороте",
    "Регламент по письменной коммуникации",
    "Регламент по планированию на неделю",
}


def _fallback_plan(question: str):
    return QueryAnalyzer(ollama_client=FakeOllamaClient("not json")).analyze(question)


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
