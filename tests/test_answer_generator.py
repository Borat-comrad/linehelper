from __future__ import annotations

import pytest

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator
from linehelper.llm.ollama_client import OllamaEmptyResponseError
from linehelper.rag.retriever import RetrievedChunk


def test_empty_question_is_rejected() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever([]),
        llm_client=FakeClient("ok"),
    )

    with pytest.raises(ValueError):
        generator.answer("   ")


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


def _chunk() -> RetrievedChunk:
    return _chunk_with(
        title="Тестовый документ",
        section="Тестовый раздел",
        text="Тестовый текст для prompt builder.",
        final_score=50.0,
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
