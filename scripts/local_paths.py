"""Path and prompt helpers for the local TUI (no questionary, no network)."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fuzzy_match(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    if needle in haystack:
        return True
    pos = 0
    for char in haystack:
        if pos < len(needle) and char == needle[pos]:
            pos += 1
    return pos == len(needle)


def relative_volume_path(path: PurePosixPath) -> str:
    posix = path.as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix
    return posix


def normalise_cache_filename(filename: str) -> str:
    path = PurePosixPath(filename.strip().replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("remote filename must be a relative path inside comfy-cache")
    return path.as_posix()


def parse_local_path(raw_path: str) -> Path:
    return Path(raw_path.strip().strip("\"'")).expanduser()


def windows_separator_note(raw_path: str) -> str:
    if "\\" not in raw_path:
        return ""
    return "Windows separators are accepted locally; cache paths are normalized to POSIX / paths."


def nearby_directory_hint(text: str, *, limit: int = 5) -> str:
    candidate = Path(text.strip().strip("\"'")).expanduser()
    base = candidate if candidate.is_dir() else candidate.parent
    if not str(base) or not base.exists() or not base.is_dir():
        base = Path.cwd()
    try:
        directories = sorted(p.name for p in base.iterdir() if p.is_dir())[:limit]
    except OSError:
        return ""
    if not directories:
        return ""
    return " Available folders: " + ", ".join(directories)


def project_path_candidates(*, include_files: bool = False) -> list[str]:
    roots = [
        Path.cwd(),
        Path.cwd() / "workflows",
        Path.cwd() / "local-models",
    ]
    candidates: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        candidates.append(str(root))
        try:
            candidates.extend(
                str(path)
                for path in root.iterdir()
                if include_files or path.is_dir()
            )
        except OSError:
            continue
    return sorted(dict.fromkeys(candidates))


def nearest_project_path(raw_path: str) -> Path | None:
    needle = Path(raw_path.strip().strip("\"'")).name.lower()
    if not needle:
        return None
    matches = [
        Path(candidate)
        for candidate in project_path_candidates(include_files=True)
        if fuzzy_match(needle, Path(candidate).name.lower())
    ]
    files = [path for path in matches if path.is_file()]
    return (files or matches or [None])[0]
