import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PostgreSQL helper")


@pytest.fixture
def local_postgres():
    spec = importlib.util.spec_from_file_location(
        "local_postgres", Path(__file__).resolve().parents[2] / "scripts/local_postgres.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("action,returncode", [("start", 0), ("status", 3), ("stop", 1)])
def test_pg_ctl_does_not_wait_for_server_inherited_pipes(local_postgres, monkeypatch, action, returncode):
    def run(command, **kwargs):
        assert not kwargs.get("capture_output")
        assert kwargs["stdout"] != subprocess.PIPE
        assert kwargs["stderr"] != subprocess.PIPE
        assert kwargs["timeout"] > 30
        kwargs["stdout"].write("server status\n")
        kwargs["stderr"].write("server operation failed\n")
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(local_postgres.subprocess, "run", run)
    if returncode and action != "status":
        with pytest.raises(RuntimeError, match="server operation failed"):
            local_postgres.pg_ctl(action)
    else:
        result = local_postgres.pg_ctl(action)
        assert result.returncode == returncode
        assert result.stdout.strip() == "server status"
        assert result.stderr.strip() == "server operation failed"
