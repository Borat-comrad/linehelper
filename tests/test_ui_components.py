from types import SimpleNamespace

from linehelper.ui.components import format_score, source_card_html, source_namespace, source_to_card


def test_format_score_keeps_two_decimal_places() -> None:
    assert format_score(12.345) == "12.35"
    assert format_score(None) == "-"


def test_source_to_card_uses_semantic_namespace_by_default() -> None:
    source = SimpleNamespace(
        title="Регламент",
        source="data/raw_docs/regulation.pdf",
        section="Раздел 1",
        page=4,
        logical_unit_title=None,
        score=91.234,
        matched_excerpt="Короткий фрагмент",
    )

    card = source_to_card(source)

    assert card.namespace == "semantic"
    assert card.title == "Регламент"
    assert card.section == "Раздел 1"
    assert card.page == "4"
    assert card.score == "91.23"


def test_source_namespace_detects_episodic_sources() -> None:
    source = SimpleNamespace(
        title="Опыт КП",
        source="memory/episodic/deals",
        logical_unit_title=None,
    )

    assert source_namespace(source) == "episodic"


def test_source_card_html_escapes_user_controlled_fields() -> None:
    source = SimpleNamespace(
        title="<script>alert(1)</script>",
        source="demo",
        section="main",
        page=None,
        logical_unit_title="Документ",
        score=None,
        matched_excerpt="<b>важно</b>",
    )

    html = source_card_html(source, index=1)

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;важно&lt;/b&gt;" in html
