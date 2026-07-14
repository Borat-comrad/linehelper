"""Command line interface for LineHelper."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Sequence

from linehelper import __version__
from linehelper.config import LineHelperConfig, LineHelperConfigError, load_config
from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator
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

    chat = subparsers.add_parser("chat", help="Задать один вопрос через основной RAG pipeline.")
    chat.add_argument("--debug", action="store_true", help="Показать диагностику Query Analyzer и retrieval.")
    chat.add_argument("question", help="Вопрос для LineHelper.")

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
    try:
        generator = RagAnswerGenerator(db_path=config.db_path)
        result = generator.answer(args.question)
    except (ValueError, RagAnswerError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Не удалось выполнить вопрос: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Вопрос: {result.question}")
    print()
    print(f"Ответ: {result.answer}")
    print()
    print("Источники:")
    if result.sources:
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
    else:
        print("-")

    if args.debug:
        query_plan = result.query_plan or {}
        print()
        print("Debug:")
        print(f"intent: {query_plan.get('intent') or '-'}")
        print(f"normalized question: {query_plan.get('normalized_question') or '-'}")
        print(f"expansions: {_join(query_plan.get('query_expansions'))}")
        print(f"preferred sources: {_join(query_plan.get('preferred_sources'))}")
        print(f"fallback: {query_plan.get('fallback_used', False)}")
        print(f"found chunks: {result.chunks_used}")
        print(f"diagnostic chunks: {len(result.diagnostic_candidates)}")
        print(f"elapsed seconds: {result.elapsed_seconds}")
    return 0


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
