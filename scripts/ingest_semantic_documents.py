"""Загрузка локальных TXT/MD-документов в semantic memory LineHelper."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "semantic_raw"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
SUPPORTED_SUFFIXES = frozenset({".txt", ".md"})

sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.memory_store import MemoryStore  # noqa: E402


@dataclass(frozen=True)
class SemanticDocument:
    """Документ, готовый к загрузке в semantic memory."""

    path: Path
    title: str
    doc_type: str
    text: str


def read_text_file(path: Path) -> str:
    """Читает текст с учетом частых кодировок локальных Windows-документов."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    return path.read_text(encoding="utf-8", errors="replace")


def iter_semantic_documents(raw_dir: Path) -> list[SemanticDocument]:
    """Возвращает поддерживаемые документы из папки исходных semantic-файлов."""
    if not raw_dir.exists():
        return []

    documents: list[SemanticDocument] = []

    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            print(f"Skip unsupported file type: {path}")
            continue

        text = read_text_file(path).strip()
        if not text:
            print(f"Skip empty document: {path}")
            continue

        documents.append(
            SemanticDocument(
                path=path,
                title=path.stem,
                doc_type=suffix.lstrip("."),
                text=text,
            )
        )

    return documents


def chunk_text(text: str, chunk_size: int = 1200, chunk_overlap: int = 150) -> list[str]:
    """Делит текст на простые пересекающиеся чанки без внешних библиотек."""
    clean_text = " ".join(text.split())
    if not clean_text:
        return []

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")

    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must not be negative")

    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[str] = []
    start = 0

    while start < len(clean_text):
        end = min(start + chunk_size, len(clean_text))
        chunks.append(clean_text[start:end].strip())

        if end == len(clean_text):
            break

        start = end - chunk_overlap

    return chunks


def ingest_directory(
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
) -> int:
    """Загружает поддерживаемые документы из raw_dir в namespace='semantic'."""
    documents = iter_semantic_documents(raw_dir)

    if not documents:
        print(f"No .txt or .md documents found in {raw_dir}")
        return 0

    store = MemoryStore(str(db_path))
    store.ensure_schema()

    added_chunks = 0

    for document in documents:
        chunks = chunk_text(
            document.text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        relative_source = document.path.relative_to(raw_dir).as_posix()

        for index, chunk in enumerate(chunks, start=1):
            store.add_chunk(
                namespace="semantic",
                doc_type=document.doc_type,
                title=document.title,
                text=chunk,
                source=relative_source,
                section=f"chunk {index}",
                metadata={
                    "loader": "scripts/ingest_semantic_documents.py",
                    "file_name": document.path.name,
                    "relative_path": relative_source,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                },
            )
            added_chunks += 1

    print(f"Added semantic chunks: {added_chunks}")
    return added_chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load .txt/.md files from data/semantic_raw into semantic memory."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help="Folder with local semantic documents.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite memory database path.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1200,
        help="Maximum chunk size in characters.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=150,
        help="Overlap between adjacent chunks in characters.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ingest_directory(
        raw_dir=args.raw_dir,
        db_path=args.db_path,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )


if __name__ == "__main__":
    main()
