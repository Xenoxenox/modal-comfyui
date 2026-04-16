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

Copy `models.example.py` to `models.py` and edit it to manage your models. You can specify:
- Hugging Face models(`models`) using `repo_id` and `filename`.
- External models(`models_ext`, e.g. civitai) using a direct `url`.

Models are downloaded to volumes and symlinked to the specified `model_dir`.
See `models.example.py` for reference.

### Plugins and Custom Nodes

Copy `plugins.example.py` to `plugins.py` and edit it to add custom node IDs or titles to be installed via `comfy-cli`.
- **Workflow Dependencies**: If you have a `workflow_api.json` in the root directory, the setup will automatically install the necessary custom nodes for that workflow.

### In case of Insufficient Custom Node

Open ComfyUI manager on comfyui and click "Used in Workflow" to see which custom nodes are used in the workflow.

Add these custom nodes to `plugins.py`(be careful of node id).

## Usage

### Web UI — Serve (Development)

Run the following command to start ComfyUI in development mode:
```bash
modal serve server/ui.py
```
This will provide a temporary URL where you can access the ComfyUI interface.

### Web UI — Deploy (Production)

To deploy ComfyUI as a persistent app:
```bash
modal deploy server/ui.py
```

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
