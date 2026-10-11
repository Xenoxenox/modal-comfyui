from __future__ import annotations

import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import modal
import pytest


def test_dependency_recovery_cannot_abort_initial_web_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fast dependency install must not kill ComfyUI before its first readiness check."""
    app_module = ModuleType("server.app")
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    app_module.WORKFLOW_SEED_DIR = str(tmp_path / "seed")
    app_module.app = SimpleNamespace(
        function=lambda **kwargs: lambda function: function
    )
    app_module.cache_vol = SimpleNamespace(reload=lambda: None)
    monkeypatch.setitem(sys.modules, "server.app", app_module)
    monkeypatch.delenv("MODAL_IS_REMOTE", raising=False)
    monkeypatch.setattr(modal, "concurrent", lambda **kwargs: lambda function: function)
    monkeypatch.setattr(
        modal, "web_server", lambda *args, **kwargs: lambda function: function
    )
    startup = runpy.run_path(str(Path(__file__).parents[1] / "server" / "ui.py"))
    launch = startup["ui"]
    namespace = launch.__globals__

    node = tmp_path / "custom_nodes" / "example"
    node.mkdir(parents=True)
    (node / "__init__.py").touch()
    (node / "requirements.txt").write_text("example-dependency\n", encoding="utf-8")
    monkeypatch.setitem(namespace, "CACHE_CUSTOM_NODES", node.parent)
    monkeypatch.setitem(namespace, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setitem(namespace, "_sync_model_links", lambda path: None)

    state = {"running": False, "ready": False, "installed": False, "restarts": 0}

    class Supervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            state["running"] = True

        def request_restart(self) -> None:
            state["running"] = False
            state["restarts"] += 1

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    def wait_for_port(*args: object) -> None:
        if not state["running"]:
            raise TimeoutError("ComfyUI was killed before becoming ready")
        state["ready"] = True

    def install(*args: object, **kwargs: object) -> SimpleNamespace:
        state["installed"] = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setitem(namespace, "ComfySupervisor", Supervisor)
    monkeypatch.setitem(namespace, "wait_for_port", wait_for_port)
    monkeypatch.setitem(
        namespace,
        "missing_requirements",
        lambda path: [] if state["installed"] else ["example-dependency"],
    )
    monkeypatch.setattr(namespace["threading"], "Thread", ImmediateThread)
    monkeypatch.setattr(namespace["subprocess"], "run", install)
    monkeypatch.setattr(
        namespace["subprocess"], "Popen", lambda *args, **kwargs: SimpleNamespace()
    )

    launch()

    assert state["ready"]
    assert state["installed"]
    assert state["restarts"] == 1
