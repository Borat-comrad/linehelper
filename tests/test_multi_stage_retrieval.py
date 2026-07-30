from __future__ import annotations

from types import SimpleNamespace

import pytest

from linehelper.memory.memory_store import MemoryStore
from linehelper.rag.retriever import (
    CandidateAggregator,
    RetrievalObservation,
    RetrievalPlanner,
    RetrievalStageHit,
    RetrievedChunk,
    SemanticRetriever,
    extract_exact_entities,
)


STRUCTURED_SOURCE = "data/raw_docs/company_structure.txt"
EQUIPMENT_SOURCE = "data/raw_docs/equipment_request.pdf"
DOCUMENT_FLOW_SOURCE = "data/raw_docs/ИП-0006 Документооборот.pdf"


def _query_plan(
    *,
    requested_fact_type: str = "unknown",
    subject: str = "",
    intent: str = "unknown",
    normalized_question: str = "",
    query_expansions: list[str] | None = None,
    preferred_sources: list[str] | None = None,
    operational_lookup: bool = False,
    clarification_action: str = "continue_retrieval",
):
    return SimpleNamespace(
        requested_fact_type=requested_fact_type,
        subject=subject,
        intent=intent,
        normalized_question=normalized_question,
        query_expansions=query_expansions or [],
        preferred_sources=preferred_sources or [],
        operational_lookup=operational_lookup,
        clarification_action=clarification_action,
    )


def _build_plan(
    question: str,
    *,
    original_question: str | None = None,
    **plan_fields,
):
    return RetrievalPlanner().build(
        original_question=original_question or question,
        resolved_question=question,
        query_plan=_query_plan(
            normalized_question=plan_fields.pop("normalized_question", question),
            **plan_fields,
        ),
    )


def _chunk(
    chunk_id: int,
    *,
    final_score: float = 10.0,
    raw_score: float = -1.0,
    record_key: str | None = None,
    title: str | None = None,
    metadata: dict | None = None,
) -> RetrievedChunk:
    chunk_metadata = dict(metadata or {})
    if record_key:
        chunk_metadata["record_key"] = record_key
    return RetrievedChunk(
        chunk_id=chunk_id,
        title=title or f"Chunk {chunk_id}",
        source="data/raw_docs/test.pdf",
        section=f"Section {chunk_id}",
        page=1,
        text=f"Text {chunk_id}",
        score=raw_score,
        metadata=chunk_metadata,
        final_score=final_score,
        rerank_score=max(0.0, final_score / 2),
    )


def _observation(
    chunk: RetrievedChunk,
    *,
    stage: str,
    query: str,
    raw_score: float,
    rank: int,
    scored_value: float,
    metadata_matches: tuple[str, ...] = (),
) -> RetrievalObservation:
    return RetrievalObservation(
        chunk=chunk,
        stage_hit=RetrievalStageHit(
            stage=stage,
            query=query,
            raw_score=raw_score,
            rank=rank,
            stage_weight=0.0,
            scored_value=scored_value,
        ),
        metadata_matches=metadata_matches,
        match_reasons=(f"matched by {stage}",),
        retrieval_adjustments=(f"adjusted by {stage}",),
    )


def _make_retriever(tmp_path) -> tuple[MemoryStore, SemanticRetriever]:
    db_path = tmp_path / "memory.db"
    store = MemoryStore(str(db_path))
    store.ensure_schema()
    return store, SemanticRetriever(db_path)


def _candidate_by_key(result, record_key: str):
    return next(
        candidate
        for candidate in result.candidates
        if candidate.chunk.metadata.get("record_key") == record_key
    )


def test_procedure_question_creates_type_specific_stages() -> None:
    plan = _build_plan(
        "Как получить новое оборудование?",
        requested_fact_type="procedure",
        subject="получение нового оборудования",
        intent="equipment_it_request",
    )

    assert [stage.name for stage in plan.stages] == [
        "procedure_lookup",
        "exact_subject",
        "fts_resolved_question",
        "sibling_lookup",
    ]
    assert plan.subject == "получение нового оборудования"


@pytest.mark.parametrize(
    "requested_fact_type",
    ["responsible_person", "primary_contact", "unit_head", "document_recipient"],
)
def test_responsibility_fact_creates_organization_function_stage(
    requested_fact_type: str,
) -> None:
    plan = _build_plan(
        "Кто отвечает за внутренний документооборот?",
        requested_fact_type=requested_fact_type,
        subject="внутренний документооборот",
        intent="roles_responsibility",
    )

    stage = next(
        stage
        for stage in plan.stages
        if stage.name == "organization_function_lookup"
    )
    assert stage.query == "внутренний документооборот"
    assert "organization_unit" in stage.filters["doc_types"]


def test_current_operational_plan_skips_semantic_stages() -> None:
    plan = _build_plan(
        "Какой статус заказа №123?",
        requested_fact_type="current_status",
        subject="заказ",
        operational_lookup=True,
    )

    assert plan.operational_lookup is True
    assert plan.stages == ()
    assert "operational_lookup_skips_semantic_retrieval" in plan.planning_reasons


def test_resolved_question_is_used_instead_of_short_follow_up() -> None:
    plan = _build_plan(
        "Как оформить коммерческое предложение?",
        original_question="Коммерческое предложение",
        requested_fact_type="procedure",
        subject="коммерческое предложение",
    )

    resolved_stage = next(
        stage for stage in plan.stages if stage.name == "fts_resolved_question"
    )
    assert resolved_stage.query == "Как оформить коммерческое предложение?"
    assert plan.original_question == "Коммерческое предложение"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Что сказано в ИП-0005?", "ИП 0005"),
        ("Кто руководит отделом 12Б?", "отдел 12Б"),
        ("Как создать СЗ_Командировка?", "СЗ_Командировка"),
        ("Кто работает с KRONES?", "KRONES"),
        ("Как связаться со Шкиренковым?", "Шкиренковым"),
    ],
)
def test_exact_entities_create_exact_stage(question: str, expected: str) -> None:
    entities = extract_exact_entities(question)
    plan = _build_plan(question)

    assert expected in entities
    assert any(
        stage.name in {"exact_identifier", "exact_entity"}
        and stage.query == expected
        for stage in plan.stages
    )


def test_entity_boundaries_do_not_match_inside_other_word() -> None:
    assert extract_exact_entities("псевдоИП-0005текст") == ()


def test_subject_is_preserved_before_generic_expansions() -> None:
    plan = _build_plan(
        "Кто отвечает за внутренний документооборот?",
        requested_fact_type="responsible_person",
        subject="внутренний документооборот",
        query_expansions=["руководитель подразделения", "ответственный сотрудник"],
    )

    subject_stage = next(stage for stage in plan.stages if stage.name == "exact_subject")
    expansion_queries = [
        stage.query
        for stage in plan.stages
        if stage.name == "fts_query_expansion"
    ]
    assert subject_stage.query == "внутренний документооборот"
    assert expansion_queries == [
        "руководитель подразделения",
        "ответственный сотрудник",
    ]
    assert subject_stage.priority < next(
        stage.priority
        for stage in plan.stages
        if stage.name == "fts_query_expansion"
    )


def test_empty_subject_is_safe() -> None:
    plan = _build_plan("Что известно?", subject="")

    assert all(
        stage.name not in {"exact_subject", "organization_function_lookup"}
        for stage in plan.stages
    )


def test_preferred_source_is_an_additional_stage_not_a_filter() -> None:
    plan = _build_plan(
        "Что сказано о распоряжениях?",
        requested_fact_type="procedure",
        subject="распоряжения",
        preferred_sources=["ИП-0005 Распоряжения"],
    )

    assert any(stage.name == "fts_resolved_question" for stage in plan.stages)
    preferred = next(
        stage for stage in plan.stages if stage.name == "preferred_source_lookup"
    )
    assert preferred.query == "ИП-0005 Распоряжения"


def test_pending_clarification_does_not_create_retrieval_stages() -> None:
    plan = _build_plan(
        "Кому отдать документы?",
        requested_fact_type="document_recipient",
        subject="документы",
        clarification_action="clarify",
    )

    assert plan.stages == ()
    assert "clarification_required_before_retrieval" in plan.planning_reasons


def test_topic_change_plan_uses_new_subject() -> None:
    plan = _build_plan(
        "Кто отвечает за таможню?",
        original_question="Кто отвечает за таможню?",
        requested_fact_type="responsible_person",
        subject="таможня",
    )

    assert plan.subject == "таможня"
    assert "КП" not in " ".join(stage.query for stage in plan.stages)


def test_aggregation_preserves_best_score_and_all_provenance() -> None:
    chunk = _chunk(10, record_key="record:test")
    observations = [
        _observation(
            chunk,
            stage="fts_resolved_question",
            query="original",
            raw_score=-1.0,
            rank=5,
            scored_value=100.0,
        ),
        _observation(
            chunk,
            stage="exact_subject",
            query="subject",
            raw_score=-5.0,
            rank=1,
            scored_value=300.0,
            metadata_matches=("logical_unit_type=procedure",),
        ),
        _observation(
            chunk,
            stage="fts_query_expansion",
            query="expansion",
            raw_score=-2.0,
            rank=3,
            scored_value=200.0,
        ),
    ]

    result = CandidateAggregator().aggregate(observations)
    candidate = result.candidates[0]

    assert candidate.final_score == 300.0
    assert candidate.best_raw_score == -5.0
    assert candidate.matched_queries == ("original", "subject", "expansion")
    assert {hit.stage for hit in candidate.stage_hits} == {
        "fts_resolved_question",
        "exact_subject",
        "fts_query_expansion",
    }
    assert candidate.metadata_matches == ("logical_unit_type=procedure",)
    assert result.duplicate_count == 2
    assert result.best_score_dedupe_correct == 1
    assert result.best_score_dedupe_checks == 1


def test_aggregation_has_deterministic_tie_breaking() -> None:
    observations = [
        _observation(
            _chunk(2),
            stage="exact_subject",
            query="same",
            raw_score=-1.0,
            rank=1,
            scored_value=100.0,
        ),
        _observation(
            _chunk(1),
            stage="exact_subject",
            query="same",
            raw_score=-1.0,
            rank=1,
            scored_value=100.0,
        ),
    ]

    first = CandidateAggregator().aggregate(observations)
    second = CandidateAggregator().aggregate(list(reversed(observations)))

    assert [item.stable_key for item in first.candidates] == [
        item.stable_key for item in second.candidates
    ]


def test_aggregation_applies_limit_after_deduplication() -> None:
    observations = [
        _observation(
            _chunk(index),
            stage="fts_resolved_question",
            query="query",
            raw_score=-float(index),
            rank=index,
            scored_value=float(index),
        )
        for index in range(1, 6)
    ]

    result = CandidateAggregator().aggregate(observations, candidate_limit=2)

    assert len(result.candidates) == 2
    assert result.candidate_count_after_dedupe == 5


def test_empty_aggregation_is_safe() -> None:
    result = CandidateAggregator().aggregate([])

    assert result.candidates == ()
    assert result.duplicate_count == 0


def test_generic_hit_does_not_replace_exact_hit() -> None:
    chunk = _chunk(1)
    result = CandidateAggregator().aggregate(
        [
            _observation(
                chunk,
                stage="exact_identifier",
                query="ИП-0005",
                raw_score=-4.0,
                rank=1,
                scored_value=350.0,
            ),
            _observation(
                chunk,
                stage="fts_query_expansion",
                query="generic",
                raw_score=-10.0,
                rank=1,
                scored_value=100.0,
            ),
        ]
    )

    assert result.candidates[0].final_score == 350.0
    assert result.candidates[0].chunk is chunk


def test_procedure_lookup_finds_equipment_request_with_provenance(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    store.add_chunk(
        namespace="semantic",
        doc_type="reference",
        title="Инструкция Заявка в хозяйственную часть",
        source=EQUIPMENT_SOURCE,
        section="Порядок работы",
        text=(
            "Создайте заявку. В комментарии укажите, о каком оборудовании "
            "или имуществе идёт речь."
        ),
        metadata={
            "logical_unit_type": "procedure",
            "logical_unit_title": "Заявка в хозяйственную часть",
            "source_file": "equipment_request.pdf",
        },
    )
    store.add_chunk(
        namespace="semantic",
        doc_type="reference",
        title="Общий справочник",
        source="data/raw_docs/general.pdf",
        text="Новое оборудование компании.",
        metadata={"logical_unit_type": "reference_block"},
    )
    plan = _build_plan(
        "Как получить новое оборудование?",
        requested_fact_type="procedure",
        subject="получение нового оборудования",
    )

    result = retriever.retrieve_plan(plan)
    candidate = next(
        item for item in result.candidates if item.chunk.source == EQUIPMENT_SOURCE
    )

    assert list(result.candidates).index(candidate) < 10
    assert "procedure_lookup" in {hit.stage for hit in candidate.stage_hits}
    assert candidate.best_raw_score is not None


def test_organization_function_lookup_finds_structured_records(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    expected_keys = {
        "organization_unit:division_1:2025",
        "organization_unit:department_2:2025",
    }
    for index, record_key in enumerate(sorted(expected_keys), start=1):
        store.add_chunk(
            namespace="semantic",
            doc_type="organization_unit",
            title=f"Подразделение {index}",
            source=STRUCTURED_SOURCE,
            text=(
                "Внутренний документооборот: Шкиренков Роман, "
                "Малахова Мария."
            ),
            metadata={
                "knowledge_domain": "organization_structure",
                "record_key": record_key,
            },
        )
    store.add_chunk(
        namespace="semantic",
        doc_type="document_flow_policy",
        title="Политика документооборота",
        source=DOCUMENT_FLOW_SOURCE,
        text="Внутренний документооборот регулируется правилами.",
        metadata={"logical_unit_type": "policy_rule"},
    )
    plan = _build_plan(
        "Кто отвечает за внутренний документооборот?",
        requested_fact_type="responsible_person",
        subject="внутренний документооборот",
    )

    result = retriever.retrieve_plan(plan)

    for record_key in expected_keys:
        candidate = _candidate_by_key(result, record_key)
        assert "organization_function_lookup" in {
            hit.stage for hit in candidate.stage_hits
        }


def test_sibling_lookup_keeps_all_policy_rules_and_excludes_unrelated_block(
    tmp_path,
) -> None:
    store, retriever = _make_retriever(tmp_path)
    expected_sections = {f"Правило {index}" for index in range(1, 5)}
    for section in sorted(expected_sections):
        store.add_chunk(
            namespace="semantic",
            doc_type="document_flow_policy",
            title="ИП-0006 Документооборот",
            source=DOCUMENT_FLOW_SOURCE,
            section=section,
            text=f"{section}. Документооборот компании.",
            metadata={
                "logical_unit_type": "policy_rule",
                "logical_unit_title": section,
                "source_file": "ИП-0006 Документооборот.pdf",
            },
        )
    unrelated_id = store.add_chunk(
        namespace="semantic",
        doc_type="document_flow_policy",
        title="ИП-0006 Документооборот",
        source=DOCUMENT_FLOW_SOURCE,
        section="Пример",
        text="Документооборот компании: пример.",
        metadata={
            "logical_unit_type": "example",
            "source_file": "ИП-0006 Документооборот.pdf",
        },
    )
    plan = _build_plan(
        "Перечисли правила документооборота.",
        requested_fact_type="list",
        subject="правила документооборота",
    )

    result = retriever.retrieve_plan(plan)
    policy_candidates = [
        candidate
        for candidate in result.candidates
        if candidate.chunk.section in expected_sections
    ]

    assert {item.chunk.section for item in policy_candidates} == expected_sections
    assert all(
        "sibling_lookup" in {hit.stage for hit in item.stage_hits}
        for item in policy_candidates
    )
    unrelated = next(
        item for item in result.candidates if item.chunk.chunk_id == unrelated_id
    )
    assert "sibling_lookup" not in {hit.stage for hit in unrelated.stage_hits}


def test_procedure_sibling_merges_with_existing_candidate(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    for part in (1, 2):
        store.add_chunk(
            namespace="semantic",
            doc_type="reference",
            title="Инструкция по заявке",
            source=EQUIPMENT_SOURCE,
            section=f"Часть {part}",
            text=f"Заявка на оборудование. Шаг {part}.",
            metadata={
                "logical_unit_type": "procedure",
                "logical_unit_title": "Заявка на оборудование",
                "part_index": part,
                "part_count": 2,
            },
        )
    plan = _build_plan(
        "Как оформить заявку на оборудование?",
        requested_fact_type="procedure",
        subject="заявка на оборудование",
    )

    result = retriever.retrieve_plan(plan)
    candidates = [
        item for item in result.candidates if item.chunk.source == EQUIPMENT_SOURCE
    ]

    assert len(candidates) == 2
    assert all(
        {"procedure_lookup", "sibling_lookup"}.issubset(
            {hit.stage for hit in item.stage_hits}
        )
        for item in candidates
    )


@pytest.mark.parametrize(
    ("question", "requested_fact_type", "subject", "doc_type", "record_key"),
    [
        (
            "Кто отвечает за доставку клиенту?",
            "responsible_person",
            "доставка клиенту",
            "responsibility_route",
            "responsibility_route:delivery:2025",
        ),
        (
            "Кто главный по рабочим местам?",
            "responsible_person",
            "рабочие места",
            "organization_unit",
            "organization_unit:workplaces:2025",
        ),
    ],
)
def test_exact_subject_keeps_relevant_organization_candidate(
    tmp_path,
    question,
    requested_fact_type,
    subject,
    doc_type,
    record_key,
) -> None:
    store, retriever = _make_retriever(tmp_path)
    store.add_chunk(
        namespace="semantic",
        doc_type=doc_type,
        title="Точный маршрут",
        source=STRUCTURED_SOURCE,
        text=f"{subject}: назначенный ответственный.",
        metadata={
            "knowledge_domain": "organization_structure",
            "record_key": record_key,
        },
    )
    plan = _build_plan(
        question,
        requested_fact_type=requested_fact_type,
        subject=subject,
    )

    result = retriever.retrieve_plan(plan)
    candidate = _candidate_by_key(result, record_key)

    assert "organization_function_lookup" in {
        hit.stage for hit in candidate.stage_hits
    }


@pytest.mark.parametrize(
    ("question", "identifier", "source"),
    [
        (
            "Как создать СЗ_Командировка?",
            "СЗ_Командировка",
            "data/raw_docs/business_trip.pdf",
        ),
        (
            "Что сказано в ИП-0005?",
            "ИП-0005",
            "data/raw_docs/ИП-0005 Распоряжения.pdf",
        ),
    ],
)
def test_exact_identifier_stage_finds_form_or_policy(
    tmp_path,
    question,
    identifier,
    source,
) -> None:
    store, retriever = _make_retriever(tmp_path)
    store.add_chunk(
        namespace="semantic",
        doc_type="one_c_instruction",
        title=f"Инструкция {identifier}",
        source=source,
        text=f"Точное корпоративное обозначение {identifier}.",
    )
    plan = _build_plan(question, requested_fact_type="procedure")

    result = retriever.retrieve_plan(plan)
    candidate = next(item for item in result.candidates if item.chunk.source == source)

    assert "exact_identifier" in {hit.stage for hit in candidate.stage_hits}


def test_exact_unit_stage_finds_organization_unit(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    record_key = "organization_unit:department_12b:2025"
    store.add_chunk(
        namespace="semantic",
        doc_type="organization_unit",
        title="Отдел 12Б — Отгрузки",
        source=STRUCTURED_SOURCE,
        text="Отдел 12Б. Руководитель: назначенный сотрудник.",
        metadata={"record_key": record_key},
    )
    plan = _build_plan(
        "Кто руководит отделом 12Б?",
        requested_fact_type="unit_head",
        subject="отдел 12Б",
    )

    result = retriever.retrieve_plan(plan)
    candidate = _candidate_by_key(result, record_key)

    assert "exact_identifier" in {hit.stage for hit in candidate.stage_hits}


def test_missing_subject_returns_empty_candidates_without_exception(tmp_path) -> None:
    _, retriever = _make_retriever(tmp_path)
    plan = _build_plan(
        "Как оформить документ, которого нет в базе?",
        requested_fact_type="procedure",
        subject="несуществующий документ",
    )

    result = retriever.retrieve_plan(plan)

    assert result.candidates == ()


def test_operational_plan_does_not_mix_semantic_retrieval(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    store.add_chunk(
        namespace="semantic",
        text="Статический документ про заказ 123.",
    )
    plan = _build_plan(
        "Какой статус заказа №123?",
        requested_fact_type="current_status",
        subject="заказ",
        operational_lookup=True,
    )

    result = retriever.retrieve_plan(plan)

    assert result.candidates == ()
    assert result.stage_hit_counts == {}


def test_candidate_provenance_serializes_to_native_diagnostics(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    store.add_chunk(
        namespace="semantic",
        doc_type="organization_unit",
        title="Отдел 12Б",
        source=STRUCTURED_SOURCE,
        text="Отдел 12Б.",
        metadata={"record_key": "organization_unit:12b"},
    )
    plan = _build_plan(
        "Что известно про отдел 12Б?",
        requested_fact_type="definition",
        subject="отдел 12Б",
    )

    diagnostics = retriever.retrieve_plan(plan).to_dict()
    candidate = diagnostics["candidate_provenance"][0]

    assert diagnostics["retrieval_plan"]["resolved_question"]
    assert diagnostics["candidate_count_before_dedupe"] >= 1
    assert candidate["stage_hits"]
    assert candidate["matched_queries"]
    assert isinstance(candidate["best_raw_score"], float)
    assert isinstance(candidate["final_score"], float)


def test_old_semantic_retriever_api_remains_compatible(tmp_path) -> None:
    store, retriever = _make_retriever(tmp_path)
    store.add_chunk(namespace="semantic", text="Совместимый старый поиск")

    result = retriever.retrieve("старый поиск", limit=1, candidate_limit=5)

    assert len(result) == 1
    assert result[0].text == "Совместимый старый поиск"
