"""Diagnostic probe for Modal web_server endpoints.

Starts a Modal app from Python so the hydrated Function web URL can be
printed and actively requested. This is intentionally separate from
``serve.py`` so normal dev serving behavior stays unchanged.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from urllib import request as urlrequest

import modal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_modal(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "modal", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    return (result.stdout or "") + (result.stderr or "")


def _sample_state(label: str, app_id: str | None = None) -> None:
    print(f"PROBE_STATE {label} app_id={app_id}", flush=True)
    print(_run_modal(["app", "list"]), flush=True)
    print(_run_modal(["container", "list"]), flush=True)


def _request(url: str, path: str, timeout: float) -> None:
    target = url.rstrip("/") + path
    print(f"PROBE_REQUEST {target}", flush=True)
    try:
        with urlrequest.urlopen(urlrequest.Request(target), timeout=timeout) as resp:
            print(f"PROBE_RESPONSE status={resp.status}", flush=True)
    except Exception as exc:
        print(f"PROBE_RESPONSE error={type(exc).__name__}: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target",
        choices=["ui", "smoke"],
        help="Probe server.ui.ui or server.smoke_ui.smoke_ui.",
    )
    parser.add_argument("--path", default="/", help="Path to request after web URL is known.")
    parser.add_argument("--hold-seconds", type=int, default=60)
    parser.add_argument("--request-timeout", type=float, default=30)
    args = parser.parse_args()

    if args.target == "ui":
        from server.app import app
        from server.ui import ui as fn
    else:
        from server.app import app
        from server.smoke_ui import smoke_ui as fn

    print(f"PROBE_START target={args.target}", flush=True)
    with app.run():
        app_id = app.app_id
        print(f"PROBE_APP app_id={app_id}", flush=True)
        web_url = fn.get_web_url()
        print(f"PROBE_WEB_URL {web_url}", flush=True)
        _sample_state("before_request", app_id)
        if web_url:
            _request(web_url, args.path, args.request_timeout)
        _sample_state("after_request", app_id)
        time.sleep(args.hold_seconds)
        _sample_state("after_hold", app_id)

    print("PROBE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
