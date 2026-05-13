"""Deploy the ComfyUI Web UI with a selected GPU.

Usage:
    python -m scripts.deploy_ui --gpu L40S
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

DEFAULT_WEB_UI_GPU = "L4"
WEB_UI_GPU_ENV = "COMFYUI_WEB_GPU"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy server/ui.py to Modal.")
    parser.add_argument(
        "--gpu",
        default=os.getenv(WEB_UI_GPU_ENV, DEFAULT_WEB_UI_GPU),
        help="Modal GPU for the Web UI. Defaults to L4.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        WEB_UI_GPU_ENV: args.gpu,
    }
    print(f"Deploying ComfyUI Web UI on GPU {args.gpu}")
    subprocess.run(["modal", "deploy", "server/ui.py"], env=env, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
