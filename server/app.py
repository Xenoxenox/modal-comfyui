from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import modal

# ── Volumes ──
cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)
output_vol = modal.Volume.from_name("comfy-output", create_if_missing=True)

CACHE_MOUNT = "/cache"
OUTPUT_MOUNT = "/output"
COMFY_ROOT = "/root/comfy/ComfyUI"
COMFY_ROOT_PATH = Path(COMFY_ROOT)
COMFY_DEFAULT_USER_DIR = COMFY_ROOT_PATH / "user" / "default"
COMFY_WORKFLOWS_DIR = COMFY_DEFAULT_USER_DIR / "workflows"
WORKFLOW_SEED_DIR = "/root/comfy/workflow-seed"
CONFIG_PATH = "/root/config.toml"
MODEL_LINK_MANIFEST = Path(CACHE_MOUNT) / ".modal-comfyui-model-links.json"

root_dir = Path(__file__).parent.parent


# ── Model Download Functions ──


def _target_name(filename: str, save_as: str | None = None) -> str:
    return save_as if save_as else Path(filename).name


def _link_cached_path(source_path: Path, target_path: Path) -> str:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() or target_path.is_symlink():
        target_path.unlink()
    target_path.symlink_to(source_path)
    return str(target_path)


def _manifest_item(source_path: Path, target_path: Path, source: str) -> dict[str, str]:
    return {
        "source": source,
        "cache_path": str(source_path),
        "target_path": str(target_path),
    }


def _write_model_link_manifest(items: list[dict[str, str]]) -> None:
    MODEL_LINK_MANIFEST.write_text(json.dumps(items, indent=2), encoding="utf-8")


def sync_prepared_model_links() -> dict[str, Any]:
    if not MODEL_LINK_MANIFEST.exists():
        print(
            "SKIP model link sync: no prepared model manifest found. "
            "Run `modal run server/app.py::prepare` first if models are missing.",
            flush=True,
        )
        return {"status": "missing_manifest", "linked": 0, "missing": []}

    try:
        items = json.loads(MODEL_LINK_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"SKIP model link sync: invalid manifest: {exc}", flush=True)
        return {"status": "invalid_manifest", "linked": 0, "missing": []}

    missing: list[dict[str, str]] = []
    linked = 0
    for item in items:
        source_path = Path(item["cache_path"])
        target_path = Path(item["target_path"])
        if not source_path.exists():
            missing.append(item)
            print(f"SKIP model link missing cache path: {source_path}", flush=True)
            continue
        _link_cached_path(source_path, target_path)
        linked += 1

    print(f"DONE model link sync: linked={linked} missing={len(missing)}", flush=True)
    return {"status": "ok", "linked": linked, "missing": missing}


def hf_download(
    repo_id: str,
    filename: str,
    model_dir: str = f"{COMFY_ROOT}/models/checkpoints",
    save_as: str | None = None,
    force: bool = False,
) -> dict[str, str]:
    import os

    from huggingface_hub import hf_hub_download

    print(f"START huggingface {repo_id}/{filename}", flush=True)
    cached = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        cache_dir=CACHE_MOUNT,
        token=os.environ.get("HF_TOKEN"),
        force_download=force,
    )

    target = Path(model_dir) / _target_name(filename, save_as)
    _link_cached_path(Path(cached), target)
    print(f"DONE huggingface {repo_id}/{filename} -> {target}", flush=True)
    return _manifest_item(Path(cached), target, "huggingface")


def download_external_model(
    url: str,
    filename: str,
    model_dir: str,
    force: bool = False,
) -> dict[str, str]:
    import os

    cache_dir = CACHE_MOUNT
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    cached_path = Path(cache_dir) / filename
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    if force or not cached_path.exists():
        print(f"START external {filename} from {url}", flush=True)
        civitai_token = os.environ.get("CIVITAI_API_KEY")
        # CivitAI: append token as query param (official method per docs)
        if civitai_token and "civitai" in url:
            separator = "&" if "?" in url else "?"
            download_url = f"{url}{separator}token={civitai_token}"
        else:
            download_url = url
        cmd = ["wget", "--content-disposition", "-O", str(cached_path), download_url]
        subprocess.run(cmd, check=True)
    else:
        print(f"SKIP external cached {cached_path}", flush=True)

    target_path = Path(model_dir) / filename
    _link_cached_path(cached_path, target_path)
    print(f"DONE external {filename} -> {target_path}", flush=True)
    return _manifest_item(cached_path, target_path, "external")


def link_local_model(
    filename: str,
    model_dir: str,
    save_as: str | None = None,
) -> dict[str, str]:
    cached_path = Path(CACHE_MOUNT) / filename
    if not cached_path.exists():
        raise FileNotFoundError(
            f"Local model is not uploaded in comfy-cache: {cached_path}"
        )

    print(f"START local {filename}", flush=True)
    target_path = Path(model_dir) / _target_name(filename, save_as)
    _link_cached_path(cached_path, target_path)
    print(f"DONE local {filename} -> {target_path}", flush=True)
    return _manifest_item(cached_path, target_path, "local")


def hf_snapshot_download(
    repo_id: str,
    target_dir: str,
    force: bool = False,
) -> dict[str, str]:
    import os

    from huggingface_hub import snapshot_download

    print(f"START huggingface_snapshot {repo_id}", flush=True)
    local_dir = snapshot_download(
        repo_id=repo_id,
        cache_dir=CACHE_MOUNT,
        token=os.environ.get("HF_TOKEN"),
        force_download=force,
    )

    target_path = Path(target_dir)
    _link_cached_path(Path(local_dir), target_path)
    print(f"DONE huggingface_snapshot {repo_id} -> {target_dir}", flush=True)
    return _manifest_item(Path(local_dir), target_path, "huggingface_snapshot")


def _model_plan_summary(
    models: list[dict],
    models_snapshot: list[dict],
    models_ext: list[dict],
    models_local: list[dict],
) -> dict[str, Any]:
    return {
        "huggingface": [
            {
                "repo_id": model["repo_id"],
                "filename": model["filename"],
                "model_dir": model["model_dir"],
                "save_as": model.get("save_as"),
            }
            for model in models
        ],
        "huggingface_snapshot": [
            {
                "repo_id": model["repo_id"],
                "target_dir": model["target_dir"],
            }
            for model in models_snapshot
        ],
        "external": [
            {
                "filename": model["filename"],
                "model_dir": model["model_dir"],
                "url": model["url"],
            }
            for model in models_ext
        ],
        "local": [
            {
                "filename": model["filename"],
                "model_dir": model["model_dir"],
                "save_as": model.get("save_as"),
            }
            for model in models_local
        ],
    }


def prepare_model_links(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    from config.loader import load_config, to_legacy
    from pathlib import Path

    cfg = load_config(Path(CONFIG_PATH))
    models, models_snapshot, models_ext, models_local, _ = to_legacy(cfg)
    plan = _model_plan_summary(models, models_snapshot, models_ext, models_local)
    counts = {key: len(value) for key, value in plan.items()}
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "force": force,
        "counts": counts,
        "items": plan,
        "links": [],
    }

    print(f"START prepare_models dry_run={dry_run} force={force} counts={counts}", flush=True)
    if dry_run:
        print("DONE prepare_models dry_run", flush=True)
        return summary

    links: list[dict[str, str]] = []
    for model in models:
        links.append(
            hf_download(
                model["repo_id"],
                model["filename"],
                model["model_dir"],
                model.get("save_as"),
                force=force,
            )
        )
    for model in models_snapshot:
        links.append(
            hf_snapshot_download(
                model["repo_id"],
                model["target_dir"],
                force=force,
            )
        )
    for model in models_ext:
        links.append(
            download_external_model(
                model["url"],
                model["filename"],
                model["model_dir"],
                force=force,
            )
        )
    for model in models_local:
        links.append(
            link_local_model(
                model["filename"],
                model["model_dir"],
                model.get("save_as"),
            )
        )

    _write_model_link_manifest(links)
    summary["links"] = links
    summary["linked"] = len(links)
    print(f"DONE prepare_models linked={len(links)} manifest={MODEL_LINK_MANIFEST}", flush=True)
    return summary


def download_all() -> None:
    prepare_model_links(dry_run=False, force=False)


# ── Read config for build-time plugin list ──
def _get_plugins() -> list[str]:
    try:
        from config.loader import load_config, to_legacy
        cfg = load_config(root_dir / "config.toml")
        _, _, _, _, plugins = to_legacy(cfg)
        return plugins
    except Exception:
        return []


# ── Image Build ──

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

# Install custom nodes from config
for _plugin_id in _get_plugins():
    image = image.run_commands(f"comfy node install {_plugin_id}")

# Setup custom nodes from workflow deps
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

# Copy workflows into image so they appear in ComfyUI's workflow browser
workflows_dir = root_dir / "workflows"
if workflows_dir.exists():
    image = image.add_local_dir(
        str(workflows_dir),
        str(WORKFLOW_SEED_DIR),
        copy=True,
    )


app = modal.App(name="modal-comfyui", image=image)


@app.function(
    volumes={CACHE_MOUNT: cache_vol},
    secrets=[
        modal.Secret.from_name("ComfyUI"),
        modal.Secret.from_name("civitai-api-key"),
    ],
    timeout=60 * 60,
)
def prepare_models(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    cache_vol.reload()
    summary = prepare_model_links(dry_run=dry_run, force=force)
    if not dry_run:
        cache_vol.commit()
    return summary


@app.local_entrypoint()
def prepare(dry_run: bool = False, force: bool = False) -> None:
    result = prepare_models.remote(dry_run=dry_run, force=force)
    print(json.dumps(result, indent=2))
