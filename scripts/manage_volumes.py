"""Modal Volume management: list cached models, clean old sessions.

Usage:
    python -m scripts.manage_volumes
"""

from __future__ import annotations

import sys

try:
    import questionary
except ImportError:
    print("需要 questionary，请运行 `uv sync` 或 `pip install questionary`。")
    raise

try:
    import modal
except ImportError:
    print("需要 modal，请运行 `uv sync` 或 `pip install modal`。")
    raise


CACHE_VOLUME = "comfy-cache"
OUTPUT_VOLUME = "comfy-output"


def list_volume_contents(volume_name: str, path: str = "/") -> None:
    """List top-level contents of a Modal Volume."""
    vol = modal.Volume.from_name(volume_name)
    print(f"\n=== {volume_name} ({path}) ===")
    try:
        for entry in vol.listdir(path):
            print(f"  {entry.path}")
    except Exception as exc:
        print(f"  (读取失败: {exc})")


def clean_output_sessions() -> None:
    """Interactively delete old sessions from comfy-output."""
    vol = modal.Volume.from_name(OUTPUT_VOLUME)
    try:
        entries = list(vol.listdir("/"))
    except Exception as exc:
        print(f"读取失败: {exc}")
        return

    if not entries:
        print("comfy-output 为空，无需清理。")
        return

    choices = [entry.path for entry in entries]
    selected = questionary.checkbox(
        "选择要删除的 session 目录：",
        choices=choices,
    ).ask()

    if not selected:
        print("未选择任何目录。")
        return

    confirm = questionary.confirm(
        f"确认删除 {len(selected)} 个目录？",
        default=False,
    ).ask()
    if not confirm:
        print("取消操作。")
        return

    for path in selected:
        vol.remove_file(path, recursive=True)
        print(f"  已删除: {path}")
    print("清理完成。")


def main() -> int:
    action = questionary.select(
        "选择操作：",
        choices=[
            "查看 comfy-cache 内容",
            "查看 comfy-output 内容",
            "清理 comfy-output 旧 session",
        ],
    ).ask()
    if not action:
        return 1

    if action == "查看 comfy-cache 内容":
        list_volume_contents(CACHE_VOLUME)
    elif action == "查看 comfy-output 内容":
        list_volume_contents(OUTPUT_VOLUME)
    elif action == "清理 comfy-output 旧 session":
        clean_output_sessions()

    return 0


if __name__ == "__main__":
    sys.exit(main())
