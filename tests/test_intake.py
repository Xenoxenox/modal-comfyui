from __future__ import annotations

import pytest

from config.intake import (
    build_external_spec,
    build_huggingface_spec,
    build_local_spec,
    build_snapshot_spec,
)
from config.loader import ConfigError
from config.schema import ModelSource


def test_builders_return_valid_model_specs() -> None:
    hf = build_huggingface_spec(
        "owner/repo",
        "models/model.safetensors",
        "checkpoints",
        save_as="model.safetensors",
        bundle="base",
    )
    external = build_external_spec(
        "https://example.com/model.safetensors",
        "model.safetensors",
        "loras",
        bundle="styles",
    )
    local = build_local_spec(
        "local-models/loras/model.safetensors",
        "loras",
        save_as="alias.safetensors",
        bundle="local",
    )
    snapshot = build_snapshot_spec(
        "owner/snapshot",
        "/root/comfy/ComfyUI/models/diffusers/snapshot",
    )

    assert hf.source == ModelSource.HUGGINGFACE
    assert hf.repo_id == "owner/repo"
    assert hf.filename == "models/model.safetensors"
    assert hf.model_dir == "checkpoints"
    assert hf.save_as == "model.safetensors"
    assert hf.bundle == "base"

    assert external.source == ModelSource.EXTERNAL
    assert external.url == "https://example.com/model.safetensors"
    assert external.filename == "model.safetensors"
    assert external.model_dir == "loras"
    assert external.bundle == "styles"

    assert local.source == ModelSource.LOCAL
    assert local.filename == "local-models/loras/model.safetensors"
    assert local.model_dir == "loras"
    assert local.save_as == "alias.safetensors"
    assert local.bundle == "local"

    assert snapshot.source == ModelSource.HUGGINGFACE_SNAPSHOT
    assert snapshot.repo_id == "owner/snapshot"
    assert snapshot.target_dir == "/root/comfy/ComfyUI/models/diffusers/snapshot"


@pytest.mark.parametrize(
    ("builder", "args", "message"),
    [
        (
            build_huggingface_spec,
            ("owner/repo", "model.safetensors", None),
            "source=huggingface requires model_dir",
        ),
        (
            build_external_spec,
            ("https://example.com/model.safetensors", None, "loras"),
            "source=external requires filename",
        ),
        (
            build_local_spec,
            (None, "loras"),
            "source=local requires filename",
        ),
        (
            build_snapshot_spec,
            ("owner/repo", None),
            "source=huggingface_snapshot requires target_dir",
        ),
    ],
)
def test_builders_raise_config_error_for_missing_required_fields(
    builder,
    args: tuple[object, ...],
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        builder(*args)


def test_model_dir_must_be_known_or_absolute() -> None:
    with pytest.raises(ConfigError, match="VALID_MODEL_DIRS"):
        build_huggingface_spec(
            "owner/repo",
            "model.safetensors",
            "relative/custom",
        )

    spec = build_huggingface_spec(
        "owner/repo",
        "model.safetensors",
        "/root/comfy/ComfyUI/models/custom",
    )

    assert spec.model_dir == "/root/comfy/ComfyUI/models/custom"
