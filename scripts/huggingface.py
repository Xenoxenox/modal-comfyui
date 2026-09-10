from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import requests

MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx"}


def parse_repo_id(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw.startswith("https://huggingface.co/"):
        raw = raw.removeprefix("https://huggingface.co/")
    parts = raw.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else raw


def list_repo_files(repo_id: str) -> list[str] | None:
    token = os.environ.get("HF_TOKEN")
    response = requests.get(
        f"https://huggingface.co/api/models/{quote(repo_id, safe='/')}",
        params={"expand[]": "siblings"},
        headers={"Authorization": f"Bearer {token}"} if token else None,
        timeout=15,
    )
    response.raise_for_status()
    siblings = response.json().get("siblings")
    if siblings is None:
        return None
    return [item["rfilename"] for item in siblings]


def is_model_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in MODEL_EXTENSIONS

def guess_model_dir(filename: str) -> str:
    lower = filename.lower()
    path_parts = Path(filename).parts
    dir_hints = {
        "unet": "unet",
        "transformer": "unet",
        "text_encoder": "clip",
        "clip": "clip",
        "vae": "vae",
        "lora": "loras",
        "controlnet": "controlnet",
        "embedding": "embeddings",
        "upscale": "upscale_models",
        "inswapper": "insightface",
        "insightface": "insightface",
        "facerestore": "facerestore_models",
        "face_restore": "facerestore_models",
        "gfpgan": "facerestore_models",
        "codeformer": "facerestore_models",
    }
    for part in path_parts:
        for hint, model_dir in dir_hints.items():
            if hint in part.lower():
                return model_dir
    for hint, model_dir in dir_hints.items():
        if hint in lower:
            return model_dir
    return "checkpoints"
