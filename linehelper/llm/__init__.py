"""Local LLM integration helpers for LineHelper."""

from linehelper.llm.answer_generator import (
    RagAnswer,
    RagAnswerError,
    RagAnswerGenerator,
    RagSource,
)
from linehelper.llm.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaEmptyResponseError,
    OllamaError,
    OllamaHttpError,
    OllamaInvalidResponseError,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
)

__all__ = [
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaEmptyResponseError",
    "OllamaError",
    "OllamaHttpError",
    "OllamaInvalidResponseError",
    "OllamaModelNotFoundError",
    "OllamaTimeoutError",
    "RagAnswer",
    "RagAnswerError",
    "RagAnswerGenerator",
    "RagSource",
]
