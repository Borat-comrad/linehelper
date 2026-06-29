"""Small Streamlit UI formatting helpers for LineHelper."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any


DEFAULT_SOURCE_NAMESPACE = "semantic"


@dataclass(frozen=True)
class SourceCard:
    """Normalized source data ready for UI rendering."""

    title: str
    namespace: str
    source: str
    section: str
    page: str
    score: str
    excerpt: str
    logical_unit_title: str


def source_namespace(source: Any) -> str:
    """Infer a memory namespace while keeping compatibility with current sources."""
    explicit_namespace = getattr(source, "namespace", None)
    if explicit_namespace:
        return str(explicit_namespace)

    searchable = " ".join(
        str(value or "")
        for value in (
            getattr(source, "source", ""),
            getattr(source, "title", ""),
            getattr(source, "logical_unit_title", ""),
        )
    ).casefold()

    if "episodic" in searchable or "episode" in searchable:
        return "episodic"
    return DEFAULT_SOURCE_NAMESPACE


def format_score(score: float | int | None) -> str:
    """Format relevance score for compact source cards."""
    if score is None:
        return "-"
    return f"{float(score):.2f}"


def source_to_card(source: Any) -> SourceCard:
    """Convert a RagSource-like object into display-safe source card fields."""
    section = getattr(source, "section", None)
    page = getattr(source, "page", None)
    logical_unit_title = getattr(source, "logical_unit_title", None)

    return SourceCard(
        title=str(getattr(source, "title", None) or "Без названия"),
        namespace=source_namespace(source),
        source=str(getattr(source, "source", None) or "-"),
        section=str(section or "-"),
        page=str(page if page is not None else "-"),
        score=format_score(getattr(source, "score", None)),
        excerpt=str(getattr(source, "matched_excerpt", None) or ""),
        logical_unit_title=str(logical_unit_title or "-"),
    )


def source_card_html(source: Any, *, index: int | None = None) -> str:
    """Render a source card as local, dependency-free HTML."""
    card = source_to_card(source)
    namespace_class = "episodic" if card.namespace == "episodic" else "semantic"
    title_prefix = f"{index}. " if index is not None else ""

    excerpt = (
        f'<div class="lh-source-excerpt">{escape(card.excerpt)}</div>'
        if card.excerpt
        else '<div class="lh-source-excerpt lh-muted">Фрагмент не передан.</div>'
    )

    return f"""
<div class="lh-source-card lh-source-{namespace_class}">
  <div class="lh-source-card__head">
    <div class="lh-source-title">{escape(title_prefix + card.title)}</div>
    <span class="lh-badge lh-badge-{namespace_class}">{escape(card.namespace)}</span>
  </div>
  <div class="lh-source-meta">
    <span>namespace: <b>{escape(card.namespace)}</b></span>
    <span>score: <b>{escape(card.score)}</b></span>
    <span>page: <b>{escape(card.page)}</b></span>
    <span>section: <b>{escape(card.section)}</b></span>
  </div>
  <div class="lh-source-meta lh-source-meta--wide">
    <span>document title: <b>{escape(card.logical_unit_title)}</b></span>
    <span>source: <b>{escape(card.source)}</b></span>
  </div>
  {excerpt}
</div>
""".strip()


def badge_html(label: str, kind: str = "neutral") -> str:
    """Return a compact badge snippet for Streamlit markdown."""
    return f'<span class="lh-badge lh-badge-{escape(kind)}">{escape(label)}</span>'
