import os
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app import database, model_settings
from app.config import settings
from app.tools import python_tool


@pytest.fixture
def container_settings(monkeypatch, tmp_path):
    key_file = tmp_path / "storage.key"
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    monkeypatch.setattr(settings, "local_secret_key_file", key_file)
    return tmp_path, key_file


def model_config():
    return model_settings.ModelSettingsInput(
        base_url="https://example.invalid/v1", model="offline-model",
        api_key=SecretStr("offline-container-secret"),
    )


def test_container_secrets_are_encrypted_and_windows_files_remain(container_settings):
    root, _ = container_settings
    (root / "model-settings.bin").write_bytes(b"original-windows-model")
    (root / "database-settings.bin").write_bytes(b"original-windows-database")
    model_settings.save_model_settings(model_config())
    database.save_database(database.DatabaseInput(
        database="lab", username="reader", password=SecretStr("offline-database-secret"),
    ))
    assert model_settings.load_saved_settings().api_key.get_secret_value() == "offline-container-secret"
    assert database.load_database().password.get_secret_value() == "offline-database-secret"
    assert b"offline-container-secret" not in (root / "model-settings.container.bin").read_bytes()
    assert b"offline-database-secret" not in (root / "database-settings.container.bin").read_bytes()
    assert (root / "model-settings.bin").read_bytes() == b"original-windows-model"
    assert (root / "database-settings.bin").read_bytes() == b"original-windows-database"
    assert "offline-container-secret" not in str(model_settings.get_model_status())


def test_bad_container_key_never_saves_plaintext(container_settings):
    root, key_file = container_settings
    key_file.write_bytes(b"invalid-key")
    with pytest.raises(model_settings.SecretStorageError):
        model_settings.save_model_settings(model_config())
    assert not (root / "model-settings.container.bin").exists()


def test_changed_container_key_fails_closed(container_settings):
    _, key_file = container_settings
    model_settings.save_model_settings(model_config())
    key_file.write_bytes(Fernet.generate_key())
    with pytest.raises(model_settings.SecretStorageError):
        model_settings.load_saved_settings()


def test_sandbox_mounts_use_only_the_shared_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "docker_bind_root", "/host/project/.local")
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    assert python_tool._mount_path(tmp_path / "uploads" / "session" / "sales.parquet") == str(
        PurePosixPath("/host/project/.local/uploads/session/sales.parquet")
    )
    with pytest.raises(ValueError):
        python_tool._mount_path(tmp_path.parent / "outside.parquet")


def test_desktop_drive_letters_do_not_become_volume_mode_separators(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "docker_bind_root", "G:/project/.local")
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    monkeypatch.setattr(settings, "docker_wsl_distro", "")
    temporary = tmp_path / "container-tmp"
    temporary.mkdir()
    monkeypatch.setattr(python_tool.tempfile, "gettempdir", lambda: str(temporary))
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="1", stderr="")
    monkeypatch.setattr(python_tool.subprocess, "run", run)
    assert python_tool.execute_python("result = 1", [str(tmp_path / "uploads/sales.parquet")]).ok
    command = commands[0]
    assert "-v" not in command
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 2
    assert all(mount.startswith("type=bind,source=G:/project/.local/") and mount.endswith(",readonly") for mount in mounts)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions apply inside the backend container")
def test_sandbox_user_can_read_temporary_script(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "docker_bind_root", "")
    monkeypatch.setattr(settings, "docker_wsl_distro", "")
    monkeypatch.setattr(python_tool.tempfile, "gettempdir", lambda: str(tmp_path))

    def run(command, **kwargs):
        script, = tmp_path.glob("*.py")
        assert script.stat().st_mode & 0o044 == 0o044
        assert script.stat().st_mode & 0o022 == 0
        return SimpleNamespace(returncode=0, stdout="1", stderr="")

    monkeypatch.setattr(python_tool.subprocess, "run", run)
    assert python_tool.execute_python("result = 1").ok
    assert not list(tmp_path.glob("*.py"))
