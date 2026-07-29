"""Run the RAG architecture v2 target pack through the production RAG entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import (  # noqa: E402
    RagAnswerError,
    RagAnswerGenerator,
)
from linehelper.llm.ollama_client import OllamaClient  # noqa: E402
from linehelper.rag.query_analyzer import (  # noqa: E402
    DEFAULT_ANALYZER_MODEL,
    QueryAnalyzer,
)
from linehelper.rag.retriever import (  # noqa: E402
    DEFAULT_MEMORY_DB_PATH,
    SemanticRetriever,
)
from scripts.rag_architecture_v2_harness import (  # noqa: E402
    DEFAULT_FIXTURE,
    FixtureValidationError,
    RecordingRetriever,
    aggregate_metrics,
    apply_evaluation,
    build_repeatability,
    load_fixture,
    render_summary_markdown,
    select_cases,
    selected_context_from_sources,
    source_to_dict,
    unavailable,
    write_json,
    write_jsonl,
)


DEFAULT_OUTPUT_DIR = Path("data/test_runs/rag_architecture_v2")
DEFAULT_RETRIEVAL_LIMIT = 5
DEFAULT_CANDIDATE_LIMIT = 30
OBSERVABILITY_GAPS = {
    "resolved_question": (
        "RagAnswerGenerator.answer() accepts only one question string and does not "
        "expose conversation resolution"
    ),
    "merged_candidates": (
        "RagAnswer exposes selected sources and excluded diagnostic candidates, "
        "but not the post-merge candidate sequence"
    ),
    "evidence_decision": (
        "the evidence gate exposes only the resulting response_kind, not subclaim coverage "
        "or the gate decision"
    ),
    "unsupported_claims": (
        "the current answer contract does not return claim-to-evidence attribution"
    ),
}


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = _parse_args(argv)
    try:
        fixture = load_fixture(DEFAULT_FIXTURE)
        cases = select_cases(
            fixture,
            case_ids=args.case_id,
            group=args.group,
        )
    except (OSError, json.JSONDecodeError, FixtureValidationError) as exc:
        print(f"Fixture error: {type(exc).__name__}: {exc}")
        return 2
    if not cases:
        print("No cases selected.")
        return 2

    out_root = _resolve(args.output_dir)
    run_dir = _new_run_dir(out_root)
    run_dir.mkdir(parents=True, exist_ok=False)
    db_path = _resolve(DEFAULT_MEMORY_DB_PATH)
    answer_client = OllamaClient(model=args.model)
    selected_analyzer_model = _select_analyzer_model(args.analyzer_model)
    analyzer_client = OllamaClient(
        model=selected_analyzer_model,
        temperature=0,
    )
    selected_model = answer_client.model
    preflight = _preflight(
        db_path=db_path,
        retrieval_only=args.retrieval_only,
        models=[selected_model, selected_analyzer_model],
        base_url=answer_client.base_url,
    )
    config = _run_config(
        args=args,
        run_dir=run_dir,
        db_path=db_path,
        selected_model=selected_model,
        selected_analyzer_model=selected_analyzer_model,
        selected_cases=cases,
    )
    write_json(run_dir / "run_config.json", config)
    write_json(run_dir / "preflight.json", preflight)

    print(f"Run directory: {run_dir}")
    print(f"Selected cases: {len(cases)}; repeat: {args.repeat}")
    print(
        f"Mode: {'retrieval-only' if args.retrieval_only else 'full production RAG'}"
    )

    records: list[dict[str, Any]]
    if not preflight["ok"]:
        print("Preflight blocked live execution: " + "; ".join(preflight["errors"]))
        records = _blocked_records(cases, args.repeat, preflight["errors"])
    else:
        try:
            recording_retriever = RecordingRetriever(SemanticRetriever(db_path))
            if args.retrieval_only:
                generator = None
            else:
                analyzer = QueryAnalyzer(
                    ollama_client=analyzer_client,
                    model=selected_analyzer_model,
                )
                generator = RagAnswerGenerator(
                    retriever=recording_retriever,
                    llm_client=answer_client,
                    query_analyzer=analyzer,
                )
        except Exception as exc:
            reason = f"runtime initialization failed: {type(exc).__name__}: {exc}"
            print(reason)
            records = _blocked_records(cases, args.repeat, [reason])
        else:
            records = []
            for case in cases:
                for repeat_index in range(1, args.repeat + 1):
                    if args.verbose:
                        print(
                            f"[{len(records) + 1}/{len(cases) * args.repeat}] "
                            f"{case['id']} repeat={repeat_index}"
                        )
                    if args.retrieval_only:
                        diagnostic = _run_retrieval_only_case(
                            case,
                            repeat_index=repeat_index,
                            retriever=recording_retriever,
                        )
                    else:
                        assert generator is not None
                        diagnostic = _run_full_case(
                            case,
                            repeat_index=repeat_index,
                            generator=generator,
                            retriever=recording_retriever,
                        )
                    records.append(apply_evaluation(case, diagnostic))
                    if args.verbose:
                        print(
                            f"  status={records[-1]['status']} "
                            f"intent={_display(records[-1].get('intent'))} "
                            f"mode={_display(records[-1].get('response_kind'))}"
                        )

    metrics = aggregate_metrics(cases, records)
    repeatability = build_repeatability(records)
    baseline = {
        "schema_version": "2.0",
        "generated_at": _now_iso(),
        "run_id": run_dir.name,
        "config": config,
        "preflight": preflight,
        "fixture": {
            "path": str(DEFAULT_FIXTURE),
            "schema_version": fixture["schema_version"],
            "selected_case_ids": [case["id"] for case in cases],
            "selected_count": len(cases),
        },
        "metrics": metrics,
        "repeatability": repeatability,
        "observability_gaps": OBSERVABILITY_GAPS,
        "cases": records,
    }
    write_json(run_dir / "baseline.json", baseline)
    write_jsonl(run_dir / "cases.jsonl", records)
    (run_dir / "summary.md").write_text(
        render_summary_markdown(
            title="RAG architecture v2 baseline run",
            metrics=metrics,
            records=records,
            repeatability=repeatability,
        ),
        encoding="utf-8",
    )

    print(
        "Summary: total={total_cases} passed={passed} failed={failed} "
        "blocked={blocked} not_applicable={not_applicable}".format(**metrics)
    )
    print(f"Machine-readable baseline: {run_dir / 'baseline.json'}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the LineHelper RAG architecture v2 baseline through "
            "RagAnswerGenerator or in explicit retrieval-only mode."
        )
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="Run one case id; repeat the option to select several cases.",
    )
    parser.add_argument("--group", default=None)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None)
    parser.add_argument("--analyzer-model", default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    return args


def _run_full_case(
    case: dict[str, Any],
    *,
    repeat_index: int,
    generator: RagAnswerGenerator,
    retriever: RecordingRetriever,
) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    executed_messages: list[dict[str, str]] = []
    for message in case["messages"]:
        executed_messages.append(dict(message))
        if message["role"] != "user":
            continue
        retriever.reset()
        question = str(message["content"])
        started = time.monotonic()
        try:
            result = generator.answer(
                question,
                retrieval_limit=DEFAULT_RETRIEVAL_LIMIT,
                candidate_limit=DEFAULT_CANDIDATE_LIMIT,
            )
        except RagAnswerError as exc:
            turn = _empty_diagnostic(
                case_id=str(case["id"]),
                messages=executed_messages,
                original_question=question,
                repeat_index=repeat_index,
                reason=f"{type(exc).__name__}: {exc}",
                status="blocked",
            )
            turn["duration_ms"] = _elapsed_ms(started)
            turns.append(turn)
            break
        except Exception as exc:
            turn = _empty_diagnostic(
                case_id=str(case["id"]),
                messages=executed_messages,
                original_question=question,
                repeat_index=repeat_index,
                reason=f"{type(exc).__name__}: {exc}",
                status="failed",
            )
            turn["duration_ms"] = _elapsed_ms(started)
            turn["traceback"] = traceback.format_exc()
            turns.append(turn)
            break

        raw_candidates = retriever.flattened_candidates()
        selected_context = selected_context_from_sources(
            result.sources,
            raw_candidates,
        )
        query_plan = result.query_plan or unavailable(
            "RagAnswer did not expose QueryPlan diagnostics"
        )
        intent = (
            query_plan.get("intent")
            if isinstance(query_plan, dict) and query_plan.get("intent")
            else unavailable("intent is absent from QueryPlan diagnostics")
        )
        requested_fact_type = (
            query_plan.get("requested_fact_type")
            if isinstance(query_plan, dict)
            and query_plan.get("requested_fact_type")
            else unavailable(
                "requested_fact_type is absent from QueryPlan diagnostics"
            )
        )
        temporal_scope = (
            query_plan.get("temporal_scope")
            if isinstance(query_plan, dict) and query_plan.get("temporal_scope")
            else unavailable("temporal_scope is absent from QueryPlan diagnostics")
        )
        subject = (
            query_plan.get("subject")
            if isinstance(query_plan, dict) and query_plan.get("subject")
            else unavailable("subject is absent from QueryPlan diagnostics")
        )
        operational_boundary = (
            {
                "available": True,
                "operational_lookup": query_plan["operational_lookup"],
                "decision_reason": query_plan.get(
                    "operational_decision_reason",
                    "not_exposed",
                ),
                "derived_from": "query_plan.operational_lookup",
                "native_decision_exposed": True,
            }
            if isinstance(query_plan, dict)
            and isinstance(query_plan.get("operational_lookup"), bool)
            else unavailable(
                "operational_lookup is absent from QueryPlan diagnostics"
            )
        )
        clarification = (
            {
                "available": True,
                "needed": query_plan["validated_clarification_required"],
                "raw_required": query_plan.get(
                    "raw_clarification_required",
                    False,
                ),
                "validated_required": query_plan[
                    "validated_clarification_required"
                ],
                "raw_kind": query_plan.get("raw_clarification_kind", "none"),
                "validated_kind": query_plan.get(
                    "validated_clarification_kind",
                    "none",
                ),
                "raw_ambiguity_span": query_plan.get("raw_ambiguity_span"),
                "ambiguity_span": query_plan.get("validated_ambiguity_span"),
                "raw_candidate_meanings": query_plan.get(
                    "raw_candidate_meanings",
                    [],
                ),
                "candidate_meanings": query_plan.get(
                    "validated_candidate_meanings",
                    [],
                ),
                "raw_missing_slots": query_plan.get("raw_missing_slots", []),
                "missing_slots": query_plan.get("validated_missing_slots", []),
                "raw_question": query_plan.get("raw_clarification_question"),
                "question": query_plan.get(
                    "validated_clarification_question"
                ),
                "action": query_plan.get(
                    "clarification_action",
                    "continue_retrieval",
                ),
                "validation_reasons": query_plan.get(
                    "clarification_validation_reasons",
                    [],
                ),
                "retrieval_started": bool(retriever.calls),
                "derived_from": "native_query_plan_clarification",
            }
            if isinstance(query_plan, dict)
            and isinstance(
                query_plan.get("validated_clarification_required"),
                bool,
            )
            else unavailable(
                "structured clarification is absent from QueryPlan diagnostics"
            )
        )
        turn = {
            "case_id": case["id"],
            "repeat_index": repeat_index,
            "messages": [dict(value) for value in executed_messages],
            "original_question": question,
            "resolved_question": unavailable(OBSERVABILITY_GAPS["resolved_question"]),
            "query_plan": query_plan,
            "requested_fact_type": requested_fact_type,
            "raw_requested_fact_type": (
                query_plan.get("raw_requested_fact_type")
                if isinstance(query_plan, dict)
                else unavailable("QueryPlan diagnostics are unavailable")
            ),
            "temporal_scope": temporal_scope,
            "raw_temporal_scope": (
                query_plan.get("raw_temporal_scope")
                if isinstance(query_plan, dict)
                else unavailable("QueryPlan diagnostics are unavailable")
            ),
            "subject": subject,
            "raw_subject": (
                query_plan.get("raw_subject")
                if isinstance(query_plan, dict)
                else unavailable("QueryPlan diagnostics are unavailable")
            ),
            "raw_intent": (
                query_plan.get("raw_intent")
                if isinstance(query_plan, dict)
                else unavailable("QueryPlan diagnostics are unavailable")
            ),
            "intent": intent,
            "clarification": clarification,
            "operational_boundary": operational_boundary,
            "retrieval_queries": [call["query"] for call in retriever.calls],
            "raw_candidates": raw_candidates,
            "merged_candidates": unavailable(
                OBSERVABILITY_GAPS["merged_candidates"]
            ),
            "selected_context": selected_context,
            "evidence_decision": unavailable(
                OBSERVABILITY_GAPS["evidence_decision"]
            ),
            "answer": result.answer,
            "sources": [source_to_dict(source) for source in result.sources],
            "unsupported_claims": unavailable(
                OBSERVABILITY_GAPS["unsupported_claims"]
            ),
            "duration_ms": _elapsed_ms(started),
            "response_kind": result.response_kind,
            "status": "passed",
            "failure_reasons": [],
        }
        turns.append(turn)
        executed_messages.append({"role": "assistant", "content": result.answer})

    if not turns:
        return _empty_diagnostic(
            case_id=str(case["id"]),
            messages=executed_messages,
            original_question="",
            repeat_index=repeat_index,
            reason="case contains no executable user turn",
            status="failed",
        )
    diagnostic = dict(turns[-1])
    diagnostic["messages"] = [dict(value) for value in case["messages"]]
    diagnostic["turns"] = turns
    if any(turn.get("status") == "blocked" for turn in turns):
        diagnostic["status"] = "blocked"
        diagnostic["failure_reasons"] = [
            reason
            for turn in turns
            for reason in turn.get("failure_reasons") or []
        ]
    elif any(turn.get("status") == "failed" for turn in turns):
        diagnostic["status"] = "failed"
        diagnostic["failure_reasons"] = [
            reason
            for turn in turns
            for reason in turn.get("failure_reasons") or []
        ]
    return diagnostic


def _run_retrieval_only_case(
    case: dict[str, Any],
    *,
    repeat_index: int,
    retriever: RecordingRetriever,
) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    for message in case["messages"]:
        if message["role"] != "user":
            continue
        retriever.reset()
        question = str(message["content"])
        started = time.monotonic()
        try:
            retriever.retrieve(
                question,
                limit=10,
                candidate_limit=DEFAULT_CANDIDATE_LIMIT,
            )
        except Exception as exc:
            turn = _empty_diagnostic(
                case_id=str(case["id"]),
                messages=case["messages"],
                original_question=question,
                repeat_index=repeat_index,
                reason=f"retrieval failed: {type(exc).__name__}: {exc}",
                status="failed",
            )
        else:
            turn = {
                "case_id": case["id"],
                "repeat_index": repeat_index,
                "messages": [dict(value) for value in case["messages"]],
                "original_question": question,
                "resolved_question": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "query_plan": unavailable("not executed in retrieval-only mode"),
                "requested_fact_type": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "raw_requested_fact_type": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "temporal_scope": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "raw_temporal_scope": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "subject": unavailable("not executed in retrieval-only mode"),
                "raw_subject": unavailable("not executed in retrieval-only mode"),
                "raw_intent": unavailable("not executed in retrieval-only mode"),
                "intent": unavailable("not executed in retrieval-only mode"),
                "clarification": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "operational_boundary": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "retrieval_queries": [call["query"] for call in retriever.calls],
                "raw_candidates": retriever.flattened_candidates(),
                "merged_candidates": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "selected_context": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "evidence_decision": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "answer": unavailable("not executed in retrieval-only mode"),
                "sources": unavailable("not executed in retrieval-only mode"),
                "unsupported_claims": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "duration_ms": _elapsed_ms(started),
                "response_kind": unavailable(
                    "not executed in retrieval-only mode"
                ),
                "status": "passed",
                "failure_reasons": [],
            }
        turns.append(turn)
    diagnostic = dict(turns[-1])
    diagnostic["turns"] = turns
    return diagnostic


def _blocked_records(
    cases: list[dict[str, Any]],
    repeat: int,
    reasons: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for case in cases:
        user_messages = [
            message for message in case["messages"] if message["role"] == "user"
        ]
        original_question = str(user_messages[-1]["content"]) if user_messages else ""
        for repeat_index in range(1, repeat + 1):
            diagnostic = _empty_diagnostic(
                case_id=str(case["id"]),
                messages=case["messages"],
                original_question=original_question,
                repeat_index=repeat_index,
                reason="; ".join(reasons),
                status="blocked",
            )
            records.append(apply_evaluation(case, diagnostic))
    return records


def _empty_diagnostic(
    *,
    case_id: str,
    messages: list[dict[str, Any]],
    original_question: str,
    repeat_index: int,
    reason: str,
    status: str,
) -> dict[str, Any]:
    marker = unavailable(reason)
    return {
        "case_id": case_id,
        "repeat_index": repeat_index,
        "messages": [dict(value) for value in messages],
        "original_question": original_question,
        "resolved_question": marker,
        "query_plan": marker,
        "requested_fact_type": marker,
        "raw_requested_fact_type": marker,
        "temporal_scope": marker,
        "raw_temporal_scope": marker,
        "subject": marker,
        "raw_subject": marker,
        "raw_intent": marker,
        "intent": marker,
        "clarification": marker,
        "operational_boundary": marker,
        "retrieval_queries": marker,
        "raw_candidates": marker,
        "merged_candidates": marker,
        "selected_context": marker,
        "evidence_decision": marker,
        "answer": marker,
        "sources": marker,
        "unsupported_claims": marker,
        "duration_ms": 0,
        "response_kind": marker,
        "status": status,
        "failure_reasons": [reason],
    }


def _preflight(
    *,
    db_path: Path,
    retrieval_only: bool,
    models: list[str],
    base_url: str,
) -> dict[str, Any]:
    errors: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "retrieval_only": retrieval_only,
        "ollama": {"required": not retrieval_only},
        "errors": errors,
    }
    if not db_path.exists():
        errors.append(f"memory DB not found: {db_path}")
    if retrieval_only:
        result["ok"] = not errors
        return result

    url = f"{base_url.rstrip('/')}/api/tags"
    started = time.monotonic()
    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - local Ollama only.
            payload = json.loads(response.read().decode("utf-8"))
        available_models = [
            str(item.get("name"))
            for item in payload.get("models", [])
            if isinstance(item, dict) and item.get("name")
        ]
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        result["ollama"] = {
            "required": True,
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": _elapsed_ms(started),
        }
        errors.append(f"Ollama preflight failed: {type(exc).__name__}: {exc}")
    else:
        missing_models = [
            model for model in models if not _model_available(model, available_models)
        ]
        result["ollama"] = {
            "required": True,
            "ok": not missing_models,
            "url": url,
            "available_models": available_models,
            "requested_models": models,
            "missing_models": missing_models,
            "duration_ms": _elapsed_ms(started),
        }
        if missing_models:
            errors.append("Ollama model(s) unavailable: " + ", ".join(missing_models))
    result["ok"] = not errors
    return result


def _run_config(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    db_path: Path,
    selected_model: str,
    selected_analyzer_model: str,
    selected_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "created_at": _now_iso(),
        "run_dir": str(run_dir),
        "fixture": str(DEFAULT_FIXTURE),
        "db_path": str(db_path),
        "mode": "retrieval_only" if args.retrieval_only else "full_pipeline",
        "model": selected_model,
        "analyzer_model": selected_analyzer_model,
        "repeat": args.repeat,
        "case_ids": [case["id"] for case in selected_cases],
        "group": args.group,
        "python": sys.version,
        "platform": platform.platform(),
        "git": {
            "branch": _git("branch", "--show-current"),
            "commit": _git("rev-parse", "HEAD"),
            "status_short": _git("status", "--short"),
        },
        "environment": {
            "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", ""),
            "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", ""),
            "OLLAMA_ANALYZER_MODEL": os.getenv("OLLAMA_ANALYZER_MODEL", ""),
        },
    }


def _git(*args: str) -> str:
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
    except OSError as exc:
        return f"unavailable: {exc}"
    return completed.stdout.strip() if completed.returncode == 0 else completed.stderr.strip()


def _new_run_dir(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = root / stamp
    suffix = 1
    while candidate.exists():
        candidate = root / f"{stamp}_{suffix:02d}"
        suffix += 1
    return candidate


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _model_available(requested: str, available: list[str]) -> bool:
    normalized = requested.removesuffix(":latest")
    return any(
        item == requested
        or item.removesuffix(":latest") == normalized
        for item in available
    )


def _select_analyzer_model(requested: str | None) -> str:
    return requested or os.getenv("OLLAMA_ANALYZER_MODEL") or DEFAULT_ANALYZER_MODEL


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _display(value: Any) -> str:
    if isinstance(value, dict) and value.get("available") is False:
        return "not_available"
    return str(value)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
