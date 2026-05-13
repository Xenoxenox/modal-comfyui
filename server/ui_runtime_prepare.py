from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import modal


cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)

CACHE_MOUNT = "/cache"
WORKFLOW_SEED_DIR = "/root/comfy/workflow-seed"
CONFIG_PATH = "/root/config.toml"

root_dir = Path(__file__).parent.parent


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

app = modal.App(name="modal-comfyui-runtime-prepare", image=image)


@app.function(
    volumes={CACHE_MOUNT: cache_vol},
    secrets=[
        modal.Secret.from_name("ComfyUI"),
        modal.Secret.from_name("civitai-api-key"),
    ],
    timeout=60 * 60,
)
def prepare_models(dry_run: bool = True) -> dict[str, Any]:
    import tomllib

    cache_vol.reload()
    with open(CONFIG_PATH, "rb") as file:
        raw = tomllib.load(file)

    models = []
    models_snapshot = []
    models_ext = []
    skipped = []
    for key, data in raw.get("models", {}).items():
        source = data.get("source")
        if source == "huggingface":
            models.append(
                {
                    "repo_id": data["repo_id"],
                    "filename": data["filename"],
                    "model_dir": f"/root/comfy/ComfyUI/models/{data['model_dir']}",
                    "save_as": data.get("save_as"),
                }
            )
        elif source == "huggingface_snapshot":
            models_snapshot.append(
                {
                    "repo_id": data["repo_id"],
                    "target_dir": data["target_dir"],
                }
            )
        elif source == "external":
            models_ext.append(
                {
                    "url": data["url"],
                    "filename": data["filename"],
                    "model_dir": f"/root/comfy/ComfyUI/models/{data['model_dir']}",
                }
            )
        else:
            skipped.append({"key": key, "source": source})

    plugins = raw.get("plugins", {})

    plan = {
        "dry_run": dry_run,
        "huggingface_files": len(models),
        "huggingface_snapshots": len(models_snapshot),
        "external_files": len(models_ext),
        "plugins": len(plugins),
        "skipped": skipped,
        "items": {
            "huggingface_files": [
                {
                    "repo_id": model["repo_id"],
                    "filename": model["filename"],
                    "model_dir": model["model_dir"],
                    "save_as": model.get("save_as"),
                }
                for model in models
            ],
            "huggingface_snapshots": [
                {
                    "repo_id": model["repo_id"],
                    "target_dir": model["target_dir"],
                }
                for model in models_snapshot
            ],
            "external_files": [
                {
                    "filename": model["filename"],
                    "model_dir": model["model_dir"],
                    "url_host": model["url"].split("/")[2],
                }
                for model in models_ext
            ],
        },
    }

    print(f"PREPARE_MODELS_PLAN {plan}", flush=True)
    if dry_run:
        return plan

    raise NotImplementedError(
        "This implementation test only validates runtime function startup and "
        "config planning. Move download_all() logic here after dry_run passes."
    )


@app.local_entrypoint()
def prepare(dry_run: bool = True) -> None:
    result = prepare_models.remote(dry_run)
    print(result)


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
