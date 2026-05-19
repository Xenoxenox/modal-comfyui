# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## High-signal project facts (non-obvious)
- Runtime mismatch is intentional: local project requires Python `>=3.13` in `pyproject.toml`, but the Modal image pins `python_version="3.11"` in `server/app.py`.
- `server/app.py` reads model and plugin configuration from `config.toml`; copy `config.toml.example` to `config.toml` before running Modal commands.
- `config.toml` and `workflow_api.json` are intentionally gitignored; this repo expects local/private configuration.
- If `workflow_api.json` exists at repo root, build installs workflow deps via `comfy node install-deps --workflow=/root/workflow_api.json`; if absent, custom node setup is skipped with a warning.
- Web UI traffic goes through `server/nginx.conf`: Modal exposes nginx on port `8000`, while ComfyUI listens only on `127.0.0.1:8188`.
- The nginx workflow userdata rule is intentional: `/api/userdata/workflows/<file>` is proxied to `/api/userdata/workflows%2F<file>` so ComfyUI workflow read/write does not return 405 after `%2F` is decoded upstream.
- Two Modal Volumes: `comfy-cache` mounted at `/cache` (model weights, custom nodes) and `comfy-output` mounted at `/output` (generated results by session ID).
- Empty Web UI mode uses `COMFYUI_CONFIG_PROFILE=empty`, bakes `config.empty.toml`, and runs in Modal Environment `empty`; same-named Volumes are isolated by Modal Environment.
- Empty Web UI mode must not mount default prepare secrets; the `empty` Modal Environment is expected to work without `ComfyUI` or `civitai-api-key` secrets.
- Do not treat an empty config alone as empty mode: an old model manifest in the same `comfy-cache` would still symlink prepared models back into ComfyUI.
- Model assets are cached in `comfy-cache`, then symlinked into ComfyUI model dirs (do not assume direct file copies).
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
- Install deps: `uv sync`
- Modal auth bootstrap: `modal setup`
- Interactive manager: `python manage.py`
- Dev serve (Web UI): `python serve.py --gpu L4`
- Empty dev serve (workflow cleanup): `python serve.py --empty --gpu T4`
- Deploy (Web UI): `python -m scripts.deploy_ui --gpu L4`
- Empty deploy (workflow cleanup): `python -m scripts.deploy_ui --empty --gpu T4`
- Headless inference: `python -m client.infer`
- Volume management: `python -m scripts.manage_volumes`
- Prepare dry-run: set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, then run `python -m modal run server/app.py::prepare --dry-run` on Windows to avoid GBK failures on Modal CLI glyphs.

## Test/lint reality (important)
- No test framework, test directory, or lint/format tool config is present in this repository.
- There is no project-defined single-test command; adding tests requires introducing a test runner first.

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
