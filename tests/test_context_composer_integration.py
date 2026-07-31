from __future__ import annotations

from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import QueryPlan
from linehelper.rag.retriever import RetrievedChunk


def test_t02_four_document_flow_rules_reach_real_orchestration_context() -> None:
    rules = [
        _chunk(
            index=index,
            source="data/raw_docs/ИП-0006 Документооборот.pdf",
            title="ИП-0006 Документооборот",
            section=f"Правило {index}",
            logical_unit_title=f"Правило {index}",
            logical_unit_type="policy_rule",
            text=f"{index}. Правило документооборота.",
            score=320.0,
            stage="sibling_lookup",
        )
        for index in range(1, 5)
    ]
    generator = _generator(
        plan=_plan(
            "Какие правила документооборота действуют?",
            requested_fact_type="list",
            intent="document_flow",
            subject="правила документооборота",
        ),
        chunks=rules,
    )

    result = generator.answer("Какие правила документооборота действуют?")

    assert result.chunks_used == 4
    assert [source.section for source in result.sources] == [
        "Правило 1",
        "Правило 2",
        "Правило 3",
        "Правило 4",
    ]
    assert result.context is not None
    assert result.context["coverage_satisfied"] == {
        "distinct_sibling_logical_units": True
    }


def test_t03_lower_rank_equipment_procedure_reaches_real_orchestration_context() -> None:
    chunks = [
        _chunk(
            index=44,
            source="data/raw_docs/Инструкция Согласования договоров.pdf",
            title="Инструкция Согласования договоров",
            section="часть 1",
            logical_unit_title="Согласование договоров",
            logical_unit_type="procedure",
            text="Общая процедура согласования договора.",
            score=316.6,
            stage="procedure_lookup",
        ),
        _chunk(
            index=45,
            source="data/raw_docs/Инструкция Согласования договоров.pdf",
            title="Инструкция Согласования договоров",
            section="часть 2",
            logical_unit_title="Согласование договоров",
            logical_unit_type="procedure",
            text="Продолжение процедуры согласования договора.",
            score=316.6,
            stage="procedure_lookup",
        ),
        _chunk(
            index=29,
            source=(
                "data/raw_docs/2026-19-06 Инструкция Заявка в 1 отд-е "
                "(построение) хоз.часть.pdf"
            ),
            title=(
                "2026-19-06 Инструкция Заявка в 1 отд-е "
                "(построение) хоз.часть"
            ),
            section="Порядок работы: часть 1",
            logical_unit_title="Заявка в 1 отделение хоз.часть",
            logical_unit_type="procedure",
            text="Создайте заявку в первое отделение и заполните обязательные поля.",
            score=315.0,
            stage="procedure_lookup",
        ),
    ]
    generator = _generator(
        plan=_plan(
            "Как получить новое оборудование?",
            requested_fact_type="procedure",
            intent="equipment_it_request",
            subject="получение нового оборудования",
        ),
        chunks=chunks,
    )

    result = generator.answer("Как получить новое оборудование?")

    assert result.response_kind == "answer"
    assert any("хоз.часть" in source.title for source in result.sources)
    assert result.context is not None
    assert result.context["coverage_satisfied"]["primary_procedure"] is True


def test_t05a_t06_t08_context_regression_in_real_orchestration() -> None:
    cases = [
        (
            "Как оформить отпуск и за сколько дней подать заявление?",
            _plan(
                "Как оформить отпуск и за сколько дней подать заявление?",
                requested_fact_type="procedure",
                intent="vacation",
                subject="отпуск",
            ),
            [
                _chunk(
                    index=42,
                    source="data/raw_docs/Инструкция Отпуск.pdf",
                    title="Инструкция Отпуск в Документообороте",
                    section="часть 1",
                    logical_unit_title="Оформление отпуска",
                    logical_unit_type="procedure",
                    text="Создайте заявление на отпуск.",
                    score=363.2,
                    stage="procedure_lookup",
                ),
                _chunk(
                    index=43,
                    source="data/raw_docs/Инструкция Отпуск.pdf",
                    title="Инструкция Отпуск в Документообороте",
                    section="часть 2",
                    logical_unit_title="Оформление отпуска",
                    logical_unit_type="procedure",
                    text="Продолжение инструкции на отпуск.",
                    score=350.0,
                    stage="procedure_lookup",
                ),
                _chunk(
                    index=27,
                    source="data/raw_docs/Навигатор команды.pdf",
                    title="2026-05-05 Навигатор команды_ServiceLine",
                    section="Правило срока",
                    logical_unit_title="Правило подачи заявления",
                    logical_unit_type="procedure",
                    text="Заявление подают не позднее чем за 14 календарных дней.",
                    score=320.0,
                    stage="procedure_lookup",
                ),
            ],
            {42, 27},
        ),
        (
            "Кто отвечает за отгрузку клиенту?",
            _plan(
                "Кто отвечает за отгрузку клиенту?",
                requested_fact_type="responsible_person",
                intent="roles_responsibility",
                subject="отгрузка клиенту",
            ),
            [
                _chunk(
                    index=594,
                    source="data/raw_docs/bvr_company_structure_instruction_v2 (2).txt",
                    title="Доставка клиенту",
                    section="Логистика",
                    logical_unit_title="Доставка клиенту",
                    logical_unit_type="responsibility_route",
                    text="За доставку клиенту отвечает Симонов.",
                    score=410.0,
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
                intent="roles_responsibility",
                subject="внутренний документооборот",
            ),
            [
                _chunk(
                    index=index,
                    source="data/raw_docs/bvr_company_structure_instruction_v2 (2).txt",
                    title=person,
                    section="Оргструктура",
                    logical_unit_title=f"Внутренний документооборот: {person}",
                    logical_unit_type="organization_unit",
                    text=f"{person} отвечает за внутренний документооборот.",
                    score=score,
                    record_key=f"organization_unit:{index}",
                )
                for index, person, score in (
                    (437, "Шкиренков Роман", 405.0),
                    (439, "Малахова Мария", 404.0),
                )
            ],
            {437, 439},
        ),
        (
            "Что делать после устного распоряжения?",
            _plan(
                "Что делать после устного распоряжения?",
                requested_fact_type="procedure",
                intent="order_disposition",
                subject="распоряжение компании",
            ),
            [
                _chunk(
                    index=70,
                    source="data/raw_docs/ИП-0005 Распоряжения.pdf",
                    title="ИП-0005 Распоряжения",
                    section="Пример распоряжения",
                    logical_unit_title="Пример распоряжения",
                    logical_unit_type="example",
                    text="Пример распоряжения и отметки об исполнении.",
                    score=355.0,
                ),
                _chunk(
                    index=69,
                    source="data/raw_docs/ИП-0005 Распоряжения.pdf",
                    title="ИП-0005 Распоряжения",
                    section="Отчётность",
                    logical_unit_title="Доказательства исполнения",
                    logical_unit_type="procedure",
                    text="После исполнения приложите доказательства.",
                    score=346.0,
                    stage="procedure_lookup",
                ),
                _chunk(
                    index=67,
                    source="data/raw_docs/ИП-0005 Распоряжения.pdf",
                    title="ИП-0005 Распоряжения",
                    section="Письменная форма",
                    logical_unit_title="Письменная форма распоряжения",
                    logical_unit_type="policy_rule",
                    text="Устное распоряжение оформляют письменно.",
                    score=344.0,
                ),
            ],
            {67},
        ),
    ]

    for question, plan, chunks, required_ids in cases:
        result = _generator(plan=plan, chunks=chunks).answer(question)
        selected_ids = {
            item["chunk_id"]
            for item in (result.context or {}).get("selected_context", [])
        }
        assert required_ids <= selected_ids
        assert result.response_kind == "answer"


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

    def chat(self, messages: list[dict[str, str]]) -> str:
        return "Детерминированный тестовый ответ."


def _generator(*, plan: QueryPlan, chunks: list[RetrievedChunk]) -> RagAnswerGenerator:
    return RagAnswerGenerator(
        retriever=_FakeRetriever(chunks),
        llm_client=_FakeClient(),
        query_analyzer=_FakeAnalyzer(plan),
        context_limit=3,
        context_score_ratio=0.65,
    )


def _plan(
    question: str,
    *,
    requested_fact_type: str,
    intent: str,
    subject: str,
) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        normalized_question=question,
        query_expansions=[],
        preferred_sources=[],
        answer_type=(
            "procedure" if requested_fact_type == "procedure" else "definition"
        ),
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="context composer integration",
        requested_fact_type=requested_fact_type,
        temporal_scope="static",
        subject=subject,
    )


def _chunk(
    *,
    index: int,
    source: str,
    title: str,
    section: str,
    logical_unit_title: str,
    logical_unit_type: str,
    text: str,
    score: float,
    stage: str | None = None,
    record_key: str | None = None,
) -> RetrievedChunk:
    metadata = {
        "logical_unit_title": logical_unit_title,
        "logical_unit_type": logical_unit_type,
        "doc_type": "test",
    }
    if record_key:
        metadata["record_key"] = record_key
    reasons = ["test"]
    if stage:
        reasons.append(f"retrieval stage {stage}")
    return RetrievedChunk(
        chunk_id=index,
        title=title,
        source=source,
        section=section,
        page=None,
        text=text,
        score=score,
        metadata=metadata,
        doc_type="test",
        base_score=score,
        rerank_score=score,
        final_score=score,
        matched_terms=["test"],
        matched_excerpt=text,
        selection_reasons=reasons,
    )
