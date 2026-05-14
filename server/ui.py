from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import modal

WEB_UI_GPU = os.environ.get("COMFYUI_WEB_GPU", "L4")

if os.environ.get("MODAL_IS_REMOTE") == "1":
    cache_vol = modal.Volume.from_name("comfy-cache", create_if_missing=True)
    CACHE_MOUNT = "/cache"
    MODEL_LINK_MANIFEST = Path(CACHE_MOUNT) / ".modal-comfyui-model-links.json"
    app = modal.App(name="modal-comfyui")

    def _link_cached_path(source_path: Path, target_path: Path) -> str:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists() or target_path.is_symlink():
            target_path.unlink()
        target_path.symlink_to(source_path)
        return str(target_path)

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
else:
    from server.app import app, cache_vol, CACHE_MOUNT, sync_prepared_model_links


@app.function(
    max_containers=1,
    gpu=WEB_UI_GPU,
    volumes={CACHE_MOUNT: cache_vol},
    scaledown_window=60,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
@modal.concurrent(max_inputs=10)
@modal.web_server(8000, startup_timeout=60)
def ui():
    cache_vol.reload()
    sync_prepared_model_links()
    subprocess.Popen(
        "comfy launch --background -- --listen 0.0.0.0 --port 8000", shell=True
    )
