"""Run the clean local first-install acceptance flow."""

from __future__ import annotations

import argparse
import platform
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

MIN_PYTHON = (3, 13)
REQUIRED_IMPORTS: tuple[tuple[str, str], ...] = (
    ("modal", "modal"),
    ("questionary", "questionary"),
    ("rich", "rich"),
    ("requests", "requests"),
    ("tomli-w", "tomli_w"),
    ("tqdm", "tqdm"),
    ("pytest", "pytest"),
)


def repository_root() -> Path:
    """Return the repository root, independent of the caller's directory."""

    return Path(__file__).resolve().parents[1]


def require_python_version(version: Sequence[int] | None = None) -> None:
    """Reject interpreters older than the project's declared requirement."""

    current = tuple(version or sys.version_info[:2])
    if current[:2] < MIN_PYTHON:
        actual = ".".join(str(part) for part in current[:2])
        required = ".".join(str(part) for part in MIN_PYTHON)
        raise RuntimeError(f"Python {required}+ is required; found Python {actual}.")


def require_clean_flag(clean: bool) -> None:
    """Require explicit consent before removing the local virtual environment."""

    if not clean:
        raise ValueError("--clean is required; refusing to touch .venv without it.")


def environment_path(root: Path) -> Path:
    return root / ".venv"


def clean_environment(root: Path) -> None:
    target = environment_path(root)
    if target.exists():
        shutil.rmtree(target)


def setup_command(root: Path, system: str | None = None) -> list[str]:
    """Select the existing platform installer without duplicating its logic."""

    current_system = system or platform.system()
    if current_system == "Windows":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "setup.ps1"),
        ]
    return ["sh", str(root / "setup.sh")]


def environment_python(root: Path, system: str | None = None) -> Path:
    current_system = system or platform.system()
    if current_system == "Windows":
        return environment_path(root) / "Scripts" / "python.exe"
    return environment_path(root) / "bin" / "python"


def _version_check_script() -> str:
    return (
        "import sys\n"
        "print(f'Python {sys.version.split()[0]}')\n"
        f"required = {MIN_PYTHON!r}\n"
        "if sys.version_info[:2] < required:\n"
        "    raise SystemExit(f'Python {required[0]}.{required[1]}+ is required')\n"
    )


def _import_check_script(imports: Iterable[str]) -> str:
    names = tuple(imports)
    return (
        "import importlib\n"
        f"modules = {names!r}\n"
        "for name in modules:\n"
        "    importlib.import_module(name)\n"
        "    print(f'Imported {name}')\n"
    )


def _command_text(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


def run_logged_command(
    command: Sequence[str],
    *,
    root: Path,
    log_file,
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """Run one child, teeing combined output to the terminal and UTF-8 log."""

    log_file.write(f"\n$ {_command_text(command)}\n")
    log_file.flush()
    process = popen_factory(
        [str(part) for part in command],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        log_file.write(line)
        log_file.flush()
    return process.wait()


def _log_header(log_file, *, root: Path, system: str) -> None:
    log_file.write("First-install acceptance run\n")
    log_file.write(f"Platform: {system}\n")
    log_file.write(f"Repository root: {root}\n")
    log_file.flush()


def run_first_install(
    *,
    root: Path,
    system: str,
    log_file,
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """Execute the clean install and all checks after preconditions pass."""

    clean_environment(root)
    status = run_logged_command(
        setup_command(root, system),
        root=root,
        log_file=log_file,
        popen_factory=popen_factory,
    )
    if status:
        return status

    interpreter = environment_python(root, system)
    if not interpreter.is_file():
        log_file.write(f"Missing virtual-environment interpreter: {interpreter}\n")
        return 1

    status = run_logged_command(
        [str(interpreter), "-c", _version_check_script()],
        root=root,
        log_file=log_file,
        popen_factory=popen_factory,
    )
    if status:
        return status

    status = run_logged_command(
        [
            str(interpreter),
            "-c",
            _import_check_script(module for _, module in REQUIRED_IMPORTS),
        ],
        root=root,
        log_file=log_file,
        popen_factory=popen_factory,
    )
    if status:
        return status

    return run_logged_command(
        [str(interpreter), "-m", "pytest"],
        root=root,
        log_file=log_file,
        popen_factory=popen_factory,
    )


def _new_log_path(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / "logs" / f"first-install-{timestamp}.log"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean and verify the local Python environment. Requires --clean and "
            "deletes only the repository .venv. Uses setup.ps1 on Windows or "
            "setup.sh elsewhere, writes logs/first-install-<UTC timestamp>.log, "
            "and does not perform Modal login or deployment."
        )
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="required: delete and recreate only the repository .venv",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        require_clean_flag(args.clean)
        require_python_version()
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    root = repository_root()
    system = platform.system()
    log_path = _new_log_path(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    status = 1
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            _log_header(log_file, root=root, system=system)
            status = run_first_install(root=root, system=system, log_file=log_file)
            log_file.write(f"Final exit status: {status}\n")
            log_file.flush()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        status = 1

    print(f"First-install log: {log_path}")
    print(f"First-install exit status: {status}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
