"""Deploy the ComfyUI Web UI with a selected GPU.

Usage:
    python -m scripts.deploy_ui --gpu L40S
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from scripts.web_ui_mode import (
    CONFIG_PROFILE_ENV,
    WEB_UI_GPU_ENV,
    add_web_ui_mode_args,
    empty_mode_env,
    ensure_modal_environment,
    modal_env_args,
    mode_from_args,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy server/ui.py to Modal.")
    add_web_ui_mode_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    profile, modal_env = mode_from_args(args)
    ensure_modal_environment(modal_env)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        WEB_UI_GPU_ENV: args.gpu,
        CONFIG_PROFILE_ENV: profile,
        **empty_mode_env(profile),
    }
    env_label = modal_env or "profile default"
    print(f"Deploying ComfyUI Web UI on GPU {args.gpu} profile={profile} env={env_label}")
    subprocess.run(
        ["modal", "deploy", *modal_env_args(modal_env), "server/ui.py"],
        env=env,
        check=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
