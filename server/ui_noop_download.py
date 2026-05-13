from __future__ import annotations

import subprocess
from pathlib import Path

import modal


cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)

CACHE_MOUNT = "/cache"
WORKFLOW_SEED_DIR = "/root/comfy/workflow-seed"
CONFIG_PATH = "/root/config.toml"

root_dir = Path(__file__).parent.parent


def noop_download() -> None:
    print("NOOP_DOWNLOAD_START", flush=True)
    print("NOOP_DOWNLOAD_DONE", flush=True)


def _get_plugins() -> list[str]:
    try:
        from config.loader import load_config, to_legacy

        cfg = load_config(root_dir / "config.toml")
        _, _, _, plugins = to_legacy(cfg)
        return plugins
    except Exception:
        return []


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "git-lfs", "libgl1-mesa-dev", "libglib2.0-0", "aria2", "wget")
    .pip_install_from_requirements(str(root_dir / "requirements_comfy.txt"))
    .run_commands("comfy --skip-prompt install --nvidia")
    .run_commands("git lfs install")
    .add_local_python_source("config", copy=True)
    .add_local_file(str(root_dir / "config.toml"), CONFIG_PATH, copy=True)
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

image = image.run_function(
    noop_download,
    volumes={CACHE_MOUNT: cache_vol},
)

for _plugin_id in _get_plugins():
    image = image.run_commands(f"comfy node install {_plugin_id}")

workflow_file_path = root_dir / "workflow_api.json"
if workflow_file_path.exists():
    image = (
        image.add_local_file(workflow_file_path, "/root/workflow_api.json", copy=True)
        .run_commands("comfy node install-deps --workflow=/root/workflow_api.json")
    )
else:
    print(
        f"Warning: {workflow_file_path} not found. "
        "API endpoint might not work without a workflow."
    )

workflows_dir = root_dir / "workflows"
if workflows_dir.exists():
    image = image.add_local_dir(
        str(workflows_dir),
        str(WORKFLOW_SEED_DIR),
        copy=True,
    )

app = modal.App(name="modal-comfyui-noop-download", image=image)


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
def ui() -> None:
    subprocess.Popen(
        "comfy launch --background -- --listen 0.0.0.0 --port 8000", shell=True
    )
