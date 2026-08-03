"""Bounded best-effort writer that cannot alter a completed RAG answer."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import os
from pathlib import Path
from queue import Full, Queue
import subprocess
from threading import Thread
from typing import Any

from linehelper import __version__
from linehelper.analytics.config import AnalyticsConfig
from linehelper.analytics.interaction_store import InteractionStore
from linehelper.analytics.models import CleanupResult, InteractionFeedback, InteractionRecord
from linehelper.analytics.sanitizer import sanitize_text
from linehelper.analytics.serialization import interaction_record_from_answer
from linehelper.rag.query_analyzer import DEFAULT_ANALYZER_MODEL


LOGGER = logging.getLogger(__name__)
_STOP = object()


@dataclass(frozen=True)
class _WriteOperation:
    kind: str
    payload: InteractionRecord | InteractionFeedback


class InteractionLogger:
    """Serialize synchronously, persist asynchronously through one bounded queue."""

    def __init__(
        self,
        *,
        config: AnalyticsConfig,
        store: InteractionStore | None = None,
        app_version: str = __version__,
        git_commit: str | None = None,
        analyzer_model: str | None = None,
    ) -> None:
        self.config = config
        self.store = store or InteractionStore(
            config.db_path,
            busy_timeout_ms=config.busy_timeout_ms,
        )
        self.app_version = app_version
        self.git_commit = git_commit
        self.analyzer_model = analyzer_model or os.getenv(
            "OLLAMA_ANALYZER_MODEL",
            DEFAULT_ANALYZER_MODEL,
        )
        self.last_error: str | None = None
        self._closed = False
        self._queue: Queue[_WriteOperation | object] = Queue(maxsize=config.queue_size)
        self.store.ensure_schema()
        self.store.cleanup_if_due(retention_days=config.retention_days)
        self._worker = Thread(
            target=self._write_loop,
            name="linehelper-analytics-writer",
            daemon=True,
        )
        self._worker.start()

    @property
    def enabled(self) -> bool:
        return not self._closed

    def record_answer(
        self,
        result: Any,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        record = interaction_record_from_answer(
            result,
            config=self.config,
            session_id=session_id,
            user_id=user_id,
            analyzer_model=self.analyzer_model,
            app_version=self.app_version,
            git_commit=self.git_commit,
        )
        return self.record_interaction(record)

    def record_interaction(self, record: InteractionRecord) -> str | None:
        if self._closed:
            return None
        if self._enqueue(_WriteOperation("interaction", record)):
            return record.interaction_id
        return None

    def record_feedback(self, feedback: InteractionFeedback) -> bool:
        if self._closed:
            return False
        sanitized = replace(
            feedback,
            comment=(
                sanitize_text(feedback.comment, redact_pii=self.config.redact_pii)
                if self.config.store_text
                else None
            ),
        )
        return self._enqueue(_WriteOperation("feedback", sanitized))

    def cleanup_expired(self, cutoff=None) -> CleanupResult:
        try:
            self.flush()
            return self.store.cleanup_expired(
                retention_days=self.config.retention_days,
                cutoff=cutoff,
            )
        except Exception as exc:  # best-effort maintenance
            self._report_error(exc)
            return CleanupResult(0, cutoff, performed=False, error=self.last_error)

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._queue.put(_STOP)
        self._worker.join(timeout=5.0)
        self._closed = True

    def _enqueue(self, operation: _WriteOperation) -> bool:
        try:
            self._queue.put_nowait(operation)
            return True
        except Full:
            self.last_error = "analytics_queue_full"
            LOGGER.warning("Interaction analytics record dropped: queue is full")
            return False

    def _write_loop(self) -> None:
        while True:
            operation = self._queue.get()
            try:
                if operation is _STOP:
                    return
                assert isinstance(operation, _WriteOperation)
                if operation.kind == "interaction":
                    assert isinstance(operation.payload, InteractionRecord)
                    self.store.record_interaction(operation.payload)
                elif operation.kind == "feedback":
                    assert isinstance(operation.payload, InteractionFeedback)
                    self.store.record_feedback(operation.payload)
                self.last_error = None
            except Exception as exc:  # logging must never affect answer delivery
                self._report_error(exc)
            finally:
                self._queue.task_done()

    def _report_error(self, exc: Exception) -> None:
        message = sanitize_text(str(exc), redact_pii=True) or type(exc).__name__
        self.last_error = f"{type(exc).__name__}: {message[:300]}"
        LOGGER.warning("Interaction analytics write failed: %s", self.last_error)


class NullInteractionLogger:
    """Disabled logger that never creates a database or changes answer behavior."""

    enabled = False

    def __init__(self, reason: str = "analytics_disabled") -> None:
        self.last_error = reason

    def record_answer(self, result: Any, **_: Any) -> None:
        return None

    def record_interaction(self, record: InteractionRecord) -> None:
        return None

    def record_feedback(self, feedback: InteractionFeedback) -> bool:
        return False

    def cleanup_expired(self, cutoff=None) -> CleanupResult:
        return CleanupResult(0, cutoff, performed=False, error=self.last_error)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def create_interaction_logger(
    config: AnalyticsConfig,
    *,
    project_root: Path | None = None,
) -> InteractionLogger | NullInteractionLogger:
    """Create the configured logger; initialization failures degrade to null."""
    if not config.enabled:
        return NullInteractionLogger()
    try:
        return InteractionLogger(
            config=config,
            git_commit=_git_commit(project_root) if project_root else None,
        )
    except Exception as exc:
        message = sanitize_text(str(exc), redact_pii=True) or type(exc).__name__
        LOGGER.warning(
            "Interaction analytics initialization failed: %s: %s",
            type(exc).__name__,
            message[:300],
        )
        return NullInteractionLogger(f"{type(exc).__name__}: {message[:300]}")


def _git_commit(project_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    value = completed.stdout.strip()
    return value or None
