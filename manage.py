#!/usr/bin/env python3
"""TUI manager for ComfyUI model and plugin configuration.

Usage:
    python manage.py
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import re
import subprocess
import sys
from collections.abc import Callable
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests

try:
    import questionary
except ImportError:
    print("questionary is required. Run: uv sync")
    raise

from config.loader import load_config, save_config, ConfigError
from config.schema import (
    Config,
    ModalSecrets,
    ModelSource,
    ModelSpec,
    PluginSpec,
    VALID_MODEL_DIRS,
)
from scripts.manage_volumes import volume_management_menu
from scripts.manage_volumes import list_prepared_model_files, remove_volume_model_files
from scripts.billing import print_exit_summary
from scripts.modal_status import (
    ExpectedModalSecret,
    ModalStatusSnapshot,
    fresh_modal_status_snapshot,
    modal_auth_is_missing,
    sanitize_modal_error,
)
from scripts.preferences import load_preferences, save_preferences
from scripts.tui import (
    STYLE,
    ask_confirm,
    ask_select,
    console,
    gpu_choice_items,
    model_card,
    plugin_card,
    print_banner,
    print_command_panel,
    print_model_cards,
    print_plugin_cards,
    print_result_panel,
    print_status,
    print_step,
    source_badge,
)

# ── ANSI Colors (Modal-inspired dark + green theme) ──
G = "\033[92m"   # bright green (accent)
G0 = "\033[32m"  # dim green (secondary)
W = "\033[97m"   # bright white (headings)
D = "\033[37m"   # dim gray (body)
R = "\033[91m"   # red (errors)
B = "\033[1m"    # bold
RST = "\033[0m"  # reset

CONFIG_PATH = Path(__file__).parent / "config.toml"
EXAMPLE_PATH = Path(__file__).parent / "config.toml.example"
CACHE_VOLUME = "comfy-cache"
LOCAL_MODEL_CACHE_DIR = PurePosixPath("local-models")
WEB_UI_GPU_ENV = "COMFYUI_WEB_GPU"
MODAL_HF_SECRET_NAME_ENV = "MODAL_HF_SECRET_NAME"
MODAL_CIVITAI_SECRET_NAME_ENV = "MODAL_CIVITAI_SECRET_NAME"
DEFAULT_HF_SECRET_NAME = "ComfyUI"
DEFAULT_CIVITAI_SECRET_NAME = "civitai-api-key"
DISABLED_SECRET_NAMES = {"", "none", "false"}
DEFAULT_WEB_UI_GPU = "L4"
EMPTY_WEB_UI_GPU = "T4"
BACK_ACTION = "back"

MODAL_SECRET_SPECS = [
    {
        "label": "Hugging Face",
        "env_var": MODAL_HF_SECRET_NAME_ENV,
        "default_name": DEFAULT_HF_SECRET_NAME,
        "key": "HF_TOKEN",
        "purpose": "private or gated Hugging Face model downloads",
    },
    {
        "label": "CivitAI",
        "env_var": MODAL_CIVITAI_SECRET_NAME_ENV,
        "default_name": DEFAULT_CIVITAI_SECRET_NAME,
        "key": "CIVITAI_API_KEY",
        "purpose": "private or token-gated CivitAI downloads",
    },
]


@dataclasses.dataclass(frozen=True)
class ModalSecretStatus:
    label: str
    name: str | None
    key: str
    status: str
    detail: str


def _ensure_config() -> Config:
    if not CONFIG_PATH.exists():
        print(f"  {D}config.toml not found.{RST}")
        if ask_confirm("Create empty config.toml?", default=True):
            save_config(Config(models={}, plugins={}), CONFIG_PATH)
        else:
            sys.exit(1)
    return load_config(CONFIG_PATH)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _compact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url[:60]
    if "civitai.com" in parsed.netloc:
        match = re.search(r"/(?:api/download/models|models)/(\d+)", parsed.path)
        if match:
            return f"civitai:{match.group(1)}"
    return parsed.netloc


def _fuzzy_match(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    if needle in haystack:
        return True
    pos = 0
    for char in haystack:
        if pos < len(needle) and char == needle[pos]:
            pos += 1
    return pos == len(needle)


def _rel_to_volume_path(path: PurePosixPath) -> str:
    posix = path.as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix
    return posix


def _normalise_cache_filename(filename: str) -> str:
    path = PurePosixPath(filename.strip().replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("remote filename must be a relative path inside comfy-cache")
    return path.as_posix()


def _parse_local_path(raw_path: str) -> Path:
    return Path(raw_path.strip().strip("\"'")).expanduser()


def _windows_path_note(raw_path: str) -> str:
    if "\\" not in raw_path:
        return ""
    return "Windows separators are accepted locally; cache paths are normalized to POSIX / paths."


def nearby_directory_hint(text: str, *, limit: int = 5) -> str:
    candidate = Path(text.strip().strip("\"'")).expanduser()
    base = candidate if candidate.is_dir() else candidate.parent
    if not str(base) or not base.exists() or not base.is_dir():
        base = Path.cwd()
    try:
        directories = sorted(p.name for p in base.iterdir() if p.is_dir())[:limit]
    except OSError:
        return ""
    if not directories:
        return ""
    return " Available folders: " + ", ".join(directories)


def _project_path_candidates() -> list[str]:
    roots = [
        Path.cwd(),
        Path.cwd() / "workflows",
        Path.cwd() / "local-models",
    ]
    candidates: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        candidates.append(str(root))
        try:
            candidates.extend(str(path) for path in root.iterdir())
        except OSError:
            continue
    return sorted(dict.fromkeys(candidates))


def _nearest_project_path(raw_path: str) -> Path | None:
    needle = Path(raw_path.strip().strip("\"'")).name.lower()
    if not needle:
        return None
    matches = [
        Path(candidate)
        for candidate in _project_path_candidates()
        if _fuzzy_match(needle, Path(candidate).name.lower())
    ]
    files = [path for path in matches if path.is_file()]
    return (files or matches or [None])[0]


def _upload_to_cache(local_path: Path, cache_filename: str) -> bool:
    try:
        import modal
    except ImportError:
        print(f"  {R}modal is required. Run: uv sync{RST}")
        return False

    remote_rel = PurePosixPath(cache_filename)
    print(
        f"  {D}Uploading to Modal Volume {W}{CACHE_VOLUME}{RST}{D}: "
        f"{remote_rel.as_posix()}{RST}"
    )
    try:
        volume = modal.Volume.from_name(CACHE_VOLUME, create_if_missing=True)
        with volume.batch_upload(force=True) as batch:
            batch.put_file(str(local_path), _rel_to_volume_path(remote_rel))
    except Exception as e:
        print(f"  {R}Upload failed:{RST} {e}")
        return False

    print(f"  {G}Uploaded.{RST}")
    return True


# ── Modal Secrets ──


def _configured_secret_name(env_var: str, default: str) -> str | None:
    secret_name = os.environ.get(env_var, default).strip()
    if secret_name.lower() in DISABLED_SECRET_NAMES:
        return None
    return secret_name


def _modal_secret_config_name(cfg: Config | None, env_var: str) -> str | None:
    if cfg is None:
        return None
    if env_var == MODAL_HF_SECRET_NAME_ENV:
        return cfg.modal_secrets.hf_secret_name
    if env_var == MODAL_CIVITAI_SECRET_NAME_ENV:
        return cfg.modal_secrets.civitai_secret_name
    return None


def _effective_secret_name(cfg: Config | None, spec: dict) -> str | None:
    secret_name = os.environ.get(spec["env_var"])
    if secret_name is None:
        secret_name = _modal_secret_config_name(cfg, spec["env_var"])
    if secret_name is None:
        secret_name = spec["default_name"]
    secret_name = secret_name.strip()
    if secret_name.lower() in DISABLED_SECRET_NAMES:
        return None
    return secret_name


def _set_modal_secret_config(cfg: Config, env_var: str, secret_name: str) -> None:
    current = cfg.modal_secrets
    hf_secret_name = current.hf_secret_name
    civitai_secret_name = current.civitai_secret_name
    if env_var == MODAL_HF_SECRET_NAME_ENV:
        hf_secret_name = secret_name
    elif env_var == MODAL_CIVITAI_SECRET_NAME_ENV:
        civitai_secret_name = secret_name
    cfg.modal_secrets = ModalSecrets(
        hf_secret_name=hf_secret_name,
        civitai_secret_name=civitai_secret_name,
    )


def _expected_modal_secrets(
    cfg: Config | None,
    configured_overrides: dict[str, str] | None = None,
) -> list[ExpectedModalSecret]:
    configured_overrides = configured_overrides or {}
    expected: list[ExpectedModalSecret] = []
    for spec in MODAL_SECRET_SPECS:
        secret_name = configured_overrides.get(spec["env_var"])
        if secret_name is None:
            secret_name = _effective_secret_name(cfg, spec)
        expected.append(
            ExpectedModalSecret(
                label=spec["label"],
                name=secret_name,
                key=spec["key"],
                env_var=spec["env_var"],
            )
        )
    return expected


def _modal_secret_names() -> tuple[set[str], str | None]:
    try:
        import modal
    except ImportError:
        return set(), "modal is required. Run: uv sync"

    try:
        return {secret.name for secret in modal.Secret.objects.list() if secret.name}, None
    except Exception as exc:
        return set(), sanitize_modal_error(str(exc))


def _modal_secret_statuses(
    secret_names: set[str],
    list_error: str | None = None,
    cfg: Config | None = None,
    configured_overrides: dict[str, str] | None = None,
) -> list[ModalSecretStatus]:
    statuses: list[ModalSecretStatus] = []
    configured_overrides = configured_overrides or {}
    for spec in MODAL_SECRET_SPECS:
        secret_name = configured_overrides.get(spec["env_var"])
        if secret_name is None:
            secret_name = _effective_secret_name(cfg, spec)
        if secret_name is None:
            status = "disabled"
            detail = f"disabled via {spec['env_var']}"
        elif list_error and "Token missing" in list_error:
            status = "skipped"
            detail = "blocked by sign-in"
        elif list_error:
            status = "unknown"
            detail = sanitize_modal_error(list_error)
        elif secret_name in secret_names:
            status = "ok"
            detail = f"{secret_name} ({spec['key']})"
        else:
            status = "missing"
            detail = f"{secret_name} ({spec['key']})"
        statuses.append(
            ModalSecretStatus(
                label=spec["label"],
                name=secret_name,
                key=spec["key"],
                status=status,
                detail=detail,
            )
        )
    return statuses


def _modal_secret_status_rows(statuses: list[ModalSecretStatus]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for status in statuses:
        if status.status == "ok":
            value = f"OK: {status.detail}"
        elif status.status == "missing":
            value = f"MISSING: {status.detail}"
        elif status.status == "disabled":
            value = status.detail
        elif status.status == "skipped":
            value = f"SKIPPED: {status.detail}"
        else:
            value = f"UNKNOWN: {status.detail}"
        rows.append((status.label, value))
    return rows


def _print_modal_secret_status(
    cfg: Config | None = None,
    configured_overrides: dict[str, str] | None = None,
    known_existing: set[str] | None = None,
) -> None:
    secret_names, list_error = _modal_secret_names()
    if known_existing:
        secret_names.update(known_existing)
    statuses = _modal_secret_statuses(secret_names, list_error, cfg, configured_overrides)
    rows = _modal_secret_status_rows(statuses)
    missing = any(status.status == "missing" for status in statuses)
    unknown = any(status.status in {"unknown", "skipped"} for status in statuses)
    print_result_panel(
        "[bold yellow]Modal Secrets[/bold yellow]" if missing or unknown else "[bold green]Modal Secrets[/bold green]",
        rows,
        border_style="yellow" if missing or unknown else "green",
    )


def _print_modal_status_snapshot(snapshot: ModalStatusSnapshot) -> None:
    account_warn = snapshot.account.status != "ok"
    print_result_panel(
        "[bold yellow]Modal Account[/bold yellow]" if account_warn else "[bold green]Modal Account[/bold green]",
        [
            ("Status", snapshot.account.status.upper()),
            ("Profile", snapshot.account.profile),
            ("Detail", snapshot.account.detail),
        ],
        border_style="yellow" if account_warn else "green",
    )

    rows: list[tuple[str, str]] = []
    for status in snapshot.secrets:
        if status.status == "ok":
            value = f"OK: {status.detail}"
        elif status.status == "missing":
            value = f"MISSING: {status.detail}"
        elif status.status == "disabled":
            value = status.detail
        elif status.status == "skipped":
            value = f"SKIPPED: {status.detail}"
        else:
            value = f"UNKNOWN: {status.detail}"
        rows.append((status.label, value))
    warn = any(status.status in {"missing", "unknown", "skipped"} for status in snapshot.secrets)
    print_result_panel(
        "[bold yellow]Modal Secrets[/bold yellow]" if warn else "[bold green]Modal Secrets[/bold green]",
        rows,
        border_style="yellow" if warn else "green",
    )


def _fresh_modal_status(cfg: Config, *, known_existing: set[str] | None = None) -> ModalStatusSnapshot:
    return fresh_modal_status_snapshot(
        _expected_modal_secrets(cfg),
        known_existing=known_existing,
    )


def _prompt_modal_setup_if_needed(cfg: Config) -> None:
    snapshot = _fresh_modal_status(cfg)
    _print_modal_status_snapshot(snapshot)
    if not modal_auth_is_missing(snapshot):
        return

    try:
        run_setup = ask_confirm("Modal token is missing. Do you want to run 'modal setup' now?", True)
    except KeyboardInterrupt:
        return
    if not run_setup:
        return

    print_status(
        "[bold blue]Starting Modal setup. Complete the browser flow, then return here.[/bold blue]",
        style="blue",
    )
    result = subprocess.run([sys.executable, "-m", "modal", "setup"], check=False)
    if result.returncode == 0:
        print_status("[bold green]Modal setup finished. Refreshed status is shown below.[/bold green]", style="green")
    else:
        print_status(
            f"[yellow]Modal setup exited with code {result.returncode}. You can retry later or continue in the TUI.[/yellow]",
            style="yellow",
        )
    _print_modal_status_snapshot(_fresh_modal_status(cfg))


def _upsert_modal_secret(secret_name: str, key: str, value: str) -> None:
    import modal
    from modal.exception import NotFoundError

    try:
        secret = modal.Secret.from_name(secret_name)
        secret.info()
    except NotFoundError:
        modal.Secret.objects.create(secret_name, {key: value})
    else:
        secret.update({key: value})


def _configure_modal_secrets_menu(cfg: Config) -> None:
    snapshot = _fresh_modal_status(cfg)
    if snapshot.account.status == "missing":
        _print_modal_status_snapshot(snapshot)
        print_status("Sign in to Modal before creating or updating Modal Secrets.", style="yellow")
        return
    secret_names = {status.name for status in snapshot.secrets if status.status == "ok" and status.name}
    list_error = None if snapshot.account.status != "missing" else "Token missing. Run `modal setup` to sign in."
    choices = []
    for spec in MODAL_SECRET_SPECS:
        secret_name = _effective_secret_name(cfg, spec)
        if secret_name is None:
            description = f"Disabled via {spec['env_var']}."
        elif list_error:
            description = f"Configure {secret_name} with key {spec['key']}."
        elif secret_name in secret_names:
            description = f"Update {secret_name} with key {spec['key']}."
        else:
            description = f"Create {secret_name} with key {spec['key']}."
        choices.append(questionary.Choice(spec["label"], value=spec, description=description))
    choices.append(questionary.Choice("Back", value=BACK_ACTION, description="Return to the main menu."))

    spec = ask_select(
        "Configure Modal Secret:",
        choices=choices,
        instruction="Tokens are sent to Modal only and are not written to config.toml or logs.",
    )
    if spec == BACK_ACTION:
        return

    default_secret_name = _effective_secret_name(cfg, spec)
    if default_secret_name is None:
        default_secret_name = spec["default_name"]
    secret_name = questionary.autocomplete(
        "Modal Secret name:",
        choices=[spec["default_name"], *sorted(secret_names - {spec["default_name"]})],
        default=default_secret_name,
        style=STYLE,
    ).ask()
    if secret_name is None:
        raise KeyboardInterrupt
    secret_name = secret_name.strip()
    if not secret_name:
        print_status("No secret name entered; secret unchanged.", style="yellow")
        return

    token = questionary.password(
        f"{spec['key']} for Modal Secret {secret_name}:",
        style=STYLE,
    ).ask()
    if token is None:
        raise KeyboardInterrupt
    token = token.strip()
    if not token:
        print_status("No token entered; secret unchanged.", style="yellow")
        return

    _upsert_modal_secret(secret_name, spec["key"], token)
    os.environ[spec["env_var"]] = secret_name
    _set_modal_secret_config(cfg, spec["env_var"], secret_name)
    save_config(cfg, CONFIG_PATH)
    print_status(f"Modal Secret {secret_name} is configured.", style="green")
    _print_modal_secret_status(
        cfg,
        known_existing={secret_name},
    )


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


def _add_local_model(cfg: Config) -> None:
    raw_path = questionary.path(
        "Local model file:",
        get_paths=_project_path_candidates,
        instruction=nearby_directory_hint(""),
        style=STYLE,
    ).ask()
    if not raw_path:
        return
    separator_note = _windows_path_note(raw_path)
    if separator_note:
        console.print(f"  [dim]{separator_note}[/]")

    local_path = _parse_local_path(raw_path)
    if not local_path.exists() or not local_path.is_file():
        suggestion = _nearest_project_path(raw_path)
        if suggestion and suggestion.exists() and suggestion.is_file():
            if ask_confirm(
                f"File not found. Did you mean {suggestion}?",
                default=True,
            ):
                local_path = suggestion
            else:
                return
        else:
            print(f"  {R}File not found:{RST} {local_path}")
            hint = nearby_directory_hint(raw_path)
            if hint:
                console.print(f"  [dim]{hint}[/]")
            return
    if not local_path.is_file():
        print(f"  {R}Not a file:{RST} {local_path}")
        hint = nearby_directory_hint(raw_path)
        if hint:
            console.print(f"  [dim]{hint}[/]")
        return
    if not _is_model_file(local_path.name):
        print(f"  {R}Unsupported model file extension:{RST} {local_path.suffix}")
        return

    model_dir = questionary.select(
        "Target directory:",
        choices=VALID_MODEL_DIRS,
        default=_guess_model_dir(local_path.name),
        style=STYLE,
    ).ask()
    if not model_dir:
        return

    save_as_input = questionary.text(
        f"Save as in ComfyUI (blank = '{local_path.name}'):",
        default="",
        style=STYLE,
    ).ask()
    save_as = save_as_input or None
    display_name = save_as or local_path.name

    default_cache_filename = (
        LOCAL_MODEL_CACHE_DIR / model_dir / Path(display_name).name
    ).as_posix()
    cache_filename_input = questionary.text(
        "Cache path in comfy-cache:",
        default=default_cache_filename,
        style=STYLE,
    ).ask()
    if not cache_filename_input:
        return

    try:
        cache_filename = _normalise_cache_filename(cache_filename_input)
    except ValueError as e:
        print(f"  {R}Invalid cache path:{RST} {e}")
        return

    bundle = questionary.text("Bundle name (optional):", style=STYLE).ask() or None

    default_key = _slugify(Path(display_name).stem)
    key = questionary.text("Config key:", default=default_key, style=STYLE).ask()
    if not key or key in cfg.models:
        print(f"  {R}Key '{key}' conflict or empty, skipping.{RST}")
        return

    print(
        f"\n  {D}Source:{RST} {W}{local_path}{RST}"
        f"\n  {D}Target:{RST} {W}{model_dir}/{display_name}{RST}"
        f"\n  {D}Cache:{RST}  {W}{cache_filename}{RST}\n"
    )
    if not ask_confirm("Upload now?", default=True):
        return

    if not _upload_to_cache(local_path, cache_filename):
        return

    cfg.models[key] = ModelSpec(
        source=ModelSource.LOCAL,
        filename=cache_filename,
        model_dir=model_dir,
        save_as=save_as,
        bundle=bundle,
    )
    print(
        f"  {G}+{RST} {W}{key}{RST}: "
        f"{D}{CACHE_VOLUME}/{cache_filename} → {model_dir}/{display_name}{RST}"
    )


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
        orphan_count = len(_orphan_prepared_model_files(cfg, include_sizes=False))
        if orphan_count:
            console.print(
                f"\n[bold white]Models[/] [dim](0 configured)[/] "
                f"[bold yellow]![/] [dim]{orphan_count} orphan"
                f"{'' if orphan_count == 1 else 's'} detected in cloud[/]"
            )
        print(f"  {D}No models configured.{RST}")
        return

    filter_text = questionary.text(
        "Filter models (blank = all):",
        default="",
        style=STYLE,
    ).ask()
    if filter_text is None:
        return
    needle = filter_text.strip().lower()

    bundles: dict[str | None, list[tuple[str, ModelSpec]]] = defaultdict(list)
    for key, spec in cfg.models.items():
        haystack = " ".join(
            str(part)
            for part in (
                key,
                spec.bundle,
                spec.source.value,
                spec.repo_id,
                spec.filename,
                spec.model_dir,
                spec.target_dir,
                spec.url,
            )
            if part
        ).lower()
        if not _fuzzy_match(needle, haystack):
            continue
        bundles[spec.bundle].append((key, spec))

    total = sum(len(items) for items in bundles.values())
    orphan_count = len(_orphan_prepared_model_files(cfg, include_sizes=False))
    orphan_notice = (
        f" [bold yellow]![/] [dim]{orphan_count} orphan"
        f"{'' if orphan_count == 1 else 's'} detected in cloud[/]"
        if orphan_count
        else ""
    )
    console.print(
        f"\n[bold white]Models[/] [dim]({total} shown / {len(cfg.models)} total)[/]"
        f"{orphan_notice}"
    )
    console.print("[dim][F] Filter  [R] Refresh  [D] Delete  [B] Back[/]")

    for bundle_name in sorted(bundles, key=lambda x: (x is None, x or "")):
        items = bundles[bundle_name]
        if bundle_name:
            console.print(f"\n[bold green]Bundle: {bundle_name}[/] [dim]({len(items)} models)[/]")
        else:
            console.print("\n[dim]Standalone:[/]")

        cards = []
        for key, spec in items:
            if spec.source == ModelSource.HUGGINGFACE:
                src_label = "HU"
                target = f"{spec.model_dir}/{spec.save_as or Path(spec.filename).name}"
                detail = f"{spec.repo_id} -> {target}"
            elif spec.source == ModelSource.HUGGINGFACE_SNAPSHOT:
                src_label = "SN"
                target = str(spec.target_dir)
                detail = f"{spec.repo_id} -> {target}"
            elif spec.source == ModelSource.EXTERNAL:
                src_label = "EX"
                target = f"{spec.model_dir}/{spec.filename}"
                detail = f"{_compact_url(spec.url or '')} -> {target}"
            elif spec.source == ModelSource.LOCAL:
                src_label = "LO"
                target = f"{spec.model_dir}/{spec.save_as or Path(spec.filename).name}"
                detail = f"{CACHE_VOLUME}/{spec.filename} -> {target}"
            else:
                src_label = "??"
                detail = ""
            console.print(f"  [bold white]{key:<25}[/] {source_badge(src_label)} [dim]{detail}[/]")
            cards.append(model_card(key, src_label, detail))
        print_model_cards(cards)
    print()


def _model_target_suffix(spec: ModelSpec) -> str | None:
    if spec.source == ModelSource.HUGGINGFACE:
        if not spec.model_dir or not spec.filename:
            return None
        return f"/models/{spec.model_dir}/{spec.save_as or Path(spec.filename).name}"
    if spec.source == ModelSource.EXTERNAL:
        if not spec.model_dir or not spec.filename:
            return None
        return f"/models/{spec.model_dir}/{spec.filename}"
    if spec.source == ModelSource.LOCAL:
        if not spec.model_dir or not spec.filename:
            return None
        return f"/models/{spec.model_dir}/{spec.save_as or Path(spec.filename).name}"
    if spec.source == ModelSource.HUGGINGFACE_SNAPSHOT:
        if not spec.target_dir:
            return None
        target = PurePosixPath(str(spec.target_dir).replace("\\", "/"))
        if target.is_absolute():
            return target.as_posix()
        return f"/models/{target.as_posix()}"
    return None


def _configured_model_target_suffixes(cfg: Config) -> set[str]:
    return {
        suffix
        for spec in cfg.models.values()
        if (suffix := _model_target_suffix(spec)) is not None
    }


def _target_matches_config(target_path: str, suffixes: set[str]) -> bool:
    return any(target_path.endswith(suffix) for suffix in suffixes)


def _orphan_prepared_model_files(cfg: Config, *, include_sizes: bool = True) -> list:
    suffixes = _configured_model_target_suffixes(cfg)
    try:
        prepared = list_prepared_model_files(include_sizes=include_sizes)
    except Exception as exc:
        console.print(f"[yellow]Could not read prepared model manifest:[/] {exc}")
        return []
    return [
        item
        for item in prepared
        if item.target_path and not _target_matches_config(item.target_path, suffixes)
    ]


def _remote_cache_paths_for_models(cfg: Config, keys: list[str]) -> dict[str, list[str]]:
    suffixes = {
        key: suffix
        for key in keys
        if (suffix := _model_target_suffix(cfg.models[key])) is not None
    }
    result = {key: [] for key in keys}
    if not suffixes:
        return result
    try:
        prepared = list_prepared_model_files(include_sizes=False)
    except Exception as exc:
        console.print(f"[yellow]Could not read prepared model manifest:[/] {exc}")
        return result
    for item in prepared:
        for key, suffix in suffixes.items():
            if item.target_path.endswith(suffix):
                result[key].append(item.cache_path)
    return result


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

    if not ask_confirm(f"Remove {len(to_remove)} model(s)?", default=False):
        return

    remote_paths_by_key = _remote_cache_paths_for_models(cfg, list(to_remove))
    remote_paths = sorted({path for paths in remote_paths_by_key.values() for path in paths})
    delete_remote = False
    if remote_paths:
        print_result_panel(
            "[bold yellow]Remote Files Matched[/bold yellow]",
            [
                ("Config records", len(to_remove)),
                ("Remote files", len(remote_paths)),
                ("Volume", CACHE_VOLUME),
            ],
            border_style="yellow",
        )
        delete_remote = ask_confirm(
            f"Also delete {len(remote_paths)} remote model file(s) from {CACHE_VOLUME}?",
            default=True,
        )

    if delete_remote and remote_paths:
        removed, failed = remove_volume_model_files(remote_paths)
        print_result_panel(
            "[bold green]Remote Delete[/bold green]",
            [
                ("Removed", len(removed)),
                ("Failed", len(failed) or None),
            ],
        )
        for cache_path, error in failed[:5]:
            console.print(f"[red]Failed:[/] {cache_path} [dim]{error}[/]")

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
        console.print("[dim]  No plugins configured.[/]")
        return

    filter_text = questionary.text(
        "Filter plugins (blank = all):",
        default="",
        style=STYLE,
    ).ask()
    if filter_text is None:
        return
    needle = filter_text.strip().lower()

    cards = []
    console.print(f"\n[bold white]Installed Plugins[/] [dim]({len(cfg.plugins)} total)[/]")
    for key, spec in cfg.plugins.items():
        source = spec.repo or spec.node_id or "local"
        name = spec.name or key
        haystack = f"{key} {name} {source}".lower()
        if not _fuzzy_match(needle, haystack):
            continue
        branch = "main" if spec.repo else "registry"
        console.print(f"  [bold white]{key:<25}[/] [dim]{source}[/]")
        cards.append(plugin_card(name, source, branch))
    print_plugin_cards(cards)
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

    if not ask_confirm(f"Remove {len(to_remove)} plugin(s)?", default=False):
        return

    for key in to_remove:
        del cfg.plugins[key]
        print(f"  {R}-{RST} {W}{key}{RST}")


# ── Deploy ──


def _modal_env(gpu_choice: str | None = None) -> dict[str, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if gpu_choice:
        env[WEB_UI_GPU_ENV] = gpu_choice
    return env


def _choose_web_gpu(default: str = DEFAULT_WEB_UI_GPU, *, preference_key: str = "last_web_gpu") -> str:
    preferences = load_preferences()
    preferred = str(preferences.get(preference_key) or default)
    choice = ask_select(
        "Web UI GPU:",
        choices=gpu_choice_items(preferred),
        default=preferred,
        instruction=(
            "Used by server/ui.py for modal serve/deploy. "
            "Headless inference still asks for GPU per run."
        ),
    )
    save_preferences({preference_key: choice})
    return choice


def _workflow_dependency_status() -> str:
    return "present" if (Path(__file__).parent / "workflow_api.json").exists() else "absent"


def _confirm_remote_action(
    title: str,
    command: list[str],
    rows: list[tuple[str, object]],
    *,
    default_confirm: bool = False,
) -> bool:
    review_rows = [
        *rows,
        ("Cache Volume", CACHE_VOLUME),
        ("Output Volume", "comfy-output"),
        ("workflow_api.json", _workflow_dependency_status()),
    ]
    print_result_panel(title, review_rows, border_style="yellow")
    print_command_panel("[bold yellow]Command Review[/bold yellow]", command, rows, border_style="yellow")
    return ask_confirm("Start this Modal operation?", default=default_confirm)


def _run_prepare_for_preflight(reason: str) -> None:
    cmd = ["modal", "run", "server/app.py::prepare"]
    if not _confirm_remote_action(
        "[bold yellow]Prepare Models Pre-flight[/bold yellow]",
        cmd,
        [("Reason", reason), ("Volume", CACHE_VOLUME), ("Mode", "normal")],
        default_confirm=True,
    ):
        print_status("Prepare skipped by user.", style="yellow")
        return
    subprocess.run(cmd, env=_modal_env(), check=True)


def _normal_preflight(cfg: Config) -> None:
    print_step("Run ComfyUI > Normal Mode > Pre-flight Check")
    suffixes = _configured_model_target_suffixes(cfg)
    if not suffixes:
        print_result_panel(
            "[bold green]Pre-flight Check[/bold green]",
            [
                ("config.toml", "loaded"),
                ("Expected target paths", 0),
                ("Remote manifest", "not needed"),
                ("Action", "skip prepare"),
            ],
            border_style="green",
        )
        return

    prepared_target_paths: list[str] = []
    manifest_status = "checked"
    manifest_error: str | None = None
    try:
        prepared = list_prepared_model_files(include_sizes=False)
        prepared_target_paths = [item.target_path for item in prepared if item.target_path]
    except Exception as exc:
        manifest_status = "warning"
        manifest_error = str(exc)

    present_suffixes = {
        suffix
        for suffix in suffixes
        if any(target_path.endswith(suffix) for target_path in prepared_target_paths)
    }
    missing_suffixes = sorted(suffixes - present_suffixes)
    needs_prepare = manifest_error is not None or bool(missing_suffixes)
    reason = "manifest read failed" if manifest_error else "missing configured model links"

    rows = [
        ("config.toml", "loaded"),
        ("Expected target paths", len(suffixes)),
        ("Remote manifest", manifest_status),
        ("Manifest warning", manifest_error),
        ("Missing target paths", len(missing_suffixes) if missing_suffixes else "none"),
        ("Action", "run prepare" if needs_prepare else "skip prepare"),
    ]
    print_result_panel(
        "[bold yellow]Pre-flight Check[/bold yellow]" if needs_prepare else "[bold green]Pre-flight Check[/bold green]",
        rows,
        border_style="yellow" if needs_prepare else "green",
    )
    for suffix in missing_suffixes[:10]:
        console.print(f"  [yellow]missing[/] {suffix}")
    if len(missing_suffixes) > 10:
        console.print(f"  [dim]... {len(missing_suffixes) - 10} more[/]")

    if needs_prepare:
        _run_prepare_for_preflight(reason)


def _launch_normal_web_ui(cfg: Config) -> None:
    _normal_preflight(cfg)
    gpu_choice = _choose_web_gpu(DEFAULT_WEB_UI_GPU, preference_key="last_web_gpu")
    cmd = [sys.executable, "serve.py", "--gpu", gpu_choice]
    if not _confirm_remote_action(
        "[bold yellow]Web UI Dev Serve Review[/bold yellow]",
        cmd,
        [
            ("Mode", "normal"),
            ("GPU", gpu_choice),
            ("Config", CONFIG_PATH),
            ("Encoding", "UTF-8 environment enabled"),
        ],
        default_confirm=False,
    ):
        print_status("Web UI launch cancelled.", style="yellow")
        return
    subprocess.run(
        cmd,
        env=_modal_env(),
        check=True,
    )


def _launch_empty_web_ui() -> None:
    gpu_choice = _choose_web_gpu(EMPTY_WEB_UI_GPU, preference_key="last_empty_gpu")
    cmd = [sys.executable, "serve.py", "--empty", "--gpu", gpu_choice]
    if not _confirm_remote_action(
        "[bold yellow]Empty Web UI Dev Serve Review[/bold yellow]",
        cmd,
        [
            ("Mode", "empty"),
            ("GPU", gpu_choice),
            ("Config profile", "empty"),
            ("Modal Environment", "empty"),
            ("Prepare", "skipped"),
        ],
        default_confirm=False,
    ):
        print_status("Empty Web UI launch cancelled.", style="yellow")
        return
    subprocess.run(
        cmd,
        env=_modal_env(),
        check=True,
    )


def _run_comfyui_menu(get_config: Callable[[], Config]) -> None:
    action = ask_select(
        "Run ComfyUI:",
        choices=[
            questionary.Choice(
                "Normal Mode (Full)",
                value="normal",
                description="Pre-flight config/manifest check, prepare missing links, then launch Web UI.",
            ),
            questionary.Choice(
                "Minimal Mode (Empty)",
                value="empty",
                description="Launch empty workflow-editing Web UI with T4 default and no prepare.",
            ),
            questionary.Choice(
                "Back",
                value="back",
                description="Return to the main menu.",
            ),
        ],
    )

    if action == "normal":
        save_preferences({"last_run_mode": "normal"})
        _launch_normal_web_ui(get_config())
    elif action == "empty":
        save_preferences({"last_run_mode": "empty"})
        _launch_empty_web_ui()


def _cloud_deployment_menu() -> None:
    action = ask_select(
        "Cloud Deployment:",
        choices=[
            questionary.Choice(
                "Deploy Web UI",
                value="deploy",
                description="Run python -m scripts.deploy_ui with a selected Web UI GPU.",
            ),
            questionary.Choice(
                "Back",
                value="back",
                description="Return to the main menu.",
            ),
        ],
    )

    if action == "deploy":
        gpu_choice = _choose_web_gpu(DEFAULT_WEB_UI_GPU, preference_key="last_deploy_gpu")
        cmd = [sys.executable, "-m", "scripts.deploy_ui", "--gpu", gpu_choice]
        if not _confirm_remote_action(
            "[bold yellow]Web UI Deploy Review[/bold yellow]",
            cmd,
            [
                ("GPU", gpu_choice),
                ("Mode", "deploy"),
                ("Config", CONFIG_PATH),
            ],
            default_confirm=False,
        ):
            print_status("Deploy cancelled.", style="yellow")
            return
        subprocess.run(
            cmd,
            env=_modal_env(),
            check=True,
        )


# ── Main Menu ──


def _models_menu(cfg: Config) -> None:
    while True:
        action = questionary.select(
            "Model action:",
            choices=[
                "Add model (HuggingFace)",
                "Add model (CivitAI / External URL)",
                "Add model (Local upload)",
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
        elif "Local upload" in action:
            _add_local_model(cfg)
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
    session_start = dt.datetime.now().astimezone()
    print_banner(
        "ComfyUI Manager",
        "Configure models/plugins, run ComfyUI, deploy Web UI, and manage Modal volumes.",
    )

    cfg: Config | None = None

    def get_config() -> Config:
        nonlocal cfg
        if cfg is None:
            try:
                cfg = _ensure_config()
            except ConfigError as e:
                print(f"  {R}{B}Config error:{RST} {e}")
                sys.exit(1)
            n_models = len(cfg.models)
            n_plugins = len(cfg.plugins)
            print_status(f"{n_models} models - {n_plugins} plugins", style="green")
        return cfg

    _prompt_modal_setup_if_needed(get_config())

    while True:
        try:
            choice = ask_select(
                "What do you want to do?",
                choices=[
                    questionary.Choice(
                        "Run ComfyUI",
                        value="run",
                        description="Launch normal or empty Web UI modes.",
                    ),
                    questionary.Choice(
                        "Manage models",
                        value="models",
                        description="Add/list/remove model config and upload local model files.",
                    ),
                    questionary.Choice(
                        "Manage plugins",
                        value="plugins",
                        description="Add/list/remove ComfyUI custom node config.",
                    ),
                    questionary.Choice(
                        "Cloud Deployment",
                        value="cloud",
                        description="Deploy persistent Web UI endpoint.",
                    ),
                    questionary.Choice(
                        "Configure Modal Secrets",
                        value="secrets",
                        description="Create or update Hugging Face and CivitAI Modal secrets.",
                    ),
                    questionary.Choice(
                        "Refresh Modal Status",
                        value="status",
                        description="Re-check Modal Account and Modal Secrets in a fresh subprocess.",
                    ),
                    questionary.Choice(
                        "Manage Modal Volumes",
                        value="volumes",
                        description="List comfy-cache/comfy-output or clean old output sessions.",
                    ),
                    questionary.Choice("Exit", value="exit"),
                ],
            )
        except KeyboardInterrupt:
            break

        if choice == "exit":
            break
        try:
            if choice == "run":
                print_step("Main > Run ComfyUI")
                _run_comfyui_menu(get_config)
            elif choice == "models":
                print_step("Main > Models")
                cfg = get_config()
                _models_menu(cfg)
            elif choice == "plugins":
                print_step("Main > Plugins")
                cfg = get_config()
                _plugins_menu(cfg)
            elif choice == "cloud":
                print_step("Main > Cloud Deployment")
                _cloud_deployment_menu()
            elif choice == "secrets":
                print_step("Main > Configure Modal Secrets")
                _configure_modal_secrets_menu(get_config())
            elif choice == "status":
                print_step("Main > Modal Status")
                _print_modal_status_snapshot(_fresh_modal_status(get_config()))
            elif choice == "volumes":
                print_step("Main > Volumes")
                cfg = get_config()
                orphan_cache_paths = {
                    item.cache_path
                    for item in _orphan_prepared_model_files(cfg, include_sizes=False)
                    if item.cache_path
                }
                volume_management_menu(orphan_cache_paths=orphan_cache_paths)
        except KeyboardInterrupt:
            console.print("[dim]Returned to main menu.[/dim]")

    print_exit_summary(session_start, dt.datetime.now().astimezone())
    console.print("\n[bold green]Done.[/bold green]\n")


if __name__ == "__main__":
    main()
