from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import modal

from server.model_manifest import sync_prepared_model_links as _sync_model_links

WEB_UI_GPU = os.environ.get("COMFYUI_WEB_GPU", "L4")
COMFYUI_PORT = 8188
NGINX_PORT = 8000
NGINX_CONF = "/root/nginx.conf"
STARTUP_POLL_INTERVAL = 0.5
STARTUP_TIMEOUT = 55
COMFY_ROOT = "/root/comfy/ComfyUI"
COMMIT_QUIET_SECS = 30
COMFY_RESTART_DELAY = 5

if os.environ.get("MODAL_IS_REMOTE") == "1":
    cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)
    CACHE_MOUNT = "/cache"
    MODEL_LINK_MANIFEST = Path(CACHE_MOUNT) / ".modal-comfyui-model-links.json"
    app = modal.App(name="modal-comfyui")

    def sync_prepared_model_links() -> dict[str, Any]:
        return _sync_model_links(MODEL_LINK_MANIFEST)
else:
    from server.app import app, cache_vol, CACHE_MOUNT, sync_prepared_model_links

CACHE_CUSTOM_NODES = f"{CACHE_MOUNT}/custom_nodes"


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(STARTUP_POLL_INTERVAL)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


def _ensure_experimental_nodes_dir() -> None:
    Path(CACHE_CUSTOM_NODES).mkdir(parents=True, exist_ok=True)
    default_nodes = Path(COMFY_ROOT) / "custom_nodes"
    blessed_nodes = Path(COMFY_ROOT) / "blessed_custom_nodes"
    if default_nodes.is_dir() and not default_nodes.is_symlink():
        default_nodes.rename(blessed_nodes)
    if not default_nodes.exists():
        default_nodes.symlink_to(CACHE_CUSTOM_NODES)


def _start_commit_watcher() -> None:
    def _watcher() -> None:
        root = Path(CACHE_CUSTOM_NODES)

        def _snapshot() -> dict[str, float]:
            try:
                return {p.name: p.stat().st_mtime for p in root.iterdir() if p.is_dir()}
            except Exception:
                return {}

        seen = _snapshot()
        pending: float | None = None
        while True:
            time.sleep(5)
            now = _snapshot()
            if now != seen:
                seen = now
                pending = time.monotonic()
            if pending is not None and time.monotonic() - pending >= COMMIT_QUIET_SECS:
                try:
                    cache_vol.commit()
                except Exception:
                    pass
                pending = None

    threading.Thread(target=_watcher, daemon=True).start()


def _start_comfy_process() -> subprocess.Popen:
    return subprocess.Popen(
        [
            "comfy",
            "launch",
            "--",
            "--listen",
            "127.0.0.1",
            "--port",
            str(COMFYUI_PORT),
        ]
    )


def _start_comfy_supervisor() -> list[subprocess.Popen]:
    process_ref = [_start_comfy_process()]

    def _supervise() -> None:
        while True:
            returncode = process_ref[0].wait()
            print(
                f"WARNING: ComfyUI exited with code {returncode}; "
                f"restarting in {COMFY_RESTART_DELAY}s"
            )
            time.sleep(COMFY_RESTART_DELAY)
            process_ref[0] = _start_comfy_process()

    threading.Thread(target=_supervise, daemon=True).start()
    return process_ref


def _start_dep_installer(process_ref: list[subprocess.Popen]) -> None:
    def _install() -> None:
        root = Path(CACHE_CUSTOM_NODES)
        any_installed = False
        for node_dir in root.iterdir():
            if not node_dir.is_dir():
                continue
            req = node_dir / "requirements.txt"
            if not req.exists():
                continue
            marker = node_dir / ".deps-installed"
            current_hash = hashlib.sha256(req.read_bytes()).hexdigest()
            if marker.exists() and marker.read_text().strip() == current_hash:
                continue
            result = subprocess.run(
                ["pip", "install", "-r", str(req), "-q"],
                check=False,
            )
            if result.returncode == 0:
                marker.write_text(current_hash)
                any_installed = True
            else:
                print(f"WARNING: pip install failed for {req}")
        if any_installed:
            process_ref[0].terminate()

    threading.Thread(target=_install, daemon=True).start()


@app.function(
    max_containers=1,
    gpu=WEB_UI_GPU,
    volumes={CACHE_MOUNT: cache_vol},
    scaledown_window=60,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
@modal.concurrent(max_inputs=10)
@modal.web_server(NGINX_PORT, startup_timeout=60)
def ui():
    cache_vol.reload()
    _ensure_experimental_nodes_dir()
    sync_prepared_model_links()
    _start_commit_watcher()
    process_ref = _start_comfy_supervisor()
    _start_dep_installer(process_ref)
    _wait_for_port("127.0.0.1", COMFYUI_PORT, STARTUP_TIMEOUT)
    subprocess.Popen(
        ["nginx", "-c", NGINX_CONF, "-g", "daemon off;"]
    )
