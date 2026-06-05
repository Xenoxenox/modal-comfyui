from __future__ import annotations

import os
import socket
import subprocess
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

if os.environ.get("MODAL_IS_REMOTE") == "1":
    cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)
    CACHE_MOUNT = "/cache"
    MODEL_LINK_MANIFEST = Path(CACHE_MOUNT) / ".modal-comfyui-model-links.json"
    app = modal.App(name="modal-comfyui")

    def sync_prepared_model_links() -> dict[str, Any]:
        return _sync_model_links(MODEL_LINK_MANIFEST)
else:
    from server.app import app, cache_vol, CACHE_MOUNT, sync_prepared_model_links


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(STARTUP_POLL_INTERVAL)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


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
    sync_prepared_model_links()
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
