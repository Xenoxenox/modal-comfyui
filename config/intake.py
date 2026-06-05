from __future__ import annotations

from config.loader import validate_model
from config.schema import ModelSource, ModelSpec


def build_huggingface_spec(
    repo_id: str | None,
    filename: str | None,
    model_dir: str | None,
    save_as: str | None = None,
    bundle: str | None = None,
) -> ModelSpec:
    spec = ModelSpec(
        source=ModelSource.HUGGINGFACE,
        repo_id=repo_id,
        filename=filename,
        model_dir=model_dir,
        save_as=save_as,
        bundle=bundle,
    )
    validate_model("<intake>", spec)
    return spec


def build_external_spec(
    url: str | None,
    filename: str | None,
    model_dir: str | None,
    bundle: str | None = None,
) -> ModelSpec:
    spec = ModelSpec(
        source=ModelSource.EXTERNAL,
        url=url,
        filename=filename,
        model_dir=model_dir,
        bundle=bundle,
    )
    validate_model("<intake>", spec)
    return spec


def build_local_spec(
    filename: str | None,
    model_dir: str | None,
    save_as: str | None = None,
    bundle: str | None = None,
) -> ModelSpec:
    spec = ModelSpec(
        source=ModelSource.LOCAL,
        filename=filename,
        model_dir=model_dir,
        save_as=save_as,
        bundle=bundle,
    )
    validate_model("<intake>", spec)
    return spec


def build_snapshot_spec(
    repo_id: str | None,
    target_dir: str | None,
) -> ModelSpec:
    spec = ModelSpec(
        source=ModelSource.HUGGINGFACE_SNAPSHOT,
        repo_id=repo_id,
        target_dir=target_dir,
    )
    validate_model("<intake>", spec)
    return spec
