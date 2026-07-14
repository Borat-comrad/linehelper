"""Read-only diagnostics and status helpers for LineHelper."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from linehelper.config import LineHelperConfig
from linehelper.organization.models import KNOWLEDGE_DOMAIN, SOURCE_VERSION


ORGANIZATION_SOURCE_FILE = "bvr_company_structure_instruction_v2 (2).txt"


@dataclass(frozen=True)
class CheckResult:
    level: str
    message: str
    critical: bool = False


@dataclass(frozen=True)
class OllamaStatus:
    available: bool
    model_available: bool
    models: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class DatabaseStats:
    db_exists: bool
    schema_valid: bool
    semantic_chunks: int
    episodic_chunks: int
    organization_chunks: int
    organization_source_version: str | None
    duplicate_organization_record_keys: int
    error: str | None = None


def run_doctor(config: LineHelperConfig) -> tuple[list[CheckResult], int]:
    """Run read-only diagnostics. Return checks and process exit code."""
    checks: list[CheckResult] = []
    checks.append(_ok(f"Python {sys.version.split()[0]}"))
    checks.append(_ok(f"Корень проекта найден: {config.project_root}") if config.project_root.exists() else _fail("Корень проекта не найден"))
    checks.append(_import_check("linehelper", "Пакет linehelper импортируется", critical=True))
    checks.append(_import_check("streamlit", "Streamlit установлен", critical=True))

    ollama = get_ollama_status(config, timeout_seconds=5)
    checks.append(_ok("Ollama доступна") if ollama.available else _fail(f"Ollama недоступна: {ollama.error or 'нет ответа'}"))
    checks.append(
        _ok(f"{config.model} установлена")
        if ollama.model_available
        else _fail(f"Модель {config.model} не найдена в Ollama")
    )

    stats = get_database_stats(config.db_path)
    checks.append(_ok(f"SQLite-база существует: {config.db_path}") if stats.db_exists else _fail(f"SQLite-база не найдена: {config.db_path}"))
    checks.append(_ok("Схема базы валидна") if stats.schema_valid else _fail(f"Схема базы невалидна: {stats.error or 'missing tables'}"))
    checks.append(_ok(f"Semantic chunks: {stats.semantic_chunks}") if stats.semantic_chunks > 0 else _fail("Semantic chunks не найдены"))
    checks.append(
        _ok("Organization chunks: 192")
        if stats.organization_chunks == 192
        else _fail(f"Organization chunks: {stats.organization_chunks}, ожидалось 192")
    )
    checks.append(
        _ok("Дубликатов organization record_key нет")
        if stats.duplicate_organization_record_keys == 0
        else _fail(f"Дубликаты organization record_key: {stats.duplicate_organization_record_keys}")
    )
    checks.append(_query_analyzer_check())
    checks.append(
        _ok(f"Исходный TXT оргструктуры найден: {config.organization_source_path}")
        if config.organization_source_path.exists()
        else _fail(f"Исходный TXT оргструктуры не найден: {config.organization_source_path}")
    )
    checks.append(
        _ok("Тестовые пакеты существуют")
        if _test_packs_exist(config.project_root)
        else _fail("Один или несколько тестовых пакетов не найдены")
    )
    checks.append(_runtime_dir_writable_check(config.runtime_dir))

    exit_code = 1 if any(check.critical for check in checks if check.level == "FAIL") else 0
    return checks, exit_code


def get_ollama_status(config: LineHelperConfig, *, timeout_seconds: float = 3.0) -> OllamaStatus:
    """Check Ollama /api/tags without changing local data."""
    url = f"{config.ollama_url}/api/tags"
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - configured local Ollama endpoint.
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return OllamaStatus(False, False, (), f"HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return OllamaStatus(False, False, (), f"{type(exc).__name__}: {exc}")

    models = data.get("models") if isinstance(data, dict) else []
    names = tuple(
        str(item.get("name") or "")
        for item in models
        if isinstance(item, dict) and item.get("name")
    )
    return OllamaStatus(
        available=True,
        model_available=config.model in names,
        models=names,
    )


def get_database_stats(db_path: Path) -> DatabaseStats:
    """Read chunk counters and organization metadata from SQLite."""
    if not db_path.exists():
        return DatabaseStats(False, False, 0, 0, 0, None, 0, "database file does not exist")

    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            schema_valid = _schema_valid(connection)
            if not schema_valid:
                return DatabaseStats(True, False, 0, 0, 0, None, 0, "memory_chunks table is missing required columns")
            semantic = _count_namespace(connection, "semantic")
            episodic = _count_namespace(connection, "episodic")
            organization_rows = connection.execute(
                """
                SELECT metadata_json
                FROM memory_chunks
                WHERE namespace = 'semantic'
                """
            ).fetchall()
    except sqlite3.Error as exc:
        return DatabaseStats(True, False, 0, 0, 0, None, 0, f"SQLite error: {exc}")

    organization_count = 0
    source_versions: Counter[str] = Counter()
    record_keys: Counter[str] = Counter()
    for row in organization_rows:
        metadata = _metadata(row["metadata_json"])
        if (
            metadata.get("knowledge_domain") == KNOWLEDGE_DOMAIN
            and metadata.get("source_file") == ORGANIZATION_SOURCE_FILE
        ):
            organization_count += 1
            version = metadata.get("source_version")
            record_key = metadata.get("record_key")
            if isinstance(version, str):
                source_versions[version] += 1
            if isinstance(record_key, str):
                record_keys[record_key] += 1

    duplicate_count = sum(1 for count in record_keys.values() if count > 1)
    source_version = source_versions.most_common(1)[0][0] if source_versions else None
    return DatabaseStats(
        True,
        True,
        semantic,
        episodic,
        organization_count,
        source_version,
        duplicate_count,
    )


def query_analyzer_state() -> tuple[bool, str]:
    """Check whether Query Analyzer can be constructed."""
    try:
        from linehelper.rag.query_analyzer import QueryAnalyzer

        analyzer = QueryAnalyzer()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    model = getattr(analyzer, "model", None) or "unknown"
    return True, f"enabled by default, model={model}"


def _schema_valid(connection: sqlite3.Connection) -> bool:
    rows = connection.execute("PRAGMA table_info(memory_chunks)").fetchall()
    columns = {str(row["name"]) for row in rows}
    required = {
        "id",
        "namespace",
        "doc_type",
        "title",
        "text",
        "source",
        "section",
        "metadata_json",
    }
    return required.issubset(columns)


def _count_namespace(connection: sqlite3.Connection, namespace: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM memory_chunks WHERE namespace = ?",
        (namespace,),
    ).fetchone()
    return int(row["count"] or 0)


def _metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _import_check(module_name: str, ok_message: str, *, critical: bool = False) -> CheckResult:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        return _fail(f"{ok_message}: ошибка импорта {type(exc).__name__}: {exc}", critical=critical)
    return _ok(ok_message)


def _query_analyzer_check() -> CheckResult:
    ok, detail = query_analyzer_state()
    return _ok(f"Query Analyzer создаётся: {detail}") if ok else _fail(f"Query Analyzer не создаётся: {detail}")


def _test_packs_exist(project_root: Path) -> bool:
    required = (
        project_root / "docs" / "test_packs" / "linehelper_organization_test_scenarios_360.jsonl",
        project_root / "docs" / "test_packs" / "linehelper_runtime_query_analyzer_test_pack_360.csv",
    )
    return all(path.exists() for path in required)


def _runtime_dir_writable_check(runtime_dir: Path) -> CheckResult:
    if runtime_dir.exists():
        return (
            _ok(f"Каталог data/runtime доступен для записи: {runtime_dir}")
            if os.access(runtime_dir, os.W_OK)
            else _fail(f"Каталог data/runtime недоступен для записи: {runtime_dir}")
        )
    parent = runtime_dir.parent
    if parent.exists() and os.access(parent, os.W_OK):
        return _ok(f"Каталог data/runtime может быть создан: {runtime_dir}")
    return _fail(f"Каталог data/runtime не существует, родительский каталог недоступен для записи: {runtime_dir}")


def _ok(message: str) -> CheckResult:
    return CheckResult("OK", message)


def _fail(message: str, *, critical: bool = True) -> CheckResult:
    return CheckResult("FAIL", message, critical=critical)


def _warn(message: str) -> CheckResult:
    return CheckResult("WARN", message)
