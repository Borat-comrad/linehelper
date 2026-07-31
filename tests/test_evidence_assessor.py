from __future__ import annotations

from linehelper.rag.context_composer import ContextPlanner
from linehelper.rag.evidence_assessor import (
    EvidenceAssessor,
    EvidencePlanner,
)
from linehelper.rag.query_analyzer import QueryPlan
from linehelper.rag.retriever import RetrievedChunk


def test_full_evidence_decision() -> None:
    question = "Как оформить отпуск?"
    plan = _evidence_plan(
        question,
        requested_fact_type="procedure",
        subject="отпуск компании",
    )
    decision = EvidenceAssessor().assess(
        question,
        [
            _chunk(
                42,
                title="Инструкция Отпуск в Документообороте",
                text="Создайте заявление на отпуск.",
                logical_unit_type="procedure",
            )
        ],
        plan=plan,
    )

    assert decision.mode == "full_answer"
    assert decision.supported_requirements == ("primary_procedure",)
    assert [chunk.chunk_id for chunk in decision.supporting_chunks] == [42]


def test_partial_evidence_decision() -> None:
    question = "Кому подавать заявление на командировку?"
    plan = _evidence_plan(
        question,
        requested_fact_type="document_recipient",
        subject="командировка",
    )
    decision = EvidenceAssessor().assess(
        question,
        [
            _chunk(
                47,
                title="Инструкция Согласования командировки",
                text="Создайте СЗ_Командировка и запустите согласование.",
                logical_unit_type="procedure",
            )
        ],
        plan=plan,
    )

    assert decision.mode == "partial_answer"
    assert decision.supported_requirements == (
        "related_submission_procedure",
    )
    assert decision.unsupported_requirements == (
        "named_document_recipient",
    )


def test_insufficient_evidence_decision() -> None:
    question = "Кому подавать заявление на командировку?"
    plan = _evidence_plan(
        question,
        requested_fact_type="document_recipient",
        subject="командировка",
    )
    decision = EvidenceAssessor().assess(question, [], plan=plan)

    assert decision.mode == "insufficient_evidence"
    assert decision.supporting_chunks == ()
    assert set(decision.unsupported_requirements) == {
        "named_document_recipient",
        "related_submission_procedure",
    }


def test_supporting_and_non_supporting_procedural_chunks() -> None:
    question = "Как получить новое оборудование?"
    plan = _evidence_plan(
        question,
        requested_fact_type="procedure",
        subject="получение нового оборудования",
    )
    chunks = [
        _chunk(
            44,
            title="Инструкция Согласования договоров",
            text="Порядок согласования договора.",
            logical_unit_type="procedure",
        ),
        _chunk(
            29,
            title="Инструкция Заявка в 1 отделение хоз.часть",
            text="Создайте заявку в хоз.часть.",
            logical_unit_type="procedure",
        ),
        _chunk(
            39,
            title="Заявка для IT",
            text="Порядок создания заявки для IT.",
            logical_unit_type="procedure",
        ),
    ]

    decision = EvidenceAssessor().assess(question, chunks, plan=plan)

    assert [chunk.chunk_id for chunk in decision.supporting_chunks] == [29]
    assert [
        chunk.chunk_id for chunk in decision.non_supporting_chunks
    ] == [44, 39]


def test_evidence_order_follows_selected_context_order() -> None:
    question = "Какие правила документооборота действуют?"
    plan = _evidence_plan(
        question,
        requested_fact_type="list",
        subject="правила документооборота",
    )
    chunks = [
        _chunk(
            index,
            title="ИП-0006 Документооборот",
            text=f"Правило {index}.",
            logical_unit_type="policy_rule",
            section=f"Правило {index}",
            sibling=True,
        )
        for index in (72, 71, 73, 74)
    ]

    decision = EvidenceAssessor().assess(question, chunks, plan=plan)

    assert [chunk.chunk_id for chunk in decision.supporting_chunks] == [
        72,
        71,
        73,
        74,
    ]


def test_duplicate_evidence_does_not_create_requirement_coverage() -> None:
    question = "Как оформить отпуск?"
    plan = _evidence_plan(
        question,
        requested_fact_type="procedure",
        subject="отпуск",
    )
    chunks = [
        _chunk(
            index,
            title="Инструкция Отпуск",
            text="Оформите заявление на отпуск.",
            logical_unit_type="procedure",
            section=f"Часть {index}",
        )
        for index in (42, 43)
    ]

    decision = EvidenceAssessor().assess(question, chunks, plan=plan)

    assert decision.supported_requirements == ("primary_procedure",)
    assert decision.coverage_rate == 1.0
    assert len(decision.supporting_chunks) == 2


def test_structured_responsibility_requires_subject_match() -> None:
    question = "Кто отвечает за отгрузку клиенту?"
    plan = _evidence_plan(
        question,
        requested_fact_type="responsible_person",
        subject="отгрузка клиенту",
    )
    chunks = [
        _chunk(
            594,
            title="Доставка клиенту",
            text="За доставку клиенту отвечает Симонова.",
            entity_type="responsibility_route",
            record_key="responsibility_route:delivery",
        ),
        _chunk(
            592,
            title="Таможня и подготовка",
            text="За таможню отвечает другой сотрудник.",
            entity_type="responsibility_route",
            record_key="responsibility_route:customs",
        ),
        _chunk(
            73,
            title="ИП-0006 Документооборот",
            text="Общее правило внутреннего взаимодействия.",
            logical_unit_type="policy_rule",
        ),
    ]

    decision = EvidenceAssessor().assess(question, chunks, plan=plan)

    assert decision.mode == "full_answer"
    assert [chunk.chunk_id for chunk in decision.supporting_chunks] == [594]
    assert [
        chunk.chunk_id for chunk in decision.non_supporting_chunks
    ] == [592, 73]


def test_procedure_subject_match_uses_structured_metadata() -> None:
    question = "Как оформить командировку?"
    plan = _evidence_plan(
        question,
        requested_fact_type="procedure",
        subject="формирование командировки",
    )
    decision = EvidenceAssessor().assess(
        question,
        [
            _chunk(
                801,
                title="Инструкция согласования командировки",
                section="Согласование командировки",
                text="Создайте служебную записку и запустите обработку.",
                logical_unit_type="procedure",
            )
        ],
        plan=plan,
    )

    assert decision.mode == "full_answer"
    assert [chunk.chunk_id for chunk in decision.supporting_chunks] == [801]
    diagnostic = decision.chunk_assessments[0]
    assert "title" in diagnostic.subject_match_source
    assert any(
        term.startswith("командир")
        for term in diagnostic.matched_subject_terms
    )


def test_generic_procedure_without_subject_match_is_rejected() -> None:
    question = "Как оформить командировку?"
    plan = _evidence_plan(
        question,
        requested_fact_type="procedure",
        subject="командировка",
    )
    decision = EvidenceAssessor().assess(
        question,
        [
            _chunk(
                802,
                title="Инструкция согласования договора",
                section="Согласование договора",
                text="Создайте договор и запустите обработку.",
                logical_unit_type="procedure",
            )
        ],
        plan=plan,
    )

    assert decision.mode == "insufficient_evidence"
    assert decision.supporting_chunks == ()
    assert decision.chunk_assessments[0].support_decision == "non_supporting"


def test_negative_subject_guard_rejects_other_document_procedures() -> None:
    question = "Как оформить документ, которого нет в базе?"
    plan = _evidence_plan(
        question,
        requested_fact_type="procedure",
        subject="документооборот",
    )
    chunks = [
        _chunk(
            803,
            title="Инструкция согласования приказов в документообороте",
            section="Согласование приказов",
            text="Создайте приказ и запустите согласование.",
            logical_unit_type="procedure",
        ),
        _chunk(
            804,
            title="Инструкция согласования договоров в документообороте",
            section="Согласование договоров",
            text="Создайте договор и запустите согласование.",
            logical_unit_type="procedure",
        ),
    ]

    decision = EvidenceAssessor().assess(question, chunks, plan=plan)

    assert decision.mode == "insufficient_evidence"
    assert decision.supporting_chunks == ()
    assert [
        chunk.chunk_id for chunk in decision.non_supporting_chunks
    ] == [803, 804]
    assert all(
        diagnostic.negative_subject_guard
        for diagnostic in decision.chunk_assessments
    )


def test_compound_responsibility_has_partial_identity_coverage() -> None:
    question = "Кто отвечает за рабочие места и оборудование?"
    plan = _evidence_plan(
        question,
        requested_fact_type="responsible_person",
        subject="отдел рабочих мест и оборудования",
    )
    chunks = [
        _chunk(
            805,
            title="Петров Алексей",
            section="Справочник сотрудников",
            text=(
                "Петров Алексей отвечает за системное администрирование "
                "и рабочие места."
            ),
            entity_type="employee",
            record_key="employee:petrov_aleksey",
            doc_type="employee_role",
        )
    ]

    decision = EvidenceAssessor().assess(question, chunks, plan=plan)

    assert decision.mode == "partial_answer"
    assert decision.supported_requirements == ("responsible_identity",)
    assert decision.unsupported_requirements == (
        "complete_responsibility_scope",
    )
    assert [chunk.chunk_id for chunk in decision.supporting_chunks] == [805]
    diagnostic = decision.chunk_assessments[0]
    assert diagnostic.requirement_ids == ("responsible_identity",)
    assert any(
        term.startswith("оборудов")
        for term in diagnostic.rejected_subject_terms
    )


def test_evidence_diagnostics_are_json_serializable_facts() -> None:
    question = "Кому подавать заявление на командировку?"
    plan = _evidence_plan(
        question,
        requested_fact_type="document_recipient",
        subject="командировка",
    )
    decision = EvidenceAssessor().assess(
        question,
        [
            _chunk(
                47,
                title="Инструкция Согласования командировки",
                text="Создайте СЗ_Командировка.",
                logical_unit_type="procedure",
            )
        ],
        plan=plan,
    )

    diagnostic = decision.to_dict()

    assert diagnostic["available"] is True
    assert diagnostic["answer_mode"] == "partial_answer"
    assert diagnostic["supporting_chunk_ids"] == [47]
    assert diagnostic["non_supporting_chunk_ids"] == []
    requirement = diagnostic["evidence_requirements"][0]
    assert requirement["rejection_reasons"] == [
        "named_initial_recipient_not_found"
    ]
    chunk = diagnostic["chunk_assessments"][0]
    assert chunk["support_decision"] == "supporting"
    assert chunk["requirement_ids"] == ["related_submission_procedure"]
    assert chunk["negative_subject_guard"] is False


def _evidence_plan(
    question: str,
    *,
    requested_fact_type: str,
    subject: str,
):
    query_plan = QueryPlan(
        intent=(
            "roles_responsibility"
            if requested_fact_type
            in {"responsible_person", "document_recipient"}
            else "document_flow"
        ),
        normalized_question=question,
        query_expansions=[],
        preferred_sources=[],
        answer_type="general",
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="evidence test",
        requested_fact_type=requested_fact_type,
        temporal_scope="static",
        subject=subject,
    )
    context_plan = ContextPlanner().build(
        query_plan=query_plan,
        configured_max_chunks=6,
        max_context_chars=8_000,
        score_ratio=0.65,
    )
    return EvidencePlanner().build(
        question,
        query_plan=query_plan,
        context_plan=context_plan,
    )


def _chunk(
    chunk_id: int,
    *,
    title: str,
    text: str,
    logical_unit_type: str | None = None,
    section: str = "Раздел",
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
        source=f"data/raw_docs/{title}.pdf",
        section=section,
        page=1,
        text=text,
        score=100.0,
        metadata=metadata,
        doc_type=doc_type,
        base_score=100.0,
        rerank_score=100.0,
        final_score=100.0,
        matched_terms=[],
        matched_excerpt=text,
        selection_reasons=reasons,
    )
