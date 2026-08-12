"""Command line interface for LineHelper."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from linehelper import __version__
from linehelper.analytics.interaction_logger import create_interaction_logger
from linehelper.config import LineHelperConfig, LineHelperConfigError, load_config
from linehelper.llm.answer_generator import (
    RagAnswer,
    RagAnswerError,
    RagAnswerGenerator,
    rag_answer_history_metadata,
)
from linehelper.rag.conversation_resolver import ConversationSession
from linehelper.runtime.doctor import (
    get_database_stats,
    get_ollama_status,
    query_analyzer_state,
    run_doctor,
)
from linehelper.runtime.process_manager import (
    get_runtime_status,
    start_background,
    start_foreground,
    stop_background,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the LineHelper CLI."""
    _configure_stdout()
    args = _parse_args(argv)
    try:
        config = load_config()
    except LineHelperConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1

    command = args.command or "start"
    if command == "start":
        return _command_start(args, config)
    if command == "stop":
        return _command_stop(config)
    if command == "status":
        return _command_status(config)
    if command == "doctor":
        return _command_doctor(config)
    if command == "chat":
        return _command_chat(args, config)
    if command == "index":
        return _command_index(args, config)
    if command == "test":
        return _command_test(args, config)
    if command == "version":
        return _command_version(config)
    print(f"Неизвестная команда: {command}", file=sys.stderr)
    return 2


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="linehelper",
        description="Единая команда запуска и управления LineHelper.",
    )
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="Запустить Streamlit UI LineHelper.")
    start.add_argument("--port", type=int, default=None, help="Порт Streamlit UI.")
    start.add_argument("--host", default=None, help="Адрес/host Streamlit UI.")
    start.add_argument("--background", action="store_true", help="Запустить UI в фоне.")
    start.add_argument("--no-browser", action="store_true", help="Не открывать браузер автоматически.")

    subparsers.add_parser("stop", help="Остановить фоновый LineHelper.")
    subparsers.add_parser("status", help="Показать состояние LineHelper.")
    subparsers.add_parser("doctor", help="Проверить окружение LineHelper.")

    chat = subparsers.add_parser(
        "chat",
        help="Задать вопрос или начать интерактивный диалог через основной RAG pipeline.",
    )
    chat.add_argument("--debug", action="store_true", help="Показать диагностику Query Analyzer и retrieval.")
    chat.add_argument(
        "--interactive",
        action="store_true",
        help="Запустить диалог; без question интерактивный режим включается автоматически.",
    )
    chat.add_argument("question", nargs="?", help="Вопрос для LineHelper.")

    index = subparsers.add_parser("index", help="Команды индексации.")
    index_subparsers = index.add_subparsers(dest="index_command")
    organization_index = index_subparsers.add_parser("organization", help="Индексировать оргструктуру.")
    organization_index.add_argument("--dry-run", action="store_true")
    organization_index.add_argument("--replace-version", action="store_true")
    organization_index.add_argument("--verbose", action="store_true")

    test = subparsers.add_parser("test", help="Запустить тестовые пакеты.")
    test_subparsers = test.add_subparsers(dest="test_command")
    organization_test = test_subparsers.add_parser("organization", help="Тестовый пакет оргструктуры.")
    organization_test.add_argument("--limit", type=int)
    organization_test.add_argument("--group")
    organization_test.add_argument("--question-id")
    organization_test.add_argument("--resume", action="store_true")
    organization_test.add_argument("--retrieval-only", action="store_true")
    organization_test.add_argument("--model")
    organization_test.add_argument("--concurrency", type=int)
    organization_test.add_argument("--verbose", action="store_true")

    runtime_test = test_subparsers.add_parser("runtime", help="Тестовый пакет runtime Query Analyzer.")
    runtime_test.add_argument("--pack", type=Path)
    runtime_test.add_argument("--out-dir", type=Path)
    runtime_test.add_argument("--limit", type=int)
    runtime_test.add_argument("--group")
    runtime_test.add_argument("--resume", action="store_true")
    runtime_test.add_argument("--sleep", type=float)
    runtime_test.add_argument("--debug", action="store_true")

    subparsers.add_parser("version", help="Показать версию LineHelper.")
    return parser.parse_args(argv)


def _command_start(args: argparse.Namespace, config: LineHelperConfig) -> int:
    host = getattr(args, "host", None) or config.streamlit_host
    port = getattr(args, "port", None) or config.streamlit_port
    background = bool(getattr(args, "background", False))
    no_browser = bool(getattr(args, "no_browser", False))
    if background:
        status = start_background(config, host=host, port=port, no_browser=no_browser)
        print("LineHelper запущен в фоновом режиме.")
        print(f"PID: {status.pid}")
        print(f"UI: {status.ui_url}")
        print(f"Log: {status.log_file}")
        return 0
    return start_foreground(config, host=host, port=port, no_browser=no_browser)


def _command_stop(config: LineHelperConfig) -> int:
    stopped, message = stop_background(config)
    print(message)
    return 0 if stopped or "не запущен" in message else 1


def _command_status(config: LineHelperConfig) -> int:
    runtime = get_runtime_status(config)
    db = get_database_stats(config.db_path)
    ollama = get_ollama_status(config)
    analyzer_ok, analyzer_detail = query_analyzer_state()

    print(f"LineHelper: {'running' if runtime.running else 'stopped'}")
    print(f"PID: {runtime.pid if runtime.pid is not None else '-'}")
    print(f"UI: {runtime.ui_url}")
    print(f"Database: {_display_path(config.db_path, config.project_root)}")
    print(f"Database exists: {'yes' if db.db_exists else 'no'}")
    print(f"Semantic chunks: {db.semantic_chunks}")
    print(f"Episodic chunks: {db.episodic_chunks}")
    print(f"Organization chunks: {db.organization_chunks}")
    print(f"Organization source version: {db.organization_source_version or '-'}")
    print(f"Query Analyzer: {analyzer_detail if analyzer_ok else 'unavailable'}")
    print(f"Ollama: {'available' if ollama.available else 'unavailable'}")
    print(f"Model: {config.model}")
    print(f"Model available: {'yes' if ollama.model_available else 'no'}")
    return 0


def _command_doctor(config: LineHelperConfig) -> int:
    checks, exit_code = run_doctor(config)
    for check in checks:
        print(f"[{check.level}] {check.message}")
    return exit_code


def _command_chat(args: argparse.Namespace, config: LineHelperConfig) -> int:
    interaction_logger = create_interaction_logger(
        config.analytics,
        project_root=config.project_root,
    )
    try:
        generator = RagAnswerGenerator(
            db_path=config.db_path,
            catalog_db_path=config.catalog_db_path,
            catalog_source_root=config.catalog_source_root,
            interaction_logger=interaction_logger,
        )
    except Exception as exc:
        interaction_logger.close()
        print(f"Не удалось инициализировать RAG: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.interactive or args.question is None:
            return _interactive_chat(generator, debug=bool(args.debug))

        try:
            result = generator.answer(
                str(args.question),
                session_id=str(uuid4()),
            )
        except (ValueError, RagAnswerError) as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Не удалось выполнить вопрос: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        _print_chat_result(result, debug=bool(args.debug))
        return 0
    finally:
        interaction_logger.close()


def _interactive_chat(generator: RagAnswerGenerator, *, debug: bool) -> int:
    """Run one in-memory dialog without persisting it between CLI processes."""
    session = ConversationSession()
    session_id = str(uuid4())
    print("Интерактивный LineHelper. Команды: /new — новый диалог, /exit — выход.")
    while True:
        try:
            question = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question.casefold() in {"/exit", "/quit"}:
            return 0
        if question.casefold() == "/new":
            session.reset()
            session_id = str(uuid4())
            print("История текущего диалога очищена.")
            continue
        try:
            result = generator.answer(
                question,
                history=session.messages,
                session_id=session_id,
            )
        except (ValueError, RagAnswerError) as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            continue
        except Exception as exc:
            print(
                f"Не удалось выполнить вопрос: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue

        _print_chat_result(result, debug=debug)
        session.append_exchange(
            question,
            result.answer,
            assistant_metadata=rag_answer_history_metadata(result),
        )


def _print_chat_result(result: RagAnswer, *, debug: bool) -> None:
    """Render one completed answer for one-shot and interactive modes."""
    print(f"Вопрос: {result.question}")
    print()
    print(f"Ответ: {result.answer}")
    print()
    if result.catalog is not None:
        print("Источники каталога:")
        if result.catalog_sources:
            for index, source in enumerate(result.catalog_sources, start=1):
                document = source.source_filename or "документ не указан"
                details = [
                    document,
                    f"страница спецификации: {source.source_page}",
                    f"узел: {source.assembly_code}",
                ]
                if source.revision:
                    details.append(f"ревизия: {source.revision}")
                if source.machine_number:
                    details.append(f"машина: {source.machine_number}")
                print(f"{index}. {' | '.join(details)}")
        else:
            print("- подтверждённые catalog occurrences отсутствуют")
    if result.sources:
        print("Источники документов:" if result.catalog is not None else "Источники:")
        for index, source in enumerate(result.sources, start=1):
            details = " | ".join(
                part
                for part in (
                    source.title,
                    source.section,
                    f"score={source.score:.3f}" if source.score is not None else "",
                )
                if part
            )
            print(f"{index}. {details}")
    elif result.catalog is None:
        print("Источники:")
        print("-")

    if debug:
        query_plan = result.query_plan or {}
        conversation = result.conversation or {}
        print()
        print("Debug:")
        print(f"original question: {conversation.get('original_question') or result.question}")
        print(
            "resolved question: "
            f"{conversation.get('resolved_question') or result.resolved_question or '-'}"
        )
        print(
            "conversation history used: "
            f"{conversation.get('conversation_history_used', False)}"
        )
        print(f"is follow-up: {conversation.get('is_follow_up', False)}")
        print(f"topic changed: {conversation.get('topic_changed', False)}")
        print(f"resolution kind: {conversation.get('resolution_kind') or '-'}")
        print(f"inherited slots: {_join(conversation.get('inherited_slots'))}")
        print(
            "resolution reasons: "
            f"{_join(conversation.get('resolution_reasons'))}"
        )
        print(f"intent: {query_plan.get('intent') or '-'}")
        print(f"raw intent: {query_plan.get('raw_intent') or '-'}")
        print(f"requested fact type: {query_plan.get('requested_fact_type') or '-'}")
        print(
            "requested fact type initial/finalized: "
            f"{query_plan.get('initial_requested_fact_type') or '-'}/"
            f"{query_plan.get('finalized_requested_fact_type') or '-'}"
        )
        print(
            "fact type resolution status: "
            f"{query_plan.get('resolution_status') or '-'}"
        )
        print(
            "fact type matched signals: "
            f"{_join(query_plan.get('matched_signals'))}"
        )
        print(
            "fact type rejected candidates: "
            f"{_join(query_plan.get('rejected_fact_types'))}"
        )
        print(
            "fact type decision reasons: "
            f"{_join(query_plan.get('decision_reasons'))}"
        )
        print(f"temporal scope: {query_plan.get('temporal_scope') or '-'}")
        print(f"subject: {query_plan.get('subject') or '-'}")
        print(f"operational lookup: {query_plan.get('operational_lookup', False)}")
        print(f"catalog identifier: {query_plan.get('catalog_identifier') or '-'}")
        print(f"catalog result count: {query_plan.get('catalog_result_count', '-')}")
        print(f"catalog status: {query_plan.get('catalog_status') or '-'}")
        print(f"catalog match type: {query_plan.get('catalog_match_type') or '-'}")
        print(f"source route: {query_plan.get('source_route') or '-'}")
        print(
            "catalog probe performed: "
            f"{query_plan.get('catalog_probe_performed', False)}"
        )
        print(
            "catalog probe result count: "
            f"{query_plan.get('catalog_probe_result_count', 0)}"
        )
        print(f"catalog top score: {query_plan.get('catalog_top_score')}")
        print(
            "catalog top field coverage: "
            f"{query_plan.get('catalog_top_field_coverage')}"
        )
        print(
            "catalog coherent results: "
            f"{query_plan.get('catalog_probe_coherent_results', 0)}"
        )
        print(
            "corporate evidence available: "
            f"{query_plan.get('corporate_evidence_available', False)}"
        )
        print(
            "catalog requirement status: "
            f"{query_plan.get('catalog_requirement_status') or '-'}"
        )
        print(
            "corporate requirement status: "
            f"{query_plan.get('corporate_requirement_status') or '-'}"
        )
        print(f"answer mode: {query_plan.get('answer_mode') or '-'}")
        print(
            "resolved requirements: "
            f"{query_plan.get('resolved_requirements') or '-'}"
        )
        print(
            "operational reason: "
            f"{query_plan.get('operational_decision_reason') or '-'}"
        )
        print(
            "validation reasons: "
            f"{_join(query_plan.get('query_plan_validation_reasons'))}"
        )
        print(
            "clarification raw/validated: "
            f"{query_plan.get('raw_clarification_required', False)}/"
            f"{query_plan.get('validated_clarification_required', False)}"
        )
        print(
            "clarification kind raw/validated: "
            f"{query_plan.get('raw_clarification_kind') or '-'}/"
            f"{query_plan.get('validated_clarification_kind') or '-'}"
        )
        print(
            "clarification span raw/validated: "
            f"{query_plan.get('raw_ambiguity_span') or '-'}/"
            f"{query_plan.get('validated_ambiguity_span') or '-'}"
        )
        print(
            "clarification missing slots: "
            f"{_join(query_plan.get('validated_missing_slots'))}"
        )
        print(
            "clarification action: "
            f"{query_plan.get('clarification_action') or '-'}"
        )
        print(
            "clarification validation reasons: "
            f"{_join(query_plan.get('clarification_validation_reasons'))}"
        )
        print(f"normalized question: {query_plan.get('normalized_question') or '-'}")
        print(f"expansions: {_join(query_plan.get('query_expansions'))}")
        print(f"preferred sources: {_join(query_plan.get('preferred_sources'))}")
        print(f"fallback: {query_plan.get('fallback_used', False)}")
        retrieval = result.retrieval or {}
        print(f"retrieval stages: {_join(retrieval.get('retrieval_stages'))}")
        print(
            "retrieval candidates before/after dedupe: "
            f"{retrieval.get('candidate_count_before_dedupe', '-')}/"
            f"{retrieval.get('candidate_count_after_dedupe', '-')}"
        )
        print(f"retrieval duplicates: {retrieval.get('duplicate_count', '-')}")
        print(f"retrieval duration ms: {retrieval.get('duration_ms', '-')}")
        context = result.context or {}
        context_plan = context.get("context_plan") or {}
        context_size = context.get("context_size") or {}
        print(f"context answer shape: {context_plan.get('answer_shape', '-')}")
        print(
            "context coverage: "
            f"{context.get('coverage_satisfied') or '-'}"
        )
        print(
            "context size chunks/chars: "
            f"{context_size.get('chunks', '-')}/"
            f"{context_size.get('estimated_chars', '-')}"
        )
        evidence = result.evidence or {}
        print(f"evidence answer mode: {evidence.get('answer_mode', '-')}")
        print(
            "evidence supporting chunks: "
            f"{_join(evidence.get('supporting_chunk_ids'))}"
        )
        print(
            "evidence non-supporting chunks: "
            f"{_join(evidence.get('non_supporting_chunk_ids'))}"
        )
        print(
            "evidence supported requirements: "
            f"{_join(evidence.get('supported_requirements'))}"
        )
        print(
            "evidence unsupported requirements: "
            f"{_join(evidence.get('unsupported_requirements'))}"
        )
        print(
            "evidence decision reasons: "
            f"{_join(evidence.get('decision_reasons'))}"
        )
        answer_contract = result.answer_contract or {}
        contract_validation = result.contract_validation or {}
        print(
            "answer contract mode: "
            f"{answer_contract.get('answer_mode', '-')}"
        )
        print(
            "answer contract allowed chunks: "
            f"{_join(answer_contract.get('allowed_chunk_ids'))}"
        )
        print(
            "answer contract supported requirements: "
            f"{_join(answer_contract.get('supported_requirement_ids'))}"
        )
        print(
            "answer contract unsupported requirements: "
            f"{_join(answer_contract.get('unsupported_requirement_ids'))}"
        )
        print(
            "contract valid/fallback: "
            f"{contract_validation.get('valid', '-')}/"
            f"{contract_validation.get('fallback_applied', '-')}"
        )
        print(
            "contract violations: "
            f"{_join(contract_validation.get('violations'))}"
        )
        print(
            "final answer sections: "
            f"{_join(result.final_answer_sections)}"
        )
        print(f"found chunks: {result.chunks_used}")
        print(f"diagnostic chunks: {len(result.diagnostic_candidates)}")
        print(f"elapsed seconds: {result.elapsed_seconds}")
        print(f"interaction id: {result.interaction_id or '-'}")
        print(f"analytics logged: {result.analytics_logged}")
        print(f"analytics error: {result.analytics_error or '-'}")


def _command_index(args: argparse.Namespace, config: LineHelperConfig) -> int:
    if args.index_command != "organization":
        print("Укажите команду индексации: linehelper index organization", file=sys.stderr)
        return 2
    from scripts import index_company_structure

    argv = []
    if args.dry_run:
        argv.append("--dry-run")
    if args.replace_version:
        argv.append("--replace-version")
    if args.verbose:
        argv.append("--verbose")
    argv.extend(["--db-path", str(config.db_path)])
    return int(index_company_structure.main(argv) or 0)


def _command_test(args: argparse.Namespace, config: LineHelperConfig) -> int:
    if args.test_command == "organization":
        from scripts import run_organization_test_pack

        return int(run_organization_test_pack.main(_organization_test_argv(args, config)) or 0)
    if args.test_command == "runtime":
        from scripts import run_runtime_query_analyzer_test_pack

        return int(run_runtime_query_analyzer_test_pack.main(_runtime_test_argv(args)) or 0)
    print("Укажите тестовый пакет: linehelper test organization или linehelper test runtime", file=sys.stderr)
    return 2


def _command_version(config: LineHelperConfig) -> int:
    print(f"LineHelper: {_package_version()}")
    print(f"Git commit: {_git_output(config.project_root, 'rev-parse', '--short', 'HEAD') or '-'}")
    dirty = bool(_git_output(config.project_root, "status", "--short"))
    print(f"Worktree: {'dirty' if dirty else 'clean'}")
    print(f"Python: {sys.version.split()[0]}")
    return 0


def _organization_test_argv(args: argparse.Namespace, config: LineHelperConfig) -> list[str]:
    argv = ["--db-path", str(config.db_path)]
    _append_optional(argv, "--limit", args.limit)
    _append_optional(argv, "--group", args.group)
    _append_optional(argv, "--question-id", args.question_id)
    _append_optional(argv, "--model", args.model)
    _append_optional(argv, "--concurrency", args.concurrency)
    if args.resume:
        argv.append("--resume")
    if args.retrieval_only:
        argv.append("--retrieval-only")
    if args.verbose:
        argv.append("--verbose")
    return argv


def _runtime_test_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    _append_optional(argv, "--pack", args.pack)
    _append_optional(argv, "--out-dir", args.out_dir)
    _append_optional(argv, "--limit", args.limit)
    _append_optional(argv, "--group", args.group)
    _append_optional(argv, "--sleep", args.sleep)
    if args.resume:
        argv.append("--resume")
    if args.debug:
        argv.append("--debug")
    return argv


def _append_optional(argv: list[str], option: str, value: object | None) -> None:
    if value is not None:
        argv.extend([option, str(value)])


def _package_version() -> str:
    try:
        return metadata.version("linehelper")
    except metadata.PackageNotFoundError:
        return __version__


def _git_output(cwd: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ""
    return completed.stdout.strip()


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _join(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "-"
    return str(value or "-")


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
