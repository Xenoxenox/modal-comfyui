from __future__ import annotations

import os
import shutil
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
COMFY_CUSTOM_NODES = f"{COMFY_ROOT}/custom_nodes"


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(STARTUP_POLL_INTERVAL)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


def _setup_persistent_custom_nodes() -> None:
    src = Path(COMFY_CUSTOM_NODES)
    dst = Path(CACHE_CUSTOM_NODES)
    dst.mkdir(parents=True, exist_ok=True)

    seeded = False
    if src.exists() and not src.is_symlink():
        for item in src.iterdir():
            target = dst / item.name
            if target.exists():
                continue
            if item.is_dir():
                shutil.copytree(str(item), str(target))
            else:
                shutil.copy2(str(item), str(target))
            seeded = True
        shutil.rmtree(str(src))
        if seeded:
            try:
                cache_vol.commit()
            except Exception as exc:
                print(f"WARNING: custom node seed commit failed: {exc}")

    if src.is_symlink():
        src.unlink()
    src.symlink_to(str(dst))

    for node_dir in dst.iterdir():
        req = node_dir / "requirements.txt"
        if node_dir.is_dir() and req.exists():
            result = subprocess.run(
                ["pip", "install", "-r", str(req), "-q"],
                check=False,
            )
            if result.returncode != 0:
                print(f"WARNING: pip install failed for {req}")


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
    _setup_persistent_custom_nodes()
    sync_prepared_model_links()
    _start_commit_watcher()
    subprocess.Popen(
        [
            "comfy",
            "launch",
            "--background",
            "--",
            "--listen",
            "127.0.0.1",
            "--port",
            str(COMFYUI_PORT),
        ]
    )
    _wait_for_port("127.0.0.1", COMFYUI_PORT, STARTUP_TIMEOUT)
    subprocess.Popen(
        ["nginx", "-c", NGINX_CONF, "-g", "daemon off;"]
    )
