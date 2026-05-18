# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Run ComfyUI on Modal with two modes: **Web UI** (browser-based workflow design) and **Headless Inference** (local client submits JSON workflow → cloud GPU executes → results downloaded). The local machine only orchestrates; all GPU work happens in Modal containers.

## Commands

```bash
uv sync                        # install dependencies (modal + questionary + rich)
modal setup                    # authenticate with Modal (one-time)
python manage.py               # interactive manager for Run ComfyUI, config, deploy, volumes
python serve.py --gpu L4       # dev Web UI — auto-cleans old apps, logs to logs/modal_serve_[timestamp].log
python serve.py --empty --gpu T4  # empty workflow-editing Web UI in Modal Environment "empty"
modal serve server/ui.py       # dev Web UI (manual)
python -m scripts.deploy_ui --gpu L4  # production Web UI — persistent endpoint
python -m scripts.deploy_ui --empty --gpu T4  # persistent empty workflow-editing endpoint
python -m client.infer         # headless inference — interactive GPU/workflow selection
python -m client.watch <url>   # local watcher — download new Web UI outputs into output/
python -m scripts.manage_volumes  # manage Modal Volumes (tree/list/clean)
```

No test framework or linter is configured in this repo.

## Before Running Any Modal Command

`server/app.py` reads model and plugin config from `config.toml` (gitignored). Copy the example and edit it:

```bash
cp config.toml.example config.toml
```

Optionally place a `workflow_api.json` at the repo root or workflow JSON files in `workflows/`. If `workflow_api.json` is present, the image build automatically installs its required custom nodes via `comfy node install-deps`.

## Empty Workflow-Editing Mode

Use empty mode when you want to open ComfyUI cheaply to organize workflows
without loading your normal model/plugin config:

```bash
python serve.py --empty --gpu T4
```

Empty mode sets `COMFYUI_CONFIG_PROFILE=empty`, bakes the tracked
`config.empty.toml`, and explicitly uses Modal Environment `empty`. Modal
Environments isolate Apps, Secrets, and Storage, so `comfy-cache` and
`comfy-output` are same-named but separate resources in empty mode. Empty mode
also skips prepare-time Modal secrets, so the `empty` Environment does not need
the normal `ComfyUI` or `civitai-api-key` secrets. This matters because
`server/ui.py` syncs the prepared model manifest at startup; an empty config
without Environment isolation could still re-link old prepared models.

Persistent empty deploy is supported:

```bash
python -m scripts.deploy_ui --empty --gpu T4
```

## Local-Only Helper Files

- `scripts/report_workflow_issue.py` and `scripts/run_and_report.py` are local troubleshooting helpers.
- Do not commit them again; they are now ignored in `.gitignore`.
- Because they were already committed once, removing them from future pushes requires a later cleanup commit with `git rm --cached`.

## Modal Secrets for Model Prepare

`modal run server/app.py::prepare` mounts secrets for private model downloads. Secret names are configurable with local environment variables or gitignored `config.toml` `[modal.secrets]`, while the keys inside Modal stay fixed:

| Environment variable | Default Modal secret | Key inside secret | Purpose |
|----------------------|----------------------|-------------------|---------|
| `MODAL_HF_SECRET_NAME` | `ComfyUI` | `HF_TOKEN` | Hugging Face token for gated models |
| `MODAL_CIVITAI_SECRET_NAME` | `civitai-api-key` | `CIVITAI_API_KEY` | CivitAI download token |

Create secrets: `modal secret create <name> KEY=value`

The TUI writes only non-sensitive Secret names to `config.toml`; tokens stay in
Modal Secrets. Local environment variables override `config.toml` when set.

If a configured Modal secret is missing in the active Modal Environment,
prepare skips mounting it and continues. This supports public Hugging Face
models and configs that do not use CivitAI. Private or gated downloads still
require the matching Modal secret. Set either env var to an empty string,
`none`, or `false` to skip mounting that secret explicitly. Do not put token
values in `config.toml`, docs, or logs.

## Windows / Encoding Notes

`modal serve` outputs Unicode (✓ ✨) that breaks Windows GBK terminal. Prefer the local launcher:

```bash
python serve.py --gpu L4   # handles encoding + auto-cleans stuck apps
```

If a `modal serve` gets stuck on `Running app...`, a previous ephemeral app is blocking the slot:

```bash
modal app list                    # find ephemeral app ID
modal app stop <app-id>           # stop it
python serve.py --gpu L4           # restart
```

## Architecture

```
modal-comfyui/
├── client/              # Local client code (runs on your machine)
│   ├── infer.py         # Headless inference entry-point (questionary → Modal)
│   ├── watch.py         # Polls Web UI history/view and downloads new images to local output/
│   └── utils.py         # Logging, UTF-8 fix, workflow loading, result download
├── server/              # Remote code (runs inside Modal containers)
│   ├── app.py           # Modal App, Image, Volumes, model download functions
│   ├── ui.py            # Web UI Function (@modal.web_server)
│   ├── generate.py      # Headless inference logic (called via serialized=True)
│   └── comfy_wrapper.py # ComfyUI subprocess management & HTTP API wrapper
├── config/              # Config schema and loader (copied into image)
│   ├── schema.py        # ModelSpec, PluginSpec, Config dataclasses
│   └── loader.py        # load_config(), save_config(), to_legacy()
├── scripts/
│   └── manage_volumes.py  # Volume tree/listing and cleanup
├── serve.py             # Convenience launcher: selects GPU, cleans stuck apps, starts modal serve
├── workflows/           # Workflow JSON files (copied into image at build time)
│   └── newbie-official.json  # NewBie image Exp0.1 official workflow
├── config.toml          # (gitignored) Models + plugins config
├── config.toml.example  # Example config template
```

### Volumes

| Volume | Mount Point | Contents |
|--------|------------|----------|
| `comfy-cache` | `/cache` | HF model cache, external model downloads, custom nodes |
| `comfy-output` | `/output` | Generated images (currently unused — output not yet wired) |

The table above describes a single Modal Environment. Empty mode uses the
`empty` Environment, where same-named Volumes are isolated from the normal
environment.

### Modal Image Build Pipeline (`server/app.py`)

The image is built in layers and cached by Modal:

1. `debian_slim(python_version="3.11")` — **image uses Python 3.11, but local `pyproject.toml` requires `>=3.13`; this mismatch is intentional.**
2. `apt_install` + `pip_install_from_requirements` (`comfy-cli`, `huggingface_hub`, `wget`)
3. `comfy --skip-prompt install --nvidia` — installs ComfyUI into the image
4. `add_local_python_source("config")` + `add_local_file("config.toml")` — config baked in **after** heavy layers so model/plugin changes don't bust apt/pip/comfy cache
5. `prepare_models` runs on demand via `modal run server/app.py::prepare` against `comfy-cache` — reads `config.toml`, downloads models and symlinks them into ComfyUI model dirs; does **not** copy files
6. If plugins defined in `config.toml`: `comfy node install` for each plugin ID
7. `workflows/` directory is copied into `/root/comfy/workflow-seed/`
8. `server/nginx.conf` is copied to `/root/nginx.conf`; nginx is installed in the image and runs in front of ComfyUI for the Web UI.

### Model & Plugin Config (`config.toml`)

All models and plugins are defined in `config.toml` (gitignored, copy from `config.toml.example`):

```toml
[models.my-checkpoint]
source = "huggingface"
repo_id = "org/repo"
filename = "model.safetensors"
model_dir = "checkpoints"   # relative to ComfyUI/models/
save_as = "renamed.safetensors"  # optional

[models.my-lora]
source = "external"
url = "https://civitai.com/api/download/models/..."
filename = "my-lora.safetensors"
model_dir = "loras"

[plugins.my-nodes]
node_id = "comfyui-my-nodes"   # or use repo = "https://github.com/..."
```

### Currently Configured Models

| Model | Type | ComfyUI Path |
|-------|------|-------------|
| Illustrious-XL-v0.1 | SDXL checkpoint | `models/checkpoints/` |
| NewBie-image-Exp0.1 transformer | UNET | `models/unet/newbie01.safetensors` |
| NewBie gemma3-4b-it | CLIP text encoder | `models/clip/gemma3-4b-it.safetensors` |
| NewBie jina-clip-v2 | CLIP text encoder | `models/clip/jina-clip-v2.safetensors` |
| NewBie VAE | VAE | `models/vae/diffusion_pytorch_model.safetensors` |
| NewBie void LoRA | LoRA | `models/loras/newbie-void-v1.0.safetensors` |

### NewBie Workflow Notes

`workflows/newbie-official.json` uses:
- `UNETLoader` → `newbie01.safetensors`
- `DualCLIPLoader` → `gemma3-4b-it.safetensors` + `jina-clip-v2.safetensors` (type: `newbie`)
- `VAELoader` → `diffusion_pytorch_model.safetensors`
- `SaveImage` → prefix `newbie`
- Trigger words for void LoRA: `void,anime_style` at weight 1.0

### Web UI (`server/ui.py`)

- `@modal.web_server(8000)` exposes nginx, not ComfyUI directly.
- ComfyUI listens on `127.0.0.1:8188`; nginx listens on `8000` and proxies HTTP plus `/ws` WebSocket traffic to ComfyUI.
- `server/nginx.conf` includes a targeted workflow userdata rule that proxies `/api/userdata/workflows/<file>` to `/api/userdata/workflows%2F<file>`. This preserves ComfyUI workflow read/write when an upstream layer decodes `%2F` before route matching.
- Do not change Web UI back to direct ComfyUI port `8000` unless you also provide an equivalent tested fix for workflow userdata POST/DELETE 405.
- Web UI GPU defaults to `L4`; use `python serve.py --gpu <GPU>` or `python -m scripts.deploy_ui --gpu <GPU>` instead of setting env vars manually
- GPU snapshots (`enable_gpu_snapshot`) for faster cold starts — **only works with `modal deploy`, not `modal serve`**
- Scales to zero after 60s idle (`scaledown_window`)
- output_vol is imported but **not yet mounted** — generated images currently stay in the container and are lost on scale-down

### Local Output Watcher (`client/watch.py`)

- `output/` is populated by the local watcher, not by `modal serve` itself
- The watcher polls `GET /history` and downloads images via `GET /view`
- Run `python -m client.watch <modal-web-ui-url>` when using the Web UI and you want local files under `output/`



## Code Patterns

- Use `pathlib.Path` for all filesystem paths (not raw strings)
- `subprocess.run(..., check=True)` everywhere — hard-fail on non-zero exit
- Keep typed function signatures
- Model downloads use symlinks from `/cache` to ComfyUI model dirs (never copy weights)
- CivitAI downloads: use `wget` with `?token=` query param (not Bearer header)
