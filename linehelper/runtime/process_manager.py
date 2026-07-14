"""Start, stop, and inspect the Streamlit runtime process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from linehelper.config import LineHelperConfig


PID_FILE_NAME = "linehelper.pid"
LOG_FILE_NAME = "linehelper.log"
METADATA_FILE_NAME = "linehelper.runtime.json"


@dataclass(frozen=True)
class RuntimeStatus:
    running: bool
    pid: int | None
    ui_url: str
    pid_file: Path
    log_file: Path
    verified_linehelper_process: bool


def start_foreground(
    config: LineHelperConfig,
    *,
    host: str,
    port: int,
    no_browser: bool,
) -> int:
    """Run Streamlit in the foreground and forward Ctrl+C to the child process."""
    command = build_streamlit_command(config, host=host, port=port, headless=no_browser)
    process = subprocess.Popen(command, cwd=config.project_root)
    try:
        return process.wait()
    except KeyboardInterrupt:
        _terminate_process_tree(process.pid, verified=True)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process.pid, verified=True)
            process.wait()
        return 130


def start_background(
    config: LineHelperConfig,
    *,
    host: str,
    port: int,
    no_browser: bool,
) -> RuntimeStatus:
    """Start Streamlit as a detached background process and persist runtime files."""
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file(config)
    log_file = _log_file(config)
    metadata_file = _metadata_file(config)

    existing_pid = read_pid(pid_file)
    if existing_pid is not None and is_process_active(existing_pid):
        verified = is_linehelper_streamlit_process(existing_pid, config)
        if verified:
            metadata = _read_metadata(metadata_file)
            return RuntimeStatus(
                running=True,
                pid=existing_pid,
                ui_url=metadata.get("ui_url") or ui_url(host, port),
                pid_file=pid_file,
                log_file=log_file,
                verified_linehelper_process=True,
            )
        _remove_runtime_files(pid_file=pid_file, metadata_file=metadata_file)
    elif pid_file.exists():
        _remove_runtime_files(pid_file=pid_file, metadata_file=metadata_file)

    command = build_streamlit_command(config, host=host, port=port, headless=True or no_browser)
    log_handle = log_file.open("a", encoding="utf-8")
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    process = subprocess.Popen(
        command,
        cwd=config.project_root,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    log_handle.close()

    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    metadata_file.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "host": host,
                "port": port,
                "ui_url": ui_url(host, port),
                "log_file": str(log_file),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return RuntimeStatus(
        running=True,
        pid=process.pid,
        ui_url=ui_url(host, port),
        pid_file=pid_file,
        log_file=log_file,
        verified_linehelper_process=True,
    )


def stop_background(config: LineHelperConfig) -> tuple[bool, str]:
    """Stop only the background process recorded by LineHelper runtime files."""
    pid_file = _pid_file(config)
    metadata_file = _metadata_file(config)
    pid = read_pid(pid_file)
    if pid is None:
        _remove_runtime_files(pid_file=pid_file, metadata_file=metadata_file)
        return False, "LineHelper не запущен в фоновом режиме."

    if not is_process_active(pid):
        _remove_runtime_files(pid_file=pid_file, metadata_file=metadata_file)
        return False, "LineHelper не запущен в фоновом режиме."

    if not is_linehelper_streamlit_process(pid, config):
        return False, (
            "PID-файл указывает на другой активный процесс. "
            "LineHelper не стал его завершать."
        )

    _terminate_process_tree(pid, verified=True)
    _remove_runtime_files(pid_file=pid_file, metadata_file=metadata_file)
    return True, f"LineHelper остановлен. PID: {pid}"


def get_runtime_status(config: LineHelperConfig) -> RuntimeStatus:
    """Return current background runtime status without changing the database."""
    pid_file = _pid_file(config)
    log_file = _log_file(config)
    metadata = _read_metadata(_metadata_file(config))
    host = str(metadata.get("host") or config.streamlit_host)
    port = int(metadata.get("port") or config.streamlit_port)
    pid = read_pid(pid_file)
    if pid is None:
        return RuntimeStatus(False, None, ui_url(host, port), pid_file, log_file, False)

    running = is_process_active(pid)
    verified = running and is_linehelper_streamlit_process(pid, config)
    return RuntimeStatus(running and verified, pid, ui_url(host, port), pid_file, log_file, verified)


def build_streamlit_command(
    config: LineHelperConfig,
    *,
    host: str,
    port: int,
    headless: bool,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(config.streamlit_app_path),
        "--server.port",
        str(port),
        "--server.address",
        host,
        "--server.headless",
        "true" if headless else "false",
    ]


def read_pid(pid_file: Path) -> int | None:
    try:
        value = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(value)
    except ValueError:
        return None
    return pid if pid > 0 else None


def is_process_active(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        if os.name != "nt":
            return False
        return _windows_command_line(pid) is not None
    return True


def is_linehelper_streamlit_process(pid: int, config: LineHelperConfig) -> bool:
    command_line = _process_command_line(pid)
    if not command_line:
        return False
    normalized = command_line.replace("\\", "/").casefold()
    app_path = str(config.streamlit_app_path).replace("\\", "/").casefold()
    return (
        "streamlit" in normalized
        and (
            app_path in normalized
            or "linehelper/ui/streamlit_app.py" in normalized
        )
    )


def ui_url(host: str, port: int) -> str:
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"http://{display_host}:{port}"


def _terminate_process_tree(pid: int, *, verified: bool) -> None:
    if not verified:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _kill_process_tree(pid: int, *, verified: bool) -> None:
    if not verified:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _process_command_line(pid: int) -> str | None:
    if os.name == "nt":
        return _windows_command_line(pid)
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        data = proc_cmdline.read_bytes()
    except OSError:
        data = b""
    if data:
        return data.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    return completed.stdout.strip() or None


def _windows_command_line(pid: int) -> str | None:
    command = (
        f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    return completed.stdout.strip() or None


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _remove_runtime_files(*, pid_file: Path, metadata_file: Path) -> None:
    for path in (pid_file, metadata_file):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _pid_file(config: LineHelperConfig) -> Path:
    return config.runtime_dir / PID_FILE_NAME


def _log_file(config: LineHelperConfig) -> Path:
    return config.runtime_dir / LOG_FILE_NAME


def _metadata_file(config: LineHelperConfig) -> Path:
    return config.runtime_dir / METADATA_FILE_NAME
