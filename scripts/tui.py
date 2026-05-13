from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import questionary
from questionary import Choice, Style
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

STYLE = Style([
    ("qmark", "fg:#58a6ff bold"),
    ("question", "fg:#f0f6fc bold"),
    ("answer", "fg:#7ee787 bold"),
    ("pointer", "fg:#f778ba bold"),
    ("highlighted", "fg:#f778ba bold"),
    ("selected", "fg:#7ee787"),
    ("separator", "fg:#30363d"),
    ("instruction", "fg:#8b949e italic"),
    ("text", "fg:#e6edf3"),
    ("checkbox", "fg:#7ee787"),
    ("disabled", "fg:#555555"),
])

console = Console()

GPU_CHOICES: list[tuple[str, str]] = [
    ("T4", "Lowest cost; useful for light workflows and smoke tests."),
    ("L4", "Default Web UI choice; balanced cost and compatibility."),
    ("L40S", "More VRAM and throughput for larger image/video workflows."),
    ("A10G", "Common midrange GPU for SDXL-era workflows."),
    ("A100-40GB", "High-memory option for large graphs."),
    ("A100-80GB", "Very high-memory option for large video workflows."),
    ("H100", "Fast premium option for heavy workloads."),
    ("H200", "Premium option with more memory than H100."),
    ("B200", "Newest high-end option when available in your Modal region."),
]

DEFAULT_GPU_CHOICES = [gpu for gpu, _description in GPU_CHOICES]

SOURCE_STYLES = {
    "HU": "white on blue",
    "SN": "black on cyan",
    "EX": "white on magenta",
    "LO": "black on green",
}


def print_banner(title: str, subtitle: str) -> None:
    console.print(
        Panel.fit(
            f"[bold cyan]{title}[/bold cyan]\n[dim]{subtitle}[/dim]",
            border_style="blue",
            padding=(1, 4),
        )
    )


def print_step(title: str) -> None:
    console.rule(f"[dim]{title}[/dim]", style="dim #30363d")


def print_status(message: str, *, style: str = "green") -> None:
    console.print(Panel.fit(message, border_style=style, padding=(0, 2)))


def print_result_panel(
    title: str,
    rows: Sequence[tuple[str, Any]],
    *,
    border_style: str = "green",
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True)
    table.add_column("Value", style="bold white", overflow="fold")
    for key, value in rows:
        if value is None or value == "":
            continue
        table.add_row(key, str(value))
    console.print(Panel(table, title=title, border_style=border_style, box=box.ROUNDED))


def source_badge(source: str) -> str:
    style = SOURCE_STYLES.get(source, "white on grey23")
    return f"[{style}] {source} [/]"


def print_command_panel(
    title: str,
    command: Sequence[str],
    rows: Sequence[tuple[str, Any]] = (),
    *,
    border_style: str = "blue",
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True)
    table.add_column("Value", style="bold white", overflow="fold")
    for key, value in rows:
        if value is None or value == "":
            continue
        table.add_row(key, str(value))
    command_text = " ".join(command)
    content = Table.grid(expand=True)
    if rows:
        content.add_row(table)
    content.add_row(Syntax(command_text, "powershell", word_wrap=True, theme="ansi_dark"))
    console.print(Panel(content, title=title, border_style=border_style, box=box.ROUNDED))


def model_card(name: str, source: str, target: str) -> Panel:
    color = SOURCE_STYLES.get(source, "white").split()[-1]
    content = Text.assemble(
        (f" {source} ", f"bold {SOURCE_STYLES.get(source, 'white on grey23')}"),
        f" {name}\n",
        (target, "dim italic"),
    )
    return Panel(content, expand=False, border_style=color)


def print_model_cards(cards: Sequence[Panel]) -> None:
    if cards:
        console.print(Columns(cards, equal=False, expand=False))


def ask_text(
    message: str,
    default: str = "",
    *,
    validate: Callable[[str], bool | str] | None = None,
    instruction: str | None = None,
) -> str:
    answer = questionary.text(
        message,
        default=default,
        validate=validate,
        instruction=instruction,
        style=STYLE,
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer.strip() or default.strip()


def ask_confirm(
    message: str,
    default: bool = False,
    *,
    instruction: str | None = None,
) -> bool:
    answer = questionary.confirm(
        message,
        default=default,
        instruction=instruction,
        style=STYLE,
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return bool(answer)


def ask_select(
    message: str,
    choices: Sequence[str | Choice],
    default: str | Choice | None = None,
    *,
    instruction: str | None = None,
) -> Any:
    answer = questionary.select(
        message,
        choices=choices,
        default=default,
        instruction=instruction,
        show_description=True,
        style=STYLE,
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def gpu_choice_items(default: str = "L4") -> list[Choice]:
    return [
        Choice(gpu, value=gpu, checked=(gpu == default), description=description)
        for gpu, description in GPU_CHOICES
    ]
