"""Local client entry-point for headless ComfyUI inference.

Usage:
    python -m client.infer

Interactive prompts (via questionary) let you choose the GPU, workflow
file, and timeout. The workflow is then dispatched to a Modal container
running ComfyUI headlessly; results are downloaded to a local directory.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from client.utils import (
    download_outputs,
    ensure_utf8_stdio,
    load_workflow,
    setup_logger,
)

ensure_utf8_stdio()

try:
    import questionary
except ImportError:
    print("需要 questionary，请运行 `uv sync` 或 `pip install questionary`。")
    raise

try:
    import modal
except ImportError:
    print("需要 modal，请运行 `uv sync` 或 `pip install modal`。")
    raise

DEFAULT_GPU_CHOICES = [
    "T4",
    "L4",
    "L40S",
    "A10G",
    "A100-40GB",
    "A100-80GB",
    "H100",
    "H200",
    "B200",
]


@dataclass
class UserSelection:
    gpu_choice: str
    workflow_path: Path
    timeout_minutes: int
    seed: int | None


def ask_selection() -> UserSelection:
    """Interactive prompts to configure the inference run."""
    gpu_choice = questionary.select(
        "选择 GPU：",
        choices=DEFAULT_GPU_CHOICES,
    ).ask()
    if not gpu_choice:
        raise KeyboardInterrupt

    workflow_str = questionary.path(
        "选择 workflow JSON 文件：",
        default="workflows/",
    ).ask()
    if not workflow_str:
        raise KeyboardInterrupt
    workflow_path = Path(workflow_str).expanduser().resolve()
    if not workflow_path.exists():
        raise FileNotFoundError(f"路径不存在：{workflow_path}")

    timeout_str = questionary.text(
        "超时时间（分钟）：",
        default="10",
    ).ask()
    timeout_minutes = int(timeout_str or "10")

    seed_str = questionary.text(
        "随机种子（留空使用 workflow 默认）：",
        default="",
    ).ask()
    seed = int(seed_str) if seed_str else None

    return UserSelection(
        gpu_choice=gpu_choice,
        workflow_path=workflow_path,
        timeout_minutes=timeout_minutes,
        seed=seed,
    )


def apply_seed(workflow_json: dict, seed: int) -> dict:
    """Override KSampler seed values in the workflow."""
    for _node_id, node in workflow_json.items():
        if isinstance(node, dict) and node.get("class_type") in (
            "KSampler",
            "KSamplerAdvanced",
        ):
            inputs = node.get("inputs", {})
            if "seed" in inputs:
                inputs["seed"] = seed
    return workflow_json


def main() -> int:
    log_path = setup_logger()
    exit_code = 0

    try:
        selection = ask_selection()

        logging.info("加载 workflow: %s", selection.workflow_path)
        workflow_json = load_workflow(selection.workflow_path)

        if selection.seed is not None:
            workflow_json = apply_seed(workflow_json, selection.seed)
            logging.info("已覆盖 seed: %d", selection.seed)

        session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        logging.info("Session: %s", session_id)
        logging.info("GPU: %s", selection.gpu_choice)
        logging.info("超时: %d 分钟", selection.timeout_minutes)

        # ── Build a per-invocation Modal App ──
        # This pattern (define @app.function inside a function, then
        # call with app.run()) allows the GPU type to be chosen at
        # runtime. Proven in modal_infer.py.bak L428-448.

        from server.app import image, cache_vol, output_vol, CACHE_MOUNT, OUTPUT_MOUNT

        infer_app = modal.App("comfyui-infer")

        @infer_app.function(
            image=image,
            gpu=selection.gpu_choice,
            timeout=selection.timeout_minutes * 60,
            volumes={
                CACHE_MOUNT: cache_vol,
                OUTPUT_MOUNT: output_vol,
            },
            serialized=True,
        )
        def remote_generate(wf_json: dict, sess_id: str) -> dict:
            from server.generate import run_generate
            return run_generate(wf_json, sess_id)

        logging.info("=== 开始远程执行 ===")
        logging.info("正在启动 GPU 容器...")

        with infer_app.run():
            result = remote_generate.remote(workflow_json, session_id)

        logging.info("=== 远程执行完成 ===")

        # Download results
        output_dir = Path("output") / session_id
        written = download_outputs(result, output_dir)

        logging.info("=== 运行完成 ===")
        logging.info("Session: %s", session_id)
        logging.info("输出路径: %s", output_dir)
        if written:
            logging.info("生成文件：")
            for f in written:
                logging.info("  %s", f.name)
        logging.info("✅ 请在上方输出路径查看结果。")

    except KeyboardInterrupt:
        logging.warning("用户中断。")
        exit_code = 1
    except Exception as exc:
        logging.exception("运行失败：%s", exc)
        logging.error("日志见：%s", log_path)
        exit_code = 1

    with contextlib.suppress(EOFError):
        input("按回车键退出...")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
