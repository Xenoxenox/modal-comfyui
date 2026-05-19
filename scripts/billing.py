from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from rich import box
from rich.panel import Panel
from rich.table import Table

from scripts.modal_status import sanitize_modal_error
from scripts.tui import console


def _run_text(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def current_modal_profile() -> str | None:
    try:
        result = _run_text([sys.executable, "-m", "modal", "profile", "current"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def floor_hour(value: dt.datetime) -> dt.datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def ceil_hour(value: dt.datetime) -> dt.datetime:
    floored = floor_hour(value)
    if floored == value:
        return floored
    return floored + dt.timedelta(hours=1)


def _format_dt(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M %Z")


def _parse_cost(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except InvalidOperation:
        return Decimal("0")


def fetch_billing_report(start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]] | None:
    try:
        result = _run_text(
            [
                sys.executable,
                "-m",
                "modal",
                "billing",
                "report",
                "--start",
                start.isoformat(),
                "--end",
                end.isoformat(),
                "--resolution",
                "h",
                "--tz",
                "local",
                "--json",
            ],
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return None


def print_exit_summary(session_start: dt.datetime, session_end: dt.datetime) -> None:
    billing_start = floor_hour(session_start)
    billing_end = ceil_hour(session_end)
    profile = current_modal_profile() or "Unavailable"
    rows = fetch_billing_report(billing_start, billing_end)

    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column("Key", style="dim", no_wrap=True)
    summary.add_column("Value", style="bold white", overflow="fold")
    summary.add_row("Modal Profile", sanitize_modal_error(profile))
    summary.add_row("Session Start", _format_dt(session_start))
    summary.add_row("Session End", _format_dt(session_end))
    summary.add_row("Billing Window", f"{_format_dt(billing_start)} -> {_format_dt(billing_end)}")
    summary.add_row("Dashboard", "https://modal.com/home")

    if rows is None:
        summary.add_row("Billing", "unavailable")
        console.print(Panel(summary, title="[bold blue]Session Summary[/bold blue]", border_style="blue", box=box.ROUNDED))
        console.print("[dim]Billing summary is best-effort and does not block TUI exit.[/dim]")
        return

    totals: dict[str, Decimal] = {}
    for row in rows:
        description = str(row.get("Description") or row.get("description") or "Other")
        cost = _parse_cost(row.get("Cost") or row.get("cost"))
        totals[description] = totals.get(description, Decimal("0")) + cost

    total = sum(totals.values(), Decimal("0"))
    summary.add_row("Total Reported Cost", f"${total:.4f}")
    summary.add_row("Rows", str(len(rows)))
    console.print(Panel(summary, title="[bold blue]Session Summary[/bold blue]", border_style="blue", box=box.ROUNDED))

    if totals:
        table = Table(title="Billing Rows", box=box.SIMPLE)
        table.add_column("Description", overflow="fold")
        table.add_column("Cost", justify="right")
        for description, cost in sorted(totals.items()):
            table.add_row(description, f"${cost:.4f}")
        console.print(table)
    else:
        console.print("[dim]No finalized billing rows for this session window yet.[/dim]")
    console.print("[dim]Modal reports full billing intervals only; the latest partial hour may appear later.[/dim]")
