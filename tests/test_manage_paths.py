from __future__ import annotations

from pathlib import Path

import manage


def test_project_path_candidates_are_directories_for_questionary(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env.anthro").write_text("not a directory")
    (tmp_path / "workflows").mkdir()
    local_models = tmp_path / "local-models"
    local_models.mkdir()
    model_file = local_models / "sample.safetensors"
    model_file.write_text("model")

    monkeypatch.chdir(tmp_path)

    candidates = [Path(path) for path in manage._project_path_candidates()]

    assert candidates
    assert all(path.is_dir() for path in candidates)
    assert tmp_path / ".env.anthro" not in candidates
    assert manage._nearest_project_path("sample.safetensors") == model_file


def test_add_local_model_path_prompt_uses_supported_kwargs(monkeypatch) -> None:
    captured_kwargs = {}

    class Prompt:
        def ask(self) -> None:
            return None

    def fake_path(*args, **kwargs) -> Prompt:
        captured_kwargs.update(kwargs)
        return Prompt()

    monkeypatch.setattr(manage.questionary, "path", fake_path)

    manage._add_local_model(None)  # cfg is unused when the first prompt is cancelled.

    assert captured_kwargs["get_paths"] is manage._project_path_candidates
    assert "instruction" not in captured_kwargs
