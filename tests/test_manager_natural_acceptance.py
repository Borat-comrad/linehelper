from __future__ import annotations

from scripts.run_manager_natural_acceptance import (
    CASES,
    GROUP_LABELS,
    AcceptanceCase,
    ExpectedOutcome,
    failure_layer,
    grade,
    answer_mode,
    summarize,
    validate_pack,
)
from linehelper.llm.answer_generator import RagAnswer


def test_pack_contains_36_unique_new_cases_across_all_groups():
    validate_pack()
    assert len(CASES) == 36
    assert len({case.case_id for case in CASES}) == 36
    assert {case.group for case in CASES} == set(GROUP_LABELS)
    assert min(sum(case.group == group for case in CASES) for group in GROUP_LABELS) >= 4


def test_catalog_case_is_graded_from_structured_entities_not_answer_wording():
    case = next(case for case in CASES if case.case_id == "A01")
    actual = _actual(
        route="catalog",
        catalog_state="found",
        corporate_state="not_requested",
        answer_mode="catalog",
        extracted_subject="вал нижней части укупорщика",
        catalog_evidence=[{"part_number": "X44235100"}],
        final_answer="Произвольная косметическая формулировка",
    )

    assert grade(case, actual) == []


def test_irrelevant_corporate_source_is_an_evidence_guard_failure():
    case = next(case for case in CASES if case.case_id == "G01")
    actual = _actual(
        route="mixed",
        catalog_state="found",
        corporate_state="not_found",
        answer_mode="partial_mixed",
        catalog_evidence=[{"part_number": "20411640"}],
        corporate_evidence=[{"title": "Рабочий стол и работа с отчетами"}],
    )

    failures = grade(case, actual)

    assert "irrelevant_corporate_evidence_accepted" in failures
    assert failure_layer(failures) == "EVIDENCE_GUARD"


def test_summary_keeps_failures_visible_by_group():
    records = [
        {
            "id": "A01",
            "group": "A",
            "technical_grade": "PASS",
            "failure_layer": None,
            "failure_reasons": [],
        },
        {
            "id": "C01",
            "group": "C",
            "technical_grade": "FAIL",
            "failure_layer": "MIXED_COMPOSITION",
            "failure_reasons": ["wrong_answer_mode"],
        },
    ]

    result = summarize(records)

    assert result["total"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["groups"]["C"]["failed"] == 1


def test_catalog_not_found_response_kind_overrides_generic_catalog_mode():
    result = RagAnswer(
        question="Узел: 99999998",
        answer="Узел не найден.",
        model="catalog-store",
        sources=[],
        chunks_used=0,
        prompt_length=0,
        elapsed_seconds=0.0,
        retrieval_limit=6,
        candidate_limit=30,
        context_limit=3,
        context_score_ratio=0.72,
        diagnostic_candidates=[],
        response_kind="catalog_not_found",
    )

    assert answer_mode(result, {"answer_mode": "catalog"}, {}) == "not_found"


def _actual(**overrides):
    value = {
        "route": "corporate",
        "response_kind": "no_answer",
        "requested_fact_type": "unknown",
        "resolved_requirements": [],
        "extracted_subject": None,
        "extracted_entity": [],
        "assembly_context": None,
        "catalog_probe_performed": False,
        "catalog_match_type": None,
        "catalog_state": "not_requested",
        "corporate_state": "not_found",
        "answer_mode": "not_found",
        "catalog_evidence": [],
        "catalog_sources": [],
        "corporate_evidence": [],
        "supporting_chunk_ids": [],
        "rejected_evidence": {},
        "final_answer": "",
    }
    value.update(overrides)
    return value
