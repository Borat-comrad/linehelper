from __future__ import annotations

from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import QueryPlan
from linehelper.rag.retriever import RetrievedChunk


def test_t04_orchestration_returns_partial_answer_with_known_procedure() -> None:
    question = "Кому подавать заявление на командировку?"
    chunks = [
        _chunk(
            47,
            title="Инструкция Согласования командировки",
            source="data/raw_docs/Инструкция Согласования командировки.pdf",
            section="Согласование командировки",
            text="Создайте СЗ_Командировка и запустите согласование.",
            logical_unit_type="procedure",
            score=369.0,
        ),
        _chunk(
            19,
            title="Навигатор команды",
            source="data/raw_docs/Навигатор команды.pdf",
            section="Общие правила",
            text="В одном из пунктов упоминается командировка.",
            logical_unit_type="procedure",
            score=240.0,
        ),
    ]
    client = _FakeClient()
    generator = _generator(
        plan=_plan(
            question,
            requested_fact_type="document_recipient",
            subject="командировка",
            intent="business_trip",
        ),
        chunks=chunks,
        client=client,
    )

    result = generator.answer(question)

    assert result.response_kind == "partial_answer"
    assert result.evidence is not None
    assert result.evidence["answer_mode"] == "partial_answer"
    assert result.evidence["supporting_chunk_ids"] == [47]
    assert result.evidence["non_supporting_chunk_ids"] == [19]
    assert result.evidence["unsupported_requirements"] == [
        "named_document_recipient"
    ]
    assert [source.title for source in result.sources] == [
        "Инструкция Согласования командировки"
    ]
    assert result.answer_contract is not None
    assert result.answer_contract["answer_mode"] == "partial_answer"
    assert result.answer_contract["allowed_chunk_ids"] == [47]
    assert result.answer_contract["unsupported_requirement_ids"] == [
        "named_document_recipient"
    ]
    assert result.final_answer_sections == [
        "supported_answer",
        "missing_information",
        "sources",
    ]
    assert "Нет данных по следующим пунктам:" in result.answer
    assert "именованный первоначальный адресат" in result.answer
    prompt = client.messages[-1]["content"]
    assert "именованный первоначальный адресат" not in prompt
    assert "не заполняй пробелы" in prompt


def test_t03_orchestration_uses_required_chunk_and_excludes_noise() -> None:
    question = "Как получить новое оборудование?"
    chunks = [
        _chunk(
            44,
            title="Инструкция Согласования договоров",
            source="data/raw_docs/Инструкция Согласования договоров.pdf",
            section="Договоры",
            text="Порядок согласования договора.",
            logical_unit_type="procedure",
            score=316.6,
        ),
        _chunk(
            29,
            title="Инструкция Заявка в 1 отделение хоз.часть",
            source="data/raw_docs/Инструкция Заявка в хоз.часть.pdf",
            section="Заявка в хоз.часть",
            text="Создайте заявку в хоз.часть и заполните обязательные поля.",
            logical_unit_type="procedure",
            score=315.0,
        ),
        _chunk(
            39,
            title="Заявка для IT",
            source="data/raw_docs/Заявка для IT.pdf",
            section="Заявка для IT",
            text="Порядок создания заявки для IT.",
            logical_unit_type="procedure",
            score=314.0,
        ),
    ]
    client = _FakeClient()
    generator = _generator(
        plan=_plan(
            question,
            requested_fact_type="procedure",
            subject="получение нового оборудования",
            intent="equipment_it_request",
        ),
        chunks=chunks,
        client=client,
    )

    result = generator.answer(question)

    assert result.response_kind == "answer"
    assert result.evidence is not None
    assert result.evidence["supporting_chunk_ids"] == [29]
    assert result.evidence["non_supporting_chunk_ids"] == [44, 39]
    assert [source.title for source in result.sources] == [
        "Инструкция Заявка в 1 отделение хоз.часть"
    ]
    assert result.answer_contract is not None
    assert result.answer_contract["allowed_chunk_ids"] == [29]
    assert [
        source["chunk_id"]
        for source in result.answer_contract["source_entries"]
    ] == [29]
    prompt = client.messages[-1]["content"]
    assert "заявку в хоз.часть" in prompt
    assert "Порядок согласования договора" not in prompt
    assert "Порядок создания заявки для IT" not in prompt


def test_pr01_metadata_matched_procedure_is_not_generic_no_answer() -> None:
    question = "Как оформить командировку?"
    chunks = [
        _chunk(
            47,
            title="Инструкция Согласования командировки",
            source="data/raw_docs/Инструкция командировки.pdf",
            section="Согласование командировки",
            text="Создайте СЗ_Командировка и заполните обязательные поля.",
            logical_unit_type="procedure",
            score=370.0,
        ),
        _chunk(
            24,
            title="Навигатор команды",
            source="data/raw_docs/Навигатор команды.pdf",
            section="Общие правила",
            text="Общие правила работы сотрудника.",
            logical_unit_type="procedure",
            score=250.0,
        ),
    ]
    result = _generator(
        plan=_plan(
            question,
            requested_fact_type="procedure",
            subject="формирование командировки",
            intent="business_trip",
        ),
        chunks=chunks,
        client=_FakeClient(),
    ).answer(question)

    assert result.response_kind == "answer"
    assert result.evidence is not None
    assert result.evidence["answer_mode"] == "full_answer"
    assert result.evidence["supporting_chunk_ids"] == [47]
    assert result.evidence["non_supporting_chunk_ids"] == [24]
    assert [source.title for source in result.sources] == [
        "Инструкция Согласования командировки"
    ]


def test_mr08_negative_subject_rejects_other_document_sources() -> None:
    question = "Как оформить документ, которого нет в базе?"
    chunks = [
        _chunk(
            48,
            title="Инструкция Согласования приказов в Документообороте",
            source="data/raw_docs/Инструкция приказов.pdf",
            section="Согласование приказов",
            text="Создайте приказ и запустите согласование.",
            logical_unit_type="procedure",
            score=365.0,
        ),
        _chunk(
            45,
            title="Инструкция Согласования договоров в Документообороте",
            source="data/raw_docs/Инструкция договоров.pdf",
            section="Согласование договоров",
            text="Создайте договор и запустите согласование.",
            logical_unit_type="procedure",
            score=360.0,
        ),
    ]
    result = _generator(
        plan=_plan(
            question,
            requested_fact_type="procedure",
            subject="документооборот",
            intent="unknown",
        ),
        chunks=chunks,
        client=_FakeClient(),
    ).answer(question)

    assert result.response_kind == "no_answer"
    assert result.evidence is not None
    assert result.evidence["answer_mode"] == "insufficient_evidence"
    assert result.evidence["supporting_chunk_ids"] == []
    assert result.evidence["non_supporting_chunk_ids"] == [48, 45]
    assert result.sources == []
    assert result.answer_contract is not None
    assert result.answer_contract["allowed_chunk_ids"] == []
    assert result.answer_contract["source_entries"] == []


def test_t02_t05a_t06_t08_or01_evidence_regression() -> None:
    cases = [
        (
            "Какие правила документооборота действуют?",
            _plan(
                "Какие правила документооборота действуют?",
                requested_fact_type="list",
                subject="правила документооборота",
                intent="document_flow",
            ),
            [
                _chunk(
                    index,
                    title="ИП-0006 Документооборот",
                    source="data/raw_docs/ИП-0006 Документооборот.pdf",
                    section=f"Правило {index}",
                    text=f"{index}. Правило документооборота.",
                    logical_unit_type="policy_rule",
                    score=350.0 - index,
                    sibling=True,
                )
                for index in (71, 72, 73, 74)
            ],
            {71, 72, 73, 74},
        ),
        (
            "Как оформить отпуск и за сколько дней подать заявление?",
            _plan(
                "Как оформить отпуск и за сколько дней подать заявление?",
                requested_fact_type="procedure",
                subject="отпуск компании",
                intent="vacation",
            ),
            [
                _chunk(
                    42,
                    title="Инструкция Отпуск",
                    source="data/raw_docs/Инструкция Отпуск.pdf",
                    section="Оформление отпуска",
                    text="Создайте заявление на отпуск.",
                    logical_unit_type="procedure",
                    score=363.0,
                ),
                _chunk(
                    43,
                    title="Инструкция Отпуск",
                    source="data/raw_docs/Инструкция Отпуск.pdf",
                    section="Обработка отпуска",
                    text="Запустите обработку заявления на отпуск.",
                    logical_unit_type="procedure",
                    score=350.0,
                ),
                _chunk(
                    27,
                    title="Навигатор команды",
                    source="data/raw_docs/Навигатор команды.pdf",
                    section="Срок отпуска",
                    text=(
                        "Заявление на отпуск подают не позднее чем "
                        "за 14 календарных дней."
                    ),
                    logical_unit_type="procedure",
                    score=320.0,
                ),
            ],
            {42, 27},
        ),
        (
            "Кто отвечает за отгрузку клиенту?",
            _plan(
                "Кто отвечает за отгрузку клиенту?",
                requested_fact_type="responsible_person",
                subject="отгрузка клиенту",
                intent="roles_responsibility",
            ),
            [
                _chunk(
                    594,
                    title="Доставка клиенту",
                    source="data/raw_docs/bvr_company_structure.txt",
                    section="Логистика",
                    text="За доставку клиенту отвечает Симонова.",
                    score=410.0,
                    entity_type="responsibility_route",
                    record_key="responsibility_route:delivery",
                )
            ],
            {594},
        ),
        (
            "Кто отвечает за внутренний документооборот?",
            _plan(
                "Кто отвечает за внутренний документооборот?",
                requested_fact_type="responsible_person",
                subject="внутренний документооборот компании",
                intent="roles_responsibility",
            ),
            [
                _chunk(
                    index,
                    title=person,
                    source="data/raw_docs/bvr_company_structure.txt",
                    section="Оргструктура",
                    text=(
                        f"{person} отвечает за внутренний документооборот."
                    ),
                    score=405.0 - index / 1000,
                    entity_type="organization_unit",
                    record_key=f"organization_unit:{index}",
                )
                for index, person in (
                    (437, "Шкиренков Роман"),
                    (439, "Малахова Мария"),
                )
            ],
            {437, 439},
        ),
        (
            "Что делать после устного распоряжения?",
            _plan(
                "Что делать после устного распоряжения?",
                requested_fact_type="procedure",
                subject="распоряжение компании",
                intent="order_disposition",
            ),
            [
                _chunk(
                    index,
                    title="ИП-0005 Распоряжения",
                    source="data/raw_docs/ИП-0005 Распоряжения.pdf",
                    section=section,
                    text=text,
                    logical_unit_type=logical_type,
                    score=360.0 - index / 100,
                )
                for index, section, logical_type, text in (
                    (
                        70,
                        "Пример распоряжения",
                        "example",
                        "Пример письменного распоряжения.",
                    ),
                    (
                        69,
                        "Исполнение распоряжения",
                        "procedure",
                        "После исполнения приложите доказательства.",
                    ),
                    (
                        67,
                        "Письменная форма",
                        "policy_rule",
                        "Устное распоряжение оформляют письменно.",
                    ),
                )
            ],
            {67},
        ),
    ]

    for question, plan, chunks, required_ids in cases:
        result = _generator(
            plan=plan,
            chunks=chunks,
            client=_FakeClient(),
        ).answer(question)
        assert result.response_kind == "answer"
        assert result.evidence is not None
        assert result.evidence["answer_mode"] == "full_answer"
        assert required_ids <= set(result.evidence["supporting_chunk_ids"])
        assert result.answer_contract is not None
        assert result.answer_contract["answer_mode"] == "full_answer"
        assert required_ids <= set(
            result.answer_contract["allowed_chunk_ids"]
        )
        assert "Нет данных по следующим пунктам:" not in result.answer

    compound_question = "Кто отвечает за рабочие места и оборудование?"
    compound_result = _generator(
        plan=_plan(
            compound_question,
            requested_fact_type="responsible_person",
            subject="отдел рабочих мест и оборудования",
            intent="roles_responsibility",
        ),
        chunks=[
            _chunk(
                577,
                title="Ответственный за рабочие места",
                source="data/raw_docs/organization.txt",
                section="Справочник сотрудников",
                text=(
                    "Сотрудник отвечает за системное администрирование "
                    "и рабочие места."
                ),
                score=400.0,
                entity_type="employee",
                record_key="employee:workplaces",
                doc_type="employee_role",
            )
        ],
        client=_FakeClient(),
    ).answer(compound_question)
    assert compound_result.response_kind == "partial_answer"
    assert compound_result.evidence is not None
    assert compound_result.evidence["supporting_chunk_ids"] == [577]
    assert compound_result.evidence["supported_requirements"] == [
        "responsible_identity"
    ]
    assert compound_result.evidence["unsupported_requirements"] == [
        "complete_responsibility_scope"
    ]
    assert [source.title for source in compound_result.sources] == [
        "Ответственный за рабочие места"
    ]


class _FakeAnalyzer:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan

    def analyze(self, question: str) -> QueryPlan:
        return self.plan


class _FakeRetriever:
    supports_retrieval_plan = False

    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks

    def retrieve(self, question: str, *, limit: int, candidate_limit: int):
        return list(self.chunks)


class _FakeClient:
    model = "fake-model"

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return "Детерминированный ответ только по evidence."


def _generator(
    *,
    plan: QueryPlan,
    chunks: list[RetrievedChunk],
    client: _FakeClient,
) -> RagAnswerGenerator:
    return RagAnswerGenerator(
        retriever=_FakeRetriever(chunks),
        llm_client=client,
        query_analyzer=_FakeAnalyzer(plan),
        context_limit=3,
        context_score_ratio=0.65,
    )


def _plan(
    question: str,
    *,
    requested_fact_type: str,
    subject: str,
    intent: str,
) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        normalized_question=question,
        query_expansions=[],
        preferred_sources=[],
        answer_type="general",
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="evidence integration",
        requested_fact_type=requested_fact_type,
        temporal_scope="static",
        subject=subject,
    )


def _chunk(
    chunk_id: int,
    *,
    title: str,
    source: str,
    section: str,
    text: str,
    score: float,
    logical_unit_type: str | None = None,
    sibling: bool = False,
    entity_type: str | None = None,
    record_key: str | None = None,
    doc_type: str = "test",
) -> RetrievedChunk:
    metadata: dict[str, object] = {
        "logical_unit_title": section,
        "doc_type": doc_type,
    }
    if logical_unit_type:
        metadata["logical_unit_type"] = logical_unit_type
    if entity_type:
        metadata["entity_type"] = entity_type
    if record_key:
        metadata["record_key"] = record_key
    reasons = ["test"]
    if sibling:
        reasons.append("retrieval stage sibling_lookup")
    return RetrievedChunk(
        chunk_id=chunk_id,
        title=title,
        source=source,
        section=section,
        page=1,
        text=text,
        score=score,
        metadata=metadata,
        doc_type=doc_type,
        base_score=score,
        rerank_score=score,
        final_score=score,
        matched_terms=[],
        matched_excerpt=text,
        selection_reasons=reasons,
    )
