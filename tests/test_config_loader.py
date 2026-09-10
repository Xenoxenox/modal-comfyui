from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import ConfigError, load_config
from config.schema import ModelSource


def _load_single_model(tmp_path: Path, model_toml: str):
    path = tmp_path / "config.toml"
    path.write_text(f"[models.example]\n{model_toml}", encoding="utf-8")
    return load_config(path).models["example"]


def test_loader_builds_all_model_sources(tmp_path: Path) -> None:
    huggingface = _load_single_model(
        tmp_path,
        'source = "huggingface"\nrepo_id = "owner/repo"\nfilename = "model.safetensors"\nmodel_dir = "checkpoints"\nsave_as = "alias.safetensors"\nbundle = "base"\n',
    )
    assert huggingface.source is ModelSource.HUGGINGFACE
    assert huggingface.save_as == "alias.safetensors"

    external = _load_single_model(
        tmp_path,
        'source = "external"\nurl = "https://example.com/model.safetensors"\nfilename = "model.safetensors"\nmodel_dir = "loras"\n',
    )
    assert external.source is ModelSource.EXTERNAL


def test_loader_rejects_unknown_model_directories(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="VALID_MODEL_DIRS"):
        _load_single_model(
            tmp_path,
            'source = "huggingface"\nrepo_id = "owner/repo"\nfilename = "model.safetensors"\nmodel_dir = "relative/custom"\n',
        )

    absolute = _load_single_model(
        tmp_path,
        'source = "huggingface"\nrepo_id = "owner/repo"\nfilename = "model.safetensors"\nmodel_dir = "/root/comfy/ComfyUI/models/custom"\n',
    )
    assert absolute.model_dir == "/root/comfy/ComfyUI/models/custom"


@pytest.mark.parametrize(
    ("model_toml", "message"),
    [
        ('source = "huggingface"\nrepo_id = "owner/repo"\nfilename = "model.safetensors"\n', "source=huggingface requires model_dir"),
        ('source = "external"\nurl = "https://example.com/model.safetensors"\nmodel_dir = "loras"\n', "source=external requires filename"),
        ('source = "local"\nmodel_dir = "loras"\n', "source=local requires filename"),
        ('source = "huggingface_snapshot"\nrepo_id = "owner/repo"\n', "source=huggingface_snapshot requires target_dir"),
    ],
)
def test_loader_requires_source_specific_fields(
    tmp_path: Path,
    model_toml: str,
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        _load_single_model(tmp_path, model_toml)
