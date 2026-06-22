from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from linehelper.llm.ollama_client import (
    OllamaClient,
    OllamaEmptyResponseError,
    OllamaModelNotFoundError,
    load_ollama_settings_from_env,
)


def test_settings_use_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "OLLAMA_TIMEOUT_SECONDS",
        "OLLAMA_TEMPERATURE",
        "OLLAMA_NUM_PREDICT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_ollama_settings_from_env()

    assert settings.base_url == "http://localhost:11434"
    assert settings.model == "qwen2.5:14b"
    assert settings.timeout_seconds == 180
    assert settings.temperature == 0
    assert settings.num_predict == 700


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/")
    monkeypatch.setenv("OLLAMA_MODEL", "local-model")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.2")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "345")

    settings = load_ollama_settings_from_env()

    assert settings.base_url == "http://127.0.0.1:11434"
    assert settings.model == "local-model"
    assert settings.timeout_seconds == 12
    assert settings.temperature == 0.2
    assert settings.num_predict == 345


def test_chat_sends_utf8_json_to_api_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"message": {"content": "Ответ на русском"}}, ensure_ascii=False
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        return FakeResponse()

    monkeypatch.setattr("linehelper.llm.ollama_client.urlopen", fake_urlopen)
    client = OllamaClient(base_url="http://local", model="qwen", timeout_seconds=9)

    answer = client.chat([{"role": "user", "content": "Привет"}])

    payload = json.loads(captured["body"].decode("utf-8"))
    assert answer == "Ответ на русском"
    assert captured["url"] == "http://local/api/chat"
    assert captured["timeout"] == 9
    assert captured["headers"]["Content-type"] == "application/json; charset=utf-8"
    assert payload["model"] == "qwen"
    assert payload["stream"] is False
    assert payload["messages"][0]["content"] == "Привет"


def test_empty_response_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"message":{"content":"   "}}'

    monkeypatch.setattr(
        "linehelper.llm.ollama_client.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    with pytest.raises(OllamaEmptyResponseError):
        OllamaClient(base_url="http://local").chat(
            [{"role": "user", "content": "test"}]
        )


def test_model_not_found_is_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            404,
            "Not Found",
            hdrs=None,
            fp=_BytesReader(b'{"error":"model not found"}'),
        )

    monkeypatch.setattr("linehelper.llm.ollama_client.urlopen", fake_urlopen)

    with pytest.raises(OllamaModelNotFoundError):
        OllamaClient(base_url="http://local", model="missing").chat(
            [{"role": "user", "content": "test"}]
        )


class _BytesReader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        return None
