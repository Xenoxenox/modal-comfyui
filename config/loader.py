from __future__ import annotations

import tomllib
from pathlib import Path

from config.schema import Config, ModelSource, ModelSpec, PluginSpec, VALID_MODEL_DIRS

COMFY_ROOT = "/root/comfy/ComfyUI"
ROOT = Path(__file__).parent.parent

_CONFIG_SEARCH_PATHS = [
    ROOT / "config.toml",
    Path("/root/config.toml"),
]


class ConfigError(Exception):
    pass


def _validate_model(key: str, spec: ModelSpec) -> None:
    if spec.source == ModelSource.HUGGINGFACE:
        missing = []
        if not spec.repo_id:
            missing.append("repo_id")
        if not spec.filename:
            missing.append("filename")
        if not spec.model_dir:
            missing.append("model_dir")
        if missing:
            raise ConfigError(
                f"models.{key}: source=huggingface requires {', '.join(missing)}"
            )
    elif spec.source == ModelSource.HUGGINGFACE_SNAPSHOT:
        missing = []
        if not spec.repo_id:
            missing.append("repo_id")
        if not spec.target_dir:
            missing.append("target_dir")
        if missing:
            raise ConfigError(
                f"models.{key}: source=huggingface_snapshot requires {', '.join(missing)}"
            )
    elif spec.source == ModelSource.EXTERNAL:
        missing = []
        if not spec.url:
            missing.append("url")
        if not spec.filename:
            missing.append("filename")
        if not spec.model_dir:
            missing.append("model_dir")
        if missing:
            raise ConfigError(
                f"models.{key}: source=external requires {', '.join(missing)}"
            )

    if spec.model_dir and spec.model_dir not in VALID_MODEL_DIRS:
        if not spec.model_dir.startswith("/"):
            raise ConfigError(
                f"models.{key}: model_dir={spec.model_dir!r} is not in "
                f"VALID_MODEL_DIRS and is not an absolute path"
            )


def _parse_model(key: str, data: dict) -> ModelSpec:
    source_str = data.get("source")
    if not source_str:
        raise ConfigError(f"models.{key}: missing 'source' field")
    try:
        source = ModelSource(source_str)
    except ValueError:
        valid = ", ".join(s.value for s in ModelSource)
        raise ConfigError(
            f"models.{key}: invalid source={source_str!r}, must be one of: {valid}"
        )

    known_fields = {
        "source", "repo_id", "filename", "model_dir",
        "save_as", "target_dir", "url", "bundle",
    }
    unknown = set(data.keys()) - known_fields
    if unknown:
        import warnings
        warnings.warn(f"models.{key}: unknown fields ignored: {unknown}")

    spec = ModelSpec(
        source=source,
        repo_id=data.get("repo_id"),
        filename=data.get("filename"),
        model_dir=data.get("model_dir"),
        save_as=data.get("save_as"),
        target_dir=data.get("target_dir"),
        url=data.get("url"),
        bundle=data.get("bundle"),
    )
    _validate_model(key, spec)
    return spec


def _parse_plugin(key: str, data: dict) -> PluginSpec:
    repo = data.get("repo")
    node_id = data.get("node_id")
    if not node_id and not repo:
        raise ConfigError(f"plugins.{key}: must have 'node_id' or 'repo'")
    return PluginSpec(
        node_id=node_id,
        name=data.get("name"),
        repo=repo,
    )


def load_config(path: Path | None = None) -> Config:
    if path is None:
        for candidate in _CONFIG_SEARCH_PATHS:
            if candidate.exists():
                path = candidate
                break
        else:
            raise ConfigError(
                "Config file not found. Searched:\n"
                + "\n".join(f"  {p}" for p in _CONFIG_SEARCH_PATHS)
                + "\nCopy config.toml.example to config.toml and edit it."
            )
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    models: dict[str, ModelSpec] = {}
    for key, data in raw.get("models", {}).items():
        models[key] = _parse_model(key, data)

    plugins: dict[str, PluginSpec] = {}
    for key, data in raw.get("plugins", {}).items():
        plugins[key] = _parse_plugin(key, data)

    return Config(models=models, plugins=plugins)


def save_config(config: Config, path: Path | None = None) -> None:
    try:
        import tomli_w
    except ImportError:
        raise ImportError(
            "tomli-w is required to write config. "
            "Install it: uv add tomli-w"
        )

    if path is None:
        path = ROOT / "config.toml"

    data: dict = {}

    if config.models:
        data["models"] = {}
        for key, spec in config.models.items():
            entry: dict = {"source": spec.source.value}
            if spec.repo_id:
                entry["repo_id"] = spec.repo_id
            if spec.filename:
                entry["filename"] = spec.filename
            if spec.model_dir:
                entry["model_dir"] = spec.model_dir
            if spec.save_as:
                entry["save_as"] = spec.save_as
            if spec.target_dir:
                entry["target_dir"] = spec.target_dir
            if spec.url:
                entry["url"] = spec.url
            if spec.bundle:
                entry["bundle"] = spec.bundle
            data["models"][key] = entry

    if config.plugins:
        data["plugins"] = {}
        for key, spec in config.plugins.items():
            entry: dict = {}
            if spec.node_id:
                entry["node_id"] = spec.node_id
            if spec.name:
                entry["name"] = spec.name
            if spec.repo:
                entry["repo"] = spec.repo
            data["plugins"][key] = entry

    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def to_legacy(
    config: Config,
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    models: list[dict] = []
    models_snapshot: list[dict] = []
    models_ext: list[dict] = []
    comfy_plugins: list[str] = []

    for spec in config.models.values():
        if spec.source == ModelSource.HUGGINGFACE:
            entry: dict = {
                "repo_id": spec.repo_id,
                "filename": spec.filename,
                "model_dir": f"{COMFY_ROOT}/models/{spec.model_dir}",
            }
            if spec.save_as:
                entry["save_as"] = spec.save_as
            models.append(entry)
        elif spec.source == ModelSource.HUGGINGFACE_SNAPSHOT:
            models_snapshot.append({
                "repo_id": spec.repo_id,
                "target_dir": spec.target_dir,
            })
        elif spec.source == ModelSource.EXTERNAL:
            models_ext.append({
                "url": spec.url,
                "filename": spec.filename,
                "model_dir": f"{COMFY_ROOT}/models/{spec.model_dir}",
            })

    for spec in config.plugins.values():
        install_id = spec.repo or spec.node_id
        if install_id:
            comfy_plugins.append(install_id)

    return models, models_snapshot, models_ext, comfy_plugins
