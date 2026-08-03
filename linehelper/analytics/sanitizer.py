"""Small deterministic sanitizer for analytics text fields."""

from __future__ import annotations

import re


_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE_CANDIDATE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
_SECRET_RE = re.compile(
    r"(?i)\b(?P<name>token|password|api[_-]?key|authorization)\s*[:=]\s*"
    r"(?:bearer\s+)?(?P<value>[^\s,;]+)"
)


def sanitize_text(value: str | None, *, redact_pii: bool = True) -> str | None:
    """Redact common PII and explicit secret assignments without broad DLP claims."""
    if value is None:
        return None
    text = str(value)
    if not redact_pii:
        return text
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_CANDIDATE_RE.sub(_redact_phone_candidate, text)
    return _SECRET_RE.sub(lambda match: f"{match.group('name')}=[REDACTED]", text)


def _redact_phone_candidate(match: re.Match[str]) -> str:
    value = match.group(0)
    return "[PHONE]" if len(re.sub(r"\D", "", value)) >= 10 else value
