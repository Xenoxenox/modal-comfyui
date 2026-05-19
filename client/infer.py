"""Local client entry-point for headless ComfyUI inference.

Usage:
    python -m client.infer
    python -m client.infer --workflow workflows/example.json --gpu T4 --yes

Interactive prompts (via questionary) let you choose the GPU, workflow
file, and timeout when no command-line options are provided. The workflow
is then dispatched to a Modal container running ComfyUI headlessly; results
are downloaded to a local directory.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections.abc import Sequence
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

from scripts.modal_run_info import (
    AppLogStreamer,
    modal_log_path,
    modal_app_logs_command,
    modal_app_stop_command,
    shell_command_text,
)
from scripts.preferences import load_preferences, save_preferences


@dataclass
class UserSelection:
    gpu_choice: str
    workflow_path: Path
    timeout_minutes: int
    seed: int | None
    run_mode: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a ComfyUI API workflow headlessly on Modal.",
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        help="Path to a ComfyUI API-format workflow JSON file.",
    )
    parser.add_argument(
        "--gpu",
        choices=DEFAULT_GPU_CHOICES,
        default="L4",
        help="Modal GPU type to request. Default: L4.",
    )
    parser.add_argument(
        "--timeout",
        "--timeout-minutes",
        dest="timeout_minutes",
        type=int,
        default=10,
        help="Maximum remote runtime in minutes. Default: 10.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Override KSampler and KSamplerAdvanced seed values.",
    )
    parser.add_argument(
        "--mode",
        choices=("attached", "detached"),
        default="attached",
        help="Attached waits for results; detached returns after submission. Default: attached.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Submit without the interactive confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the submission review without contacting Modal.",
    )
    return parser


def selection_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> UserSelection:
    if args.workflow is None:
        parser.error("--workflow is required when using command-line options")

    workflow_path = args.workflow.expanduser().resolve()
    if not workflow_path.exists():
        raise FileNotFoundError(f"路径不存在：{workflow_path}")

    if args.timeout_minutes <= 0:
        raise ValueError("--timeout must be greater than 0")

    return UserSelection(
        gpu_choice=args.gpu,
        workflow_path=workflow_path,
        timeout_minutes=args.timeout_minutes,
        seed=args.seed,
        run_mode=args.mode,
    )


def ask_selection() -> UserSelection:
    """Interactive prompts to configure the inference run."""
    try:
        import questionary
    except ImportError:
        print("需要 questionary，请运行 `uv sync` 或 `pip install questionary`。")
        raise

    preferences = load_preferences()
    preferred_gpu = str(preferences.get("last_infer_gpu") or "L4")
    gpu_choice = questionary.select(
        "选择 GPU：",
        choices=DEFAULT_GPU_CHOICES,
        default=preferred_gpu,
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
        default=str(preferences.get("last_infer_timeout") or "10"),
    ).ask()
    timeout_minutes = int(timeout_str or "10")

    seed_str = questionary.text(
        "随机种子（留空使用 workflow 默认）：",
        default="",
    ).ask()
    seed = int(seed_str) if seed_str else None

    preferred_run_mode = str(preferences.get("last_run_mode") or "attached")
    run_mode = questionary.select(
        "运行模式：",
        choices=[
            questionary.Choice("Attached (等待完成并下载输出)", value="attached"),
            questionary.Choice("Detached (提交后返回 app/log 信息)", value="detached"),
        ],
        default=preferred_run_mode,
    ).ask()
    if not run_mode:
        raise KeyboardInterrupt

    save_preferences(
        {
            "last_infer_gpu": gpu_choice,
            "last_infer_timeout": timeout_minutes,
            "last_run_mode": run_mode,
        }
    )

    return UserSelection(
        gpu_choice=gpu_choice,
        workflow_path=workflow_path,
        timeout_minutes=timeout_minutes,
        seed=seed,
        run_mode=run_mode,
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


def print_inference_review(selection: UserSelection, session_id: str, output_dir: Path) -> None:
    from scripts.tui import print_result_panel

    estimated_gpu_hours = selection.timeout_minutes / 60
    print_result_panel(
        "[bold yellow]Headless Inference Review[/bold yellow]",
        [
            ("Workflow", selection.workflow_path),
            ("Session", session_id),
            ("Output", output_dir),
            ("GPU", selection.gpu_choice),
            ("Timeout", f"{selection.timeout_minutes} minutes"),
            ("Estimated GPU ceiling", f"{estimated_gpu_hours:.2f} GPU-hours before Modal pricing/credits"),
            ("Run mode", selection.run_mode),
            ("Seed override", selection.seed),
        ],
        border_style="yellow",
    )


def print_submission_info(
    *,
    app_id: str,
    app_dashboard_url: str,
    function_call_id: str,
    function_call_dashboard_url: str,
    logs_command: str,
    stop_command: str,
    log_path: Path,
) -> None:
    from scripts.tui import print_result_panel

    print_result_panel(
        "[bold blue]Modal Inference Submitted[/bold blue]",
        [
            ("App ID", app_id),
            ("Dashboard", app_dashboard_url),
            ("Function Call ID", function_call_id),
            ("Function Call", function_call_dashboard_url),
            ("Logs Command", logs_command),
            ("Stop Command", f"{stop_command} (only if the app lingers after completion)"),
            ("Local Log", log_path),
        ],
        border_style="blue",
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    has_cli_args = bool(argv)

    log_path = setup_logger()
    exit_code = 0

    try:
        if has_cli_args:
            selection = selection_from_args(args, parser)
        else:
            selection = ask_selection()

        logging.info("加载 workflow: %s", selection.workflow_path)
        workflow_json = load_workflow(selection.workflow_path)

        if selection.seed is not None:
            workflow_json = apply_seed(workflow_json, selection.seed)
            logging.info("已覆盖 seed: %d", selection.seed)

        session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        output_dir = Path("output") / session_id
        print_inference_review(selection, session_id, output_dir)

        if args.dry_run:
            logging.info("Dry run complete; no Modal job submitted.")
            return 0

        if not args.yes:
            from scripts.tui import ask_confirm

            if not ask_confirm("Submit this Modal inference job?", default=False):
                logging.warning("用户取消提交。")
                return 1
        else:
            logging.info("确认已由 --yes 跳过。")

        try:
            from server.app import app as infer_app, run_headless_inference
        except ModuleNotFoundError as exc:
            if exc.name == "modal":
                print("需要 modal，请运行 `uv sync` 或 `pip install modal`。")
            raise

        logging.info("Session: %s", session_id)
        logging.info("GPU: %s", selection.gpu_choice)
        logging.info("超时: %d 分钟", selection.timeout_minutes)
        logging.info("运行模式: %s", selection.run_mode)

        logging.info("=== 开始远程执行 ===")
        logging.info("正在提交到 Modal。Modal 可能正在分配 GPU、检查 image 或挂载容器。")

        remote_generate = run_headless_inference.with_options(
            gpu=selection.gpu_choice,
            timeout=selection.timeout_minutes * 60,
        )

        with infer_app.run(name="comfyui-infer", detach=(selection.run_mode == "detached")):
            function_call = remote_generate.spawn(workflow_json, session_id)
            app_id = infer_app.app_id
            app_dashboard_url = infer_app.get_dashboard_url()
            function_call_id = function_call.object_id
            function_call_dashboard_url = function_call.get_dashboard_url()
            logs_command = shell_command_text(modal_app_logs_command(app_id, function_call_id))
            stop_command = shell_command_text(modal_app_stop_command(app_id))
            print_submission_info(
                app_id=app_id,
                app_dashboard_url=app_dashboard_url,
                function_call_id=function_call_id,
                function_call_dashboard_url=function_call_dashboard_url,
                logs_command=logs_command,
                stop_command=stop_command,
                log_path=log_path,
            )
            if selection.run_mode == "detached":
                logging.info("Detached 模式已提交。使用上面的 logs command 查看进度。")
                return 0
            app_log_path = modal_log_path("modal_app_comfyui_infer")
            log_streamer = AppLogStreamer(app_id, function_call_id, app_log_path)
            logging.info("Streaming Modal logs to %s", app_log_path)
            log_streamer.start()
            try:
                result = function_call.get()
            finally:
                log_streamer.stop()

        logging.info("=== 远程执行完成 ===")

        # Download results
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

    if not has_cli_args:
        with contextlib.suppress(EOFError):
            input("按回车键退出...")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
