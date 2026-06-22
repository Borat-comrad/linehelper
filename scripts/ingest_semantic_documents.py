"""Semantic ingest for local LineHelper knowledge documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw_docs"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
DEFAULT_PREVIEW_PATH = PROJECT_ROOT / "data" / "semantic_index" / "chunk_preview.jsonl"
SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".pdf"})
LOADER_VERSION = "semantic_ingest_v2"
NAMESPACE = "semantic"
HARD_MAX_CHARS = 2400
MIN_MERGE_CHARS = 300

sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover - for very old Python versions
    pass

from linehelper.memory.memory_store import MemoryStore  # noqa: E402


DOC_TYPES = {
    "company_goals",
    "company_ckp",
    "zrs_policy",
    "orders_policy",
    "document_flow_policy",
    "onboarding_instruction",
    "org_structure",
    "unknown",
}

LOGICAL_UNIT_TYPES = {
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


@dataclass(frozen=True)
class DocumentPage:
    number: int
    text: str


@dataclass(frozen=True)
class SemanticDocument:
    path: Path
    relative_path: str
    title: str
    suffix: str
    pages: list[DocumentPage]
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class SectionBlock:
    title: str
    section_path: list[str]
    text: str
    page_start: int | None
    page_end: int | None


@dataclass
class LogicalUnit:
    logical_unit_id: str
    logical_unit_type: str
    title: str
    text: str
    section: str
    section_path: str
    parent_section: str
    page_start: int | None
    page_end: int | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentProfile:
    source_file: str
    relative_path: str
    doc_type: str
    title: str
    page_count: int
    detected_headings: list[str]
    detected_sections: list[str]
    logical_units: list[dict[str, Any]]
    structure_quality: str
    warnings: list[str]


@dataclass
class PreparedChunk:
    text: str
    metadata: dict[str, Any]


def read_text_file(path: Path) -> str:
    """Read local text with encodings common for Windows-authored files."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    return path.read_text(encoding="utf-8", errors="replace")


def read_pdf_pages(path: Path) -> tuple[list[DocumentPage], list[str]]:
    """Extract PDF text page by page without OCR."""
    warnings: list[str] = []

    try:
        from pypdf import PdfReader
    except ImportError:
        return [], [
            "pypdf is not installed; PDF text extraction is unavailable. "
            "Install dependencies from requirements.txt."
        ]

    pages: list[DocumentPage] = []

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # pragma: no cover - depends on broken PDFs
        return [], [f"Failed to open PDF: {exc}"]

    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - depends on broken PDFs
            text = ""
            warnings.append(f"Page {index}: failed to extract text: {exc}")

        if not text.strip():
            warnings.append(f"Page {index}: no extractable text")

        pages.append(DocumentPage(number=index, text=normalize_newlines(text)))

    return pages, warnings


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def make_relative_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def iter_semantic_documents(raw_dir: Path, verbose: bool = False) -> list[SemanticDocument]:
    """Return supported semantic documents from raw_dir."""
    if not raw_dir.exists():
        return []

    documents: list[SemanticDocument] = []

    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            if verbose:
                print(f"Skip unsupported file type: {path.name}")
            continue

        warnings: list[str] = []
        if suffix == ".pdf":
            pages, warnings = read_pdf_pages(path)
        else:
            text = read_text_file(path)
            pages = [DocumentPage(number=1, text=normalize_newlines(text))]

        if not any(page.text.strip() for page in pages):
            if verbose:
                print(f"Skip empty document: {path.name}")
            continue

        documents.append(
            SemanticDocument(
                path=path,
                relative_path=make_relative_path(path),
                title=path.stem,
                suffix=suffix,
                pages=pages,
                warnings=warnings,
            )
        )

    return documents


def compact_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_for_hash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


def first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip(" #\t")
        if clean:
            return clean[:180]
    return None


def first_meaningful_line(text: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip(" #\t")
        if clean and not is_noise_content_line(clean):
            return clean[:180]
    return first_nonempty_line(text)


def detect_doc_type(document: SemanticDocument) -> str:
    name = document.path.stem.casefold()
    haystack = f"{document.path.name}\n{document.text[:8000]}".casefold()

    if "как начать работу в новой должности" in name:
        return "onboarding_instruction"
    if "ип-0002" in name or "цели и замыслы" in name:
        return "company_goals"
    if "ип-0003" in name or re.search(r"\bцкп\b", name):
        return "company_ckp"
    if "ип-0004" in name or re.search(r"\bструктура\s+зрс\b", name):
        return "zrs_policy"
    if "ип-0005" in name or "распоряжен" in name:
        return "orders_policy"
    if "ип-0006" in name or "документооборот" in name or "1с до" in name:
        return "document_flow_policy"
    if "оргсхем" in name or "орган труда" in name:
        return "org_structure"
    if "контраген" in name or "договор" in name or "счет" in name or "buh-bit" in name or "crm" in name:
        return "document_flow_policy"
    if "приказ" in name:
        return "orders_policy"
    if "координац" in name or "планирован" in name:
        return "orders_policy"

    checks: list[tuple[str, list[str]]] = [
        ("org_structure", ["оргсхем", "организационн", "структурн", "отделение", "секции"]),
        ("company_ckp", ["цкп serviceline", "цкп компании", "комплексная услуга", "ценный конечный продукт"]),
        ("company_goals", ["цели и замыслы", "замыслы компании", "идеальная картина", "рациональност", "экологичност"]),
        ("zrs_policy", ["структура зрс", "заявка руководителю", "зрс состоит", "ситуация данные решение"]),
        ("orders_policy", ["распоряжен", "письменная форма распоряжения", "контроль исполнения"]),
        ("document_flow_policy", ["документооборот", "официальная переписка", "внутренний документооборот"]),
        ("onboarding_instruction", ["как начать работу в новой должности", "новой должности", "вас игнорируют"]),
    ]

    scores: dict[str, int] = {doc_type: 0 for doc_type in DOC_TYPES}
    for doc_type, keywords in checks:
        scores[doc_type] += sum(1 for keyword in keywords if keyword in haystack)

    if scores["org_structure"] == 1 and "оргсхем" not in haystack:
        scores["org_structure"] = 0

    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] > 0 else "unknown"


def markdown_heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), compact_spaces(match.group(2))


def is_list_or_step_line(line: str) -> bool:
    clean = line.strip()
    return bool(
        re.match(r"^([-*•]\s+|\d+[.)]\s+|[а-яa-z]\)\s+)", clean, re.IGNORECASE)
        or re.match(r"^шаг\s*№?\s*\d+", clean, re.IGNORECASE)
    )


def uppercase_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if char.upper() == char and char.lower() != char) / len(letters)


def generic_heading(line: str, prev_blank: bool, next_blank: bool) -> str | None:
    clean = compact_spaces(line.strip(" -*•\t"))
    if not clean or len(clean) > 160:
        return None
    if is_noise_heading(clean):
        return None
    if clean[0].islower():
        return None
    if is_list_or_step_line(line) and not re.match(r"^шаг\s*№?\s*\d+", clean, re.IGNORECASE):
        return None
    if clean.endswith((".", ";", ",")) and not re.match(r"^\d+(\.\d+)*\.?\s+", clean):
        return None

    keyword_pattern = re.compile(
        r"\b(ПРИМЕР|ОБЯЗАННОСТИ|ЦКП|ЦЕЛЬ|ЗАМЫСЕЛ|ИСТОРИЯ|РАСПОРЯЖЕНИЯ|"
        r"ДОКУМЕНТООБОРОТ|ИНСТРУКЦИЯ|ПОРЯДОК|ПРАВИЛА|СТРУКТУРА|ОТДЕЛ|СЕКЦИЯ|ШАГ)\b",
        re.IGNORECASE,
    )

    numbered = bool(re.match(r"^\d+(\.\d+)*\.?\s+\S+", clean) and (prev_blank or next_blank))
    step = bool(re.match(r"^шаг\s*№?\s*\d+", clean, re.IGNORECASE))
    upper = uppercase_ratio(clean) >= 0.65 and len(clean) <= 120
    colon = clean.endswith(":") and len(clean) <= 120
    keyword = bool(keyword_pattern.search(clean) and (prev_blank or next_blank or upper or colon))

    if numbered or step or upper or colon or keyword:
        return clean.rstrip(":")

    return None


def is_noise_heading(text: str) -> bool:
    clean = compact_spaces(text)
    lowered = clean.casefold()
    if re.fullmatch(r"\d+", clean):
        return True
    if "serviceline все авторские права защищены" in lowered:
        return True
    if lowered.startswith("поместить в папку"):
        return True
    if lowered in {"утвержден", "оглавление", "регламент"}:
        return True
    if re.fullmatch(r"стр\.?\s*\d+", lowered):
        return True
    return False


def is_noise_content_line(text: str) -> bool:
    clean = compact_spaces(text)
    lowered = clean.casefold()
    if not clean:
        return True
    if re.fullmatch(r"\d+", clean):
        return True
    if re.fullmatch(r"страница\s+\d+\s+из\s+\d+", lowered):
        return True
    if "serviceline все авторские права защищены" in lowered:
        return True
    if lowered.startswith("поместить в папку"):
        return True
    return False


def meaningful_text(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not is_noise_content_line(line))


def split_page_lines(page: DocumentPage) -> list[tuple[str, int]]:
    return [(line.rstrip(), page.number) for line in page.text.splitlines()]


def split_blocks(lines: list[tuple[str, int]]) -> list[tuple[str, int, int]]:
    blocks: list[tuple[str, int, int]] = []
    current: list[str] = []
    page_start: int | None = None
    page_end: int | None = None

    def flush() -> None:
        nonlocal current, page_start, page_end
        if current and page_start is not None and page_end is not None:
            blocks.append(("\n".join(current).strip(), page_start, page_end))
        current = []
        page_start = None
        page_end = None

    for line, page_number in lines:
        if not line.strip():
            flush()
            continue
        if page_start is None:
            page_start = page_number
        page_end = page_number
        current.append(line)

    flush()
    return blocks


def section_level_from_heading(title: str, default_level: int = 1) -> int:
    match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+", title)
    if match:
        return min(match.group(1).count(".") + 1, 3)
    if re.match(r"^шаг\s*№?\s*\d+", title, re.IGNORECASE):
        return 2
    return default_level


def parse_sections(document: SemanticDocument) -> tuple[list[SectionBlock], list[str]]:
    if document.suffix == ".md":
        return parse_markdown_sections(document)
    return parse_generic_sections(document)


def parse_markdown_sections(document: SemanticDocument) -> tuple[list[SectionBlock], list[str]]:
    sections: list[SectionBlock] = []
    headings: list[str] = []
    stack: list[str] = []
    current_lines: list[str] = []
    current_path: list[str] = [document.title]
    page_start: int | None = None
    page_end: int | None = None

    def flush() -> None:
        nonlocal current_lines, page_start, page_end
        text = "\n".join(current_lines).strip()
        if text:
            title = current_path[-1] if current_path else document.title
            sections.append(
                SectionBlock(
                    title=title,
                    section_path=list(current_path),
                    text=text,
                    page_start=page_start,
                    page_end=page_end,
                )
            )
        current_lines = []
        page_start = None
        page_end = None

    for page in document.pages:
        for raw_line in page.text.splitlines():
            heading = markdown_heading(raw_line)
            if heading:
                flush()
                level, title = heading
                headings.append(title)
                stack = stack[: level - 1]
                stack.append(title)
                current_path = list(stack)
                current_lines.append(raw_line.strip())
                page_start = page.number
                page_end = page.number
                continue

            if raw_line.strip():
                if page_start is None:
                    page_start = page.number
                page_end = page.number
            current_lines.append(raw_line.rstrip())

    flush()
    if not sections and document.text.strip():
        sections.append(
            SectionBlock(
                title=document.title,
                section_path=[document.title],
                text=document.text.strip(),
                page_start=1,
                page_end=document.page_count or 1,
            )
        )

    return sections, headings


def parse_generic_sections(document: SemanticDocument) -> tuple[list[SectionBlock], list[str]]:
    sections: list[SectionBlock] = []
    headings: list[str] = []
    stack: list[str] = [document.title]
    current_lines: list[str] = []
    current_path: list[str] = [document.title]
    page_start: int | None = None
    page_end: int | None = None

    flat_lines: list[tuple[str, int]] = []
    for page in document.pages:
        flat_lines.extend(split_page_lines(page))

    def flush() -> None:
        nonlocal current_lines, page_start, page_end
        text = "\n".join(current_lines).strip()
        if text:
            sections.append(
                SectionBlock(
                    title=current_path[-1] if current_path else document.title,
                    section_path=list(current_path),
                    text=text,
                    page_start=page_start,
                    page_end=page_end,
                )
            )
        current_lines = []
        page_start = None
        page_end = None

    for index, (line, page_number) in enumerate(flat_lines):
        prev_blank = index == 0 or not flat_lines[index - 1][0].strip()
        next_blank = index == len(flat_lines) - 1 or not flat_lines[index + 1][0].strip()
        heading = generic_heading(line, prev_blank=prev_blank, next_blank=next_blank)

        if heading:
            flush()
            level = section_level_from_heading(heading)
            stack = stack[: max(level, 1)]
            if stack and stack[-1] == heading:
                stack[-1] = heading
            else:
                stack.append(heading)
            current_path = list(stack[1:] or [heading])
            headings.append(heading)
            current_lines.append(line.strip())
            page_start = page_number
            page_end = page_number
            continue

        if line.strip():
            if page_start is None:
                page_start = page_number
            page_end = page_number
        current_lines.append(line.rstrip())

    flush()
    if not sections and document.text.strip():
        sections.append(
            SectionBlock(
                title=document.title,
                section_path=[document.title],
                text=document.text.strip(),
                page_start=1,
                page_end=document.page_count or 1,
            )
        )

    return sections, headings


def classify_logical_unit(doc_type: str, section_title: str, text: str) -> str:
    haystack = f"{section_title}\n{text[:2000]}".casefold()

    if doc_type == "org_structure":
        return "org_unit"
    if doc_type == "company_ckp" and ("цкп" in haystack or "ценный конечный продукт" in haystack):
        return "ckp_statement"
    if doc_type == "company_goals" and any(word in haystack for word in ("цель", "замысел", "идеальная картина")):
        return "company_goal"
    if doc_type == "document_flow_policy" and any(
        word in haystack
        for word in (
            "инструкц",
            "заяв",
            "соглас",
            "создать",
            "заходим",
            "нажима",
            "выбираем",
            "заполняем",
            "вкладк",
            "документ",
            "процесс",
            "этап",
            "1с",
            "crm",
        )
    ):
        return "procedure"
    if any(word in haystack for word in ("обязанност", "отвечает", "ответственн", "роль", "руководител", "подчин")):
        return "role_responsibility"
    if any(
        word in haystack
        for word in (
            "шаг",
            "инструкц",
            "порядок",
            "процедур",
            "выполн",
            "согласован",
            "заявк",
            "создать",
            "создаем",
            "заходим",
            "нажимаем",
            "выбираем",
            "заполняем",
            "переходим",
            "далее",
        )
    ):
        return "procedure"
    if any(word in haystack for word in ("требован", "чек-лист", "список", "перечень")) or count_list_items(text) >= 3:
        return "checklist"
    if any(word in haystack for word in ("это", "является", "означает", "определение")) and len(text) < 1200:
        return "definition"
    if any(word in haystack for word in ("правило", "запрещ", "должен", "необходимо", "следует", "распоряжен")):
        return "policy_rule"
    if "пример" in haystack:
        return "example"
    if any(word in haystack for word in ("история", "истор")):
        return "historical_context"
    if any(word in haystack for word in ("справ", "ссылка", "контакт", "форма")):
        return "reference_block"
    return "mixed"


def count_list_items(text: str) -> int:
    return sum(1 for line in text.splitlines() if is_list_or_step_line(line))


def section_text_with_heading(section: SectionBlock) -> str:
    text = section.text.strip()
    if not text:
        return ""
    first = first_nonempty_line(text)
    if first and compact_spaces(first).casefold() == compact_spaces(section.title).casefold():
        return text
    return f"{section.title}\n\n{text}"


def build_logical_units(
    *,
    document: SemanticDocument,
    doc_type: str,
    sections: list[SectionBlock],
) -> list[LogicalUnit]:
    units: list[LogicalUnit] = []

    for index, section in enumerate(sections, start=1):
        text = section_text_with_heading(section).strip()
        if not text:
            continue
        if len(meaningful_text(text)) < 40:
            continue

        unit_type = classify_logical_unit(doc_type, section.title, text)
        section_path = " > ".join(section.section_path)
        units.append(
            LogicalUnit(
                logical_unit_id=f"{document.path.stem}-{index:04d}",
                logical_unit_type=unit_type,
                title=section.title,
                text=text,
                section=section.title,
                section_path=section_path,
                parent_section=section.section_path[-2] if len(section.section_path) > 1 else "",
                page_start=section.page_start,
                page_end=section.page_end,
            )
        )

    return merge_small_related_units(units)


def merge_small_related_units(units: list[LogicalUnit]) -> list[LogicalUnit]:
    if len(units) < 2:
        return units

    protected = {"definition", "policy_rule", "example", "ckp_statement", "company_goal"}
    merged: list[LogicalUnit] = []

    for unit in units:
        if (
            merged
            and len(unit.text) < MIN_MERGE_CHARS
            and unit.logical_unit_type not in protected
            and merged[-1].parent_section == unit.parent_section
            and len(merged[-1].text) + len(unit.text) + 2 <= HARD_MAX_CHARS
        ):
            previous = merged[-1]
            previous.text = f"{previous.text}\n\n{unit.text}"
            previous.title = merge_titles(previous.title, unit.title)
            previous.section = previous.title
            if previous.logical_unit_type == "org_unit" and unit.logical_unit_type == "org_unit":
                previous.section_path = previous.parent_section or previous.section_path
            else:
                previous.section_path = merge_section_paths(previous.section_path, unit.title)
            previous.page_end = unit.page_end or previous.page_end
            if previous.logical_unit_type != unit.logical_unit_type:
                previous.logical_unit_type = "mixed"
            continue
        merged.append(unit)

    return merged


def merge_titles(first: str, second: str, max_length: int = 180) -> str:
    merged = f"{first} / {second}"
    if len(merged) <= max_length:
        return merged
    return f"{first[:80].rstrip()} / ... / {second[:80].rstrip()}"


def merge_section_paths(section_path: str, title: str, max_length: int = 300) -> str:
    merged = f"{section_path} > {title}"
    if len(merged) <= max_length:
        return merged
    return section_path


def detect_structure_quality(
    sections: list[SectionBlock],
    headings: list[str],
    warnings: list[str],
    *,
    doc_type: str,
    text_length: int,
) -> str:
    if not headings and doc_type != "unknown" and text_length >= 500:
        return "medium"
    if warnings and not headings:
        return "poor"
    if len(headings) >= 3 and len(sections) >= 3:
        return "good"
    if headings or len(sections) >= 2:
        return "medium"
    return "poor"


def build_profile(document: SemanticDocument) -> tuple[DocumentProfile, list[LogicalUnit]]:
    doc_type = detect_doc_type(document)
    sections, headings = parse_sections(document)
    warnings = list(document.warnings)

    if not headings:
        warnings.append(
            "Document structure is weakly detected: no reliable headings were found; "
            "fallback by paragraphs/pages is used."
        )

    quality = detect_structure_quality(
        sections,
        headings,
        warnings,
        doc_type=doc_type,
        text_length=len(document.text),
    )
    units = build_logical_units(document=document, doc_type=doc_type, sections=sections)

    if quality == "poor":
        warnings.append(
            "Poor structure quality: PDF text extraction produced too little reliable hierarchy; "
            "manual review is recommended."
        )
        for unit in units:
            unit.warnings.append("Poor document structure; semantic unit may be broad.")
    if doc_type == "unknown":
        warnings.append(
            "Document type remains unknown: filename and extracted text did not match supported semantic doc_type rules."
        )

    profile = DocumentProfile(
        source_file=document.path.name,
        relative_path=document.relative_path,
        doc_type=doc_type,
        title=first_meaningful_line(document.text) or document.title,
        page_count=document.page_count,
        detected_headings=headings[:100],
        detected_sections=[" > ".join(section.section_path) for section in sections[:100]],
        logical_units=[
            {
                "logical_unit_id": unit.logical_unit_id,
                "logical_unit_type": unit.logical_unit_type,
                "logical_unit_title": unit.title,
                "section_path": unit.section_path,
                "page_start": unit.page_start,
                "page_end": unit.page_end,
            }
            for unit in units
        ],
        structure_quality=quality,
        warnings=warnings,
    )
    return profile, units


def split_paragraphs_preserving_lists(text: str) -> list[str]:
    raw_blocks = split_blocks([(line, 1) for line in text.splitlines()])
    paragraphs = [block[0] for block in raw_blocks if block[0].strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []

    grouped: list[str] = []
    index = 0
    while index < len(paragraphs):
        current = paragraphs[index]
        current_lines = current.splitlines()
        is_list_block = any(is_list_or_step_line(line) for line in current_lines)
        if is_list_block and grouped and len(grouped[-1]) < 300:
            grouped[-1] = f"{grouped[-1]}\n\n{current}"
        else:
            grouped.append(current)
        index += 1
    return grouped


def split_oversized_paragraph(paragraph: str, hard_max: int, overlap: int) -> list[str]:
    if len(paragraph) <= hard_max:
        return [paragraph]

    parts: list[str] = []
    start = 0
    safe_overlap = max(0, min(overlap, 250))

    while start < len(paragraph):
        end = min(start + hard_max, len(paragraph))
        if end < len(paragraph):
            boundary = max(paragraph.rfind("\n", start, end), paragraph.rfind(". ", start, end))
            if boundary > start + hard_max // 2:
                end = boundary + 1
        parts.append(paragraph[start:end].strip())
        if end >= len(paragraph):
            break
        start = max(end - safe_overlap, start + 1)

    return [part for part in parts if part]


def split_logical_unit_text(text: str, hard_max: int, overlap: int) -> list[str]:
    if len(text) <= hard_max:
        return [text]

    blocks = split_paragraphs_preserving_lists(text)
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n\n".join(current).strip())
        current = []
        current_len = 0

    for block in blocks:
        block_parts = split_oversized_paragraph(block, hard_max=hard_max, overlap=overlap)
        for block_part in block_parts:
            candidate_len = current_len + len(block_part) + (2 if current else 0)
            if current and candidate_len > hard_max:
                flush()
            current.append(block_part)
            current_len = sum(len(item) for item in current) + max(0, len(current) - 1) * 2

    flush()

    if len(parts) <= 1:
        return parts

    safe_overlap = max(0, min(overlap, 250))
    if safe_overlap <= 0:
        return parts

    overlapped: list[str] = [parts[0]]
    for part in parts[1:]:
        previous_tail = parts[len(overlapped) - 1][-safe_overlap:].strip()
        if previous_tail and len(previous_tail) + len(part) + 2 <= hard_max:
            overlapped.append(f"{previous_tail}\n\n{part}")
        else:
            overlapped.append(part)
    return overlapped


def prepare_chunks(
    *,
    document: SemanticDocument,
    profile: DocumentProfile,
    units: list[LogicalUnit],
    chunk_size: int,
    chunk_overlap: int,
) -> list[PreparedChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must not be negative")
    if chunk_overlap >= HARD_MAX_CHARS:
        raise ValueError("chunk_overlap must be smaller than hard max")

    hard_max = min(max(chunk_size, 800), HARD_MAX_CHARS)
    prepared: list[PreparedChunk] = []

    split_units: list[tuple[LogicalUnit, list[str]]] = [
        (unit, split_logical_unit_text(unit.text, hard_max=hard_max, overlap=chunk_overlap))
        for unit in units
    ]
    chunk_count = sum(len(parts) for _, parts in split_units)
    chunk_index = 0

    for unit, parts in split_units:
        part_count = len(parts)
        for part_index, text in enumerate(parts, start=1):
            chunk_index += 1
            warnings = list(profile.warnings) + list(unit.warnings)
            if part_count > 1:
                warnings.append("Logical unit was split into linked parts because it exceeded hard max.")

            hash_value = content_hash(text)
            metadata = {
                "namespace": NAMESPACE,
                "doc_type": profile.doc_type,
                "source_file": document.path.name,
                "relative_path": document.relative_path,
                "title": profile.title,
                "section": unit.section,
                "section_path": unit.section_path,
                "page_start": unit.page_start,
                "page_end": unit.page_end,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "logical_unit_id": unit.logical_unit_id,
                "logical_unit_type": unit.logical_unit_type,
                "logical_unit_title": unit.title,
                "part_index": part_index,
                "part_count": part_count,
                "parent_section": unit.parent_section,
                "chunking_strategy": "semantic_structural",
                "loader_version": LOADER_VERSION,
                "content_hash": hash_value,
                "structure_quality": profile.structure_quality,
                "warnings": sorted(set(warnings)),
            }
            prepared.append(PreparedChunk(text=text, metadata=metadata))

    return prepared


def semantic_chunk_exists(db_path: Path, source_file: str, hash_value: str) -> bool:
    if not db_path.exists():
        return False

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT metadata_json
            FROM memory_chunks
            WHERE namespace = ?
            """,
            (NAMESPACE,),
        ).fetchall()

    for row in rows:
        metadata_json = row["metadata_json"]
        if not metadata_json:
            continue
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            continue
        if metadata.get("source_file") == source_file and metadata.get("content_hash") == hash_value:
            return True

    return False


def add_chunks_to_store(
    *,
    store: MemoryStore,
    db_path: Path,
    chunks: list[PreparedChunk],
    force: bool,
) -> tuple[int, int]:
    added = 0
    duplicates = 0

    for chunk in chunks:
        metadata = chunk.metadata
        if not force and semantic_chunk_exists(
            db_path,
            source_file=str(metadata["source_file"]),
            hash_value=str(metadata["content_hash"]),
        ):
            duplicates += 1
            continue

        store.add_chunk(
            namespace=NAMESPACE,
            doc_type=str(metadata["doc_type"]),
            title=str(metadata["title"]),
            text=chunk.text,
            source=str(metadata["relative_path"]),
            page=metadata["page_start"],
            section=str(metadata["section"]),
            metadata=metadata,
        )
        added += 1

    return added, duplicates


def write_preview(chunks: list[PreparedChunk], preview_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    preview_path.parent.mkdir(parents=True, exist_ok=True)

    with preview_path.open("w", encoding="utf-8") as file:
        for chunk in chunks:
            metadata = chunk.metadata
            preview = {
                "source_file": metadata["source_file"],
                "relative_path": metadata["relative_path"],
                "title": metadata["title"],
                "doc_type": metadata["doc_type"],
                "namespace": metadata["namespace"],
                "section": metadata["section"],
                "section_path": metadata["section_path"],
                "chunk_index": metadata["chunk_index"],
                "chunk_count": metadata["chunk_count"],
                "logical_unit_id": metadata["logical_unit_id"],
                "logical_unit_type": metadata["logical_unit_type"],
                "logical_unit_title": metadata["logical_unit_title"],
                "part_index": metadata["part_index"],
                "part_count": metadata["part_count"],
                "parent_section": metadata["parent_section"],
                "page_start": metadata["page_start"],
                "page_end": metadata["page_end"],
                "chunking_strategy": metadata["chunking_strategy"],
                "loader_version": metadata["loader_version"],
                "content_hash": metadata["content_hash"],
                "structure_quality": metadata["structure_quality"],
                "text_length": len(chunk.text),
                "text_preview": chunk.text[:500],
                "warnings": metadata["warnings"],
            }
            file.write(json.dumps(preview, ensure_ascii=False) + "\n")

    return preview_path


def print_document_report(
    *,
    profile: DocumentProfile,
    chunk_count: int,
    added_count: int | None = None,
    duplicate_count: int | None = None,
) -> None:
    print(f"\nDocument: {profile.source_file}")
    print(f"  doc_type: {profile.doc_type}")
    print(f"  title: {profile.title}")
    print(f"  page_count: {profile.page_count}")
    print(f"  structure_quality: {profile.structure_quality}")
    print(f"  sections: {len(profile.detected_sections)}")
    print(f"  logical_units: {len(profile.logical_units)}")
    print(f"  chunks: {chunk_count}")
    if added_count is not None:
        print(f"  added: {added_count}")
    if duplicate_count is not None:
        print(f"  duplicates_skipped: {duplicate_count}")
    if profile.detected_sections:
        print("  first_section_paths:")
        for section_path in profile.detected_sections[:3]:
            print(f"    - {section_path}")
    if profile.warnings:
        print("  warnings:")
        for warning in profile.warnings:
            print(f"    - {warning}")


def profile_to_printable(profile: DocumentProfile) -> dict[str, Any]:
    value = asdict(profile)
    value["logical_units"] = value["logical_units"][:20]
    return value


def ingest_directory(
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    chunk_size: int = 1800,
    chunk_overlap: int = 200,
    dry_run: bool = False,
    force: bool = False,
    verbose: bool = False,
    profile_only: bool = False,
    write_preview_file: bool = False,
) -> dict[str, Any]:
    """Profile, chunk and optionally load supported documents into semantic memory."""
    documents = iter_semantic_documents(raw_dir, verbose=verbose)

    if not documents:
        print(f"No .txt, .md or .pdf documents found in {raw_dir}")
        return {
            "files_found": 0,
            "files_processed": 0,
            "chunks_built": 0,
            "chunks_added": 0,
            "duplicates_skipped": 0,
            "poor_structure_documents": 0,
            "preview_path": None,
            "profiles": [],
        }

    store: MemoryStore | None = None
    if not dry_run and not profile_only:
        store = MemoryStore(str(db_path))
        store.ensure_schema()

    all_chunks: list[PreparedChunk] = []
    profiles: list[DocumentProfile] = []
    chunks_added = 0
    duplicates_skipped = 0
    poor_structure_documents = 0

    for document in documents:
        profile, units = build_profile(document)
        profiles.append(profile)
        if profile.structure_quality == "poor":
            poor_structure_documents += 1

        if profile_only:
            print(json.dumps(profile_to_printable(profile), ensure_ascii=False, indent=2))
            print_document_report(profile=profile, chunk_count=0)
            continue

        chunks = prepare_chunks(
            document=document,
            profile=profile,
            units=units,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        all_chunks.extend(chunks)

        added = 0
        duplicates = 0
        if store is not None:
            added, duplicates = add_chunks_to_store(
                store=store,
                db_path=db_path,
                chunks=chunks,
                force=force,
            )
            chunks_added += added
            duplicates_skipped += duplicates

        print_document_report(
            profile=profile,
            chunk_count=len(chunks),
            added_count=None if dry_run else added,
            duplicate_count=None if dry_run else duplicates,
        )

    preview_path: Path | None = None
    if write_preview_file and not profile_only:
        preview_path = write_preview(all_chunks)

    summary = {
        "files_found": len(documents),
        "files_processed": len(profiles),
        "chunks_built": len(all_chunks),
        "chunks_added": chunks_added,
        "duplicates_skipped": duplicates_skipped,
        "poor_structure_documents": poor_structure_documents,
        "preview_path": str(preview_path) if preview_path else None,
        "profiles": profiles,
    }

    print("\nSummary:")
    print(f"  files_found: {summary['files_found']}")
    print(f"  files_processed: {summary['files_processed']}")
    print(f"  chunks_built: {summary['chunks_built']}")
    print(f"  chunks_added: {summary['chunks_added']}")
    print(f"  duplicates_skipped: {summary['duplicates_skipped']}")
    print(f"  poor_structure_documents: {summary['poor_structure_documents']}")
    if preview_path:
        print(f"  preview_path: {preview_path}")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load local semantic documents from data/raw_docs into LineHelper semantic memory."
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
        default=1800,
        help="Target chunk size in characters; hard max is 2400.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=200,
        help="Overlap for forced splits of one logical unit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build profiles and chunks without writing to the database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Add chunks even when the same source_file/content_hash already exists.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra loader messages.",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Analyze documents and print profiles without building chunks or writing to the database.",
    )
    parser.add_argument(
        "--write-preview",
        action="store_true",
        help="Write chunk preview to data/semantic_index/chunk_preview.jsonl.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ingest_directory(
        raw_dir=args.raw_dir,
        db_path=args.db_path,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        dry_run=args.dry_run,
        force=args.force,
        verbose=args.verbose,
        profile_only=args.profile_only,
        write_preview_file=args.write_preview,
    )


if __name__ == "__main__":
    main()
