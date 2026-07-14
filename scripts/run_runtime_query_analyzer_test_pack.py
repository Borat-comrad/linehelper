"""Run the runtime Query Analyzer RAG test pack and build level-1 reports."""

from __future__ import annotations

import argparse
import csv
import json
import os
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

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator  # noqa: E402
from linehelper.rag.retriever import DEFAULT_MEMORY_DB_PATH  # noqa: E402


DEFAULT_PACK = Path("docs/test_packs/linehelper_runtime_query_analyzer_test_pack_360.csv")
DEFAULT_OUT_DIR = Path("data/test_runs/runtime_query_analyzer")
DEFAULT_DB_PATH = DEFAULT_MEMORY_DB_PATH

JSONL_NAME = "results.jsonl"
CSV_NAME = "results.csv"
REPORT_NAME = "report_level1.md"
SUMMARY_NAME = "summary.json"
FAILURES_NAME = "failures.md"
WARNINGS_NAME = "warnings.md"
RUN_CONFIG_NAME = "run_config.json"

CSV_FIELDS = [
    "question_id",
    "group_id",
    "group_name",
    "question",
    "expected_intent",
    "expected_behavior",
    "expected_response_kind",
    "expected_source_hint",
    "risk_tag",
    "response_kind",
    "answer_text",
    "answer_preview",
    "sources_count",
    "sources_titles",
    "top_source_title",
    "top_source_section",
    "query_plan_enabled",
    "query_plan_intent",
    "query_plan_answer_type",
    "query_plan_normalized_question",
    "query_plan_expansions",
    "query_plan_preferred_sources",
    "query_plan_confidence",
    "query_plan_fallback_used",
    "query_plan_error",
    "diagnostic_candidates_count",
    "prompt_length",
    "chunks_used",
    "latency_total_sec",
    "latency_analyzer_sec",
    "latency_answer_sec",
    "error",
    "error_type",
    "error_message",
    "error_stage",
    "error_traceback",
    "verdict",
    "flags",
]

GROUP_EXPECTED_INTENTS: dict[str, set[str]] = {
    "G01": {"org_structure"},
    "G02": {"roles_responsibility", "org_structure"},
    "G03": {"company_identity"},
    "G04": {"company_ckp"},
    "G05": {"ambiguous_abbreviation", "kp_commercial_offer"},
    "G06": {"zrs_definition", "zrs_approval"},
    "G07": {"document_flow"},
    "G08": {"contract_approval", "document_flow"},
    "G09": {"vacation", "business_trip"},
    "G10": {"task_management", "order_disposition"},
    "G11": {"weekly_planning", "statistics_kpi"},
    "G12": {"onboarding_position", "written_communication"},
    "G13": {"equipment_it_request"},
    "G14": {"attendance_absence"},
    "G15": {"document_loss", "document_flow"},
    "G16": {"one_c_operational_lookup"},
    "G17": {"comparison"},
    "G18": {"clarification", "no_answer", "off_topic", "unknown"},
}

RELAXED_GROUPS = {"G17", "G18"}
NO_SOURCE_INTENTS = {
    "ambiguous_abbreviation",
    "attendance_absence",
    "document_loss",
    "equipment_it_request",
    "kp_commercial_offer",
    "off_topic",
    "one_c_operational_lookup",
    "unknown",
}
KNOWN_CORPORATE_INTENTS = {
    "company_identity",
    "company_ckp",
    "org_structure",
    "roles_responsibility",
    "zrs_definition",
    "zrs_approval",
    "vacation",
    "document_flow",
    "contract_approval",
    "business_trip",
    "order_disposition",
    "task_management",
    "weekly_planning",
    "statistics_kpi",
    "onboarding_position",
    "written_communication",
}

OPERATIONAL_1C_KEYWORDS = (
    "цена",
    "цену",
    "стоимость",
    "остатки",
    "остаток",
    "статус заказа",
    "заказ",
    "счет",
    "счёт",
    "счета",
    "счёта",
    "контрагент",
    "контрагента",
    "контрагенты",
    "отгрузка",
    "отгрузку",
    "номенклатура",
)
OFF_TOPIC_KEYWORDS = (
    "борщ",
    "погода",
    "стих",
    "кот",
    "матч",
    "утят",
    "игнорируй инструкции",
)
ATTENDANCE_KEYWORDS = (
    "опоздал",
    "опоздание",
    "заболел",
    "болею",
    "не вышел",
    "не выйду",
    "невыход",
    "отсутствие",
    "больничный",
)
ORG_KEYWORDS = (
    "отдел",
    "отделы",
    "подраздел",
    "подразделения",
    "оргструктур",
    "оргсхем",
    "структур",
)
COMPANY_IDENTITY_KEYWORDS = (
    "чем занимается компания",
    "что делает компания",
    "цель компании",
    "цель serviceline",
    "для чего существует компания",
)
AMBIGUOUS_KP_QUESTIONS = {"кп", "что такое кп", "что такое кп?", "что значит кп", "что значит кп?"}
FORBIDDEN_CKP_PHRASE = "центр комплексных предложений"


class TimedQueryAnalyzer:
    """Small timing wrapper around the production analyzer client."""

    def __init__(self) -> None:
        from linehelper.rag.query_analyzer import QueryAnalyzer

        self._inner = QueryAnalyzer()
        self.last_elapsed_seconds: float | None = None
        self.model = getattr(self._inner, "model", None)

    def analyze(self, question: str) -> Any:
        started_at = time.monotonic()
        try:
            return self._inner.analyze(question)
        finally:
            self.last_elapsed_seconds = round(time.monotonic() - started_at, 3)


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = _parse_args(argv)
    _set_default_runtime_env()

    pack_path = _resolve_path(args.pack)
    out_root = _resolve_path(args.out_dir)
    rows = _read_pack(pack_path)
    selected_rows = _select_rows(rows, group_filter=args.group, limit=args.limit)
    run_dir = _prepare_run_dir(out_root, resume=args.resume)
    processed_ids = _load_processed_question_ids(run_dir / JSONL_NAME) if args.resume else set()
    rows_to_run = [
        row for row in selected_rows if str(row["question_id"]) not in processed_ids
    ]

    run_config = _build_run_config(
        args=args,
        pack_path=pack_path,
        out_root=out_root,
        run_dir=run_dir,
        selected_count=len(selected_rows),
        pending_count=len(rows_to_run),
        processed_count=len(processed_ids),
    )
    print(f"Run directory: {run_dir}")
    print(f"Selected questions: {len(selected_rows)}")
    if processed_ids:
        print(f"Resume: skipping {len(processed_ids)} already processed question(s)")

    db_path = _resolve_path(DEFAULT_DB_PATH)
    run_config["memory_db_path"] = str(db_path)
    if args.debug:
        _debug_environment(run_config, db_path)

    if not db_path.exists():
        run_config["startup_error"] = {
            "stage": "init_generator",
            "message": f"Memory DB not found: {db_path}",
        }
        _write_json(run_dir / RUN_CONFIG_NAME, run_config)
        _finalize_run(run_dir, [], run_config)
        print(f"Memory DB not found: {db_path}")
        return 1

    preflight = _preflight_ollama()
    run_config["ollama_preflight"] = preflight
    _write_json(run_dir / RUN_CONFIG_NAME, run_config)
    if args.debug:
        print(f"[debug] preflight: {preflight}")
    if not preflight["ok"]:
        _finalize_run(run_dir, [], run_config)
        print(f"Ollama preflight failed: {preflight['error']}")
        return 1

    try:
        if args.debug:
            print("[debug] stage=init_generator")
        analyzer = TimedQueryAnalyzer()
        generator = RagAnswerGenerator(db_path=db_path, query_analyzer=analyzer)
    except Exception as exc:
        record = _startup_error_record(
            stage="init_generator",
            exc=exc,
            selected_count=len(selected_rows),
        )
        _write_jsonl_records(run_dir / JSONL_NAME, [record], append=False)
        records = _read_jsonl(run_dir / JSONL_NAME)
        _finalize_run(run_dir, records, run_config)
        if args.debug:
            print(traceback.format_exc())
        return 1

    with (run_dir / JSONL_NAME).open("a", encoding="utf-8", newline="\n") as jsonl_file:
        for index, row in enumerate(rows_to_run, start=1):
            question_id = row["question_id"]
            question = row["question"]
            print(f"[{index}/{len(rows_to_run)}] {question_id}: {question}")

            if args.debug:
                print(f"[debug] question={question}")
                print("[debug] stage=agent_answer")
            record = _run_question(
                row,
                generator=generator,
                analyzer=analyzer,
                debug=args.debug,
            )
            jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            jsonl_file.flush()

            if args.sleep > 0:
                time.sleep(args.sleep)

    records = _read_jsonl(run_dir / JSONL_NAME)
    summary = _finalize_run(run_dir, records, run_config)

    print()
    print("Summary:")
    print(
        "total={total} PASS={pass} WARN={warn} FAIL={fail} ERROR={error}".format(
            **summary
        )
    )
    print(f"Report: {run_dir / REPORT_NAME}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LineHelper runtime Query Analyzer test pack.",
    )
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--group", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def _set_default_runtime_env() -> None:
    os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
    os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:3b")
    os.environ.setdefault("OLLAMA_ANALYZER_MODEL", "qwen2.5:3b")


def _debug_environment(run_config: dict[str, Any], db_path: Path) -> None:
    print("[debug] environment:")
    for name in (
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "OLLAMA_ANALYZER_MODEL",
    ):
        print(f"[debug]   {name}={os.getenv(name) or ''}")
    print(f"[debug] memory_db_path={db_path}")
    print(f"[debug] run_dir={run_config['run_dir']}")


def _preflight_ollama() -> dict[str, Any]:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    url = f"{base_url}/api/tags"
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    started_at = time.monotonic()
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - local Ollama endpoint.
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
    except HTTPError as exc:
        return {
            "ok": False,
            "stage": "ollama_preflight",
            "url": url,
            "error_type": type(exc).__name__,
            "error": f"HTTP {exc.code}: {exc.reason}",
            "elapsed_sec": round(time.monotonic() - started_at, 3),
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "stage": "ollama_preflight",
            "url": url,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "elapsed_sec": round(time.monotonic() - started_at, 3),
        }

    models = data.get("models") if isinstance(data, dict) else []
    model_names = [
        str(model.get("name") or "")
        for model in models
        if isinstance(model, dict) and model.get("name")
    ]
    return {
        "ok": True,
        "stage": "ollama_preflight",
        "url": url,
        "models_count": len(model_names),
        "models": model_names,
        "elapsed_sec": round(time.monotonic() - started_at, 3),
    }


def _finalize_run(
    run_dir: Path,
    records: list[dict[str, Any]],
    run_config: dict[str, Any],
) -> dict[str, Any]:
    if not (run_dir / JSONL_NAME).exists():
        (run_dir / JSONL_NAME).write_text("", encoding="utf-8")
    _write_results_csv(run_dir / CSV_NAME, records)
    summary = _build_summary(records)
    _write_json(run_dir / SUMMARY_NAME, summary)
    _write_text(run_dir / REPORT_NAME, _build_level1_report(records, summary, run_config))
    _write_text(run_dir / FAILURES_NAME, _build_failures_report(records))
    _write_text(run_dir / WARNINGS_NAME, _build_warnings_report(records))
    return summary


def _startup_error_record(
    *,
    stage: str,
    exc: Exception,
    selected_count: int,
) -> dict[str, Any]:
    return {
        "question_id": "__startup__",
        "group_id": "",
        "group_name": "",
        "question": f"Startup before {selected_count} selected question(s)",
        "expected_intent": "",
        "expected_behavior": "",
        "expected_response_kind": "",
        "expected_source_hint": "",
        "risk_tag": "",
        "response_kind": "",
        "answer_text": "",
        "answer_preview": "",
        "sources_count": 0,
        "sources_titles": [],
        "top_source_title": "",
        "top_source_section": "",
        "query_plan_enabled": None,
        "query_plan_intent": None,
        "query_plan_answer_type": None,
        "query_plan_normalized_question": None,
        "query_plan_expansions": [],
        "query_plan_preferred_sources": [],
        "query_plan_confidence": None,
        "query_plan_fallback_used": False,
        "query_plan_error": None,
        "diagnostic_candidates_count": 0,
        "prompt_length": None,
        "chunks_used": None,
        "latency_total_sec": None,
        "latency_analyzer_sec": None,
        "latency_answer_sec": None,
        "error": f"{type(exc).__name__}: {exc}",
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "error_stage": stage,
        "error_traceback": traceback.format_exc(),
        "verdict": "ERROR",
        "flags": ["AGENT_ERROR"],
    }


def _write_jsonl_records(
    path: Path,
    records: list[dict[str, Any]],
    *,
    append: bool,
) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _infer_error_stage(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".casefold()
    if "query analyzer" in text or "query_plan" in text:
        return "query_analyzer"
    if "ollama" in text or "api/chat" in text or "model" in text:
        return "ollama_answer"
    if "retriev" in text or "fts" in text or "sqlite" in text or "database" in text:
        return "retrieval"
    if "prompt" in text:
        return "prompt_builder"
    if "json" in text or "serializ" in text:
        return "result_serialization"
    return "agent_answer"


def _read_pack(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"test pack not found: {path}")

    sample = path.read_text(encoding="utf-8-sig")[:4096]
    dialect = csv.Sniffer().sniff(sample, delimiters=";,	,")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        columns = [column.strip() for column in reader.fieldnames]
        mapping = _resolve_columns(columns)
        rows = []
        for index, raw_row in enumerate(reader, start=1):
            clean_row = {
                str(key).strip(): (value.strip() if isinstance(value, str) else value)
                for key, value in raw_row.items()
                if key is not None
            }
            question = _get_column(clean_row, mapping["question"])
            if not question:
                continue
            question_id = _get_column(clean_row, mapping.get("question_id")) or f"Q{index:03d}"
            group_value = _get_column(clean_row, mapping.get("group")) or ""
            group_id, group_name = _split_group(group_value)
            rows.append(
                {
                    "question_id": question_id,
                    "group_raw": group_value,
                    "group_id": group_id,
                    "group_name": group_name,
                    "question": question,
                    "expected_intent": _get_column(clean_row, mapping.get("expected_intent")),
                    "expected_behavior": _get_column(clean_row, mapping.get("expected_behavior")),
                    "expected_response_kind": _get_column(
                        clean_row,
                        mapping.get("expected_response_kind"),
                    ),
                    "expected_source_hint": _get_column(
                        clean_row,
                        mapping.get("expected_source_hint"),
                    ),
                    "risk_tag": _get_column(clean_row, mapping.get("risk_tag")),
                    "diagnostic_focus": _get_column(clean_row, mapping.get("diagnostic_focus")),
                    "raw": clean_row,
                }
            )

    if not rows:
        raise ValueError(f"CSV has no runnable questions: {path}")
    return rows


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    aliases = {
        "question_id": ("id", "question_id", "qid", "номер", "номер вопроса", "n"),
        "group": ("group", "diagnostic_group", "group_id", "группа", "diagnostic group"),
        "question": ("question", "вопрос", "query", "text", "prompt"),
        "expected_intent": ("expected_intent", "intent", "expected intent"),
        "expected_behavior": (
            "expected_behavior",
            "expected behaviour",
            "expected",
            "notes",
            "note",
            "expected_notes",
        ),
        "expected_response_kind": (
            "expected_response_kind",
            "expected_kind",
            "expected answer kind",
            "response_kind",
        ),
        "expected_source_hint": (
            "expected_source_hint",
            "source_hint",
            "expected_source",
            "source",
        ),
        "risk_tag": ("risk_tag", "risk", "tag"),
        "diagnostic_focus": ("diagnostic_focus", "focus"),
    }
    normalized_to_column = {_normalize_column(column): column for column in columns}
    resolved: dict[str, str] = {}
    for target, target_aliases in aliases.items():
        for alias in target_aliases:
            column = normalized_to_column.get(_normalize_column(alias))
            if column:
                resolved[target] = column
                break

    if "question" not in resolved:
        raise ValueError(
            "CSV must contain a question column; tried aliases: "
            + ", ".join(aliases["question"])
        )
    return resolved


def _normalize_column(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").split())


def _get_column(row: dict[str, Any], column: str | None) -> str:
    if not column:
        return ""
    value = row.get(column)
    return value.strip() if isinstance(value, str) else ""


def _split_group(value: str) -> tuple[str, str]:
    clean_value = value.strip()
    if not clean_value:
        return "", ""
    parts = clean_value.split(maxsplit=1)
    group_id = parts[0].strip()
    group_name = parts[1].strip() if len(parts) > 1 else ""
    return group_id, group_name


def _select_rows(
    rows: list[dict[str, Any]],
    *,
    group_filter: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected = rows
    if group_filter:
        needle = group_filter.casefold().strip()
        selected = [
            row
            for row in selected
            if needle in str(row["group_id"]).casefold()
            or needle in str(row["group_name"]).casefold()
            or needle in str(row["group_raw"]).casefold()
        ]
    if limit is not None:
        if limit < 0:
            raise ValueError("--limit must be >= 0")
        selected = selected[:limit]
    return selected


def _prepare_run_dir(out_root: Path, *, resume: bool) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    if resume:
        existing = sorted(
            path
            for path in out_root.iterdir()
            if path.is_dir() and (path / JSONL_NAME).exists()
        )
        if existing:
            return existing[-1]

    run_dir = out_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _load_processed_question_ids(path: Path) -> set[str]:
    return {
        str(record.get("question_id"))
        for record in _read_jsonl(path)
        if record.get("question_id")
    }


def _run_question(
    row: dict[str, Any],
    *,
    generator: RagAnswerGenerator,
    analyzer: TimedQueryAnalyzer,
    debug: bool,
) -> dict[str, Any]:
    analyzer.last_elapsed_seconds = None
    started_at = time.monotonic()
    error = ""
    error_type = ""
    error_message = ""
    error_traceback = ""
    error_stage = ""
    result: Any | None = None

    try:
        result = generator.answer(str(row["question"]))
    except RagAnswerError as exc:
        error = f"RagAnswerError: {exc}"
        error_type = type(exc).__name__
        error_message = str(exc)
        error_traceback = traceback.format_exc()
        error_stage = _infer_error_stage(exc)
        if debug:
            print(f"[debug] error_stage={error_stage}")
            print(error_traceback)
    except Exception as exc:  # pragma: no cover - defensive runner boundary
        error = f"{type(exc).__name__}: {exc}"
        error_type = type(exc).__name__
        error_message = str(exc)
        error_traceback = traceback.format_exc()
        error_stage = _infer_error_stage(exc)
        if debug:
            print(f"[debug] error_stage={error_stage}")
            print(error_traceback)

    latency_total = round(time.monotonic() - started_at, 3)
    if result is not None and getattr(result, "elapsed_seconds", None) is not None:
        latency_total = float(result.elapsed_seconds)

    record = _base_record(row)
    if result is None:
        record.update(
            {
                "latency_total_sec": latency_total,
                "latency_analyzer_sec": analyzer.last_elapsed_seconds,
                "latency_answer_sec": _answer_latency(latency_total, analyzer.last_elapsed_seconds),
                "error": error or "agent returned no answer",
                "error_type": error_type or "NoAnswer",
                "error_message": error_message or "agent returned no answer",
                "error_stage": error_stage or "agent_answer",
                "error_traceback": error_traceback,
                "verdict": "ERROR",
                "flags": ["AGENT_ERROR"],
            }
        )
        return record

    query_plan = result.query_plan if isinstance(result.query_plan, dict) else {}
    sources = [_object_to_dict(source) for source in getattr(result, "sources", [])]
    diagnostic_candidates = [
        _object_to_dict(source) for source in getattr(result, "diagnostic_candidates", [])
    ]
    answer_text = str(getattr(result, "answer", "") or "")
    source_titles = [_source_title(source) for source in sources]
    top_source = sources[0] if sources else {}

    record.update(
        {
            "response_kind": str(getattr(result, "response_kind", "") or ""),
            "answer_text": answer_text,
            "answer_preview": _preview(answer_text),
            "sources_count": len(sources),
            "sources_titles": source_titles,
            "top_source_title": _source_title(top_source),
            "top_source_section": top_source.get("section"),
            "query_plan_enabled": query_plan.get("enabled"),
            "query_plan_intent": query_plan.get("intent"),
            "query_plan_answer_type": query_plan.get("answer_type"),
            "query_plan_normalized_question": query_plan.get("normalized_question"),
            "query_plan_expansions": _coerce_list(query_plan.get("query_expansions")),
            "query_plan_preferred_sources": _coerce_list(query_plan.get("preferred_sources")),
            "query_plan_confidence": query_plan.get("confidence"),
            "query_plan_fallback_used": bool(query_plan.get("fallback_used", False)),
            "query_plan_error": query_plan.get("error"),
            "diagnostic_candidates_count": len(diagnostic_candidates),
            "prompt_length": getattr(result, "prompt_length", None),
            "chunks_used": getattr(result, "chunks_used", None),
            "latency_total_sec": latency_total,
            "latency_analyzer_sec": analyzer.last_elapsed_seconds,
            "latency_answer_sec": _answer_latency(latency_total, analyzer.last_elapsed_seconds),
            "error": error,
            "error_type": "",
            "error_message": "",
            "error_stage": "",
            "error_traceback": "",
            "sources": sources,
            "diagnostic_candidates": diagnostic_candidates,
            "model": getattr(result, "model", None),
        }
    )

    verdict, flags = _evaluate_record(record)
    record["verdict"] = verdict
    record["flags"] = flags
    return record


def _base_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row["question_id"],
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "question": row["question"],
        "expected_intent": row.get("expected_intent", ""),
        "expected_behavior": row.get("expected_behavior", ""),
        "expected_response_kind": row.get("expected_response_kind", ""),
        "expected_source_hint": row.get("expected_source_hint", ""),
        "risk_tag": row.get("risk_tag", ""),
        "response_kind": "",
        "answer_text": "",
        "answer_preview": "",
        "sources_count": 0,
        "sources_titles": [],
        "top_source_title": "",
        "top_source_section": "",
        "query_plan_enabled": None,
        "query_plan_intent": None,
        "query_plan_answer_type": None,
        "query_plan_normalized_question": None,
        "query_plan_expansions": [],
        "query_plan_preferred_sources": [],
        "query_plan_confidence": None,
        "query_plan_fallback_used": False,
        "query_plan_error": None,
        "diagnostic_candidates_count": 0,
        "prompt_length": None,
        "chunks_used": None,
        "latency_total_sec": None,
        "latency_analyzer_sec": None,
        "latency_answer_sec": None,
        "error": "",
        "error_type": "",
        "error_message": "",
        "error_stage": "",
        "error_traceback": "",
        "verdict": "",
        "flags": [],
    }


def _evaluate_record(record: dict[str, Any]) -> tuple[str, list[str]]:
    critical: list[str] = []
    warnings: list[str] = []

    if record.get("error"):
        return "ERROR", ["AGENT_ERROR"]

    question = str(record.get("question") or "")
    question_norm = _norm(question).rstrip("?!.")
    group_id = str(record.get("group_id") or "")
    intent = _optional_str(record.get("query_plan_intent"))
    response_kind = _optional_str(record.get("response_kind"))
    sources_count = int(record.get("sources_count") or 0)
    expected_intent = _expected_intent(record)
    expected_response_kind = str(record.get("expected_response_kind") or "")
    answer_text_norm = _norm(
        " ".join(
            str(value or "")
            for value in (
                record.get("answer_text"),
                record.get("query_plan_normalized_question"),
                " ".join(record.get("query_plan_expansions") or []),
            )
        )
    )

    if record.get("query_plan_enabled") is not True:
        critical.append("QUERY_PLAN_NOT_ENABLED")
    if not intent:
        critical.append("QUERY_PLAN_MISSING")

    if question_norm in AMBIGUOUS_KP_QUESTIONS:
        if response_kind != "clarification" and intent != "ambiguous_abbreviation":
            critical.append("KP_AMBIGUOUS_NOT_CLARIFIED")

    if "цкп" in question_norm and FORBIDDEN_CKP_PHRASE in answer_text_norm:
        critical.append("CKP_FORBIDDEN_MEANING")

    if _is_off_topic_question(record) and sources_count > 0:
        critical.append("OFF_TOPIC_WITH_SOURCES")

    if _is_one_c_operational_question(record) and sources_count > 0:
        critical.append("ONE_C_WITH_SEMANTIC_SOURCES")

    if (group_id in {"G01", "G02"} or _looks_like_org_question(question)) and intent:
        if intent not in {"org_structure", "roles_responsibility"}:
            critical.append("ORG_STRUCTURE_WRONG_INTENT")

    if _looks_like_company_identity_question(question) and intent == "org_structure":
        critical.append("COMPANY_IDENTITY_AS_ORG_STRUCTURE")

    if _is_attendance_question(record) and intent == "vacation":
        critical.append("ATTENDANCE_AS_VACATION")

    if _low_confidence(record.get("query_plan_confidence")):
        warnings.append("LOW_CONFIDENCE")

    if _has_known_corporate_intent(intent, expected_intent, group_id):
        if sources_count == 0 and response_kind not in {"clarification", "no_answer"}:
            warnings.append("NO_SOURCES_FOR_KNOWN_CORPORATE_INTENT")

    answer_length = len(str(record.get("answer_text") or "").strip())
    if response_kind == "answer" and answer_length < 40:
        warnings.append("ANSWER_TOO_SHORT")
    if answer_length > 4500:
        warnings.append("ANSWER_TOO_LONG")

    if expected_response_kind == "answer" and response_kind in {"partial_answer", "no_answer"}:
        warnings.append("PARTIAL_FOR_EXPECTED_ANSWER")

    if expected_intent and intent and not _intent_matches(expected_intent, intent, group_id):
        warnings.append("UNEXPECTED_INTENT")

    if (
        not record.get("query_plan_expansions")
        and intent not in {"ambiguous_abbreviation", "off_topic", "unknown"}
    ):
        warnings.append("EMPTY_QUERY_EXPANSIONS")

    if record.get("query_plan_fallback_used"):
        warnings.append("FALLBACK_USED")

    if sources_count == 0 and int(record.get("diagnostic_candidates_count") or 0) == 0:
        warnings.append("NO_DIAGNOSTIC_CANDIDATES")

    latency = _safe_float(record.get("latency_total_sec"))
    if latency is not None:
        if latency > 60:
            warnings.append("VERY_SLOW_RESPONSE")
        elif latency > 30:
            warnings.append("SLOW_RESPONSE")

    if response_kind in {"no_answer", "off_topic"} and sources_count > 0:
        warnings.append("SOURCES_WITH_NO_ANSWER_OR_OFF_TOPIC")

    flags = _dedupe([*critical, *warnings])
    if critical:
        return "FAIL", flags
    if warnings:
        return "WARN", flags
    return "PASS", flags


def _expected_intent(record: dict[str, Any]) -> str:
    expected = str(record.get("expected_intent") or "").strip()
    if expected and expected not in {"various", "various_or_off_topic"}:
        return expected
    group_id = str(record.get("group_id") or "")
    values = GROUP_EXPECTED_INTENTS.get(group_id, set())
    return next(iter(values)) if len(values) == 1 else ""


def _intent_matches(expected_intent: str, actual_intent: str, group_id: str) -> bool:
    if expected_intent == actual_intent:
        return True
    if expected_intent in {"various", "various_or_off_topic"}:
        return True
    if group_id in RELAXED_GROUPS:
        return True
    expected_values = set()
    for item in expected_intent.replace("|", ",").split(","):
        clean_item = item.strip()
        if clean_item:
            expected_values.add(clean_item)
    expected_values.update(GROUP_EXPECTED_INTENTS.get(group_id, set()))
    if expected_intent == "answer_or_partial":
        return True
    return actual_intent in expected_values


def _has_known_corporate_intent(
    intent: str | None,
    expected_intent: str,
    group_id: str,
) -> bool:
    if intent in KNOWN_CORPORATE_INTENTS:
        return True
    if expected_intent in KNOWN_CORPORATE_INTENTS:
        return True
    return bool(GROUP_EXPECTED_INTENTS.get(group_id, set()) & KNOWN_CORPORATE_INTENTS)


def _is_off_topic_question(record: dict[str, Any]) -> bool:
    question = _norm(str(record.get("question") or ""))
    intent = str(record.get("expected_intent") or "")
    return intent == "off_topic" or any(keyword in question for keyword in OFF_TOPIC_KEYWORDS)


def _is_one_c_operational_question(record: dict[str, Any]) -> bool:
    question = _norm(str(record.get("question") or ""))
    return (
        str(record.get("group_id") or "") == "G16"
        or str(record.get("expected_intent") or "") == "one_c_operational_lookup"
        or any(keyword in question for keyword in OPERATIONAL_1C_KEYWORDS)
    )


def _is_attendance_question(record: dict[str, Any]) -> bool:
    question = _norm(str(record.get("question") or ""))
    return (
        str(record.get("group_id") or "") == "G14"
        or str(record.get("expected_intent") or "") == "attendance_absence"
        or any(keyword in question for keyword in ATTENDANCE_KEYWORDS)
    )


def _looks_like_org_question(question: str) -> bool:
    normalized = _norm(question)
    return any(keyword in normalized for keyword in ORG_KEYWORDS)


def _looks_like_company_identity_question(question: str) -> bool:
    normalized = _norm(question)
    return any(keyword in normalized for keyword in COMPANY_IDENTITY_KEYWORDS)


def _low_confidence(value: Any) -> bool:
    confidence = _safe_float(value)
    return confidence is not None and confidence < 0.5


def _build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    verdict_counts = Counter(str(record.get("verdict") or "") for record in records)
    latencies = [
        latency
        for latency in (_safe_float(record.get("latency_total_sec")) for record in records)
        if latency is not None
    ]
    by_group: dict[str, dict[str, Any]] = {}
    for group_key, group_records in _group_records(records).items():
        group_counter = Counter(str(record.get("verdict") or "") for record in group_records)
        by_group[group_key] = {
            "total": len(group_records),
            "pass": group_counter.get("PASS", 0),
            "warn": group_counter.get("WARN", 0),
            "fail": group_counter.get("FAIL", 0),
            "error": group_counter.get("ERROR", 0),
        }

    return {
        "total": len(records),
        "pass": verdict_counts.get("PASS", 0),
        "warn": verdict_counts.get("WARN", 0),
        "fail": verdict_counts.get("FAIL", 0),
        "error": verdict_counts.get("ERROR", 0),
        "by_group": by_group,
        "by_intent": dict(Counter(_optional_str(record.get("query_plan_intent")) or "null" for record in records)),
        "by_response_kind": dict(Counter(_optional_str(record.get("response_kind")) or "null" for record in records)),
        "top_flags": dict(Counter(flag for record in records for flag in record.get("flags", []))),
        "avg_latency_sec": round(statistics.fmean(latencies), 3) if latencies else None,
        "median_latency_sec": round(statistics.median(latencies), 3) if latencies else None,
        "max_latency_sec": round(max(latencies), 3) if latencies else None,
    }


def _build_level1_report(
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    run_config: dict[str, Any],
) -> str:
    lines = [
        "# LineHelper Runtime Query Analyzer Test Run — Level 1 Technical Report",
        "",
        "## 1. Run configuration",
        f"- date/time: {run_config['started_at']}",
        f"- branch: {run_config['git_branch']}",
        f"- git commit hash: {run_config['git_commit']}",
        f"- pack path: {run_config['pack_path']}",
        f"- number of questions: {summary['total']}",
        f"- OLLAMA_MODEL: {run_config['env'].get('OLLAMA_MODEL') or ''}",
        f"- OLLAMA_ANALYZER_MODEL: {run_config['env'].get('OLLAMA_ANALYZER_MODEL') or ''}",
        f"- Python version: {run_config['python_version']}",
        f"- memory DB path: {run_config.get('memory_db_path') or ''}",
        f"- Ollama preflight: {_format_preflight(run_config.get('ollama_preflight'))}",
        f"- startup error: {_format_startup_error(run_config.get('startup_error'))}",
        "",
        "## 2. Overall summary",
        f"- total questions: {summary['total']}",
        f"- PASS: {summary['pass']}",
        f"- WARN: {summary['warn']}",
        f"- FAIL: {summary['fail']}",
        f"- ERROR: {summary['error']}",
        f"- average latency: {_format_number(summary['avg_latency_sec'])} sec",
        f"- median latency: {_format_number(summary['median_latency_sec'])} sec",
        f"- max latency: {_format_number(summary['max_latency_sec'])} sec",
        "",
        "## 3. Summary by diagnostic group",
        _group_summary_table(records),
        "",
        "## 4. Intent distribution",
        _counter_table(summary["by_intent"], "intent"),
        "",
        "## 5. Response kind distribution",
        _counter_table(summary["by_response_kind"], "response_kind"),
        "",
        "## 6. Source usage",
    ]
    lines.extend(_source_usage_lines(records))
    lines.extend(
        [
            "",
            "## 7. Critical failures",
            _critical_failures_section(records),
            "",
            "## 8. Warnings",
            _warnings_section(records),
            "",
            "## 9. Slowest questions",
            _slowest_questions_section(records),
            "",
            "## 10. Most suspicious cases for second-level diagnosis",
            _suspicious_cases_section(records),
            "",
            "## 11. First-level conclusion",
            _first_level_conclusion(records, summary),
            "",
        ]
    )
    return "\n".join(lines)


def _group_summary_table(records: list[dict[str, Any]]) -> str:
    rows = [
        "| group_id | group_name | total | PASS | WARN | FAIL | ERROR | most common intent | most common flags |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for group_key, group_records in _group_records(records).items():
        group_id, group_name = _split_group_key(group_key)
        verdicts = Counter(record.get("verdict") for record in group_records)
        intents = Counter(_optional_str(record.get("query_plan_intent")) or "null" for record in group_records)
        flags = Counter(flag for record in group_records for flag in record.get("flags", []))
        rows.append(
            "| {group_id} | {group_name} | {total} | {pass_count} | {warn} | {fail} | {error} | {intent} | {flags} |".format(
                group_id=_md(group_id),
                group_name=_md(group_name),
                total=len(group_records),
                pass_count=verdicts.get("PASS", 0),
                warn=verdicts.get("WARN", 0),
                fail=verdicts.get("FAIL", 0),
                error=verdicts.get("ERROR", 0),
                intent=_md(_top_items(intents, limit=1)),
                flags=_md(_top_items(flags, limit=3)),
            )
        )
    return "\n".join(rows)


def _counter_table(counter_data: dict[str, int], label: str) -> str:
    rows = [f"| {label} | count |", "|---|---:|"]
    for key, count in sorted(counter_data.items(), key=lambda item: (-item[1], item[0])):
        rows.append(f"| {_md(str(key))} | {count} |")
    return "\n".join(rows)


def _source_usage_lines(records: list[dict[str, Any]]) -> list[str]:
    source_counter = Counter(
        title for record in records for title in record.get("sources_titles", []) if title
    )
    zero_source_corporate = [
        record
        for record in records
        if int(record.get("sources_count") or 0) == 0
        and _has_known_corporate_intent(
            _optional_str(record.get("query_plan_intent")),
            _expected_intent(record),
            str(record.get("group_id") or ""),
        )
    ]
    sources_no_answer = [
        record
        for record in records
        if int(record.get("sources_count") or 0) > 0
        and str(record.get("response_kind") or "") in {"no_answer", "off_topic"}
    ]
    lines = ["- top source titles:"]
    if source_counter:
        lines.extend(f"  - {_md(title)}: {count}" for title, count in source_counter.most_common(10))
    else:
        lines.append("  - none")
    lines.append(f"- questions with zero sources but corporate intent: {len(zero_source_corporate)}")
    lines.extend(_example_lines(zero_source_corporate, limit=10))
    lines.append(f"- questions with sources but no_answer/off_topic: {len(sources_no_answer)}")
    lines.extend(_example_lines(sources_no_answer, limit=10))
    return lines


def _critical_failures_section(records: list[dict[str, Any]]) -> str:
    failures = [record for record in records if record.get("verdict") in {"FAIL", "ERROR"}]
    if not failures:
        return "No FAIL or ERROR cases."
    lines = []
    for record in failures:
        lines.extend(_case_lines(record))
    return "\n".join(lines)


def _warnings_section(records: list[dict[str, Any]]) -> str:
    by_flag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("verdict") != "WARN":
            continue
        for flag in record.get("flags", []):
            by_flag[flag].append(record)
    if not by_flag:
        return "No WARN cases."
    lines = []
    for flag, flag_records in sorted(by_flag.items()):
        lines.append(f"### {flag}")
        lines.extend(_example_lines(flag_records, limit=8))
        lines.append("")
    return "\n".join(lines).strip()


def _slowest_questions_section(records: list[dict[str, Any]]) -> str:
    rows = [
        "| question_id | group | latency_sec | verdict | intent | question |",
        "|---|---|---:|---|---|---|",
    ]
    sorted_records = sorted(
        records,
        key=lambda record: _safe_float(record.get("latency_total_sec")) or -1,
        reverse=True,
    )[:15]
    for record in sorted_records:
        rows.append(
            "| {qid} | {group} | {latency} | {verdict} | {intent} | {question} |".format(
                qid=_md(str(record.get("question_id") or "")),
                group=_md(_group_label(record)),
                latency=_format_number(record.get("latency_total_sec")),
                verdict=_md(str(record.get("verdict") or "")),
                intent=_md(str(record.get("query_plan_intent") or "")),
                question=_md(_preview(str(record.get("question") or ""), 120)),
            )
        )
    return "\n".join(rows)


def _suspicious_cases_section(records: list[dict[str, Any]]) -> str:
    scored = sorted(
        records,
        key=lambda record: (_suspicion_score(record), _safe_float(record.get("latency_total_sec")) or 0),
        reverse=True,
    )
    suspicious = [record for record in scored if _suspicion_score(record) > 0][:40]
    if not suspicious:
        return "No suspicious cases selected."
    lines = []
    for record in suspicious:
        lines.append(
            "- {qid} [{group}] {verdict}, intent={intent}, kind={kind}, flags={flags}: {question}".format(
                qid=record.get("question_id"),
                group=_group_label(record),
                verdict=record.get("verdict"),
                intent=record.get("query_plan_intent"),
                kind=record.get("response_kind"),
                flags=", ".join(record.get("flags", [])) or "-",
                question=_preview(str(record.get("question") or ""), 160),
            )
        )
    return "\n".join(lines)


def _first_level_conclusion(records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    if not records:
        return "No completed questions were available for a stability conclusion."

    fail_error = int(summary["fail"]) + int(summary["error"])
    strongest = _rank_groups(records, reverse=True)[:3]
    weakest = _rank_groups(records, reverse=False)[:3]
    stable = fail_error == 0
    safe_manual = int(summary["error"]) == 0 and fail_error / max(1, int(summary["total"])) <= 0.15
    return (
        f"Runtime Query Analyzer technical stability is {'acceptable' if stable else 'mixed'} "
        f"for this run: {fail_error} critical FAIL/ERROR case(s) were detected. "
        f"Strongest groups by PASS share: {_format_group_rank(strongest)}. "
        f"Weakest groups by PASS share: {_format_group_rank(weakest)}. "
        f"Broader manual testing is {'reasonable to continue' if safe_manual else 'not yet recommended without second-level diagnosis'}."
    )


def _build_failures_report(records: list[dict[str, Any]]) -> str:
    failures = [record for record in records if record.get("verdict") in {"FAIL", "ERROR"}]
    lines = ["# Runtime Query Analyzer FAIL/ERROR Cases", ""]
    if not failures:
        lines.append("No FAIL or ERROR cases.")
        lines.append("")
        return "\n".join(lines)
    for record in failures:
        lines.extend(_case_lines(record, include_answer=True))
    return "\n".join(lines)


def _build_warnings_report(records: list[dict[str, Any]]) -> str:
    lines = ["# Runtime Query Analyzer WARN Cases", ""]
    warn_records = [record for record in records if record.get("verdict") == "WARN"]
    if not warn_records:
        lines.append("No WARN cases.")
        lines.append("")
        return "\n".join(lines)
    lines.append(_warnings_section(records))
    lines.append("")
    return "\n".join(lines)


def _case_lines(record: dict[str, Any], *, include_answer: bool = False) -> list[str]:
    lines = [
        f"### {record.get('question_id')} — {record.get('verdict')}",
        f"- group: {_group_label(record)}",
        f"- question: {record.get('question')}",
        f"- expected: intent={record.get('expected_intent') or '-'}, kind={record.get('expected_response_kind') or '-'}",
        f"- actual intent: {record.get('query_plan_intent') or '-'}",
        f"- response_kind: {record.get('response_kind') or '-'}",
        f"- sources: {', '.join(record.get('sources_titles', [])) or '-'}",
        f"- flags: {', '.join(record.get('flags', [])) or '-'}",
        f"- answer preview: {_preview(str(record.get('answer_text') or ''), 500)}",
    ]
    if record.get("error"):
        lines.append(f"- error: {record.get('error')}")
    if record.get("error_type") or record.get("error_stage"):
        lines.append(f"- error type: {record.get('error_type') or '-'}")
        lines.append(f"- error stage: {record.get('error_stage') or '-'}")
        lines.append(f"- error message: {record.get('error_message') or '-'}")
    if record.get("error_traceback"):
        lines.extend(["", "Traceback:", "", "```text", str(record.get("error_traceback")).rstrip(), "```"])
    if include_answer and record.get("answer_text"):
        lines.extend(["", "Answer:", "", str(record.get("answer_text")), ""])
    else:
        lines.append("")
    return lines


def _build_run_config(
    *,
    args: argparse.Namespace,
    pack_path: Path,
    out_root: Path,
    run_dir: Path,
    selected_count: int,
    pending_count: int,
    processed_count: int,
) -> dict[str, Any]:
    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "branch": _git_output("branch", "--show-current"),
        "git_branch": _git_output("branch", "--show-current"),
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_status_short": _git_output("status", "--short"),
        "pack_path": str(pack_path),
        "out_root": str(out_root),
        "run_dir": str(run_dir),
        "selected_questions": selected_count,
        "pending_questions": pending_count,
        "processed_questions": processed_count,
        "limit": args.limit,
        "group": args.group,
        "resume": args.resume,
        "sleep": args.sleep,
        "debug": args.debug,
        "python_version": sys.version.replace("\n", " "),
        "env": {
            "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL"),
            "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL"),
            "OLLAMA_ANALYZER_MODEL": os.getenv("OLLAMA_ANALYZER_MODEL"),
        },
    }


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


def _write_results_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            csv_record = dict(record)
            for key in (
                "sources_titles",
                "query_plan_expansions",
                "query_plan_preferred_sources",
                "flags",
            ):
                csv_record[key] = "; ".join(str(item) for item in csv_record.get(key, []))
            writer.writerow(csv_record)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            records.append(json.loads(line))
    return records


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _object_to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def _source_title(source: dict[str, Any]) -> str:
    return str(source.get("title") or source.get("source") or "")


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return []


def _answer_latency(total: float | None, analyzer: float | None) -> float | None:
    if total is None or analyzer is None:
        return None
    return round(max(0.0, total - analyzer), 3)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _preview(value: str, limit: int = 420) -> str:
    clean_value = " ".join(value.split())
    if len(clean_value) <= limit:
        return clean_value
    return clean_value[: limit - 3].rstrip() + "..."


def _norm(value: str) -> str:
    return value.casefold().replace("ё", "е").strip()


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _group_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[_group_label(record)].append(record)
    return dict(sorted(grouped.items()))


def _group_label(record: dict[str, Any]) -> str:
    group_id = str(record.get("group_id") or "")
    group_name = str(record.get("group_name") or "")
    return f"{group_id} {group_name}".strip() or "-"


def _split_group_key(value: str) -> tuple[str, str]:
    parts = value.split(maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _top_items(counter: Counter[str], *, limit: int) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{key} ({count})" for key, count in counter.most_common(limit))


def _example_lines(records: list[dict[str, Any]], *, limit: int) -> list[str]:
    if not records:
        return ["  - none"]
    return [
        "  - {qid} [{group}] {intent}/{kind}: {question}".format(
            qid=record.get("question_id"),
            group=_group_label(record),
            intent=record.get("query_plan_intent") or "-",
            kind=record.get("response_kind") or "-",
            question=_preview(str(record.get("question") or ""), 160),
        )
        for record in records[:limit]
    ]


def _suspicion_score(record: dict[str, Any]) -> int:
    score = 0
    flags = set(record.get("flags", []))
    score += 8 if record.get("verdict") == "ERROR" else 0
    score += 7 if record.get("verdict") == "FAIL" else 0
    score += 3 if record.get("verdict") == "WARN" else 0
    important = {
        "UNEXPECTED_INTENT",
        "OFF_TOPIC_WITH_SOURCES",
        "NO_SOURCES_FOR_KNOWN_CORPORATE_INTENT",
        "ONE_C_WITH_SEMANTIC_SOURCES",
        "KP_AMBIGUOUS_NOT_CLARIFIED",
        "CKP_FORBIDDEN_MEANING",
        "FALLBACK_USED",
        "VERY_SLOW_RESPONSE",
        "SLOW_RESPONSE",
    }
    score += 2 * len(flags & important)
    return score


def _rank_groups(records: list[dict[str, Any]], *, reverse: bool) -> list[tuple[str, float]]:
    ranking = []
    for group, group_records in _group_records(records).items():
        pass_count = sum(1 for record in group_records if record.get("verdict") == "PASS")
        ranking.append((group, pass_count / max(1, len(group_records))))
    return sorted(ranking, key=lambda item: item[1], reverse=reverse)


def _format_group_rank(values: list[tuple[str, float]]) -> str:
    if not values:
        return "-"
    return ", ".join(f"{group} ({share:.0%})" for group, share in values)


def _format_number(value: Any) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.3f}"


def _format_preflight(value: Any) -> str:
    if not isinstance(value, dict):
        return "not run"
    if value.get("ok"):
        return (
            f"ok, models_count={value.get('models_count', 0)}, "
            f"elapsed_sec={_format_number(value.get('elapsed_sec'))}"
        )
    return (
        f"failed at {value.get('stage') or 'unknown'}: "
        f"{value.get('error_type') or ''} {value.get('error') or ''}".strip()
    )


def _format_startup_error(value: Any) -> str:
    if not isinstance(value, dict):
        return "-"
    return f"{value.get('stage') or 'unknown'}: {value.get('message') or ''}"


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
