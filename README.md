# modal-comfyui

Run ComfyUI on Modal with a local TUI for model setup, cloud GPU Web UI sessions,
and headless workflow execution.

This project keeps your local machine as the controller. Model downloads, ComfyUI,
and GPU work run inside Modal containers; local code prepares configuration,
launches jobs, and downloads results.

## Who this is for

Use this project if you want to:

- run ComfyUI on cloud GPUs without managing a GPU server;
- cache large model files in Modal Volumes across runs;
- test workflows in the ComfyUI Web UI, then run API-format workflows headlessly;
- switch GPUs per serve/deploy/inference run from an interactive terminal.

Modal is usage-based and scales to zero, so idle Web UI apps do not keep a GPU
running. At the time of writing, Modal's Starter plan lists $30/month in free
compute credit and Modal Volumes list 1 TiB/month included storage. Check the
current Modal pricing page before relying on those limits:

https://modal.com/pricing

## Prerequisites

- A Modal account.
- Python 3.13+ locally. The Modal image uses Python 3.11 intentionally.
- `uv` installed locally.
- A terminal that can run interactive prompts.
- Optional: Modal secrets for private Hugging Face or CivitAI downloads.

Install dependencies and authenticate with Modal:

```bash
uv sync
modal setup
```
`uv sync` only installs the local Python environment; it does not sign in to
Modal. If you skip `modal setup`, the TUI detects the missing local Modal token
and can launch the setup flow for you from the same Python environment.

> [!TIP]
> If the terminal shows "modal: The term 'modal' is not recognized as a name of a cmdlet..." or a similar prompt, add "uv run" before "modal setup".
> ```bash
> uv run modal setup
> ```
> Or just activate .venv before entering such commands:
> ```bash
> source .venv/bin/activate
> ```
> ```powershell
> .venv\Scripts\Activate.ps1
> ```

Before running Modal commands, create your private local config:

```bash
cp config.toml.example config.toml
```

On Windows PowerShell, use `Copy-Item` if `cp` is not available:

```powershell
Copy-Item config.toml.example config.toml
```

`config.toml` is gitignored because it is expected to contain your local model
and plugin choices. Do not store access token values in `config.toml`, README
examples, or logs.

## Fast path: use the TUI

Start the interactive manager:

```bash
python manage.py
```

The TUI is the recommended entry point. Its main menu lets you:

- run ComfyUI in Normal Mode with a pre-flight config/manifest check;
- run Minimal Mode (Empty) for low-cost workflow cleanup without normal config or prepare;
- manage models in `config.toml`;
- manage ComfyUI custom nodes/plugins;
- deploy a persistent Web UI endpoint;
- inspect and clean Modal Volumes.

The TUI writes `config.toml` for you and shows the exact Modal command before it
runs prepare, serve, deploy, or headless inference actions. Remote operations
show a pre-flight review before submission so you can confirm the mode, GPU,
config profile, Volumes, and command.

At startup, the TUI reports two separate Modal states:

- `Modal Account`: whether the local Modal token/profile is usable.
- `Modal Secrets`: whether the Modal Secrets needed by this project exist in
  the active Modal Environment.

If `Modal Account` is missing, `Modal Secrets` checks are skipped as blocked by
sign-in instead of being reported as unknown. When the TUI detects a missing
Modal token, it asks:

```text
Modal token is missing. Do you want to run 'modal setup' now?
```

Choosing yes runs `python -m modal setup` from the current Python environment.
After setup finishes, the TUI refreshes account and secret status in a fresh
Python subprocess, so you do not need to exit and restart the manager.

## Configure `config.toml`

You can edit `config.toml` manually or use `python manage.py` to add entries.
The file supports four model sources:

```toml
[models.my-checkpoint]
source = "huggingface"
repo_id = "owner/repo"
filename = "model.safetensors"
model_dir = "checkpoints"

[models.my-lora]
source = "external"
url = "https://example.com/path/to/model.safetensors"
filename = "my-lora.safetensors"
model_dir = "loras"

[models.my-local-upload]
source = "local"
filename = "local-models/loras/my-lora.safetensors"
model_dir = "loras"
save_as = "my-lora.safetensors"

[models.my-diffusers-repo]
source = "huggingface_snapshot"
repo_id = "owner/diffusers-repo"
target_dir = "/root/comfy/ComfyUI/models/diffusers/my-diffusers-repo"

[plugins.comfyui-easy-use]
node_id = "comfyui-easy-use"
name = "ComfyUI Easy Use"
```

`model_dir` is relative to `/root/comfy/ComfyUI/models/` inside the Modal
container. Valid directories include `checkpoints`, `clip`, `clip_vision`,
`controlnet`, `diffusers`, `diffusion_models`, `embeddings`,
`facerestore_models`, `gligen`, `hypernetworks`, `insightface`, `loras`,
`photomaker`, `style_models`, `text_encoders`, `unet`, `upscale_models`, `vae`,
and `vae_approx`.

For private downloads, create Modal secrets with the keys expected inside Modal:

| Local env var | Default Modal secret name | Key inside Modal |
| --- | --- | --- |
| `MODAL_HF_SECRET_NAME` | `ComfyUI` | `HF_TOKEN` |
| `MODAL_CIVITAI_SECRET_NAME` | `civitai-api-key` | `CIVITAI_API_KEY` |

The TUI can create or update these Modal secrets and stores only the selected
secret names in gitignored `config.toml`:

```toml
[modal.secrets]
hf_secret_name = "ComfyUI"
civitai_secret_name = "civitai-api-key"
```

Local environment variables still override `config.toml` when set. Do not put
token values in `config.toml`.

Modal token IDs, Modal token secrets, Hugging Face tokens, CivitAI API keys, and
similarly shaped `ak-*` / `as-*` values must never be copied into docs, issues,
commits, logs, or command panels. TUI status/error paths redact Modal token-shaped
strings before display, but you should still avoid pasting secrets into prompts
or files.

If a configured Modal secret does not exist in the active Modal Environment,
prepare skips mounting that secret and continues. Public Hugging Face models and
direct public URLs can run without creating these secrets; private or gated
downloads still require the matching Modal secret.

> [!TIP]
> Show currently used Modal secrets:
> ```bash
> modal secret list
> ```
> Create your own Modal secrets:
> ```bash
> modal secret create [OPTIONS] SECRET_NAME [KEYVALUES]...
> ```
> For example:
> ```bash
> modal secret create ComfyUI HF_TOKEN=hf_[Your HF access token]
> ```

Set either local env var or `config.toml` secret name to an empty string, `none`,
or `false` to skip mounting that secret. Secret names are configurable; the keys
inside Modal stay fixed.

## Prepare models

Prepare downloads configured models into the `comfy-cache` Modal Volume and
writes a manifest used to symlink cached files into ComfyUI model directories.
Model weights are not copied into the image.

From the TUI:

```bash
python manage.py
```

Choose `Run ComfyUI`, then `Normal Mode (Full)`. The pre-flight check loads
`config.toml`, checks the remote model-link manifest, and runs prepare only when
the manifest is missing, invalid, or missing target paths required by the current
config. If prepare is needed, the TUI shows a command review before launching the
remote Modal operation.

Direct command:

```bash
modal run server/app.py::prepare
```

Useful variants:

```bash
modal run server/app.py::prepare --dry-run
modal run server/app.py::prepare --force
```

If `workflow_api.json` exists at the repository root, the Modal image build also
installs workflow dependencies with `comfy node install-deps`. If it is absent,
that step is skipped with a warning.

## Start the Web UI

For development sessions, prefer the launcher:

```bash
python serve.py --gpu L4
```

It sets UTF-8 environment variables, stops old ephemeral Modal apps, writes logs
to `logs/modal_serve_<timestamp>.log`, waits for the Modal URL, and probes the
ComfyUI health endpoint.

Modal may spend time allocating GPU capacity, checking the image, building, or
mounting the container before ComfyUI is reachable. The launcher displays these
phases immediately and, when Modal exposes them, prints:

- App ID
- Dashboard URL
- Function Call ID / URL
- local app log path
- a copyable logs command such as
  `python -m modal app logs <app_id> --follow --function-call <function_call_id>`
- a stop command to use only if the app lingers after you are done

You can choose another Modal GPU:

```bash
python serve.py --gpu L40S
```

For low-cost workflow cleanup, start an empty Web UI on a T4:

```bash
python serve.py --empty --gpu T4
```

Empty mode bakes the tracked `config.empty.toml` instead of your private
`config.toml` and runs in the Modal `empty` Environment. Modal Environments
isolate Apps, Secrets, and Storage, so the same `comfy-cache` and
`comfy-output` names point at separate empty-mode Volumes. This prevents an old
prepared model manifest from the normal environment from symlinking full model
weights back into ComfyUI. Empty mode also skips prepare-time Modal secrets, so
the `empty` Environment does not need your normal Hugging Face or CivitAI
secrets.

The TUI runs these launchers from `Run ComfyUI`:

- `Normal Mode (Full)` -> `python serve.py --gpu <GPU>` after pre-flight.
- `Minimal Mode (Empty)` -> `python serve.py --empty --gpu <GPU>` with T4 as the default.

The Web UI is served through nginx on Modal port `8000`. ComfyUI itself listens
only on `127.0.0.1:8188` inside the container. Keep using the Modal URL printed
by `serve.py`.

If you want Web UI outputs saved locally while using the browser, run the local
watcher in another terminal:

```bash
python -m client.watch <modal-web-ui-url>
```

It polls ComfyUI history and downloads new images into `output/`.

## Deploy the Web UI

Deploy creates a persistent Modal app endpoint:

```bash
python -m scripts.deploy_ui --gpu L4
```

Empty deploy is also supported when you want a persistent workflow-editing
endpoint without the normal model cache:

```bash
python -m scripts.deploy_ui --empty --gpu T4
```

The TUI runs normal deploy from `Cloud Deployment` -> `Deploy Web UI`. Empty
deploy is supported by the direct command above, but is not exposed in the first
TUI deploy menu.

GPU snapshots are configured for the deployed Web UI path. They help cold starts
for `modal deploy`; they do not apply to `modal serve`.

## Manage Modal Volumes

This project uses two Modal Volumes:

| Volume | Mount point | Purpose |
| --- | --- | --- |
| `comfy-cache` | `/cache` | model downloads, local uploads, prepared symlink manifest |
| `comfy-output` | `/output` | headless inference outputs by session ID |

Use the TUI:

```bash
python manage.py
```

Choose `Manage Modal Volumes` to list cache/output contents, refresh recursive
usage, delete prepared model files, prune orphaned prepared models, or clean old
output sessions.

Direct commands:

```bash
python -m scripts.manage_volumes
python -m scripts.manage_volumes list --volume comfy-cache
python -m scripts.manage_volumes list --volume comfy-output
python -m scripts.manage_volumes list --volume comfy-cache --refresh-usage
```

Recursive usage scans can take a while on large caches. Without
`--refresh-usage`, the tool reuses the last local usage cache when available.

The volume management commands inspect the current Modal Environment selected
by Modal. Empty mode uses the `empty` Environment, so its same-named Volumes are
separate from the normal environment's `comfy-cache` and `comfy-output`.

## Headless inference (UNTESTED; TO BE UPDATED IN NEXT TAG)

Headless inference runs a ComfyUI API-format workflow without opening the
browser. Put workflow JSON files in `workflows/`, then run:

```bash
python -m client.infer
```

The client prompts for:

- GPU type;
- workflow JSON path;
- timeout in minutes;
- optional seed override for `KSampler` and `KSamplerAdvanced` nodes;
- attached or detached launch mode.

The local `client/` code defines a per-invocation Modal app so the GPU can be
chosen at runtime. The remote `server/` code starts ComfyUI in the Modal
container, executes the workflow, writes results under `/output/<session-id>`,
and returns files for the client to download into `output/<session-id>/`.

Attached mode waits for completion and downloads outputs. Detached mode returns
after submission with the App ID, dashboard links, Function Call ID, local log,
logs command, and stop command. The stop command is only a manual fallback if the
app lingers; completed jobs are not automatically stopped by the client.

Keep the execution contexts separate:

- `client/` runs locally on your machine;
- `server/` runs inside Modal containers.

## Windows and Modal serve notes

`modal serve` can print Unicode characters that break GBK Windows terminals.
Use the local launcher instead:

```bash
python serve.py --gpu L4
```

If a previous ephemeral app blocks a new serve session, `serve.py` calls
`modal app list` and stops old ephemeral apps before launching.

If you run Modal commands manually, set a UTF-8 terminal/environment yourself
and inspect Modal apps with:

```bash
modal app list
```

## Workflow save/load in the Web UI

ComfyUI workflow userdata read/write goes through nginx. The nginx config
preserves workflow paths that include encoded slashes, so workflow save/load
requests do not need any manual user workaround for the historical 405 issue.

## Command reference

```bash
uv sync
modal setup
python manage.py
modal run server/app.py::prepare
python serve.py --gpu L4
python serve.py --empty --gpu T4
python -m scripts.deploy_ui --gpu L4
python -m scripts.deploy_ui --empty --gpu T4
python -m scripts.manage_volumes
python -m client.infer
python -m client.watch <modal-web-ui-url>
```

## Contributing

Contributions are welcome, especially improvements that make Modal startup,
model preparation, or ComfyUI workflow execution more reliable.
