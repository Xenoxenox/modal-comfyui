"""Modal Volume management for ComfyUI cache and outputs.

Usage:
    python -m scripts.manage_volumes
    python -m scripts.manage_volumes list
    python -m scripts.manage_volumes list --volume comfy-cache --path /
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
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

from rich.tree import Tree

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
MODEL_LINK_MANIFEST = "/.modal-comfyui-model-links.json"
FREE_TIER_REFERENCE_BYTES = 1024**4

SOURCE_BADGES = {
    "huggingface": "[white on blue] HU [/]",
    "huggingface_snapshot": "[black on cyan] SN [/]",
    "external": "[white on magenta] EX [/]",
    "local": "[black on green] LO [/]",
}


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


def _usage_bar(size: int, capacity: int = FREE_TIER_REFERENCE_BYTES) -> str:
    ratio = min(max(size / capacity, 0.0), 1.0)
    filled = max(1 if size else 0, round(ratio * 10))
    empty = 10 - filled
    percent = ratio * 100
    return f"[green]{'■' * filled}[/][dim]{'□' * empty}[/] {percent:.1f}%"


def _entry_label(entry: VolumeEntry) -> str:
    if entry.type == "dir":
        return f"[bold cyan]{entry.path.rstrip('/').split('/')[-1] or entry.path}[/]"
    size = _format_size(entry.size)
    usage = f" {_usage_bar(entry.size)}" if entry.size is not None else ""
    name = entry.path.rstrip("/").split("/")[-1] or entry.path
    return f"[white]{name}[/] [dim]{size}[/]{usage}"


def _read_volume_text(volume_name: str, path: str) -> str:
    vol = modal.Volume.from_name(volume_name)
    data = vol.read_file(path)
    if isinstance(data, bytes):
        return data.decode("utf-8")
    if isinstance(data, str):
        return data
    return b"".join(data).decode("utf-8")


def _load_model_manifest() -> list[dict[str, Any]]:
    raw = _read_volume_text(CACHE_VOLUME, MODEL_LINK_MANIFEST)
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def _model_dir_from_target(target_path: str) -> tuple[str, str]:
    path = PurePosixPath(target_path)
    parts = path.parts
    if "models" not in parts:
        return "other", path.name
    idx = parts.index("models")
    if idx + 1 >= len(parts):
        return "other", path.name
    model_dir = parts[idx + 1]
    name = PurePosixPath(*parts[idx + 2:]).as_posix() if idx + 2 < len(parts) else path.name
    return model_dir, name or path.name


def _compact_cache_path(cache_path: str) -> str:
    path = PurePosixPath(cache_path)
    parts = path.parts
    if len(parts) >= 2 and parts[1] == "cache":
        if "local-models" in parts:
            idx = parts.index("local-models")
            return PurePosixPath(*parts[idx:]).as_posix()
        if parts[-1]:
            return f".../{parts[-1]}"
    return path.name or cache_path


def print_model_link_tree() -> bool:
    """Print prepared model links grouped by ComfyUI model directory."""
    try:
        items = _load_model_manifest()
    except Exception:
        return False

    if not items:
        return False

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        model_dir, name = _model_dir_from_target(str(item.get("target_path", "")))
        grouped.setdefault(model_dir, []).append({**item, "display_name": name})

    root = Tree("[bold blue]comfy-cache prepared model links[/]")
    for model_dir in sorted(grouped):
        branch = root.add(f"[bold cyan]{model_dir}/[/]")
        for item in sorted(grouped[model_dir], key=lambda x: str(x["display_name"])):
            source = str(item.get("source", ""))
            badge = SOURCE_BADGES.get(source, "[white on grey23] ?? [/]")
            name = str(item["display_name"])
            cache_path = str(item.get("cache_path", ""))
            branch.add(f"{badge} [white]{name}[/] [dim]{_compact_cache_path(cache_path)}[/]")
    console.print(root)
    return True


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

    total_size = sum(entry.size or 0 for entry in entries)
    print_result_panel(
        "[bold blue]Usage Reference[/bold blue]",
        [
            ("Listed bytes", _format_size(total_size)),
            ("1 TiB reference", _usage_bar(total_size)),
            ("Note", "Modal pricing currently includes 1 TiB/mo free storage."),
        ],
        border_style="cyan",
    )

    if volume_name == CACHE_VOLUME and path == "/" and print_model_link_tree():
        return

    root = Tree(f"[bold blue]{volume_name}:{path}[/]")
    for entry in sorted(entries, key=lambda x: (x.type != "dir", x.path)):
        root.add(_entry_label(entry))
    console.print(root)


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
