from __future__ import annotations

import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

CACHE_MOUNT = "/cache"
CACHE_CUSTOM_NODES = Path(CACHE_MOUNT) / "custom_nodes"
CACHE_USER_DIR = Path(CACHE_MOUNT) / "user"


def ensure_runtime_dirs() -> None:
    """Create Volume-backed ComfyUI directories before ComfyUI scans them."""
    CACHE_CUSTOM_NODES.mkdir(parents=True, exist_ok=True)
    CACHE_USER_DIR.mkdir(parents=True, exist_ok=True)


def launch_comfy(host: str, port: int, user_directory: Path = CACHE_USER_DIR) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "comfy",
            "launch",
            "--",
            "--listen",
            host,
            "--port",
            str(port),
            "--user-directory",
            str(user_directory),
        ]
    )


def wait_for_port(host: str, port: int, timeout: float, interval: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(interval)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


class ComfySupervisor:
    """Own one foreground ComfyUI process and restart it after every exit."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        restart_delay: float = 5,
        on_exit: Callable[[], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._restart_delay = restart_delay
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        with self._lock:
            if self._process is not None:
                raise RuntimeError("ComfyUI supervisor already started")
            self._process = launch_comfy(self._host, self._port)
        threading.Thread(target=self._supervise, daemon=True).start()

    def request_restart(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def _supervise(self) -> None:
        while True:
            with self._lock:
                process = self._process
            if process is None:
                return
            returncode = process.wait()
            if self._on_exit is not None:
                self._on_exit()
            print(
                f"WARNING: ComfyUI exited with code {returncode}; "
                f"restarting in {self._restart_delay}s"
            )
            time.sleep(self._restart_delay)
            with self._lock:
                if self._process is process:
                    self._process = launch_comfy(self._host, self._port)
