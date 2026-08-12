"""User-facing formatting helpers for structured catalog values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_catalog_quantity(value: object) -> str:
    """Format a stored quantity for the Russian UI without changing its value."""
    raw = str(value).strip()
    if not raw:
        return raw
    try:
        number = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return raw.replace(".", ",")
    if not number.is_finite():
        return raw.replace(".", ",")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"-0", "+0"}:
        rendered = "0"
    return rendered.replace(".", ",")
