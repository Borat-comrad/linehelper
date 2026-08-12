"""Resolve registered catalog pages to safe local PDF navigation targets."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Literal

import fitz

from .models import CatalogPartResult


PageKind = Literal["source", "reference"]
NavigationStatus = Literal["available", "unavailable", "invalid_page"]


@dataclass(frozen=True)
class CatalogDocumentIdentity:
    catalog_id: int
    source_filename: str
    source_checksum: str
    page_count: int


@dataclass(frozen=True)
class CatalogPageTarget:
    catalog_id: int
    document_name: str
    document_checksum: str
    document_page_count: int
    page_kind: PageKind
    page_number: int | None
    pdf_index: int | None
    status: NavigationStatus
    error: str | None = None
    _source_path: Path | None = field(default=None, repr=False, compare=False)
    _source_root: Path | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        """Return browser-safe metadata without a local filesystem path."""
        return {
            "catalog_id": self.catalog_id,
            "document_name": self.document_name,
            "document_checksum": self.document_checksum,
            "document_page_count": self.document_page_count,
            "page_kind": self.page_kind,
            "page_number": self.page_number,
            "pdf_index": self.pdf_index,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class CatalogOccurrenceNavigation:
    bom_item_id: int | None
    part_number: str
    assembly_code: str
    position: str
    document: CatalogDocumentIdentity
    source: CatalogPageTarget
    reference: CatalogPageTarget | None

    def to_dict(self) -> dict[str, object]:
        return {
            "bom_item_id": self.bom_item_id,
            "part_number": self.part_number,
            "assembly_code": self.assembly_code,
            "position": self.position,
            "document": {
                "catalog_id": self.document.catalog_id,
                "source_filename": self.document.source_filename,
                "source_checksum": self.document.source_checksum,
                "page_count": self.document.page_count,
            },
            "source": self.source.to_dict(),
            "reference": self.reference.to_dict() if self.reference else None,
        }


class CatalogPagePreviewError(RuntimeError):
    """Raised when an internal page target cannot be rendered safely."""


class CatalogPageLocator:
    """Find checksum-registered PDFs strictly inside a configured source root."""

    def __init__(self, source_root: Path | str):
        self.source_root = Path(source_root).expanduser().resolve()
        self._document_cache: dict[
            CatalogDocumentIdentity,
            tuple[Path | None, int | None, str | None],
        ] = {}

    def locate_occurrence(
        self,
        result: CatalogPartResult,
    ) -> CatalogOccurrenceNavigation:
        document = _document_from_result(result)
        source = self.locate(document, result.source_page, "source")
        reference = (
            self.locate(document, result.reference_page, "reference")
            if result.reference_page is not None
            else None
        )
        return CatalogOccurrenceNavigation(
            bom_item_id=result.bom_item_id,
            part_number=result.part_number,
            assembly_code=result.assembly_code,
            position=result.position,
            document=document,
            source=source,
            reference=reference,
        )

    def locate(
        self,
        document: CatalogDocumentIdentity,
        page_number: int,
        page_kind: PageKind,
    ) -> CatalogPageTarget:
        if page_number <= 0 or page_number > document.page_count:
            return _target(
                document,
                page_number,
                page_kind,
                status="invalid_page",
                error="page_out_of_range",
            )

        source_path, actual_page_count, error = self._resolve_document(document)
        if source_path is None:
            return _target(
                document,
                page_number,
                page_kind,
                status="unavailable",
                error=error or "source_pdf_unavailable",
            )
        if actual_page_count != document.page_count:
            return _target(
                document,
                page_number,
                page_kind,
                status="unavailable",
                error="page_count_mismatch",
            )
        if page_number > actual_page_count:
            return _target(
                document,
                page_number,
                page_kind,
                status="invalid_page",
                error="page_out_of_range",
            )
        return _target(
            document,
            page_number,
            page_kind,
            status="available",
            source_path=source_path,
            source_root=self.source_root,
        )

    def _resolve_document(
        self,
        document: CatalogDocumentIdentity,
    ) -> tuple[Path | None, int | None, str | None]:
        cached = self._document_cache.get(document)
        if cached is not None:
            return cached

        if not self.source_root.is_dir():
            result = (None, None, "catalog_source_root_missing")
        elif Path(document.source_filename).name != document.source_filename:
            result = (None, None, "invalid_source_filename")
        else:
            try:
                candidates = [
                    path.resolve()
                    for path in self.source_root.rglob(document.source_filename)
                    if path.is_file()
                    and _is_within(path.resolve(), self.source_root)
                ]
                matching = [
                    path
                    for path in candidates
                    if _sha256(path) == document.source_checksum
                ]
                if not candidates:
                    result = (None, None, "source_pdf_missing")
                elif not matching:
                    result = (None, None, "source_checksum_mismatch")
                elif len(matching) != 1:
                    result = (None, None, "ambiguous_source_pdf")
                else:
                    with fitz.open(matching[0]) as pdf:
                        result = (matching[0], pdf.page_count, None)
            except Exception:
                result = (None, None, "source_pdf_unreadable")
        self._document_cache[document] = result
        return result


def render_catalog_page_png(
    target: CatalogPageTarget,
    *,
    scale: float = 1.5,
) -> bytes:
    """Render one validated server-side page; no path comes from browser input."""
    source_path = target._source_path
    source_root = target._source_root
    if (
        target.status != "available"
        or target.pdf_index is None
        or source_path is None
        or source_root is None
        or not _is_within(source_path.resolve(), source_root.resolve())
        or source_path.suffix.casefold() != ".pdf"
    ):
        raise CatalogPagePreviewError("catalog page target is unavailable")
    if not 0.5 <= scale <= 4.0:
        raise CatalogPagePreviewError("invalid preview scale")
    try:
        with fitz.open(source_path) as pdf:
            if target.pdf_index < 0 or target.pdf_index >= pdf.page_count:
                raise CatalogPagePreviewError("catalog page index is out of range")
            page = pdf.load_page(target.pdf_index)
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False,
            )
            return pixmap.tobytes("png")
    except CatalogPagePreviewError:
        raise
    except Exception as exc:
        raise CatalogPagePreviewError("catalog page preview failed") from exc


def _document_from_result(result: CatalogPartResult) -> CatalogDocumentIdentity:
    if (
        result.catalog_id is None
        or not result.source_filename
        or not result.source_checksum
        or result.catalog_page_count is None
        or result.catalog_page_count <= 0
    ):
        raise ValueError("catalog result is missing document identity")
    return CatalogDocumentIdentity(
        catalog_id=result.catalog_id,
        source_filename=result.source_filename,
        source_checksum=result.source_checksum,
        page_count=result.catalog_page_count,
    )


def _target(
    document: CatalogDocumentIdentity,
    page_number: int,
    page_kind: PageKind,
    *,
    status: NavigationStatus,
    error: str | None = None,
    source_path: Path | None = None,
    source_root: Path | None = None,
) -> CatalogPageTarget:
    return CatalogPageTarget(
        catalog_id=document.catalog_id,
        document_name=document.source_filename,
        document_checksum=document.source_checksum,
        document_page_count=document.page_count,
        page_kind=page_kind,
        page_number=page_number,
        pdf_index=page_number - 1 if status == "available" else None,
        status=status,
        error=error,
        _source_path=source_path,
        _source_root=source_root,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
