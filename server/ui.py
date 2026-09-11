from __future__ import annotations

import os
import subprocess
import threading

import modal

from server.comfy_runtime import (
    CACHE_CUSTOM_NODES,
    CACHE_MOUNT,
    ComfySupervisor,
    ensure_runtime_dirs,
    missing_requirements,
    scannable_custom_node,
    validate_custom_node,
    wait_for_port,
)
from server.model_manifest import (
    MANIFEST_PATH,
    sync_prepared_model_links as _sync_model_links,
)

WEB_UI_GPU = os.environ.get("COMFYUI_WEB_GPU", "L4")
COMFYUI_PORT = 8188
NGINX_PORT = 8000
NGINX_CONF = "/root/nginx.conf"
STARTUP_TIMEOUT = 55

if os.environ.get("MODAL_IS_REMOTE") == "1":
    cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)
    app = modal.App(name="modal-comfyui")
else:
    from server.app import app, cache_vol




def _commit_volume() -> None:
    try:
        cache_vol.commit()
    except Exception as exc:
        print(f"WARNING: failed to commit comfy-cache: {exc}")


def _report_unloadable_nodes() -> None:
    for node_dir in sorted(CACHE_CUSTOM_NODES.iterdir()):
        if not scannable_custom_node(node_dir):
            continue
        loadable, reason = validate_custom_node(node_dir)
        if not loadable:
            print(f"WARNING: custom node {node_dir.name} will not load: {reason}")


def _start_dep_installer(supervisor: ComfySupervisor) -> None:
    def _install() -> None:
        installed_any = False
        for node_dir in CACHE_CUSTOM_NODES.iterdir():
            if not scannable_custom_node(node_dir):
                continue
            req = node_dir / "requirements.txt"
            if not req.exists():
                continue
            missing = missing_requirements(req)
            if not missing:
                continue
            result = subprocess.run(
                ["pip", "install", "-r", str(req), "-q"],
                check=False,
            )
            if result.returncode != 0:
                print(f"WARNING: pip install failed for {req}")
                continue
            still_missing = missing_requirements(req)
            if still_missing:
                print(
                    f"WARNING: pip install for {req} left "
                    f"{', '.join(still_missing)} missing"
                )
                continue
            installed_any = True
        if installed_any:
            supervisor.request_restart()

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
    ensure_runtime_dirs()
    _sync_model_links(MANIFEST_PATH)
    _report_unloadable_nodes()
    supervisor = ComfySupervisor(
        "127.0.0.1",
        COMFYUI_PORT,
        on_exit=_commit_volume,
    )
    supervisor.start()
    _start_dep_installer(supervisor)
    wait_for_port("127.0.0.1", COMFYUI_PORT, STARTUP_TIMEOUT)
    subprocess.Popen(
        ["nginx", "-c", NGINX_CONF, "-g", "daemon off;"]
    )
