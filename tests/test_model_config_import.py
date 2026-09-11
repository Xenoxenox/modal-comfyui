from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))


import pytest

import manage
from config.loader import load_config, save_config
from config.schema import Config, ModalSecrets, ModelSource, ModelSpec, PluginSpec
from scripts.manage_volumes import PreparedModelFile


def _prepared(
    cache_path: str,
    target_path: str,
    *,
    source: str = "huggingface",
    display_name: str | None = None,
) -> PreparedModelFile:
    return PreparedModelFile(
        source=source,
        cache_path=cache_path,
        target_path=target_path,
        model_dir="unused",
        display_name=display_name or Path(target_path).name,
    )


def test_legacy_manifest_entries_become_local_model_specs() -> None:
    model_file = _prepared(
        "/cache/hub/models--owner--repo/snapshots/rev/model.safetensors",
        "/root/comfy/ComfyUI/models/checkpoints/model.safetensors",
    )
    snapshot = _prepared(
        "/cache/hub/models--owner--repo/snapshots/rev",
        "/root/comfy/ComfyUI/models/diffusers/repo",
        source="huggingface_snapshot",
    )

    file_spec = manage._model_spec_from_prepared(model_file)
    snapshot_spec = manage._model_spec_from_prepared(snapshot)

    assert file_spec == ModelSpec(
        source=ModelSource.LOCAL,
        filename="hub/models--owner--repo/snapshots/rev/model.safetensors",
        model_dir="checkpoints",
    )
    assert snapshot_spec == ModelSpec(
        source=ModelSource.LOCAL,
        filename="hub/models--owner--repo/snapshots/rev",
        model_dir="diffusers",
        save_as="repo",
    )


@pytest.mark.parametrize(
    ("cache_path", "target_path"),
    [
        ("/outside/model.safetensors", "/root/comfy/ComfyUI/models/checkpoints/model.safetensors"),
        ("/cache/model.safetensors", "/other/models/checkpoints/model.safetensors"),
        ("/cache", "/root/comfy/ComfyUI/models/checkpoints/model.safetensors"),
        ("/cache/model.safetensors", "/root/comfy/ComfyUI/models"),
        ("/cache/../model.safetensors", "/root/comfy/ComfyUI/models/checkpoints/model.safetensors"),
        ("/cache/model.safetensors", "/root/comfy/ComfyUI/models/../model.safetensors"),
    ],
)
def test_manifest_conversion_rejects_paths_outside_runtime_roots(
    cache_path: str,
    target_path: str,
) -> None:
    with pytest.raises(ValueError):
        manage._model_spec_from_prepared(_prepared(cache_path, target_path))


def test_merge_is_additive_deterministic_and_idempotent() -> None:
    existing = ModelSpec(
        source=ModelSource.LOCAL,
        filename="existing.safetensors",
        model_dir="checkpoints",
    )
    occupied_key = ModelSpec(
        source=ModelSource.LOCAL,
        filename="occupied.safetensors",
        model_dir="loras",
    )
    plugin = PluginSpec(node_id="example-node")
    secrets = ModalSecrets(hf_secret_name="private-hf", civitai_secret_name="private-civitai")
    cfg = Config(
        models={"existing": existing, "new-model": occupied_key},
        plugins={"example": plugin},
        modal_secrets=secrets,
    )
    prepared = [
        _prepared(
            "/cache/existing.safetensors",
            "/root/comfy/ComfyUI/models/checkpoints/existing.safetensors",
        ),
        _prepared(
            "/cache/new-model.safetensors",
            "/root/comfy/ComfyUI/models/vae/new-model.safetensors",
        ),
    ]

    imported, already_configured, invalid = manage._merge_prepared_models(cfg, prepared)

    assert imported == ["new-model-2"]
    assert already_configured == 1
    assert invalid == []
    assert cfg.models["existing"] is existing
    assert cfg.models["new-model"] is occupied_key
    assert cfg.models["new-model-2"].model_dir == "vae"
    assert cfg.plugins == {"example": plugin}
    assert cfg.modal_secrets == secrets

    imported, already_configured, invalid = manage._merge_prepared_models(cfg, prepared)
    assert imported == []
    assert already_configured == 2
    assert invalid == []


def test_merge_reports_invalid_entries_without_blocking_valid_siblings() -> None:
    cfg = Config(models={}, plugins={})
    prepared = [
        _prepared(
            "/outside/bad.safetensors",
            "/root/comfy/ComfyUI/models/checkpoints/bad.safetensors",
        ),
        _prepared(
            "/cache/good.safetensors",
            "/root/comfy/ComfyUI/models/checkpoints/good.safetensors",
        ),
    ]

    imported, already_configured, invalid = manage._merge_prepared_models(cfg, prepared)

    assert imported == ["good"]
    assert already_configured == 0
    assert len(invalid) == 1
    assert "/outside/bad.safetensors" in invalid[0]


def test_volume_import_round_trips_through_config_loader(tmp_path: Path, monkeypatch) -> None:
    prepared = _prepared(
        "/cache/reused/model.safetensors",
        "/root/comfy/ComfyUI/models/checkpoints/restored.safetensors",
        source="external",
    )
    cfg = Config(models={}, plugins={})
    monkeypatch.setattr(manage, "list_prepared_model_files", lambda **kwargs: [prepared])

    assert manage._import_models_from_volume(cfg) == 1

    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    restored = load_config(config_path)
    assert restored.models["restored"] == ModelSpec(
        source=ModelSource.LOCAL,
        filename="reused/model.safetensors",
        model_dir="checkpoints",
        save_as="restored.safetensors",
    )


def test_volume_read_failure_does_not_mutate_config(monkeypatch) -> None:
    original = ModelSpec(
        source=ModelSource.LOCAL,
        filename="existing.safetensors",
        model_dir="checkpoints",
    )
    cfg = Config(models={"existing": original}, plugins={})

    def fail(**kwargs):
        raise RuntimeError("not signed in")

    monkeypatch.setattr(manage, "list_prepared_model_files", fail)

    assert manage._import_models_from_volume(cfg) == 0
    assert cfg.models == {"existing": original}


def test_missing_config_can_restore_from_volume(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(manage, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        manage,
        "ask_select",
        lambda *args, **kwargs: "Restore models from comfy-cache",
    )

    def import_one(cfg: Config) -> int:
        cfg.models["restored"] = ModelSpec(
            source=ModelSource.LOCAL,
            filename="restored.safetensors",
            model_dir="checkpoints",
        )
        return 1

    monkeypatch.setattr(manage, "_import_models_from_volume", import_one)

    cfg = manage._ensure_config()

    assert cfg.models["restored"].source is ModelSource.LOCAL
    assert load_config(config_path) == cfg


def test_models_menu_exposes_volume_import(monkeypatch) -> None:
    actions = iter(["Import models from comfy-cache", "Back"])

    class Prompt:
        def ask(self) -> str:
            return next(actions)

    calls: list[str] = []
    monkeypatch.setattr(manage.questionary, "select", lambda *args, **kwargs: Prompt())
    monkeypatch.setattr(
        manage,
        "_import_models_from_volume",
        lambda cfg: calls.append("import") or 0,
    )
    monkeypatch.setattr(manage, "save_config", lambda cfg, path: calls.append("save"))

    manage._models_menu(Config(models={}, plugins={}))

    assert calls == ["import", "save"]
