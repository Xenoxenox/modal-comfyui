from __future__ import annotations

import datetime as dt
import dataclasses
import json
import os
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scripts.modal_status import sanitize_modal_error

console = Console()


@dataclasses.dataclass(frozen=True)
class BillingReport:
    rows: list[dict[str, Any]] | None
    error: str | None = None
    show_error_trace: bool = False


_billing_command_cache: list[str] | None | bool = None


def _run_text(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _modal_command_supports_billing(command_prefix: list[str]) -> bool:
    try:
        result = _run_text([*command_prefix, "billing", "report", "--help"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _billing_command_prefix() -> list[str] | None:
    global _billing_command_cache
    if _billing_command_cache is not None:
        return None if _billing_command_cache is False else list(_billing_command_cache)

    candidates = [["modal"]]
    current_python = [sys.executable, "-m", "modal"]
    if current_python not in candidates:
        candidates.append(current_python)

    scripts_dir = os.path.dirname(sys.executable)
    venv_modal = os.path.join(scripts_dir, "modal.exe" if os.name == "nt" else "modal")
    if os.path.exists(venv_modal):
        venv_candidate = [venv_modal]
        if venv_candidate not in candidates:
            candidates.append(venv_candidate)

    for candidate in candidates:
        if _modal_command_supports_billing(candidate):
            _billing_command_cache = candidate
            return list(candidate)

    _billing_command_cache = False
    return None


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


def _modal_billing_time(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _billing_error(detail: str) -> BillingReport:
    clean_detail = sanitize_modal_error(detail.strip())
    lowered = clean_detail.lower()
    if "no such command" in lowered and "billing" in lowered:
        return BillingReport(
            None,
            "Modal CLI billing command is unavailable; use the Modal Web Dashboard.",
            show_error_trace=False,
        )
    return BillingReport(None, clean_detail, show_error_trace=True)


def fetch_billing_report(start: dt.datetime, end: dt.datetime) -> BillingReport:
    start_arg = _modal_billing_time(start)
    end_arg = _modal_billing_time(end)
    command_prefix = _billing_command_prefix()
    if command_prefix is None:
        return BillingReport(
            None,
            "No Modal CLI with billing report support was found; use the Modal Web Dashboard.",
            show_error_trace=False,
        )
    try:
        result = _run_text(
            [
                *command_prefix,
                "billing",
                "report",
                "--start",
                start_arg,
                "--end",
                end_arg,
                "--resolution",
                "h",
                "--tz",
                "local",
                "--json",
            ],
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _billing_error(str(exc))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"modal billing exited with code {result.returncode}").strip()
        return _billing_error(detail)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return BillingReport(None, f"Could not parse Modal billing JSON: {exc}")
    if isinstance(data, list):
        return BillingReport([row for row in data if isinstance(row, dict)])
    return BillingReport(None, "Modal billing JSON was not a list.")


def print_exit_summary(session_start: dt.datetime, session_end: dt.datetime) -> None:
    billing_start = floor_hour(session_start)
    billing_end = ceil_hour(session_end)
    profile = current_modal_profile() or "Unavailable"
    report = fetch_billing_report(billing_start, billing_end)
    rows = report.rows

    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column("Key", style="dim", no_wrap=True)
    summary.add_column("Value", style="bold white", overflow="fold")
    summary.add_row("Modal Profile", sanitize_modal_error(profile))
    summary.add_row("Session Start", _format_dt(session_start))
    summary.add_row("Session End", _format_dt(session_end))
    summary.add_row("Billing Window", f"{_format_dt(billing_start)} -> {_format_dt(billing_end)}")
    summary.add_row("Dashboard", "https://modal.com/home")

    if rows is None:
        summary.add_row("Billing", "[link=https://modal.com/settings/billing]Check on Modal Web Dashboard[/link]")
        console.print(Panel(summary, title="[bold blue]Session Summary[/bold blue]", border_style="blue", box=box.ROUNDED))
        if report.error and report.show_error_trace:
            console.print(f"[dim yellow]Billing CLI Error Trace: {report.error}[/]")
        elif report.error:
            console.print(f"[dim]{report.error}[/dim]")
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
