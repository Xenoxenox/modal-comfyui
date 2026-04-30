from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelSource(str, Enum):
    HUGGINGFACE = "huggingface"
    HUGGINGFACE_SNAPSHOT = "huggingface_snapshot"
    EXTERNAL = "external"


VALID_MODEL_DIRS = [
    "checkpoints",
    "clip",
    "clip_vision",
    "controlnet",
    "diffusers",
    "diffusion_models",
    "embeddings",
    "gligen",
    "hypernetworks",
    "loras",
    "photomaker",
    "style_models",
    "text_encoders",
    "unet",
    "upscale_models",
    "vae",
    "vae_approx",
]


@dataclass(frozen=True)
class ModelSpec:
    source: ModelSource
    repo_id: str | None = None
    filename: str | None = None
    model_dir: str | None = None
    save_as: str | None = None
    target_dir: str | None = None
    url: str | None = None
    bundle: str | None = None


@dataclass(frozen=True)
class PluginSpec:
    node_id: str | None = None
    name: str | None = None
    repo: str | None = None


@dataclass(frozen=True)
class Config:
    models: dict[str, ModelSpec]
    plugins: dict[str, PluginSpec]
