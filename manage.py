#!/usr/bin/env python3
"""TUI manager for ComfyUI model and plugin configuration.

Usage:
    python manage.py
"""
from __future__ import annotations

import dataclasses
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import questionary
except ImportError:
    print("questionary is required. Run: uv sync")
    raise

from config.loader import load_config, save_config, ConfigError
from config.schema import Config, ModelSource, ModelSpec, PluginSpec, VALID_MODEL_DIRS

CONFIG_PATH = Path(__file__).parent / "config.toml"
EXAMPLE_PATH = Path(__file__).parent / "config.toml.example"


def _ensure_config() -> Config:
    if not CONFIG_PATH.exists():
        print(f"config.toml not found.")
        if questionary.confirm("Create empty config.toml?", default=True).ask():
            save_config(Config(models={}, plugins={}), CONFIG_PATH)
        else:
            sys.exit(1)
    return load_config(CONFIG_PATH)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ── HuggingFace Auto-Detect ──


def _parse_hf_input(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw.startswith("https://huggingface.co/"):
        raw = raw.removeprefix("https://huggingface.co/")
    parts = raw.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return raw


def _hf_list_files(repo_id: str) -> list[str] | None:
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        info = api.repo_info(repo_id)
        if info.siblings is None:
            return None
        return [s.rfilename for s in info.siblings]
    except Exception as e:
        print(f"  HF API failed: {e}")
        return None


_MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"}


def _is_model_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in _MODEL_EXTENSIONS


def _guess_model_dir(filename: str) -> str:
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
    }
    for part in path_parts:
        for hint, dir_name in dir_hints.items():
            if hint in part.lower():
                return dir_name

    for hint, dir_name in dir_hints.items():
        if hint in lower:
            return dir_name

    return "checkpoints"


# ── Add Model Flows ──


def _add_hf_model(cfg: Config) -> None:
    raw = questionary.text("HF repo (URL or owner/name):").ask()
    if not raw:
        return

    repo_id = _parse_hf_input(raw)
    print(f"  Repo: {repo_id}")

    files = _hf_list_files(repo_id)
    if files is not None:
        model_files = [f for f in files if _is_model_file(f)]
        if not model_files:
            print("  No model files found in repo.")
            return

        selected = questionary.checkbox(
            "Select files to install:",
            choices=[questionary.Choice(f, value=f) for f in model_files],
        ).ask()
        if not selected:
            return
    else:
        print("  Falling back to manual entry.")
        filename = questionary.text("Filename (path in repo):").ask()
        if not filename:
            return
        selected = [filename]

    bundle = None
    if len(selected) > 1:
        bundle = questionary.text(
            "Bundle name (groups these models, optional):",
            default=_slugify(repo_id.split("/")[-1]),
        ).ask() or None

    for filename in selected:
        suggested_dir = _guess_model_dir(filename)
        model_dir = questionary.select(
            f"Target dir for '{Path(filename).name}':",
            choices=VALID_MODEL_DIRS,
            default=suggested_dir,
        ).ask()

        original_name = Path(filename).name
        save_as_input = questionary.text(
            f"Save as (blank = '{original_name}'):",
            default="",
        ).ask()
        save_as = save_as_input or None

        default_key = _slugify(
            f"{repo_id.split('/')[-1]}-{save_as or original_name}".removesuffix(
                ".safetensors"
            )
        )
        key = questionary.text("Config key:", default=default_key).ask()
        if not key:
            continue

        if key in cfg.models:
            print(f"  Key '{key}' already exists, skipping.")
            continue

        cfg.models[key] = ModelSpec(
            source=ModelSource.HUGGINGFACE,
            repo_id=repo_id,
            filename=filename,
            model_dir=model_dir,
            save_as=save_as,
            bundle=bundle,
        )
        display_name = save_as or original_name
        print(f"  + {key}: {repo_id} → {model_dir}/{display_name}")


def _add_external_model(cfg: Config) -> None:
    url = questionary.text("Download URL:").ask()
    if not url:
        return

    filename = questionary.text("Filename:").ask()
    if not filename:
        return

    model_dir = questionary.select(
        "Target directory:",
        choices=VALID_MODEL_DIRS,
        default="loras",
    ).ask()

    bundle = questionary.text("Bundle name (optional):").ask() or None

    default_key = _slugify(filename.removesuffix(".safetensors"))
    key = questionary.text("Config key:", default=default_key).ask()
    if not key or key in cfg.models:
        print(f"  Key '{key}' conflict or empty, skipping.")
        return

    cfg.models[key] = ModelSpec(
        source=ModelSource.EXTERNAL,
        url=url,
        filename=filename,
        model_dir=model_dir,
        bundle=bundle,
    )
    print(f"  + {key}: {url} → {model_dir}/{filename}")


def _add_snapshot_model(cfg: Config) -> None:
    raw = questionary.text("HF repo (URL or owner/name):").ask()
    if not raw:
        return

    repo_id = _parse_hf_input(raw)

    target_dir = questionary.text(
        "Target directory (absolute path):",
        default=f"/root/comfy/ComfyUI/models/diffusers/{repo_id.split('/')[-1]}",
    ).ask()
    if not target_dir:
        return

    default_key = _slugify(repo_id.split("/")[-1])
    key = questionary.text("Config key:", default=default_key).ask()
    if not key or key in cfg.models:
        print(f"  Key '{key}' conflict or empty, skipping.")
        return

    cfg.models[key] = ModelSpec(
        source=ModelSource.HUGGINGFACE_SNAPSHOT,
        repo_id=repo_id,
        target_dir=target_dir,
    )
    print(f"  + {key}: snapshot {repo_id} → {target_dir}")


# ── Model Management ──


def _list_models(cfg: Config) -> None:
    if not cfg.models:
        print("  No models configured.")
        return

    bundles: dict[str | None, list[tuple[str, ModelSpec]]] = defaultdict(list)
    for key, spec in cfg.models.items():
        bundles[spec.bundle].append((key, spec))

    total = len(cfg.models)
    print(f"\nModels ({total} total):")

    for bundle_name in sorted(bundles, key=lambda x: (x is None, x or "")):
        items = bundles[bundle_name]
        if bundle_name:
            print(f"\n  Bundle: {bundle_name} ({len(items)} models)")
        else:
            print(f"\n  Standalone:")

        for key, spec in items:
            src_label = spec.source.value[:2].upper()
            if spec.source == ModelSource.HUGGINGFACE:
                target = f"{spec.model_dir}/{spec.save_as or Path(spec.filename).name}"
                print(f"    {key:<25} {src_label}  {spec.repo_id}  → {target}")
            elif spec.source == ModelSource.HUGGINGFACE_SNAPSHOT:
                print(f"    {key:<25} SN  {spec.repo_id}  → {spec.target_dir}")
            elif spec.source == ModelSource.EXTERNAL:
                target = f"{spec.model_dir}/{spec.filename}"
                print(f"    {key:<25} EX  {spec.url[:40]}...  → {target}")
    print()


def _remove_models(cfg: Config) -> None:
    if not cfg.models:
        print("  No models to remove.")
        return

    choices = []
    for key, spec in cfg.models.items():
        label = f"[{spec.bundle}] {key}" if spec.bundle else key
        choices.append(questionary.Choice(label, value=key))

    to_remove = questionary.checkbox("Select models to remove:", choices=choices).ask()
    if not to_remove:
        return

    if not questionary.confirm(
        f"Remove {len(to_remove)} model(s)?", default=False
    ).ask():
        return

    for key in to_remove:
        del cfg.models[key]
        print(f"  - {key}")


def _manage_bundles(cfg: Config) -> None:
    if not cfg.models:
        print("  No models configured.")
        return

    bundles: dict[str | None, list[str]] = defaultdict(list)
    for key, spec in cfg.models.items():
        bundles[spec.bundle].append(key)

    existing_names = sorted(n for n in bundles if n)

    # Show current state
    print()
    for name in existing_names:
        print(f"  Bundle: {name} ({len(bundles[name])} models)")
    if None in bundles:
        keys = bundles[None]
        print(f"  Standalone ({len(keys)}): {', '.join(keys)}")
    print()

    # Select models to reassign
    choices = []
    for key, spec in cfg.models.items():
        label = f"[{spec.bundle}] {key}" if spec.bundle else key
        choices.append(questionary.Choice(label, value=key))

    selected = questionary.checkbox(
        "Select models to assign/move:",
        choices=choices,
    ).ask()
    if not selected:
        return

    # Choose destination
    dest_choices = [
        *existing_names,
        questionary.Separator(),
        "(new bundle)",
        "(standalone)",
    ]
    dest = questionary.select(
        "Target bundle:",
        choices=dest_choices,
    ).ask()
    if not dest:
        return

    if dest == "(new bundle)":
        dest = questionary.text("New bundle name:").ask()
        if not dest:
            return
    elif dest == "(standalone)":
        dest = None

    for key in selected:
        old = cfg.models[key]
        cfg.models[key] = dataclasses.replace(old, bundle=dest)
        label = dest or "(standalone)"
        print(f"  {key} → {label}")


# ── Plugin Management ──


def _add_plugin(cfg: Config) -> None:
    source = questionary.select(
        "Plugin source:",
        choices=["ComfyUI Registry (node ID)", "GitHub repo URL"],
    ).ask()

    if source == "ComfyUI Registry (node ID)":
        node_id = questionary.text("Node ID:").ask()
        if not node_id:
            return
        name = questionary.text("Display name (optional):").ask() or None
        key = _slugify(node_id)
        cfg.plugins[key] = PluginSpec(node_id=node_id, name=name)
        print(f"  + {key}: {node_id}")
    else:
        repo_url = questionary.text("GitHub repo URL:").ask()
        if not repo_url:
            return
        repo_url = repo_url.strip().rstrip("/")
        # Derive key from last two path segments (owner/repo)
        parts = repo_url.rstrip("/").split("/")
        repo_slug = "-".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        default_key = _slugify(repo_slug)
        name = questionary.text("Display name (optional):").ask() or None
        key = questionary.text("Config key:", default=default_key).ask()
        if not key:
            return
        if key in cfg.plugins:
            print(f"  Key '{key}' already exists, skipping.")
            return
        cfg.plugins[key] = PluginSpec(repo=repo_url, name=name)
        print(f"  + {key}: {repo_url}")


def _list_plugins(cfg: Config) -> None:
    if not cfg.plugins:
        print("  No plugins configured.")
        return
    print(f"\nPlugins ({len(cfg.plugins)} total):")
    for key, spec in cfg.plugins.items():
        name_str = f"  ({spec.name})" if spec.name else ""
        if spec.repo:
            id_str = spec.repo
        else:
            id_str = spec.node_id or ""
        print(f"  {key:<25} {id_str}{name_str}")
    print()


def _remove_plugins(cfg: Config) -> None:
    if not cfg.plugins:
        print("  No plugins to remove.")
        return

    choices = [
        questionary.Choice(f"{key} ({spec.repo or spec.node_id})", value=key)
        for key, spec in cfg.plugins.items()
    ]
    to_remove = questionary.checkbox("Select plugins to remove:", choices=choices).ask()
    if not to_remove:
        return

    if not questionary.confirm(
        f"Remove {len(to_remove)} plugin(s)?", default=False
    ).ask():
        return

    for key in to_remove:
        del cfg.plugins[key]
        print(f"  - {key}")


# ── Deploy ──


def _deploy(cfg: Config) -> None:
    action = questionary.select(
        "Deploy action:",
        choices=[
            "Deploy to Modal (modal deploy server/ui.py)",
            "Dev serve (python serve.py)",
            "Back",
        ],
    ).ask()

    if action and "modal deploy" in action:
        import os

        print("\nRunning: modal deploy server/ui.py")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        subprocess.run(["modal", "deploy", "server/ui.py"], env=env)
    elif action and "serve" in action:
        print("\nRunning: python serve.py")
        subprocess.run([sys.executable, "serve.py"])


# ── Main Menu ──


def _models_menu(cfg: Config) -> None:
    while True:
        action = questionary.select(
            "Model action:",
            choices=[
                "Add model (HuggingFace)",
                "Add model (CivitAI / External URL)",
                "Add model (HF Snapshot)",
                "List models",
                "Manage bundles",
                "Remove model",
                "Back",
            ],
        ).ask()

        if not action or action == "Back":
            break
        elif "HuggingFace" in action:
            _add_hf_model(cfg)
        elif "External" in action:
            _add_external_model(cfg)
        elif "Snapshot" in action:
            _add_snapshot_model(cfg)
        elif "List" in action:
            _list_models(cfg)
        elif "Manage bundles" in action:
            _manage_bundles(cfg)
        elif "Remove" in action:
            _remove_models(cfg)

        save_config(cfg, CONFIG_PATH)


def _plugins_menu(cfg: Config) -> None:
    while True:
        action = questionary.select(
            "Plugin action:",
            choices=["Add plugin", "List plugins", "Remove plugin", "Back"],
        ).ask()

        if not action or action == "Back":
            break
        elif "Add" in action:
            _add_plugin(cfg)
        elif "List" in action:
            _list_plugins(cfg)
        elif "Remove" in action:
            _remove_plugins(cfg)

        save_config(cfg, CONFIG_PATH)


def main() -> None:
    print("ComfyUI Config Manager\n")

    try:
        cfg = _ensure_config()
    except ConfigError as e:
        print(f"Config error: {e}")
        sys.exit(1)

    print(
        f"  Loaded: {len(cfg.models)} model(s), {len(cfg.plugins)} plugin(s)\n"
    )

    while True:
        choice = questionary.select(
            "What do you want to do?",
            choices=[
                "Manage models",
                "Manage plugins",
                "Deploy to Modal",
                "Exit",
            ],
        ).ask()

        if not choice or choice == "Exit":
            break
        elif choice == "Manage models":
            _models_menu(cfg)
        elif choice == "Manage plugins":
            _plugins_menu(cfg)
        elif choice == "Deploy to Modal":
            _deploy(cfg)

    print("Done.")


if __name__ == "__main__":
    main()
