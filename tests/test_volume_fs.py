from __future__ import annotations

from pathlib import Path

from scripts.volume_fs import entry_type, join_volume_path, read_volume_file


class _Entry:
    def __init__(self, path: str, *, type_value=None, is_dir: bool = False) -> None:
        self.path = path
        if type_value is not None:
            self.type = type_value
        if is_dir:
            self.is_dir = True


def test_entry_type_understands_modal_type_shapes() -> None:
    assert entry_type(_Entry("/a", type_value=1)) == "file"
    assert entry_type(_Entry("/a", type_value=2)) == "dir"

    class _Enum:
        name = "FILE"

    assert entry_type(_Entry("/a", type_value=_Enum())) == "file"
    assert entry_type(_Entry("/a", is_dir=True)) == "dir"
    assert entry_type(_Entry("/a")) == "file"


def test_join_volume_path_handles_parent_and_absolute_children() -> None:
    assert join_volume_path("/", "session-1") == "/session-1"
    assert join_volume_path("/session-1", "image.png") == "/session-1/image.png"
    assert join_volume_path("/session-1", "/absolute.png") == "/absolute.png"
    assert join_volume_path("/session-1/nested", "image.png") == "/session-1/nested/image.png"


def test_read_volume_file_decodes_every_modal_shape() -> None:
    class _Bytes:
        def read_file(self, path: str) -> bytes:
            return b"payload"

    class _Text:
        def read_file(self, path: str) -> str:
            return "payload"

    class _Chunks:
        def read_file(self, path: str) -> list[bytes]:
            return [b"pay", b"load"]

    assert read_volume_file(_Bytes(), "/f") == b"payload"
    assert read_volume_file(_Text(), "/f") == b"payload"
    assert read_volume_file(_Chunks(), "/f") == b"payload"


def test_download_volume_session_writes_nested_files(tmp_path: Path) -> None:
    from scripts.output_download import download_volume_session

    class _Entry:
        def __init__(self, path: str, kind) -> None:
            self.path = path
            self.type = kind

    class _Volume:
        def listdir(self, path: str):
            if path == "/s1":
                return [_Entry("/s1/nested", 2), _Entry("/s1/first.png", 1)]
            return [_Entry("/s1/nested/second.png", 1)]

        def read_file(self, path: str) -> bytes:
            return f"bytes:{path}".encode()

    result = download_volume_session(_Volume(), "s1", local_root=tmp_path)

    assert result.file_count == 2
    assert (tmp_path / "s1" / "first.png").read_bytes() == b"bytes:/s1/first.png"
    assert (tmp_path / "s1" / "nested" / "second.png").read_bytes() == b"bytes:/s1/nested/second.png"
