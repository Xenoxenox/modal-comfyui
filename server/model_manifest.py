from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _link_cached_path(source_path: Path, target_path: Path) -> str:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() or target_path.is_symlink():
        target_path.unlink()
    target_path.symlink_to(source_path)
    return str(target_path)


def _manifest_item(source_path: Path, target_path: Path, source: str) -> dict[str, str]:
    return {
        "source": source,
        "cache_path": str(source_path),
        "target_path": str(target_path),
    }


def write_model_link_manifest(
    manifest_path: Path,
    items: list[dict[str, str]],
) -> None:
    manifest_path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def sync_prepared_model_links(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        print(
            "SKIP model link sync: no prepared model manifest found. "
            "Run `modal run server/app.py::prepare` first if models are missing.",
            flush=True,
        )
        return {"status": "missing_manifest", "linked": 0, "missing": []}

    try:
        items = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"SKIP model link sync: invalid manifest: {exc}", flush=True)
        return {"status": "invalid_manifest", "linked": 0, "missing": []}

    missing: list[dict[str, str]] = []
    linked = 0
    for item in items:
        source_path = Path(item["cache_path"])
        target_path = Path(item["target_path"])
        if not source_path.exists():
            missing.append(item)
            print(f"SKIP model link missing cache path: {source_path}", flush=True)
            continue
        _link_cached_path(source_path, target_path)
        linked += 1

    print(f"DONE model link sync: linked={linked} missing={len(missing)}", flush=True)
    return {"status": "ok", "linked": linked, "missing": missing}
