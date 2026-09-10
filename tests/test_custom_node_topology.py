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


def test_image_custom_nodes_directory_is_never_relocated() -> None:
    for path in (
        EXTRA_PATHS,
        REPO_ROOT / "server" / "ui.py",
        REPO_ROOT / "server" / "comfy_runtime.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "blessed_custom_nodes" not in source, path
        assert "symlink_to" not in source, path


def test_both_entrypoints_prepare_the_volume_node_directory() -> None:
    for name in ("server/ui.py", "server/generate.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "ensure_runtime_dirs()" in source, name
