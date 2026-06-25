#!/usr/bin/env python3
"""
用法：python serve.py [--gpu L4]
自动停止旧的 ephemeral app，启动 modal serve，并自动下载 Web UI 生成图片。

环境变量：
  SERVE_IDLE_TIMEOUT   URL 就绪前无日志增长超时秒数（默认 120）
"""
import argparse
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import suppress
from urllib import request as urlrequest

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from scripts.modal_run_info import (
    modal_app_logs_command,
    modal_app_stop_command,
    modal_log_path,
    parse_modal_run_info,
    shell_command_text,
)
from scripts.modal_status import sanitize_modal_error
from scripts.web_ui_mode import (
    CONFIG_PROFILE_ENV,
    DEFAULT_WEB_UI_GPU,
    WEB_UI_GPU_ENV,
    add_web_ui_mode_args,
    empty_mode_env,
    ensure_modal_environment,
    modal_env_args,
    mode_from_args,
)

console = Console()
IDLE_TIMEOUT = int(os.getenv("SERVE_IDLE_TIMEOUT", "120"))

PHASE_SIGNALS = [
    ("modal.run",                    "[4/4] URL ready        "),
    ("Application startup complete", "[3/4] App ready        "),
    ("ComfyUI is ready",             "[3/4] ComfyUI ready    "),
    ("Running app",                  "[2/4] Starting container"),
    ("Pulling",                      "[1/4] Pulling image    "),
    ("Building",                     "[1/4] Building image   "),
    ("Creating",                     "[1/4] Creating container"),
]


def _current_phase(text: str) -> str:
    best_label = "[0/4] Waiting          "
    best_idx = -1
    for signal, label in PHASE_SIGNALS:
        idx = text.rfind(signal)
        if idx > best_idx:
            best_idx = idx
            best_label = label
    return best_label


ERROR_SIGNALS = [
    "getaddrinfo failed",
    "Connection refused",
    "Failed to establish",
    "Traceback (most recent call last)",
    "Exception:",
]
REMOTE_LOG_ERRORS = {"Traceback (most recent call last)", "Exception:"}
RECOVERABLE_RUNTIME_ERRORS = {"Connection refused"}


def _error_signal(line: str, *, service_ready: bool) -> str | None:
    for err in ERROR_SIGNALS:
        if err in line:
            if err in REMOTE_LOG_ERRORS:
                return None
            if service_ready and err in RECOVERABLE_RUNTIME_ERRORS:
                return None
            return err
    return None


def _probe_url(url: str, retries: int = 5, delay: float = 3.0) -> bool:
    probe = f"{url}/system_stats"
    for i in range(retries):
        try:
            with urlrequest.urlopen(urlrequest.Request(probe), timeout=8):
                return True
        except Exception:
            if i < retries - 1:
                time.sleep(delay)
    return False


def _idle_timeout_expired(last_change: float, now: float, timeout: int, *, service_ready: bool) -> bool:
    return not service_ready and now - last_change > timeout


def _start_output_watcher(url: str) -> subprocess.Popen[str] | None:
    watch_log_path = modal_log_path("comfy_watch")
    cmd = [sys.executable, "-m", "client.watch", url]
    try:
        with watch_log_path.open("w", encoding="utf-8", errors="replace") as watch_log:
            proc = subprocess.Popen(
                cmd,
                env={
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8",
                },
                stdout=watch_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
    except Exception as exc:
        console.print(f"[bold yellow]Watcher start failed:[/] {exc}")
        return None
    console.print(
        f"[dim]Local output watcher started[/dim] "
        f"[dim]log={watch_log_path} output=output/[/dim]"
    )
    return proc


def stop_old_apps(modal_env: str | None = None):
    list_cmd = [sys.executable, "-m", "modal", "app", "list", *modal_env_args(modal_env)]
    result = subprocess.run(
        list_cmd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    for line in result.stdout.splitlines():
        if "ephemeral" in line:
            app_id = line.split("|")[1].strip()
            if app_id:
                print(f"Stopping old app: {app_id}")
                subprocess.run(
                    [sys.executable, "-m", "modal", "app", "stop", *modal_env_args(modal_env), app_id],
                    capture_output=True,
                )
    time.sleep(5)


def _print_run_info(info: dict[str, str], log_path: object, proc_pid: int | None = None) -> None:
    app_id = info.get("app_id")
    function_call_id = info.get("function_call_id")

    metadata = Table(show_header=False, box=None, padding=(0, 2))
    metadata.add_column("Key", style="dim", no_wrap=True)
    metadata.add_column("Value", overflow="fold")
    if info.get("web_url"):
        url = info["web_url"]
        metadata.add_row("ComfyUI URL", f"[bold green underline link={url}]{url}[/]")
    if app_id:
        metadata.add_row("App ID", f"[bold white]{app_id}[/]")
    if info.get("dashboard_url"):
        url = info["dashboard_url"]
        metadata.add_row("Dashboard", f"[bold blue underline link={url}]{url}[/]")
    if function_call_id:
        metadata.add_row("Function Call ID", f"[bold white]{function_call_id}[/]")
    if info.get("function_call_url"):
        url = info["function_call_url"]
        metadata.add_row("Function Call", f"[blue underline link={url}]{url}[/]")
    metadata.add_row("Local app log", f"[dim]{log_path}[/]")
    if proc_pid is not None:
        metadata.add_row("Local PID", f"[dim]{proc_pid} (Ctrl+C to stop attached serve)[/]")

    content = Table.grid(expand=True)
    content.add_row(metadata)
    if app_id:
        content.add_row(
            Syntax(
                shell_command_text(modal_app_logs_command(app_id, function_call_id)),
                "powershell",
                word_wrap=True,
                theme="ansi_dark",
            )
        )
        content.add_row(
            Syntax(
                shell_command_text(modal_app_stop_command(app_id))
                + "  # only if the app lingers after you are done",
                "powershell",
                word_wrap=True,
                theme="ansi_dark",
            )
        )
    console.print(
        Panel(
            content,
            title="[bold blue]Modal Run Details[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )


def _stop_process(proc: subprocess.Popen[str], *, interrupted: bool = False) -> None:
    if proc.poll() is not None:
        return
    if interrupted:
        console.print("\n[bold yellow]ComfyUI server session interrupted by user. Cleaning up...[/bold yellow]")
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=3)


def _start_output_reader(proc: subprocess.Popen[str]) -> queue.Queue[str | None]:
    lines: queue.Queue[str | None] = queue.Queue()

    def read_lines() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start modal serve for the ComfyUI Web UI."
    )
    add_web_ui_mode_args(parser)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically open the ComfyUI URL in the default browser.",
    )
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="Do not start the local output watcher that downloads generated images.",
    )
    return parser


def main():
    args = _build_parser().parse_args()
    profile, modal_env = mode_from_args(args)
    ensure_modal_environment(modal_env)
    stop_old_apps(modal_env)

    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        WEB_UI_GPU_ENV: args.gpu,
        CONFIG_PROFILE_ENV: profile,
        **empty_mode_env(profile),
    }
    log_path = modal_log_path("modal_serve")

    env_label = modal_env or "profile default"
    console.print(
        f"[bold blue]Starting modal serve[/bold blue] GPU=[bold]{args.gpu}[/] "
        f"profile=[bold]{profile}[/] env=[bold]{env_label}[/] log=[dim]{log_path}[/]"
    )
    console.print(
        "[dim]Modal may allocate GPU capacity, check the image, build, and mount containers "
        "before ComfyUI is reachable.[/dim]"
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "modal", "serve", *modal_env_args(modal_env), "server/ui.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    last_change = time.monotonic()
    seen_text_parts: list[str] = []
    printed_run_info = False
    printed_web_url = False
    opened_url = False
    watch_proc: subprocess.Popen[str] | None = None
    last_phase = ""

    output_lines = _start_output_reader(proc)

    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            while True:
                try:
                    line = output_lines.get(timeout=0.2)
                except queue.Empty:
                    if proc.poll() is not None:
                        return
                    if _idle_timeout_expired(
                        last_change,
                        time.monotonic(),
                        IDLE_TIMEOUT,
                        service_ready=opened_url,
                    ):
                        console.print(
                            f"\n[bold yellow]No startup log progress for {IDLE_TIMEOUT}s[/bold yellow] "
                            f"[dim]Check {log_path}[/dim]"
                        )
                        _stop_process(proc)
                        return
                    continue

                if line is None:
                    return

                if line:
                    last_change = time.monotonic()
                    log.write(line)
                    log.flush()
                    seen_text_parts.append(line)
                    text = sanitize_modal_error("".join(seen_text_parts))

                    phase = _current_phase(text).strip()
                    if phase and phase != last_phase:
                        console.print(f"[dim]{phase}[/]")
                        last_phase = phase

                    err = _error_signal(line, service_ready=opened_url)
                    if err:
                        console.print(f"\n[bold red]Error detected:[/] {err!r}")
                        console.print(f"[dim]Check log: {log_path}[/dim]")
                        _stop_process(proc)
                        return

                    run_info = parse_modal_run_info(text)
                    if run_info and not printed_run_info and (
                        "app_id" in run_info or "web_url" in run_info
                    ):
                        _print_run_info(run_info, log_path, proc.pid)
                        printed_run_info = True
                        if "web_url" not in run_info:
                            console.print("[dim]Waiting for ComfyUI URL...[/dim]")

                    if "web_url" in run_info and not printed_web_url:
                        _print_run_info(run_info, log_path, proc.pid)
                        printed_web_url = True

                    if "web_url" in run_info and not opened_url:
                        url = run_info["web_url"]
                        if watch_proc is None and not args.no_watch:
                            watch_proc = _start_output_watcher(url)
                        if not args.no_open:
                            with suppress(Exception):
                                webbrowser.open(url)
                        if _probe_url(url):
                            console.print("[bold green]Health check passed[/bold green]")
                        else:
                            console.print("[bold yellow]Health check failed; service may still be starting[/bold yellow]")
                        opened_url = True
    except KeyboardInterrupt:
        _stop_process(proc, interrupted=True)
        return
    finally:
        if watch_proc is not None:
            _stop_process(watch_proc)


if __name__ == "__main__":
    main()
