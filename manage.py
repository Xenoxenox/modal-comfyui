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

import requests

try:
    import questionary
    from questionary import Style
except ImportError:
    print("questionary is required. Run: uv sync")
    raise

from config.loader import load_config, save_config, ConfigError
from config.schema import Config, ModelSource, ModelSpec, PluginSpec, VALID_MODEL_DIRS

# ── ANSI Colors (Modal-inspired dark + green theme) ──
G = "\033[92m"   # bright green (accent)
G0 = "\033[32m"  # dim green (secondary)
W = "\033[97m"   # bright white (headings)
D = "\033[37m"   # dim gray (body)
R = "\033[91m"   # red (errors)
B = "\033[1m"    # bold
RST = "\033[0m"  # reset

STYLE = Style([
    ("qmark", "fg:#3DCA5D bold"),
    ("question", "fg:#ffffff bold"),
    ("answer", "fg:#3DCA5D bold"),
    ("pointer", "fg:#3DCA5D bold"),
    ("highlighted", "fg:#3DCA5D bold"),
    ("selected", "fg:#3DCA5D"),
    ("separator", "fg:#059443"),
    ("instruction", "fg:#888888"),
    ("text", "fg:#cccccc"),
    ("checkbox", "fg:#3DCA5D"),
    ("disabled", "fg:#555555"),
])

CONFIG_PATH = Path(__file__).parent / "config.toml"
EXAMPLE_PATH = Path(__file__).parent / "config.toml.example"


def _ensure_config() -> Config:
    if not CONFIG_PATH.exists():
        print(f"  {D}config.toml not found.{RST}")
        if questionary.confirm("Create empty config.toml?", default=True, style=STYLE).ask():
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
        print(f"  {R}HF API failed:{RST} {e}")
        return None


_MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx"}


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
        "inswapper": "insightface",
        "insightface": "insightface",
        "facerestore": "facerestore_models",
        "face_restore": "facerestore_models",
        "gfpgan": "facerestore_models",
        "codeformer": "facerestore_models",
    }
    for part in path_parts:
        for hint, dir_name in dir_hints.items():
            if hint in part.lower():
                return dir_name

    for hint, dir_name in dir_hints.items():
        if hint in lower:
            return dir_name

    return "checkpoints"


# ── CivitAI URL Resolution ──

_CIVITAI_PAGE_RE = re.compile(r"civitai\.com/models/(\d+)")
_CIVITAI_API = "https://civitai.com/api/v1/models"

_CIVITAI_TROUBLESHOOT = """\
CivitAI API request failed. Common issues:
  1. Missing API key — add CIVITAI_API=<your-key> to .env at repo root
     Get your key: https://civitai.com/user/account → API Keys
  2. Network blocked — set proxy env vars before running:
       export HTTP_PROXY=http://127.0.0.1:<port>
       export HTTPS_PROXY=http://127.0.0.1:<port>
     CivitAI is blocked in mainland China without a proxy."""


def _load_civitai_key() -> str | None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CIVITAI_API="):
            return line.split("=", 1)[1].strip()
    return None


def _resolve_civitai_url(url: str) -> tuple[str, str] | None:
    """Resolve a CivitAI page URL to (download_url, filename). Returns None if not applicable."""
    if "civitai.com/api/download/" in url:
        return None  # already a download URL

    m = _CIVITAI_PAGE_RE.search(url)
    if not m:
        return None  # not a CivitAI URL

    model_id = m.group(1)
    api_key = _load_civitai_key()
    if not api_key:
        print("  " + _CIVITAI_TROUBLESHOOT.replace("\n", "\n  "))
        return None

    try:
        resp = requests.get(
            f"{_CIVITAI_API}/{model_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  {R}CivitAI API error:{RST} {e}")
        print(f"  {D}" + _CIVITAI_TROUBLESHOOT.replace("\n", f"\n  {D}") + f"{RST}")
        return None

    data = resp.json()
    versions = data.get("modelVersions")
    if not versions:
        print(f"  {R}CivitAI model {model_id} has no versions.{RST}")
        return None

    ver = versions[0]  # latest version
    download_url = ver.get("downloadUrl")
    files = ver.get("files", [])
    filename = files[0]["name"] if files else None

    if not download_url or not filename:
        print(f"  {R}CivitAI model {model_id}: missing download URL or filename.{RST}")
        return None

    return download_url, filename


# ── Add Model Flows ──


def _add_hf_model(cfg: Config) -> None:
    raw = questionary.text("HF repo (URL or owner/name):", style=STYLE).ask()
    if not raw:
        return

    repo_id = _parse_hf_input(raw)
    print(f"  {D}Repo:{RST} {W}{repo_id}{RST}")

    files = _hf_list_files(repo_id)
    if files is not None:
        model_files = [f for f in files if _is_model_file(f)]
        if not model_files:
            print(f"  {D}No model files found in repo.{RST}")
            return

        selected = questionary.checkbox(
            "Select files to install:",
            choices=[questionary.Choice(f, value=f) for f in model_files],
            style=STYLE,
        ).ask()
        if not selected:
            return
    else:
        print(f"  {D}Falling back to manual entry.{RST}")
        filename = questionary.text("Filename (path in repo):", style=STYLE).ask()
        if not filename:
            return
        selected = [filename]

    bundle = None
    if len(selected) > 1:
        bundle = questionary.text(
            "Bundle name (groups these models, optional):",
            default=_slugify(repo_id.split("/")[-1]),
            style=STYLE,
        ).ask() or None

    for filename in selected:
        suggested_dir = _guess_model_dir(filename)
        model_dir = questionary.select(
            f"Target dir for '{Path(filename).name}':",
            choices=VALID_MODEL_DIRS,
            default=suggested_dir,
            style=STYLE,
        ).ask()

        original_name = Path(filename).name
        save_as_input = questionary.text(
            f"Save as (blank = '{original_name}'):",
            default="",
            style=STYLE,
        ).ask()
        save_as = save_as_input or None

        name_for_key = save_as or original_name
        name_for_key = Path(name_for_key).stem  # strip extension for key
        default_key = _slugify(
            f"{repo_id.split('/')[-1]}-{name_for_key}"
        )
        key = questionary.text("Config key:", default=default_key, style=STYLE).ask()
        if not key:
            continue

        if key in cfg.models:
            print(f"  {R}Key '{key}' already exists, skipping.{RST}")
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
        print(f"  {G}+{RST} {W}{key}{RST}: {D}{repo_id} → {model_dir}/{display_name}{RST}")


def _add_external_model(cfg: Config) -> None:
    url = questionary.text("Download URL:", style=STYLE).ask()
    if not url:
        return

    resolved = _resolve_civitai_url(url)
    if resolved:
        url, default_filename = resolved
        print(f"  {G}Resolved:{RST} {W}{url}{RST}")
        filename = questionary.text("Filename:", default=default_filename, style=STYLE).ask()
    else:
        filename = questionary.text("Filename:", style=STYLE).ask()
    if not filename:
        return

    model_dir = questionary.select(
        "Target directory:",
        choices=VALID_MODEL_DIRS,
        default="loras",
        style=STYLE,
    ).ask()

    bundle = questionary.text("Bundle name (optional):", style=STYLE).ask() or None

    default_key = _slugify(Path(filename).stem)
    key = questionary.text("Config key:", default=default_key, style=STYLE).ask()
    if not key or key in cfg.models:
        print(f"  {R}Key '{key}' conflict or empty, skipping.{RST}")
        return

    cfg.models[key] = ModelSpec(
        source=ModelSource.EXTERNAL,
        url=url,
        filename=filename,
        model_dir=model_dir,
        bundle=bundle,
    )
    print(f"  {G}+{RST} {W}{key}{RST}: {D}{url} → {model_dir}/{filename}{RST}")


def _add_snapshot_model(cfg: Config) -> None:
    raw = questionary.text("HF repo (URL or owner/name):", style=STYLE).ask()
    if not raw:
        return

    repo_id = _parse_hf_input(raw)

    target_dir = questionary.text(
        "Target directory (absolute path):",
        default=f"/root/comfy/ComfyUI/models/diffusers/{repo_id.split('/')[-1]}",
        style=STYLE,
    ).ask()
    if not target_dir:
        return

    default_key = _slugify(repo_id.split("/")[-1])
    key = questionary.text("Config key:", default=default_key, style=STYLE).ask()
    if not key or key in cfg.models:
        print(f"  {R}Key '{key}' conflict or empty, skipping.{RST}")
        return

    cfg.models[key] = ModelSpec(
        source=ModelSource.HUGGINGFACE_SNAPSHOT,
        repo_id=repo_id,
        target_dir=target_dir,
    )
    print(f"  {G}+{RST} {W}{key}{RST}: {D}snapshot {repo_id} → {target_dir}{RST}")


# ── Model Management ──


def _list_models(cfg: Config) -> None:
    if not cfg.models:
        print(f"  {D}No models configured.{RST}")
        return

    bundles: dict[str | None, list[tuple[str, ModelSpec]]] = defaultdict(list)
    for key, spec in cfg.models.items():
        bundles[spec.bundle].append((key, spec))

    total = len(cfg.models)
    print(f"\n  {W}{B}Models{RST} {D}({total} total){RST}")

    for bundle_name in sorted(bundles, key=lambda x: (x is None, x or "")):
        items = bundles[bundle_name]
        if bundle_name:
            print(f"\n  {G0}{B}Bundle: {bundle_name}{RST} {D}({len(items)} models){RST}")
        else:
            print(f"\n  {D}Standalone:{RST}")

        for key, spec in items:
            src_label = spec.source.value[:2].upper()
            if spec.source == ModelSource.HUGGINGFACE:
                target = f"{spec.model_dir}/{spec.save_as or Path(spec.filename).name}"
                print(f"    {W}{key:<25}{RST} {G0}{src_label}{RST}  {D}{spec.repo_id}{RST}  {G0}→{RST} {D}{target}{RST}")
            elif spec.source == ModelSource.HUGGINGFACE_SNAPSHOT:
                print(f"    {W}{key:<25}{RST} {G0}SN{RST}  {D}{spec.repo_id}{RST}  {G0}→{RST} {D}{spec.target_dir}{RST}")
            elif spec.source == ModelSource.EXTERNAL:
                target = f"{spec.model_dir}/{spec.filename}"
                print(f"    {W}{key:<25}{RST} {G0}EX{RST}  {D}{spec.url[:40]}...{RST}  {G0}→{RST} {D}{target}{RST}")
    print()


def _remove_models(cfg: Config) -> None:
    if not cfg.models:
        print(f"  {D}No models to remove.{RST}")
        return

    choices = []
    for key, spec in cfg.models.items():
        label = f"[{spec.bundle}] {key}" if spec.bundle else key
        choices.append(questionary.Choice(label, value=key))

    to_remove = questionary.checkbox("Select models to remove:", choices=choices, style=STYLE).ask()
    if not to_remove:
        return

    if not questionary.confirm(
        f"Remove {len(to_remove)} model(s)?", default=False, style=STYLE
    ).ask():
        return

    for key in to_remove:
        del cfg.models[key]
        print(f"  {R}-{RST} {W}{key}{RST}")


def _manage_bundles(cfg: Config) -> None:
    if not cfg.models:
        print(f"  {D}No models configured.{RST}")
        return

    bundles: dict[str | None, list[str]] = defaultdict(list)
    for key, spec in cfg.models.items():
        bundles[spec.bundle].append(key)

    existing_names = sorted(n for n in bundles if n)

    # Show current state
    print()
    for name in existing_names:
        print(f"  {G0}{B}Bundle: {name}{RST} {D}({len(bundles[name])} models){RST}")
    if None in bundles:
        keys = bundles[None]
        print(f"  {D}Standalone ({len(keys)}):{RST} {D}{', '.join(keys)}{RST}")
    print()

    # Select models to reassign
    choices = []
    for key, spec in cfg.models.items():
        label = f"[{spec.bundle}] {key}" if spec.bundle else key
        choices.append(questionary.Choice(label, value=key))

    selected = questionary.checkbox(
        "Select models to assign/move:",
        choices=choices,
        style=STYLE,
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
        style=STYLE,
    ).ask()
    if not dest:
        return

    if dest == "(new bundle)":
        dest = questionary.text("New bundle name:", style=STYLE).ask()
        if not dest:
            return
    elif dest == "(standalone)":
        dest = None

    for key in selected:
        old = cfg.models[key]
        cfg.models[key] = dataclasses.replace(old, bundle=dest)
        label = dest or "(standalone)"
        print(f"  {G}→{RST} {W}{key}{RST} {D}→ {label}{RST}")


# ── Plugin Management ──


def _add_plugin(cfg: Config) -> None:
    source = questionary.select(
        "Plugin source:",
        choices=["ComfyUI Registry (node ID)", "GitHub repo URL"],
        style=STYLE,
    ).ask()

    if source == "ComfyUI Registry (node ID)":
        node_id = questionary.text("Node ID:", style=STYLE).ask()
        if not node_id:
            return
        name = questionary.text("Display name (optional):", style=STYLE).ask() or None
        key = _slugify(node_id)
        cfg.plugins[key] = PluginSpec(node_id=node_id, name=name)
        print(f"  {G}+{RST} {W}{key}{RST}: {D}{node_id}{RST}")
    else:
        repo_url = questionary.text("GitHub repo URL:", style=STYLE).ask()
        if not repo_url:
            return
        repo_url = repo_url.strip().rstrip("/")
        # Derive key from last two path segments (owner/repo)
        parts = repo_url.rstrip("/").split("/")
        repo_slug = "-".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        default_key = _slugify(repo_slug)
        name = questionary.text("Display name (optional):", style=STYLE).ask() or None
        key = questionary.text("Config key:", default=default_key, style=STYLE).ask()
        if not key:
            return
        if key in cfg.plugins:
            print(f"  {R}Key '{key}' already exists, skipping.{RST}")
            return
        cfg.plugins[key] = PluginSpec(repo=repo_url, name=name)
        print(f"  {G}+{RST} {W}{key}{RST}: {D}{repo_url}{RST}")


def _list_plugins(cfg: Config) -> None:
    if not cfg.plugins:
        print(f"  {D}No plugins configured.{RST}")
        return
    print(f"\n  {W}{B}Plugins{RST} {D}({len(cfg.plugins)} total){RST}")
    for key, spec in cfg.plugins.items():
        name_str = f"  {D}({spec.name}){RST}" if spec.name else ""
        if spec.repo:
            id_str = spec.repo
        else:
            id_str = spec.node_id or ""
        print(f"  {W}{key:<25}{RST} {D}{id_str}{RST}{name_str}")
    print()


def _remove_plugins(cfg: Config) -> None:
    if not cfg.plugins:
        print(f"  {D}No plugins to remove.{RST}")
        return

    choices = [
        questionary.Choice(f"{key} ({spec.repo or spec.node_id})", value=key)
        for key, spec in cfg.plugins.items()
    ]
    to_remove = questionary.checkbox("Select plugins to remove:", choices=choices, style=STYLE).ask()
    if not to_remove:
        return

    if not questionary.confirm(
        f"Remove {len(to_remove)} plugin(s)?", default=False, style=STYLE
    ).ask():
        return

    for key in to_remove:
        del cfg.plugins[key]
        print(f"  {R}-{RST} {W}{key}{RST}")


# ── Deploy ──


def _deploy(cfg: Config) -> None:
    action = questionary.select(
        "Deploy action:",
        choices=[
            "Deploy to Modal (modal deploy server/ui.py)",
            "Dev serve (python serve.py)",
            "Back",
        ],
        style=STYLE,
    ).ask()

    if action and "modal deploy" in action:
        import os

        print(f"\n  {G}Running:{RST} {W}modal deploy server/ui.py{RST}")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        subprocess.run(["modal", "deploy", "server/ui.py"], env=env)
    elif action and "serve" in action:
        print(f"\n  {G}Running:{RST} {W}python serve.py{RST}")
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
            style=STYLE,
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
            style=STYLE,
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
    print()
    print(f"  {G}{B}┌─────────────────────────────────┐{RST}")
    print(f"  {G}{B}│{RST}  {W}{B}ComfyUI  Config  Manager{RST}       {G}{B}│{RST}")
    print(f"  {G}{B}└─────────────────────────────────┘{RST}")
    print()

    try:
        cfg = _ensure_config()
    except ConfigError as e:
        print(f"  {R}{B}Config error:{RST} {e}")
        sys.exit(1)

    n_models = len(cfg.models)
    n_plugins = len(cfg.plugins)
    print(f"  {D}{n_models} models · {n_plugins} plugins{RST}\n")

    while True:
        choice = questionary.select(
            "What do you want to do?",
            choices=[
                "Manage models",
                "Manage plugins",
                "Deploy to Modal",
                "Exit",
            ],
            style=STYLE,
        ).ask()

        if not choice or choice == "Exit":
            break
        elif choice == "Manage models":
            _models_menu(cfg)
        elif choice == "Manage plugins":
            _plugins_menu(cfg)
        elif choice == "Deploy to Modal":
            _deploy(cfg)

    print(f"\n  {G}{B}Done.{RST}\n")


if __name__ == "__main__":
    main()
