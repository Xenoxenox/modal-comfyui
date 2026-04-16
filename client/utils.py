"""Client-side utilities: logging, UTF-8 fix, workflow loading, result download."""

from __future__ import annotations

import base64
import io
import json
import logging
import sys
from datetime import datetime
from pathlib import Path


def ensure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            encoding = getattr(stream, "encoding", None)
            if encoding and encoding.lower().startswith("utf-8"):
                continue
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
            elif hasattr(stream, "buffer"):
                setattr(
                    sys,
                    name,
                    io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"),
                )
        except Exception:
            pass


def setup_logger() -> Path:
    """Initialize logging to both console and timestamped file."""
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"comfyui_run_{timestamp}.log"

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    logging.info("日志输出：%s", log_path)
    return log_path


def load_workflow(path: Path) -> dict:
    """Read and validate a ComfyUI API-format workflow JSON file."""
    if not path.exists():
        raise FileNotFoundError(f"Workflow 文件不存在：{path}")
    if path.suffix.lower() != ".json":
        raise ValueError(f"Workflow 文件必须为 JSON 格式：{path}")

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"Workflow JSON 顶层必须是 dict，实际类型：{type(data).__name__}")

    return data


def download_outputs(result: dict, output_dir: Path) -> list[Path]:
    """Decode base64 outputs from remote result and write to local files.

    Returns a list of written file paths.
    """
    created_files = result.get("created_files", {})
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename, content_b64 in created_files.items():
        content = base64.b64decode(content_b64)
        local_path = output_dir / filename
        local_path.write_bytes(content)
        logging.info("写入文件: %s (%d bytes)", local_path, len(content))
        written.append(local_path)

    return written
