#!/usr/bin/env sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$REPO_ROOT"

TSINGHUA_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

step() {
  printf '\n==> %s\n' "$1"
}

step "Operating system"
OS_NAME=$(uname -s 2>/dev/null || printf 'unknown')
case "$OS_NAME" in
  Linux*) DETECTED_OS="Linux" ;;
  Darwin*) DETECTED_OS="macOS" ;;
  *) DETECTED_OS="$OS_NAME" ;;
esac
printf 'Detected %s.\n' "$DETECTED_OS"

step "Network"
if ping -c 1 -W 2 google.com >/dev/null 2>&1; then
  GOOGLE_REACHABLE=1
  printf 'google.com is reachable. Using the default Python package index.\n'
else
  GOOGLE_REACHABLE=0
  printf 'google.com timed out. uv sync will use the Tsinghua PyPI mirror.\n'
fi

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      PATH="$(dirname "$candidate"):$PATH"
      export PATH
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

step "uv"
if UV_CMD=$(find_uv); then
  printf 'Found uv: %s\n' "$("$UV_CMD" --version)"
else
  printf 'uv not found. Installing uv with the official installer...\n'
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    printf 'curl or wget is required to install uv.\n' >&2
    exit 1
  fi
  if ! UV_CMD=$(find_uv); then
    printf 'uv was installed, but it is not available in PATH. Restart the shell or add the uv install directory to PATH.\n' >&2
    exit 1
  fi
  printf 'Installed uv: %s\n' "$("$UV_CMD" --version)"
fi

step "Virtual environment"
if [ -f ".venv/pyvenv.cfg" ]; then
  printf 'Found existing .venv. uv will verify and update it.\n'
else
  printf 'No .venv found. uv will create it.\n'
fi

step "Dependencies"
if [ "$GOOGLE_REACHABLE" -eq 1 ]; then
  "$UV_CMD" sync --quiet --frozen
else
  "$UV_CMD" sync --quiet --frozen --default-index "$TSINGHUA_INDEX"
fi

step "Done"
printf 'Dependencies are ready.\n'
printf 'Next: authenticate Modal when needed:\n'
printf '  uv run modal setup\n'
printf 'Start the TUI:\n'
printf '  uv run python manage.py\n'
