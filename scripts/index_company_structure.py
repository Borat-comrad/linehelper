"""CLI for importing Serviceline organization structure into semantic memory."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.organization.indexer import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_SOURCE_PATH,
    import_company_structure,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import company organization structure into LineHelper semantic memory."
    )
    parser.add_argument(
        "source_file",
        nargs="?",
        type=Path,
        default=DEFAULT_SOURCE_PATH,
        help="TXT source file. Defaults to data/raw_docs/bvr_company_structure_instruction_v2 (2).txt.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and build chunks without writing to DB.")
    parser.add_argument(
        "--replace-version",
        action="store_true",
        help="Replace existing chunks for this source file and source version before import.",
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="SQLite memory DB path.")
    parser.add_argument("--verbose", action="store_true", help="Print parser warnings and chunk examples.")
    parser.add_argument(
        "--exclude-contacts",
        action="store_true",
        help="Build anonymized chunks without phone/email values.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_path = _project_path(args.source_file)
    db_path = _project_path(args.db_path)
    report = import_company_structure(
        source_path=source_path,
        db_path=db_path,
        replace_version=True if not args.dry_run else args.replace_version,
        dry_run=args.dry_run,
        exclude_contacts=args.exclude_contacts,
    )

    print(f"Source file: {_display_path(source_path)}")
    print(f"Source version: {report.source_version}")
    print(f"Organization units indexed: {report.units_indexed}")
    print(f"Employees indexed: {report.employees_indexed}")
    print(f"Responsibility routes indexed: {report.routes_indexed}")
    print(f"Vacancies indexed: {report.vacancies_indexed}")
    print(f"Inactive entities indexed: {report.inactive_entities_indexed}")
    print(f"Role combinations indexed: {report.role_combinations_indexed}")
    print(f"Phones recognized: {report.phones_recognized}")
    print(f"Emails recognized: {report.emails_recognized}")
    print(f"Total semantic chunks written: {report.total_chunks_written}")
    print(f"Warnings: {len(report.warnings)}")

    if args.dry_run:
        print("\nDry-run chunks by type:")
        for doc_type, count in sorted(report.chunks_by_type.items()):
            print(f"  {doc_type}: {count}")
        print("\nDry-run examples:")
        for chunk in report.examples[:3]:
            print(f"  [{chunk.doc_type}] {chunk.title}")
            print(_redact(chunk.text)[:700].strip())
            print()

    if args.verbose and report.warnings:
        print("Parser warnings:")
        for warning in report.warnings:
            print(f"  WARNING: {_redact(warning)}")

    return 0


def _project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _redact(text: str) -> str:
    text = re.sub(r"(?:\+7|8)[\s()/-]*\d{3}[\s()/-]*\d{3}[\s()/-]*\d{2}[\s()/-]*\d{2}", "[phone]", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"\S+@\S+", "[email]", text)
    return text


if __name__ == "__main__":
    raise SystemExit(main())
