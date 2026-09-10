from __future__ import annotations

import os
import sys


def modal_command(*args: str) -> list[str]:
    """Run Modal through the active Python environment."""
    return [sys.executable, "-X", "utf8", "-m", "modal", *args]


def utf8_env(**overrides: str) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        **overrides,
    }
