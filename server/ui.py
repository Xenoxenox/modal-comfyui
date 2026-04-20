from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from urllib import request, error as urlerror

import modal

from server.app import (
    app,
    cache_vol,
    CACHE_MOUNT,
    COMFY_WORKFLOWS_DIR,
    WORKFLOW_SEED_DIR,
)


@app.function(
    max_containers=1,
    gpu="L4",
    volumes={CACHE_MOUNT: cache_vol},
    scaledown_window=60,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
@modal.concurrent(max_inputs=10)
@modal.web_server(8000, startup_timeout=60)
def ui():
    subprocess.Popen(
        "comfy launch --background -- --listen 127.0.0.1 --port 8188",
        shell=True,
    )

    _wait_for_comfyui("http://127.0.0.1:8188", timeout=50)
    _seed_workflows()

    subprocess.Popen("nginx -g 'daemon off;'", shell=True)


def _wait_for_comfyui(base_url: str, timeout: int = 50) -> None:
    url = f"{base_url}/system_stats"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with request.urlopen(request.Request(url), timeout=5):
                print("[ui] ComfyUI is ready")
                return
        except (urlerror.URLError, ConnectionError, OSError):
            time.sleep(1)
    raise TimeoutError(f"ComfyUI did not start within {timeout}s")


def _seed_workflows() -> None:
    seed_dir = Path(WORKFLOW_SEED_DIR)
    if not seed_dir.exists():
        return
    dest_dir = Path(COMFY_WORKFLOWS_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in seed_dir.iterdir():
        if src.suffix == ".json":
            dst = dest_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                print(f"[ui] Seeded workflow: {src.name}")
