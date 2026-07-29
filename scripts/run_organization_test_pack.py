"""Run the LineHelper organization contour test pack with diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import (  # noqa: E402
    KP_COMMERCIAL_OFFER_MESSAGE,
    SYSTEM_MESSAGE,
    RagAnswerError,
    _boost_preferred_source_chunks,
    _chunk_score,
    _dedupe_chunks,
    _no_answer_message,
    _query_plan_clarification,
    _query_plan_is_usable,
    _query_plan_retrieval_queries,
    _runtime_query_intent,
    has_sufficient_context,
    select_context_chunks,
    strip_trailing_source_block,
)
from linehelper.llm.ollama_client import (  # noqa: E402
    DEFAULT_NUM_PREDICT,
    DEFAULT_TEMPERATURE,
    OllamaClient,
    OllamaSettings,
    load_ollama_settings_from_env,
)
from linehelper.rag.prompt_builder import build_rag_prompt  # noqa: E402
from linehelper.rag.query_analyzer import QueryAnalyzer  # noqa: E402
from linehelper.rag.retriever import (  # noqa: E402
    DEFAULT_MEMORY_DB_PATH,
    RetrievedChunk,
    SemanticRetriever,
    normalize_question,
)


DEFAULT_PACK = Path("docs/test_packs/linehelper_organization_test_scenarios_360.jsonl")
DEFAULT_OUT_ROOT = Path("data/test_runs/organization")
DEFAULT_DB_PATH = DEFAULT_MEMORY_DB_PATH
SOURCE_VERSION = "2025-12-17"
SOURCE_FILE = "bvr_company_structure_instruction_v2 (2).txt"
KNOWLEDGE_DOMAIN = "organization_structure"
RETRIEVAL_LIMIT = 5
CANDIDATE_LIMIT = 30
CONTEXT_LIMIT = 3

RESULTS_NAME = "results.jsonl"
RUN_CONFIG_NAME = "run_config.json"
SUMMARY_NAME = "summary.json"
RETRIEVAL_CSV_NAME = "retrieval_diagnostics.csv"
FAILURES_NAME = "failures.md"
MANUAL_REVIEW_NAME = "manual_review.md"
GROUP_SUMMARY_NAME = "group_summary.csv"
TIMINGS_NAME = "timings.csv"
RUN_LOG_NAME = "run.log"
FINAL_REPORT_NAME = "final_report.md"

REQUIRED_FIELDS = {
    "id",
    "group_id",
    "group_name",
    "question",
    "expected_keywords",
    "expected_behavior",
    "severity",
}

FAILURE_TYPES = {
    "retrieval_zero_results",
    "retrieval_wrong_top1",
    "retrieval_relevant_below_top5",
    "retrieval_missing_alias",
    "retrieval_wrong_entity_type",
    "query_analysis_wrong_intent",
    "query_analysis_bad_normalization",
    "context_missing_required_chunk",
    "context_truncated",
    "generation_wrong_person",
    "generation_wrong_unit",
    "generation_missing_contact",
    "generation_invented_contact",
    "generation_invented_employee",
    "generation_vacancy_error",
    "generation_inactive_status_error",
    "generation_ignored_context",
    "generation_overgeneralized",
    "generation_failed_to_ask_clarification",
    "generation_operational_data_hallucination",
    "source_data_conflict",
    "source_data_missing",
    "timeout",
    "runtime_error",
    "manual_review",
}


class TimedQueryAnalyzer:
    """QueryAnalyzer wrapper that records the last duration and error."""

    def __init__(self, *, model: str | None, timeout: float) -> None:
        client = OllamaClient(model=model, timeout_seconds=timeout)
        self._inner = QueryAnalyzer(ollama_client=client, model=model)
        self.model = self._inner.model
        self.last_elapsed_ms: float | None = None
        self.last_error: str | None = None

    def analyze(self, question: str):
        started = time.monotonic()
        try:
            plan = self._inner.analyze(question)
            self.last_error = self._inner.last_error
            return plan
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.last_elapsed_ms = _elapsed_ms(started)


class RecordingOllamaClient:
    """Ollama chat client that records exact messages sent to the model."""

    def __init__(self, *, model: str | None, timeout: float) -> None:
        self._inner = OllamaClient(model=model, timeout_seconds=timeout)
        self.model = self._inner.model
        self.last_messages: list[dict[str, str]] = []
        self.last_elapsed_ms: float | None = None

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.last_messages = [dict(message) for message in messages]
        started = time.monotonic()
        try:
            return self._inner.chat(messages)
        finally:
            self.last_elapsed_ms = _elapsed_ms(started)


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = _parse_args(argv)
    pack_path = _resolve(args.test_pack)
    db_path = _resolve(args.db_path)
    out_root = _resolve(args.output_dir)

    scenarios = _load_scenarios(pack_path)
    selected = _select_scenarios(
        scenarios,
        group=args.group,
        limit=args.limit,
        start_from=args.start_from,
        question_id=args.question_id,
    )

    run_dir, resume = _prepare_run_dir(out_root, args.resume)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / RUN_LOG_NAME
    _log(log_path, f"Run directory: {run_dir}")
    _log(log_path, f"Selected scenarios: {len(selected)}")

    processed_ids = _processed_ids(run_dir / RESULTS_NAME) if resume else set()
    to_run = [scenario for scenario in selected if scenario["id"] not in processed_ids]
    settings = _runtime_settings(args)
    preflight = _run_preflight(
        db_path=db_path,
        pack_path=pack_path,
        scenarios=scenarios,
        selected=selected,
        settings=settings,
        require_ollama=not args.retrieval_only and not args.skip_generation,
    )

    run_config = _build_run_config(
        args=args,
        run_dir=run_dir,
        pack_path=pack_path,
        db_path=db_path,
        selected_count=len(selected),
        pending_count=len(to_run),
        processed_count=len(processed_ids),
        settings=settings,
        preflight=preflight,
    )
    _write_json(run_dir / RUN_CONFIG_NAME, run_config)

    if not preflight["ok"]:
        _log(log_path, f"Preflight failed: {preflight['errors']}")
        _finalize(run_dir, run_config)
        return 1

    if processed_ids:
        _log(log_path, f"Resume: skipping {len(processed_ids)} completed scenario(s).")

    dialog_history: list[dict[str, str]] = []
    previous_group: str | None = None

    with (run_dir / RESULTS_NAME).open("a", encoding="utf-8", newline="\n") as file:
        for index, scenario in enumerate(to_run, start=1):
            if scenario["group_id"] != previous_group:
                dialog_history = []
                previous_group = scenario["group_id"]
            if scenario["group_id"] != "ORG18":
                dialog_history = []

            _log(log_path, f"[{index}/{len(to_run)}] {scenario['id']}: {scenario['question']}")
            record = _run_scenario(
                scenario,
                settings=settings,
                db_path=db_path,
                retrieval_only=args.retrieval_only,
                skip_generation=args.skip_generation,
                dialog_history=dialog_history,
            )
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()

            if scenario["group_id"] == "ORG18":
                dialog_history.append(
                    {
                        "question": scenario["question"],
                        "answer": record.get("generation", {}).get("answer") or "",
                        "status": record.get("evaluation", {}).get("status") or "",
                    }
                )

    summary = _finalize(run_dir, run_config)
    _log(
        log_path,
        "Summary: completed={completed} PASS={passed} PARTIAL={partial} "
        "FAIL={failed} MANUAL_REVIEW={manual_review}".format(**summary),
    )
    print(f"Run directory: {run_dir}")
    print(
        "Summary: completed={completed} PASS={passed} PARTIAL={partial} "
        "FAIL={failed} MANUAL_REVIEW={manual_review}".format(**summary)
    )
    return 0 if summary.get("runtime_errors", 0) == 0 else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run organization contour evaluation scenarios.")
    parser.add_argument("--test-pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--group", default=None)
    parser.add_argument("--start-from", default=None)
    parser.add_argument("--question-id", default=None)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _run_scenario(
    scenario: dict[str, Any],
    *,
    settings: OllamaSettings,
    db_path: Path,
    retrieval_only: bool,
    skip_generation: bool,
    dialog_history: list[dict[str, str]],
) -> dict[str, Any]:
    started = time.monotonic()
    question = str(scenario["question"])
    generation: dict[str, Any] = {
        "answer": "",
        "duration_ms": 0.0,
        "input_tokens": None,
        "output_tokens": None,
        "error": None,
    }
    context_messages: list[dict[str, str]] = []
    context_sent_to_model = ""
    query_plan = None
    query_plan_error = None
    detected_intent = "unknown"
    normalized = normalize_question(question)
    retrieval_queries = [question]
    retrieval_chunks: list[RetrievedChunk] = []
    retrieval_duration_ms = 0.0
    context_chunks: list[RetrievedChunk] = []

    try:
        if retrieval_only:
            retriever = SemanticRetriever(db_path)
            retrieval_started = time.monotonic()
            retrieval_chunks = retriever.retrieve(
                question,
                limit=RETRIEVAL_LIMIT,
                candidate_limit=CANDIDATE_LIMIT,
            )
            retrieval_duration_ms = _elapsed_ms(retrieval_started)
            detected_intent = "retrieval_only"
            generation["answer"] = ""
        else:
            pipeline = _run_generation_pipeline(
                question,
                settings=settings,
                db_path=db_path,
                skip_generation=skip_generation,
            )
            query_plan = pipeline["query_plan"]
            query_plan_error = pipeline["query_plan_error"]
            detected_intent = pipeline["detected_intent"]
            normalized = pipeline["normalized_query"]
            retrieval_queries = pipeline["retrieval_queries"]
            retrieval_chunks = pipeline["retrieval_chunks"]
            retrieval_duration_ms = pipeline["retrieval_duration_ms"]
            context_chunks = pipeline["context_chunks"]
            context_messages = pipeline["messages"]
            context_sent_to_model = json.dumps(context_messages, ensure_ascii=False, indent=2)
            generation = pipeline["generation"]
    except Exception as exc:
        generation["error"] = f"{type(exc).__name__}: {exc}"
        generation["duration_ms"] = 0.0
        generation["answer"] = ""
        query_plan_error = generation["error"]
        if "timeout" in str(exc).casefold():
            failure_type = "timeout"
        else:
            failure_type = "runtime_error"
        retrieval = _retrieval_payload([], retrieval_duration_ms)
        evaluation = {
            "status": "FAIL",
            "automatic_score": 0.0,
            "keyword_matches": [],
            "missing_keywords": _expected_terms(scenario),
            "failure_type": failure_type,
            "manual_review_required": False,
            "comment": generation["error"],
        }
        return _record(
            scenario,
            detected_intent=detected_intent,
            normalized_query=normalized,
            retrieval_query=" | ".join(retrieval_queries),
            retrieval=retrieval,
            context_sent_to_model=context_sent_to_model,
            generation=generation,
            evaluation=evaluation,
            total_duration_ms=_elapsed_ms(started),
            query_plan=query_plan,
            query_plan_error=query_plan_error,
            dialog_history=dialog_history,
        )

    retrieval = _retrieval_payload(retrieval_chunks[:RETRIEVAL_LIMIT], retrieval_duration_ms)
    evaluation = _evaluate(
        scenario,
        answer=generation.get("answer") or "",
        retrieval_results=retrieval["results"],
        context_messages=context_messages,
        retrieval_only=retrieval_only,
        skip_generation=skip_generation,
    )

    return _record(
        scenario,
        detected_intent=detected_intent,
        normalized_query=normalized,
        retrieval_query=" | ".join(retrieval_queries),
        retrieval=retrieval,
        context_sent_to_model=context_sent_to_model,
        generation=generation,
        evaluation=evaluation,
        total_duration_ms=_elapsed_ms(started),
        query_plan=query_plan,
        query_plan_error=query_plan_error,
        dialog_history=dialog_history,
    )


def _run_generation_pipeline(
    question: str,
    *,
    settings: OllamaSettings,
    db_path: Path,
    skip_generation: bool,
) -> dict[str, Any]:
    analyzer = TimedQueryAnalyzer(model=settings.model, timeout=settings.timeout_seconds)
    answer_client = RecordingOllamaClient(model=settings.model, timeout=settings.timeout_seconds)
    retriever = SemanticRetriever(db_path)

    query_plan = None
    query_plan_error = None
    analyzer_started = time.monotonic()
    try:
        query_plan = analyzer.analyze(question)
    except Exception as exc:
        query_plan_error = f"{type(exc).__name__}: {exc}"
    analyzer_ms = analyzer.last_elapsed_ms if analyzer.last_elapsed_ms is not None else _elapsed_ms(analyzer_started)

    if query_plan is not None and not _query_plan_is_usable(query_plan):
        query_plan_error = query_plan_error or "empty_or_unknown_query_plan"
        query_plan = None

    normalized_query = getattr(query_plan, "normalized_question", None) or normalize_question(question)
    detected_intent = getattr(query_plan, "intent", None) or "unknown"
    clarification = _query_plan_clarification(query_plan) if query_plan is not None else None
    if clarification is not None:
        return {
            "query_plan": _plan_to_dict(query_plan),
            "query_plan_error": query_plan_error,
            "detected_intent": detected_intent,
            "normalized_query": normalized_query,
            "retrieval_queries": [],
            "retrieval_chunks": [],
            "retrieval_duration_ms": 0.0,
            "context_chunks": [],
            "messages": [],
            "generation": {
                "answer": clarification,
                "duration_ms": 0.0,
                "input_tokens": None,
                "output_tokens": None,
                "error": None,
            },
        }

    intent = _runtime_query_intent(question, query_plan)
    retrieval_queries = (
        list(_query_plan_retrieval_queries(question, query_plan))
        if query_plan is not None
        else [question]
    )
    retrieval_started = time.monotonic()
    chunks: list[RetrievedChunk] = []
    for retrieval_query in retrieval_queries:
        chunks.extend(
            retriever.retrieve(
                retrieval_query,
                limit=RETRIEVAL_LIMIT,
                candidate_limit=CANDIDATE_LIMIT,
            )
        )
    retrieval_duration_ms = _elapsed_ms(retrieval_started)
    chunks = sorted(
        _boost_preferred_source_chunks(
            _dedupe_chunks(chunks),
            getattr(query_plan, "preferred_sources", []) if query_plan is not None else [],
        ),
        key=_chunk_score,
        reverse=True,
    )

    if intent.name == "kp_commercial_offer":
        return {
            "query_plan": _plan_to_dict(query_plan),
            "query_plan_error": query_plan_error,
            "detected_intent": detected_intent,
            "normalized_query": normalized_query,
            "retrieval_queries": retrieval_queries,
            "retrieval_chunks": chunks,
            "retrieval_duration_ms": retrieval_duration_ms,
            "context_chunks": [],
            "messages": [],
            "generation": {
                "answer": KP_COMMERCIAL_OFFER_MESSAGE,
                "duration_ms": 0.0,
                "input_tokens": None,
                "output_tokens": None,
                "error": None,
            },
        }

    context_chunks = select_context_chunks(
        question,
        chunks,
        intent=intent,
        max_chunks=CONTEXT_LIMIT,
    )
    if not has_sufficient_context(question, context_chunks, intent=intent):
        context_chunks = []

    if not context_chunks:
        return {
            "query_plan": _plan_to_dict(query_plan),
            "query_plan_error": query_plan_error,
            "detected_intent": detected_intent,
            "normalized_query": normalized_query,
            "retrieval_queries": retrieval_queries,
            "retrieval_chunks": chunks,
            "retrieval_duration_ms": retrieval_duration_ms,
            "context_chunks": [],
            "messages": [],
            "generation": {
                "answer": _no_answer_message(intent),
                "duration_ms": 0.0,
                "input_tokens": None,
                "output_tokens": None,
                "error": None,
            },
        }

    prompt = build_rag_prompt(question, context_chunks)
    messages = [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": prompt},
    ]
    if skip_generation:
        return {
            "query_plan": _plan_to_dict(query_plan),
            "query_plan_error": query_plan_error,
            "detected_intent": detected_intent,
            "normalized_query": normalized_query,
            "retrieval_queries": retrieval_queries,
            "retrieval_chunks": chunks,
            "retrieval_duration_ms": retrieval_duration_ms,
            "context_chunks": context_chunks,
            "messages": messages,
            "generation": {
                "answer": "",
                "duration_ms": 0.0,
                "input_tokens": None,
                "output_tokens": None,
                "error": None,
            },
        }

    try:
        raw_answer = answer_client.chat(messages)
        answer = strip_trailing_source_block(raw_answer)
        error = None
    except Exception as exc:
        answer = ""
        error = f"{type(exc).__name__}: {exc}"

    generation_ms = answer_client.last_elapsed_ms or 0.0
    return {
        "query_plan": _plan_to_dict(query_plan),
        "query_plan_error": query_plan_error,
        "detected_intent": detected_intent,
        "normalized_query": normalized_query,
        "retrieval_queries": retrieval_queries,
        "retrieval_chunks": chunks,
        "retrieval_duration_ms": retrieval_duration_ms + analyzer_ms,
        "context_chunks": context_chunks,
        "messages": answer_client.last_messages or messages,
        "generation": {
            "answer": answer,
            "duration_ms": generation_ms,
            "input_tokens": None,
            "output_tokens": None,
            "error": error,
        },
    }


def _evaluate(
    scenario: dict[str, Any],
    *,
    answer: str,
    retrieval_results: list[dict[str, Any]],
    context_messages: list[dict[str, str]],
    retrieval_only: bool,
    skip_generation: bool,
) -> dict[str, Any]:
    expected = _expected_terms(scenario)
    answer_text = answer if not retrieval_only and not skip_generation else _retrieval_haystack(retrieval_results)
    keyword_matches, missing = _keyword_matches(expected, answer_text)
    retrieval_keyword_matches, retrieval_missing = _keyword_matches(expected, _retrieval_haystack(retrieval_results))
    top1_relevant = bool(retrieval_results and _terms_present(expected, _result_haystack(retrieval_results[0])))
    top5_relevant = not retrieval_missing if expected else bool(retrieval_results)

    failure_type: str | None = None
    manual_required = _needs_manual_review(scenario)
    status = "PASS"
    automatic_score = 1.0

    if not retrieval_results:
        status = "FAIL"
        automatic_score = 0.0
        failure_type = "retrieval_zero_results"
    elif expected and not top5_relevant:
        status = "FAIL"
        automatic_score = 0.0
        failure_type = "retrieval_missing_alias"
    elif retrieval_only or skip_generation:
        if expected and not top1_relevant and top5_relevant:
            status = "PARTIAL"
            automatic_score = 0.7
            failure_type = "retrieval_wrong_top1"
        elif manual_required:
            status = "MANUAL_REVIEW"
            automatic_score = 0.5
            failure_type = "manual_review"
    else:
        invented_contact = _invented_contact(answer, context_messages)
        if invented_contact:
            status = "FAIL"
            automatic_score = 0.0
            failure_type = "generation_invented_contact"
        elif expected and missing:
            status = "PARTIAL" if retrieval_keyword_matches else "FAIL"
            automatic_score = len(keyword_matches) / max(1, len(expected))
            failure_type = _generation_failure_type(scenario, missing, retrieval_keyword_matches)
        elif _vacancy_error(scenario, answer):
            status = "FAIL"
            automatic_score = 0.0
            failure_type = "generation_vacancy_error"
        elif _inactive_error(scenario, answer):
            status = "FAIL"
            automatic_score = 0.0
            failure_type = "generation_inactive_status_error"
        elif manual_required:
            status = "MANUAL_REVIEW"
            automatic_score = 0.5
            failure_type = "manual_review"

    if failure_type is not None and failure_type not in FAILURE_TYPES:
        failure_type = "runtime_error"

    comment = _evaluation_comment(
        status=status,
        missing=missing,
        retrieval_missing=retrieval_missing,
        failure_type=failure_type,
        manual_required=manual_required,
    )
    return {
        "status": status,
        "automatic_score": round(float(automatic_score), 3),
        "keyword_matches": keyword_matches,
        "missing_keywords": missing,
        "failure_type": failure_type,
        "manual_review_required": manual_required or status == "MANUAL_REVIEW",
        "comment": comment,
        "top1_relevant": top1_relevant,
        "top3_relevant": _topn_relevant(expected, retrieval_results, 3),
        "top5_relevant": top5_relevant,
    }


def _generation_failure_type(
    scenario: dict[str, Any],
    missing: list[str],
    retrieval_matches: list[str],
) -> str:
    expected_text = " ".join(_expected_terms(scenario)).casefold()
    behavior = str(scenario.get("expected_behavior") or "").casefold()
    if any(_looks_like_contact(term) for term in missing):
        return "generation_missing_contact" if retrieval_matches else "source_data_missing"
    if "ваканс" in expected_text or "ваканс" in behavior:
        return "generation_vacancy_error"
    if "не актив" in expected_text or "неактив" in behavior:
        return "generation_inactive_status_error"
    if retrieval_matches:
        return "generation_ignored_context"
    return "source_data_missing"


def _record(
    scenario: dict[str, Any],
    *,
    detected_intent: str,
    normalized_query: str,
    retrieval_query: str,
    retrieval: dict[str, Any],
    context_sent_to_model: str,
    generation: dict[str, Any],
    evaluation: dict[str, Any],
    total_duration_ms: float,
    query_plan: dict[str, Any] | None,
    query_plan_error: str | None,
    dialog_history: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": scenario["id"],
        "group_id": scenario["group_id"],
        "group_name": scenario["group_name"],
        "question": scenario["question"],
        "expected_keywords": scenario.get("expected_keywords", ""),
        "expected_behavior": scenario.get("expected_behavior", ""),
        "severity": scenario.get("severity", ""),
        "detected_intent": detected_intent,
        "normalized_query": normalized_query,
        "retrieval_query": retrieval_query,
        "query_plan": query_plan,
        "query_plan_error": query_plan_error,
        "dialog_history_before": list(dialog_history),
        "retrieval": retrieval,
        "context_sent_to_model": context_sent_to_model,
        "generation": generation,
        "evaluation": evaluation,
        "total_duration_ms": round(total_duration_ms, 3),
    }


def _retrieval_payload(chunks: list[RetrievedChunk], duration_ms: float) -> dict[str, Any]:
    return {
        "result_count": len(chunks),
        "duration_ms": round(duration_ms, 3),
        "results": [_chunk_result(chunk, rank=index) for index, chunk in enumerate(chunks, start=1)],
    }


def _chunk_result(chunk: RetrievedChunk, *, rank: int) -> dict[str, Any]:
    metadata = chunk.metadata or {}
    return {
        "rank": rank,
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
        "doc_type": chunk.doc_type,
        "score": chunk.final_score if chunk.final_score is not None else chunk.score,
        "source": chunk.source,
        "section": chunk.section,
        "record_key": metadata.get("record_key"),
        "entity_type": metadata.get("entity_type"),
        "employee_name": metadata.get("employee_name"),
        "unit_name": metadata.get("unit_name"),
        "topic": metadata.get("topic"),
        "phone": metadata.get("phone"),
        "email": metadata.get("email"),
        "source_version": metadata.get("source_version"),
        "text": chunk.text,
    }


def _load_scenarios(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"test pack not found: {path}")
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = REQUIRED_FIELDS - set(row)
            if missing:
                raise ValueError(f"line {line_number}: missing fields: {sorted(missing)}")
            rows.append(row)
    if len(rows) != 360:
        raise ValueError(f"expected 360 scenarios, got {len(rows)}")
    return rows


def _select_scenarios(
    scenarios: list[dict[str, Any]],
    *,
    group: str | None,
    limit: int | None,
    start_from: str | None,
    question_id: str | None,
) -> list[dict[str, Any]]:
    selected = list(scenarios)
    if group:
        selected = [row for row in selected if row["group_id"] == group]
    if question_id:
        selected = [row for row in selected if row["id"] == question_id]
    if start_from:
        try:
            start_index = next(index for index, row in enumerate(selected) if row["id"] == start_from)
        except StopIteration as exc:
            raise ValueError(f"--start-from id not found in selected scenarios: {start_from}") from exc
        selected = selected[start_index:]
    if limit is not None:
        selected = selected[: max(0, limit)]
    if not selected:
        raise ValueError("no scenarios selected")
    return selected


def _prepare_run_dir(out_root: Path, resume: bool) -> tuple[Path, bool]:
    if resume:
        if (out_root / RUN_CONFIG_NAME).exists():
            return out_root, True
        candidates = sorted(
            path for path in out_root.glob("*") if path.is_dir() and (path / RUN_CONFIG_NAME).exists()
        )
        if not candidates:
            raise FileNotFoundError(f"no previous run found under {out_root}")
        unfinished = [
            path for path in candidates if not _read_json(path / RUN_CONFIG_NAME).get("finished_at")
        ]
        return (unfinished[-1] if unfinished else candidates[-1]), True
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_root / timestamp, False


def _runtime_settings(args: argparse.Namespace) -> OllamaSettings:
    base = load_ollama_settings_from_env()
    return OllamaSettings(
        base_url=base.base_url,
        model=args.model or base.model,
        timeout_seconds=args.timeout,
        temperature=base.temperature,
        num_predict=base.num_predict,
    )


def _run_preflight(
    *,
    db_path: Path,
    pack_path: Path,
    scenarios: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    settings: OllamaSettings,
    require_ollama: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    db_check = _check_organization_chunks(db_path)
    if not db_check["ok"]:
        errors.append("organization chunk check failed")
    jsonl_check = {
        "ok": len(scenarios) == 360 and all(REQUIRED_FIELDS <= set(row) for row in scenarios),
        "count": len(scenarios),
        "path": _display_path(pack_path),
    }
    if not jsonl_check["ok"]:
        errors.append("JSONL validation failed")
    out_check = {"ok": True}
    ollama_check = _check_ollama(settings) if require_ollama else {"ok": True, "skipped": True}
    if not ollama_check.get("ok"):
        errors.append("Ollama preflight failed")
    return {
        "ok": not errors,
        "errors": errors,
        "database": db_check,
        "jsonl": jsonl_check,
        "output_dir": out_check,
        "ollama": ollama_check,
        "selected_count": len(selected),
    }


def _check_organization_chunks(db_path: Path) -> dict[str, Any]:
    expected_counts = {
        "organization_overview": 1,
        "organization_unit": 55,
        "employee_role": 94,
        "responsibility_route": 22,
        "organization_vacancy": 11,
        "organization_status": 2,
        "role_combination": 7,
    }
    if not db_path.exists():
        return {"ok": False, "error": f"database not found: {db_path}"}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT doc_type, metadata_json
            FROM memory_chunks
            WHERE namespace = 'semantic'
            """
        ).fetchall()
    finally:
        conn.close()
    matched = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if (
            metadata.get("knowledge_domain") == KNOWLEDGE_DOMAIN
            and metadata.get("source_version") == SOURCE_VERSION
            and metadata.get("source_file") == SOURCE_FILE
        ):
            matched.append((row["doc_type"], metadata.get("record_key")))
    record_keys = [item[1] for item in matched]
    counts = Counter(item[0] for item in matched)
    duplicates = {key: count for key, count in Counter(record_keys).items() if count > 1}
    return {
        "ok": len(matched) == 192
        and len(set(record_keys)) == 192
        and not duplicates
        and dict(counts) == expected_counts,
        "matched_total": len(matched),
        "unique_record_keys": len(set(record_keys)),
        "duplicate_record_keys_count": len(duplicates),
        "doc_type_counts": dict(counts),
        "expected_doc_type_counts": expected_counts,
    }


def _check_ollama(settings: OllamaSettings) -> dict[str, Any]:
    url = f"{settings.base_url.rstrip('/')}/api/tags"
    started = time.monotonic()
    try:
        with urlopen(Request(url, method="GET"), timeout=10) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "url": url,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "elapsed_ms": _elapsed_ms(started),
        }
    models = [
        str(item.get("name"))
        for item in data.get("models", [])
        if isinstance(item, dict) and item.get("name")
    ]
    return {
        "ok": settings.model in models,
        "url": url,
        "model": settings.model,
        "models": models,
        "models_count": len(models),
        "elapsed_ms": _elapsed_ms(started),
    }


def _build_run_config(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    pack_path: Path,
    db_path: Path,
    selected_count: int,
    pending_count: int,
    processed_count: int,
    settings: OllamaSettings,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    git_status = _git_output("status", "--short")
    return {
        "run_id": run_dir.name,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "test_pack": _display_path(pack_path),
        "database": _display_path(db_path),
        "source_version": SOURCE_VERSION,
        "model": settings.model,
        "llm_backend": "ollama",
        "temperature": settings.temperature,
        "max_tokens": settings.num_predict,
        "retrieval_limit": RETRIEVAL_LIMIT,
        "candidate_limit": CANDIDATE_LIMIT,
        "context_limit": CONTEXT_LIMIT,
        "concurrency": args.concurrency,
        "retrieval_only": bool(args.retrieval_only),
        "skip_generation": bool(args.skip_generation),
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_branch": _git_output("branch", "--show-current"),
        "working_tree_dirty": bool(git_status.strip()),
        "git_status_short": git_status,
        "python_version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "total_selected_questions": selected_count,
        "pending_questions": pending_count,
        "processed_questions": processed_count,
        "timeout": args.timeout,
        "filters": {
            "limit": args.limit,
            "group": args.group,
            "start_from": args.start_from,
            "question_id": args.question_id,
        },
        "preflight": preflight,
    }


def _finalize(run_dir: Path, run_config: dict[str, Any]) -> dict[str, Any]:
    records = _read_results(run_dir / RESULTS_NAME)
    total_selected = int(run_config.get("total_selected_questions") or len(records))
    run_config["processed_questions"] = len(records)
    run_config["pending_questions"] = max(total_selected - len(records), 0)
    summary = _build_summary(records, run_config)
    _write_json(run_dir / SUMMARY_NAME, summary)
    _write_retrieval_csv(run_dir / RETRIEVAL_CSV_NAME, records)
    _write_group_summary_csv(run_dir / GROUP_SUMMARY_NAME, summary)
    _write_timings_csv(run_dir / TIMINGS_NAME, records)
    _write_text(run_dir / FAILURES_NAME, _build_failures_md(records))
    _write_text(run_dir / MANUAL_REVIEW_NAME, _build_manual_review_md(records))
    if run_config.get("total_selected_questions") == 360 and not run_config.get("retrieval_only"):
        _write_text(run_dir / FINAL_REPORT_NAME, _build_final_report(records, summary, run_config))
    run_config["finished_at"] = summary.get("finished_at") or datetime.now().isoformat(timespec="seconds")
    _write_json(run_dir / RUN_CONFIG_NAME, run_config)
    return summary


def _build_summary(records: list[dict[str, Any]], run_config: dict[str, Any]) -> dict[str, Any]:
    statuses = Counter(record["evaluation"]["status"] for record in records)
    total = int(run_config.get("total_selected_questions") or len(records))
    completed = len(records)
    retrieval_durations = [record["retrieval"]["duration_ms"] for record in records]
    generation_durations = [record["generation"]["duration_ms"] for record in records]
    failures = Counter(
        record["evaluation"].get("failure_type")
        for record in records
        if record["evaluation"].get("failure_type")
    )
    by_group = _summary_by_group(records)
    by_severity = _summary_by_severity(records)
    zero_results = sum(1 for record in records if record["retrieval"]["result_count"] == 0)
    top1 = _share(record["evaluation"].get("top1_relevant") for record in records)
    top3 = _share(record["evaluation"].get("top3_relevant") for record in records)
    top5 = _share(record["evaluation"].get("top5_relevant") for record in records)
    runtime_errors = sum(1 for record in records if record["generation"].get("error"))
    timeouts = sum(1 for record in records if record["evaluation"].get("failure_type") == "timeout")
    summary = {
        "run_id": run_config["run_id"],
        "total_questions": total,
        "completed": completed,
        "passed": statuses.get("PASS", 0),
        "partial": statuses.get("PARTIAL", 0),
        "failed": statuses.get("FAIL", 0),
        "manual_review": statuses.get("MANUAL_REVIEW", 0),
        "pass_rate": round((statuses.get("PASS", 0) + statuses.get("PARTIAL", 0) * 0.5) / max(1, completed), 4),
        "strict_pass_rate": round(statuses.get("PASS", 0) / max(1, completed), 4),
        "by_group": by_group,
        "by_severity": by_severity,
        "failures_by_type": dict(failures),
        "retrieval": {
            "zero_results": zero_results,
            "top1_recall": top1,
            "top3_recall": top3,
            "top5_recall": top5,
            "zero_result_rate": round(zero_results / max(1, completed), 4),
            "wrong_entity_type_rate": 0.0,
            "average_duration_ms": _avg(retrieval_durations),
            "p95_duration_ms": _p95(retrieval_durations),
        },
        "generation": {
            "average_duration_ms": _avg(generation_durations),
            "p95_duration_ms": _p95(generation_durations),
            "timeouts": timeouts,
            "runtime_errors": runtime_errors,
        },
        "contacts": {
            "missing_contact_count": failures.get("generation_missing_contact", 0),
            "wrong_contact_count": 0,
            "invented_contact_count": failures.get("generation_invented_contact", 0),
        },
        "statuses": {
            "vacancy_errors": failures.get("generation_vacancy_error", 0),
            "inactive_status_errors": failures.get("generation_inactive_status_error", 0),
        },
        "hallucinations": {
            "invented_employee_count": failures.get("generation_invented_employee", 0),
            "operational_data_hallucination_count": failures.get("generation_operational_data_hallucination", 0),
        },
        "dialog": _dialog_summary(records),
        "started_at": run_config.get("started_at"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": _duration_seconds(run_config.get("started_at")),
        "runtime_errors": runtime_errors,
    }
    return summary


def _summary_by_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["group_id"]].append(record)
    result = {}
    for group_id, group_records in sorted(grouped.items()):
        statuses = Counter(record["evaluation"]["status"] for record in group_records)
        result[group_id] = {
            "group_name": group_records[0]["group_name"],
            "total": len(group_records),
            "pass": statuses.get("PASS", 0),
            "partial": statuses.get("PARTIAL", 0),
            "fail": statuses.get("FAIL", 0),
            "manual_review": statuses.get("MANUAL_REVIEW", 0),
            "pass_rate": round(statuses.get("PASS", 0) / max(1, len(group_records)), 4),
            "p0_failures": _severity_failures(group_records, "P0"),
            "p1_failures": _severity_failures(group_records, "P1"),
            "p2_failures": _severity_failures(group_records, "P2"),
            "zero_results": sum(1 for record in group_records if record["retrieval"]["result_count"] == 0),
            "average_duration_ms": _avg(record["total_duration_ms"] for record in group_records),
        }
    return result


def _summary_by_severity(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record.get("severity") or ""].append(record)
    return {
        severity: dict(Counter(record["evaluation"]["status"] for record in severity_records))
        for severity, severity_records in sorted(grouped.items())
    }


def _dialog_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    dialog_records = [record for record in records if record["group_id"] == "ORG18"]
    if not dialog_records:
        return {
            "context_pass_rate": 0.0,
            "pronoun_resolution_failures": 0,
            "wrong_context_carryover": 0,
        }
    passed = sum(1 for record in dialog_records if record["evaluation"]["status"] == "PASS")
    pronoun_failures = sum(
        1
        for record in dialog_records
        if record["evaluation"]["status"] == "FAIL"
        and _has_pronoun_reference(record["question"])
    )
    return {
        "context_pass_rate": round(passed / max(1, len(dialog_records)), 4),
        "pronoun_resolution_failures": pronoun_failures,
        "wrong_context_carryover": 0,
    }


def _write_retrieval_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "id",
        "group_id",
        "question",
        "retrieval_query",
        "result_count",
        "top1_title",
        "top1_doc_type",
        "top1_score",
        "top1_record_key",
        "top1_relevant",
        "expected_keywords_found_in_top5",
        "zero_results",
        "retrieval_duration_ms",
        "failure_type",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            top1 = record["retrieval"]["results"][0] if record["retrieval"]["results"] else {}
            writer.writerow(
                {
                    "id": record["id"],
                    "group_id": record["group_id"],
                    "question": record["question"],
                    "retrieval_query": record["retrieval_query"],
                    "result_count": record["retrieval"]["result_count"],
                    "top1_title": top1.get("title", ""),
                    "top1_doc_type": top1.get("doc_type", ""),
                    "top1_score": top1.get("score", ""),
                    "top1_record_key": top1.get("record_key", ""),
                    "top1_relevant": record["evaluation"].get("top1_relevant"),
                    "expected_keywords_found_in_top5": "; ".join(record["evaluation"].get("keyword_matches", [])),
                    "zero_results": record["retrieval"]["result_count"] == 0,
                    "retrieval_duration_ms": record["retrieval"]["duration_ms"],
                    "failure_type": record["evaluation"].get("failure_type") or "",
                }
            )


def _write_group_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = [
        "group_id",
        "group_name",
        "total",
        "pass",
        "partial",
        "fail",
        "manual_review",
        "pass_rate",
        "p0_failures",
        "p1_failures",
        "p2_failures",
        "zero_results",
        "average_duration_ms",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for group_id, data in summary.get("by_group", {}).items():
            writer.writerow({"group_id": group_id, **data})


def _write_timings_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["id", "retrieval_ms", "generation_ms", "total_ms", "status", "error"])
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "id": record["id"],
                    "retrieval_ms": record["retrieval"]["duration_ms"],
                    "generation_ms": record["generation"]["duration_ms"],
                    "total_ms": record["total_duration_ms"],
                    "status": record["evaluation"]["status"],
                    "error": record["generation"].get("error") or "",
                }
            )


def _build_failures_md(records: list[dict[str, Any]]) -> str:
    lines = ["# Organization Contour Failures", ""]
    selected = [
        record
        for record in records
        if record["evaluation"]["status"] == "FAIL"
        or (
            record["evaluation"]["status"] == "PARTIAL"
            and record.get("severity") in {"P0", "P1"}
        )
    ]
    if not selected:
        lines.append("No FAIL or critical PARTIAL cases.")
        lines.append("")
        return "\n".join(lines)
    for record in selected:
        lines.extend(_failure_case_lines(record))
    return "\n".join(lines)


def _failure_case_lines(record: dict[str, Any]) -> list[str]:
    top = record["retrieval"]["results"][:5]
    lines = [
        f"## {record['id']} — {record['evaluation']['status']}",
        "",
        "Вопрос:",
        record["question"],
        "",
        "Ожидалось:",
        str(record.get("expected_behavior") or ""),
        f"Ключи: {record.get('expected_keywords') or ''}",
        "",
        "Ответ агента:",
        record["generation"].get("answer") or "",
        "",
        "Причина:",
        record["evaluation"].get("failure_type") or "",
        "",
        "Retrieval top-5:",
    ]
    if not top:
        lines.append("Нет результатов.")
    else:
        for item in top:
            lines.append(f"{item['rank']}. {item.get('title')} | {item.get('doc_type')} | {item.get('record_key')}")
    lines.extend(
        [
            "",
            "Контекст модели:",
            "```json",
            record.get("context_sent_to_model") or "[]",
            "```",
            "",
            "Диагноз:",
            _diagnosis(record),
            "",
            "Приоритет:",
            record.get("severity") or "",
            "",
            "Рекомендация:",
            _recommendation(record),
            "",
        ]
    )
    return lines


def _build_manual_review_md(records: list[dict[str, Any]]) -> str:
    lines = ["# Organization Manual Review", ""]
    selected = [
        record
        for record in records
        if record["evaluation"].get("manual_review_required")
        or record["evaluation"]["status"] == "MANUAL_REVIEW"
    ]
    if not selected:
        lines.append("No manual review cases.")
        lines.append("")
        return "\n".join(lines)
    for record in selected:
        lines.extend(
            [
                f"## {record['id']} — {record['evaluation']['status']}",
                "",
                f"- question: {record['question']}",
                f"- expected_behavior: {record.get('expected_behavior') or ''}",
                f"- proposed_status: {record['evaluation']['status']}",
                f"- reason: {record['evaluation'].get('comment') or ''}",
                f"- answer: {record['generation'].get('answer') or ''}",
                "- retrieval:",
            ]
        )
        for item in record["retrieval"]["results"][:5]:
            lines.append(f"  {item['rank']}. {item.get('title')} | {item.get('doc_type')}")
        lines.append("")
    return "\n".join(lines)


def _build_final_report(records: list[dict[str, Any]], summary: dict[str, Any], run_config: dict[str, Any]) -> str:
    lines = [
        "# ОТЧЁТ ПО ТЕСТИРОВАНИЮ ОРГАНИЗАЦИОННОГО КОНТУРА LINEHELPER",
        "",
        "## 1. Конфигурация",
        f"- дата: {summary.get('finished_at')}",
        f"- commit: {run_config.get('git_commit')}",
        f"- модель: {run_config.get('model')}",
        f"- backend: {run_config.get('llm_backend')}",
        f"- база: {run_config.get('database')}",
        f"- версия оргструктуры: {run_config.get('source_version')}",
        f"- вопросов: {summary.get('total_questions')}",
        "",
        "## 2. Общий результат",
        f"- PASS: {summary.get('passed')}",
        f"- PARTIAL: {summary.get('partial')}",
        f"- FAIL: {summary.get('failed')}",
        f"- MANUAL_REVIEW: {summary.get('manual_review')}",
        f"- pass rate: {summary.get('pass_rate')}",
        f"- strict pass rate: {summary.get('strict_pass_rate')}",
        "",
        "## 3. Результаты по группам",
        _markdown_group_table(summary),
        "",
        "## 4. Качество retrieval",
        json.dumps(summary.get("retrieval", {}), ensure_ascii=False, indent=2),
        "",
        "## 5. Качество генерации",
        json.dumps(summary.get("generation", {}), ensure_ascii=False, indent=2),
        "",
        "## 6. Контакты",
        json.dumps(summary.get("contacts", {}), ensure_ascii=False, indent=2),
        "",
        "## 7. Вакансии и статусы",
        json.dumps(summary.get("statuses", {}), ensure_ascii=False, indent=2),
        "",
        "## 8. Совмещения",
        "См. результаты групп и сценарии с role_combination.",
        "",
        "## 9. Диалоговый контекст",
        json.dumps(summary.get("dialog", {}), ensure_ascii=False, indent=2),
        "",
        "## 10. Главные дефекты",
        _top_defects(records),
        "",
        "## 11. Разделение проблем по слоям",
        json.dumps(summary.get("failures_by_type", {}), ensure_ascii=False, indent=2),
        "",
        "## 12. Рекомендуемый план исправлений",
        _recommended_plan(records),
        "",
        "## 13. Вывод",
        _baseline_conclusion(summary),
        "",
    ]
    return "\n".join(lines)


def _expected_terms(scenario: dict[str, Any]) -> list[str]:
    value = str(scenario.get("expected_keywords") or "").strip()
    if not value:
        return []
    separator = ";" if ";" in value else ","
    terms = [part.strip() for part in value.split(separator) if part.strip()]
    return terms if terms else [value]


def _keyword_matches(expected: list[str], text: str) -> tuple[list[str], list[str]]:
    matches: list[str] = []
    missing: list[str] = []
    normalized_text = _normalize_eval_text(text)
    phones_in_text = set(_phones_normalized(text))
    emails_in_text = set(_emails_normalized(text))
    for term in expected:
        term_phones = _phones_normalized(term)
        term_emails = _emails_normalized(term)
        if term_phones:
            ok = all(phone in phones_in_text for phone in term_phones)
        elif term_emails:
            ok = all(email in emails_in_text for email in term_emails)
        else:
            ok = _normalize_eval_text(term) in normalized_text
        if ok:
            matches.append(term)
        else:
            missing.append(term)
    return matches, missing


def _normalize_eval_text(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"(?:\+7|8)[\s()/-]*(\d{3})[\s()/-]*(\d{3})[\s()/-]*(\d{2})[\s()/-]*(\d{2})", r"+7\1\2\3\4", value)
    value = re.sub(r"(\d{2})\.(\d{2})\.(\d{4})", r"\3-\2-\1", value)
    value = re.sub(r"[^\w@.+-]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _phones_normalized(value: str) -> list[str]:
    phones: list[str] = []
    for match in re.finditer(r"(?:\+7|8)[\s()/-]*\d{3}[\s()/-]*\d{3}[\s()/-]*\d{2}[\s()/-]*\d{2}", value):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) == 11 and digits.startswith("8"):
            phones.append("+7" + digits[1:])
        elif len(digits) == 11 and digits.startswith("7"):
            phones.append("+" + digits)
    return phones


def _emails_normalized(value: str) -> list[str]:
    return [match.group(0).lower() for match in re.finditer(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)]


def _terms_present(expected: list[str], text: str) -> bool:
    if not expected:
        return bool(text.strip())
    _, missing = _keyword_matches(expected, text)
    return not missing


def _topn_relevant(expected: list[str], results: list[dict[str, Any]], n: int) -> bool:
    return _terms_present(expected, "\n".join(_result_haystack(item) for item in results[:n]))


def _retrieval_haystack(results: list[dict[str, Any]]) -> str:
    return "\n".join(_result_haystack(item) for item in results)


def _result_haystack(item: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get(key) or "")
        for key in ("title", "doc_type", "record_key", "entity_type", "employee_name", "unit_name", "topic", "phone", "email", "text")
    )


def _invented_contact(answer: str, context_messages: list[dict[str, str]]) -> bool:
    if not answer:
        return False
    context_text = "\n".join(message.get("content", "") for message in context_messages)
    context_phones = set(_phones_normalized(context_text))
    context_emails = set(_emails_normalized(context_text))
    answer_phones = set(_phones_normalized(answer))
    answer_emails = set(_emails_normalized(answer))
    return bool((answer_phones - context_phones) or (answer_emails - context_emails))


def _vacancy_error(scenario: dict[str, Any], answer: str) -> bool:
    expected = " ".join(_expected_terms(scenario)).casefold()
    if "ваканс" not in expected:
        return False
    normalized = _normalize_eval_text(answer)
    return bool(answer.strip()) and "ваканс" not in normalized


def _inactive_error(scenario: dict[str, Any], answer: str) -> bool:
    expected = " ".join(_expected_terms(scenario)).casefold()
    if "не актив" not in expected and "неактив" not in expected:
        return False
    normalized = _normalize_eval_text(answer)
    return bool(answer.strip()) and "не актив" not in normalized and "неактив" not in normalized


def _needs_manual_review(scenario: dict[str, Any]) -> bool:
    text = f"{scenario.get('question', '')} {scenario.get('expected_behavior', '')}".casefold()
    return scenario.get("group_id") == "ORG18" or any(
        marker in text
        for marker in ("уточн", "несколько", "сравн", "аналит", "синтез", "вариант")
    )


def _looks_like_contact(term: str) -> bool:
    return bool(_phones_normalized(term) or _emails_normalized(term))


def _has_pronoun_reference(question: str) -> bool:
    normalized = _normalize_eval_text(question)
    return any(token in normalized.split() for token in ("он", "она", "его", "ее", "её", "это", "эти"))


def _evaluation_comment(
    *,
    status: str,
    missing: list[str],
    retrieval_missing: list[str],
    failure_type: str | None,
    manual_required: bool,
) -> str:
    if status == "PASS":
        return "Все ожидаемые ключевые признаки найдены."
    if status == "MANUAL_REVIEW" or manual_required:
        return "Сценарий требует ручной проверки качества ответа."
    if missing:
        return f"В ответе не найдены: {', '.join(missing)}."
    if retrieval_missing:
        return f"В top-5 retrieval не найдены: {', '.join(retrieval_missing)}."
    return failure_type or ""


def _plan_to_dict(plan: Any) -> dict[str, Any] | None:
    if plan is None:
        return None
    if is_dataclass(plan):
        return asdict(plan)
    if isinstance(plan, dict):
        return dict(plan)
    return {
        key: getattr(plan, key)
        for key in dir(plan)
        if not key.startswith("_") and not callable(getattr(plan, key))
    }


def _processed_ids(path: Path) -> set[str]:
    return {str(record.get("id")) for record in _read_results(path) if record.get("id")}


def _read_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))
    return records


def _severity_failures(records: list[dict[str, Any]], severity: str) -> int:
    return sum(
        1
        for record in records
        if record.get("severity") == severity and record["evaluation"]["status"] == "FAIL"
    )


def _share(values: Any) -> float:
    materialized = [bool(value) for value in values]
    if not materialized:
        return 0.0
    return round(sum(1 for value in materialized if value) / len(materialized), 4)


def _avg(values: Any) -> float:
    materialized = [float(value) for value in values if value is not None]
    return round(statistics.mean(materialized), 3) if materialized else 0.0


def _p95(values: Any) -> float:
    materialized = sorted(float(value) for value in values if value is not None)
    if not materialized:
        return 0.0
    index = min(len(materialized) - 1, int(round((len(materialized) - 1) * 0.95)))
    return round(materialized[index], 3)


def _duration_seconds(started_at: str | None) -> float:
    if not started_at:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0.0
    return round((datetime.now() - started).total_seconds(), 3)


def _diagnosis(record: dict[str, Any]) -> str:
    failure_type = record["evaluation"].get("failure_type")
    if failure_type == "generation_missing_contact":
        return "Поиск нашёл контекст, но ответ не содержит ожидаемый контакт."
    if failure_type == "retrieval_missing_alias":
        return "В top-5 retrieval отсутствуют ожидаемые ключевые сущности."
    if failure_type == "retrieval_wrong_top1":
        return "Релевантность есть в top-5, но top-1 не содержит ожидаемый ответ."
    if failure_type == "generation_invented_contact":
        return "Ответ содержит контакт, которого не было в переданном model context."
    return "См. results.jsonl: требуется разбор слоя по failure_type."


def _recommendation(record: dict[str, Any]) -> str:
    failure_type = record["evaluation"].get("failure_type")
    if failure_type == "generation_missing_contact":
        return "Проверить prompt для маршрутизационных вопросов: требовать phone/email из responsibility_route, если они есть в context."
    if failure_type in {"retrieval_missing_alias", "retrieval_wrong_top1"}:
        return "Проверить алиасы и rerank по entity_type/record_key для данного вопроса; доказательство — top-5 в этом кейсе."
    if failure_type == "generation_invented_contact":
        return "Усилить запрет на контакты вне context и добавить пост-проверку ответа на phone/email."
    return "Минимальное исправление определить после просмотра retrieval top-5 и context в этом кейсе."


def _markdown_group_table(summary: dict[str, Any]) -> str:
    lines = [
        "| group_id | group_name | total | pass | partial | fail | manual_review | pass_rate |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group_id, data in summary.get("by_group", {}).items():
        lines.append(
            f"| {group_id} | {data.get('group_name')} | {data.get('total')} | {data.get('pass')} | "
            f"{data.get('partial')} | {data.get('fail')} | {data.get('manual_review')} | {data.get('pass_rate')} |"
        )
    return "\n".join(lines)


def _top_defects(records: list[dict[str, Any]]) -> str:
    failures = [record for record in records if record["evaluation"]["status"] == "FAIL"]
    if not failures:
        return "Критических FAIL не найдено."
    lines = []
    for severity in ("P0", "P1", "P2"):
        lines.append(f"### {severity}")
        items = [record for record in failures if record.get("severity") == severity]
        if not items:
            lines.append("Нет.")
            continue
        for record in items[:20]:
            lines.append(
                f"- {record['id']}: {record['evaluation'].get('failure_type')} — {record['question']}"
            )
    return "\n".join(lines)


def _recommended_plan(records: list[dict[str, Any]]) -> str:
    failure_types = Counter(
        record["evaluation"].get("failure_type")
        for record in records
        if record["evaluation"].get("failure_type")
    )
    if not failure_types:
        return "Сначала зафиксировать baseline как успешный; исправления не требуются."
    return "\n".join(
        f"{index}. {failure_type}: разобрать {count} кейс(ов) из failures.md."
        for index, (failure_type, count) in enumerate(failure_types.most_common(), start=1)
    )


def _baseline_conclusion(summary: dict[str, Any]) -> str:
    strict = float(summary.get("strict_pass_rate") or 0.0)
    if strict >= 0.9 and int(summary.get("failed") or 0) == 0:
        return "Организационный контур выглядит пригодным для MVP после ручной проверки MANUAL_REVIEW."
    return "До решения P0/P1 дефектов выпускать контур без ограничений не рекомендуется."


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _log(path: Path, message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(line + "\n")


def _git_output(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ""
    return completed.stdout.strip()


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 3)


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
