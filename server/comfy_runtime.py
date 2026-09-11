from __future__ import annotations

import importlib.metadata
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

CACHE_MOUNT = "/cache"
CACHE_CUSTOM_NODES = Path(CACHE_MOUNT) / "custom_nodes"
CACHE_USER_DIR = Path(CACHE_MOUNT) / "user"
CACHE_DEFAULT_USER_DIR = CACHE_USER_DIR / "default"
CACHE_WORKFLOWS_DIR = CACHE_DEFAULT_USER_DIR / "workflows"

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def seed_workflows(seed_dir: Path, workflows_dir: Path) -> int:
    """Copy repository workflow seeds into the writable user directory."""
    if not seed_dir.is_dir():
        return 0

    copied = 0
    for source in seed_dir.rglob("*.json"):
        if not source.is_file():
            continue
        destination = workflows_dir / source.relative_to(seed_dir)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    return copied


def ensure_runtime_dirs() -> None:
    """Create Volume-backed ComfyUI directories before ComfyUI scans them."""
    CACHE_CUSTOM_NODES.mkdir(parents=True, exist_ok=True)
    CACHE_USER_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)




def missing_requirements(requirements: Path) -> list[str]:
    """Return the distributions a custom node needs that this interpreter lacks.

    Custom nodes install their Python dependencies into the container's own
    site-packages, which is discarded when the container exits; only the Volume
    survives. A dependency record stored on the Volume therefore says nothing
    about the interpreter that is about to import the node.
    """
    missing: list[str] = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement or requirement.startswith("-"):
            continue
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            continue
        name = match.group(0)
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return missing


def scannable_custom_node(path: Path) -> bool:
    """Mirror ComfyUI's scan: ``.disabled`` packs and ``__pycache__`` are not loaded."""
    if path.name == "__pycache__" or path.suffix == ".disabled":
        return False
    return path.is_dir() or path.suffix == ".py"


def validate_custom_node(path: Path) -> tuple[bool, str | None]:
    """Report whether a scanned custom-node entry can load in this interpreter.

    Only import preconditions are checked: importing the node itself would run
    arbitrary third-party side effects and would report every pack that imports
    ComfyUI modules (``from server import PromptServer``, ``import comfy``) as
    broken, because those modules only exist inside the ComfyUI process.
    """
    if path.is_file():
        return True, None
    if not (path / "__init__.py").is_file():
        return False, "missing __init__.py"
    requirements = path / "requirements.txt"
    if requirements.is_file():
        missing = missing_requirements(requirements)
        if missing:
            return False, f"missing python dependency: {', '.join(missing)}"
    return True, None


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
        ],
        start_new_session=True,
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
        """Stop the whole ComfyUI process tree so the relaunch can bind its port.

        ``comfy launch`` runs ``main.py`` as a separate child process, so signalling
        only the wrapper leaves ComfyUI orphaned and holding the listening port.
        """
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
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
