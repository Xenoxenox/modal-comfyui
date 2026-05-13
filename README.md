# modal-comfyui

Run ComfyUI on Modal with auto-scaling, GPU snapshots, and easy model management.

Good for testing wan2.2 or other video generation models.

## Prerequisites

- A Modal account
- Python installed
- `uv` installed

## Installation

1. Clone this repository.
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Set up your modal account (if not done already):
   ```bash
   modal setup
   ```

## Configuration

### Models

Copy `config.toml.example` to `config.toml` and edit it to manage your models. You can specify:
- Hugging Face models(`models`) using `repo_id` and `filename`.
- External models(`models_ext`, e.g. civitai) using a direct `url`.

Models are downloaded to volumes and symlinked to the specified `model_dir`.
See `config.toml.example` for reference.

### Modal Secrets for Model Prepare

`modal run server/app.py::prepare` mounts Modal secrets only for model downloads. By default it stays compatible with the original secret names:

| Environment variable | Default Modal secret | Secret key used inside Modal |
|----------------------|----------------------|------------------------------|
| `MODAL_HF_SECRET_NAME` | `ComfyUI` | `HF_TOKEN` |
| `MODAL_CIVITAI_SECRET_NAME` | `civitai-api-key` | `CIVITAI_API_KEY` |

You can point either variable at a differently named Modal secret. Set it to an empty string, `none`, or `false` to skip mounting that secret, which is useful for public Hugging Face models or configs that do not use CivitAI.

Do not put tokens in `config.toml`, README examples, or logs. Store tokens only in Modal secrets, and keep the secret keys as `HF_TOKEN` and `CIVITAI_API_KEY`.

### Plugins and Custom Nodes

Add custom node IDs or GitHub repos to `config.toml` to install them via `comfy-cli`.
- **Workflow Dependencies**: If you have a `workflow_api.json` in the root directory, the setup will automatically install the necessary custom nodes for that workflow.

### In case of Insufficient Custom Node

Open ComfyUI manager on comfyui and click "Used in Workflow" to see which custom nodes are used in the workflow.

Add these custom nodes to `config.toml` (be careful of node id).

## Usage

### Interactive Manager

Use the local manager for model/plugin config, model prepare, Web UI GPU selection, and Volume inspection:
```bash
python manage.py
```

### Web UI — Serve (Development)

Run the following command to start ComfyUI in development mode:
```bash
python serve.py --gpu L4
```
This will provide a temporary URL where you can access the ComfyUI interface.
Choose another GPU by changing the argument:
```bash
python serve.py --gpu L40S
```

### Web UI — Deploy (Production)

To deploy ComfyUI as a persistent app:
```bash
python -m scripts.deploy_ui --gpu L4
```
`python manage.py` can also prompt for the Web UI GPU before running serve or deploy.

### Headless Inference

Run ComfyUI workflows without the browser — submit a JSON workflow, choose a GPU, and download results:
```bash
python -m client.infer
```

Place your ComfyUI API-format workflow JSON files in the `workflows/` directory.

### Volume Management

List cached models or clean up old inference sessions:
```bash
python -m scripts.manage_volumes
```
Non-interactive list checks are also available:
```bash
python -m scripts.manage_volumes list --volume comfy-cache
python -m scripts.manage_volumes list --volume comfy-output
```

## Features

- **Dual Mode**: Web UI for workflow design, headless mode for batch production.
- **Auto-scaling**: Scales down to zero when not in use to save costs.
- **GPU Snapshots**: Fast startup times using Modal's GPU snapshots.
- **Model Caching**: Uses Modal Volumes to cache models across runs.
- **Custom Node Management**: Integrated with `comfy-cli` for easy plugin installation.
- **Interactive CLI**: GPU selection, workflow file, timeout via questionary prompts.

## Contributing

Please feel free to contribute to make this project better.
Performance improvements/optimizations are very welcome.
