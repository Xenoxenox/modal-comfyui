from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PREFERENCES_DIR = Path(".modal-comfyui")
PREFERENCES_PATH = PREFERENCES_DIR / "preferences.json"
ALLOWED_KEYS = {
    "last_web_gpu",
    "last_empty_gpu",
    "last_run_mode",
    "last_deploy_gpu",
    "last_infer_gpu",
    "last_infer_timeout",
}


def load_preferences() -> dict[str, Any]:
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in ALLOWED_KEYS if key in data}


def save_preferences(updates: dict[str, Any]) -> None:
    clean_updates = {key: value for key, value in updates.items() if key in ALLOWED_KEYS}
    if not clean_updates:
        return
    data = load_preferences()
    data.update(clean_updates)
    PREFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
