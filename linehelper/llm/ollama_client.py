"""Small UTF-8 safe Ollama chat client for the local RAG MVP."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_TEMPERATURE = 0.0
DEFAULT_NUM_PREDICT = 700


class OllamaError(RuntimeError):
    """Base error for local Ollama calls."""


class OllamaConnectionError(OllamaError):
    """Raised when Ollama is not reachable."""


class OllamaTimeoutError(OllamaError):
    """Raised when Ollama does not answer before timeout."""


class OllamaModelNotFoundError(OllamaError):
    """Raised when the configured model is not available in Ollama."""


class OllamaHttpError(OllamaError):
    """Raised for non-success HTTP responses from Ollama."""


class OllamaInvalidResponseError(OllamaError):
    """Raised when Ollama returns invalid JSON or an unexpected shape."""


class OllamaEmptyResponseError(OllamaError):
    """Raised when Ollama returns an empty assistant message."""


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    temperature: float = DEFAULT_TEMPERATURE
    num_predict: int = DEFAULT_NUM_PREDICT


def load_ollama_settings_from_env() -> OllamaSettings:
    """Load Ollama settings from environment variables with MVP defaults."""
    return OllamaSettings(
        base_url=os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=os.getenv("OLLAMA_MODEL", DEFAULT_MODEL),
        timeout_seconds=_env_float("OLLAMA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        temperature=_env_float("OLLAMA_TEMPERATURE", DEFAULT_TEMPERATURE),
        num_predict=_env_int("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT),
    )


class OllamaClient:
    """Minimal client for Ollama's `/api/chat` endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
    ) -> None:
        settings = load_ollama_settings_from_env()
        self.base_url = (base_url or settings.base_url).rstrip("/")
        self.model = model or settings.model
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.timeout_seconds
        )
        self.temperature = (
            temperature if temperature is not None else settings.temperature
        )
        self.num_predict = (
            num_predict if num_predict is not None else settings.num_predict
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """Send chat messages to local Ollama and return assistant text."""
        selected_model = model or self.model
        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": (
                    self.temperature if temperature is None else temperature
                ),
                "num_predict": (
                    self.num_predict if num_predict is None else num_predict
                ),
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(  # noqa: S310 - local configured Ollama endpoint only.
                request,
                timeout=timeout_seconds or self.timeout_seconds,
            ) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            self._raise_http_error(exc, selected_model)
        except socket.timeout as exc:
            raise OllamaTimeoutError(
                f"Ollama did not answer within {timeout_seconds or self.timeout_seconds:g} seconds."
            ) from exc
        except TimeoutError as exc:
            raise OllamaTimeoutError(
                f"Ollama did not answer within {timeout_seconds or self.timeout_seconds:g} seconds."
            ) from exc
        except URLError as exc:
            raise OllamaConnectionError(
                f"Ollama is not reachable at {self.base_url}. Check that Ollama is running."
            ) from exc

        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise OllamaInvalidResponseError("Ollama returned invalid JSON.") from exc

        content = _extract_message_content(data)
        if not content.strip():
            raise OllamaEmptyResponseError("Ollama returned an empty assistant message.")

        return content.strip()

    def _raise_http_error(self, exc: HTTPError, model: str) -> None:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""

        body_lower = body.lower()
        if exc.code == 404 or ("model" in body_lower and "not found" in body_lower):
            raise OllamaModelNotFoundError(
                f"Ollama model is not available: {model!r}."
            ) from exc

        detail = f" Details: {body}" if body else ""
        raise OllamaHttpError(f"Ollama HTTP error {exc.code}.{detail}") from exc


def _extract_message_content(data: Any) -> str:
    if not isinstance(data, dict):
        raise OllamaInvalidResponseError("Ollama response is not a JSON object.")

    message = data.get("message")
    if not isinstance(message, dict):
        raise OllamaInvalidResponseError("Ollama response does not contain message.")

    content = message.get("content")
    if not isinstance(content, str):
        raise OllamaInvalidResponseError(
            "Ollama response message does not contain text content."
        )

    return content


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
