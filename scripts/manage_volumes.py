"""Modal Volume management for ComfyUI cache and outputs.

Usage:
    python -m scripts.manage_volumes
    python -m scripts.manage_volumes list
    python -m scripts.manage_volumes list --volume comfy-cache --path /
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

try:
    import questionary
except ImportError:
    print("questionary is required. Run: uv sync")
    raise

try:
    import modal
except ImportError:
    print("modal is required. Run: uv sync")
    raise

from rich.table import Table

from scripts.tui import (
    STYLE,
    ask_confirm,
    ask_select,
    console,
    print_banner,
    print_result_panel,
    print_status,
)

CACHE_VOLUME = "comfy-cache"
OUTPUT_VOLUME = "comfy-output"
KNOWN_VOLUMES = (CACHE_VOLUME, OUTPUT_VOLUME)


@dataclass(frozen=True)
class VolumeEntry:
    path: str
    type: str
    size: int | None = None


def _volume_description(volume_name: str) -> str:
    if volume_name == CACHE_VOLUME:
        return "model downloads, local uploads, and prepared symlink manifest"
    if volume_name == OUTPUT_VOLUME:
        return "headless inference outputs by session id"
    return "custom Modal volume"


def _entry_type(entry: Any) -> str:
    raw_type = getattr(entry, "type", None)
    if raw_type is not None:
        value = getattr(raw_type, "value", raw_type)
        if value == 1:
            return "file"
        if value == 2:
            return "dir"
        name = getattr(raw_type, "name", None)
        if name:
            return str(name).lower()
        return str(raw_type)
    if getattr(entry, "is_dir", False):
        return "dir"
    return "file"


def _entry_size(entry: Any) -> int | None:
    size = getattr(entry, "size", None)
    return size if isinstance(size, int) else None


def _format_size(size: int | None) -> str:
    if size is None:
        return "-"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def list_volume_entries(volume_name: str, path: str = "/") -> list[VolumeEntry]:
    """Return entries under a Modal Volume path."""
    vol = modal.Volume.from_name(volume_name)
    entries = []
    for entry in vol.listdir(path):
        entries.append(
            VolumeEntry(
                path=str(entry.path),
                type=_entry_type(entry),
                size=_entry_size(entry),
            )
        )
    return entries


def print_volume_contents(volume_name: str, path: str = "/") -> None:
    """List contents of a Modal Volume with a readable table."""
    print_result_panel(
        "[bold blue]Volume[/bold blue]",
        [
            ("Name", volume_name),
            ("Path", path),
            ("Purpose", _volume_description(volume_name)),
        ],
        border_style="blue",
    )
    try:
        entries = list_volume_entries(volume_name, path)
    except Exception as exc:
        print_status(f"Read failed: {exc}", style="red")
        return

    if not entries:
        print_status("No entries found.", style="yellow")
        return

    table = Table(title=f"{volume_name}:{path}", show_lines=False)
    table.add_column("Path", overflow="fold")
    table.add_column("Type", style="dim", no_wrap=True)
    table.add_column("Size", justify="right", no_wrap=True)
    for entry in entries:
        table.add_row(entry.path, entry.type, _format_size(entry.size))
    console.print(table)


def clean_output_sessions() -> None:
    """Interactively delete old sessions from comfy-output."""
    vol = modal.Volume.from_name(OUTPUT_VOLUME)
    try:
        entries = [entry.path for entry in vol.listdir("/")]
    except Exception as exc:
        print_status(f"Read failed: {exc}", style="red")
        return

    if not entries:
        print_status("comfy-output is empty; nothing to clean.", style="yellow")
        return

    selected = questionary.checkbox(
        "Select comfy-output session directories to delete:",
        choices=entries,
        style=STYLE,
    ).ask()

    if not selected:
        print_status("No sessions selected.", style="yellow")
        return

    confirmed = ask_confirm(
        f"Delete {len(selected)} selected session path(s) from {OUTPUT_VOLUME}?",
        default=False,
        instruction="This removes data from the Modal Volume and cannot be undone here.",
    )
    if not confirmed:
        print_status("Cancelled.", style="yellow")
        return

    removed = 0
    for path in selected:
        vol.remove_file(path, recursive=True)
        removed += 1
    print_result_panel(
        "[bold green]Cleanup Complete[/bold green]",
        [
            ("Volume", OUTPUT_VOLUME),
            ("Removed paths", removed),
        ],
    )


def volume_management_menu() -> None:
    while True:
        action = ask_select(
            "Volume action:",
            choices=[
                questionary.Choice(
                    "List comfy-cache",
                    value=("list", CACHE_VOLUME),
                    description=_volume_description(CACHE_VOLUME),
                ),
                questionary.Choice(
                    "List comfy-output",
                    value=("list", OUTPUT_VOLUME),
                    description=_volume_description(OUTPUT_VOLUME),
                ),
                questionary.Choice(
                    "Clean comfy-output sessions",
                    value=("clean", OUTPUT_VOLUME),
                    description="Delete selected old output session directories.",
                ),
                questionary.Choice("Back", value=("back", "")),
            ],
        )

        kind, volume_name = action
        if kind == "back":
            break
        if kind == "list":
            print_volume_contents(volume_name)
        elif kind == "clean":
            clean_output_sessions()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Modal Volumes used by modal-comfyui.")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List entries in a Modal Volume.")
    list_parser.add_argument(
        "--volume",
        choices=KNOWN_VOLUMES,
        default=CACHE_VOLUME,
        help="Volume to inspect. Defaults to comfy-cache.",
    )
    list_parser.add_argument(
        "--path",
        default="/",
        help="Volume path to list. Defaults to /.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        print_volume_contents(args.volume, args.path)
        return 0

    print_banner(
        "ComfyUI Volumes",
        "Inspect comfy-cache/comfy-output and clean old output sessions.",
    )
    volume_management_menu()
    return 0


if __name__ == "__main__":
    sys.exit(main())
