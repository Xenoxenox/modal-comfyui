from __future__ import annotations

from pathlib import Path

import pytest

from scripts import test_first_install


def test_python_version_gate_rejects_before_cleanup() -> None:
    with pytest.raises(RuntimeError, match=r"Python 3\.13\+ is required"):
        test_first_install.require_python_version((3, 12))


def test_clean_flag_is_mandatory() -> None:
    with pytest.raises(ValueError, match="--clean is required"):
        test_first_install.require_clean_flag(False)


def test_clean_environment_targets_only_repository_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".venv"
    target.mkdir()
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        test_first_install.shutil,
        "rmtree",
        lambda path: calls.append((path, False)),
    )

    test_first_install.clean_environment(tmp_path)

    assert calls == [(target, False)]



def test_setup_command_selects_existing_platform_installers(tmp_path: Path) -> None:
    assert test_first_install.setup_command(tmp_path, "Windows") == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(tmp_path / "setup.ps1"),
    ]
    assert test_first_install.setup_command(tmp_path, "Linux") == [
        "sh",
        str(tmp_path / "setup.sh"),
    ]


def test_environment_python_uses_platform_specific_path(tmp_path: Path) -> None:
    assert test_first_install.environment_python(tmp_path, "Windows") == (
        tmp_path / ".venv" / "Scripts" / "python.exe"
    )
    assert test_first_install.environment_python(tmp_path, "Linux") == (
        tmp_path / ".venv" / "bin" / "python"
    )


def test_required_imports_map_declared_distributions_to_import_names() -> None:
    assert dict(test_first_install.REQUIRED_IMPORTS) == {
        "modal": "modal",
        "questionary": "questionary",
        "rich": "rich",
        "requests": "requests",
        "tomli-w": "tomli_w",
        "tqdm": "tqdm",
        "pytest": "pytest",
    }
    script = test_first_install._import_check_script(
        module for _, module in test_first_install.REQUIRED_IMPORTS
    )
    assert "'tomli_w'" in script


def test_run_logged_command_uses_root_and_tees_output(tmp_path: Path) -> None:
    class FakeProcess:
        stdout = iter(("first\n", "second\n"))

        def wait(self) -> int:
            return 0

    calls: list[dict[str, object]] = []

    def fake_popen(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return FakeProcess()

    log_file = (tmp_path / "run.log").open("w+", encoding="utf-8")
    try:
        status = test_first_install.run_logged_command(
            ["echo", "ok"],
            root=tmp_path,
            log_file=log_file,
            popen_factory=fake_popen,
        )
        log_file.seek(0)
        output = log_file.read()
    finally:
        log_file.close()

    assert status == 0
    assert calls[0]["command"] == ["echo", "ok"]
    assert calls[0]["cwd"] == tmp_path
    assert "first\nsecond\n" in output
    assert "$ echo ok" in output
