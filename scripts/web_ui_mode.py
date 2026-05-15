"""Shared Web UI launch mode helpers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess

DEFAULT_WEB_UI_GPU = "L4"
WEB_UI_GPU_ENV = "COMFYUI_WEB_GPU"
CONFIG_PROFILE_ENV = "COMFYUI_CONFIG_PROFILE"
MODAL_HF_SECRET_NAME_ENV = "MODAL_HF_SECRET_NAME"
MODAL_CIVITAI_SECRET_NAME_ENV = "MODAL_CIVITAI_SECRET_NAME"
DEFAULT_CONFIG_PROFILE = "default"
EMPTY_CONFIG_PROFILE = "empty"
EMPTY_MODAL_ENVIRONMENT = "empty"


def add_web_ui_mode_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--gpu",
        default=os.getenv(WEB_UI_GPU_ENV, DEFAULT_WEB_UI_GPU),
        help="Modal GPU for the Web UI. Defaults to L4.",
    )
    parser.add_argument(
        "--profile",
        choices=[DEFAULT_CONFIG_PROFILE, EMPTY_CONFIG_PROFILE],
        default=os.getenv(CONFIG_PROFILE_ENV, DEFAULT_CONFIG_PROFILE),
        help="Config profile to bake into the Modal image. Defaults to default.",
    )
    parser.add_argument(
        "--env",
        dest="modal_env",
        default=None,
        help=(
            "Modal Environment to use. Defaults to Modal CLI resolution. "
            "Empty profile defaults to 'empty'."
        ),
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Use the tracked empty config profile in the 'empty' Modal Environment.",
    )


def mode_from_args(args: argparse.Namespace) -> tuple[str, str | None]:
    profile = EMPTY_CONFIG_PROFILE if args.empty else args.profile
    modal_env = args.modal_env
    if args.empty and modal_env is None:
        modal_env = EMPTY_MODAL_ENVIRONMENT
    if profile == EMPTY_CONFIG_PROFILE and modal_env is None:
        modal_env = EMPTY_MODAL_ENVIRONMENT
    return profile, modal_env


def modal_env_args(modal_env: str | None) -> list[str]:
    return ["--env", modal_env] if modal_env else []


def empty_mode_env(profile: str) -> dict[str, str]:
    if profile != EMPTY_CONFIG_PROFILE:
        return {}
    return {
        MODAL_HF_SECRET_NAME_ENV: "none",
        MODAL_CIVITAI_SECRET_NAME_ENV: "none",
    }


def ensure_modal_environment(modal_env: str | None) -> None:
    if modal_env != EMPTY_MODAL_ENVIRONMENT:
        return

    result = subprocess.run(
        ["modal", "environment", "list", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if result.returncode == 0:
        try:
            environments = json.loads(result.stdout)
        except json.JSONDecodeError:
            environments = []
        names = {
            str(item.get("name"))
            for item in environments
            if isinstance(item, dict) and item.get("name")
        }
        if modal_env in names:
            return

    print(f"Creating Modal Environment: {modal_env}")
    subprocess.run(["modal", "environment", "create", modal_env], check=True)
