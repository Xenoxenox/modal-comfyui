from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class DownloadResult:
    session_id: str
    output_dir: Path
    file_count: int
    total_bytes: int


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


def _join_volume_path(parent: str, child: str) -> str:
    if child.startswith("/"):
        return child
    if parent == "/":
        return f"/{child}"
    return f"{parent.rstrip('/')}/{PurePosixPath(child).name}"


def _read_volume_file(volume: Any, path: str) -> bytes:
    data = volume.read_file(path)
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    return b"".join(data)


def download_volume_session(
    volume: Any,
    session_id: str,
    *,
    local_root: Path = Path("output"),
) -> DownloadResult:
    """Download one comfy-output session directory into local output/<session_id>."""
    session_id = session_id.strip().strip("/")
    if not session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(f"Invalid session id: {session_id!r}")

    session_path = f"/{session_id}"
    output_dir = local_root / session_id
    file_count = 0
    total_bytes = 0
    stack = [session_path]

    while stack:
        current = stack.pop()
        for entry in volume.listdir(current):
            remote_path = _join_volume_path(current, str(entry.path))
            entry_type = _entry_type(entry)
            if entry_type == "dir":
                stack.append(remote_path)
                continue

            relative = PurePosixPath(remote_path).relative_to(PurePosixPath(session_path))
            local_path = output_dir.joinpath(*relative.parts)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            content = _read_volume_file(volume, remote_path)
            local_path.write_bytes(content)
            file_count += 1
            total_bytes += len(content)

    return DownloadResult(
        session_id=session_id,
        output_dir=output_dir,
        file_count=file_count,
        total_bytes=total_bytes,
    )
