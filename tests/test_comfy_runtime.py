from __future__ import annotations

import signal
from pathlib import Path

import pytest

from server import comfy_runtime
from server.comfy_runtime import (
    CACHE_CUSTOM_NODES,
    CACHE_USER_DIR,
    CACHE_WORKFLOWS_DIR,
    ComfySupervisor,
    ensure_runtime_dirs,
    launch_comfy,
    missing_requirements,
    scannable_custom_node,
    seed_workflows,
    validate_custom_node,
)


class _FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.pid = 4321
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
    workflows = tmp_path / "workflows"
    monkeypatch.setattr(comfy_runtime, "CACHE_CUSTOM_NODES", nodes)
    monkeypatch.setattr(comfy_runtime, "CACHE_USER_DIR", users)
    monkeypatch.setattr(comfy_runtime, "CACHE_WORKFLOWS_DIR", workflows)

    ensure_runtime_dirs()

    assert nodes.is_dir()
    assert users.is_dir()
    assert workflows.is_dir()


def test_seed_workflows_missing_seed_is_a_noop(tmp_path: Path) -> None:
    assert seed_workflows(tmp_path / "missing", tmp_path / "workflows") == 0
    assert not (tmp_path / "workflows").exists()


def test_seed_workflows_copies_nested_json_only_and_preserves_existing_files(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed"
    destination = tmp_path / "workflows"
    (seed / "curated").mkdir(parents=True)
    (seed / "top.json").write_text('{"name": "top"}', encoding="utf-8")
    (seed / "curated" / "nested.json").write_text(
        '{"name": "nested"}', encoding="utf-8"
    )
    (seed / "ignored.txt").write_text("not a workflow", encoding="utf-8")
    (destination / "curated").mkdir(parents=True)
    (destination / "curated" / "nested.json").write_text(
        '{"name": "saved"}', encoding="utf-8"
    )

    assert seed_workflows(seed, destination) == 1
    assert (destination / "top.json").read_text(encoding="utf-8") == '{"name": "top"}'
    assert (destination / "curated" / "nested.json").read_text(encoding="utf-8") == (
        '{"name": "saved"}'
    )
    assert not (destination / "ignored.txt").exists()


def test_launch_comfy_serves_loopback_with_the_cache_user_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_popen(args, *rest, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
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
        str(CACHE_USER_DIR),
    ]
    assert captured["kwargs"]["start_new_session"] is True


def test_supervisor_restart_signals_the_whole_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(comfy_runtime, "launch_comfy", lambda *args, **kwargs: process)
    monkeypatch.setattr(comfy_runtime.threading, "Thread", _NoThread)
    # ``os.getpgid``/``os.killpg`` are POSIX-only; inject them so the
    # supervisor's signalling contract is exercised on every platform.
    monkeypatch.setattr(
        comfy_runtime.os, "getpgid", lambda pid: pid + 1, raising=False
    )
    monkeypatch.setattr(
        comfy_runtime.os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
        raising=False,
    )

    supervisor = ComfySupervisor("127.0.0.1", 8188)
    supervisor.start()
    supervisor.request_restart()

    assert signalled == [(process.pid + 1, signal.SIGTERM)]


def test_supervisor_restart_terminates_when_the_process_group_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    signalled: list[tuple[int, int]] = []

    def missing(pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(comfy_runtime, "launch_comfy", lambda *args, **kwargs: process)
    monkeypatch.setattr(comfy_runtime.threading, "Thread", _NoThread)
    monkeypatch.setattr(comfy_runtime.os, "getpgid", missing, raising=False)
    monkeypatch.setattr(
        comfy_runtime.os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
        raising=False,
    )

    supervisor = ComfySupervisor("127.0.0.1", 8188)
    supervisor.start()
    supervisor.request_restart()

    assert process.terminated
    assert signalled == []


def test_supervisor_restart_is_a_noop_after_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(returncode=1)
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(comfy_runtime, "launch_comfy", lambda *args, **kwargs: process)
    monkeypatch.setattr(comfy_runtime.threading, "Thread", _NoThread)
    monkeypatch.setattr(
        comfy_runtime.os, "getpgid", lambda pid: pid + 1, raising=False
    )
    monkeypatch.setattr(
        comfy_runtime.os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
        raising=False,
    )

    supervisor = ComfySupervisor("127.0.0.1", 8188)
    supervisor.start()
    supervisor.request_restart()

    assert not process.terminated
    assert signalled == []


def test_supervisor_relaunches_through_launch_comfy_after_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StopSupervising(Exception):
        pass

    launched: list[tuple[str, int]] = []

    def fake_launch(host: str, port: int, *args, **kwargs):
        if len(launched) == 2:
            raise _StopSupervising
        launched.append((host, port))
        return _FakeProcess(returncode=1)

    monkeypatch.setattr(comfy_runtime, "launch_comfy", fake_launch)
    monkeypatch.setattr(comfy_runtime.threading, "Thread", _NoThread)

    supervisor = ComfySupervisor("127.0.0.1", 8188, restart_delay=0)
    supervisor.start()
    with pytest.raises(_StopSupervising):
        supervisor._supervise()

    assert launched == [("127.0.0.1", 8188), ("127.0.0.1", 8188)]


def test_cache_paths_live_under_the_cache_mount() -> None:
    assert CACHE_CUSTOM_NODES.as_posix() == "/cache/custom_nodes"
    assert CACHE_USER_DIR.as_posix() == "/cache/user"


def test_missing_requirements_names_distributions_the_interpreter_lacks(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "# brotli - metadata decompression\n"
        "\n"
        "-r other-requirements.txt\n"
        "pytest>=8\n"
        "zzz-uninstalled-fixture >= 1  # inline comment\n",
        encoding="utf-8",
    )

    assert missing_requirements(requirements) == ["zzz-uninstalled-fixture"]


def test_missing_requirements_is_empty_when_every_distribution_is_installed(
    tmp_path: Path,
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest>=8\n", encoding="utf-8")

    assert missing_requirements(requirements) == []


def test_validate_custom_node_requires_a_package_entry(tmp_path: Path) -> None:
    node_dir = tmp_path / "pack"
    node_dir.mkdir()

    assert validate_custom_node(node_dir) == (False, "missing __init__.py")


def test_validate_custom_node_accepts_a_pack_with_installed_dependencies(
    tmp_path: Path,
) -> None:
    node_dir = tmp_path / "pack"
    node_dir.mkdir()
    (node_dir / "__init__.py").write_text("", encoding="utf-8")
    (node_dir / "requirements.txt").write_text("pytest>=8\n", encoding="utf-8")

    assert validate_custom_node(node_dir) == (True, None)


def test_validate_custom_node_accepts_a_pack_without_requirements(tmp_path: Path) -> None:
    node_dir = tmp_path / "pack"
    node_dir.mkdir()
    (node_dir / "__init__.py").write_text("", encoding="utf-8")

    assert validate_custom_node(node_dir) == (True, None)


def test_validate_custom_node_ignores_a_volume_recorded_dependency_state(
    tmp_path: Path,
) -> None:
    node_dir = tmp_path / "pack"
    node_dir.mkdir()
    (node_dir / "__init__.py").write_text("", encoding="utf-8")
    (node_dir / "requirements.txt").write_text(
        "zzz-uninstalled-fixture\n", encoding="utf-8"
    )
    (node_dir / ".deps-installed").write_text(
        "written by a previous container", encoding="utf-8"
    )

    assert validate_custom_node(node_dir) == (
        False,
        "missing python dependency: zzz-uninstalled-fixture",
    )


def test_scannable_custom_node_mirrors_the_comfyui_scan(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    single_file = tmp_path / "node.py"
    single_file.write_text("", encoding="utf-8")
    disabled = tmp_path / "old-pack.disabled"
    disabled.mkdir()
    bytecode = tmp_path / "__pycache__"
    bytecode.mkdir()
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("", encoding="utf-8")

    assert scannable_custom_node(pack) is True
    assert scannable_custom_node(single_file) is True
    assert scannable_custom_node(disabled) is False
    assert scannable_custom_node(bytecode) is False
    assert scannable_custom_node(unrelated) is False


def test_validate_custom_node_accepts_a_single_file_node(tmp_path: Path) -> None:
    node_file = tmp_path / "node.py"
    node_file.write_text("", encoding="utf-8")

    assert validate_custom_node(node_file) == (True, None)
