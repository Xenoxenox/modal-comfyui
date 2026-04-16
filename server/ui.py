from __future__ import annotations

import subprocess

import modal

from server.app import app, cache_vol, CACHE_MOUNT


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
        "comfy launch --background -- --listen 0.0.0.0 --port 8000", shell=True
    )
