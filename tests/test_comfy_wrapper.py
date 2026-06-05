from __future__ import annotations

from pathlib import Path

import pytest

from server import comfy_wrapper
from server.comfy_wrapper import ComfyExecutor


def test_collect_outputs_copies_declared_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "comfy-output"
    nested = output_root / "nested"
    nested.mkdir(parents=True)
    (output_root / "first.png").write_bytes(b"first")
    (nested / "second.gif").write_bytes(b"second")
    monkeypatch.setattr(comfy_wrapper, "DEFAULT_OUTPUT_DIR", str(output_root))

    history = {
        "outputs": {
            "1": {
                "images": [{"filename": "first.png", "subfolder": ""}],
                "gifs": [{"filename": "second.gif", "subfolder": "nested"}],
            }
        }
    }

    dest_dir = tmp_path / "session"
    collected = ComfyExecutor().collect_outputs(history, dest_dir)

    assert collected == [dest_dir / "first.png", dest_dir / "second.gif"]
    assert (dest_dir / "first.png").read_bytes() == b"first"
    assert (dest_dir / "second.gif").read_bytes() == b"second"


def test_collect_outputs_raises_when_declared_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "comfy-output"
    output_root.mkdir()
    monkeypatch.setattr(comfy_wrapper, "DEFAULT_OUTPUT_DIR", str(output_root))

    history = {
        "outputs": {
            "1": {
                "images": [{"filename": "missing.png", "subfolder": ""}],
            }
        }
    }

    with pytest.raises(RuntimeError, match="declared 1 output"):
        ComfyExecutor().collect_outputs(history, tmp_path / "session")

    captured = capsys.readouterr()
    assert "[comfy_wrapper] MISSING declared output:" in captured.out
    assert "missing.png" in captured.out


def test_collect_outputs_allows_zero_declared_outputs(tmp_path: Path) -> None:
    history = {"outputs": {}}

    collected = ComfyExecutor().collect_outputs(history, tmp_path / "session")

    assert collected == []
