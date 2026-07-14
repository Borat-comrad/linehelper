from __future__ import annotations

import pytest

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator
from linehelper.llm.ollama_client import OllamaEmptyResponseError
from linehelper.rag.query_analyzer import QueryPlan, fallback_query_plan
from linehelper.rag.retriever import RetrievedChunk


@pytest.fixture(autouse=True)
def _use_rule_based_default_query_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    class RuleBasedQueryAnalyzer:
        def analyze(self, question: str) -> QueryPlan:
            return fallback_query_plan(question)

    monkeypatch.setattr(
        "linehelper.rag.query_analyzer.QueryAnalyzer",
        RuleBasedQueryAnalyzer,
    )


def test_empty_question_is_rejected() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([]),
        llm_client=FakeClient("ok"),
    )

    with pytest.raises(ValueError):
        generator.answer("   ")


def test_query_analyzer_is_called_by_default() -> None:
    analyzer = FakeQueryAnalyzer(_org_structure_plan())
    retriever = FakeRetriever([])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeClient("unused"),
        query_analyzer=analyzer,
    )

    result = generator.answer("какие отделы есть в компании?")

    assert analyzer.calls == ["какие отделы есть в компании?"]
    assert result.query_plan is not None
    assert result.query_plan["enabled"] is True
    assert result.query_plan["intent"] == "org_structure"


def test_query_analyzer_expands_retrieval_by_default() -> None:
    analyzer = FakeQueryAnalyzer(_org_structure_plan())
    retriever = FakeRetriever([])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeClient("unused"),
        query_analyzer=analyzer,
    )

    result = generator.answer("какие отделы есть в компании?")

    assert analyzer.calls == ["какие отделы есть в компании?"]
    assert [call[0] for call in retriever.calls] == [
        "какие отделы есть в компании?",
        "Какие подразделения есть в организационной структуре компании?",
        "оргсхема компании",
        "организационная структура компании",
    ]
    assert result.query_plan is not None
    assert result.query_plan["enabled"] is True
    assert result.query_plan["intent"] == "org_structure"


def test_query_analyzer_error_falls_back_to_plain_retrieval_path() -> None:
    retriever = FakeRetriever([])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeClient("unused"),
        query_analyzer=ErrorQueryAnalyzer(),
    )

    result = generator.answer("какие отделы есть в компании?")

    assert retriever.calls == [("какие отделы есть в компании?", 5, 30)]
    assert result.query_plan is not None
    assert result.query_plan["enabled"] is True
    assert result.query_plan["fallback_used"] is True
    assert "query analyzer failed" in result.query_plan["error"]
    assert result.response_kind == "no_answer"


def test_query_analyzer_internal_fallback_is_reported_in_diagnostics() -> None:
    retriever = FakeRetriever([])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeClient("unused"),
        query_analyzer=FallbackQueryAnalyzer(_org_structure_plan()),
    )

    result = generator.answer("какие отделы есть в компании?")

    assert result.query_plan is not None
    assert result.query_plan["enabled"] is True
    assert result.query_plan["intent"] == "org_structure"
    assert result.query_plan["fallback_used"] is True
    assert result.query_plan["fallback_reason"] == "query_analyzer_error"
    assert "broken analyzer json" in result.query_plan["error"]


def test_query_analyzer_expands_org_structure_retrieval_queries() -> None:
    retriever = FakeRetriever([])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeClient("unused"),
        query_analyzer=FakeQueryAnalyzer(_org_structure_plan()),
    )

    generator.answer("какие отделы есть в компании?")

    retrieval_queries = [call[0] for call in retriever.calls]
    assert "какие отделы есть в компании?" in retrieval_queries
    assert "Какие подразделения есть в организационной структуре компании?" in retrieval_queries
    assert "оргсхема компании" in retrieval_queries
    assert "организационная структура компании" in retrieval_queries


def test_query_analyzer_keeps_unit_identifier_in_expanded_queries() -> None:
    retriever = FakeRetriever([])
    generator = RagAnswerGenerator(
        retriever=retriever,
        llm_client=FakeClient("unused"),
        query_analyzer=FakeQueryAnalyzer(_org_structure_plan()),
    )

    generator.answer("что расскажешь про отдел 4А?")

    retrieval_queries = [call[0] for call in retriever.calls]
    assert "оргсхема компании 4А" in retrieval_queries
    assert "организационная структура компании 4А" in retrieval_queries


def test_preferred_source_is_not_hard_filter_for_exact_organization_unit() -> None:
    organization_unit = _organization_chunk(
        title="Отделение 4А — Закупки",
        doc_type="organization_unit",
        text="Отделение 4А — Закупки. Руководитель: Силаева Юлия. ЦКП: заказы подготовлены к отгрузке.",
        final_score=420.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "organization_unit:division_4a:2025-12-17",
            "unit_number": "4А",
            "head_name": "Силаева Юлия",
        },
    )
    old_pdf = _organization_chunk(
        title="2026-03-03_Оргсхема _ Компании",
        doc_type="org_structure",
        source="data/raw_docs/2026-03-03_Оргсхема _ Компании.pdf",
        text="Оргсхема компании описывает крупные отделения.",
        final_score=380.0,
        metadata={"source_file": "2026-03-03_Оргсхема _ Компании.pdf"},
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([organization_unit, old_pdf]),
        llm_client=FakeClient("ok"),
        query_analyzer=FakeQueryAnalyzer(_org_structure_plan()),
    )

    result = generator.answer("что расскажешь про отдел 4А?")

    assert result.response_kind == "answer"
    assert result.sources[0].title == "Отделение 4А — Закупки"
    assert "Отделение 4А — Закупки" in generator.llm_client.messages[-1]["content"]


def test_leadership_context_keeps_employee_contact_after_exact_unit_boost() -> None:
    organization_unit = _organization_chunk(
        title="Отделение 4А — Закупки",
        doc_type="organization_unit",
        text="Отделение 4А — Закупки. Руководитель: Силаева Юлия.",
        final_score=600.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "organization_unit:division_4a:2025-12-17",
            "unit_number": "4А",
            "head_name": "Силаева Юлия",
        },
    )
    employee = _organization_chunk(
        title="Силаева Юлия",
        doc_type="employee_role",
        text=(
            "Силаева Юлия. Должности: руководитель отделения закупок (4А). "
            "Рабочий телефон: +7 961 105-03-08."
        ),
        final_score=160.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "employee:silaeva_yuliya:2025-12-17",
            "employee_name": "Силаева Юлия",
        },
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([organization_unit, employee]),
        llm_client=FakeClient("ok"),
        query_analyzer=FakeQueryAnalyzer(_roles_responsibility_plan()),
    )

    result = generator.answer("Кто руководит отделением 4А — Закупки?")

    assert [source.title for source in result.sources] == [
        "Отделение 4А — Закупки",
        "Силаева Юлия",
    ]
    assert "+7 961 105-03-08" in generator.llm_client.messages[-1]["content"]


def test_leadership_question_prefers_unit_and_employee_over_brand_route() -> None:
    route = _organization_chunk(
        title="Маршрут: ЗАКУПКИ: KRONES / KHS / HEUFT",
        doc_type="responsibility_route",
        text="По вопросам закупок KRONES обращаться к Карачурину Денису.",
        final_score=320.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "responsibility_route:zakupki_krones_khs_heuft:2025-12-17",
        },
    )
    unit = _organization_chunk(
        title="Отделение 4А — Закупки",
        doc_type="organization_unit",
        text="Отделение 4А — Закупки. Руководитель: Силаева Юлия.",
        final_score=240.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "organization_unit:division_4a:2025-12-17",
            "head_name": "Силаева Юлия",
        },
    )
    employee = _organization_chunk(
        title="Силаева Юлия",
        doc_type="employee_role",
        text="Силаева Юлия. Должности: руководитель отделения 4А — Закупки; начальник отдела 11А.",
        final_score=230.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "employee:silaeva_yuliya:2025-12-17",
            "employee_name": "Силаева Юлия",
        },
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([route, unit, employee]),
        llm_client=FakeClient("ok"),
        query_analyzer=FakeQueryAnalyzer(_roles_responsibility_plan()),
    )

    result = generator.answer("кто главный у закупщиков?")

    assert [source.title for source in result.sources[:2]] == [
        "Отделение 4А — Закупки",
        "Силаева Юлия",
    ]


def test_contact_question_keeps_brand_responsibility_route_first() -> None:
    route = _organization_chunk(
        title="Маршрут: ЗАКУПКИ: KRONES / KHS / HEUFT",
        doc_type="responsibility_route",
        text="По вопросам закупок KRONES обращаться к Карачурину Денису. Телефон: +7 910 000-00-00.",
        final_score=250.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "responsibility_route:zakupki_krones_khs_heuft:2025-12-17",
            "topic": "закупки KRONES",
        },
    )
    unit = _organization_chunk(
        title="Отделение 4А — Закупки",
        doc_type="organization_unit",
        text="Отделение 4А — Закупки. Руководитель: Силаева Юлия.",
        final_score=300.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "organization_unit:division_4a:2025-12-17",
            "head_name": "Силаева Юлия",
        },
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([unit, route]),
        llm_client=FakeClient("ok"),
        query_analyzer=FakeQueryAnalyzer(_roles_responsibility_plan()),
    )

    result = generator.answer("К кому обратиться по закупкам KRONES?")

    assert result.sources[0].title == "Маршрут: ЗАКУПКИ: KRONES / KHS / HEUFT"


def test_legacy_org_chart_source_still_handles_org_chart_rules() -> None:
    old_pdf = _organization_chunk(
        title="Регламент по использованию оргсхемы",
        doc_type="org_structure",
        source="data/raw_docs/Регламент по использованию оргсхемы.pdf",
        text="Оргсхема нужна для описания организующей схемы и правил использования оргсхемы.",
        final_score=360.0,
        metadata={"source_file": "Регламент по использованию оргсхемы.pdf"},
    )
    structured_overview = _organization_chunk(
        title="Оргструктура Serviceline",
        doc_type="organization_overview",
        text="Компания Serviceline включает основные отделения.",
        final_score=220.0,
        metadata={
            "knowledge_domain": "organization_structure",
            "source_file": "bvr_company_structure_instruction_v2 (2).txt",
            "record_key": "organization_overview:serviceline:2025-12-17",
        },
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([old_pdf, structured_overview]),
        llm_client=FakeClient("ok"),
        query_analyzer=FakeQueryAnalyzer(_org_structure_plan()),
    )

    result = generator.answer("Что такое оргсхема и для чего она нужна?")

    assert result.sources[0].title == "Регламент по использованию оргсхемы"


def test_query_plan_diagnostics_contains_runtime_metadata() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([]),
        llm_client=FakeClient("unused"),
        query_analyzer=FakeQueryAnalyzer(_org_structure_plan()),
    )

    result = generator.answer("какие отделы есть в компании?")

    assert result.query_plan == {
        "enabled": True,
        "intent": "org_structure",
        "answer_type": "list",
        "normalized_question": "Какие подразделения есть в организационной структуре компании?",
        "query_expansions": [
            "оргсхема компании",
            "организационная структура компании",
        ],
        "preferred_sources": ["2026-03-03_Оргсхема _ Компании"],
        "confidence": 0.9,
    }


@pytest.mark.parametrize(
    ("question", "plan_name", "expected_intent"),
    [
        ("какие отделы есть в компании?", "org_structure", "org_structure"),
        ("чем занимается компания?", "company_identity", "company_identity"),
    ],
)
def test_query_analyzer_exposes_expected_intents_in_diagnostics(
    question: str,
    plan_name: str,
    expected_intent: str,
) -> None:
    plan = (
        _org_structure_plan()
        if plan_name == "org_structure"
        else _company_identity_plan()
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([]),
        llm_client=FakeClient("unused"),
        query_analyzer=FakeQueryAnalyzer(plan),
    )

    result = generator.answer(question)

    assert result.query_plan is not None
    assert result.query_plan["intent"] == expected_intent


def test_one_c_query_plan_does_not_use_semantic_sources_as_answer_sources() -> None:
    semantic_noise = _chunk_with(
        title="ИП-0005 Распоряжения",
        section="Статусы распоряжений",
        text="В тексте случайно встречается статус заказа, но это не поиск в 1С.",
        final_score=250.0,
    )
    client = FakeClient("unused")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([semantic_noise]),
        llm_client=client,
        query_analyzer=FakeQueryAnalyzer(_one_c_operational_lookup_plan()),
    )

    result = generator.answer("какой статус заказа")

    assert result.query_plan is not None
    assert result.query_plan["intent"] == "one_c_operational_lookup"
    assert result.response_kind == "no_answer"
    assert result.sources == []
    assert result.chunks_used == 0
    assert client.messages == []


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

    assert result.answer == (
        "В базе знаний Serviceline нет точного ответа на этот вопрос. "
        "Похоже, вопрос не относится к корпоративным регламентам, инструкциям, "
        "оргструктуре или документообороту."
    )
    assert result.sources == []
    assert result.chunks_used == 0
    assert result.prompt_length == 0
    assert result.response_kind == "no_answer"
    assert client.messages == []


def test_vacation_question_filters_noisy_context_chunks() -> None:
    vacation_1 = _chunk_with(
        title="Инструкция Отпуск в Документообороте",
        section="Порядок работы: часть 1",
        text="Чтобы оформить отпуск, сотрудник создает задачу на отпуск в Документообороте.",
        final_score=180.0,
    )
    vacation_2 = _chunk_with(
        title="Инструкция Отпуск в Документообороте",
        section="Порядок работы: часть 2",
        text="После создания задачи отпуск согласуется по маршруту в Документообороте.",
        final_score=174.0,
    )
    noise = [
        _chunk_with(
            title="Работа с задачами",
            section="Статусы задач",
            text="Если нужно взять задачу в работу, измените статус задачи.",
            final_score=123.0,
        ),
        _chunk_with(
            title="Регламент по статистикам",
            section="Падающая статистика",
            text="Руководитель может взять статистику для анализа.",
            final_score=93.0,
        ),
        _chunk_with(
            title="Инструкция - Как начать работу в новой должности",
            section="Когда вас игнорируют",
            text="При начале работы в новой должности нужно изучить должностную папку.",
            final_score=92.0,
        ),
    ]
    client = FakeClient("Нужно оформить отпуск по инструкции.")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([vacation_1, vacation_2, *noise]),
        llm_client=client,
        context_limit=3,
        context_score_ratio=0.65,
    )

    result = generator.answer("Хочу взять отпуск, что делать?")
    prompt = client.messages[-1]["content"]

    assert result.chunks_used == 2
    assert [source.title for source in result.sources] == [
        "Инструкция Отпуск в Документообороте",
        "Инструкция Отпуск в Документообороте",
    ]
    assert "Инструкция Отпуск в Документообороте" in prompt
    assert "Работа с задачами" not in prompt
    assert "Регламент по статистикам" not in prompt
    assert "Инструкция - Как начать работу в новой должности" not in prompt


def test_comparison_question_keeps_task_and_vacation_context() -> None:
    task = _chunk_with(
        title="Работа с задачами",
        section="Статусы задач",
        text="Если нужно взять задачу в работу, измените статус задачи.",
        final_score=265.0,
    )
    vacation = _chunk_with(
        title="Инструкция Отпуск в Документообороте",
        section="Порядок работы: часть 1",
        text="Чтобы оформить отпуск, сотрудник создает задачу на отпуск в Документообороте.",
        final_score=180.0,
    )
    noise = _chunk_with(
        title="Инструкция - Как начать работу в новой должности",
        section="Компетентность",
        text="Общий фрагмент про начало работы в новой должности.",
        final_score=191.0,
    )
    client = FakeClient("Нет, это разные действия: задача берется в работу, а отпуск оформляется отдельно.")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([task, noise, vacation]),
        llm_client=client,
        context_limit=3,
    )

    result = generator.answer("Взять задачу в работу — это то же самое, что взять отпуск?")
    prompt = client.messages[-1]["content"]
    source_titles = [source.title for source in result.sources]

    assert result.response_kind == "answer"
    assert "Работа с задачами" in source_titles
    assert "Инструкция Отпуск в Документообороте" in source_titles
    assert source_titles != ["Инструкция Отпуск в Документообороте"]
    assert "Работа с задачами" in prompt
    assert "Инструкция Отпуск в Документообороте" in prompt
    assert result.answer.startswith("Нет")


def test_control_process_comparison_keeps_non_vacation_context() -> None:
    control = _chunk_with(
        title="ИП-0005 Распоряжения",
        section="Письменная форма и контроль",
        text="Распоряжение фиксирует исполнителя, срок, ожидаемый результат и контроль исполнения.",
        final_score=226.0,
    )
    vacation = _chunk_with(
        title="Инструкция Отпуск в Документообороте",
        section="Порядок работы: часть 1",
        text="Чтобы оформить отпуск, сотрудник создает задачу на отпуск в Документообороте.",
        final_score=252.0,
    )
    client = FakeClient("Нет, взять под контроль процесс и оформить отпуск — разные смыслы.")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([vacation, control]),
        llm_client=client,
        context_limit=3,
    )

    result = generator.answer("Мне нужно взять под контроль процесс, это отпуск?")
    prompt = client.messages[-1]["content"]
    source_titles = [source.title for source in result.sources]

    assert result.response_kind == "answer"
    assert "ИП-0005 Распоряжения" in source_titles
    assert "Инструкция Отпуск в Документообороте" in source_titles
    assert "Письменная форма и контроль" in prompt
    assert "Инструкция Отпуск в Документообороте" in prompt
    assert result.answer.startswith("Нет")


def test_control_process_comparison_without_control_source_returns_no_answer() -> None:
    vacation = _chunk_with(
        title="Инструкция Отпуск в Документообороте",
        section="Порядок работы: часть 1",
        text="Чтобы оформить отпуск, сотрудник создает задачу на отпуск в Документообороте.",
        final_score=252.0,
    )
    client = FakeClient("unused")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([vacation]),
        llm_client=client,
        context_limit=3,
    )

    result = generator.answer("Мне нужно взять под контроль процесс, это отпуск?")

    assert result.response_kind == "no_answer"
    assert result.sources == []
    assert result.chunks_used == 0
    assert result.diagnostic_candidates
    assert client.messages == []


def test_company_question_prefers_goals_and_ckp_context() -> None:
    company_goal = _chunk_with(
        title="ИП-0002 Цели и замыслы компании Serviceline",
        section="Основная цель компании",
        text="Основная цель компании Serviceline описывает общий смысл деятельности.",
        final_score=95.0,
    )
    company_ckp = _chunk_with(
        title="ИП-0003 ЦКП SERVICELINE",
        section="Ценный конечный продукт",
        text="ЦКП SERVICELINE описывает ценный конечный продукт компании.",
        final_score=90.0,
    )
    procurement_noise = _chunk_with(
        title="2026-03-03_Оргсхема",
        section="Отделение закупки",
        text="Отделение закупки занимается работой с поставщиками.",
        final_score=120.0,
    )
    client = FakeClient("Компания описана через цели и ЦКП.")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([procurement_noise, company_goal, company_ckp]),
        llm_client=client,
    )

    result = generator.answer("Чем занимается компания?")
    prompt = client.messages[-1]["content"]

    assert result.chunks_used == 2
    assert [source.title for source in result.sources] == [
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
    ]
    assert "ИП-0002 Цели и замыслы компании Serviceline" in prompt
    assert "ИП-0003 ЦКП SERVICELINE" in prompt
    assert "Отделение закупки" not in prompt


def test_document_flow_question_prefers_document_flow_context() -> None:
    document_flow = _chunk_with(
        title="ИП-0006 Документооборот",
        section="Документооборот в компании",
        text="Документооборот описывает работу с документами и согласованиями в 1С ДО.",
        final_score=88.0,
    )
    approval_instruction = _chunk_with(
        title="Инструкция Согласования в Документообороте",
        section="Согласование документа",
        text="Инструкция описывает согласование документов в Документообороте.",
        final_score=82.0,
    )
    role_noise = _chunk_with(
        title="Инструкция - Как начать работу в новой должности",
        section="Должностная папка",
        text="Сотрудник изучает должностную папку и рабочий стол.",
        final_score=110.0,
    )
    client = FakeClient("Документооборот описан в ИП-0006.")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([role_noise, document_flow, approval_instruction]),
        llm_client=client,
    )

    result = generator.answer("как работает документооборот в компании?")
    prompt = client.messages[-1]["content"]

    assert result.chunks_used == 2
    assert [source.title for source in result.sources] == [
        "ИП-0006 Документооборот",
        "Инструкция Согласования в Документообороте",
    ]
    assert "ИП-0006 Документооборот" in prompt
    assert "Должностная папка" not in prompt


def test_off_topic_question_returns_no_answer_without_sources_or_llm() -> None:
    weak_noise = [
        _chunk_with(
            title="Регламент по статистикам",
            section="Падающая статистика",
            text="Руководитель анализирует статистики.",
            final_score=14.0,
        ),
        _chunk_with(
            title="Рабочий стол",
            section="Планирование",
            text="Сотрудник ведет рабочий стол и задачи.",
            final_score=11.0,
        ),
    ]
    client = FakeClient("unused")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever(weak_noise),
        llm_client=client,
    )

    result = generator.answer("сколько маленьких утят, после бега есть хотят?")

    assert result.response_kind == "no_answer"
    assert result.sources == []
    assert result.chunks_used == 0
    assert result.prompt_length == 0
    assert result.diagnostic_candidates
    assert "нет точного ответа" in result.answer
    assert client.messages == []


@pytest.mark.parametrize(
    ("question", "case"),
    [
        (
            "как получить новый ноутбук?",
            "laptop",
        ),
        (
            "Мне нужно запросить новое оборудование для работы, что делать?",
            "equipment",
        ),
        (
            "сколько маленьких утят, после бега есть хотят?",
            "ducklings",
        ),
    ],
)
def test_problematic_random_matches_return_no_answer_without_sources_or_llm(
    question: str,
    case: str,
) -> None:
    chunks_by_case = {
        "laptop": [
            _chunk_with(
                title="Регламент по статистикам",
                section="Падающая статистика",
                text="В случайном OCR-фрагменте встретилось слово ноутбук, но инструкция отсутствует.",
                final_score=92.0,
            ),
            _chunk_with(
                title="Инструкция - Как начать работу в новой должности",
                section="Когда вас игнорируют: часть 2",
                text="При начале работы в новой должности нужно изучить должностную папку.",
                final_score=86.0,
            ),
        ],
        "equipment": [
            _chunk_with(
                title="2026-05-05 Навигатор команды_ServiceLine",
                section="Порядок работы: часть 14",
                text="Общий навигатор команды описывает разные рабочие ссылки.",
                final_score=184.0,
            ),
            _chunk_with(
                title="Рабочий стол и работа с отчетами",
                section="Виджеты",
                text="Виджеты показывают актуальное количество задач и отчетов.",
                final_score=161.0,
            ),
        ],
        "ducklings": [
            _chunk_with(
                title="Регламент по статистикам",
                section="Падающая статистика",
                text="Падающая статистика показывает проблему в потоке производства.",
                final_score=99.0,
            ),
            _chunk_with(
                title="Регламент по планированию на неделю",
                section="Разделение времени между должностями",
                text="Планирование описывает распределение рабочего времени.",
                final_score=81.0,
            ),
        ],
    }
    chunks = chunks_by_case[case]
    client = FakeClient("unused")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever(chunks),
        llm_client=client,
    )

    result = generator.answer(question)

    assert result.response_kind == "no_answer"
    assert result.sources == []
    assert result.chunks_used == 0
    assert result.prompt_length == 0
    assert len(result.diagnostic_candidates) == len(chunks)
    assert result.answer == (
        "В базе знаний Serviceline нет точного ответа на этот вопрос. "
        "Похоже, вопрос не относится к корпоративным регламентам, инструкциям, "
        "оргструктуре или документообороту."
    )
    assert client.messages == []


def test_kp_question_asks_clarification_without_llm() -> None:
    client = FakeClient("unused")
    retriever = FakeRetriever([_chunk()])
    generator = RagAnswerGenerator(retriever=retriever, llm_client=client)

    result = generator.answer("Что такое КП?")

    assert result.response_kind == "clarification"
    assert result.sources == []
    assert result.chunks_used == 0
    assert "коммерческое предложение" in result.answer
    assert "ЦКП" in result.answer
    assert client.messages == []
    assert retriever.calls == []


def test_kp_commercial_offer_does_not_repeat_clarification_or_use_ckp() -> None:
    ckp = _chunk_with(
        title="ИП-0003 ЦКП SERVICELINE",
        section="Ценный конечный продукт",
        text="ЦКП SERVICELINE описывает ценный конечный продукт компании.",
        final_score=200.0,
    )
    commercial_mention = _chunk_with(
        title="2026-03-03_Оргсхема",
        section="Отдел продаж",
        text="Коммерческое предложение может упоминаться как продукт отдела продаж.",
        final_score=80.0,
    )
    client = FakeClient("unused")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([ckp, commercial_mention]),
        llm_client=client,
    )

    result = generator.answer("КП как коммерческое предложение")

    assert result.response_kind == "partial_answer"
    assert result.sources == []
    assert result.chunks_used == 0
    assert result.prompt_length == 0
    assert "нет отдельной полной инструкции" in result.answer
    assert "ИП-0003 ЦКП SERVICELINE" not in [
        source.title for source in result.diagnostic_candidates
    ]
    assert client.messages == []


@pytest.mark.parametrize(
    "question",
    [
        "ЦКП как ценный конечный продукт компании",
        "Что такое ЦКП?",
    ],
)
def test_ckp_questions_use_ckp_context(question: str) -> None:
    ckp = _chunk_with(
        title="ИП-0003 ЦКП SERVICELINE",
        section="Ценный конечный продукт",
        text="ЦКП SERVICELINE описывает ценный конечный продукт компании.",
        final_score=200.0,
    )
    commercial_noise = _chunk_with(
        title="Коммерческое предложение",
        section="Отдел продаж",
        text="КП как коммерческое предложение упоминается в продажах.",
        final_score=250.0,
    )
    client = FakeClient("ЦКП компании описан в ИП-0003.")
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([commercial_noise, ckp]),
        llm_client=client,
    )

    result = generator.answer(question)

    assert result.response_kind == "answer"
    assert result.chunks_used == 1
    assert [source.title for source in result.sources] == ["ИП-0003 ЦКП SERVICELINE"]
    assert "ИП-0003 ЦКП SERVICELINE" in client.messages[-1]["content"]


def test_answer_generator_strips_trailing_llm_sources_block() -> None:
    client = FakeClient(
        "Краткий ответ по найденному фрагменту.\n\n"
        "Источники:\n"
        "[1] Тестовый документ, Тестовый раздел"
    )
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([_chunk()]),
        llm_client=client,
    )

    result = generator.answer("Что такое тест?")

    assert result.answer == "Краткий ответ по найденному фрагменту."
    assert result.sources
    assert result.sources[0].title == "Тестовый документ"


def test_llm_error_is_wrapped() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([_chunk()]),
        llm_client=ErrorClient(),
    )

    with pytest.raises(RagAnswerError, match="empty"):
        generator.answer("Что такое тест?")


def test_empty_llm_answer_is_error() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([_chunk()]),
        llm_client=FakeClient("   "),
    )

    with pytest.raises(RagAnswerError, match="empty answer"):
        generator.answer("Что такое тест?")


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


class FakeQueryAnalyzer:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan
        self.calls: list[str] = []

    def analyze(self, question: str) -> QueryPlan:
        self.calls.append(question)
        return self.plan


class ErrorQueryAnalyzer:
    def analyze(self, question: str) -> QueryPlan:
        raise RuntimeError("query analyzer failed")


class FallbackQueryAnalyzer:
    last_error = "broken analyzer json"

    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan

    def analyze(self, question: str) -> QueryPlan:
        return self.plan


def _org_structure_plan() -> QueryPlan:
    return QueryPlan(
        intent="org_structure",
        normalized_question="Какие подразделения есть в организационной структуре компании?",
        query_expansions=[
            "оргсхема компании",
            "организационная структура компании",
        ],
        preferred_sources=["2026-03-03_Оргсхема _ Компании"],
        answer_type="list",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.9,
        notes="test plan",
    )


def _company_identity_plan() -> QueryPlan:
    return QueryPlan(
        intent="company_identity",
        normalized_question="Чем занимается компания Serviceline?",
        query_expansions=[
            "цели компании",
            "замыслы компании",
            "ЦКП Serviceline",
        ],
        preferred_sources=[
            "ИП-0002 Цели и замыслы компании Serviceline",
            "ИП-0003 ЦКП SERVICELINE",
        ],
        answer_type="definition",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.85,
        notes="test plan",
    )


def _one_c_operational_lookup_plan() -> QueryPlan:
    return QueryPlan(
        intent="one_c_operational_lookup",
        normalized_question="Какой статус заказа в 1С?",
        query_expansions=[
            "операционный запрос 1С",
            "статус заказа",
        ],
        preferred_sources=[],
        answer_type="partial_answer",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.8,
        notes="test plan",
    )


def _roles_responsibility_plan() -> QueryPlan:
    return QueryPlan(
        intent="roles_responsibility",
        normalized_question="Кто отвечает за закупки?",
        query_expansions=["руководитель закупок"],
        preferred_sources=[],
        answer_type="definition",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.9,
        notes="test plan",
    )


def _chunk() -> RetrievedChunk:
    return _chunk_with(
        title="Тестовый документ",
        section="Тестовый раздел",
        text="Тестовый текст для prompt builder.",
        final_score=50.0,
    )


def _organization_chunk(
    *,
    title: str,
    doc_type: str,
    text: str,
    final_score: float,
    metadata: dict[str, object],
    source: str = "data/raw_docs/bvr_company_structure_instruction_v2 (2).txt",
    section: str = "Раздел оргструктуры",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=abs(hash((title, doc_type))) % 100000,
        title=title,
        source=source,
        section=section,
        page=None,
        text=text,
        score=1.0,
        metadata={**metadata, "doc_type": doc_type},
        doc_type=doc_type,
        base_score=1.0,
        rerank_score=2.0,
        final_score=final_score,
        matched_terms=[],
        matched_excerpt=text,
        selection_reasons=["test"],
    )


def _chunk_with(
    *,
    title: str,
    section: str,
    text: str,
    final_score: float,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1,
        title=title,
        source="data/raw_docs/test.pdf",
        section=section,
        page=3,
        text=text,
        score=1.0,
        metadata={
            "logical_unit_title": "Тестовый смысловой блок",
            "logical_unit_type": "policy_rule",
            "doc_type": "test_doc",
        },
        doc_type="test_doc",
        base_score=1.0,
        rerank_score=2.0,
        final_score=final_score,
        matched_terms=["тест"],
        matched_excerpt="релевантный фрагмент",
        selection_reasons=["test"],
    )
