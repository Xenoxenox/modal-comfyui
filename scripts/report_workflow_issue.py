"""
收集本次 ComfyUI 前端异常的关键信息，并通过 Claude hook 自动回传。

用法：
    python scripts/report_workflow_issue.py
    python scripts/report_workflow_issue.py --title "双击 workflow 无法加载"
    python scripts/report_workflow_issue.py --log-path logs/modal_serve_20260417_093000.log
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ERROR_FILE = Path(".claude/_last_error.txt")
LOG_DIR = Path("logs")
DEFAULT_TITLE = "ComfyUI Workflows 双击读取失败"
DEFAULT_TAIL_LINES = 120


for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="收集浏览器报错和最新 modal serve 日志，并通过 Claude hook 自动回传。"
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE,
        help="问题标题，会出现在自动上报内容顶部。",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        help="指定要附带的日志文件；不传则自动选择 logs/ 下最新的 modal_serve_*.log。",
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=DEFAULT_TAIL_LINES,
        help="附带日志末尾的行数，默认 120。",
    )
    return parser.parse_args()


def prompt_text(prompt: str, default: str = "") -> str:
    raw = input(prompt).strip()
    return raw or default


def capture_clipboard(label: str) -> str:
    print(
        f"\n请先把{label}复制到剪贴板，然后按 Enter 读取。"
        "输入 skip 可跳过这一项。",
        flush=True,
    )
    choice = input("> ").strip().lower()
    if choice == "skip":
        return ""

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = result.stdout.strip()
    if not text:
        print(f"[WARN] 剪贴板为空，跳过“{label}”。", flush=True)
        return ""
    return text


def find_latest_log(explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return explicit_path if explicit_path.exists() else None

    if not LOG_DIR.exists():
        return None

    candidates = sorted(
        LOG_DIR.glob("modal_serve_*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    legacy_path = Path("modal_serve.log")
    if legacy_path.exists():
        return legacy_path

    return None


def tail_text(path: Path, max_lines: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > max_lines:
        lines = ["... (前面日志已省略，仅保留末尾内容) ...", ""] + lines[-max_lines:]
    return "\n".join(lines)


def build_report(
    *,
    title: str,
    summary: str,
    console_text: str,
    network_text: str,
    log_path: Path | None,
    log_text: str,
) -> str:
    sections: list[str] = [f"问题标题: {title}"]

    if summary:
        sections.append(f"复现说明:\n{summary}")

    if console_text:
        sections.append(f"浏览器 Console:\n{console_text}")

    if network_text:
        sections.append(f"Network / cURL:\n{network_text}")

    if log_path and log_text:
        sections.append(f"服务端日志 ({log_path}):\n{log_text}")
    elif log_path:
        sections.append(f"服务端日志 ({log_path}):\n[日志为空]")
    else:
        sections.append("服务端日志:\n[未找到 modal_serve 日志文件]")

    sections.append(
        "请优先分析 Workflows 列表可见但双击无法读取的问题，"
        "重点关注 userdata/workflows 读取链路、请求状态码和文件路径映射。"
    )

    return "\n\n".join(sections)


def main() -> int:
    args = parse_args()

    print("准备收集这次 ComfyUI 前端异常的信息。", flush=True)
    summary = prompt_text(
        "一句话描述这次操作和现象（可直接回车跳过）: "
    )
    console_text = capture_clipboard("浏览器 Console 红字报错")
    network_text = capture_clipboard("失败请求的 cURL 或响应体")

    log_path = find_latest_log(args.log_path)
    log_text = ""
    if log_path is not None:
        log_text = tail_text(log_path, max_lines=args.tail_lines)
        print(f"[INFO] 已附带日志: {log_path}", flush=True)
    else:
        print("[WARN] 未找到可附带的 modal serve 日志文件。", flush=True)

    report = build_report(
        title=args.title,
        summary=summary,
        console_text=console_text,
        network_text=network_text,
        log_path=log_path,
        log_text=log_text,
    )

    ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
    ERROR_FILE.write_text(report, encoding="utf-8")

    print(f"\n问题上报已写入 {ERROR_FILE}。")
    print("Claude Stop hook 会在本轮结束时自动读取并分析。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
