"""Run a resumable autonomous probe against the production LineHelper RAG path."""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import os
import random
import re
import shutil
import sqlite3
import statistics
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import RagAnswerGenerator  # noqa: E402
from linehelper.llm.ollama_client import OllamaClient  # noqa: E402
from linehelper.rag.retriever import DEFAULT_MEMORY_DB_PATH, SemanticRetriever  # noqa: E402
from scripts import run_runtime_query_analyzer_test_pack as base  # noqa: E402


DEFAULT_SEED_PACK = Path("docs/test_packs/linehelper_runtime_query_analyzer_test_pack_360.csv")
DEFAULT_OUT_DIR = Path("data/test_runs/autonomous_runtime_probe")
DEFAULT_DURATION_MINUTES = 240.0
DEFAULT_MAX_QUESTIONS = 500
DEFAULT_CHECKPOINT_EVERY = 25
DEFAULT_GENERATOR_BATCH_SIZE = 10
MIN_FREE_BYTES = 100 * 1024 * 1024

RESULTS_JSONL = "results.jsonl"
RESULTS_CSV = "results.csv"
ANSWERS_MD = "answers_full.md"
GENERATED_CSV = "generated_questions.csv"
GENERATOR_JSONL = "generator_batches.jsonl"
SUMMARY_JSON = "summary.json"
CHECKPOINT_MD = "checkpoint_report.md"
REPORT_MD = "report_level1.md"
FAILURES_MD = "failures.md"
WARNINGS_MD = "warnings.md"
SUSPICIOUS_MD = "suspicious_cases.md"
RUN_CONFIG_JSON = "run_config.json"
RUN_LOG = "run.log"
PREFLIGHT_ERROR_MD = "preflight_error.md"

CSV_FIELDS = [
    "run_id", "sequence_id", "question_id", "seed_question_id",
    "diagnostic_group", "diagnostic_focus", "generation_strategy", "mutation_type",
    "original_question", "question", "expected_intent", "expected_response_kind",
    "expected_source_hint", "risk_tag", "response_kind", "answer_preview",
    "sources_count", "sources_titles", "sources_sections", "top_source_title",
    "top_source_section", "query_plan_present", "query_plan_intent",
    "query_plan_answer_type", "query_plan_normalized_question", "query_plan_expansions",
    "query_plan_preferred_sources", "query_plan_confidence", "query_plan_fallback_used",
    "query_plan_error", "diagnostic_candidates_count", "diagnostic_candidate_titles",
    "chunks_used", "prompt_length", "latency_total_sec", "latency_analyzer_sec",
    "latency_retrieval_sec", "latency_answer_sec", "verdict", "flags", "error_stage",
    "error_type", "error_message", "timestamp",
]

GENERATED_FIELDS = [
    "question_id", "seed_question_id", "diagnostic_group", "diagnostic_focus",
    "generation_strategy", "mutation_type", "original_question", "question",
    "expected_intent", "expected_response_kind", "expected_source_hint", "risk_tag",
    "scheduled_sequence_id", "created_at",
]

MIXED_CYCLE = [
    "seed", "mutation", "noise", "seed", "adversarial",
    "mutation", "generated", "seed", "noise", "mutation",
    "adversarial", "seed", "generated", "mutation", "noise",
    "seed", "adversarial", "mutation", "generated", "seed",
]

SYNONYMS = [
    ("отделы", "подразделения"), ("подразделений", "отделений"),
    ("подразделения", "службы"), ("компания", "Serviceline"),
    ("Сервислайн", "Serviceline"), ("документооборот", "1С ДО"),
    ("согласование документов", "документооборот"), ("задача", "поручение"),
    ("поручение", "распоряжение"), ("коммерческое предложение", "КП"),
    ("ценный конечный продукт", "ЦКП"), ("получить", "запросить"),
    ("оформить", "согласовать"),
]

ADVERSARIAL_CASES = [
    ("A01", "G17 Сравнения", "comparison", "answer", "КП и ЦКП — это одно и то же?", "comparison"),
    ("A02", "G17 Сравнения", "comparison", "answer", "Задача и распоряжение отличаются?", "comparison"),
    ("A03", "G17 Сравнения", "comparison", "answer", "Командировка считается отпуском?", "boundary"),
    ("A04", "G02 Ответственные", "roles_responsibility", "answer", "Кто отвечает за закупку и логистику?", "mixed"),
    ("A05", "G17 Сравнения", "comparison", "answer", "Что важнее: цель компании или её ЦКП?", "comparison"),
    ("A06", "G08 Договоры", "contract_approval", "answer", "Как оформить договор и потом направить задачу?", "mixed"),
    ("A07", "G18 Вне базы", "off_topic", "no_answer", "Какая завтра погода?", "off_topic"),
    ("A08", "G18 Вне базы", "off_topic", "no_answer", "Как приготовить борщ?", "off_topic"),
    ("A09", "G18 Вне базы", "off_topic", "no_answer", "Напиши стих про склад.", "off_topic"),
    ("A10", "G18 Вне базы", "off_topic", "no_answer", "Какой курс доллара?", "off_topic"),
    ("A11", "G18 Вне базы", "off_topic", "no_answer", "Сколько маленьких утят хотят есть?", "off_topic"),
    ("A12", "G16 Операционные 1С", "one_c_operational_lookup", "no_answer", "Найди цену детали.", "one_c"),
    ("A13", "G16 Операционные 1С", "one_c_operational_lookup", "no_answer", "Есть ли остатки на складе?", "one_c"),
    ("A14", "G16 Операционные 1С", "one_c_operational_lookup", "no_answer", "Какой статус заказа?", "one_c"),
    ("A15", "G16 Операционные 1С", "one_c_operational_lookup", "no_answer", "Покажи счета клиента.", "one_c"),
    ("A16", "G16 Операционные 1С", "one_c_operational_lookup", "no_answer", "Найди контрагента.", "one_c"),
    ("A17", "G16 Операционные 1С", "one_c_operational_lookup", "no_answer", "Когда была отгрузка?", "one_c"),
    ("A18", "G05 КП", "ambiguous_abbreviation", "clarification", "КП", "ambiguous"),
    ("A19", "G04 ЦКП", "company_ckp", "answer", "ЦКП", "short"),
    ("A20", "G01 Оргструктура", "org_structure", "answer", "оргсхема", "short"),
    ("A21", "G07 Документооборот", "document_flow", "answer", "договор", "short"),
    ("A22", "G09 Отпуск", "vacation", "answer", "отпуск", "short"),
    ("A23", "G10 Задачи", "task_management", "answer", "задача", "short"),
    ("A24", "G14 Посещаемость", "attendance_absence", "clarification", "я опоздал это отпуск?", "boundary"),
]

FOLLOW_UP_SEQUENCES = [
    ["Что такое ЦКП?", "А зачем он нужен?", "А чем это отличается от КП?"],
    ["Как оформить договор?", "Кто его согласует?", "А если его отклонили?"],
]

PROCEDURE_RE = re.compile(r"\b(?:оформ|соглас|направ|зарегистр|подать|утверд|созда|отправ)\w*", re.I)
CORPORATE_RE = re.compile(
    r"\b(?:компан|serviceline|сервислайн|отдел|цкп|зрс|договор|отпуск|задач|распоряж|1с|кп)\w*",
    re.I,
)


class TimedRetriever:
    def __init__(self, inner: SemanticRetriever) -> None:
        self.inner = inner
        self.elapsed_seconds = 0.0

    def reset(self) -> None:
        self.elapsed_seconds = 0.0

    def retrieve(self, *args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            return self.inner.retrieve(*args, **kwargs)
        finally:
            self.elapsed_seconds += time.monotonic() - started


class TimedLlmClient:
    def __init__(self, inner: OllamaClient) -> None:
        self.inner = inner
        self.model = inner.model
        self.elapsed_seconds = 0.0

    def reset(self) -> None:
        self.elapsed_seconds = 0.0

    def chat(self, *args: Any, **kwargs: Any) -> str:
        started = time.monotonic()
        try:
            return self.inner.chat(*args, **kwargs)
        finally:
            self.elapsed_seconds += time.monotonic() - started


class QuestionFactory:
    def __init__(
        self,
        seeds: list[dict[str, Any]],
        *,
        random_seed: int,
        run_dir: Path,
        generator_batch_size: int,
        no_llm_generation: bool,
        dry_run: bool,
    ) -> None:
        self.seeds = list(seeds)
        random.Random(random_seed).shuffle(self.seeds)
        self.random_seed = random_seed
        self.run_dir = run_dir
        self.generator_batch_size = generator_batch_size
        self.no_llm_generation = no_llm_generation or dry_run
        self.generated_queue = self._load_generated_queue()
        self.batch_number = len(_read_jsonl(run_dir / GENERATOR_JSONL))

    def make(self, strategy: str, sequence_id: int) -> dict[str, Any]:
        if strategy == "seed":
            seed = self.seeds[(sequence_id * 7) % len(self.seeds)]
            return _question_from_seed(seed, sequence_id, "seed", "none", seed["question"])
        if strategy == "mutation":
            seed = self.seeds[(sequence_id * 11) % len(self.seeds)]
            question, mutation_type = _mutate_question(seed["question"], sequence_id)
            return _question_from_seed(seed, sequence_id, "mutation", mutation_type, question)
        if strategy == "noise":
            seed = self.seeds[(sequence_id * 13) % len(self.seeds)]
            question, mutation_type = _noise_question(seed["question"], sequence_id)
            return _question_from_seed(seed, sequence_id, "noise", mutation_type, question)
        if strategy == "adversarial":
            return _adversarial_question(sequence_id)
        if strategy == "generated" and not self.no_llm_generation:
            try:
                return self._generated_question(sequence_id)
            except Exception as exc:
                self._log_generation_error(exc, sequence_id)
        seed = self.seeds[(sequence_id * 17) % len(self.seeds)]
        question, mutation_type = _mutate_question(seed["question"], sequence_id + 3)
        return _question_from_seed(
            seed,
            sequence_id,
            "mutation",
            "llm_generation_fallback_" + mutation_type,
            question,
        )

    def _generated_question(self, sequence_id: int) -> dict[str, Any]:
        if not self.generated_queue:
            self._generate_batch(sequence_id)
        item = self.generated_queue.pop(0)
        seed = item["seed"]
        return _question_from_seed(
            seed,
            sequence_id,
            "generated",
            "local_llm_batch",
            item["question"],
            question_id=f"G{item['batch_number']:03d}-{item['item_number']:03d}-{sequence_id:04d}",
        )

    def _generate_batch(self, sequence_id: int) -> None:
        self.batch_number += 1
        seed = self.seeds[(sequence_id * 19) % len(self.seeds)]
        examples = [
            row["question"] for row in self.seeds
            if row.get("group_id") == seed.get("group_id")
        ][:5] or [seed["question"]]
        prompt = _generator_prompt(seed, examples, self.generator_batch_size)
        model = os.getenv("OLLAMA_ANALYZER_MODEL") or os.getenv("OLLAMA_MODEL") or "qwen2.5:3b"
        client = OllamaClient(model=model, temperature=0.4, num_predict=900)
        started = time.monotonic()
        raw = client.chat(
            [
                {"role": "system", "content": "Ты генерируешь только тестовые вопросы и не отвечаешь на них."},
                {"role": "user", "content": prompt},
            ],
            model=model,
        )
        latency = round(time.monotonic() - started, 3)
        questions = _parse_generated_questions(raw)
        if not questions:
            raise ValueError("generator returned no valid questions")
        questions = questions[: self.generator_batch_size]
        batch = {
            "batch_number": self.batch_number,
            "generator_prompt": prompt,
            "generator_model": model,
            "generated_batch": questions,
            "generation_latency": latency,
            "seed_question_id": seed["question_id"],
            "timestamp": _now(),
        }
        _append_jsonl(self.run_dir / GENERATOR_JSONL, batch)
        self.generated_queue.extend(
            {"question": question, "seed": seed, "batch_number": self.batch_number, "item_number": index}
            for index, question in enumerate(questions, start=1)
        )

    def _load_generated_queue(self) -> list[dict[str, Any]]:
        scheduled = {
            row.get("question", "")
            for row in _read_csv(self.run_dir / GENERATED_CSV)
            if row.get("generation_strategy") == "generated"
        }
        queue: list[dict[str, Any]] = []
        seed_by_id = {str(row["question_id"]): row for row in self.seeds}
        for batch in _read_jsonl(self.run_dir / GENERATOR_JSONL):
            seed = seed_by_id.get(str(batch.get("seed_question_id")))
            if not seed:
                continue
            for index, question in enumerate(batch.get("generated_batch") or [], start=1):
                if question not in scheduled:
                    queue.append({
                        "question": question,
                        "seed": seed,
                        "batch_number": int(batch.get("batch_number") or 0),
                        "item_number": index,
                    })
        return queue

    def _log_generation_error(self, exc: Exception, sequence_id: int) -> None:
        self.batch_number += 1
        _append_jsonl(self.run_dir / GENERATOR_JSONL, {
            "batch_number": self.batch_number,
            "generator_model": os.getenv("OLLAMA_ANALYZER_MODEL") or os.getenv("OLLAMA_MODEL"),
            "generated_batch": [],
            "generation_latency": None,
            "sequence_id": sequence_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "timestamp": _now(),
        })


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = _parse_args(argv)
    legacy_flag = os.environ.pop("LINEHELPER_USE_QUERY_ANALYZER", None)
    os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
    os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:3b")
    os.environ.setdefault("OLLAMA_ANALYZER_MODEL", "qwen2.5:3b")

    run_dir, run_config = _prepare_run(args, legacy_flag)
    logger = RunLogger(run_dir / RUN_LOG, debug=args.debug)
    logger.log(f"run_id={run_config['run_id']} status=STARTING")

    seed_path = _resolve_path(Path(run_config["seed_pack"]))
    try:
        seeds = base._read_pack(seed_path)
    except Exception as exc:
        return _preflight_failure(run_dir, run_config, [f"seed pack: {type(exc).__name__}: {exc}"], logger)

    preflight = _preflight(
        seed_path=seed_path,
        run_dir=run_dir,
        seeds=seeds,
        dry_run=args.dry_run,
        logger=logger,
    )
    run_config["preflight"] = preflight
    _write_json(run_dir / RUN_CONFIG_JSON, run_config)
    if not preflight["ok"]:
        return _preflight_failure(run_dir, run_config, preflight["errors"], logger)

    existing = _read_jsonl(run_dir / RESULTS_JSONL)
    sequence_id = max((int(row.get("sequence_id") or 0) for row in existing), default=0)
    factory = QuestionFactory(
        seeds,
        random_seed=int(run_config["random_seed"]),
        run_dir=run_dir,
        generator_batch_size=int(run_config["generator_batch_size"]),
        no_llm_generation=bool(run_config["no_llm_generation"]),
        dry_run=args.dry_run,
    )

    if args.dry_run:
        planned = []
        for offset in range(1, min(args.max_questions, 10) + 1):
            strategy = _strategy_for(args.mode, sequence_id + offset, int(run_config["random_seed"]))
            question = factory.make(strategy, sequence_id + offset)
            _append_generated_question(run_dir / GENERATED_CSV, question, sequence_id + offset)
            planned.append(question)
        run_config["status"] = "DRY_RUN"
        run_config["dry_run_planned_questions"] = len(planned)
        _write_json(run_dir / RUN_CONFIG_JSON, run_config)
        _write_all_reports(run_dir, existing, run_config, started_monotonic=time.monotonic(), status="DRY_RUN")
        logger.log(f"status=DRY_RUN planned={len(planned)}")
        print(f"Dry-run completed: {run_dir}")
        return 0

    db_path = _resolve_path(DEFAULT_MEMORY_DB_PATH)
    timed_retriever = TimedRetriever(SemanticRetriever(db_path))
    timed_llm = TimedLlmClient(OllamaClient())
    analyzer = base.TimedQueryAnalyzer()
    generator = RagAnswerGenerator(
        retriever=timed_retriever,
        llm_client=timed_llm,
        query_analyzer=analyzer,
    )
    known_sources = _known_sources(db_path)

    started_monotonic = time.monotonic()
    deadline = started_monotonic + args.duration_minutes * 60
    records = list(existing)
    status = "COMPLETED"
    stop_reason = "limit_reached"
    checkpoint_start = len(records)

    try:
        while len(records) < args.max_questions and time.monotonic() < deadline:
            if not _memory_db_healthy(db_path):
                status, stop_reason = "STOPPED", "memory_db_unavailable"
                break
            if _free_bytes(run_dir) < MIN_FREE_BYTES:
                status, stop_reason = "STOPPED", "critical_low_disk_space"
                break

            sequence_id += 1
            strategy = _strategy_for(args.mode, sequence_id, int(run_config["random_seed"]))
            question = factory.make(strategy, sequence_id)
            _append_generated_question(run_dir / GENERATED_CSV, question, sequence_id)
            logger.log(f"sequence={sequence_id} strategy={question['generation_strategy']} question={question['question']}")

            timed_retriever.reset()
            timed_llm.reset()
            analyzer.last_elapsed_seconds = None
            row = _base_runtime_row(question)
            record = base._run_question(row, generator=generator, analyzer=analyzer, debug=args.debug)
            record = _augment_record(
                record,
                question=question,
                run_id=run_config["run_id"],
                sequence_id=sequence_id,
                retrieval_latency=timed_retriever.elapsed_seconds,
                answer_latency=timed_llm.elapsed_seconds,
                known_sources=known_sources,
                prior_records=records,
            )
            _append_jsonl(run_dir / RESULTS_JSONL, record)
            records.append(record)
            logger.log(
                f"sequence={sequence_id} verdict={record['verdict']} intent={record.get('query_plan_intent')} "
                f"latency={record.get('latency_total_sec')} flags={','.join(record.get('flags') or [])}"
            )

            if len(records) % args.checkpoint_every == 0:
                _write_all_reports(
                    run_dir,
                    records,
                    run_config,
                    started_monotonic=started_monotonic,
                    status="RUNNING",
                    checkpoint_start=checkpoint_start,
                )
                checkpoint_start = len(records)

            reason = _automatic_stop_reason(records)
            if reason:
                status, stop_reason = "STOPPED", reason
                break
            if args.sleep > 0:
                time.sleep(args.sleep)
    except KeyboardInterrupt:
        status, stop_reason = "INTERRUPTED", "ctrl_c"
    except Exception as exc:  # defensive outer boundary, including persistence errors
        status, stop_reason = "STOPPED", f"runner_error:{type(exc).__name__}:{exc}"
        logger.log(traceback.format_exc())
    finally:
        run_config["status"] = status
        run_config["stop_reason"] = stop_reason
        run_config["finished_at"] = _now()
        _write_json(run_dir / RUN_CONFIG_JSON, run_config)
        _write_all_reports(
            run_dir,
            records,
            run_config,
            started_monotonic=started_monotonic,
            status=status,
            checkpoint_start=checkpoint_start,
        )
        logger.log(f"status={status} reason={stop_reason} processed={len(records)}")

    print(f"Run directory: {run_dir}")
    print(f"Status: {status}; reason: {stop_reason}; processed: {len(records)}")
    return 0 if status in {"COMPLETED", "INTERRUPTED"} else 2


class RunLogger:
    def __init__(self, path: Path, *, debug: bool) -> None:
        self.path = path
        self.debug = debug

    def log(self, message: str) -> None:
        line = f"{_now()} {message}"
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(line + "\n")
        if self.debug:
            print(line)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the autonomous LineHelper runtime probe.")
    parser.add_argument("--duration-minutes", type=float, default=DEFAULT_DURATION_MINUTES)
    parser.add_argument("--max-questions", type=int, default=DEFAULT_MAX_QUESTIONS)
    parser.add_argument("--seed-pack", type=Path, default=DEFAULT_SEED_PACK)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--checkpoint-every", type=int, default=DEFAULT_CHECKPOINT_EVERY)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--mode", choices=("seed", "mutation", "adversarial", "generated", "mixed"), default="mixed")
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--random-seed", type=int, default=20260716)
    parser.add_argument("--generator-batch-size", type=int, default=DEFAULT_GENERATOR_BATCH_SIZE)
    parser.add_argument("--no-llm-generation", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    if args.duration_minutes <= 0 or args.max_questions <= 0:
        parser.error("--duration-minutes and --max-questions must be > 0")
    if args.checkpoint_every <= 0 or args.generator_batch_size <= 0 or args.sleep < 0:
        parser.error("checkpoint/batch values must be > 0 and sleep must be >= 0")
    return args


def _prepare_run(args: argparse.Namespace, legacy_flag: str | None) -> tuple[Path, dict[str, Any]]:
    if args.resume:
        run_dir = _resolve_path(args.resume)
        config_path = run_dir / RUN_CONFIG_JSON
        if not run_dir.is_dir() or not config_path.exists():
            raise FileNotFoundError(f"resume run is invalid: {run_dir}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        args.seed_pack = Path(config["seed_pack"])
        args.mode = str(config["mode"])
        args.random_seed = int(config["random_seed"])
        args.generator_batch_size = int(config["generator_batch_size"])
        args.no_llm_generation = bool(config["no_llm_generation"])
        config["resumed_at"] = _now()
        config["resume_count"] = int(config.get("resume_count") or 0) + 1
    else:
        out_root = _resolve_path(args.out_dir)
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=False)
        config = {
            "run_id": run_dir.name,
            "started_at": _now(),
            "seed_pack": str(_resolve_path(args.seed_pack)),
            "out_dir": str(out_root),
            "run_dir": str(run_dir),
            "mode": args.mode,
            "random_seed": args.random_seed,
            "generator_batch_size": args.generator_batch_size,
            "no_llm_generation": args.no_llm_generation,
            "git_branch": base._git_output("branch", "--show-current"),
            "git_commit": base._git_output("rev-parse", "HEAD"),
            "git_status_short_at_start": base._git_output("status", "--short"),
            "python_version": sys.version.replace("\n", " "),
            "conversation_history_supported": False,
            "follow_up_sequences_excluded_from_single_question_metrics": FOLLOW_UP_SEQUENCES,
            "memory_write_policy": "read_only; no semantic or episodic writes",
            "ui_smoke": "not_run; no existing lightweight browser automation detected",
        }
    config.update({
        "duration_minutes": args.duration_minutes,
        "max_questions": args.max_questions,
        "checkpoint_every": args.checkpoint_every,
        "sleep": args.sleep,
        "dry_run": args.dry_run,
        "debug": args.debug,
        "legacy_query_analyzer_flag_value_before_removal": legacy_flag,
        "legacy_query_analyzer_flag_removed": "LINEHELPER_USE_QUERY_ANALYZER" not in os.environ,
        "env": {
            "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL"),
            "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL"),
            "OLLAMA_ANALYZER_MODEL": os.getenv("OLLAMA_ANALYZER_MODEL"),
            "LINEHELPER_USE_QUERY_ANALYZER": os.getenv("LINEHELPER_USE_QUERY_ANALYZER"),
        },
    })
    _write_json(run_dir / RUN_CONFIG_JSON, config)
    for name in (RESULTS_JSONL, GENERATOR_JSONL):
        (run_dir / name).touch(exist_ok=True)
    return run_dir, config


def _preflight(
    *, seed_path: Path, run_dir: Path, seeds: list[dict[str, Any]], dry_run: bool, logger: RunLogger,
) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, Any] = {}
    db_path = _resolve_path(DEFAULT_MEMORY_DB_PATH)
    checks["seed_pack"] = {"ok": seed_path.exists(), "path": str(seed_path), "questions": len(seeds)}
    try:
        probe = run_dir / ".write_probe"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        checks["output_directory"] = {"ok": True, "path": str(run_dir)}
    except OSError as exc:
        checks["output_directory"] = {"ok": False, "error": str(exc)}
        errors.append(f"output directory is not writable: {exc}")
    try:
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0])
        checks["memory_db"] = {"ok": True, "path": str(db_path), "chunks": count}
    except Exception as exc:
        checks["memory_db"] = {"ok": False, "path": str(db_path), "error": str(exc)}
        errors.append(f"memory DB unavailable: {type(exc).__name__}: {exc}")
    analyzer_source = inspect.getsource(RagAnswerGenerator._analyze_query)
    flag_absent = "LINEHELPER_USE_QUERY_ANALYZER" not in analyzer_source
    checks["query_analyzer_architecture"] = {
        "ok": flag_absent and "LINEHELPER_USE_QUERY_ANALYZER" not in os.environ,
        "legacy_env_absent": "LINEHELPER_USE_QUERY_ANALYZER" not in os.environ,
        "production_method": "RagAnswerGenerator._analyze_query",
        "analyzer_constructed_when_missing": "QueryAnalyzer()" in analyzer_source,
    }
    if not checks["query_analyzer_architecture"]["ok"]:
        errors.append("legacy LINEHELPER_USE_QUERY_ANALYZER still controls or contaminates runtime")
    checks["disk_free_bytes"] = _free_bytes(run_dir)
    if checks["disk_free_bytes"] < MIN_FREE_BYTES:
        errors.append(f"critically low free disk space: {checks['disk_free_bytes']} bytes")
    if dry_run:
        checks["ollama"] = {"ok": None, "skipped": "dry_run"}
        checks["runtime_query_plan"] = {"ok": None, "skipped": "dry_run"}
        return {"ok": not errors, "checks": checks, "errors": errors, "timestamp": _now()}

    ollama = base._preflight_ollama()
    checks["ollama"] = ollama
    if not ollama.get("ok"):
        errors.append(f"Ollama unavailable: {ollama.get('error')}")
    else:
        available = [str(name) for name in ollama.get("models") or []]
        requested = [os.environ["OLLAMA_MODEL"], os.environ["OLLAMA_ANALYZER_MODEL"]]
        missing = [model for model in requested if not _model_available(model, available)]
        checks["ollama_models"] = {"ok": not missing, "requested": requested, "available": available, "missing": missing}
        if missing:
            errors.append("Ollama model(s) missing: " + ", ".join(missing))
    if not errors:
        try:
            smoke = RagAnswerGenerator(db_path=db_path).answer(seeds[0]["question"])
            plan = smoke.query_plan if isinstance(smoke.query_plan, dict) else None
            checks["runtime_query_plan"] = {
                "ok": bool(plan),
                "question": seeds[0]["question"],
                "diagnostics": plan,
                "response_kind": smoke.response_kind,
            }
            if not plan:
                errors.append("critical: normal RagAnswerGenerator response has no diagnostics.query_plan")
        except Exception as exc:
            checks["runtime_query_plan"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            errors.append(f"runtime query_plan smoke failed: {type(exc).__name__}: {exc}")
    logger.log(f"preflight ok={not errors} errors={errors}")
    return {"ok": not errors, "checks": checks, "errors": errors, "timestamp": _now()}


def _preflight_failure(run_dir: Path, config: dict[str, Any], errors: list[str], logger: RunLogger) -> int:
    config["status"] = "PREFLIGHT_FAILED"
    config["preflight_errors"] = errors
    _write_json(run_dir / RUN_CONFIG_JSON, config)
    lines = ["# Autonomous runtime probe preflight error", "", "The question series was not started.", ""]
    lines.extend(f"- {error}" for error in errors)
    _write_text(run_dir / PREFLIGHT_ERROR_MD, "\n".join(lines) + "\n")
    _write_all_reports(run_dir, [], config, started_monotonic=time.monotonic(), status="PREFLIGHT_FAILED")
    logger.log("status=PREFLIGHT_FAILED " + " | ".join(errors))
    print(f"Preflight failed: {run_dir / PREFLIGHT_ERROR_MD}")
    return 1


def _question_from_seed(
    seed: dict[str, Any], sequence_id: int, strategy: str, mutation_type: str, question: str,
    *, question_id: str | None = None,
) -> dict[str, Any]:
    return {
        "question_id": question_id or f"{strategy[:1].upper()}-{seed['question_id']}-{sequence_id:04d}",
        "seed_question_id": seed["question_id"],
        "diagnostic_group": " ".join(value for value in (seed.get("group_id"), seed.get("group_name")) if value),
        "group_id": seed.get("group_id", ""),
        "group_name": seed.get("group_name", ""),
        "diagnostic_focus": seed.get("diagnostic_focus", ""),
        "generation_strategy": strategy,
        "mutation_type": mutation_type,
        "original_question": seed["question"],
        "question": question,
        "expected_intent": seed.get("expected_intent", ""),
        "expected_response_kind": seed.get("expected_response_kind", ""),
        "expected_source_hint": seed.get("expected_source_hint", ""),
        "risk_tag": seed.get("risk_tag", ""),
    }


def _adversarial_question(sequence_id: int) -> dict[str, Any]:
    case = ADVERSARIAL_CASES[(sequence_id - 1) % len(ADVERSARIAL_CASES)]
    case_id, group, intent, kind, question, mutation_type = case
    group_id, group_name = base._split_group(group)
    cycle = (sequence_id - 1) // len(ADVERSARIAL_CASES)
    if cycle:
        prefixes = ("Скажи прямо: ", "а вот ", "не понял: ", "быстро уточни: ")
        question = prefixes[cycle % len(prefixes)] + _lower_initial(question)
    return {
        "question_id": f"{case_id}-{sequence_id:04d}",
        "seed_question_id": "",
        "diagnostic_group": group,
        "group_id": group_id,
        "group_name": group_name,
        "diagnostic_focus": mutation_type,
        "generation_strategy": "adversarial",
        "mutation_type": mutation_type,
        "original_question": case[4],
        "question": question,
        "expected_intent": intent,
        "expected_response_kind": kind,
        "expected_source_hint": "",
        "risk_tag": mutation_type,
    }


def _mutate_question(question: str, index: int) -> tuple[str, str]:
    old, new = SYNONYMS[index % len(SYNONYMS)]
    mutated, count = re.subn(re.escape(old), new, question, count=1, flags=re.I)
    if count:
        return mutated, f"synonym:{old}->{new}"
    variants = (
        lambda value: "Расскажи, " + _lower_initial(value),
        lambda value: value.rstrip("?.") + " — как это устроено?",
        lambda value: "Подскажи, пожалуйста: " + _lower_initial(value),
        lambda value: "Можно кратко уточнить: " + _lower_initial(value),
    )
    return variants[index % len(variants)](question), "deterministic_paraphrase"


def _noise_question(question: str, index: int) -> tuple[str, str]:
    text = question.lower().replace("?", "").replace(".", "")
    variants = (
        lambda value: "а " + value.replace("вообще", "вобще"),
        lambda value: value.replace("что", "чо").replace("куда", "куд"),
        lambda value: "не понял " + value,
        lambda value: value.replace("отделы", "атделы").replace("договор", "догавор"),
        lambda value: value.upper(),
        lambda value: value + " ну это самое",
    )
    return variants[index % len(variants)](text), "user_noise"


def _strategy_for(mode: str, sequence_id: int, random_seed: int) -> str:
    if mode == "mixed":
        cycle = list(MIXED_CYCLE)
        random.Random(random_seed).shuffle(cycle)
        return cycle[(sequence_id - 1) % len(cycle)]
    if mode == "mutation":
        return "noise" if sequence_id % 4 == 0 else "mutation"
    return mode


def _generator_prompt(seed: dict[str, Any], examples: list[str], batch_size: int) -> str:
    return (
        "Сгенерируй {count} новых пользовательских вопросов для технической проверки LineHelper.\n"
        "Диагностическая группа: {group}. Ожидаемый intent: {intent}.\n"
        "Сохраняй тему и тип сложности, но меняй формулировки. Допускаются разговорность, "
        "опечатки, краткость и смешанные формулировки. Не добавляй корпоративных фактов и не "
        "отвечай на вопросы. Верни только JSON-массив строк.\nПримеры:\n- {examples}"
    ).format(
        count=batch_size,
        group=" ".join(filter(None, (seed.get("group_id"), seed.get("group_name")))),
        intent=seed.get("expected_intent") or "unknown",
        examples="\n- ".join(examples),
    )


def _parse_generated_questions(value: str) -> list[str]:
    clean = value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", clean)
        if not match:
            return []
        data = json.loads(match.group(0))
    if not isinstance(data, list):
        return []
    return _dedupe_text([item.strip() for item in data if isinstance(item, str) and item.strip()])


def _base_runtime_row(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": question["question_id"],
        "group_id": question["group_id"],
        "group_name": question["group_name"],
        "question": question["question"],
        "expected_intent": question["expected_intent"],
        "expected_behavior": "",
        "expected_response_kind": question["expected_response_kind"],
        "expected_source_hint": question["expected_source_hint"],
        "risk_tag": question["risk_tag"],
        "diagnostic_focus": question["diagnostic_focus"],
    }


def _augment_record(
    record: dict[str, Any], *, question: dict[str, Any], run_id: str, sequence_id: int,
    retrieval_latency: float, answer_latency: float, known_sources: set[tuple[str, str]],
    prior_records: list[dict[str, Any]],
) -> dict[str, Any]:
    sources = record.get("sources") or []
    candidates = record.get("diagnostic_candidates") or []
    record.update({
        "run_id": run_id,
        "sequence_id": sequence_id,
        "seed_question_id": question["seed_question_id"],
        "diagnostic_group": question["diagnostic_group"],
        "diagnostic_focus": question["diagnostic_focus"],
        "generation_strategy": question["generation_strategy"],
        "mutation_type": question["mutation_type"],
        "original_question": question["original_question"],
        "query_plan_present": record.get("query_plan_enabled") is True,
        "sources_sections": [str(source.get("section") or "") for source in sources],
        "diagnostic_candidate_titles": [base._source_title(source) for source in candidates],
        "latency_retrieval_sec": round(retrieval_latency, 3),
        "latency_answer_sec": round(answer_latency, 3),
        "traceback": record.get("error_traceback") or "",
        "timestamp": _now(),
    })
    record["answer_preview"] = base._preview(str(record.get("answer_text") or ""), 500)
    verdict, flags = _evaluate_probe_record(record, known_sources=known_sources, prior_records=prior_records)
    record["verdict"] = verdict
    record["flags"] = flags
    return record


def _evaluate_probe_record(
    record: dict[str, Any], *, known_sources: set[tuple[str, str]] | None = None,
    prior_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str]]:
    base_verdict, base_flags = base._evaluate_record(record)
    rename = {
        "NO_SOURCES_FOR_KNOWN_CORPORATE_INTENT": "NO_SOURCES_FOR_CORPORATE_INTENT",
        "PARTIAL_FOR_EXPECTED_ANSWER": "PARTIAL_FOR_EXPECTED_FULL_ANSWER",
    }
    flags = [rename.get(flag, flag) for flag in base_flags]
    critical = set(flags if base_verdict == "FAIL" else [])
    warnings = set(flags if base_verdict == "WARN" else [])
    if base_verdict == "ERROR":
        return "ERROR", _dedupe_text(flags or ["AGENT_ERROR"])

    intent = str(record.get("query_plan_intent") or "")
    kind = str(record.get("response_kind") or "")
    answer = str(record.get("answer_text") or "")
    question = str(record.get("question") or "")
    sources = record.get("sources") or []
    sources_count = int(record.get("sources_count") or 0)
    if not record.get("query_plan_present"):
        critical.add("QUERY_PLAN_MISSING")
    if kind == "no_answer" and sources_count:
        critical.add("NO_ANSWER_WITH_SOURCES")
    if intent in (base.NO_SOURCE_INTENTS - {"unknown"}) and sources_count:
        critical.add("SOURCE_INCOMPATIBLE_WITH_INTENT")
    if known_sources is not None:
        for source in sources:
            key = (str(source.get("title") or ""), str(source.get("source") or ""))
            if key not in known_sources:
                critical.add("SOURCE_NOT_IN_DATABASE")
                break
    corporate = bool(CORPORATE_RE.search(question)) or intent in base.KNOWN_CORPORATE_INTENTS
    if kind == "answer" and corporate and not sources_count and PROCEDURE_RE.search(answer):
        critical.add("CORPORATE_PROCEDURE_WITHOUT_SOURCE")
    if corporate and kind == "no_answer":
        warnings.add("NO_ANSWER_FOR_CORPORATE_QUESTION")
    if str(record.get("risk_tag") or "") == "off_topic" and intent != "off_topic":
        warnings.add("UNEXPECTED_INTENT")
    if str(record.get("risk_tag") or "") == "one_c" and intent != "one_c_operational_lookup":
        warnings.add("UNEXPECTED_INTENT")
    if kind == "answer" and corporate and not sources_count and len(answer) >= 80:
        warnings.add("POSSIBLE_HALLUCINATION")
    expected_hint = str(record.get("expected_source_hint") or "").strip()
    if expected_hint and sources:
        haystack = " ".join(
            f"{source.get('title', '')} {source.get('source', '')} {source.get('section', '')}"
            for source in sources
        )
        hint_tokens = [token for token in re.findall(r"\w+", expected_hint.casefold()) if len(token) > 3]
        if hint_tokens and not any(token in haystack.casefold() for token in hint_tokens):
            warnings.add("SUSPICIOUS_SOURCE")
            if PROCEDURE_RE.search(question):
                warnings.add("PROCEDURE_WITHOUT_STRONG_SOURCE")
    source_text = " ".join(str(source.get("title") or "") + " " + str(source.get("source") or "") for source in sources)
    if re.search(r"smoke|test|manual", source_text, re.I):
        warnings.add("SUSPICIOUS_SOURCE")
    if not sources_count and re.search(r"обратитесь к|уточните у|спросите у", answer, re.I):
        warnings.add("GENERIC_ESCALATION_ADVICE")
    recent = list(prior_records or [])[-5:]
    top = str(record.get("top_source_title") or "")
    if top and len(recent) == 5 and all(str(item.get("top_source_title") or "") == top for item in recent):
        warnings.add("REPEATED_SOURCE_DOMINANCE")
    if critical:
        return "FAIL", _dedupe_text([*critical, *warnings])
    if warnings:
        return "WARN", _dedupe_text(warnings)
    return "PASS", []


def _automatic_stop_reason(records: list[dict[str, Any]]) -> str | None:
    if len(records) >= 5 and all(record.get("verdict") == "ERROR" for record in records[-5:]):
        return "five_consecutive_errors"
    if records:
        last = records[-1]
        error = f"{last.get('error_type')} {last.get('error_message')}".casefold()
        if last.get("verdict") == "ERROR" and any(token in error for token in ("not reachable", "connection", "winerror 10061")):
            return "ollama_unavailable"
    if len(records) >= 10:
        latencies = [base._safe_float(row.get("latency_total_sec")) for row in records[-10:]]
        values = [value for value in latencies if value is not None]
        if len(values) == 10 and statistics.fmean(values) > 120:
            return "last_10_average_latency_over_120_seconds"
    return None


def _write_all_reports(
    run_dir: Path, records: list[dict[str, Any]], config: dict[str, Any], *,
    started_monotonic: float, status: str, checkpoint_start: int = 0,
) -> None:
    elapsed = max(0.0, time.monotonic() - started_monotonic)
    summary = _summary(records, config, status=status, elapsed=elapsed)
    _write_results_csv(run_dir / RESULTS_CSV, records)
    _write_text(run_dir / ANSWERS_MD, _answers_markdown(records))
    _write_json(run_dir / SUMMARY_JSON, summary)
    _write_text(run_dir / FAILURES_MD, _cases_report("FAIL/ERROR", records, {"FAIL", "ERROR"}))
    _write_text(run_dir / WARNINGS_MD, _cases_report("WARN", records, {"WARN"}))
    _write_text(run_dir / SUSPICIOUS_MD, _suspicious_report(records))
    _write_text(run_dir / CHECKPOINT_MD, _checkpoint_report(records, summary, config, checkpoint_start))
    _write_text(run_dir / REPORT_MD, _level1_report(records, summary, config))


def _summary(records: list[dict[str, Any]], config: dict[str, Any], *, status: str, elapsed: float) -> dict[str, Any]:
    verdicts = Counter(str(row.get("verdict") or "") for row in records)
    latencies = [value for value in (base._safe_float(row.get("latency_total_sec")) for row in records) if value is not None]
    return {
        "run_id": config["run_id"], "status": status, "processed": len(records),
        "remaining": max(0, int(config["max_questions"]) - len(records)),
        "elapsed_sec": round(elapsed, 3),
        "pass": verdicts["PASS"], "warn": verdicts["WARN"], "fail": verdicts["FAIL"], "error": verdicts["ERROR"],
        "average_latency_sec": round(statistics.fmean(latencies), 3) if latencies else None,
        "median_latency_sec": round(statistics.median(latencies), 3) if latencies else None,
        "max_latency_sec": round(max(latencies), 3) if latencies else None,
        "by_diagnostic_group": _nested_counts(records, "diagnostic_group"),
        "by_generation_strategy": _nested_counts(records, "generation_strategy"),
        "intent_distribution": dict(Counter(str(row.get("query_plan_intent") or "null") for row in records)),
        "response_kind_distribution": dict(Counter(str(row.get("response_kind") or "null") for row in records)),
        "top_sources": dict(Counter(title for row in records for title in row.get("sources_titles") or []).most_common(20)),
        "top_flags": dict(Counter(flag for row in records for flag in row.get("flags") or []).most_common(30)),
        "updated_at": _now(),
    }


def _nested_counts(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted({str(row.get(field) or "unknown") for row in records}):
        subset = [row for row in records if str(row.get(field) or "unknown") == key]
        verdicts = Counter(str(row.get("verdict") or "") for row in subset)
        result[key] = {"total": len(subset), "PASS": verdicts["PASS"], "WARN": verdicts["WARN"], "FAIL": verdicts["FAIL"], "ERROR": verdicts["ERROR"]}
    return result


def _checkpoint_report(records: list[dict[str, Any]], summary: dict[str, Any], config: dict[str, Any], start: int) -> str:
    average = summary.get("average_latency_sec") or 0
    estimate = summary["remaining"] * (average + float(config.get("sleep") or 0))
    last = records[-10:]
    new_fail_flags = Counter(
        flag for row in records[start:] if row.get("verdict") in {"FAIL", "ERROR"} for flag in row.get("flags") or []
    )
    recommendation = "CONTINUE"
    if summary["error"] or summary["fail"]:
        recommendation = "INSPECT"
    if _automatic_stop_reason(records):
        recommendation = "STOP"
    lines = [
        "# Autonomous Runtime Probe Checkpoint", "",
        f"- processed: {summary['processed']}", f"- remaining: {summary['remaining']}",
        f"- elapsed time: {summary['elapsed_sec']} sec", f"- estimated remaining time: {round(estimate, 1)} sec",
        f"- PASS/WARN/FAIL/ERROR: {summary['pass']}/{summary['warn']}/{summary['fail']}/{summary['error']}",
        f"- average latency: {summary['average_latency_sec']} sec", f"- median latency: {summary['median_latency_sec']} sec",
        f"- recommendation: **{recommendation}**", "", "## Last 10 results", "",
        "| seq | id | verdict | intent | latency | question |", "|---:|---|---|---|---:|---|",
    ]
    for row in last:
        lines.append(f"| {row.get('sequence_id')} | {_md(row.get('question_id'))} | {row.get('verdict')} | {_md(row.get('query_plan_intent'))} | {row.get('latency_total_sec')} | {_md(base._preview(str(row.get('question') or ''), 100))} |")
    lines.extend(["", "## Top intents", _counter_md(summary["intent_distribution"]), "", "## Top sources", _counter_md(summary["top_sources"]), "", "## Top flags", _counter_md(summary["top_flags"]), "", "## New failure patterns", _counter_md(dict(new_fail_flags))])
    return "\n".join(lines) + "\n"


def _level1_report(records: list[dict[str, Any]], summary: dict[str, Any], config: dict[str, Any]) -> str:
    failures = [row for row in records if row.get("verdict") in {"FAIL", "ERROR"}]
    warnings = [row for row in records if row.get("verdict") == "WARN"]
    slowest = sorted(records, key=lambda row: base._safe_float(row.get("latency_total_sec")) or -1, reverse=True)[:20]
    plan_present = sum(bool(row.get("query_plan_present")) for row in records)
    fallbacks = sum(bool(row.get("query_plan_fallback_used")) for row in records)
    suspicious = _select_suspicious(records, 50)
    lines = [
        "# LineHelper Autonomous Runtime Probe — Level 1 Technical Report", "",
        "## 1. Run configuration", _config_md(config), "",
        "## 2. Runtime architecture actually tested",
        "`question → Query Analyzer → QueryPlan → retrieval → evidence gate → final LLM → answer and sources`",
        "The production `RagAnswerGenerator.answer()` path was called directly. The legacy feature flag was removed; no direct analyzer-only path and no memory writes were used.", "",
        "## 3. Overall results", _summary_md(summary), "",
        "## 4. Results by diagnostic group", _nested_md(summary["by_diagnostic_group"], "diagnostic group"), "",
        "## 5. Results by generation strategy", _nested_md(summary["by_generation_strategy"], "strategy"), "",
        "## 6. Intent distribution", _counter_md(summary["intent_distribution"]), "",
        "## 7. Response-kind distribution", _counter_md(summary["response_kind_distribution"]), "",
        "## 8. Source usage and source anomalies", _counter_md(summary["top_sources"]),
        f"\nSource-related flagged cases: {sum(any('SOURCE' in flag for flag in row.get('flags') or []) for row in records)}.", "",
        "## 9. Query Analyzer quality signals",
        f"- query_plan present: {plan_present}/{len(records)}", f"- fallback used: {fallbacks}",
        f"- missing/empty expansions: {sum('EMPTY_QUERY_EXPANSIONS' in (row.get('flags') or []) for row in records)}", "",
        "## 10. Critical failures", _brief_cases(failures, 40), "",
        "## 11. Warning patterns", _counter_md(dict(Counter(flag for row in warnings for flag in row.get('flags') or []))), "",
        "## 12. Slowest questions", _latency_table(slowest), "",
        "## 13. Repeated failure clusters", _failure_clusters(records), "",
        "## 14. Most suspicious cases for level-2 review", _brief_cases(suspicious, 50), "",
        "## 15. First-level technical conclusion",
        _conclusion(summary), "",
        "Contextual follow-up note: the current `RagAnswerGenerator.answer(question)` call has no conversation-history argument. Follow-up sequences were recorded in run configuration but excluded from single-question quality metrics.", "",
    ]
    return "\n".join(lines)


def _answers_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# Full answers — Autonomous Runtime Probe", ""]
    for row in records:
        lines.extend([
            f"## {row.get('sequence_id')}. {row.get('question_id')}",
            f"- group: {row.get('diagnostic_group') or '-'}",
            f"- generation strategy: {row.get('generation_strategy') or '-'} / {row.get('mutation_type') or '-'}",
            f"- question: {row.get('question')}",
            f"- intent: {row.get('query_plan_intent') or '-'}",
            f"- response_kind: {row.get('response_kind') or '-'}",
            f"- sources: {', '.join(row.get('sources_titles') or []) or '-'}",
            f"- flags: {', '.join(row.get('flags') or []) or '-'}",
            f"- latency: {row.get('latency_total_sec')} sec", "", "### Answer", "",
            str(row.get("answer_text") or ""), "",
        ])
    return "\n".join(lines)


def _cases_report(title: str, records: list[dict[str, Any]], verdicts: set[str]) -> str:
    selected = [row for row in records if row.get("verdict") in verdicts]
    return f"# Autonomous Runtime Probe {title} cases\n\n" + _brief_cases(selected, len(selected)) + "\n"


def _suspicious_report(records: list[dict[str, Any]]) -> str:
    return "# Suspicious cases for level-2 review\n\n" + _brief_cases(_select_suspicious(records, 50), 50) + "\n"


def _select_suspicious(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    def score(row: dict[str, Any]) -> tuple[int, float]:
        points = {"ERROR": 8, "FAIL": 7, "WARN": 3, "PASS": 0}.get(str(row.get("verdict")), 0)
        points += len(row.get("flags") or [])
        if row.get("generation_strategy") in {"adversarial", "noise", "generated"}:
            points += 2
        if row.get("query_plan_fallback_used"):
            points += 2
        return points, base._safe_float(row.get("latency_total_sec")) or 0
    return sorted(records, key=score, reverse=True)[:limit]


def _brief_cases(records: list[dict[str, Any]], limit: int) -> str:
    if not records:
        return "No cases."
    lines = []
    for row in records[:limit]:
        lines.append(
            f"- **{row.get('question_id')}** [{row.get('verdict')}] strategy={row.get('generation_strategy')}, "
            f"intent={row.get('query_plan_intent') or '-'}, source={row.get('top_source_title') or '-'}, "
            f"flags={', '.join(row.get('flags') or []) or '-'}: {_md(base._preview(str(row.get('question') or ''), 180))}"
        )
    return "\n".join(lines)


def _latency_table(records: list[dict[str, Any]]) -> str:
    lines = ["| id | latency sec | analyzer | retrieval | answer | verdict | question |", "|---|---:|---:|---:|---:|---|---|"]
    for row in records:
        lines.append(f"| {_md(row.get('question_id'))} | {row.get('latency_total_sec')} | {row.get('latency_analyzer_sec')} | {row.get('latency_retrieval_sec')} | {row.get('latency_answer_sec')} | {row.get('verdict')} | {_md(base._preview(str(row.get('question') or ''), 100))} |")
    return "\n".join(lines)


def _failure_clusters(records: list[dict[str, Any]]) -> str:
    clusters = Counter((row.get("diagnostic_group") or "unknown", flag) for row in records for flag in row.get("flags") or [] if row.get("verdict") in {"FAIL", "ERROR"})
    if not clusters:
        return "No repeated critical clusters."
    return "\n".join(f"- {group} / {flag}: {count}" for (group, flag), count in clusters.most_common(20))


def _conclusion(summary: dict[str, Any]) -> str:
    if not summary["processed"]:
        return "No runtime questions were completed; no quality conclusion is possible."
    critical = summary["fail"] + summary["error"]
    return (
        f"The run completed {summary['processed']} question(s) with {critical} critical FAIL/ERROR case(s). "
        "This is a first-level technical classification only; corporate-content correctness must be reviewed from "
        "answers_full.md together with sources and diagnostics."
    )


def _summary_md(summary: dict[str, Any]) -> str:
    return "\n".join([
        f"- status: {summary['status']}", f"- processed: {summary['processed']}",
        f"- PASS: {summary['pass']}", f"- WARN: {summary['warn']}", f"- FAIL: {summary['fail']}", f"- ERROR: {summary['error']}",
        f"- average latency: {summary['average_latency_sec']} sec", f"- median latency: {summary['median_latency_sec']} sec",
    ])


def _config_md(config: dict[str, Any]) -> str:
    keys = ("run_id", "started_at", "git_branch", "git_commit", "seed_pack", "mode", "duration_minutes", "max_questions", "random_seed")
    lines = [f"- {key}: {config.get(key)}" for key in keys]
    lines.extend(f"- {key}: {value}" for key, value in (config.get("env") or {}).items())
    return "\n".join(lines)


def _nested_md(data: dict[str, Any], label: str) -> str:
    lines = [f"| {label} | total | PASS | WARN | FAIL | ERROR |", "|---|---:|---:|---:|---:|---:|"]
    for key, values in data.items():
        lines.append(f"| {_md(key)} | {values['total']} | {values['PASS']} | {values['WARN']} | {values['FAIL']} | {values['ERROR']} |")
    return "\n".join(lines)


def _counter_md(data: dict[str, Any]) -> str:
    if not data:
        return "No data."
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(data.items(), key=lambda item: (-int(item[1]), str(item[0])))[:30]:
        lines.append(f"| {_md(key)} | {value} |")
    return "\n".join(lines)


def _known_sources(db_path: Path) -> set[tuple[str, str]]:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        return {(str(title or ""), str(source or "")) for title, source in connection.execute("SELECT title, source FROM memory_chunks")}


def _memory_db_healthy(db_path: Path) -> bool:
    try:
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2) as connection:
            connection.execute("SELECT 1 FROM memory_chunks LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _model_available(requested: str, available: list[str]) -> bool:
    def normalized(value: str) -> str:
        return value.casefold().removesuffix(":latest")
    return normalized(requested) in {normalized(value) for value in available}


def _append_generated_question(path: Path, question: dict[str, Any], sequence_id: int) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=GENERATED_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        row = dict(question)
        row["scheduled_sequence_id"] = sequence_id
        row["created_at"] = _now()
        writer.writerow(row)


def _write_results_csv(path: Path, records: list[dict[str, Any]]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            for key in ("sources_titles", "sources_sections", "query_plan_expansions", "query_plan_preferred_sources", "diagnostic_candidate_titles", "flags"):
                row[key] = "; ".join(str(item) for item in row.get(key) or [])
            writer.writerow(row)
    temp.replace(path)


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(serialized + "\n")
        file.flush()
        os.fsync(file.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8", newline="\n")
    temp.replace(path)


def _resolve_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _dedupe_text(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result


def _lower_initial(value: str) -> str:
    if not value or (len(value) >= 2 and value[:2].isupper()):
        return value
    return value[0].lower() + value[1:]


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
