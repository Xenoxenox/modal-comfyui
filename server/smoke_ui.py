from __future__ import annotations

import subprocess

import modal

from server.app import app


@app.function(max_containers=1, scaledown_window=60)
@modal.web_server(8000, startup_timeout=30)
def smoke_ui():
    subprocess.Popen(["python", "-m", "http.server", "8000"])
