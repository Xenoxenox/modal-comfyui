from __future__ import annotations

from pathlib import Path

import pytest

from server import comfy_runtime
from server.comfy_runtime import (
    CACHE_CUSTOM_NODES,
    CACHE_USER_DIR,
    ComfySupervisor,
    ensure_runtime_dirs,
    launch_comfy,
)


class _FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0


class _NoThread:
    """Keeps ComfySupervisor.start() from launching the restart loop."""

    def __init__(self, target=None, daemon=None) -> None:
        self.target = target

    def start(self) -> None:
        return None


def test_ensure_runtime_dirs_creates_volume_backed_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = tmp_path / "custom_nodes"
    users = tmp_path / "user"
    monkeypatch.setattr(comfy_runtime, "CACHE_CUSTOM_NODES", nodes)
    monkeypatch.setattr(comfy_runtime, "CACHE_USER_DIR", users)

    ensure_runtime_dirs()

    assert nodes.is_dir()
    assert users.is_dir()


def test_launch_comfy_serves_loopback_with_the_cache_user_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_popen(args, *rest, **kwargs):
        captured["args"] = args
        return _FakeProcess()

    monkeypatch.setattr(comfy_runtime.subprocess, "Popen", fake_popen)

    launch_comfy("127.0.0.1", 8188)

    assert captured["args"] == [
        "comfy",
        "launch",
        "--",
        "--listen",
        "127.0.0.1",
        "--port",
        "8188",
        "--user-directory",
        CACHE_USER_DIR.as_posix(),
    ]


def test_supervisor_restart_terminates_the_running_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(comfy_runtime, "launch_comfy", lambda *args, **kwargs: process)
    monkeypatch.setattr(comfy_runtime.threading, "Thread", _NoThread)

    supervisor = ComfySupervisor("127.0.0.1", 8188)
    supervisor.start()
    supervisor.request_restart()

    assert process.terminated


def test_supervisor_restart_is_a_noop_after_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(returncode=1)
    monkeypatch.setattr(comfy_runtime, "launch_comfy", lambda *args, **kwargs: process)
    monkeypatch.setattr(comfy_runtime.threading, "Thread", _NoThread)

    supervisor = ComfySupervisor("127.0.0.1", 8188)
    supervisor.start()
    supervisor.request_restart()

    assert not process.terminated


def test_cache_paths_live_under_the_cache_mount() -> None:
    assert CACHE_CUSTOM_NODES.as_posix() == "/cache/custom_nodes"
    assert CACHE_USER_DIR.as_posix() == "/cache/user"
