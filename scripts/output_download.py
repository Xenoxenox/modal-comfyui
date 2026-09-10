from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.volume_fs import entry_type, join_volume_path, read_volume_file


@dataclass(frozen=True)
class DownloadResult:
    session_id: str
    output_dir: Path
    file_count: int
    total_bytes: int




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
            remote_path = join_volume_path(current, str(entry.path))
            entry_kind = entry_type(entry)
            if entry_kind == "dir":
                stack.append(remote_path)
                continue

            relative = PurePosixPath(remote_path).relative_to(PurePosixPath(session_path))
            local_path = output_dir.joinpath(*relative.parts)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            content = read_volume_file(volume, remote_path)
            local_path.write_bytes(content)
            file_count += 1
            total_bytes += len(content)

    return DownloadResult(
        session_id=session_id,
        output_dir=output_dir,
        file_count=file_count,
        total_bytes=total_bytes,
    )
