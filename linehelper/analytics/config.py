"""Environment-backed settings for local interaction analytics."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AnalyticsConfig:
    """Safe local defaults; non-positive retention disables automatic cleanup."""

    enabled: bool
    db_path: Path
    retention_days: int
    store_text: bool
    redact_pii: bool
    queue_size: int
    user_hash_secret: str | None
    busy_timeout_ms: int = 1000

    @classmethod
    def from_env(cls, project_root: Path) -> "AnalyticsConfig":
        default_path = project_root / "data" / "analytics" / "linehelper_interactions.db"
        raw_path = os.getenv("LINEHELPER_ANALYTICS_DB_PATH", "").strip()
        db_path = Path(raw_path).expanduser() if raw_path else default_path
        if not db_path.is_absolute():
            db_path = project_root / db_path
        secret = os.getenv("LINEHELPER_ANALYTICS_USER_HASH_SECRET", "").strip()
        return cls(
            enabled=_env_bool("LINEHELPER_ANALYTICS_ENABLED", True),
            db_path=db_path.resolve(),
            retention_days=_env_int("LINEHELPER_ANALYTICS_RETENTION_DAYS", 90),
            store_text=_env_bool("LINEHELPER_ANALYTICS_STORE_TEXT", True),
            redact_pii=_env_bool("LINEHELPER_ANALYTICS_REDACT_PII", True),
            queue_size=max(1, _env_int("LINEHELPER_ANALYTICS_QUEUE_SIZE", 1000)),
            user_hash_secret=secret or None,
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
