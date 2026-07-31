from __future__ import annotations

from types import SimpleNamespace

from linehelper.rag.context_composer import ContextComposer, ContextPlanner
from linehelper.rag.retriever import RetrievedChunk


def test_list_selects_four_distinct_sibling_logical_units() -> None:
    chunks = [
        _chunk(
            index=index,
            source="data/raw_docs/ИП-0006 Документооборот.pdf",
            section=f"Правило {index}",
            logical_unit_title=f"Правило {index}",
            logical_unit_type="policy_rule",
            stage="sibling_lookup",
        )
        for index in range(1, 5)
    ]
    plan = _plan("list", configured_max_chunks=3)

    result = ContextComposer().compose(
        "Какие правила документооборота действуют?",
        chunks,
        plan=plan,
        base_selection=chunks[:3],
    )

    assert plan.answer_shape == "list"
    assert plan.budget.max_chunks == 4
    assert [chunk.chunk_id for chunk in result.selected] == [1, 2, 3, 4]
    assert result.coverage_satisfied["distinct_sibling_logical_units"] is True


def test_procedure_selects_required_candidate_below_score_ratio() -> None:
    chunks = [
        _chunk(
            index=1,
            source="data/raw_docs/contracts.pdf",
            section="часть 1",
            logical_unit_title="Согласование договоров",
            logical_unit_type="procedure",
            score=320.0,
            stage="procedure_lookup",
        ),
        _chunk(
            index=2,
            source="data/raw_docs/contracts.pdf",
            section="часть 2",
            logical_unit_title="Согласование договоров",
            logical_unit_type="procedure",
            score=318.0,
            stage="procedure_lookup",
        ),
        _chunk(
            index=3,
            source="data/raw_docs/equipment-request.pdf",
            section="Заявка",
            logical_unit_title="Заявка в хозяйственную часть",
            logical_unit_type="procedure",
            score=180.0,
            stage="procedure_lookup",
        ),
    ]
    plan = _plan("procedure", configured_max_chunks=3, score_ratio=0.65)

    result = ContextComposer().compose(
        "Как получить новое оборудование?",
        chunks,
        plan=plan,
        base_selection=[],
    )

    assert [chunk.chunk_id for chunk in result.selected] == [1, 3]
    assert result.coverage_satisfied["primary_procedure"] is True


def test_budget_limits_context_size() -> None:
    chunks = [
        _chunk(index=1, text="123456"),
        _chunk(index=2, text="abcdef"),
    ]
    plan = _plan("definition", configured_max_chunks=3, max_context_chars=10)

    result = ContextComposer().compose(
        "Что это?",
        chunks,
        plan=plan,
        base_selection=chunks,
    )

    assert [chunk.chunk_id for chunk in result.selected] == [1]
    assert result.estimated_chars == 6
    assert "budget_exceeded" in result.excluded_reasons.values()


def test_duplicate_coverage_does_not_consume_another_slot() -> None:
    chunks = [
        _chunk(
            index=1,
            source="data/raw_docs/procedure.pdf",
            section="часть 1",
            logical_unit_title="Одна процедура",
            logical_unit_type="procedure",
            stage="procedure_lookup",
        ),
        _chunk(
            index=2,
            source="data/raw_docs/procedure.pdf",
            section="часть 2",
            logical_unit_title="Одна процедура",
            logical_unit_type="procedure",
            stage="procedure_lookup",
        ),
    ]

    result = ContextComposer().compose(
        "Как оформить заявление?",
        chunks,
        plan=_plan("procedure"),
    )

    assert [chunk.chunk_id for chunk in result.selected] == [1]
    assert result.excluded_reasons[
        "chunk_id:2|source:data/raw_docs/procedure.pdf|section:часть 2"
    ] == "duplicate_coverage"


def test_responsibility_preserves_best_structured_candidate() -> None:
    structured = _chunk(
        index=7,
        logical_unit_title="Доставка клиенту",
        logical_unit_type="responsibility_route",
        record_key="responsibility_route:delivery",
        score=250.0,
    )

    result = ContextComposer().compose(
        "Кто отвечает за доставку?",
        [structured],
        plan=_plan("responsible_person"),
        base_selection=[structured],
    )

    assert result.selected == (structured,)
    assert result.coverage_satisfied["primary_responsibility"] is True


def test_empty_candidates_are_safe() -> None:
    result = ContextComposer().compose(
        "Что известно?",
        [],
        plan=_plan("definition"),
    )

    assert result.selected == ()
    assert result.estimated_chars == 0
    assert result.coverage_satisfied["primary_fact"] is True


def test_context_order_is_deterministic() -> None:
    chunks = [
        _chunk(
            index=index,
            source="data/raw_docs/rules.pdf",
            section=f"R{index}",
            logical_unit_title=f"R{index}",
            logical_unit_type="policy_rule",
            score=100.0,
            stage="sibling_lookup",
        )
        for index in (3, 1, 2, 4)
    ]
    plan = _plan("list")
    composer = ContextComposer()

    first = composer.compose("Перечисли правила", chunks, plan=plan)
    second = composer.compose("Перечисли правила", chunks, plan=plan)

    assert [chunk.chunk_id for chunk in first.selected] == [3, 1, 2, 4]
    assert first.to_dict() == second.to_dict()


def test_diagnostics_include_selection_and_exclusion_reasons() -> None:
    chunks = [
        _chunk(
            index=index,
            source="data/raw_docs/rules.pdf",
            section=f"R{index}",
            logical_unit_title=f"R{index}",
            logical_unit_type="policy_rule",
            stage="sibling_lookup",
        )
        for index in range(1, 6)
    ]
    result = ContextComposer().compose(
        "Перечисли правила",
        chunks,
        plan=_plan("list"),
        base_selection=chunks[:3],
    )

    diagnostics = result.to_dict()

    assert diagnostics["selected_context_reasons"][0]["reasons"] == [
        "required_sibling"
    ]
    assert diagnostics["excluded_candidate_reasons"] == [
        {
            "stable_key": (
                "chunk_id:5|source:data/raw_docs/rules.pdf|section:R5"
            ),
            "reason": "lower_priority",
        }
    ]
    assert diagnostics["context_size"]["chunks"] == 4


def _plan(
    requested_fact_type: str,
    *,
    configured_max_chunks: int = 3,
    max_context_chars: int = 8_000,
    score_ratio: float = 0.65,
):
    return ContextPlanner().build(
        query_plan=SimpleNamespace(requested_fact_type=requested_fact_type),
        configured_max_chunks=configured_max_chunks,
        max_context_chars=max_context_chars,
        score_ratio=score_ratio,
    )


def _chunk(
    *,
    index: int,
    source: str = "data/raw_docs/source.pdf",
    section: str | None = None,
    logical_unit_title: str | None = None,
    logical_unit_type: str = "policy_rule",
    record_key: str | None = None,
    score: float = 100.0,
    text: str = "Короткий тестовый контекст.",
    stage: str | None = None,
) -> RetrievedChunk:
    metadata = {
        "logical_unit_title": logical_unit_title or section or f"unit-{index}",
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
        title=logical_unit_title or f"Chunk {index}",
        source=source,
        section=section or f"section-{index}",
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
