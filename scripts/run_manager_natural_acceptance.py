"""Run an independent manager-language acceptance pack through production chat.

The runner is diagnostic-only: it calls ``RagAnswerGenerator.answer`` exactly as
the CLI/Streamlit path does, records the returned production structures and never
writes user feedback or changes production data.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
import json
from pathlib import Path
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


PACK_NAME = "manager_natural_acceptance"
GROUP_LABELS = {
    "A": "Catalog natural",
    "B": "Corporate natural",
    "C": "Mixed",
    "D": "Ambiguous",
    "E": "Partial answers",
    "F": "NOT_FOUND",
    "G": "Domain guard",
    "H": "Human noise",
}
FAILURE_LAYERS = frozenset(
    {
        "UNDERSTANDING",
        "ROUTING",
        "CATALOG_RETRIEVAL",
        "CORPORATE_RETRIEVAL",
        "EVIDENCE_GUARD",
        "MIXED_COMPOSITION",
        "FINAL_ANSWER",
        "UNKNOWN",
    }
)


@dataclass(frozen=True)
class ExpectedOutcome:
    routes: tuple[str, ...]
    catalog_states: tuple[str, ...]
    corporate_states: tuple[str, ...]
    final_modes: tuple[str, ...]
    must_include_entities: tuple[str, ...] = ()
    must_not_include_entities: tuple[str, ...] = ()
    subject_terms: tuple[str, ...] = ()
    entity_terms: tuple[str, ...] = ()
    assembly_context_terms: tuple[str, ...] = ()
    catalog_match_types: tuple[str, ...] = ()
    corporate_anchor_terms: tuple[str, ...] = ()
    forbidden_corporate_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class AcceptanceCase:
    case_id: str
    group: str
    query: str
    expected: ExpectedOutcome


def _expected(
    route: str | tuple[str, ...],
    catalog: str | tuple[str, ...],
    corporate: str | tuple[str, ...],
    mode: str | tuple[str, ...],
    **kwargs: Any,
) -> ExpectedOutcome:
    return ExpectedOutcome(
        routes=(route,) if isinstance(route, str) else route,
        catalog_states=(catalog,) if isinstance(catalog, str) else catalog,
        corporate_states=(corporate,) if isinstance(corporate, str) else corporate,
        final_modes=(mode,) if isinstance(mode, str) else mode,
        **kwargs,
    )


CASES: tuple[AcceptanceCase, ...] = (
    # A — catalog questions phrased as ordinary manager requests.
    AcceptanceCase(
        "A01", "A", "Подбери вал, который установлен в нижней части укупорщика.",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("X44235100",), subject_terms=("вал", "укупорщик")),
    ),
    AcceptanceCase(
        "A02", "A", "Перечисли комплектующие прижимного устройства.",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("58803877S002",), subject_terms=("прижим", "устройств")),
    ),
    AcceptanceCase(
        "A03", "A", "Что установлено в верхней части наполнителя?",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("20411640",), subject_terms=("верхн", "наполнител")),
    ),
    AcceptanceCase(
        "A04", "A", "Мне нужны детали, входящие в узел 20411617.",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("58803877S002",), subject_terms=("20411617",)),
    ),
    AcceptanceCase(
        "A05", "A", "Покажи варианты деталей с началом кода X44235.",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("X44235100",), catalog_match_types=("catalog_prefix", "catalog_normalized_prefix", "catalog_substring")),
    ),
    AcceptanceCase(
        "A06", "A", "Поищи деталь, в коде которой встречается 442351.",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("X44235100",), catalog_match_types=("catalog_substring",)),
    ),

    # B — established corporate topics, deliberately paraphrased.
    AcceptanceCase(
        "B01", "B", "Если распоряжение дали устно, как менеджеру затем его оформить?",
        _expected("corporate", "not_requested", "found", "corporate_found", corporate_anchor_terms=("распоряж", "ип-0005")),
    ),
    AcceptanceCase(
        "B02", "B", "Как по нашим правилам согласовать договор перед подписанием?",
        _expected("corporate", "not_requested", "found", "corporate_found", corporate_anchor_terms=("договор", "согласован", "документооборот")),
    ),
    AcceptanceCase(
        "B03", "B", "Объясни менеджеру, что означает завершённая работа сотрудника — ЗРС.",
        _expected("corporate", "not_requested", "found", "corporate_found", corporate_anchor_terms=("зрс", "завершенн")),
    ),
    AcceptanceCase(
        "B04", "B", "Как сформулирован ЦКП компании Serviceline?",
        _expected("corporate", "not_requested", "found", "corporate_found", corporate_anchor_terms=("цкп", "ценн", "serviceline")),
    ),
    AcceptanceCase(
        "B05", "B", "Какие действия ожидаются от сотрудника при вступлении в новую должность?",
        _expected("corporate", "not_requested", "found", ("corporate_found", "corporate_partial"), corporate_anchor_terms=("должност", "новой", "сотрудник")),
    ),

    # C — two independent needs; a grounded partial_mixed is valid.
    AcceptanceCase(
        "C01", "C", "Найди вал в нижней части укупорщика и проверь, есть ли инструкция по его обслуживанию.",
        _expected("mixed", "found", ("found", "not_found"), ("full_mixed", "partial_mixed"), must_include_entities=("X44235100",), subject_terms=("вал", "укупорщик")),
    ),
    AcceptanceCase(
        "C02", "C", "Покажи комплектующие прижимного устройства и уточни, имеется ли порядок его ремонта.",
        _expected("mixed", "found", ("found", "not_found"), ("full_mixed", "partial_mixed"), must_include_entities=("58803877S002",), subject_terms=("прижим", "устройств")),
    ),
    AcceptanceCase(
        "C03", "C", "Что установлено в верхней части наполнителя, и есть ли документ по обслуживанию этого узла?",
        _expected("mixed", "found", ("found", "not_found"), ("full_mixed", "partial_mixed"), must_include_entities=("20411640",), subject_terms=("верхн", "наполнител")),
    ),
    AcceptanceCase(
        "C04", "C", "Перечисли детали нижней части укупорщика; потом проверь инструкцию по их обслуживанию.",
        _expected("mixed", "found", ("found", "not_found"), ("full_mixed", "partial_mixed"), must_include_entities=("X44236986",), subject_terms=("нижн", "укупорщик")),
    ),
    AcceptanceCase(
        "C05", "C", "Найди трубу прижимного устройства и сообщи, описана ли её замена в документах.",
        _expected("mixed", "found", ("found", "not_found"), ("full_mixed", "partial_mixed"), must_include_entities=("H29205010648",), subject_terms=("труб", "прижим")),
    ),

    # D — lack of a referent must remain safe, not authoritative.
    AcceptanceCase(
        "D01", "D", "Что есть по этому валу?",
        _expected(("corporate", "safe"), ("not_requested", "not_found"), "not_found", ("clarification", "not_found")),
    ),
    AcceptanceCase(
        "D02", "D", "Покажи сведения об этом узле.",
        _expected(("corporate", "safe"), ("not_requested", "not_found"), "not_found", ("clarification", "not_found")),
    ),
    AcceptanceCase(
        "D03", "D", "Какая у него позиция?",
        _expected(("corporate", "safe"), ("not_requested", "not_found"), "not_found", ("clarification", "not_found")),
    ),
    AcceptanceCase(
        "D04", "D", "Нужна информация по детали.",
        _expected(("corporate", "safe"), ("not_requested", "not_found"), "not_found", ("clarification", "not_found")),
    ),

    # E — explicit partial-answer contracts, including the reverse direction.
    AcceptanceCase(
        "E01", "E", "Что есть в прижимном устройстве и предусмотрена ли процедура его калибровки?",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("58803877S002",)),
    ),
    AcceptanceCase(
        "E02", "E", "Покажи верхнюю часть наполнителя и проверь наличие инструкции по её чистке.",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("20411640",)),
    ),
    AcceptanceCase(
        "E03", "E", "Найди вал нижней части укупорщика и скажи, есть ли регламент его смазки.",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("X44235100",)),
    ),
    AcceptanceCase(
        "E04", "E", "Проверь каталог на узел КАДРЫ-001 и напомни правила начала работы в новой должности.",
        _expected("mixed", "not_found", "found", "partial_mixed", corporate_anchor_terms=("должност", "сотрудник")),
    ),

    # F — honest absence is success; weak evidence is not.
    AcceptanceCase(
        "F01", "F", "Код детали ZQX777777.",
        _expected("catalog", "not_found", "not_requested", "not_found", must_not_include_entities=("ZQX777777",)),
    ),
    AcceptanceCase(
        "F02", "F", "Узел: 99999998",
        _expected("catalog", "not_found", "not_requested", "not_found", must_not_include_entities=("99999998",)),
    ),
    AcceptanceCase(
        "F03", "F", "Поиск по каталогу: квантовый датчик розлива",
        _expected("catalog", "not_found", "not_requested", "not_found"),
    ),
    AcceptanceCase(
        "F04", "F", "Есть ли утверждённая процедура телепортации сотрудников между офисами?",
        _expected("corporate", "not_requested", "not_found", "not_found", forbidden_corporate_terms=("командиров", "отпуск", "документооборот")),
    ),

    # G — attractive generic words must not admit unrelated corporate chunks.
    AcceptanceCase(
        "G01", "G", "Что стоит в верхней части наполнителя и есть ли порядок работы с рабочим столом этого узла?",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("20411640",), forbidden_corporate_terms=("рабочий стол", "корзин", "оргсхем")),
    ),
    AcceptanceCase(
        "G02", "G", "Какие детали входят в прижимное устройство и есть ли общая инструкция по работе с устройством?",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("58803877S002",), forbidden_corporate_terms=("рабочий стол", "документооборот", "корзин")),
    ),
    AcceptanceCase(
        "G03", "G", "Что находится в нижней части укупорщика и есть ли общий порядок работы с этой частью?",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("X44236986",), forbidden_corporate_terms=("рабочий стол", "оргсхем", "корзин")),
    ),
    AcceptanceCase(
        "G04", "G", "Покажи состав верхней части наполнителя и найди документ «Рабочий стол и работа с отчётами» для этого узла.",
        _expected("mixed", "found", "not_found", "partial_mixed", must_include_entities=("20411640",), forbidden_corporate_terms=("рабочий стол", "работа с отчет", "работа с отчёт")),
    ),

    # H — realistic punctuation, politeness, word order and moderate noise.
    AcceptanceCase(
        "H01", "H", "Подскажите, пожалуйста: вал в нижней части укупорщика есть?",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("X44235100",), subject_terms=("вал", "укупорщик")),
    ),
    AcceptanceCase(
        "H02", "H", "что входит в прижимное устройство???",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("58803877S002",), subject_terms=("прижим", "устройств")),
    ),
    AcceptanceCase(
        "H03", "H", "Поищи, пожалуйста, икс 44235 — начало кода детали.",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("X44235100",)),
    ),
    AcceptanceCase(
        "H04", "H", "По нижней части укупорщика: вал там какой стоит?",
        _expected("catalog", "found", "not_requested", "catalog", must_include_entities=("X44235100",), subject_terms=("вал", "укупорщик")),
    ),
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    validate_pack()
    if args.regrade_results is not None:
        regrade_results(args.regrade_results.resolve())
        return 0
    if args.list_cases:
        for case in CASES:
            print(f"{case.case_id}\t{GROUP_LABELS[case.group]}\t{case.query}")
        print(f"total={len(CASES)}")
        return 0

    selected = CASES
    if args.case:
        selected = tuple(case for case in CASES if case.case_id == args.case.upper())
        if not selected:
            raise SystemExit(f"Unknown case: {args.case}")

    started_at = datetime.now().astimezone()
    stamp = started_at.strftime("%Y%m%dT%H%M%S%z")
    run_id = args.run_id or f"{PACK_NAME}_{stamp}"
    output_dir = (args.output_dir or (
        PROJECT_ROOT / "data" / "test_runs" / PACK_NAME / stamp
    )).resolve()
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite an existing run: {output_dir}")
    output_dir.mkdir(parents=True)

    config = load_config()
    analytics_config = AnalyticsConfig(
        enabled=True,
        db_path=output_dir / "analytics.db",
        retention_days=0,
        store_text=True,
        redact_pii=True,
        queue_size=100,
        user_hash_secret=None,
    )
    logger = create_interaction_logger(analytics_config, project_root=PROJECT_ROOT)
    if not logger.enabled:
        raise SystemExit(f"Acceptance analytics unavailable: {logger.last_error}")
    generator = RagAnswerGenerator(
        db_path=config.db_path,
        catalog_db_path=config.catalog_db_path,
        catalog_source_root=config.catalog_source_root,
        interaction_logger=logger,
    )

    payload: dict[str, Any] = {
        "pack": PACK_NAME,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "public_flow": "RagAnswerGenerator.answer",
        "conversation_history_used": False,
        "user_feedback_created": False,
        "git": git_snapshot(),
        "cases": [],
        "summary": None,
    }
    results_path = output_dir / "results.json"
    write_json(results_path, payload)
    try:
        for index, case in enumerate(selected, 1):
            print(f"[{index:02d}/{len(selected)}] {case.case_id}: {case.query}", flush=True)
            started_case = time.monotonic()
            try:
                answer = generator.answer(case.query, session_id=run_id)
                record = build_record(case, answer)
            except Exception as exc:
                record = error_record(
                    case,
                    exc,
                    duration_ms=round((time.monotonic() - started_case) * 1000, 3),
                )
            payload["cases"].append(record)
            write_json(results_path, payload)
            print(
                f"  route={record['actual']['route']} mode={record['actual']['answer_mode']} "
                f"grade={record['technical_grade']} layer={record['failure_layer'] or '-'} "
                f"duration_ms={record['duration_ms']}",
                flush=True,
            )
    finally:
        logger.close()

    payload["finished_at"] = datetime.now().astimezone().isoformat()
    payload["summary"] = summarize(payload["cases"])
    write_json(results_path, payload)
    write_json(output_dir / "summary.json", payload["summary"])
    write_failures(output_dir / "failures.csv", payload["cases"])
    print_summary(payload["summary"])
    print(f"run_id={run_id}")
    print(f"results={results_path}")
    return 0 if payload["summary"]["failed"] == 0 else 1


def build_record(case: AcceptanceCase, result: RagAnswer) -> dict[str, Any]:
    query_plan = dict(result.query_plan or {})
    catalog = dict(result.catalog or {})
    evidence = dict(result.evidence or {})
    catalog_results = catalog.get("results") if isinstance(catalog.get("results"), list) else []
    resolved_requirements = list(query_plan.get("resolved_requirements") or [])
    corporate_sources = [jsonable(source) for source in result.sources]
    catalog_sources = [jsonable(source) for source in result.catalog_sources]
    actual = {
        "route": actual_route(result, query_plan),
        "response_kind": result.response_kind,
        "requested_fact_type": query_plan.get("finalized_requested_fact_type") or query_plan.get("requested_fact_type"),
        "resolved_requirements": jsonable(resolved_requirements),
        "extracted_subject": query_plan.get("catalog_subject") or query_plan.get("subject"),
        "extracted_entity": list(query_plan.get("catalog_entity_terms") or []),
        "assembly_context": query_plan.get("catalog_assembly_context"),
        "catalog_probe_performed": bool(query_plan.get("catalog_probe_performed", False)),
        "catalog_match_type": query_plan.get("catalog_match_type") or catalog.get("match_type"),
        "catalog_state": catalog_state(catalog_results, catalog, resolved_requirements, query_plan),
        "corporate_state": corporate_state(result, resolved_requirements, query_plan),
        "answer_mode": answer_mode(result, query_plan, evidence),
        "catalog_evidence": jsonable(catalog_results),
        "catalog_sources": catalog_sources,
        "corporate_evidence": corporate_sources,
        "supporting_chunk_ids": list(evidence.get("supporting_chunk_ids") or []),
        "rejected_evidence": {
            "non_supporting_chunk_ids": list(evidence.get("non_supporting_chunk_ids") or []),
            "rejected_supporting_chunk_ids": list(evidence.get("rejected_supporting_chunk_ids") or []),
            "domain_inconsistent_chunk_ids": list(evidence.get("domain_inconsistent_chunk_ids") or []),
            "domain_consistency_checked": evidence.get("domain_consistency_checked"),
            "domain_consistency_passed": evidence.get("domain_consistency_passed"),
            "domain_consistency_reason": evidence.get("domain_consistency_reason"),
        },
        "final_answer": result.answer,
    }
    failures = grade(case, actual)
    layer = failure_layer(failures)
    return {
        "id": case.case_id,
        "group": case.group,
        "group_label": GROUP_LABELS[case.group],
        "query": case.query,
        "expected": asdict(case.expected),
        "actual": actual,
        "query_plan": jsonable(query_plan),
        "retrieval": jsonable(result.retrieval or {}),
        "evidence": jsonable(evidence),
        "technical_grade": "PASS" if not failures else "FAIL",
        "failure_reasons": failures,
        "failure_layer": layer,
        "duration_ms": round(result.elapsed_seconds * 1000, 3),
        "interaction_id": result.interaction_id,
        "analytics_logged": result.analytics_logged,
        "error": result.analytics_error,
    }


def grade(case: AcceptanceCase, actual: dict[str, Any]) -> list[str]:
    expected = case.expected
    failures: list[str] = []
    if actual["route"] not in expected.routes:
        failures.append("wrong_source_route")
    if actual["catalog_state"] not in expected.catalog_states:
        failures.append("wrong_catalog_state")
    if actual["corporate_state"] not in expected.corporate_states:
        failures.append("wrong_corporate_state")
    if actual["answer_mode"] not in expected.final_modes:
        failures.append("wrong_answer_mode")
    if expected.catalog_match_types and actual["catalog_match_type"] not in expected.catalog_match_types:
        failures.append("wrong_catalog_match_type")

    subject = normalized(str(actual.get("extracted_subject") or ""))
    if any(normalized(term) not in subject for term in expected.subject_terms):
        failures.append("subject_extraction_failed")
    entity = normalized(" ".join(actual.get("extracted_entity") or []))
    if any(normalized(term) not in entity for term in expected.entity_terms):
        failures.append("entity_extraction_failed")
    context = normalized(str(actual.get("assembly_context") or ""))
    if any(normalized(term) not in context for term in expected.assembly_context_terms):
        failures.append("assembly_context_failed")

    catalog_haystack = normalized(json.dumps(actual["catalog_evidence"], ensure_ascii=False))
    for value in expected.must_include_entities:
        if normalized(value) not in catalog_haystack:
            failures.append(f"catalog_entity_missing:{value}")
    for value in expected.must_not_include_entities:
        if normalized(value) in catalog_haystack:
            failures.append(f"unexpected_catalog_entity:{value}")

    corporate_haystack = normalized(json.dumps(actual["corporate_evidence"], ensure_ascii=False))
    answer_and_sources = normalized(f"{actual['final_answer']} {corporate_haystack}")
    if expected.corporate_anchor_terms and not any(
        normalized(term) in answer_and_sources for term in expected.corporate_anchor_terms
    ):
        failures.append("corporate_subject_anchor_missing")
    if any(normalized(term) in corporate_haystack for term in expected.forbidden_corporate_terms):
        failures.append("irrelevant_corporate_evidence_accepted")

    if actual["route"] == "catalog" and actual["corporate_evidence"]:
        failures.append("corporate_leakage_into_catalog")
    if actual["route"] == "corporate" and actual["catalog_sources"]:
        failures.append("catalog_leakage_into_corporate")
    return list(dict.fromkeys(failures))


def failure_layer(failures: Sequence[str]) -> str | None:
    if not failures:
        return None
    priority = (
        ("runtime_error", "UNKNOWN"),
        ("subject_extraction", "UNDERSTANDING"),
        ("entity_extraction", "UNDERSTANDING"),
        ("assembly_context", "UNDERSTANDING"),
        ("wrong_source_route", "ROUTING"),
        ("catalog_leakage", "ROUTING"),
        ("wrong_catalog", "CATALOG_RETRIEVAL"),
        ("catalog_entity", "CATALOG_RETRIEVAL"),
        ("unexpected_catalog", "CATALOG_RETRIEVAL"),
        ("wrong_corporate", "CORPORATE_RETRIEVAL"),
        ("corporate_subject_anchor", "CORPORATE_RETRIEVAL"),
        ("irrelevant_corporate", "EVIDENCE_GUARD"),
        ("corporate_leakage", "EVIDENCE_GUARD"),
        ("wrong_answer_mode", "MIXED_COMPOSITION"),
    )
    for prefix, layer in priority:
        if any(reason.startswith(prefix) for reason in failures):
            return layer
    return "UNKNOWN"


def actual_route(result: RagAnswer, query_plan: dict[str, Any]) -> str:
    route = str(query_plan.get("source_route") or "").strip()
    if route:
        return route
    if result.catalog is not None:
        return "catalog"
    return "corporate"


def catalog_state(
    results: list[dict[str, Any]],
    catalog: dict[str, Any],
    requirements: list[dict[str, Any]],
    query_plan: dict[str, Any],
) -> str:
    if results or catalog.get("assembly_count"):
        return "found"
    status = str(catalog.get("status") or "")
    catalog_requirements = [item for item in requirements if item.get("source") == "catalog"]
    if any(item.get("status") == "found" for item in catalog_requirements):
        return "found"
    if status in {"not_found", "no_candidates"} or any(
        item.get("status") == "not_found" for item in catalog_requirements
    ):
        return "not_found"
    if query_plan.get("catalog_probe_performed") and not query_plan.get("catalog_match_type"):
        return "not_found"
    return "not_requested"


def corporate_state(
    result: RagAnswer,
    requirements: list[dict[str, Any]],
    query_plan: dict[str, Any],
) -> str:
    corporate_requirements = [item for item in requirements if item.get("source") == "corporate"]
    if result.sources or any(item.get("status") == "found" for item in corporate_requirements):
        return "found"
    if any(item.get("status") == "not_found" for item in corporate_requirements):
        return "not_found"
    if actual_route(result, query_plan) in {"corporate", "mixed"}:
        return "not_found"
    return "not_requested"


def answer_mode(result: RagAnswer, query_plan: dict[str, Any], evidence: dict[str, Any]) -> str:
    if result.response_kind == "clarification":
        return "clarification"
    if result.response_kind in {"no_answer", "catalog_not_found", "catalog_search_not_found"}:
        return "not_found"
    explicit = str(query_plan.get("answer_mode") or "")
    if explicit in {"catalog", "full_mixed", "partial_mixed"}:
        return explicit
    if actual_route(result, query_plan) == "catalog":
        return "catalog"
    mode = str(evidence.get("mode") or evidence.get("answer_mode") or "")
    if result.response_kind == "partial_answer" or mode == "partial_answer":
        return "corporate_partial"
    if result.sources or mode == "full_answer":
        return "corporate_found"
    return "not_found"


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for group, label in GROUP_LABELS.items():
        selected = [record for record in records if record["group"] == group]
        passed = sum(record["technical_grade"] == "PASS" for record in selected)
        groups[group] = {
            "label": label,
            "total": len(selected),
            "passed": passed,
            "failed": len(selected) - passed,
        }
    failed = [record for record in records if record["technical_grade"] == "FAIL"]
    return {
        "total": len(records),
        "passed": len(records) - len(failed),
        "failed": len(failed),
        "success_rate": round((len(records) - len(failed)) / len(records) * 100, 2) if records else 0.0,
        "groups": groups,
        "failures": [
            {
                "id": record["id"],
                "layer": record["failure_layer"],
                "reasons": record["failure_reasons"],
            }
            for record in failed
        ],
        "corporate_leakage": sum(
            "corporate_leakage_into_catalog" in record["failure_reasons"]
            for record in records
        ),
        "catalog_leakage": sum(
            "catalog_leakage_into_corporate" in record["failure_reasons"]
            for record in records
        ),
        "hallucinated_or_irrelevant_evidence": sum(
            "irrelevant_corporate_evidence_accepted" in record["failure_reasons"]
            or any(reason.startswith("unexpected_catalog_entity") for reason in record["failure_reasons"])
            for record in records
        ),
    }


def error_record(case: AcceptanceCase, exc: Exception, *, duration_ms: float) -> dict[str, Any]:
    return {
        "id": case.case_id,
        "group": case.group,
        "group_label": GROUP_LABELS[case.group],
        "query": case.query,
        "expected": asdict(case.expected),
        "actual": {"route": "error", "answer_mode": "runtime_error"},
        "query_plan": {},
        "retrieval": {},
        "evidence": {},
        "technical_grade": "FAIL",
        "failure_reasons": ["runtime_error"],
        "failure_layer": "UNKNOWN",
        "duration_ms": duration_ms,
        "interaction_id": None,
        "analytics_logged": False,
        "error": f"{type(exc).__name__}: {exc}",
    }


def regrade_results(path: Path) -> None:
    """Apply corrected deterministic grading to a completed immutable live run."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases_by_id = {case.case_id: case for case in CASES}
    for record in payload.get("cases", []):
        case = cases_by_id[record["id"]]
        actual = record["actual"]
        if actual.get("response_kind") in {
            "no_answer",
            "catalog_not_found",
            "catalog_search_not_found",
        }:
            actual["answer_mode"] = "not_found"
        failures = grade(case, actual)
        record["technical_grade"] = "PASS" if not failures else "FAIL"
        record["failure_reasons"] = failures
        record["failure_layer"] = failure_layer(failures)
    payload["summary"] = summarize(payload.get("cases", []))
    payload["regraded_at"] = datetime.now().astimezone().isoformat()
    payload["regrade_note"] = (
        "Corrected evaluator precedence: explicit no-answer/catalog-not-found "
        "response_kind is graded as NOT_FOUND before generic route mode."
    )
    write_json(path, payload)
    write_json(path.with_name("summary.json"), payload["summary"])
    write_failures(path.with_name("failures.csv"), payload.get("cases", []))
    print_summary(payload["summary"])
    print(f"regraded={path}")


def validate_pack() -> None:
    if not 30 <= len(CASES) <= 40:
        raise AssertionError("Manager acceptance pack must contain 30–40 cases")
    if len({case.case_id for case in CASES}) != len(CASES):
        raise AssertionError("Case ids must be unique")
    if {case.group for case in CASES} != set(GROUP_LABELS):
        raise AssertionError("All A–H groups must be represented")


def print_summary(summary: dict[str, Any]) -> None:
    print("\nManager Natural Acceptance\n")
    for group, data in summary["groups"].items():
        print(f"{group} {data['label']:<20} {data['passed']}/{data['total']}")
    print(f"\nTOTAL{'':<21} {summary['passed']}/{summary['total']}")
    print("\nFailures:")
    if not summary["failures"]:
        print("none")
    for item in summary["failures"]:
        print(f"{item['id']}  {item['layer']}  {', '.join(item['reasons'])}")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")


def write_failures(path: Path, records: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("id", "group", "query", "failure_layer", "failure_reasons"))
        writer.writeheader()
        for record in records:
            if record["technical_grade"] == "FAIL":
                writer.writerow(
                    {
                        "id": record["id"],
                        "group": record["group"],
                        "query": record["query"],
                        "failure_layer": record["failure_layer"],
                        "failure_reasons": ";".join(record["failure_reasons"]),
                    }
                )


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def normalized(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def git_snapshot() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

    return {
        "branch": run("branch", "--show-current"),
        "head": run("rev-parse", "HEAD"),
        "status_short": run("status", "--short").splitlines(),
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run one case id, for example C05")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument(
        "--regrade-results",
        type=Path,
        help="Recompute grades from an existing results.json without runtime calls",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
