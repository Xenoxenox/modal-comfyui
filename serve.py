#!/usr/bin/env python3
"""
用法：python serve.py [--gpu L4]
自动停止旧的 ephemeral app，再启动 modal serve，日志写入 logs/modal_serve_[时间戳].log

环境变量：
  SERVE_IDLE_TIMEOUT   无日志增长超时秒数（默认 120）
"""
import argparse
import os
import subprocess
import sys
import time
from urllib import request as urlrequest

import tqdm

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

IDLE_TIMEOUT = int(os.getenv("SERVE_IDLE_TIMEOUT", "120"))
POLL_INTERVAL = 2
MAX_TICKS = (10 * 60) // POLL_INTERVAL  # 10 min display ceiling

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
    print("\nModal run details")
    if app_id:
        print(f"  App ID: {app_id}")
    if info.get("dashboard_url"):
        print(f"  Dashboard: {info['dashboard_url']}")
    if function_call_id:
        print(f"  Function Call ID: {function_call_id}")
    if info.get("function_call_url"):
        print(f"  Function Call: {info['function_call_url']}")
    if app_id:
        print(f"  Logs command: {shell_command_text(modal_app_logs_command(app_id, function_call_id))}")
        print(
            "  Stop command: "
            f"{shell_command_text(modal_app_stop_command(app_id))} "
            "(only if the app lingers after you are done)"
        )
    if info.get("web_url"):
        print(f"  ComfyUI URL: {info['web_url']}")
    print(f"  Local app log: {log_path}")
    if proc_pid is not None:
        print(f"  Local PID: {proc_pid} (Ctrl+C to stop attached serve)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start modal serve for the ComfyUI Web UI."
    )
    add_web_ui_mode_args(parser)
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
    print(
        f"Starting modal serve on GPU {args.gpu} "
        f"profile={profile} env={env_label} -> {log_path}"
    )
    print("Modal may spend time allocating GPU capacity, checking the image, and mounting containers.")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "modal", "serve", *modal_env_args(modal_env), "server/ui.py"],
            env=env,
            stdout=log,
            stderr=log,
        )

    last_size = 0
    last_change = time.monotonic()
    seen_text = ""
    printed_run_info = False

    with tqdm.tqdm(
        total=MAX_TICKS,
        unit="tick",
        dynamic_ncols=True,
        bar_format="{desc} [{elapsed}<{remaining}]",
        file=sys.stdout,
    ) as bar:
        bar.set_description("[0/4] Waiting          ")
        for _ in range(MAX_TICKS):
            time.sleep(POLL_INTERVAL)

            try:
                size = log_path.stat().st_size
            except OSError:
                bar.update(1)
                continue

            if size != last_size:
                last_size = size
                last_change = time.monotonic()

            if time.monotonic() - last_change > IDLE_TIMEOUT:
                bar.close()
                print(f"\nNo log progress for {IDLE_TIMEOUT}s — check {log_path}")
                proc.wait()
                return

            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                bar.update(1)
                continue

            bar.set_description(_current_phase(text))
            bar.update(1)

            new_text = text[len(seen_text):]
            for err in ERROR_SIGNALS:
                if err in new_text:
                    bar.close()
                    print(f"\n✗ Error detected: {err!r}")
                    print(f"  Check log: {log_path}")
                    proc.terminate()
                    return
            seen_text = text

            run_info = parse_modal_run_info(sanitize_modal_error(text))
            if run_info and not printed_run_info and (
                "app_id" in run_info or "web_url" in run_info
            ):
                if "web_url" in run_info:
                    bar.close()
                _print_run_info(run_info, log_path, proc.pid)
                printed_run_info = True
                if "web_url" not in run_info:
                    print("  Waiting for ComfyUI URL...")

            if "web_url" in run_info:
                url = run_info["web_url"]
                bar.close()
                if not printed_run_info:
                    _print_run_info(run_info, log_path, proc.pid)
                if _probe_url(url):
                    print("  Health check passed")
                else:
                    print("  Health check failed (service may still be starting)")
                proc.wait()
                return

    print(f"\nMax display time reached — check {log_path}")
    proc.wait()


if __name__ == "__main__":
    main()
