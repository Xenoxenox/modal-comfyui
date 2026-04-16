#!/usr/bin/env python3
"""
用法：python serve.py
自动停止旧的 ephemeral app，再启动 modal serve，日志写入 modal_serve.log
"""
import os
import subprocess
import sys
import time


def stop_old_apps():
    result = subprocess.run(
        ["modal", "app", "list"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    for line in result.stdout.splitlines():
        if "ephemeral" in line:
            app_id = line.split("|")[1].strip()
            if app_id:
                print(f"Stopping old app: {app_id}")
                subprocess.run(["modal", "app", "stop", app_id], capture_output=True)
    time.sleep(5)


def main():
    stop_old_apps()

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    log_path = "modal_serve.log"

    print(f"Starting modal serve → {log_path}")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["modal", "serve", "server/ui.py"],
            env=env,
            stdout=log,
            stderr=log,
        )

    # 等待 URL 出现在日志
    print("Waiting for URL", end="", flush=True)
    for _ in range(60):
        time.sleep(2)
        print(".", end="", flush=True)
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    if "modal.run" in line:
                        print(f"\n\n✓ ComfyUI URL: {line.strip()}")
                        print(f"  Log: {log_path}")
                        print(f"  PID: {proc.pid}  (Ctrl-C to stop)")
                        proc.wait()
                        return
        except Exception:
            pass

    print("\nURL not found yet — check modal_serve.log manually")
    proc.wait()


if __name__ == "__main__":
    main()
