from __future__ import annotations

import ast
import json
import sys
from types import ModuleType
from pathlib import Path

import pytest

from server.model_manifest import link_cached_path, manifest_item, write_model_link_manifest


@pytest.mark.parametrize("source", ["huggingface", "huggingface_snapshot", "external", "local"])
def test_prepare_persists_loadable_cached_model(tmp_path: Path, monkeypatch, source: str) -> None:
    monkeypatch.setattr("config.loader.COMFY_ROOT", str(tmp_path / "comfy"))
    # Load real preparation functions without building a Modal image or probing secrets.
    app_path = Path(__file__).parents[1] / "server" / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    functions = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and not node.decorator_list],
        type_ignores=[],
    )
    namespace = {
        "Path": Path,
        "Any": object,
        "COMFY_ROOT": str(tmp_path / "comfy"),
        "CACHE_MOUNT": str(tmp_path / "cache"),
        "CONFIG_PATH": str(tmp_path / "config.toml"),
        "MANIFEST_PATH": tmp_path / "manifest.json",
        "link_cached_path": link_cached_path,
        "manifest_item": manifest_item,
        "write_model_link_manifest": write_model_link_manifest,
    }
    exec(compile(functions, str(app_path), "exec", flags=__import__("__future__").annotations.compiler_flag), namespace)
    cached = tmp_path / "cache" / "weight.safetensors"
    cached.parent.mkdir()
    cached.write_bytes(b"cached-model-content")
    if source == "huggingface_snapshot":
        cached = cached.parent
    hub = ModuleType("huggingface_hub")
    hub.hf_hub_download = lambda **kwargs: str(cached)
    hub.snapshot_download = lambda **kwargs: str(cached)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    if source == "huggingface_snapshot":
        fields = f'repo_id = "owner/model"\ntarget_dir = "{(tmp_path / "diffusers/model").as_posix()}"'
    elif source == "local":
        fields = 'filename = "weight.safetensors"\nmodel_dir = "diffusion_models"'
    elif source == "external":
        fields = 'url = "https://example.com/weight"\nfilename = "weight.safetensors"\nmodel_dir = "diffusion_models"'
    else:
        fields = 'repo_id = "owner/model"\nfilename = "weight.safetensors"\nmodel_dir = "diffusion_models"'
    Path(namespace["CONFIG_PATH"]).write_text(f'[models.example]\nsource = "{source}"\n{fields}\n', encoding="utf-8")
    result = namespace["prepare_model_links"]()
    assert result["linked"] == 1
    manifest = json.loads(Path(namespace["MANIFEST_PATH"]).read_text())
    target = Path(result["links"][0]["target_path"])
    assert target.is_symlink()
    model_file = target / "weight.safetensors" if target.is_dir() else target
    assert model_file.read_bytes() == b"cached-model-content"
    assert manifest[0]["cache_path"] == str(cached)
