"""Run the fixed Catalog Evaluation 01 set through the public chat flow.

The runner deliberately keeps every question independent while assigning the same
analytics ``session_id`` to the whole evaluation.  It never writes user feedback.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.analytics.config import AnalyticsConfig  # noqa: E402
from linehelper.analytics.interaction_logger import create_interaction_logger  # noqa: E402
from linehelper.config import load_config  # noqa: E402
from linehelper.llm.answer_generator import RagAnswer, RagAnswerGenerator  # noqa: E402


EVALUATION_NAME = "catalog_evaluation_01"
NEXT_EVALUATION_NAME = "catalog_evaluation_next"
DEFAULT_ANALYTICS_DB = PROJECT_ROOT / "data" / "analytics" / f"{EVALUATION_NAME}.db"
ALLOWED_FAILURE_REASONS = frozenset(
    {
        "wrong_source_route",
        "missed_catalog_route",
        "false_catalog_route",
        "wrong_catalog_match_type",
        "catalog_not_found",
        "catalog_noise",
        "wrong_exact_result",
        "lost_mixed_requirement",
        "wrong_answer_mode",
        "should_clarify",
        "source_missing",
        "source_incorrect",
        "generic_insufficient",
        "answer_format_issue",
        "runtime_error",
    }
)


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    category: str
    expected_route: str
    expected_match_type: str | None = None
    expected_part_number: str | None = None


CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase("A01", "X44235100", "exact", "catalog", "catalog_exact", "X44235100"),
    EvaluationCase("A02", "58803877S002", "exact", "catalog", "catalog_exact", "58803877S002"),
    EvaluationCase("A03", "H29205010648", "exact", "catalog", "catalog_exact", "H29205010648"),
    EvaluationCase("A04", "X56767951", "exact", "catalog", "catalog_exact", "X56767951"),
    EvaluationCase("A05", "ZZZ999999999", "exact_not_found", "catalog", "catalog_exact"),
    EvaluationCase("B01", "X44236", "partial_code", "catalog", "catalog_prefix"),
    EvaluationCase("B02", "x44236", "partial_code", "catalog", "catalog_normalized_prefix"),
    EvaluationCase("B03", "X-44236", "partial_code", "catalog", "catalog_normalized_prefix"),
    EvaluationCase("B04", "442351", "substring_code", "catalog", "catalog_substring", "X44235100"),
    EvaluationCase("B05", "44", "short_code", "safe"),
    EvaluationCase("C01", "прижимное устройство", "catalog_text", "catalog", "catalog_fts"),
    EvaluationCase("C02", "верхняя часть наполнителя", "catalog_text", "catalog", "catalog_fts"),
    EvaluationCase("C03", "нижняя часть укупорщика", "catalog_text", "catalog", "catalog_fts"),
    EvaluationCase("C04", "вал", "catalog_text", "catalog", "catalog_fts"),
    EvaluationCase("C05", "труба прижимного устройства", "catalog_text", "catalog", "catalog_fts"),
    EvaluationCase("C06", "инструмент прижимного устройства", "catalog_text", "catalog", "catalog_fts"),
    EvaluationCase("D01", "что ты знаешь про прижимное устройство", "catalog_natural", "catalog", "catalog_fts"),
    EvaluationCase("D02", "что есть по прижимному устройству", "catalog_natural", "catalog", "catalog_fts"),
    EvaluationCase("D03", "найди детали прижимного устройства", "catalog_natural", "catalog", "catalog_fts"),
    EvaluationCase("D04", "какие детали входят в прижимное устройство", "catalog_natural", "catalog", "catalog_fts"),
    EvaluationCase("D05", "покажи что есть по верхней части наполнителя", "catalog_natural", "catalog", "catalog_fts"),
    EvaluationCase("D06", "есть ли в каталоге вал для нижней части укупорщика", "catalog_natural", "catalog", "catalog_fts"),
    EvaluationCase("E01", "что есть по прижимному устройству и как его обслуживать", "mixed", "mixed", "catalog_fts"),
    EvaluationCase("E02", "что известно про наполнитель и как его обслуживать", "mixed", "mixed", "catalog_fts"),
    EvaluationCase("E03", "какие детали входят в прижимное устройство и что с ним надо делать при обслуживании", "mixed", "mixed", "catalog_fts"),
    EvaluationCase("E04", "найди детали нижней части укупорщика и расскажи порядок обслуживания", "mixed", "mixed", "catalog_fts"),
    EvaluationCase("E05", "что стоит в верхней части наполнителя и есть ли инструкция по работе с этим узлом", "mixed", "mixed", "catalog_fts"),
    EvaluationCase("F01", "можно ли дать распоряжение устно", "corporate", "corporate"),
    EvaluationCase("F02", "кто отвечает за документооборот", "corporate", "corporate"),
    EvaluationCase("F03", "что такое ЗРС", "corporate", "corporate"),
    EvaluationCase("F04", "какие цели компании Serviceline", "corporate", "corporate"),
    EvaluationCase("F05", "как начать работу в новой должности", "corporate", "corporate"),
    EvaluationCase("G01", "устройство", "ambiguous", "safe"),
    EvaluationCase("G02", "вал", "ambiguous", "safe"),
    EvaluationCase("G03", "позиция 20", "ambiguous", "safe"),
    EvaluationCase("G04", "что это такое", "ambiguous", "safe"),
    EvaluationCase("G05", "как это обслуживать", "ambiguous", "safe"),
)

# Mandatory acceptance set for the next evaluation.  It is intentionally kept
# separate from the completed 37-case Evaluation 01 artifact: adding it here
# does not rerun or rewrite that historical measurement.
NEXT_STRUCTURED_CATALOG_CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase("H01", "Узел: 20411617", "structured_catalog", "catalog", "catalog_assembly_exact"),
    EvaluationCase("H02", "что входит в узел 20411617", "structured_catalog", "catalog", "catalog_assembly_contents"),
    EvaluationCase("H03", "состав узла 20411617", "structured_catalog", "catalog", "catalog_assembly_contents"),
    EvaluationCase("H04", "позиция 20 узла 20411617", "structured_catalog", "catalog", "catalog_bom_position", "58803877S002"),
    EvaluationCase("H05", "деталь X44235100", "structured_catalog", "catalog", "catalog_part_exact", "X44235100"),
    EvaluationCase("H06", "где используется X44235100", "structured_catalog", "catalog", "catalog_part_exact", "X44235100"),
    EvaluationCase("H07", "20411617", "structured_catalog", "catalog", "catalog_entity_ambiguous", "20411617"),
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_case_set()
    active_cases = (
        CASES
        if args.case_set == "evaluation-01"
        else CASES + NEXT_STRUCTURED_CATALOG_CASES
    )
    evaluation_name = (
        EVALUATION_NAME
        if args.case_set == "evaluation-01"
        else NEXT_EVALUATION_NAME
    )
    if args.list_cases:
        for case in active_cases:
            print(f"{case.case_id}\t{case.expected_route}\t{case.question}")
        print(f"total={len(active_cases)}")
        return 0
    if args.regrade_results is not None:
        regrade_results_file(args.regrade_results.resolve())
        print(f"regraded={args.regrade_results.resolve()}")
        return 0

    started_local = datetime.now().astimezone()
    stamp = started_local.strftime("%Y%m%dT%H%M%S%z")
    run_id = args.run_id or f"{evaluation_name}_{stamp}"
    output_dir = args.output_dir or (
        PROJECT_ROOT / "data" / "test_runs" / evaluation_name / stamp
    )
    output_dir = output_dir.resolve()
    analytics_db = args.analytics_db.resolve()
    _ensure_fresh_session(analytics_db, run_id)
    results_path = output_dir / "results.json"
    if results_path.exists():
        raise SystemExit(f"Refusing to overwrite an existing run: {results_path}")
    output_dir.mkdir(parents=True, exist_ok=False)

    app_config = load_config()
    analytics_config = AnalyticsConfig(
        enabled=True,
        db_path=analytics_db,
        retention_days=0,
        store_text=True,
        redact_pii=True,
        queue_size=100,
        user_hash_secret=None,
    )
    interaction_logger = create_interaction_logger(
        analytics_config,
        project_root=PROJECT_ROOT,
    )
    if not interaction_logger.enabled:
        raise SystemExit(f"Evaluation analytics unavailable: {interaction_logger.last_error}")

    generator = RagAnswerGenerator(
        db_path=app_config.db_path,
        catalog_db_path=app_config.catalog_db_path,
        catalog_source_root=app_config.catalog_source_root,
        interaction_logger=interaction_logger,
    )
    payload: dict[str, Any] = {
        "evaluation": evaluation_name,
        "run_id": run_id,
        "started_at": started_local.isoformat(),
        "finished_at": None,
        "public_flow": "RagAnswerGenerator.answer",
        "conversation_history_used": False,
        "analytics_db": str(analytics_db),
        "git": _git_snapshot(),
        "cases": [],
        "summary": None,
    }
    _write_json(results_path, payload)

    try:
        for position, case in enumerate(active_cases, start=1):
            print(f"[{position:02d}/{len(active_cases)}] {case.case_id}: {case.question}", flush=True)
            case_started = time.monotonic()
            try:
                answer = generator.answer(case.question, session_id=run_id)
                record = build_result_record(case, answer)
            except Exception as exc:  # one failed interaction must not erase the run
                record = build_error_record(
                    case,
                    exc,
                    duration_ms=round((time.monotonic() - case_started) * 1000.0, 3),
                )
            payload["cases"].append(record)
            _write_json(results_path, payload)
            print(
                f"  route={record['actual_route']} mode={record['answer_mode']} "
                f"grade={record['technical_grade']} duration_ms={record['duration_ms']}",
                flush=True,
            )
    finally:
        interaction_logger.close()

    payload["finished_at"] = datetime.now().astimezone().isoformat()
    payload["summary"] = summarize(payload["cases"])
    payload["analytics"] = _analytics_session_snapshot(analytics_db, run_id)
    _write_json(results_path, payload)
    _write_json(output_dir / "summary.json", payload["summary"])
    _write_failures(output_dir / "failures.csv", payload["cases"])
    print(f"run_id={run_id}")
    print(f"results={results_path}")
    print(json.dumps(payload["summary"]["overall"], ensure_ascii=False))
    return 0


def build_result_record(case: EvaluationCase, result: RagAnswer) -> dict[str, Any]:
    query_plan = dict(result.query_plan or {})
    catalog = dict(result.catalog or {})
    retrieval = dict(result.retrieval or {})
    evidence = dict(result.evidence or {})
    answer_contract = dict(result.answer_contract or {})
    actual_route = _actual_route(result, query_plan)
    answer_mode = _answer_mode(
        actual_route=_actual_route(result, query_plan),
        query_plan=query_plan,
        evidence=evidence,
        answer_contract=answer_contract,
        response_kind=result.response_kind,
    )
    catalog_match_type = query_plan.get("catalog_match_type") or catalog.get("match_type")
    catalog_results = catalog.get("results") if isinstance(catalog.get("results"), list) else []
    catalog_count = int(
        catalog.get("result_count")
        or query_plan.get("catalog_result_count")
        or query_plan.get("catalog_probe_result_count")
        or len(catalog_results)
        or 0
    )
    retrieved_ids = _candidate_ids(retrieval)
    supporting_ids = list(evidence.get("supporting_chunk_ids") or [])
    catalog_sources = [_jsonable(source) for source in result.catalog_sources]
    corporate_sources = [_jsonable(source) for source in result.sources]

    record: dict[str, Any] = {
        "case_id": case.case_id,
        "question": case.question,
        "category": case.category,
        "expected_route": case.expected_route,
        "actual_route": actual_route,
        "response_kind": result.response_kind,
        "answer_mode": answer_mode,
        "requested_fact_type": (
            query_plan.get("finalized_requested_fact_type")
            or query_plan.get("requested_fact_type")
        ),
        "resolved_requirements": _jsonable(query_plan.get("resolved_requirements") or []),
        "catalog_probe_performed": bool(query_plan.get("catalog_probe_performed", False)),
        "catalog_match_type": catalog_match_type,
        "catalog_result_count": catalog_count,
        "catalog_field_coverage": query_plan.get("catalog_top_field_coverage"),
        "catalog_top_score": query_plan.get("catalog_top_score"),
        "retrieved_chunk_ids": retrieved_ids,
        "supporting_chunk_ids": supporting_ids,
        "final_answer": result.answer,
        "catalog_source_count": len(catalog_sources),
        "corporate_source_count": len(corporate_sources),
        "catalog_sources": catalog_sources,
        "corporate_sources": corporate_sources,
        "catalog_results": _jsonable(catalog_results),
        "query_plan": _jsonable(query_plan),
        "retrieval": _jsonable(retrieval),
        "evidence": _jsonable(evidence),
        "duration_ms": round(result.elapsed_seconds * 1000.0, 3),
        "error": result.analytics_error,
        "fallback": _fallback_kind(result, query_plan),
        "interaction_id": result.interaction_id,
        "analytics_logged": result.analytics_logged,
    }
    grade, reasons = grade_record(case, record)
    record["technical_grade"] = grade
    record["failure_reasons"] = reasons
    return record


def build_error_record(
    case: EvaluationCase,
    exc: Exception,
    *,
    duration_ms: float,
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "question": case.question,
        "category": case.category,
        "expected_route": case.expected_route,
        "actual_route": "error",
        "response_kind": "runtime_error",
        "answer_mode": "runtime_error",
        "requested_fact_type": None,
        "resolved_requirements": [],
        "catalog_probe_performed": False,
        "catalog_match_type": None,
        "catalog_result_count": 0,
        "catalog_field_coverage": None,
        "catalog_top_score": None,
        "retrieved_chunk_ids": [],
        "supporting_chunk_ids": [],
        "final_answer": None,
        "catalog_source_count": 0,
        "corporate_source_count": 0,
        "catalog_sources": [],
        "corporate_sources": [],
        "catalog_results": [],
        "query_plan": {},
        "retrieval": {},
        "evidence": {},
        "duration_ms": duration_ms,
        "error": f"{type(exc).__name__}: {exc}",
        "fallback": None,
        "interaction_id": None,
        "analytics_logged": False,
        "technical_grade": "FAIL",
        "failure_reasons": ["runtime_error"],
    }


def grade_record(case: EvaluationCase, record: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    actual_route = record["actual_route"]
    results = record["catalog_results"]
    result_codes = [str(item.get("part_number") or "") for item in results]
    result_count = int(record["catalog_result_count"] or 0)
    match_type = record["catalog_match_type"]

    if record.get("error") and not record.get("analytics_logged"):
        reasons.append("runtime_error")
    if case.expected_route in {"catalog", "corporate", "mixed"} and actual_route != case.expected_route:
        reasons.append("wrong_source_route")
        if case.expected_route in {"catalog", "mixed"}:
            reasons.append("missed_catalog_route")
        if case.expected_route == "corporate" and actual_route == "catalog":
            reasons.append("false_catalog_route")

    if case.category == "exact":
        if not results:
            reasons.append("catalog_not_found")
        elif case.expected_part_number not in result_codes:
            reasons.append("wrong_exact_result")
        if match_type not in {"catalog_exact", "catalog_normalized_exact"}:
            reasons.append("wrong_catalog_match_type")
        if case.case_id == "A01" and results:
            expected = {
                "part_number": "X44235100",
                "assembly_code": "X44236986",
                "position": "70",
                "source_page": 467,
            }
            row = next((item for item in results if item.get("part_number") == "X44235100"), {})
            if any(str(row.get(key)) != str(value) for key, value in expected.items()):
                reasons.append("wrong_exact_result")
            if not _decimal_equal(row.get("quantity"), "4"):
                reasons.append("wrong_exact_result")
        if record["catalog_source_count"] == 0:
            reasons.append("source_missing")

    elif case.category == "exact_not_found":
        if results or result_count:
            reasons.append("catalog_noise")
        if match_type not in {"catalog_exact", None}:
            reasons.append("wrong_catalog_match_type")
        if record["response_kind"] not in {
            "catalog_not_found",
            "catalog_search_not_found",
        }:
            reasons.append("wrong_answer_mode")
        if "не найден" not in str(record.get("final_answer") or "").casefold():
            reasons.append("answer_format_issue")

    elif case.category in {"partial_code", "substring_code"}:
        if not results:
            reasons.append("catalog_not_found")
        if match_type != case.expected_match_type:
            reasons.append("wrong_catalog_match_type")
        if case.expected_part_number and case.expected_part_number not in result_codes:
            reasons.append("catalog_not_found")
        if case.case_id == "B01" and "301134230120" in result_codes:
            reasons.append("catalog_noise")
        if case.case_id in {"B01", "B02", "B03"}:
            prefix = "X44236"
            if any(not _compact_code(code).startswith(prefix) for code in result_codes):
                reasons.append("catalog_noise")

    elif case.category == "short_code":
        if match_type in {"catalog_substring", "catalog_fts"} or result_count > 0:
            reasons.append("catalog_noise")
        if record["response_kind"] not in {
            "clarification", "no_answer", "catalog_not_found", "catalog_search_not_found"
        }:
            reasons.append("should_clarify")

    elif case.category in {"catalog_text", "catalog_natural"}:
        if not results:
            reasons.append("catalog_not_found")
        if match_type != "catalog_fts":
            reasons.append("wrong_catalog_match_type")
        if results and record["catalog_source_count"] == 0:
            reasons.append("source_missing")

    elif case.category == "mixed":
        requirements = record.get("resolved_requirements") or []
        sources = {item.get("source") for item in requirements if isinstance(item, dict)}
        if "catalog" not in sources or "corporate" not in sources:
            reasons.append("lost_mixed_requirement")
        if not results:
            reasons.append("catalog_not_found")
        if record["answer_mode"] not in {"partial_mixed", "full_mixed"}:
            reasons.append("wrong_answer_mode")
        if results and record["catalog_source_count"] == 0:
            reasons.append("source_missing")
        if results and _is_generic_insufficient(record.get("final_answer")):
            reasons.append("generic_insufficient")
        if case.case_id == "E05" and record["corporate_source_count"]:
            source_text = " ".join(
                " ".join(
                    str(source.get(key) or "")
                    for key in ("title", "section")
                )
                for source in record.get("corporate_sources") or []
                if isinstance(source, dict)
            ).casefold()
            if not any(stem in source_text for stem in ("наполнител", "обслужив")):
                reasons.append("source_incorrect")

    elif case.category == "corporate":
        if actual_route == "catalog" or results:
            reasons.append("false_catalog_route")
        if record["corporate_source_count"] == 0:
            reasons.append("source_missing")
        if _is_generic_insufficient(record.get("final_answer")):
            reasons.append("generic_insufficient")

    elif case.category == "ambiguous":
        overconfident = (
            record["response_kind"] in {"catalog_exact", "answer", "mixed_answer"}
            and not _is_generic_insufficient(record.get("final_answer"))
            and record["response_kind"] != "clarification"
        )
        controlled_candidates = record["response_kind"] in {
            "catalog_candidates", "catalog_search_clarification", "catalog_search_not_found"
        }
        if overconfident and not controlled_candidates:
            reasons.append("should_clarify")

    elif case.category == "structured_catalog":
        if match_type != case.expected_match_type:
            reasons.append("wrong_catalog_match_type")
        if not results:
            reasons.append("catalog_not_found")
        if case.expected_part_number and case.expected_part_number not in result_codes:
            reasons.append("wrong_exact_result")
        if record["corporate_source_count"] or any(
            stage == "corporate"
            for stage in (record.get("retrieval") or {}).get("retrieval_stages", [])
        ):
            reasons.append("false_catalog_route")

    reasons = _dedupe(reasons)
    unknown = set(reasons) - ALLOWED_FAILURE_REASONS
    if unknown:
        raise AssertionError(f"Unsupported failure reasons: {sorted(unknown)}")
    severe = {
        "runtime_error",
        "wrong_source_route",
        "missed_catalog_route",
        "false_catalog_route",
        "wrong_exact_result",
        "catalog_not_found",
        "lost_mixed_requirement",
        "catalog_noise",
        "should_clarify",
        "source_incorrect",
    }
    if any(reason in severe for reason in reasons):
        return "FAIL", reasons
    if reasons:
        return "PARTIAL", reasons
    return "PASS", []


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    grades = {grade: sum(item["technical_grade"] == grade for item in cases) for grade in ("PASS", "PARTIAL", "FAIL")}
    total = len(cases)
    routable = [item for item in cases if item["expected_route"] in {"catalog", "corporate", "mixed"}]
    routing_correct = sum(item["actual_route"] == item["expected_route"] for item in routable)
    routes: dict[str, Any] = {}
    for route in ("catalog", "corporate", "mixed"):
        expected = [item for item in cases if item["expected_route"] == route]
        routes[route] = {
            "expected": len(expected),
            "correct": sum(item["actual_route"] == route for item in expected),
        }
    reasons: dict[str, dict[str, Any]] = {}
    for item in cases:
        if item["technical_grade"] == "PASS":
            continue
        for reason in item["failure_reasons"]:
            entry = reasons.setdefault(reason, {"count": 0, "case_ids": []})
            entry["count"] += 1
            entry["case_ids"].append(item["case_id"])
    reason_total = sum(item["count"] for item in reasons.values())
    pareto = [
        {
            "reason": reason,
            "count": details["count"],
            "error_share": round(details["count"] / reason_total, 4) if reason_total else 0.0,
            "case_ids": details["case_ids"],
        }
        for reason, details in sorted(reasons.items(), key=lambda pair: (-pair[1]["count"], pair[0]))
    ]
    summary = {
        "overall": {
            "total": total,
            **grades,
            "strict_pass_rate": _rate(grades["PASS"], total),
            "technical_success_rate": _rate(grades["PASS"] + grades["PARTIAL"], total),
            "technical_success_definition": "(PASS + PARTIAL) / total",
        },
        "routing": {
            "evaluated": len(routable),
            "correct": routing_correct,
            "accuracy": _rate(routing_correct, len(routable)),
            "by_expected_route": routes,
            "false_catalog_routes": sum("false_catalog_route" in item["failure_reasons"] for item in cases),
            "missed_catalog_routes": sum("missed_catalog_route" in item["failure_reasons"] for item in cases),
        },
        "categories": {
            category: _category_summary([item for item in cases if item["category"] == category])
            for category in sorted({item["category"] for item in cases})
        },
        "sources": _source_summary(cases),
        "latency": {
            route: _latency_summary([item["duration_ms"] for item in cases if item["actual_route"] == route])
            for route in ("catalog", "corporate", "mixed")
        },
        "failure_pareto": pareto,
    }
    structured = [item for item in cases if item["category"] == "structured_catalog"]
    if structured:
        natural = [item for item in cases if item["category"] == "catalog_natural"]
        mixed = [item for item in cases if item["category"] == "mixed"]
        corporate = [item for item in cases if item["category"] == "corporate"]
        structured_passed = sum(item["technical_grade"] == "PASS" for item in structured)
        structured_leakage = sum(
            item["corporate_source_count"] > 0
            or any(
                stage == "corporate"
                for stage in (item.get("retrieval") or {}).get("retrieval_stages", [])
            )
            for item in structured
        )
        gate_checks = {
            "fail_le_2": grades["FAIL"] <= 2,
            "routing_accuracy_ge_93_percent": _rate(routing_correct, len(routable)) is not None
            and routing_correct / len(routable) >= 0.93,
            "natural_catalog_ge_5_of_6": sum(item["technical_grade"] == "PASS" for item in natural) >= 5,
            "mixed_ge_4_of_5": sum(item["technical_grade"] == "PASS" for item in mixed) >= 4,
            "corporate_regression_5_of_5": len(corporate) == 5
            and all(item["actual_route"] == "corporate" for item in corporate),
            "false_catalog_routes_eq_0": summary["routing"]["false_catalog_routes"] == 0,
            "structured_catalog_intents_7_of_7": len(structured) == 7
            and structured_passed == 7,
            "structured_catalog_corporate_leakage_eq_0": structured_leakage == 0,
        }
        summary["acceptance_gate"] = {
            "passed": all(gate_checks.values()),
            "checks": gate_checks,
            "structured_catalog_passed": structured_passed,
            "structured_catalog_total": len(structured),
            "structured_catalog_corporate_leakage": structured_leakage,
        }
    return summary


def _category_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(items),
        "PASS": sum(item["technical_grade"] == "PASS" for item in items),
        "PARTIAL": sum(item["technical_grade"] == "PARTIAL" for item in items),
        "FAIL": sum(item["technical_grade"] == "FAIL" for item in items),
    }


def _source_summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    catalog_answers = [item for item in cases if item.get("catalog_results")]
    corporate_answers = [
        item for item in cases
        if item["actual_route"] in {"corporate", "mixed"}
        and (
            item["answer_mode"] in {"full_answer", "partial_answer", "full_mixed"}
            or any(
                isinstance(requirement, dict)
                and requirement.get("source") == "corporate"
                and requirement.get("status") == "found"
                for requirement in item.get("resolved_requirements") or []
            )
        )
    ]
    incorrect = [
        item for item in catalog_answers + corporate_answers
        if "source_incorrect" in item["failure_reasons"]
    ]
    required = len(catalog_answers) + len(corporate_answers)
    with_sources = sum(item["catalog_source_count"] > 0 for item in catalog_answers) + sum(
        item["corporate_source_count"] > 0 for item in corporate_answers
    )
    return {
        "catalog_answers": len(catalog_answers),
        "catalog_with_sources": sum(item["catalog_source_count"] > 0 for item in catalog_answers),
        "corporate_answers_requiring_sources": len(corporate_answers),
        "corporate_with_sources": sum(item["corporate_source_count"] > 0 for item in corporate_answers),
        "required": required,
        "with_sources": with_sources,
        "correct": max(0, with_sources - len(incorrect)),
        "incorrect": len(incorrect),
        "missing": max(0, required - with_sources),
        "misleading_empty_blocks": sum(
            bool(item.get("catalog_results"))
            and item["catalog_source_count"] == 0
            and item["corporate_source_count"] == 0
            for item in cases
        ),
    }


def _actual_route(result: RagAnswer, query_plan: dict[str, Any]) -> str:
    route = str(query_plan.get("source_route") or "").strip()
    if route in {"catalog", "corporate", "mixed"}:
        return route
    if result.catalog is not None:
        return "catalog"
    return "corporate"


def _answer_mode(
    *,
    actual_route: str,
    query_plan: dict[str, Any],
    evidence: dict[str, Any],
    answer_contract: dict[str, Any],
    response_kind: str,
) -> str:
    """Prefer evidence mode for corporate flow and source mode for catalog/mixed."""
    if actual_route in {"catalog", "mixed"} and query_plan.get("answer_mode"):
        return str(query_plan["answer_mode"])
    return str(
        evidence.get("answer_mode")
        or answer_contract.get("answer_mode")
        or query_plan.get("answer_mode")
        or response_kind
    )


def _fallback_kind(result: RagAnswer, query_plan: dict[str, Any]) -> str | None:
    if result.response_kind in {"no_answer", "partial_mixed_answer"}:
        return result.response_kind
    if query_plan.get("catalog_probe_performed") and query_plan.get("source_route") == "corporate":
        return "catalog_probe_rejected"
    return None


def _candidate_ids(retrieval: dict[str, Any]) -> list[Any]:
    values = retrieval.get("candidate_order") or []
    result: list[Any] = []
    for item in values:
        value = item.get("chunk_id") if isinstance(item, dict) else item
        if value is not None and value not in result:
            result.append(value)
    corporate = retrieval.get("corporate")
    if isinstance(corporate, dict):
        for value in _candidate_ids(corporate):
            if value not in result:
                result.append(value)
    return result


def _analytics_session_snapshot(db_path: Path, run_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        interaction_count = connection.execute(
            "SELECT COUNT(*) FROM interactions WHERE session_id = ?",
            (run_id,),
        ).fetchone()[0]
        feedback_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM interaction_feedback AS feedback
            JOIN interactions AS interaction
              ON interaction.interaction_id = feedback.interaction_id
            WHERE interaction.session_id = ?
            """,
            (run_id,),
        ).fetchone()[0]
    return {
        "session_id": run_id,
        "interactions_logged": int(interaction_count),
        "feedback_events": int(feedback_count),
        "evaluation_feedback_fabricated": False,
    }


def _ensure_fresh_session(db_path: Path, run_id: str) -> None:
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as connection:
        existing = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'interactions'"
        ).fetchone()[0]
        if not existing:
            return
        count = connection.execute(
            "SELECT COUNT(*) FROM interactions WHERE session_id = ?",
            (run_id,),
        ).fetchone()[0]
    if count:
        raise SystemExit(
            f"Session {run_id!r} already contains {count} interactions; repeat run refused"
        )


def _git_snapshot() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout.rstrip()

    return {
        "branch": run("branch", "--show-current"),
        "head": run("rev-parse", "HEAD"),
        "status_short": run("status", "--short").splitlines(),
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--analytics-db", type=Path, default=DEFAULT_ANALYTICS_DB)
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument(
        "--case-set",
        choices=("evaluation-01", "next"),
        default="evaluation-01",
        help="Keep Evaluation 01 immutable; 'next' appends the 7 structured-intent gates.",
    )
    parser.add_argument(
        "--regrade-results",
        type=Path,
        help="Recompute grading/summary from a completed artifact without rerunning questions.",
    )
    return parser.parse_args(argv)


def regrade_results_file(path: Path) -> None:
    """Rebuild derived grading after manual audit without executing chat again."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    all_cases = CASES + NEXT_STRUCTURED_CATALOG_CASES
    by_id = {case.case_id: case for case in all_cases}
    records = payload.get("cases") or []
    if len(records) not in {len(CASES), len(all_cases)}:
        raise ValueError(
            f"Expected {len(CASES)} or {len(all_cases)} completed cases, got {len(records)}"
        )
    for record in records:
        case = by_id[str(record["case_id"])]
        record["answer_mode"] = _answer_mode(
            actual_route=str(record["actual_route"]),
            query_plan=dict(record.get("query_plan") or {}),
            evidence=dict(record.get("evidence") or {}),
            answer_contract={},
            response_kind=str(record.get("response_kind") or ""),
        )
        if record.get("response_kind") == "runtime_error":
            record["technical_grade"] = "FAIL"
            record["failure_reasons"] = ["runtime_error"]
        else:
            grade, reasons = grade_record(case, record)
            record["technical_grade"] = grade
            record["failure_reasons"] = reasons
    payload["summary"] = summarize(records)
    _write_json(path, payload)
    _write_json(path.parent / "summary.json", payload["summary"])
    _write_failures(path.parent / "failures.csv", records)


def _validate_case_set() -> None:
    ids = [case.case_id for case in CASES]
    if len(CASES) != 37 or len(ids) != len(set(ids)):
        raise RuntimeError("Catalog Evaluation 01 must contain exactly 37 unique cases")
    next_ids = [case.case_id for case in NEXT_STRUCTURED_CATALOG_CASES]
    if len(next_ids) != 7 or len(next_ids) != len(set(next_ids)):
        raise RuntimeError("Next evaluation must contain 7 unique structured cases")
    if set(ids) & set(next_ids):
        raise RuntimeError("Evaluation 01 and next structured case IDs must not overlap")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_failures(path: Path, cases: list[dict[str, Any]]) -> None:
    rows = [item for item in cases if item["technical_grade"] != "PASS"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "case_id", "question", "expected_route", "actual_route",
                "technical_grade", "failure_reasons", "interaction_id",
            ),
        )
        writer.writeheader()
        for item in rows:
            writer.writerow(
                {
                    "case_id": item["case_id"],
                    "question": item["question"],
                    "expected_route": item["expected_route"],
                    "actual_route": item["actual_route"],
                    "technical_grade": item["technical_grade"],
                    "failure_reasons": ",".join(item["failure_reasons"]),
                    "interaction_id": item["interaction_id"],
                }
            )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "median_ms": round(statistics.median(ordered), 3) if ordered else None,
        "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 3) if ordered else None,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _compact_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _decimal_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False


def _is_generic_insufficient(answer: Any) -> bool:
    text = str(answer or "").casefold()
    generic = (
        "в найденных источниках недостаточно данных",
        "в базе знаний serviceline нет точного ответа",
    )
    has_catalog_facts = any(marker in text for marker in ("по каталогу", "детал", "узел:", "позиция:"))
    return any(marker in text for marker in generic) and not has_catalog_facts


if __name__ == "__main__":
    raise SystemExit(main())
