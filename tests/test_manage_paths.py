from __future__ import annotations

from pathlib import Path

import pytest

from scripts import huggingface, local_paths


def test_project_path_candidates_are_directories_for_questionary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".env.anthro").write_text("not a directory")
    (tmp_path / "workflows").mkdir()
    local_models = tmp_path / "local-models"
    local_models.mkdir()
    model_file = local_models / "sample.safetensors"
    model_file.write_text("model")

    monkeypatch.chdir(tmp_path)

    candidates = [Path(path) for path in local_paths.project_path_candidates()]

    assert candidates
    assert all(path.is_dir() for path in candidates)
    assert tmp_path / ".env.anthro" not in candidates
    assert local_paths.nearest_project_path("sample.safetensors") == model_file


def test_normalise_cache_filename_rejects_escaping_paths() -> None:
    assert (
        local_paths.normalise_cache_filename("local-models/loras/model.safetensors")
        == "local-models/loras/model.safetensors"
    )
    with pytest.raises(ValueError, match="relative path"):
        local_paths.normalise_cache_filename("/etc/passwd")
    with pytest.raises(ValueError, match="relative path"):
        local_paths.normalise_cache_filename("../outside.safetensors")


def test_windows_separator_note_only_fires_for_backslashes() -> None:
    assert local_paths.windows_separator_note("F:/models/a.safetensors") == ""
    assert "Windows separators" in local_paths.windows_separator_note(r"F:\models\a.safetensors")


def test_guess_model_dir_maps_known_filenames() -> None:
    assert huggingface.guess_model_dir("loras/style.safetensors") == "loras"
    assert huggingface.guess_model_dir("vae/model.safetensors") == "vae"
    assert huggingface.guess_model_dir("model.safetensors") == "checkpoints"
    assert huggingface.is_model_file("model.gguf")
    assert not huggingface.is_model_file("README.md")


def test_add_local_model_path_prompt_uses_supported_kwargs(monkeypatch) -> None:
    import manage

    captured_kwargs = {}

    class Prompt:
        def ask(self) -> None:
            return None

    def fake_path(*args, **kwargs) -> Prompt:
        captured_kwargs.update(kwargs)
        return Prompt()

    monkeypatch.setattr(manage.questionary, "path", fake_path)

    manage._add_local_model(None)  # cfg is unused when the first prompt is cancelled.

    assert captured_kwargs["get_paths"] is local_paths.project_path_candidates
    assert "instruction" not in captured_kwargs


def test_list_repo_files_uses_hub_api(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"siblings": [{"rfilename": "weights/model.safetensors"}]}

    def fake_get(url: str, **kwargs):
        assert url == "https://huggingface.co/api/models/circlestone-labs/Anima"
        assert kwargs["params"] == {"expand[]": "siblings"}
        assert kwargs["timeout"] == 15
        return Response()

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(huggingface.requests, "get", fake_get)

    assert huggingface.list_repo_files("circlestone-labs/Anima") == [
        "weights/model.safetensors"
    ]


def test_hf_list_files_reports_failure_as_none(monkeypatch) -> None:
    import manage

    def boom(repo_id: str) -> list[str]:
        raise ValueError("unexpected payload")

    monkeypatch.setattr(manage, "list_repo_files", boom)

    assert manage._hf_list_files("owner/repo") is None
