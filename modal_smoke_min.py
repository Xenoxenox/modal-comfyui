from __future__ import annotations

import subprocess

import modal


app = modal.App("modal-smoke-min")


@app.function()
@modal.web_server(8000, startup_timeout=30)
def web() -> None:
    subprocess.Popen(["python", "-m", "http.server", "8000"])
