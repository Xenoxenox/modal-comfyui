"""Headless ComfyUI inference — runs inside a Modal container.

This module defines the ``generate`` function used by ``client/infer.py``
to execute a ComfyUI workflow on a remote GPU. It is NOT registered on
a global ``app``; instead, the client creates a *per-invocation* App
with the user's chosen GPU and registers a function that calls
``run_generate`` via ``serialized=True``.
"""

from __future__ import annotations

import base64
from pathlib import Path

from server.comfy_wrapper import ComfyExecutor

OUTPUT_MOUNT = "/output"


def run_generate(workflow_json: dict, session_id: str) -> dict:
    """Execute a workflow headlessly and return base64-encoded outputs.

    Called inside a Modal container (serialized=True).
    """
    executor = ComfyExecutor()
    try:
        executor.start_server()
        executor.wait_until_ready()

        prompt_id = executor.submit_workflow(workflow_json)
        history = executor.poll_result(prompt_id)

        output_dir = Path(f"{OUTPUT_MOUNT}/{session_id}")
        output_files = executor.collect_outputs(history, output_dir)

        created_files: dict[str, str] = {}
        for f in output_files:
            created_files[f.name] = base64.b64encode(f.read_bytes()).decode()

        return {"created_files": created_files, "session_id": session_id}
    finally:
        executor.stop_server()
