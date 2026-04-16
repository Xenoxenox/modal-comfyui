"""ComfyUI headless API wrapper.

Manages a ComfyUI subprocess inside a Modal container:
start the server, submit workflow JSON via the internal HTTP API,
poll for completion, and collect output files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib import request, error as urlerror


COMFY_ROOT = "/root/comfy/ComfyUI"
DEFAULT_OUTPUT_DIR = f"{COMFY_ROOT}/output"


class ComfyExecutor:
    """Manage a ComfyUI subprocess and interact with its HTTP API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.process: subprocess.Popen | None = None

    # ── Lifecycle ──

    def start_server(self) -> None:
        """Launch ComfyUI as a background subprocess."""
        cmd = (
            f"comfy launch --background "
            f"-- --listen {self.host} --port {self.port}"
        )
        self.process = subprocess.Popen(cmd, shell=True)
        print(f"[comfy_wrapper] ComfyUI starting on {self.base_url}")

    def wait_until_ready(self, timeout: int = 120) -> None:
        """Block until the ComfyUI /system_stats endpoint responds."""
        url = f"{self.base_url}/system_stats"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                req = request.Request(url)
                with request.urlopen(req, timeout=5):
                    print("[comfy_wrapper] ComfyUI is ready")
                    return
            except (urlerror.URLError, ConnectionError, OSError):
                time.sleep(1)
        raise TimeoutError(
            f"ComfyUI did not become ready within {timeout}s"
        )

    def stop_server(self) -> None:
        """Terminate the ComfyUI subprocess."""
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=10)
            print("[comfy_wrapper] ComfyUI stopped")

    # ── Workflow Execution ──

    def submit_workflow(self, workflow_json: dict) -> str:
        """POST a workflow to /prompt and return the prompt_id."""
        payload = json.dumps({"prompt": workflow_json}).encode()
        req = request.Request(
            f"{self.base_url}/prompt",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req) as resp:
            result = json.loads(resp.read())
        prompt_id = result["prompt_id"]
        print(f"[comfy_wrapper] Submitted workflow, prompt_id={prompt_id}")
        return prompt_id

    def poll_result(self, prompt_id: str, timeout: int = 600) -> dict:
        """Poll /history/{prompt_id} until the workflow finishes.

        Returns the history entry dict for this prompt_id.
        """
        url = f"{self.base_url}/history/{prompt_id}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                req = request.Request(url)
                with request.urlopen(req, timeout=10) as resp:
                    history = json.loads(resp.read())
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})
                    if status.get("completed", False):
                        print(f"[comfy_wrapper] Workflow completed: {prompt_id}")
                        return entry
                    if status.get("status_str") == "error":
                        raise RuntimeError(
                            f"ComfyUI workflow failed: {status}"
                        )
            except (urlerror.URLError, ConnectionError, OSError):
                pass
            time.sleep(2)
        raise TimeoutError(
            f"Workflow {prompt_id} did not complete within {timeout}s"
        )

    def collect_outputs(
        self,
        history: dict,
        dest_dir: Path,
    ) -> list[Path]:
        """Copy generated files from ComfyUI output to dest_dir.

        Reads the 'outputs' field of the history entry to find which
        files were produced, then copies them from ComfyUI's output
        directory into dest_dir.

        Returns a list of destination file paths.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        collected: list[Path] = []
        outputs = history.get("outputs", {})

        for _node_id, node_output in outputs.items():
            # Each node may have 'images' or 'gifs' (for video)
            for key in ("images", "gifs"):
                for item in node_output.get(key, []):
                    filename = item["filename"]
                    subfolder = item.get("subfolder", "")
                    src = Path(DEFAULT_OUTPUT_DIR) / subfolder / filename
                    if src.exists():
                        dst = dest_dir / filename
                        shutil.copy2(src, dst)
                        collected.append(dst)
                        print(f"[comfy_wrapper] Collected: {dst}")

        return collected
