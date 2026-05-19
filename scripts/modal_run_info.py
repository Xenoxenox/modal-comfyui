from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path


APP_ID_RE = re.compile(r"\b(ap-[A-Za-z0-9_-]+)\b")
FUNCTION_CALL_ID_RE = re.compile(r"\b(fc-[A-Za-z0-9_-]+)\b")
URL_RE = re.compile(r"https://[^\s)\]>]+")


def shell_command_text(command: list[str]) -> str:
    return " ".join(command)


def modal_app_logs_command(app_id: str, function_call_id: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "modal", "app", "logs"]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "modal", "app", "logs", "-h"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        help_text = f"{result.stdout}\n{result.stderr}"
    except (OSError, subprocess.SubprocessError):
        help_text = ""

    if "--follow" in help_text:
        cmd.append("--follow")
    if function_call_id and "--function-call" in help_text:
        cmd.extend(["--function-call", function_call_id])
    cmd.append(app_id)
    return cmd


def modal_app_stop_command(app_id: str) -> list[str]:
    return [sys.executable, "-m", "modal", "app", "stop", app_id]


def parse_modal_run_info(text: str) -> dict[str, str]:
    info: dict[str, str] = {}
    if app_match := APP_ID_RE.search(text):
        info["app_id"] = app_match.group(1)
    if call_match := FUNCTION_CALL_ID_RE.search(text):
        info["function_call_id"] = call_match.group(1)
    urls = URL_RE.findall(text)
    for url in urls:
        lowered = url.lower()
        if "modal.com" in lowered:
            if "function" in lowered or "call" in lowered:
                info.setdefault("function_call_url", url)
            else:
                info.setdefault("dashboard_url", url)
        elif "modal.run" in lowered:
            info["web_url"] = url
    return info


def modal_log_path(prefix: str) -> Path:
    from datetime import datetime

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"{prefix}_{timestamp}.log"


class AppLogStreamer:
    """Streams Modal app logs to stdout while teeing them into a local file."""

    def __init__(self, app_id: str, function_call_id: str | None, local_path: Path) -> None:
        self.app_id = app_id
        self.function_call_id = function_call_id
        self.local_path = local_path
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        cmd = modal_app_logs_command(self.app_id, self.function_call_id)
        with self.local_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"# Modal App ID: {self.app_id}\n")
            if self.function_call_id:
                log.write(f"# Function Call ID: {self.function_call_id}\n")
            log.write(f"# Command: {shell_command_text(cmd)}\n\n")
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                message = f"Could not start Modal log streamer: {exc}\n"
                print(message.rstrip())
                log.write(message)
                return

            assert self._process.stdout is not None
            for line in self._process.stdout:
                if self._stop.is_set():
                    break
                print(line, end="")
                log.write(line)
                log.flush()
