from __future__ import annotations

from scripts import manage_volumes as manage


def test_huggingface_cache_repo_path_only_accepts_repo_roots() -> None:
    assert (
        manage._huggingface_cache_repo_path(
            "/cache/models--owner--repo/snapshots/commit/model.safetensors"
        )
        == "/models--owner--repo"
    )
    assert manage._huggingface_cache_repo_path("/cache/custom_nodes/node") is None
    assert manage._huggingface_cache_repo_path("/cache/models--owner--repo") == "/models--owner--repo"
    assert manage._huggingface_cache_repo_path("/") is None


def test_remove_huggingface_cache_repositories_removes_recursively(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    class FakeVolume:
        def remove_file(self, path: str, *, recursive: bool) -> None:
            calls.append((path, recursive))

    monkeypatch.setattr(
        manage.modal.Volume,
        "from_name",
        lambda name: FakeVolume(),
    )
    monkeypatch.setattr(
        manage,
        "_load_model_manifest",
        lambda: [
            {
                "cache_path": "/cache/models--owner--repo/snapshots/abc/model.safetensors",
            }
        ],
    )
    monkeypatch.setattr(manage, "forget_cached_model_files", lambda paths: None)
    monkeypatch.setattr(manage, "_mark_deleted_model_files", lambda paths: None)

    removed, failed = manage.remove_huggingface_cache_repositories(
        ["/models--owner--repo"]
    )

    assert removed == ["/models--owner--repo"]
    assert failed == []
    assert calls == [("/models--owner--repo", True)]
