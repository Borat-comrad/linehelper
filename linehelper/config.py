"""Runtime configuration helpers for the LineHelper CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from linehelper.llm.ollama_client import DEFAULT_BASE_URL, DEFAULT_MODEL


DEFAULT_STREAMLIT_HOST = "localhost"
DEFAULT_STREAMLIT_PORT = 8501


class LineHelperConfigError(RuntimeError):
    """Raised when the LineHelper project layout cannot be resolved."""


@dataclass(frozen=True)
class LineHelperConfig:
    project_root: Path
    db_path: Path
    model: str
    ollama_url: str
    streamlit_host: str
    streamlit_port: int
    runtime_dir: Path
    streamlit_app_path: Path
    organization_source_path: Path


def load_config() -> LineHelperConfig:
    """Load LineHelper settings from project defaults and environment variables."""
    project_root = find_project_root()
    db_path = _env_path("LINEHELPER_DB_PATH", project_root / "data" / "memory" / "linehelper_memory.db")
    runtime_dir = _env_path("LINEHELPER_RUNTIME_DIR", project_root / "data" / "runtime")
    streamlit_host = os.getenv("LINEHELPER_STREAMLIT_HOST", DEFAULT_STREAMLIT_HOST)
    streamlit_port = _env_int("LINEHELPER_STREAMLIT_PORT", DEFAULT_STREAMLIT_PORT)
    return LineHelperConfig(
        project_root=project_root,
        db_path=db_path,
        model=os.getenv("OLLAMA_MODEL", DEFAULT_MODEL),
        ollama_url=os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        streamlit_host=streamlit_host,
        streamlit_port=streamlit_port,
        runtime_dir=runtime_dir,
        streamlit_app_path=project_root / "linehelper" / "ui" / "streamlit_app.py",
        organization_source_path=project_root
        / "data"
        / "raw_docs"
        / "bvr_company_structure_instruction_v2 (2).txt",
    )


def find_project_root() -> Path:
    """Find the project root without relying only on the current directory."""
    env_root = os.getenv("LINEHELPER_PROJECT_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        if _looks_like_project_root(root):
            return root
        raise LineHelperConfigError(
            f"LINEHELPER_PROJECT_ROOT points to an invalid LineHelper root: {root}"
        )

    package_root = Path(__file__).resolve().parents[1]
    if _looks_like_project_root(package_root):
        return package_root

    for candidate in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        if _looks_like_project_root(candidate):
            return candidate

    raise LineHelperConfigError(
        "Could not find LineHelper project root. Set LINEHELPER_PROJECT_ROOT to the repository path."
    )


def _looks_like_project_root(path: Path) -> bool:
    return (
        (path / "linehelper").is_dir()
        and (path / "data").is_dir()
        and ((path / "pyproject.toml").exists() or (path / "requirements.txt").exists())
    )


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if value and value.strip():
        return Path(value).expanduser().resolve()
    return default.resolve()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
