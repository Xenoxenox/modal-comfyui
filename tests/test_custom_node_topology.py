from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRA_PATHS = REPO_ROOT / "extra_model_paths.yaml"


def _config_lines() -> list[str]:
    return [
        line.strip()
        for line in EXTRA_PATHS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_volume_custom_nodes_are_the_default_scan_path() -> None:
    """Manager installs into get_folder_paths("custom_nodes")[0], so the Volume must be default."""
    assert _config_lines() == [
        "volume-nodes:",
        "base_path: /cache",
        "custom_nodes: custom_nodes",
        "is_default: true",
    ]
