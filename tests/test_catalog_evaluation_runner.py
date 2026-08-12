from __future__ import annotations

from scripts.evaluate_catalog_user_flow import (
    CASES,
    NEXT_STRUCTURED_CATALOG_CASES,
    grade_record,
    summarize,
)


def _record(**overrides):
    value = {
        "actual_route": "catalog",
        "response_kind": "catalog_exact",
        "answer_mode": "catalog",
        "catalog_match_type": "catalog_exact",
        "catalog_result_count": 1,
        "catalog_results": [
            {
                "part_number": "X44235100",
                "assembly_code": "X44236986",
                "position": "70",
                "quantity": "4.000",
                "source_page": 467,
            }
        ],
        "catalog_source_count": 1,
        "corporate_source_count": 0,
        "catalog_sources": [{}],
        "corporate_sources": [],
        "resolved_requirements": [],
        "final_answer": "Деталь: X44235100",
        "error": None,
        "analytics_logged": True,
        "failure_reasons": [],
        "technical_grade": "PASS",
        "expected_route": "catalog",
        "category": "exact",
        "duration_ms": 1.0,
    }
    value.update(overrides)
    return value


def test_fixed_evaluation_set_contains_exactly_37_unique_cases():
    assert len(CASES) == 37
    assert len({case.case_id for case in CASES}) == 37


def test_next_evaluation_has_seven_structured_catalog_gate_cases():
    assert len(NEXT_STRUCTURED_CATALOG_CASES) == 7
    assert len({case.case_id for case in NEXT_STRUCTURED_CATALOG_CASES}) == 7


def test_a01_strict_exact_facts_pass():
    case = next(case for case in CASES if case.case_id == "A01")
    assert grade_record(case, _record()) == ("PASS", [])


def test_b01_rejects_non_prefix_noise():
    case = next(case for case in CASES if case.case_id == "B01")
    record = _record(
        catalog_match_type="catalog_prefix",
        catalog_results=[{"part_number": "301134230120"}],
    )
    grade, reasons = grade_record(case, record)
    assert grade == "FAIL"
    assert "catalog_noise" in reasons


def test_mixed_missing_catalog_requirement_fails():
    case = next(case for case in CASES if case.case_id == "E01")
    record = _record(
        actual_route="mixed",
        response_kind="partial_mixed_answer",
        answer_mode="partial_mixed",
        catalog_results=[],
        catalog_result_count=0,
        catalog_source_count=0,
        resolved_requirements=[
            {"source": "catalog", "status": "not_found"},
            {"source": "corporate", "status": "found"},
        ],
    )
    grade, reasons = grade_record(case, record)
    assert grade == "FAIL"
    assert "catalog_not_found" in reasons


def test_summary_does_not_count_rejected_probe_as_catalog_answer():
    probe_only = _record(
        actual_route="corporate",
        response_kind="no_answer",
        answer_mode="insufficient_evidence",
        catalog_result_count=5,
        catalog_results=[],
        catalog_source_count=0,
        corporate_source_count=0,
    )
    summary = summarize([probe_only])
    assert summary["sources"]["catalog_answers"] == 0
    assert summary["sources"]["missing"] == 0


def test_next_evaluation_summary_exposes_structured_acceptance_gate():
    records = []
    for case in NEXT_STRUCTURED_CATALOG_CASES:
        records.append(
            _record(
                case_id=case.case_id,
                category="structured_catalog",
                expected_route="catalog",
                actual_route="catalog",
                catalog_match_type=case.expected_match_type,
                retrieval={"retrieval_stages": [case.expected_match_type]},
                corporate_source_count=0,
            )
        )

    gate = summarize(records)["acceptance_gate"]

    assert gate["structured_catalog_passed"] == 7
    assert gate["structured_catalog_corporate_leakage"] == 0
    assert gate["checks"]["structured_catalog_intents_7_of_7"]
