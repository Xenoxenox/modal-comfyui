"""Modal Volume management for ComfyUI cache and outputs.

Usage:
    python -m scripts.manage_volumes
    python -m scripts.manage_volumes list
    python -m scripts.manage_volumes list --volume comfy-cache --path /
    python -m scripts.manage_volumes list --volume comfy-cache --refresh-usage
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
USAGE_CACHE_PATH = Path(".cache") / "modal-comfyui" / "volume_usage.json"
MODEL_SIZE_CACHE_PATH = Path(".cache") / "modal-comfyui" / "model_file_sizes.json"
DELETED_MODEL_CACHE_PATH = Path(".cache") / "modal-comfyui" / "deleted_model_files.json"

SOURCE_BADGES = {
    "huggingface": "[white on blue] HU [/]",
    "huggingface_snapshot": "[black on cyan] SN [/]",
    "external": "[white on magenta] EX [/]",
    "local": "[black on green] LO [/]",
}


@dataclass(frozen=True)
class PreparedModelFile:
    source: str
    cache_path: str
    target_path: str
    model_dir: str
    display_name: str
    size: int | None = None


@dataclass(frozen=True)
class VolumeEntry:
    path: str
    type: str
    size: int | None = None


@dataclass(frozen=True)
class VolumeUsage:
    total_size: int
    file_count: int
    dir_count: int
    errors: tuple[str, ...] = ()
    refreshed_at: str | None = None
    source: str = "fresh"


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


def _cache_to_volume_path(cache_path: str) -> str:
    if cache_path.startswith("/cache/"):
        return "/" + cache_path.removeprefix("/cache/")
    if cache_path == "/cache":
        return "/"
    return cache_path if cache_path.startswith("/") else f"/{cache_path}"


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


def _volume_file_size(volume_name: str, path: str) -> int | None:
    volume_path = PurePosixPath(path)
    parent = volume_path.parent.as_posix()
    if parent == ".":
        parent = "/"
    name = volume_path.name
    try:
        entries = list_volume_entries(volume_name, parent)
    except Exception:
        return None
    for entry in entries:
        if PurePosixPath(entry.path).name == name and entry.type != "dir":
            return entry.size
    return None


def _read_model_size_cache() -> dict[str, Any]:
    try:
        return json.loads(MODEL_SIZE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_model_size_cache(cache: dict[str, Any]) -> None:
    MODEL_SIZE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_SIZE_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _read_deleted_model_cache() -> set[str]:
    try:
        raw = json.loads(DELETED_MODEL_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def _write_deleted_model_cache(items: set[str]) -> None:
    DELETED_MODEL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DELETED_MODEL_CACHE_PATH.write_text(
        json.dumps(sorted(items), indent=2),
        encoding="utf-8",
    )


def _mark_deleted_model_files(cache_paths: list[str]) -> None:
    deleted = _read_deleted_model_cache()
    deleted.update(cache_paths)
    _write_deleted_model_cache(deleted)


def _save_model_sizes_from_volume_scan(sizes_by_volume_path: dict[str, int]) -> None:
    if not sizes_by_volume_path:
        return
    try:
        prepared = list_prepared_model_files(include_sizes=False)
    except Exception:
        return
    sizes: dict[str, int] = {}
    for item in prepared:
        volume_path = _cache_to_volume_path(item.cache_path)
        size = sizes_by_volume_path.get(volume_path)
        if size is not None:
            sizes[item.cache_path] = size
    if not sizes:
        return
    deleted = _read_deleted_model_cache()
    if deleted:
        deleted.difference_update(sizes)
        _write_deleted_model_cache(deleted)
    existing = _read_model_size_cache()
    merged = {
        key: value
        for key, value in existing.get("sizes", {}).items()
        if isinstance(key, str) and isinstance(value, int)
    }
    merged.update(sizes)
    _write_model_size_cache({
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sizes": merged,
    })


def forget_cached_model_files(cache_paths: list[str]) -> None:
    if not cache_paths:
        return
    remove_set = set(cache_paths)
    cache = _read_model_size_cache()
    sizes = cache.get("sizes", {})
    if isinstance(sizes, dict):
        cache["sizes"] = {
            key: value
            for key, value in sizes.items()
            if key not in remove_set
        }
        _write_model_size_cache(cache)
    usage_cache = _read_usage_cache()
    usage_cache.pop(_usage_cache_key(CACHE_VOLUME, "/"), None)
    _write_usage_cache(usage_cache)


def _cached_model_sizes() -> dict[str, int]:
    raw = _read_model_size_cache().get("sizes", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, int):
            result[key] = value
    return result


def list_prepared_model_files(
    *,
    include_sizes: bool = True,
) -> list[PreparedModelFile]:
    items = _load_model_manifest()
    cached_sizes = _cached_model_sizes()
    deleted = _read_deleted_model_cache()
    result: list[PreparedModelFile] = []
    for item in items:
        cache_path = str(item.get("cache_path", ""))
        if cache_path in deleted:
            continue
        target_path = str(item.get("target_path", ""))
        model_dir, name = _model_dir_from_target(target_path)
        size = None
        if include_sizes and cache_path:
            size = cached_sizes.get(cache_path)
        result.append(
            PreparedModelFile(
                source=str(item.get("source", "")),
                cache_path=cache_path,
                target_path=target_path,
                model_dir=model_dir,
                display_name=name,
                size=size,
            )
        )
    return result


def print_model_link_tree() -> bool:
    """Print prepared model links grouped by ComfyUI model directory."""
    try:
        items = list_prepared_model_files(include_sizes=True)
    except Exception:
        return False

    if not items:
        return False

    grouped: dict[str, list[PreparedModelFile]] = {}
    for item in items:
        grouped.setdefault(item.model_dir, []).append(item)

    root = Tree("[bold blue]comfy-cache prepared model links[/]")
    for model_dir in sorted(grouped):
        branch = root.add(f"[bold cyan]{model_dir}/[/]")
        for item in sorted(grouped[model_dir], key=lambda x: x.display_name):
            badge = SOURCE_BADGES.get(item.source, "[white on grey23] ?? [/]")
            size = f" [bold]{_format_size(item.size)}[/]" if item.size is not None else ""
            branch.add(
                f"{badge} [white]{item.display_name}[/]{size} "
                f"[dim]{_compact_cache_path(item.cache_path)}[/]"
            )
    console.print(root)
    return True


def remove_volume_model_files(cache_paths: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Remove selected model files from comfy-cache by cache_path."""
    vol = modal.Volume.from_name(CACHE_VOLUME)
    removed: list[str] = []
    failed: list[tuple[str, str]] = []
    for cache_path in cache_paths:
        volume_path = _cache_to_volume_path(cache_path)
        try:
            vol.remove_file(volume_path, recursive=False)
            removed.append(cache_path)
        except Exception as exc:
            if "No such file or directory" in str(exc):
                removed.append(cache_path)
                continue
            failed.append((cache_path, str(exc)))
    if removed:
        forget_cached_model_files(removed)
        _mark_deleted_model_files(removed)
    return removed, failed


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


def _join_volume_path(parent: str, child: str) -> str:
    if child.startswith("/"):
        return child
    if parent == "/":
        return f"/{child}"
    return f"{parent.rstrip('/')}/{PurePosixPath(child).name}"


def calculate_volume_usage(
    volume_name: str,
    path: str = "/",
    *,
    recursive: bool = True,
) -> VolumeUsage:
    """Calculate file bytes under a Volume path.

    Modal directory entry sizes are directory metadata, not recursive contents.
    Only file entries are counted as storage usage.
    """
    total_size = 0
    file_count = 0
    dir_count = 0
    errors: list[str] = []
    sizes_by_volume_path: dict[str, int] = {}
    stack = [path]

    while stack:
        current = stack.pop()
        try:
            entries = list_volume_entries(volume_name, current)
        except Exception as exc:
            errors.append(f"{current}: {exc}")
            continue

        for entry in entries:
            if entry.type == "dir":
                dir_count += 1
                if recursive:
                    stack.append(_join_volume_path(current, entry.path))
                continue
            file_count += 1
            size = entry.size or 0
            total_size += size
            sizes_by_volume_path[_join_volume_path(current, entry.path)] = size

        if not recursive:
            break

    if volume_name == CACHE_VOLUME and recursive:
        _save_model_sizes_from_volume_scan(sizes_by_volume_path)

    return VolumeUsage(
        total_size=total_size,
        file_count=file_count,
        dir_count=dir_count,
        errors=tuple(errors),
        refreshed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source="fresh",
    )


def _usage_cache_key(volume_name: str, path: str) -> str:
    return f"{volume_name}:{path}"


def _read_usage_cache() -> dict[str, Any]:
    try:
        return json.loads(USAGE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_usage_cache(cache: dict[str, Any]) -> None:
    USAGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def load_cached_usage(volume_name: str, path: str = "/") -> VolumeUsage | None:
    raw = _read_usage_cache().get(_usage_cache_key(volume_name, path))
    if not isinstance(raw, dict):
        return None
    try:
        return VolumeUsage(
            total_size=int(raw["total_size"]),
            file_count=int(raw["file_count"]),
            dir_count=int(raw["dir_count"]),
            errors=tuple(str(item) for item in raw.get("errors", [])),
            refreshed_at=str(raw.get("refreshed_at") or ""),
            source="cache",
        )
    except (KeyError, TypeError, ValueError):
        return None


def save_cached_usage(volume_name: str, path: str, usage: VolumeUsage) -> None:
    cache = _read_usage_cache()
    cache[_usage_cache_key(volume_name, path)] = {
        "total_size": usage.total_size,
        "file_count": usage.file_count,
        "dir_count": usage.dir_count,
        "errors": list(usage.errors),
        "refreshed_at": usage.refreshed_at,
    }
    _write_usage_cache(cache)


def get_volume_usage(
    volume_name: str,
    path: str = "/",
    *,
    refresh: bool = False,
) -> VolumeUsage | None:
    if not refresh:
        cached = load_cached_usage(volume_name, path)
        if cached:
            return cached
        return None
    with console.status(
        f"[bold blue]Scanning {volume_name} recursively...[/] [dim]This can take a few minutes.[/]",
        spinner="dots",
    ):
        usage = calculate_volume_usage(volume_name, path, recursive=True)
    save_cached_usage(volume_name, path, usage)
    return usage


def print_volume_contents(
    volume_name: str,
    path: str = "/",
    *,
    refresh_usage: bool = False,
) -> None:
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

    usage = get_volume_usage(volume_name, path, refresh=refresh_usage)
    if usage:
        label = "Recursive file bytes" if usage.source == "fresh" else "Cached recursive bytes"
        print_result_panel(
            "[bold blue]Usage Reference[/bold blue]",
            [
                (label, _format_size(usage.total_size)),
                ("Files", usage.file_count),
                ("Directories scanned", usage.dir_count),
                ("Refreshed", usage.refreshed_at),
                ("Scan errors", len(usage.errors) or None),
                ("1 TiB reference", _usage_bar(usage.total_size)),
                ("Note", "Modal pricing currently includes 1 TiB/mo free storage."),
            ],
            border_style="cyan",
        )
        for error in usage.errors[:5]:
            console.print(f"[yellow]Usage scan skipped:[/] {error}")
    else:
        print_result_panel(
            "[bold yellow]Usage Reference[/bold yellow]",
            [
                ("Recursive bytes", "not scanned yet"),
                ("Action", "Run refresh usage to calculate and cache accurate size."),
                ("Note", "Modal pricing currently includes 1 TiB/mo free storage."),
            ],
            border_style="yellow",
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


def _model_delete_label(model: PreparedModelFile, orphan_cache_paths: set[str]) -> str:
    size = _format_size(model.size)
    cache_label = _compact_cache_path(model.cache_path)
    target = f"{model.model_dir}/{model.display_name}"
    if model.cache_path in orphan_cache_paths:
        return f"[ORPHAN] {target}  {size}  {cache_label}"
    return f"{target}  {size}  {cache_label}"


def _print_orphan_prune_table(models: list[PreparedModelFile]) -> None:
    table = Table(title="Orphaned prepared model files", show_lines=False)
    table.add_column("Source", style="dim", no_wrap=True)
    table.add_column("Target", style="white")
    table.add_column("Size", justify="right")
    table.add_column("Cache", style="dim")
    for model in sorted(models, key=lambda item: (item.model_dir, item.display_name)):
        table.add_row(
            model.source or "-",
            f"{model.model_dir}/{model.display_name}",
            _format_size(model.size),
            _compact_cache_path(model.cache_path),
        )
    console.print(table)


def prune_orphan_model_files(orphan_cache_paths: set[str] | None = None) -> set[str]:
    """Delete prepared model files that no longer map to config.toml entries."""
    orphan_cache_paths = set(orphan_cache_paths or ())
    if not orphan_cache_paths:
        print_status("No orphaned prepared model files detected.", style="green")
        return orphan_cache_paths

    try:
        models = list_prepared_model_files(include_sizes=True)
    except Exception as exc:
        print_status(f"Read prepared model manifest failed: {exc}", style="red")
        return orphan_cache_paths

    orphans = [model for model in models if model.cache_path in orphan_cache_paths]
    if not orphans:
        print_status("No orphaned prepared model files detected.", style="green")
        return orphan_cache_paths

    _print_orphan_prune_table(orphans)
    if not ask_confirm(
        f"Prune {len(orphans)} orphaned model file(s) from {CACHE_VOLUME}?",
        default=False,
        instruction="This removes only prepared cache files that are no longer referenced by config.toml.",
    ):
        return orphan_cache_paths

    selected = [model.cache_path for model in orphans]
    removed, failed = remove_volume_model_files(selected)
    orphan_cache_paths.difference_update(removed)
    print_result_panel(
        "[bold green]Orphan Prune Complete[/bold green]",
        [
            ("Deleted", len(removed)),
            ("Failed", len(failed) or None),
            ("Volume", CACHE_VOLUME),
        ],
    )
    for cache_path, error in failed[:5]:
        console.print(f"[red]Failed:[/] {cache_path} [dim]{error}[/]")
    return orphan_cache_paths


def delete_remote_model_files(orphan_cache_paths: set[str] | None = None) -> None:
    """Interactively delete prepared model files from comfy-cache."""
    orphan_cache_paths = set(orphan_cache_paths or ())
    try:
        models = list_prepared_model_files(include_sizes=True)
    except Exception as exc:
        print_status(f"Read prepared model manifest failed: {exc}", style="red")
        return

    if not models:
        print_status("No prepared model manifest entries found.", style="yellow")
        return

    choices = []
    for model in sorted(models, key=lambda item: (item.model_dir, item.display_name)):
        label = _model_delete_label(model, orphan_cache_paths)
        choices.append(questionary.Choice(label, value=model.cache_path))

    selected = questionary.checkbox(
        "Select remote model files to delete from comfy-cache:",
        choices=choices,
        style=STYLE,
    ).ask()
    if not selected:
        print_status("No remote model files selected.", style="yellow")
        return

    if not ask_confirm(
        f"Delete {len(selected)} remote model file(s) from {CACHE_VOLUME}?",
        default=False,
        instruction="This removes only the cached files, not config.toml entries.",
    ):
        return

    removed, failed = remove_volume_model_files(list(selected))
    print_result_panel(
        "[bold green]Remote Delete Complete[/bold green]",
        [
            ("Removed", len(removed)),
            ("Failed", len(failed) or None),
            ("Volume", CACHE_VOLUME),
        ],
    )
    for cache_path, error in failed[:5]:
        console.print(f"[red]Failed:[/] {cache_path} [dim]{error}[/]")


def volume_management_menu(orphan_cache_paths: set[str] | None = None) -> None:
    orphan_cache_paths = set(orphan_cache_paths or ())
    while True:
        choices = [
            questionary.Choice(
                "List comfy-cache",
                value=("list", CACHE_VOLUME),
                description="Tree view using cached recursive usage when available.",
            ),
            questionary.Choice(
                "Refresh comfy-cache usage",
                value=("refresh", CACHE_VOLUME),
                description="Run a recursive scan; this can take a few minutes on large caches.",
            ),
            questionary.Choice(
                "List comfy-output",
                value=("list", OUTPUT_VOLUME),
                description="Output tree using cached recursive usage when available.",
            ),
            questionary.Choice(
                "Refresh comfy-output usage",
                value=("refresh", OUTPUT_VOLUME),
                description="Run recursive usage scan and cache the result.",
            ),
            questionary.Choice(
                "Delete remote model files",
                value=("delete-models", CACHE_VOLUME),
                description="Delete selected prepared model files from comfy-cache.",
            ),
        ]
        if orphan_cache_paths:
            choices.append(
                questionary.Choice(
                    f"Prune orphaned models ({len(orphan_cache_paths)})",
                    value=("prune-orphans", CACHE_VOLUME),
                    description="Delete prepared model files no longer referenced by config.toml.",
                )
            )
        choices.extend([
            questionary.Choice(
                "Clean comfy-output sessions",
                value=("clean", OUTPUT_VOLUME),
                description="Delete selected old output session directories.",
            ),
            questionary.Choice("Back", value=("back", "")),
        ])
        action = ask_select(
            "Volume action:",
            choices=choices,
        )

        kind, volume_name = action
        if kind == "back":
            break
        if kind == "list":
            print_volume_contents(volume_name)
        elif kind == "refresh":
            print_volume_contents(volume_name, refresh_usage=True)
        elif kind == "delete-models":
            delete_remote_model_files(orphan_cache_paths=orphan_cache_paths)
        elif kind == "prune-orphans":
            orphan_cache_paths = prune_orphan_model_files(orphan_cache_paths=orphan_cache_paths)
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
    list_parser.add_argument(
        "--refresh-usage",
        action="store_true",
        help="Run recursive size scan and update the local usage cache.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        print_volume_contents(
            args.volume,
            args.path,
            refresh_usage=args.refresh_usage,
        )
        return 0

    print_banner(
        "ComfyUI Volumes",
        "Inspect comfy-cache/comfy-output and clean old output sessions.",
    )
    volume_management_menu()
    return 0


if __name__ == "__main__":
    sys.exit(main())
