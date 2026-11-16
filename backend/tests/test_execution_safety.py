from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.tools import python_tool


@pytest.fixture(autouse=True)
def native_backend_by_default(monkeypatch):
    monkeypatch.setattr(python_tool.settings, "docker_wsl_distro", "")


def test_missing_docker_never_executes_generated_code_on_host(monkeypatch):
    commands = []
    def missing(command, **kwargs):
        commands.append(command)
        raise FileNotFoundError("docker unavailable")
    monkeypatch.setattr(python_tool.subprocess, "run", missing)
    result = python_tool.execute_python("result = 1")
    assert not result.ok
    assert "隔离" in result.error
    assert len(commands) == 1 and commands[0][0] == "docker"


def test_stopped_docker_does_not_fall_back_to_host(monkeypatch):
    commands = []
    def stopped(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1, stderr="Cannot connect to the Docker daemon", stdout="")
    monkeypatch.setattr(python_tool.subprocess, "run", stopped)
    result = python_tool.execute_python("result = 1")
    assert not result.ok and "隔离" in result.error
    assert len(commands) == 1


def test_timeout_removes_only_the_named_container(monkeypatch):
    commands = []
    def timeout(command, **kwargs):
        commands.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 15)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(python_tool.subprocess, "run", timeout)
    result = python_tool.execute_python("while True: pass")
    assert not result.ok
    assert commands[1][:3] == ["docker", "rm", "-f"]
    assert commands[1][3].startswith("datapilot-run-")
    assert commands[1][3] in commands[0]


def test_wsl_docker_translates_mounts_without_host_execution(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "docker_wsl_distro", "Ubuntu-24.04", raising=False)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert command[:4] == ["wsl", "--distribution", "Ubuntu-24.04", "--exec"]
        if command[4] == "wslpath":
            return SimpleNamespace(returncode=0, stdout="/mnt/c/translated/" + command[-1].replace("\\", "/").split("/")[-1] + "\n")
        assert command[4:6] == ["docker", "run"]
        assert "--network=none" in command and "--read-only" in command
        mounts = [command[i+1] for i, value in enumerate(command) if value == "-v"]
        assert all(m.startswith("/mnt/c/translated/") and m.endswith(":ro") for m in mounts)
        return SimpleNamespace(returncode=0, stdout="0.266667", stderr="")
    monkeypatch.setattr(python_tool.subprocess, "run", run)
    result = python_tool.execute_python("result = 4/15", [str(tmp_path / "stats.parquet")])
    assert result.ok and result.output == "0.266667" and len(commands) == 3


def test_wsl_boot_timeout_does_not_attempt_a_container_or_host_fallback(monkeypatch):
    monkeypatch.setattr(python_tool.settings, "docker_wsl_distro", "Ubuntu-24.04")
    commands = []
    def timeout(command, **kwargs):
        commands.append(command)
        assert command[4] == "wslpath"
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(python_tool.subprocess, "run", timeout)
    result = python_tool.execute_python("result = 1")
    assert not result.ok and "WSL" in result.error and len(commands) == 1
