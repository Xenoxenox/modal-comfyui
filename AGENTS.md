# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## High-signal project facts (non-obvious)
- Runtime mismatch is intentional: local project requires Python `>=3.13` in `pyproject.toml`, but the Modal image pins `python_version="3.11"` in `server/app.py`.
- `server/app.py` hard-imports `models` and `plugins`; you must create `models.py` and `plugins.py` from the `*.example.py` templates before running Modal commands.
- `models.py`, `plugins.py`, and `workflow_api.json` are intentionally gitignored; this repo expects local/private configuration.
- If `workflow_api.json` exists at repo root, build installs workflow deps via `comfy node install-deps --workflow=/root/workflow_api.json`; if absent, custom node setup is skipped with a warning.
- Two Modal Volumes: `comfy-cache` mounted at `/cache` (model weights, custom nodes) and `comfy-output` mounted at `/output` (generated results by session ID).
- Model assets are cached in `comfy-cache`, then symlinked into ComfyUI model dirs (do not assume direct file copies).
- External model downloads depend on `aria2c` (installed in image) and run with suppressed stdout/stderr; failures surface via non-zero exit only.
- Headless inference uses the `serialized=True` + `with app.run():` pattern to allow dynamic GPU selection at runtime. The function is defined inside the client and dispatched to the server.

## Commands actually used by this project
- Install deps: `uv sync`
- Modal auth bootstrap: `modal setup`
- Dev serve (Web UI): `modal serve server/ui.py`
- Deploy (Web UI): `modal deploy server/ui.py`
- Headless inference: `python -m client.infer`
- Volume management: `python -m scripts.manage_volumes`

## Test/lint reality (important)
- No test framework, test directory, or lint/format tool config is present in this repository.
- There is no project-defined single-test command; adding tests requires introducing a test runner first.

## Code patterns to preserve
- Keep typed function signatures and `pathlib.Path` usage style.
- Preserve `subprocess.run(..., check=True)` for setup/download commands; this code relies on hard-fail behavior.
- Model downloads always symlink from cache volume — never copy weight files.
- The `client/` directory runs locally (your machine); the `server/` directory runs inside Modal containers. Do not mix these execution contexts.
