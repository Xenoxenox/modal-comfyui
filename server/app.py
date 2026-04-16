from __future__ import annotations

import subprocess
from pathlib import Path

import modal

from models import models, models_ext, models_snapshot
from plugins import comfy_plugins

# ── Volumes ──
cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)
output_vol = modal.Volume.from_name("comfy-output", create_if_missing=True)

CACHE_MOUNT = "/cache"
OUTPUT_MOUNT = "/output"
COMFY_ROOT = "/root/comfy/ComfyUI"

root_dir = Path(__file__).parent.parent


# ── Model Download Functions ──


def hf_download(
    repo_id: str,
    filename: str,
    model_dir: str = f"{COMFY_ROOT}/models/checkpoints",
) -> None:
    import os

    from huggingface_hub import hf_hub_download

    model = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        cache_dir=CACHE_MOUNT,
        token=os.environ.get("HF_TOKEN"),
    )

    Path(model_dir).mkdir(parents=True, exist_ok=True)
    local_filename = Path(filename).name
    subprocess.run(
        f"ln -s {model} {model_dir}/{local_filename}",
        shell=True,
        check=True,
    )
    print(f"Downloaded {repo_id}/{filename} to {model_dir}/{local_filename}")


def download_external_model(url: str, filename: str, model_dir: str) -> None:
    import os

    cache_dir = CACHE_MOUNT
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    cached_path = Path(cache_dir) / filename
    if not cached_path.exists():
        print(f"Downloading {filename} from {url}...")
        # Civitai requires token as a query parameter, not a Bearer header
        download_url = url
        civitai_token = os.environ.get("CIVITAI_API_KEY")
        if civitai_token and "civitai" in url:
            separator = "&" if "?" in url else "?"
            download_url = f"{url}{separator}token={civitai_token}"
        cmd = [
            "aria2c",
            "--console-log-level=error",
            "--summary-interval=0",
            "-x", "16",
            "-s", "16",
            "-o", filename,
            "-d", cache_dir,
            download_url,
        ]
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    Path(model_dir).mkdir(parents=True, exist_ok=True)
    target_path = Path(model_dir) / filename

    if target_path.exists() or target_path.is_symlink():
        target_path.unlink()

    target_path.symlink_to(cached_path)
    print(f"Linked {filename} to {model_dir}/{filename}")


def hf_snapshot_download(
    repo_id: str,
    target_dir: str,
) -> None:
    import os

    from huggingface_hub import snapshot_download

    local_dir = snapshot_download(
        repo_id=repo_id,
        cache_dir=CACHE_MOUNT,
        token=os.environ.get("HF_TOKEN"),
    )

    Path(target_dir).parent.mkdir(parents=True, exist_ok=True)
    target_path = Path(target_dir)
    if target_path.exists() or target_path.is_symlink():
        target_path.unlink()
    target_path.symlink_to(local_dir)
    print(f"Snapshot {repo_id} → {target_dir}")


def download_all() -> None:
    for model in models:
        hf_download(model["repo_id"], model["filename"], model["model_dir"])
    for model in models_snapshot:
        hf_snapshot_download(model["repo_id"], model["target_dir"])
    for model in models_ext:
        download_external_model(model["url"], model["filename"], model["model_dir"])


# ── Image Build ──

image = (
    modal.Image.debian_slim(python_version="3.11")
    .add_local_python_source("models", "plugins", copy=True)
    .apt_install("git", "git-lfs", "libgl1-mesa-dev", "libglib2.0-0", "aria2")
    .pip_install_from_requirements(str(root_dir / "requirements_comfy.txt"))
    .run_commands("comfy --skip-prompt install --nvidia")
    .run_commands("git lfs install")
)

image = image.env({"HF_HUB_ENABLE_HF_TRANSFER": "1"}).run_function(
    download_all,
    volumes={CACHE_MOUNT: cache_vol},
    secrets=[
        modal.Secret.from_name("ComfyUI"),
        modal.Secret.from_name("civitai-api-key"),
    ],
)

# Setup custom nodes
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

if comfy_plugins:
    image = image.run_commands("comfy node install " + " ".join(comfy_plugins))


# ── App ──

app = modal.App(name="modal-comfyui", image=image)
