#!/usr/bin/env python3
"""
用法：python serve.py
自动停止旧的 ephemeral app，再启动 modal serve，日志写入 logs/modal_serve_[时间戳].log

环境变量：
  SERVE_IDLE_TIMEOUT   无日志增长超时秒数（默认 120）
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import request as urlrequest

import tqdm

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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"modal_serve_{timestamp}.log"

    print(f"Starting modal serve → {log_path}")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["modal", "serve", "server/ui.py"],
            env=env,
            stdout=log,
            stderr=log,
        )

    last_size = 0
    last_change = time.monotonic()
    seen_text = ""

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

            if "modal.run" in text:
                for line in text.splitlines():
                    if "modal.run" in line:
                        url = line.strip()
                        bar.close()
                        print(f"\n✓ ComfyUI URL: {url}")
                        print(f"  Log: {log_path}  PID: {proc.pid}  (Ctrl-C to stop)")
                        if _probe_url(url):
                            print("  ✓ Health check passed")
                        else:
                            print("  ⚠ Health check failed (service may still be starting)")
                        proc.wait()
                        return

    print(f"\nMax display time reached — check {log_path}")
    proc.wait()


if __name__ == "__main__":
    main()
