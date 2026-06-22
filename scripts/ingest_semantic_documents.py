"""Validate and import curated semantic chunks into LineHelper memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURATED_PATH = PROJECT_ROOT / "data" / "semantic_index" / "curated_chunks.jsonl"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
SEMANTIC_INDEX_DIR = PROJECT_ROOT / "data" / "semantic_index"
RAW_DOCS_DIR = PROJECT_ROOT / "data" / "raw_docs"
NAMESPACE = "semantic"
CHUNKING_STRATEGY = "llm_curated"
LOADER_VERSION = "curated_semantic_v1"

REQUIRED_FIELDS = {
    "namespace",
    "doc_type",
    "title",
    "source_file",
    "relative_path",
    "section",
    "section_path",
    "logical_unit_type",
    "logical_unit_title",
    "text",
    "page_start",
    "page_end",
    "part_index",
    "part_count",
    "tags",
    "notes",
}

ALLOWED_DOC_TYPES = {
    "company_goals",
    "company_ckp",
    "zrs_policy",
    "orders_policy",
    "document_flow_policy",
    "onboarding_instruction",
    "org_structure",
    "one_c_instruction",
    "contract_instruction",
    "hr_instruction",
    "planning_policy",
    "coordination_policy",
    "communication_policy",
    "sales_process",
    "reference",
    "unknown",
}

ALLOWED_LOGICAL_UNIT_TYPES = {
    "definition",
    "policy_rule",
    "procedure",
    "checklist",
    "example",
    "role_responsibility",
    "org_unit",
    "company_goal",
    "ckp_statement",
    "historical_context",
    "reference_block",
    "mixed",
}

RESET_FILES = (
    DEFAULT_DB_PATH,
    SEMANTIC_INDEX_DIR / "chunk_preview.jsonl",
    SEMANTIC_INDEX_DIR / "curated_chunks.jsonl",
    SEMANTIC_INDEX_DIR / "curated_chunks_preview.jsonl",
    SEMANTIC_INDEX_DIR / "curation_manifest.json",
    SEMANTIC_INDEX_DIR / "import_report.json",
)
RESET_PATTERNS = (
    PROJECT_ROOT / "data" / "memory" / "*.sqlite",
    PROJECT_ROOT / "data" / "memory" / "*.sqlite3",
)
RESET_DIRS = (SEMANTIC_INDEX_DIR / "pytest-tmp",)

sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover - older Python
    pass

from linehelper.memory.memory_store import MemoryStore  # noqa: E402


@dataclass
class ValidationResult:
    chunks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def normalize_for_hash(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def compute_content_hash(*, text: str, source_file: str, namespace: str) -> str:
    payload = "\n".join(
        (
            namespace.strip(),
            source_file.strip(),
            normalize_for_hash(text),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def load_jsonl(path: Path, result: ValidationResult) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        result.errors.append(f"Curated file does not exist: {display_path(path)}")
        return []

    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                result.errors.append(f"Line {line_number}: invalid JSON: {exc}")
                continue
            if not isinstance(value, dict):
                result.errors.append(f"Line {line_number}: JSON value must be an object.")
                continue
            rows.append((line_number, value))

    if not rows:
        result.errors.append(f"Curated file has no chunks: {display_path(path)}")
    return rows


def validate_int_field(
    chunk: dict[str, Any],
    field_name: str,
    line_number: int,
    result: ValidationResult,
    *,
    allow_none: bool = False,
) -> int | None:
    value = chunk.get(field_name)
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        result.errors.append(f"Line {line_number}: {field_name} must be an integer.")
        return None
    return value


def validate_chunk(
    chunk: dict[str, Any],
    line_number: int,
    result: ValidationResult,
    raw_docs_dir: Path = RAW_DOCS_DIR,
) -> dict[str, Any] | None:
    missing = sorted(REQUIRED_FIELDS - chunk.keys())
    if missing:
        result.errors.append(f"Line {line_number}: missing fields: {', '.join(missing)}")
        return None

    namespace = chunk.get("namespace")
    if namespace != NAMESPACE:
        result.errors.append(f"Line {line_number}: namespace must be {NAMESPACE!r}, got {namespace!r}.")

    doc_type = chunk.get("doc_type")
    if doc_type not in ALLOWED_DOC_TYPES:
        result.errors.append(f"Line {line_number}: unsupported doc_type: {doc_type!r}.")

    logical_unit_type = chunk.get("logical_unit_type")
    if logical_unit_type not in ALLOWED_LOGICAL_UNIT_TYPES:
        result.errors.append(
            f"Line {line_number}: unsupported logical_unit_type: {logical_unit_type!r}."
        )

    text = chunk.get("text")
    if not isinstance(text, str) or not text.strip():
        result.errors.append(f"Line {line_number}: text must be a non-empty string.")

    for field_name in (
        "title",
        "source_file",
        "relative_path",
        "section",
        "section_path",
        "logical_unit_title",
    ):
        if not isinstance(chunk.get(field_name), str) or not chunk[field_name].strip():
            result.errors.append(f"Line {line_number}: {field_name} must be a non-empty string.")

    tags = chunk.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        result.errors.append(f"Line {line_number}: tags must be a list of strings.")

    notes = chunk.get("notes")
    if notes is not None and not isinstance(notes, str):
        result.errors.append(f"Line {line_number}: notes must be a string.")

    part_index = validate_int_field(chunk, "part_index", line_number, result)
    part_count = validate_int_field(chunk, "part_count", line_number, result)
    page_start = validate_int_field(chunk, "page_start", line_number, result, allow_none=True)
    page_end = validate_int_field(chunk, "page_end", line_number, result, allow_none=True)

    if part_index is not None and part_index < 1:
        result.errors.append(f"Line {line_number}: part_index must be >= 1.")
    if part_count is not None and part_index is not None and part_count < part_index:
        result.errors.append(f"Line {line_number}: part_count must be >= part_index.")
    if page_start is not None and page_end is not None and page_end < page_start:
        result.errors.append(f"Line {line_number}: page_end must be >= page_start.")

    relative_path = chunk.get("relative_path")
    if isinstance(relative_path, str):
        source_path = project_path(relative_path)
        if not source_path.exists():
            result.errors.append(f"Line {line_number}: relative_path does not exist: {relative_path}")
        elif not is_under(source_path, raw_docs_dir):
            result.errors.append(f"Line {line_number}: relative_path must point inside data/raw_docs.")
        elif source_path.name != chunk.get("source_file"):
            result.errors.append(
                f"Line {line_number}: source_file does not match relative_path filename."
            )
        elif source_path.suffix.lower() == ".pdf" and (page_start is None or page_end is None):
            result.warnings.append(
                f"Line {line_number}: PDF chunk has no page_start/page_end: {chunk.get('source_file')}"
            )

    text_length = len(text.strip()) if isinstance(text, str) else 0
    if text_length and text_length < 80 and not str(chunk.get("notes") or "").strip():
        result.errors.append(
            f"Line {line_number}: text is very short ({text_length} chars) without notes."
        )
    if text_length > 3000 and not str(chunk.get("notes") or "").strip():
        result.errors.append(
            f"Line {line_number}: text is too long ({text_length} chars) without notes."
        )

    if result.errors and any(error.startswith(f"Line {line_number}:") for error in result.errors):
        return None

    normalized = dict(chunk)
    normalized["namespace"] = NAMESPACE
    normalized["text"] = text.strip()
    normalized["notes"] = str(chunk.get("notes") or "")
    normalized["content_hash"] = compute_content_hash(
        text=normalized["text"],
        source_file=str(normalized["source_file"]),
        namespace=NAMESPACE,
    )
    return normalized


def validate_curated_chunks(
    curated_path: Path = DEFAULT_CURATED_PATH,
    raw_docs_dir: Path = RAW_DOCS_DIR,
) -> ValidationResult:
    result = ValidationResult()
    rows = load_jsonl(curated_path, result)
    seen: set[tuple[str, str, str]] = set()
    part_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for line_number, chunk in rows:
        normalized = validate_chunk(chunk, line_number, result, raw_docs_dir=raw_docs_dir)
        if normalized is None:
            continue

        key = (
            normalized["namespace"],
            normalized["source_file"],
            normalized["content_hash"],
        )
        if key in seen:
            result.errors.append(
                f"Line {line_number}: duplicate chunk by namespace/source_file/content_hash."
            )
            continue
        seen.add(key)

        if int(normalized["part_count"]) > 1:
            group_key = (
                normalized["source_file"],
                normalized["section_path"],
                normalized["logical_unit_title"],
            )
            part_groups[group_key].append(normalized)

        result.chunks.append(normalized)

    for group_key, parts in part_groups.items():
        expected_count = int(parts[0]["part_count"])
        indexes = sorted(int(part["part_index"]) for part in parts)
        if any(int(part["part_count"]) != expected_count for part in parts):
            result.errors.append(
                f"Multipart unit has inconsistent part_count: {group_key[0]} | {group_key[2]}"
            )
        if indexes != list(range(1, expected_count + 1)):
            result.errors.append(
                f"Multipart unit has missing/duplicate part_index values: {group_key[0]} | {group_key[2]}"
            )

    return result


def existing_content_hashes(db_path: Path) -> set[tuple[str, str, str]]:
    if not db_path.exists():
        return set()

    hashes: set[tuple[str, str, str]] = set()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT namespace, metadata_json
            FROM memory_chunks
            WHERE namespace = ?
            """,
            (NAMESPACE,),
        ).fetchall()

    for row in rows:
        if not row["metadata_json"]:
            continue
        try:
            metadata = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            continue
        source_file = metadata.get("source_file")
        hash_value = metadata.get("content_hash")
        if source_file and hash_value:
            hashes.add((row["namespace"], str(source_file), str(hash_value)))

    return hashes


def metadata_for_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "namespace": NAMESPACE,
        "doc_type": chunk["doc_type"],
        "source_file": chunk["source_file"],
        "relative_path": chunk["relative_path"],
        "title": chunk["title"],
        "section": chunk["section"],
        "section_path": chunk["section_path"],
        "page_start": chunk["page_start"],
        "page_end": chunk["page_end"],
        "logical_unit_type": chunk["logical_unit_type"],
        "logical_unit_title": chunk["logical_unit_title"],
        "part_index": chunk["part_index"],
        "part_count": chunk["part_count"],
        "tags": chunk["tags"],
        "notes": chunk["notes"],
        "chunking_strategy": CHUNKING_STRATEGY,
        "loader_version": LOADER_VERSION,
        "content_hash": chunk["content_hash"],
    }


def import_curated_chunks(
    *,
    curated_path: Path = DEFAULT_CURATED_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    raw_docs_dir: Path = RAW_DOCS_DIR,
) -> dict[str, Any]:
    validation = validate_curated_chunks(curated_path, raw_docs_dir=raw_docs_dir)
    if not validation.ok:
        print_validation(validation)
        raise SystemExit("Curated validation failed; import aborted.")

    store = MemoryStore(str(db_path))
    store.ensure_schema()
    existing = existing_content_hashes(db_path)
    added = 0
    duplicates = 0
    errors: list[str] = []

    for chunk in validation.chunks:
        key = (NAMESPACE, chunk["source_file"], chunk["content_hash"])
        if key in existing:
            duplicates += 1
            continue

        try:
            store.add_chunk(
                namespace=NAMESPACE,
                doc_type=str(chunk["doc_type"]),
                title=str(chunk["title"]),
                text=str(chunk["text"]),
                source=str(chunk["relative_path"]),
                page=chunk["page_start"],
                section=str(chunk["section"]),
                metadata=metadata_for_chunk(chunk),
            )
        except Exception as exc:  # pragma: no cover - defensive reporting
            errors.append(f"{chunk['source_file']} | {chunk['section']}: {exc}")
            continue

        added += 1
        existing.add(key)

    semantic_count = count_semantic_chunks(db_path)
    db_size = db_path.stat().st_size if db_path.exists() else 0
    report = {
        "curated_path": display_path(curated_path),
        "db_path": display_path(db_path),
        "curated_chunks": len(validation.chunks),
        "chunks_added": added,
        "duplicates_skipped": duplicates,
        "errors": errors,
        "db_size_bytes": db_size,
        "semantic_chunks_in_db": semantic_count,
    }
    write_import_report(report)
    print_import_report(report)
    if errors:
        raise SystemExit("Import finished with errors.")
    return report


def count_semantic_chunks(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_chunks WHERE namespace = ?",
                (NAMESPACE,),
            ).fetchone()[0]
        )


def write_import_report(report: dict[str, Any]) -> None:
    path = SEMANTIC_INDEX_DIR / "import_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def print_validation(result: ValidationResult) -> None:
    print("Curated validation:")
    print(f"  chunks: {len(result.chunks)}")
    print(f"  errors: {len(result.errors)}")
    print(f"  warnings: {len(result.warnings)}")
    for error in result.errors:
        print(f"  ERROR: {error}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")


def print_dry_run(result: ValidationResult) -> None:
    by_source = Counter(chunk["source_file"] for chunk in result.chunks)
    by_doc_type = Counter(chunk["doc_type"] for chunk in result.chunks)
    by_unit_type = Counter(chunk["logical_unit_type"] for chunk in result.chunks)

    print_validation(result)
    if not result.ok:
        raise SystemExit("Curated validation failed; dry-run aborted.")

    print("\nDry-run summary:")
    print(f"  curated_chunks: {len(result.chunks)}")
    print("  chunks_by_source:")
    for source_file, count in sorted(by_source.items()):
        print(f"    - {source_file}: {count}")
    print("  doc_types:")
    for doc_type, count in sorted(by_doc_type.items()):
        print(f"    - {doc_type}: {count}")
    print("  logical_unit_types:")
    for unit_type, count in sorted(by_unit_type.items()):
        print(f"    - {unit_type}: {count}")


def print_import_report(report: dict[str, Any]) -> None:
    print("Curated import:")
    print(f"  curated_chunks: {report['curated_chunks']}")
    print(f"  chunks_added: {report['chunks_added']}")
    print(f"  duplicates_skipped: {report['duplicates_skipped']}")
    print(f"  errors: {len(report['errors'])}")
    print(f"  db_path: {report['db_path']}")
    print(f"  db_size_bytes: {report['db_size_bytes']}")
    print(f"  semantic_chunks_in_db: {report['semantic_chunks_in_db']}")
    for error in report["errors"]:
        print(f"  ERROR: {error}")


def reset_state() -> list[Path]:
    deleted: list[Path] = []
    for path in RESET_FILES:
        if path.name == ".gitkeep":
            continue
        if path.exists() and path.is_file():
            path.unlink()
            deleted.append(path)

    for pattern in RESET_PATTERNS:
        for path in pattern.parent.glob(pattern.name):
            if path.name == ".gitkeep":
                continue
            if path.is_file():
                path.unlink()
                deleted.append(path)

    for path in RESET_DIRS:
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            deleted.append(path)

    print("Reset state:")
    if deleted:
        for path in deleted:
            print(f"  deleted: {display_path(path)}")
    else:
        print("  no active local artifacts found")

    protected = (
        RAW_DOCS_DIR,
        RAW_DOCS_DIR / ".gitkeep",
        SEMANTIC_INDEX_DIR / ".gitkeep",
        PROJECT_ROOT / "data" / "memory" / ".gitkeep",
    )
    for path in protected:
        if path.exists():
            print(f"  kept: {display_path(path)}")
    return deleted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate, dry-run, reset, or import Codex-curated semantic chunks "
            "from data/semantic_index/curated_chunks.jsonl."
        )
    )
    parser.add_argument(
        "--curated-path",
        type=Path,
        default=DEFAULT_CURATED_PATH,
        help="Path to curated JSONL chunks.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite memory database path.",
    )
    parser.add_argument(
        "--validate-curated",
        action="store_true",
        help="Validate curated JSONL and exit with non-zero status on errors.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print curated import summary without writing to the database.",
    )
    parser.add_argument(
        "--import-curated",
        action="store_true",
        help="Validate and import curated chunks into MemoryStore.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete active local DB/preview/temp artifacts without touching raw docs or .gitkeep.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    actions = [
        args.validate_curated,
        args.dry_run,
        args.import_curated,
        args.reset_state,
    ]
    if sum(bool(action) for action in actions) != 1:
        raise SystemExit(
            "Choose exactly one action: --validate-curated, --dry-run, "
            "--import-curated, or --reset-state."
        )

    curated_path = project_path(args.curated_path)
    db_path = project_path(args.db_path)

    if args.reset_state:
        reset_state()
        return

    result = validate_curated_chunks(curated_path)
    if args.validate_curated:
        print_validation(result)
        if not result.ok:
            raise SystemExit(1)
        return

    if args.dry_run:
        print_dry_run(result)
        return

    import_curated_chunks(curated_path=curated_path, db_path=db_path)


if __name__ == "__main__":
    main()
