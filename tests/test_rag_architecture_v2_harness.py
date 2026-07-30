from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import QueryPlan, fallback_query_plan
from linehelper.rag.retriever import RetrievedChunk
from scripts import rag_architecture_v2_harness as harness
from scripts import run_rag_architecture_v2_baseline as baseline_runner


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rag_architecture_v2_cases.json"


def test_fixture_loads_all_core_and_neighbor_cases() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    ids = {case["id"] for case in fixture["cases"]}

    assert len(fixture["cases"]) == 71
    assert {"T01", "T02", "T03", "T04", "T05A", "T05B", "T06", "T07", "T08", "T09"} <= ids
    assert len([case for case in fixture["cases"] if "paired" in case["tags"]]) == 8
    assert {f"CR{index:02d}" for index in range(1, 13)} <= ids
    assert {f"MR{index:02d}" for index in range(1, 9)} <= ids


def test_fixture_rejects_duplicate_ids() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    fixture["cases"].append(copy.deepcopy(fixture["cases"][0]))

    with pytest.raises(harness.FixtureValidationError, match="duplicate case id"):
        harness.validate_fixture(fixture)


def test_fixture_rejects_missing_expected_contract_field() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    del fixture["cases"][0]["expected"]["requested_fact_type"]

    with pytest.raises(harness.FixtureValidationError, match="requested_fact_type"):
        harness.validate_fixture(fixture)


def test_fixture_forbids_exact_answer_golden_text() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    fixture["cases"][0]["expected"]["exact_answer"] = "Дословный ответ"

    with pytest.raises(harness.FixtureValidationError, match="exact-text"):
        harness.validate_fixture(fixture)


def test_fixture_rejects_unknown_resolution_kind() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    fixture["cases"][-1]["expected"]["resolution_kind"] = "guess"

    with pytest.raises(harness.FixtureValidationError, match="resolution_kind"):
        harness.validate_fixture(fixture)


def test_fixture_rejects_unknown_retrieval_stage() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    fixture["cases"][-1]["expected"]["required_retrieval_stages"] = ["magic"]

    with pytest.raises(harness.FixtureValidationError, match="retrieval_stages"):
        harness.validate_fixture(fixture)


def test_fixture_accepts_stable_sections_and_fact_type_alternatives() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)
    case = next(case for case in fixture["cases"] if case["id"] == "MR06")

    assert case["expected"]["required_retrieval"]["sections"] == [
        "Кадровые документы",
        "Официальная переписка",
        "Внутреннее взаимодействие",
        "Передача оригиналов документов",
    ]
    assert isinstance(_case("MR04")["expected"]["requested_fact_type"], list)


def test_select_cases_filters_by_ids_and_group() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)

    selected = harness.select_cases(
        fixture,
        case_ids=["PI02", "PI04"],
        group="paired_intent",
    )

    assert [case["id"] for case in selected] == ["PI02", "PI04"]


def test_select_cases_reports_unknown_id() -> None:
    fixture = harness.load_fixture(FIXTURE_PATH)

    with pytest.raises(harness.FixtureValidationError, match="unknown case"):
        harness.select_cases(fixture, case_ids=["DOES_NOT_EXIST"])


def test_evaluator_passes_matching_architectural_invariants() -> None:
    case = _case("T06")
    diagnostic = _diagnostic(
        intent="roles_responsibility",
        requested_fact_type="responsible_person",
        answer="По доставке клиенту следует обратиться к Симоновой.",
        raw_candidates=[_candidate(record_key=_t06_key())],
        selected_context=[_candidate(record_key=_t06_key())],
    )

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation["status"] == "passed"
    assert evaluation["failure_reasons"] == []


def test_evaluator_fails_when_required_context_is_lost() -> None:
    case = _case("T06")
    diagnostic = _diagnostic(
        intent="roles_responsibility",
        requested_fact_type="responsible_person",
        response_kind="no_answer",
        answer="В базе нет точного ответа.",
        raw_candidates=[_candidate(record_key=_t06_key())],
        selected_context=[],
    )

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation["status"] == "failed"
    assert any("missing from context" in reason for reason in evaluation["failure_reasons"])
    assert any("generic no_answer" in reason for reason in evaluation["failure_reasons"])


def test_evaluator_classifies_live_dependency_failure_as_blocked() -> None:
    case = _case("T09")
    diagnostic = {
        "status": "blocked",
        "failure_reasons": ["Ollama is not reachable"],
    }

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation == {
        "status": "blocked",
        "failure_reasons": ["Ollama is not reachable"],
        "checks": [],
    }


def test_evaluator_supports_explicit_not_applicable() -> None:
    case = _case("T09")
    case["expected"]["not_applicable"] = True

    evaluation = harness.evaluate_case(case, {})

    assert evaluation["status"] == "not_applicable"


def test_evaluator_marks_unobservable_requested_fact_type_as_failure() -> None:
    case = _case("T09")
    diagnostic = _diagnostic(
        intent="order_disposition",
        requested_fact_type=harness.unavailable(
            "not implemented in current architecture"
        ),
        answer="Распоряжение фиксируют письменно при первой возможности.",
        raw_candidates=[_candidate(chunk_id=67, title="ИП-0005 Распоряжения")],
        selected_context=[_candidate(chunk_id=67, title="ИП-0005 Распоряжения")],
    )

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation["status"] == "failed"
    assert any("requested_fact_type is not observable" in reason for reason in evaluation["failure_reasons"])


def test_multi_turn_evaluation_checks_each_turn_without_exact_assistant_text() -> None:
    case = _case("T01")
    diagnostic = _diagnostic(
        intent="kp_commercial_offer",
        requested_fact_type="procedure",
        response_kind="partial_answer",
        answer="Отдельная подтверждённая инструкция в базе отсутствует.",
        raw_candidates=[],
        selected_context=[],
        clarification=False,
    )
    diagnostic["resolved_question"] = "Как оформить коммерческое предложение?"
    diagnostic["is_follow_up"] = True
    diagnostic["topic_changed"] = False
    diagnostic["resolution_kind"] = "clarification_answer"
    diagnostic["inherited_slots"] = ["meaning_of_КП"]
    diagnostic["conversation_history_used"] = True
    diagnostic["turns"] = [
        {
            "clarification": {
                "available": True,
                "needed": True,
                "validated_kind": "abbreviation",
                "ambiguity_span": "КП",
                "candidate_meanings": [
                    "коммерческое предложение",
                    "ценный конечный продукт",
                ],
            }
        },
        {
            "resolved_question": "Как оформить коммерческое предложение?",
            "is_follow_up": True,
            "topic_changed": False,
            "resolution_kind": "clarification_answer",
            "inherited_slots": ["meaning_of_КП"],
            "clarification": {
                "available": True,
                "needed": False,
                "validated_kind": "none",
                "ambiguity_span": None,
                "candidate_meanings": [],
            }
        },
    ]

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation["status"] == "passed"


def test_answer_concepts_accept_paraphrase_instead_of_exact_text() -> None:
    case = _case("T09")
    diagnostic = _diagnostic(
        intent="order_disposition",
        requested_fact_type="procedure",
        answer=(
            "Форма должна быть письменной. Если сообщение прозвучало устно, "
            "его фиксируют письменно при первой возможности."
        ),
        raw_candidates=[_candidate(chunk_id=67, title="ИП-0005 Распоряжения")],
        selected_context=[_candidate(chunk_id=67, title="ИП-0005 Распоряжения")],
    )

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation["status"] == "passed"


def test_forbidden_person_is_reported_as_unsupported() -> None:
    case = _case("T08")
    diagnostic = _diagnostic(
        intent="roles_responsibility",
        requested_fact_type="responsible_person",
        answer="За внутренний документооборот отвечает Прокошина.",
        raw_candidates=[
            _candidate(chunk_id=437),
            _candidate(chunk_id=439),
        ],
        selected_context=[
            _candidate(chunk_id=437),
            _candidate(chunk_id=439),
        ],
    )

    evaluation = harness.evaluate_case(case, diagnostic)

    assert evaluation["status"] == "failed"
    assert any("Прокошина" in reason for reason in evaluation["failure_reasons"])


def test_aggregate_metrics_preserves_not_available_values() -> None:
    cases = [_case("T01")]
    diagnostic = _diagnostic(
        intent="kp_commercial_offer",
        requested_fact_type=harness.unavailable("missing"),
        response_kind="partial_answer",
        answer="Инструкции нет.",
        raw_candidates=[],
        selected_context=[],
    )
    diagnostic["case_id"] = "T01"
    diagnostic["resolved_question"] = harness.unavailable("history is not accepted")
    diagnostic = harness.apply_evaluation(cases[0], diagnostic)

    metrics = harness.aggregate_metrics(cases, [diagnostic])

    assert metrics["multi_turn_resolution_rate"] == "not_available"
    assert metrics["evidence_coverage_rate"] == "not_available"
    assert metrics["total_cases"] == 1


def test_aggregate_metrics_computes_recall_and_context_rates() -> None:
    cases = [_case("T06")]
    diagnostic = _diagnostic(
        intent="roles_responsibility",
        requested_fact_type="responsible_person",
        answer="Симонова отвечает за доставку.",
        raw_candidates=[_candidate(record_key=_t06_key(), retrieval_rank=3)],
        selected_context=[_candidate(record_key=_t06_key())],
    )
    diagnostic["case_id"] = "T06"
    diagnostic = harness.apply_evaluation(cases[0], diagnostic)

    metrics = harness.aggregate_metrics(cases, [diagnostic])

    assert metrics["required_chunk_recall_at_5"]["rate"] == 1.0
    assert metrics["required_chunk_recall_at_5"]["required"] == 1
    assert metrics["required_chunk_recall_at_10"]["rate"] == 1.0
    assert metrics["required_chunk_in_context_rate"]["rate"] == 1.0


def test_evaluator_checks_retrieval_stage_and_required_rank() -> None:
    case = _case("MR03")
    candidate = _candidate(
        chunk_id=47,
        title="Инструкция Согласования командировки в Документообороте",
        source="Инструкция Согласования командировки в Документообороте",
        retrieval_rank=4,
        stage_hits=[
            {
                "stage": "exact_identifier",
                "query": "СЗ_Командировка",
                "raw_score": 12.0,
                "rank": 1,
            }
        ],
    )
    diagnostic = _diagnostic(
        intent="business_trip",
        requested_fact_type="procedure",
        raw_candidates=[candidate],
    )
    diagnostic["retrieval_stages"] = [
        {"name": "exact_identifier", "query": "СЗ_Командировка"}
    ]

    evaluation = harness.evaluate_case(case, diagnostic)

    assert not any(
        check["name"] in {"retrieval_stages", "required_candidate_rank"}
        and not check["passed"]
        for check in evaluation["checks"]
    )


def test_retrieval_metrics_cover_provenance_dedupe_and_latency() -> None:
    case = _case("MR03")
    candidate = _candidate(
        chunk_id=47,
        title="Инструкция Согласования командировки в Документообороте",
        source="Инструкция Согласования командировки в Документообороте",
        retrieval_rank=1,
        stage_hits=[
            {
                "stage": "exact_identifier",
                "query": "СЗ_Командировка",
                "raw_score": 10.0,
                "rank": 1,
            }
        ],
    )
    diagnostic = _diagnostic(
        intent="business_trip",
        requested_fact_type="procedure",
        raw_candidates=[candidate],
    )
    diagnostic.update(
        {
            "case_id": "MR03",
            "retrieval_stages": [
                {"name": "exact_identifier", "query": "СЗ_Командировка"}
            ],
            "duplicate_count": 2,
            "best_score_dedupe_correct": 2,
            "best_score_dedupe_checks": 2,
            "retrieval_duration_ms": 12.5,
        }
    )
    record = harness.apply_evaluation(case, diagnostic)

    metrics = harness.aggregate_metrics([case], [record])

    assert metrics["required_candidate_recall_at_5"]["rate"] == 1.0
    assert metrics["exact_identifier_recall"]["rate"] == 1.0
    assert metrics["candidate_provenance_coverage"]["rate"] == 1.0
    assert metrics["duplicate_candidates_before_merge"] == 2
    assert metrics["duplicate_candidates_after_merge"] == 0
    assert metrics["best_score_dedupe_accuracy"]["rate"] == 1.0
    assert metrics["retrieval_latency_p50"] == 12.5
    assert metrics["retrieval_latency_p95"] == 12.5


def test_query_plan_v2_metrics_measure_availability_accuracy_and_missed_routes() -> None:
    cases = [_case("PI01"), _case("PI02")]
    responsibility = _diagnostic(
        intent="roles_responsibility",
        requested_fact_type="responsible_person",
        operational=False,
    )
    responsibility["case_id"] = "PI01"
    operational = _diagnostic(
        intent="roles_responsibility",
        requested_fact_type="responsible_person",
        operational=False,
    )
    operational["case_id"] = "PI02"
    records = [
        harness.apply_evaluation(cases[0], responsibility),
        harness.apply_evaluation(cases[1], operational),
    ]

    metrics = harness.aggregate_metrics(cases, records)

    assert metrics["requested_fact_type_availability"]["rate"] == 1.0
    assert metrics["requested_fact_type_accuracy"]["rate"] == 0.5
    assert metrics["missed_operational_count"] == 1
    assert metrics["paired_responsibility_status_pass_rate"]["rate"] == 0.5


def test_safe_clarification_metrics_measure_recall_and_missing_slots() -> None:
    cases = [_case("CL01")]
    diagnostic = _diagnostic(clarification=True, response_kind="clarification")
    diagnostic["case_id"] = "CL01"
    diagnostic["clarification"].update(
        {
            "validated_required": True,
            "validated_kind": "missing_document_type",
            "missing_slots": ["document_type"],
            "retrieval_started": False,
        }
    )
    record = harness.apply_evaluation(cases[0], diagnostic)

    metrics = harness.aggregate_metrics(cases, [record])

    assert metrics["valid_clarification_recall"]["rate"] == 1.0
    assert metrics["missing_slot_clarification_accuracy"]["rate"] == 1.0


def test_safe_clarification_metrics_measure_rejection_and_retrieval() -> None:
    cases = [_case("T09")]
    diagnostic = _diagnostic()
    diagnostic["case_id"] = "T09"
    diagnostic["clarification"].update(
        {
            "raw_required": True,
            "validated_required": False,
            "retrieval_started": True,
        }
    )
    record = harness.apply_evaluation(cases[0], diagnostic)

    metrics = harness.aggregate_metrics(cases, [record])

    assert metrics["invalid_clarification_rejection_rate"]["rate"] == 1.0
    assert metrics["retrieval_started_after_rejected_clarification"]["rate"] == 1.0


def test_conversation_metrics_measure_resolution_topic_and_stale_state() -> None:
    cases = [_case("CR01"), _case("CR05"), _case("CR07")]
    records = []
    values = [
        {
            "case_id": "CR01",
            "intent": "kp_commercial_offer",
            "requested_fact_type": "procedure",
            "resolved_question": "Как оформить коммерческое предложение?",
            "is_follow_up": True,
            "topic_changed": False,
            "resolution_kind": "clarification_answer",
            "inherited_slots": ["meaning_of_КП"],
            "conversation_history_used": True,
            "response_kind": "partial_answer",
        },
        {
            "case_id": "CR05",
            "intent": "roles_responsibility",
            "requested_fact_type": "responsible_person",
            "resolved_question": "Кто отвечает за таможню?",
            "is_follow_up": False,
            "topic_changed": True,
            "resolution_kind": "topic_change",
            "inherited_slots": [],
            "conversation_history_used": True,
            "response_kind": "no_answer",
        },
        {
            "case_id": "CR07",
            "intent": "roles_responsibility",
            "requested_fact_type": "responsible_person",
            "resolved_question": "Кто отвечает за рабочие места?",
            "is_follow_up": False,
            "topic_changed": False,
            "resolution_kind": "standalone",
            "inherited_slots": [],
            "conversation_history_used": False,
            "response_kind": "no_answer",
        },
    ]
    for case, values_for_case in zip(cases, values, strict=True):
        diagnostic = _diagnostic(
            intent=values_for_case["intent"],
            requested_fact_type=values_for_case["requested_fact_type"],
            response_kind=values_for_case["response_kind"],
            operational=False,
            clarification=False,
        )
        diagnostic.update(values_for_case)
        records.append(harness.apply_evaluation(case, diagnostic))

    metrics = harness.aggregate_metrics(cases, records)

    assert metrics["resolved_question_available_rate"]["rate"] == 1.0
    assert metrics["resolved_question_accuracy"]["rate"] == 1.0
    assert metrics["topic_change_accuracy"]["rate"] == 1.0
    assert metrics["slot_inheritance_accuracy"]["rate"] == 1.0
    assert metrics["stale_context_leak_count"] == 0
    assert metrics["history_used_when_required"]["rate"] == 1.0
    assert metrics["history_used_when_not_required"]["rate"] == 1.0
    assert metrics["repeated_clarification_after_resolution"] == 0


def test_generic_no_answer_metric_requires_stable_evidence_identity() -> None:
    cases = [_case("T06")]
    diagnostic = _diagnostic(
        intent="one_c_operational_lookup",
        requested_fact_type="responsible_person",
        operational=True,
        response_kind="no_answer",
        answer="В базе нет точного ответа.",
        raw_candidates=[_candidate(record_key=_t06_key())],
        selected_context=[],
    )
    diagnostic["case_id"] = "T06"
    diagnostic = harness.apply_evaluation(cases[0], diagnostic)

    metrics = harness.aggregate_metrics(cases, [diagnostic])

    assert metrics["generic_no_answer_when_evidence_exists"] == 1


def test_json_and_jsonl_serialization_preserve_cyrillic(tmp_path: Path) -> None:
    json_path = tmp_path / "baseline.json"
    jsonl_path = tmp_path / "cases.jsonl"
    value = {"answer": "Письменное распоряжение"}

    harness.write_json(json_path, value)
    harness.write_jsonl(jsonl_path, [value])

    assert json.loads(json_path.read_text(encoding="utf-8")) == value
    assert json.loads(jsonl_path.read_text(encoding="utf-8")) == value
    assert "Письменное" in json_path.read_text(encoding="utf-8")


def test_markdown_report_contains_metrics_and_case_status() -> None:
    markdown = harness.render_summary_markdown(
        title="Baseline",
        metrics={"total_cases": 1, "passed": 1},
        records=[
            {
                "case_id": "T09",
                "status": "passed",
                "intent": "order_disposition",
                "response_kind": "answer",
                "failure_reasons": [],
            }
        ],
    )

    assert "# Baseline" in markdown
    assert "| T09 | passed | order_disposition | answer |" in markdown


def test_repeatability_ignores_final_text_and_compares_architecture() -> None:
    first = _diagnostic(answer="Первая формулировка")
    first["case_id"] = "T09"
    second = _diagnostic(answer="Совсем другая формулировка")
    second["case_id"] = "T09"

    repeatability = harness.build_repeatability([first, second])

    assert repeatability["T09"]["all_available_dimensions_stable"] is True


def test_repeatability_compares_retrieval_plan_and_candidate_order() -> None:
    first = _diagnostic(
        raw_candidates=[
            _candidate(chunk_id=29, retrieval_rank=1),
            _candidate(chunk_id=47, retrieval_rank=2),
        ]
    )
    first.update(
        {
            "case_id": "T03",
            "retrieval_stages": [
                {"name": "procedure_lookup", "query": "новое оборудование"}
            ],
            "stage_queries": {
                "procedure_lookup": ["новое оборудование"]
            },
        }
    )
    second = copy.deepcopy(first)

    repeatability = harness.build_repeatability([first, second])

    dimensions = repeatability["T03"]["dimensions"]
    assert dimensions["retrieval_stages"]["stable"] is True
    assert dimensions["stage_queries"]["stable"] is True
    assert dimensions["candidate_order_top_10"]["stable"] is True


def test_repeatability_compares_resolved_conversation_dimensions() -> None:
    records = []
    for answer in ("Первая формулировка", "Вторая формулировка"):
        diagnostic = _diagnostic(answer=answer)
        diagnostic.update(
            {
                "case_id": "CR01",
                "resolved_question": "Как оформить коммерческое предложение?",
                "resolution_kind": "clarification_answer",
                "inherited_slots": ["meaning_of_КП"],
                "topic_changed": False,
            }
        )
        records.append(diagnostic)

    repeatability = harness.build_repeatability(records)
    dimensions = repeatability["CR01"]["dimensions"]

    assert dimensions["resolved_question"]["stable"] is True
    assert dimensions["resolution_kind"]["stable"] is True
    assert dimensions["inherited_slots"]["stable"] is True
    assert dimensions["topic_changed"]["stable"] is True


def test_repeatability_detects_context_instability() -> None:
    first = _diagnostic(selected_context=[_candidate(chunk_id=67)])
    first["case_id"] = "T09"
    second = _diagnostic(selected_context=[_candidate(chunk_id=20)])
    second["case_id"] = "T09"

    repeatability = harness.build_repeatability([first, second])

    assert repeatability["T09"]["dimensions"]["context_chunks"]["stable"] is False


def test_repeatability_compares_each_multi_turn_decision() -> None:
    first = _diagnostic()
    first["case_id"] = "T01"
    first["turns"] = [
        _diagnostic(clarification=True, response_kind="clarification"),
        _diagnostic(
            intent="kp_commercial_offer",
            response_kind="partial_answer",
        ),
    ]
    second = copy.deepcopy(first)
    second["turns"][0]["clarification"]["needed"] = False
    second["turns"][0]["response_kind"] = "answer"

    repeatability = harness.build_repeatability([first, second])

    dimensions = repeatability["T01"]["dimensions"]
    assert dimensions["turn_1_clarification"]["stable"] is False
    assert dimensions["turn_1_response_kind"]["stable"] is False
    assert dimensions["turn_2_intent"]["stable"] is True


def test_selected_context_join_restores_record_key_from_recorded_chunk() -> None:
    raw = [_candidate(record_key=_t06_key(), score=120.0)]
    source = {
        "title": raw[0]["title"],
        "source": raw[0]["source"],
        "section": raw[0]["section"],
        "score": 120.0,
    }

    selected = harness.selected_context_from_sources([source], raw)

    assert selected[0]["record_key"] == _t06_key()
    assert selected[0]["selected_via"] == "rag_source_candidate_join"


def test_recording_retriever_is_transparent_and_records_queries() -> None:
    chunk = _retrieved_chunk()
    inner = StaticRetriever([chunk])
    recording = harness.RecordingRetriever(inner)

    result = recording.retrieve("доставка клиенту", limit=5, candidate_limit=30)

    assert result == [chunk]
    assert inner.questions == ["доставка клиенту"]
    assert recording.calls[0]["chunks"][0]["record_key"] == _t06_key()


def test_deterministic_orchestration_keeps_required_responsibility_context() -> None:
    recording = harness.RecordingRetriever(StaticRetriever([_retrieved_chunk()]))
    client = FakeLlm("За доставку клиенту отвечает Симонова.")
    generator = RagAnswerGenerator(
        retriever=recording,
        llm_client=client,
        query_analyzer=StaticAnalyzer(_responsibility_plan()),
        context_limit=3,
    )

    result = generator.answer("Кто отвечает за отгрузку клиенту?")
    raw_candidates = recording.flattened_candidates()
    selected = harness.selected_context_from_sources(result.sources, raw_candidates)

    assert recording.calls
    assert result.query_plan["intent"] == "roles_responsibility"
    assert result.response_kind == "answer"
    assert selected[0]["record_key"] == _t06_key()
    assert "Прокошина" not in result.answer


def test_deterministic_orchestration_valid_clarification_stops_retrieval() -> None:
    recording = harness.RecordingRetriever(StaticRetriever([_retrieved_chunk()]))
    client = FakeLlm("unused")
    generator = RagAnswerGenerator(
        retriever=recording,
        llm_client=client,
        query_analyzer=StaticAnalyzer(_clarification_plan()),
    )

    result = generator.answer("Как оформить КП?")

    assert result.response_kind == "clarification"
    assert recording.calls == []
    assert client.messages == []


def test_deterministic_orchestration_operational_plan_rejects_semantic_noise() -> None:
    recording = harness.RecordingRetriever(StaticRetriever([_retrieved_chunk()]))
    client = FakeLlm("unused")
    generator = RagAnswerGenerator(
        retriever=recording,
        llm_client=client,
        query_analyzer=StaticAnalyzer(_operational_plan()),
    )

    result = generator.answer("Какой статус заказа №12345?")

    assert result.query_plan["intent"] == "one_c_operational_lookup"
    assert result.response_kind == "no_answer"
    assert result.sources == []
    assert client.messages == []


def test_live_runner_executes_multi_turn_case_with_history() -> None:
    recording = harness.RecordingRetriever(StaticRetriever([]))
    analyzer = FallbackAnalyzer()
    generator = RagAnswerGenerator(
        retriever=recording,
        llm_client=FakeLlm("unused"),
        query_analyzer=analyzer,
    )

    diagnostic = baseline_runner._run_full_case(
        _case("CR01"),
        repeat_index=1,
        generator=generator,
        retriever=recording,
    )

    assert len(diagnostic["turns"]) == 2
    assert (
        diagnostic["resolved_question"]
        == "Как оформить коммерческое предложение?"
    )
    assert diagnostic["resolution_kind"] == "clarification_answer"
    assert diagnostic["turns"][0]["clarification"]["needed"] is True
    assert diagnostic["turns"][1]["clarification"]["needed"] is False
    assert analyzer.questions[-1] == "Как оформить коммерческое предложение?"


def test_live_runner_cli_supports_documented_parameters() -> None:
    args = baseline_runner._parse_args(
        [
            "--case-id",
            "T06",
            "--group",
            "responsibility_shipping",
            "--repeat",
            "3",
            "--model",
            "answer-model",
            "--analyzer-model",
            "analyzer-model",
            "--output-dir",
            "out",
            "--retrieval-only",
            "--verbose",
        ]
    )

    assert args.case_id == ["T06"]
    assert args.group == "responsibility_shipping"
    assert args.repeat == 3
    assert args.model == "answer-model"
    assert args.analyzer_model == "analyzer-model"
    assert args.output_dir == Path("out")
    assert args.retrieval_only is True
    assert args.verbose is True


def test_live_runner_uses_query_analyzer_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_ANALYZER_MODEL", raising=False)

    assert baseline_runner._select_analyzer_model(None) == "qwen2.5:3b"
    assert baseline_runner._select_analyzer_model("custom") == "custom"


def test_retrieval_only_runner_records_absent_layers_as_unavailable() -> None:
    case = _case("T06")
    retriever = harness.RecordingRetriever(StaticRetriever([_retrieved_chunk()]))

    diagnostic = baseline_runner._run_retrieval_only_case(
        case,
        repeat_index=1,
        retriever=retriever,
    )

    assert diagnostic["raw_candidates"][0]["record_key"] == _t06_key()
    assert diagnostic["query_plan"]["available"] is False
    assert diagnostic["selected_context"]["available"] is False


def test_live_runner_reads_native_query_plan_v2_diagnostics() -> None:
    case = _case("T06")
    recording = harness.RecordingRetriever(StaticRetriever([_retrieved_chunk()]))
    generator = RagAnswerGenerator(
        retriever=recording,
        llm_client=FakeLlm("За доставку отвечает подтверждённый контакт."),
        query_analyzer=StaticAnalyzer(_responsibility_plan()),
    )

    diagnostic = baseline_runner._run_full_case(
        case,
        repeat_index=1,
        generator=generator,
        retriever=recording,
    )

    assert diagnostic["requested_fact_type"] == "responsible_person"
    assert diagnostic["temporal_scope"] == "static"
    assert diagnostic["subject"] == "отгрузка клиенту"
    assert diagnostic["operational_boundary"] == {
        "available": True,
        "operational_lookup": False,
        "decision_reason": "static_requested_fact_type",
        "derived_from": "query_plan.operational_lookup",
        "native_decision_exposed": True,
    }


def test_blocked_live_records_do_not_become_unit_failures() -> None:
    case = _case("T09")

    records = baseline_runner._blocked_records(
        [case],
        repeat=2,
        reasons=["Ollama unavailable"],
    )

    assert len(records) == 2
    assert {record["status"] for record in records} == {"blocked"}


def test_live_runner_writes_blocked_artifacts_when_preflight_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        baseline_runner,
        "_preflight",
        lambda **_: {
            "ok": False,
            "db_exists": True,
            "retrieval_only": False,
            "ollama": {"required": True, "ok": False},
            "errors": ["Ollama unavailable"],
        },
    )

    exit_code = baseline_runner.main(
        [
            "--case-id",
            "T06",
            "--output-dir",
            str(tmp_path),
        ]
    )

    run_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert exit_code == 0
    assert len(run_dirs) == 1
    baseline = json.loads(
        (run_dirs[0] / "baseline.json").read_text(encoding="utf-8")
    )
    assert baseline["metrics"]["blocked"] == 1
    assert (run_dirs[0] / "cases.jsonl").exists()
    assert (run_dirs[0] / "summary.md").exists()


def _case(case_id: str) -> dict[str, Any]:
    fixture = harness.load_fixture(FIXTURE_PATH)
    return copy.deepcopy(next(case for case in fixture["cases"] if case["id"] == case_id))


def _diagnostic(
    *,
    intent: Any = "order_disposition",
    requested_fact_type: Any = "procedure",
    temporal_scope: Any = "static",
    clarification: bool = False,
    operational: bool = False,
    response_kind: Any = "answer",
    answer: Any = "Распоряжение оформляется письменно при первой возможности.",
    raw_candidates: Any = None,
    selected_context: Any = None,
) -> dict[str, Any]:
    return {
        "resolved_question": harness.unavailable("not implemented"),
        "query_plan": {
            "intent": intent,
            "requested_fact_type": requested_fact_type,
            "temporal_scope": temporal_scope,
        }
        if isinstance(intent, str)
        else {},
        "raw_intent": intent,
        "raw_requested_fact_type": requested_fact_type,
        "requested_fact_type": requested_fact_type,
        "temporal_scope": temporal_scope,
        "intent": intent,
        "clarification": {
            "available": True,
            "needed": clarification,
            "raw_required": clarification,
            "validated_required": clarification,
            "raw_kind": "none",
            "validated_kind": "none",
            "ambiguity_span": None,
            "candidate_meanings": [],
            "missing_slots": [],
            "action": "clarify" if clarification else "continue_retrieval",
            "retrieval_started": not clarification,
        },
        "operational_boundary": {
            "available": True,
            "operational_lookup": operational,
            "derived_from": "intent",
        },
        "retrieval_queries": [],
        "raw_candidates": [] if raw_candidates is None else raw_candidates,
        "merged_candidates": harness.unavailable("not implemented"),
        "selected_context": [] if selected_context is None else selected_context,
        "evidence_decision": harness.unavailable("not implemented"),
        "answer": answer,
        "sources": [],
        "duration_ms": 1,
        "response_kind": response_kind,
        "turns": [],
        "status": "passed",
        "failure_reasons": [],
    }


def _candidate(
    *,
    chunk_id: int | None = None,
    record_key: str | None = None,
    title: str = "bvr_company_structure_instruction_v2 (2).txt",
    source: str = "bvr_company_structure_instruction_v2 (2).txt",
    section: str = "Маршрут ответственности",
    score: float = 120.0,
    retrieval_rank: int | None = None,
    stage_hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = {
        "chunk_id": chunk_id,
        "record_key": record_key,
        "title": title,
        "source": source,
        "section": section,
        "score": score,
        "metadata": {"record_key": record_key} if record_key else {},
        "stage_hits": [] if stage_hits is None else stage_hits,
    }
    if retrieval_rank is not None:
        value["retrieval_rank"] = retrieval_rank
    return value


def _retrieved_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=594,
        title="Маршрут: ЛОГИСТИКА И СКЛАД — Доставка клиенту",
        source="bvr_company_structure_instruction_v2 (2).txt",
        section="Логистика и склад",
        page=None,
        text=(
            "По вопросам функции Доставка клиенту следует обращаться к "
            "Симоновой Татьяне. Она является первичным ответственным контактом."
        ),
        score=100.0,
        metadata={
            "doc_type": "responsibility_route",
            "knowledge_domain": "organization_structure",
            "record_key": _t06_key(),
        },
        doc_type="responsibility_route",
        base_score=100.0,
        rerank_score=20.0,
        final_score=120.0,
        matched_terms=["доставка", "клиенту"],
        matched_excerpt="Доставка клиенту — Симонова Татьяна.",
        selection_reasons=["fake deterministic evidence"],
    )


def _t06_key() -> str:
    return "responsibility_route:logistika_i_sklad_dostavka_klientu:2025-12-17"


class StaticRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.questions: list[str] = []

    def retrieve(self, question: str, **_: Any) -> list[RetrievedChunk]:
        self.questions.append(question)
        return list(self.chunks)


class StaticAnalyzer:
    last_error = None

    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan
        self.questions: list[str] = []

    def analyze(self, question: str) -> QueryPlan:
        self.questions.append(question)
        return self.plan


class FallbackAnalyzer:
    last_error = None

    def __init__(self) -> None:
        self.questions: list[str] = []

    def analyze(self, question: str) -> QueryPlan:
        self.questions.append(question)
        return fallback_query_plan(question)


class FakeLlm:
    model = "fake-model"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.messages.append(messages)
        return self.answer


def _responsibility_plan() -> QueryPlan:
    return QueryPlan(
        intent="roles_responsibility",
        normalized_question="Кто отвечает за доставку клиенту?",
        query_expansions=["ответственный за доставку клиенту"],
        preferred_sources=["bvr_company_structure_instruction_v2 (2).txt"],
        answer_type="general",
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="deterministic test plan",
        requested_fact_type="responsible_person",
        temporal_scope="static",
        subject="отгрузка клиенту",
        operational_lookup=False,
        operational_decision_reason="static_requested_fact_type",
    )


def _clarification_plan() -> QueryPlan:
    return QueryPlan(
        intent="ambiguous_abbreviation",
        normalized_question="Как оформить КП?",
        query_expansions=["КП"],
        preferred_sources=[],
        answer_type="clarification",
        needs_clarification=True,
        clarification_question="Уточните: КП или ЦКП?",
        confidence=1.0,
        notes="deterministic test plan",
    )


def _operational_plan() -> QueryPlan:
    return QueryPlan(
        intent="one_c_operational_lookup",
        normalized_question="Какой текущий статус заказа №12345?",
        query_expansions=["статус заказа"],
        preferred_sources=[],
        answer_type="partial_answer",
        needs_clarification=False,
        clarification_question=None,
        confidence=1.0,
        notes="deterministic test plan",
        requested_fact_type="current_status",
        temporal_scope="current",
        subject="заказ №12345",
        operational_lookup=True,
        operational_decision_reason="current_operational_fact_type",
    )
