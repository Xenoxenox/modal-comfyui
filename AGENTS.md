# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## High-signal project facts (non-obvious)
- Runtime mismatch is intentional: local project requires Python `>=3.13` in `pyproject.toml`, but the Modal image pins `python_version="3.11"` in `server/app.py`.
- `server/app.py` reads model and plugin configuration from `config.toml`; copy `config.toml.example` to `config.toml` before running Modal commands.
- `config.toml` and `workflow_api.json` are intentionally gitignored; this repo expects local/private configuration.
- If `workflow_api.json` exists at repo root, build installs workflow deps via `comfy node install-deps --workflow=/root/workflow_api.json`; if absent, custom node setup is skipped with a warning.
- Web UI traffic goes through `server/nginx.conf`: Modal exposes nginx on port `8000`, while ComfyUI listens only on `127.0.0.1:8188`.
- The nginx workflow userdata rule is intentional: `/api/userdata/workflows/<relative-path>` is mapped from the original `$request_uri`, re-encoding every nested separator as `%2F` before proxying to ComfyUI. The fallback preserves unmatched/deeper encoded paths and query strings, so workflow read/write does not return 405 after `%2F` is decoded upstream. Modal's HTTP serializer currently rejects Unicode workflow path bytes before nginx with `DeserializationError`; this is an external edge limitation, not a ComfyUI route behavior.
- The nginx reboot rule is intentional: `location = /api/v2/manager/reboot` intercepts only upstream 502/503/504 (ComfyUI Manager reboots by exiting, so nginx loses its upstream mid-request) and answers 202 `{}` from the internal `/_nginx/manager_reboot_accepted`. Every other endpoint keeps its real upstream status; do not generalize this mapping to other paths or 5xx codes.
- Two Modal Volumes: `comfy-cache` mounted at `/cache` (model weights, custom nodes) and `comfy-output` mounted at `/output` (generated results by session ID).
- Empty Web UI mode uses `COMFYUI_CONFIG_PROFILE=empty`, bakes `config.empty.toml`, and runs in Modal Environment `empty`; same-named Volumes are isolated by Modal Environment.
- Empty Web UI mode must not mount default prepare secrets; the `empty` Modal Environment is expected to work without `ComfyUI` or `civitai-api-key` secrets.
- Do not treat an empty config alone as empty mode: an old model manifest in the same `comfy-cache` would still symlink prepared models back into ComfyUI.
- Model assets are cached in `comfy-cache`, then symlinked into ComfyUI model dirs (do not assume direct file copies).
- Custom nodes: the Volume dir `/cache/custom_nodes` is registered by the image-baked `extra_model_paths.yaml` with `is_default: true`, which makes it `folder_paths.get_folder_paths("custom_nodes")[0]` — the path ComfyUI-Manager installs into. Image-baked nodes stay in the image's own `custom_nodes/`; nothing renames or symlinks that directory at runtime, and no entrypoint may mutate it.
- ComfyUI's startup scan (`execute_prestartup_script`) calls `os.listdir` on every registered `custom_nodes` path without an existence guard, so a registered-but-missing directory crashes the container. Both the Web UI (`server/ui.py`) and headless inference (`server/generate.py`) therefore call `ensure_runtime_dirs()` from `server/comfy_runtime.py` before launching ComfyUI.
- ComfyUI is launched only through `server/comfy_runtime.py` (`launch_comfy` / `ComfySupervisor`); do not add a second launch or readiness convention.
- Custom-node Python dependencies land in the container's own site-packages, which is discarded when the container exits; only `/cache` survives. `server/ui.py` therefore decides from the interpreter (`missing_requirements()` / `validate_custom_node()` in `server/comfy_runtime.py`) whether a pack's `requirements.txt` is satisfied, pip-installs and restarts ComfyUI when it is not, and logs the unresolved distributions. Never gate that decision on a marker file stored in the Volume: a record written by an earlier container says nothing about the current one.
- `launch_comfy` starts `comfy launch` with `start_new_session=True` and `ComfySupervisor.request_restart()` signals the whole process group, because `comfy launch` runs `main.py` as a separate child process. Signalling only the wrapper leaves ComfyUI orphaned holding port 8188 while the supervisor relaunches into a busy port.
- Modal CLI invocations go through `scripts/modal_command.py` (`modal_command()`, `utf8_env()`) so every entrypoint uses the active Python environment.
- `prepare_models` mounts Modal secrets by name from local env vars or gitignored `config.toml` `[modal.secrets]`: `MODAL_HF_SECRET_NAME`/`hf_secret_name` defaults to `ComfyUI`, and `MODAL_CIVITAI_SECRET_NAME`/`civitai_secret_name` defaults to `civitai-api-key`. Missing default secrets are skipped so public downloads still run; empty string, `none`, or `false` disables that secret.
- Secret names are configurable, but token env keys inside Modal stay `HF_TOKEN` and `CIVITAI_API_KEY`; do not put tokens in `config.toml`, docs, or logs.
- TUI startup separates `Modal Account` from `Modal Secrets`: account/profile probing lives in `scripts/modal_status.py` and uses a fresh Python subprocess because the Modal SDK can cache `.modal.toml` in-process.
- If Modal account auth is missing, secret checks must be shown as skipped/blocked by sign-in, not as UNKNOWN secret state. The TUI may offer `sys.executable -m modal setup`, then must refresh status via the fresh probe.
- Token-shaped strings matching `ak-*` or `as-*` are redacted by `scripts/modal_status.py`; keep token IDs/secrets/HF tokens/CivitAI keys out of configs, docs, logs, command panels, and commits.
- Non-sensitive TUI preferences are stored in `.modal-comfyui/preferences.json` through `scripts/preferences.py` using an allowlist only. This directory is local runtime state and should not be committed.
- `scripts/billing.py` prints a best-effort session summary on TUI exit by calling Modal billing/profile commands; billing failures should not block exit.
- `scripts/modal_run_info.py` owns Modal app/function-call log command generation, stop command hints, app log paths, and attached log streaming/teeing for headless inference.
- `serve.py` is the preferred local Web UI launcher because it sets UTF-8 env, records local logs, parses Modal run info, probes health, and shows copyable logs/stop commands.
- External model downloads depend on `aria2c` (installed in image) and run with suppressed stdout/stderr; failures surface via non-zero exit only.
- Headless inference uses the `serialized=True` + `with app.run():` pattern to allow dynamic GPU selection at runtime. The function is defined inside the client and dispatched to the server.
- Because headless inference uses `serialized=True` against a Python 3.11 Modal image, the local Python used for headless smoke tests must be compatible with Python 3.11; local project development still targets Python `>=3.13`.

## Commands actually used by this project
- Install deps: `powershell -ExecutionPolicy Bypass -File .\setup.ps1` on Windows, or `bash ./setup.sh` on WSL/Linux/macOS.
- Manual dependency install: `uv sync`
- Modal auth bootstrap: `uv run modal setup`
- Interactive manager: `python manage.py`
- Dev serve (Web UI): `python serve.py --gpu L4`
- Empty dev serve (workflow cleanup): `python serve.py --empty --gpu T4`
- Deploy (Web UI): `python -m scripts.deploy_ui --gpu L4`
- Empty deploy (workflow cleanup): `python -m scripts.deploy_ui --empty --gpu T4`
- Headless inference: `python -m client.infer`
- Volume management: `python -m scripts.manage_volumes`
- Prepare dry-run: set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, then run `python -m modal run server/app.py::prepare --dry-run` on Windows to avoid GBK failures on Modal CLI glyphs.
- Setup scripts install `uv` when missing, use the Tsinghua PyPI mirror when `ping google.com` times out, and do not run Modal auth, create private `config.toml`, or write token values.

## Test/lint reality (important)
- `pytest` is the test runner and is declared in `pyproject.toml` `[dependency-groups] dev`; run `uv run pytest` (or `.venv/bin/python -m pytest tests`).
- `tests/` is version-controlled; add a regression test with every behaviour change. Existing coverage: `test_comfy_wrapper.py`, `test_config_loader.py` (config-load validation), `test_manage_paths.py`, `test_comfy_runtime.py`, `test_volume_fs.py`, `test_custom_node_topology.py`, `test_serve_state.py`.
- No lint/format tool config is present.

## Code patterns to preserve
- Keep typed function signatures and `pathlib.Path` usage style.
- Preserve `subprocess.run(..., check=True)` for setup/download commands; this code relies on hard-fail behavior.
- Preserve the Modal Account vs Modal Secrets separation. Do not collapse auth failures into secret UNKNOWN state, and do not use in-process Modal SDK state for post-setup refreshes.
- Keep `modal setup` launches as `[sys.executable, "-m", "modal", "setup"]` so the TUI uses the current Python environment.
- Keep preference persistence non-sensitive and allowlisted in `scripts/preferences.py`; never add token values or arbitrary config dumps to `.modal-comfyui/preferences.json`.
- Keep Modal remote operation UX explicit: pre-flight review before high-cost operations, immediate app/dashboard/function-call/log command display after submission, attached logs tee to local `logs/`, and stop commands as manual hints only.
- Model downloads always symlink from cache volume — never copy weight files.
- The `client/` directory runs locally (your machine); the `server/` directory runs inside Modal containers. Do not mix these execution contexts.
- Do not change Web UI back to direct ComfyUI port `8000`; keep nginx in front unless replacing the workflow userdata `%2F` fix with an equivalent tested solution.

## Detailed Project Guidance

### Project Overview

Run ComfyUI on Modal in two modes:

- **Web UI** — browser-based workflow design through nginx.
- **Headless inference** — the local client submits a JSON workflow, Modal executes it on GPU, and results are downloaded locally.

The local machine orchestrates requests; GPU work runs inside Modal containers.

### Modal Command Reference

```bash
powershell -ExecutionPolicy Bypass -File .\setup.ps1
bash ./setup.sh
uv sync
uv run modal setup
python manage.py
python serve.py --gpu L4
python serve.py --empty --gpu T4
modal serve server/ui.py
python -m scripts.deploy_ui --gpu L4
python -m scripts.deploy_ui --empty --gpu T4
python -m client.infer
python -m client.watch <url>
python -m scripts.manage_volumes
```

`serve.py` is preferred over direct `modal serve`: it handles UTF-8 output,
stuck ephemeral apps, local logs, Modal run metadata, health probing, and the
local output watcher.

Before any Modal command, ensure the private config exists:

```bash
cp config.toml.example config.toml
```

`workflow_api.json` is optional. If present, the image installs its workflow
custom-node dependencies with `comfy node install-deps`.

### Empty Workflow-Editing Mode

```bash
python serve.py --empty --gpu T4
python -m scripts.deploy_ui --empty --gpu T4
```

Empty mode sets `COMFYUI_CONFIG_PROFILE=empty`, uses tracked
`config.empty.toml`, and targets Modal Environment `empty`. Modal Environment
isolation gives empty mode separate same-named Volumes, Apps, and Secrets. Empty
mode does not mount normal prepare secrets and must work without `ComfyUI` or
`civitai-api-key`. An empty config alone is insufficient if an old model
manifest remains in the same Volume.

### Local-Only Helpers

`scripts/report_workflow_issue.py` and `scripts/run_and_report.py` are local
troubleshooting helpers. They are ignored and must not be re-committed. Since
they were previously committed, removing them from future pushes requires a
separate cleanup commit using `git rm --cached`.

### Windows and Encoding

Direct `modal serve` emits Unicode glyphs that can break a Windows GBK
terminal. Use `python serve.py --gpu L4`; it sets UTF-8 environment variables
and manages stuck apps. If a direct serve is stuck at `Running app...`, inspect
`modal app list`, stop the stale ephemeral app, and restart through `serve.py`.

### Repository Architecture

```text
client/       local inference, watcher, utilities, result downloads
server/       Modal app, Web UI, headless generation, ComfyUI runtime
config/       model/plugin schema and loader
scripts/      TUI, Modal status/commands, Volume and deployment helpers
tests/        version-controlled pytest suite
workflows/    JSON workflow seeds copied into the image
```

Important server boundaries:

- `server/app.py` owns the Modal App/Image/Volumes and model preparation.
- `server/ui.py` owns the nginx-backed Web UI function.
- `server/generate.py` owns headless inference execution.
- `server/comfy_runtime.py` owns ComfyUI launch, supervision, readiness, and
  runtime directory prerequisites.
- `server/comfy_wrapper.py` owns ComfyUI subprocess/API interaction for
  headless inference.
- `server/model_manifest.py` owns prepared-model symlink manifest state.

The `client/` directory runs locally; `server/` runs inside Modal containers.
Do not mix these execution contexts.

### Modal Volumes and Image Build

| Volume | Mount | Contents |
|---|---|---|
| `comfy-cache` | `/cache` | model cache, prepared models, custom nodes, user workflows |
| `comfy-output` | `/output` | headless outputs by session ID |

The image pipeline is intentionally layered:

1. Debian slim with Python 3.11; local development requires Python >=3.13.
2. System and Python dependencies.
3. ComfyUI installation.
4. Config source and private `config.toml`.
5. Optional model preparation through `modal run server/app.py::prepare`.
6. Configured blessed custom-node installation.
7. Workflow seed copy to `/root/comfy/workflow-seed`.
8. nginx configuration copied to `/root/nginx.conf`.

Model preparation reads `config.toml`, downloads into `/cache`, and symlinks
models into ComfyUI directories. Never replace those symlinks with copies.

### Model and Plugin Configuration

Models and plugins are configured in gitignored `config.toml`:

```toml
[models.example]
source = "huggingface"
repo_id = "org/repo"
filename = "model.safetensors"
model_dir = "checkpoints"

[plugins.example]
node_id = "comfyui-example"
```

Each plugin needs `node_id`, `repo`, or both; `repo` takes priority. Rebuild
the image after config changes. Do not put tokens in config, documentation,
logs, or commits.

### Custom Node Layers

Two custom-node layers coexist:

- **Blessed** — configured in `config.toml`, installed into the image during
  build, and loaded from the image's own `custom_nodes/`.
- **Experimental** — installed by ComfyUI-Manager at runtime into
  `/cache/custom_nodes/`, persisted by the Volume.

Do not rename, symlink, or mutate the image custom-node directory at runtime.
`extra_model_paths.yaml` registers `/cache/custom_nodes` with `is_default: true`,
making it ComfyUI's manager installation target. Promote a working experimental
node by adding its repository to `config.toml`, rebuilding, then removing the
old Volume copy manually.

### Workflow Notes

`workflows/` is copied to the image seed directory and seeded into the
Volume-backed ComfyUI user directory before launch. Existing user workflows
take precedence. The repository includes NewBie workflows using the configured
UNET, dual CLIP encoders, VAE, and void LoRA; do not assume those model assets
exist in empty mode.

### Web UI and Output Watcher

`@modal.web_server(8000)` exposes nginx, not ComfyUI directly. ComfyUI listens
on `127.0.0.1:8188`. `serve.py` starts `client/watch.py` by default; the watcher
polls `/history`, downloads images from `/view`, and writes them under local
`output/`. Use `python serve.py --no-watch ...` to disable it or run
`python -m client.watch <url>` manually.

### Code Style and Operational Rules

- Use `pathlib.Path` for filesystem paths.
- Keep typed function signatures.
- Use `subprocess.run(..., check=True)` for setup/download commands.
- Keep Modal Account and Modal Secrets as separate TUI concepts.
- Use fresh subprocesses when probing Modal account state after `modal setup`.
- Keep remote operation review, immediate run metadata, attached logs, and
  manual stop hints explicit.
- Keep token-shaped values and private configuration out of logs and commits.
