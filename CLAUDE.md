# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Run ComfyUI on Modal with two modes: **Web UI** (browser-based workflow design) and **Headless Inference** (local client submits JSON workflow → cloud GPU executes → results downloaded). The local machine only orchestrates; all GPU work happens in Modal containers.

## Commands

```bash
uv sync                        # install dependencies (modal + questionary)
modal setup                    # authenticate with Modal (one-time)
modal serve server/ui.py       # dev Web UI — ephemeral URL, hot-reloads
modal deploy server/ui.py      # production Web UI — persistent endpoint
python -m client.infer         # headless inference — interactive GPU/workflow selection
python -m scripts.manage_volumes  # manage Modal Volumes (list/clean)
```

No test framework or linter is configured in this repo.

## Before Running Any Modal Command

`server/app.py` hard-imports `models` and `plugins` at the module level. These files are gitignored. Create them from the example templates:

```bash
cp models.example.py models.py
cp plugins.example.py plugins.py
```

Optionally place a `workflow_api.json` at the repo root or workflow JSON files in `workflows/`. If `workflow_api.json` is present, the image build automatically installs its required custom nodes via `comfy node install-deps`.

## Architecture

```
modal-comfyui/
├── client/              # Local client code (runs on your machine)
│   ├── infer.py         # Headless inference entry-point (questionary → Modal)
│   └── utils.py         # Logging, UTF-8 fix, workflow loading, result download
├── server/              # Remote code (runs inside Modal containers)
│   ├── app.py           # Modal App, Image, Volumes, model download functions
│   ├── ui.py            # Web UI Function (@modal.web_server)
│   ├── generate.py      # Headless inference logic (called via serialized=True)
│   └── comfy_wrapper.py # ComfyUI subprocess management & HTTP API wrapper
├── scripts/
│   └── manage_volumes.py  # Volume listing and cleanup
├── workflows/           # User workflow JSON files (gitignored)
├── models.py            # (gitignored) HF + external model config
├── plugins.py           # (gitignored) Custom node IDs for comfy-cli
```

### Volumes

| Volume | Mount Point | Contents |
|--------|------------|----------|
| `comfy-cache` | `/cache` | HF model cache, external model downloads, custom nodes |
| `comfy-output` | `/output` | Generated images/videos organized by session ID |

### Modal Image Build Pipeline (`server/app.py`)

The image is built in layers and cached by Modal:

1. `debian_slim(python_version="3.11")` — **image uses Python 3.11, but local `pyproject.toml` requires `>=3.13`; this mismatch is intentional.**
2. `apt_install` + `pip_install_from_requirements` (`comfy-cli`, `huggingface_hub`)
3. `comfy --skip-prompt install --nvidia` — installs ComfyUI into the image
4. `download_all()` runs as a build step against `comfy-cache` — downloads models and symlinks them into ComfyUI model dirs; does **not** copy files
5. If `workflow_api.json` exists: `comfy node install-deps` + `comfy node install` for any IDs in `plugins.py`

### Headless Inference Flow (`client/infer.py` → `server/generate.py`)

1. User runs `python -m client.infer`, questionary prompts for GPU type, workflow file, timeout, optional seed override
2. Client loads workflow JSON, creates a per-invocation `modal.App` with the chosen GPU
3. `remote_generate.remote()` executes inside Modal: starts ComfyUI subprocess → waits for HTTP API readiness → POSTs workflow to `127.0.0.1:8188/prompt` → polls `/history/{prompt_id}` → collects output files
4. Results returned as base64-encoded dict, decoded and written to `output/{session_id}/` locally

The dynamic GPU selection uses the `serialized=True` + `with app.run():` pattern (proven in `modal_infer.py.bak`).

### Web UI (`server/ui.py`)

- `@modal.web_server(8000)` serving ComfyUI on port 8000 via `comfy launch --background`
- GPU snapshots (`enable_gpu_snapshot`) for faster cold starts
- Scales to zero after 60s idle (`scaledown_window`)

## Code Patterns

- Use `pathlib.Path` for all filesystem paths (not raw strings)
- `subprocess.run(..., check=True)` everywhere — hard-fail on non-zero exit
- Keep typed function signatures
- Model downloads use symlinks from `/cache` to ComfyUI model dirs (never copy weights)
