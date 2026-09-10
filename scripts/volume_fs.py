from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def entry_type(entry: Any) -> str:
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
    return "dir" if getattr(entry, "is_dir", False) else "file"


def join_volume_path(parent: str, child: str) -> str:
    if child.startswith("/"):
        return child
    if parent == "/":
        return f"/{child}"
    return f"{parent.rstrip('/')}/{PurePosixPath(child).name}"


def read_volume_file(volume: Any, path: str) -> bytes:
    data = volume.read_file(path)
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    return b"".join(data)
