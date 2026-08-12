from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import pytest

from linehelper.catalogs.chat import CatalogChatService
from linehelper.catalogs.models import CatalogPartResult
from linehelper.catalogs.navigation import (
    CatalogDocumentIdentity,
    CatalogPageLocator,
    CatalogPagePreviewError,
    render_catalog_page_png,
)


def test_source_and_reference_pages_resolve_to_distinct_physical_indexes(tmp_path):
    source_root, result = _fixture(tmp_path, source_page=9, reference_page=12)

    navigation = CatalogPageLocator(source_root).locate_occurrence(result)

    assert navigation.source.status == "available"
    assert navigation.source.page_number == 9
    assert navigation.source.pdf_index == 8
    assert navigation.reference is not None
    assert navigation.reference.status == "available"
    assert navigation.reference.page_number == 12
    assert navigation.reference.pdf_index == 11
    assert navigation.source != navigation.reference


def test_null_reference_has_only_source_navigation(tmp_path):
    source_root, result = _fixture(tmp_path, source_page=9, reference_page=None)

    navigation = CatalogPageLocator(source_root).locate_occurrence(result)

    assert navigation.source.status == "available"
    assert navigation.reference is None


def test_missing_pdf_is_controlled(tmp_path):
    source_root = tmp_path / "catalogs"
    source_root.mkdir()
    result = _result(
        source_filename="missing.pdf",
        source_checksum="0" * 64,
        source_page=1,
        reference_page=None,
    )

    target = CatalogPageLocator(source_root).locate_occurrence(result).source

    assert target.status == "unavailable"
    assert target.pdf_index is None
    assert target.error == "source_pdf_missing"


@pytest.mark.parametrize("page_number", [0, -1, 13])
def test_invalid_page_is_controlled(tmp_path, page_number):
    source_root, result = _fixture(tmp_path, source_page=1, reference_page=None)
    document = _document(result)

    target = CatalogPageLocator(source_root).locate(
        document,
        page_number,
        "source",
    )

    assert target.status == "invalid_page"
    assert target.pdf_index is None
    assert target.error == "page_out_of_range"


def test_multiple_occurrences_keep_independent_page_targets(tmp_path):
    source_root, first = _fixture(tmp_path, source_page=2, reference_page=3)
    second = _copy_pages(first, source_page=9, reference_page=12, bom_item_id=2)
    locator = CatalogPageLocator(source_root)

    navigation = [locator.locate_occurrence(item) for item in (first, second)]

    assert [(item.source.page_number, item.reference.page_number) for item in navigation] == [
        (2, 3),
        (9, 12),
    ]
    assert [item.bom_item_id for item in navigation] == [1, 2]


def test_preview_renders_only_validated_target_and_public_metadata_hides_path(tmp_path):
    source_root, result = _fixture(tmp_path, source_page=9, reference_page=None)
    target = CatalogPageLocator(source_root).locate_occurrence(result).source

    png = render_catalog_page_png(target)
    public = target.to_dict()

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert public["page_number"] == 9
    assert public["pdf_index"] == 8
    assert not any("path" in key for key in public)


def test_path_traversal_filename_is_rejected(tmp_path):
    source_root, result = _fixture(tmp_path, source_page=1, reference_page=None)
    unsafe = CatalogDocumentIdentity(
        catalog_id=1,
        source_filename="../catalog.pdf",
        source_checksum=result.source_checksum or "",
        page_count=12,
    )

    target = CatalogPageLocator(source_root).locate(unsafe, 1, "source")

    assert target.status == "unavailable"
    assert target.error == "invalid_source_filename"
    with pytest.raises(CatalogPagePreviewError):
        render_catalog_page_png(target)


def test_exact_chat_adds_document_and_safe_navigation_metadata(tmp_path):
    source_root, result = _fixture(tmp_path, source_page=9, reference_page=12)
    service = CatalogChatService(
        search=ExactSearch([result]),
        page_locator=CatalogPageLocator(source_root),
    )

    outcome = service.lookup("Найди 20411616")

    assert outcome is not None
    assert outcome.status == "found"
    assert "Документ: catalog.pdf" in outcome.answer
    assert len(outcome.navigation) == 1
    public = outcome.to_dict()["navigation"][0]
    assert public["source"]["pdf_index"] == 8
    assert public["reference"]["pdf_index"] == 11
    assert "source_path" not in str(public)


def _fixture(tmp_path, *, source_page, reference_page):
    source_root = tmp_path / "catalogs"
    document_dir = source_root / "volzhsky"
    document_dir.mkdir(parents=True)
    pdf_path = document_dir / "catalog.pdf"
    _write_pdf(pdf_path, 12)
    checksum = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return source_root, _result(
        source_filename=pdf_path.name,
        source_checksum=checksum,
        source_page=source_page,
        reference_page=reference_page,
    )


def _write_pdf(path: Path, page_count: int):
    document = fitz.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page()
        page.insert_text((72, 72), f"Physical page {page_number}")
    document.save(path)
    document.close()


def _result(
    *,
    source_filename,
    source_checksum,
    source_page,
    reference_page,
    bom_item_id=1,
):
    return CatalogPartResult(
        part_number="20411616",
        part_name="наполнитель",
        assembly_code="10307585",
        assembly_name="узел",
        position="200",
        quantity="1.000",
        unit="шт",
        equipment_model="Innofill",
        machine_number="47592",
        revision="05",
        source_page=source_page,
        reference_page=reference_page,
        bom_item_id=bom_item_id,
        catalog_id=1,
        source_filename=source_filename,
        source_checksum=source_checksum,
        catalog_page_count=12,
    )


def _copy_pages(result, *, source_page, reference_page, bom_item_id):
    values = result.to_dict()
    values.update(
        source_page=source_page,
        reference_page=reference_page,
        bom_item_id=bom_item_id,
    )
    return CatalogPartResult(**values)


def _document(result):
    return CatalogDocumentIdentity(
        catalog_id=result.catalog_id,
        source_filename=result.source_filename,
        source_checksum=result.source_checksum,
        page_count=result.catalog_page_count,
    )


class ExactSearch:
    def __init__(self, results):
        self.results = results

    def find_part_by_number(self, part_number):
        return self.results
