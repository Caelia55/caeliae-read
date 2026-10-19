from __future__ import annotations

import asyncio
import importlib.metadata
import os
from pathlib import Path
import shutil
import sys
import subprocess
import socket
import time
import json
from urllib.request import urlopen

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from caeliae_read import config
from test_mcp_contract import EXPECTED_TOOLS, tool_payload


NAMES = ("DATA_ROOT", "ALLOWED_ORIGINS", "MAX_PDF_BYTES", "MAX_PAGE_TEXT_CHARS",
         "SELECTION_CONTEXT_CHARS", "API_HOST", "API_PORT")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
NAMING_TEST_DATA_ROOT = (PROJECT_ROOT / ".artifacts" / "naming-test-data").resolve()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in NAMES:
        for prefix in ("CAELIAE_READ_", "COREAD_"):
            monkeypatch.delenv(prefix + name, raising=False)
    monkeypatch.setenv("CAELIAE_READ_DATA_ROOT", str(NAMING_TEST_DATA_ROOT))
    config._reported.clear()


def test_explicit_data_root_preserves_data_filename(monkeypatch, tmp_path):
    monkeypatch.setenv("CAELIAE_READ_DATA_ROOT", str(tmp_path))
    settings = config.get_settings()
    assert settings.database_path == tmp_path / "coread.sqlite3"
    assert settings.api_port == 8765


def test_missing_data_root_fails_closed(monkeypatch):
    monkeypatch.delenv("CAELIAE_READ_DATA_ROOT", raising=False)
    monkeypatch.delenv("COREAD_DATA_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="explicitly configured"):
        config.get_settings()


def _isolated_source_copy(tmp_path: Path) -> Path:
    fake_root = tmp_path / "fake-repo"
    shutil.copytree(PROJECT_ROOT / "src", fake_root / "src")
    return fake_root


def _clean_subprocess_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CAELIAE_READ_DATA_ROOT", None)
    env.pop("COREAD_DATA_ROOT", None)
    env["PYTHONPATH"] = str(root / "src")
    return env


def test_api_import_without_data_root_is_side_effect_free(tmp_path):
    fake_root = _isolated_source_copy(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "import caeliae_read.api.app"],
        cwd=fake_root,
        env=_clean_subprocess_environment(fake_root),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (fake_root / "data" / "coread.sqlite3").exists()


def test_api_startup_without_data_root_fails_closed(tmp_path):
    fake_root = _isolated_source_copy(tmp_path)
    code = (
        "from fastapi.testclient import TestClient; "
        "from caeliae_read.api.app import app; "
        "client = TestClient(app); client.__enter__()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=fake_root,
        env=_clean_subprocess_environment(fake_root),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "CAELIAE_READ_DATA_ROOT must be explicitly configured" in result.stderr
    assert not (fake_root / "data" / "coread.sqlite3").exists()


def test_formal_launchers_resolve_explicit_data_root():
    api_launcher = (PROJECT_ROOT / "scripts" / "start-local.ps1").read_text(encoding="utf-8")
    mcp_launcher = (PROJECT_ROOT / "start-caeliae-read-mcp.cmd").read_text(encoding="utf-8")
    assert "Join-Path $projectRoot 'data'" in api_launcher
    assert "EnvironmentVariables['CAELIAE_READ_DATA_ROOT'] = $dataRoot" in api_launcher
    assert 'set "CAELIAE_READ_DATA_ROOT=%~dp0data"' in mcp_launcher


@pytest.mark.parametrize("name", NAMES)
def test_environment_alias_precedence_and_stderr_once(monkeypatch, capsys, tmp_path, name):
    values = {"DATA_ROOT": str(tmp_path), "ALLOWED_ORIGINS": "https://example.invalid",
              "MAX_PDF_BYTES": "12345", "MAX_PAGE_TEXT_CHARS": "3000",
              "SELECTION_CONTEXT_CHARS": "80", "API_HOST": "127.0.0.2", "API_PORT": "9876"}
    value = values[name]
    monkeypatch.setenv("CAELIAE_READ_" + name, value)
    new = config.get_settings()
    assert capsys.readouterr().err == ""
    monkeypatch.delenv("CAELIAE_READ_" + name)
    monkeypatch.setenv("COREAD_" + name, value)
    assert config.get_settings() == new
    config.get_settings()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.count("deprecated") == 1
    assert value not in captured.err
    monkeypatch.setenv("CAELIAE_READ_" + name, value)
    assert config.get_settings() == new
    captured = capsys.readouterr()
    assert captured.out == "" and "ignored" in captured.err


def test_data_root_conflict_and_equivalent_paths(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CAELIAE_READ_DATA_ROOT", str(tmp_path / "new"))
    monkeypatch.setenv("COREAD_DATA_ROOT", str(tmp_path / "old"))
    with pytest.raises(ValueError, match="configuration conflict") as error:
        config.get_settings()
    assert str(tmp_path) not in str(error.value)
    monkeypatch.setenv("COREAD_DATA_ROOT", str(tmp_path / "new" / "."))
    assert config.get_settings().data_root == (tmp_path / "new").resolve()
    assert capsys.readouterr().out == ""


def test_non_data_new_value_wins(monkeypatch, capsys):
    monkeypatch.setenv("CAELIAE_READ_API_PORT", "9876")
    monkeypatch.setenv("COREAD_API_PORT", "9875")
    assert config.get_settings().api_port == 9876
    captured = capsys.readouterr()
    assert "ignored" in captured.err and "9875" not in captured.err


async def inspect_entry(command: str, args: list[str], data_root: Path, old_only: bool = False):
    env = os.environ.copy()
    env.pop("COREAD_DATA_ROOT", None)
    env.pop("CAELIAE_READ_DATA_ROOT", None)
    env[("COREAD_" if old_only else "CAELIAE_READ_") + "DATA_ROOT"] = str(data_root)
    async with stdio_client(StdioServerParameters(command=command, args=args, env=env)) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            assert initialized.serverInfo.name == "caeliae-read"
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
            return tool_payload(await session.call_tool("get_current_reading_context", {}))


def test_new_and_legacy_entries_share_isolated_state(settings, client, session):
    from conftest import state_payload
    from uuid import uuid4
    client.put(f"/api/sessions/{session['session_id']}/state", json=state_payload(session, event_id=str(uuid4())))
    expected = client.get("/api/reading/current").json()
    scripts = Path(sys.executable).parent
    entries = [
        (sys.executable, ["-m", "caeliae_read.mcp.server"]),
        (sys.executable, ["-m", "coread_core.mcp.server"]),
        (str(scripts / "caeliae-read-mcp.exe"), []),
        (str(scripts / "coread-mcp.exe"), []),
    ]
    for command, args in entries:
        assert asyncio.run(inspect_entry(command, args, settings.data_root)) == expected
    assert asyncio.run(inspect_entry(sys.executable, ["-m", "coread_core.mcp.server"], settings.data_root, old_only=True)) == expected


def test_console_entries_delegate_to_single_implementation():
    distribution = importlib.metadata.distribution("caeliae-read")
    entries = {entry.name: entry for entry in distribution.entry_points}
    assert entries["caeliae-read-api"].load() is entries["coread-api"].load()
    assert entries["caeliae-read-mcp"].load() is entries["coread-mcp"].load()


@pytest.mark.parametrize("module", ["caeliae_read.mcp.server", "coread_core.mcp.server"])
def test_conflicting_data_roots_fail_before_creating_storage(tmp_path, module):
    env = os.environ.copy()
    new, old = tmp_path / "new", tmp_path / "old"
    env.update(CAELIAE_READ_DATA_ROOT=str(new), COREAD_DATA_ROOT=str(old))
    result = subprocess.run([sys.executable, "-m", module], env=env, capture_output=True, timeout=15)
    assert result.returncode != 0 and result.stdout == b""
    assert b"configuration conflict" in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert not new.exists() and not old.exists()


def test_conflicting_data_roots_fail_during_api_startup(tmp_path):
    env = os.environ.copy()
    new, old = tmp_path / "new", tmp_path / "old"
    env.update(CAELIAE_READ_DATA_ROOT=str(new), COREAD_DATA_ROOT=str(old))
    code = (
        "from fastapi.testclient import TestClient; "
        "from caeliae_read.api.app import app; "
        "client = TestClient(app); client.__enter__()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "configuration conflict" in result.stderr
    assert str(tmp_path) not in result.stderr
    assert not new.exists() and not old.exists()


@pytest.mark.parametrize("entry", ["caeliae-read-api", "coread-api"])
def test_api_console_entry_starts_isolated_service(tmp_path, entry):
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    env = os.environ.copy()
    env.update(CAELIAE_READ_DATA_ROOT=str(tmp_path), CAELIAE_READ_API_PORT=str(port))
    child = subprocess.Popen([str(Path(sys.executable).parent / f"{entry}.exe")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert child.poll() is None
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    assert json.load(response)["service"] == "caeliae-read"
                break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail("isolated API did not become ready")
        assert (tmp_path / "coread.sqlite3").exists()
        assert not (tmp_path / "caeliae.sqlite3").exists()
    finally:
        child.terminate()
        child.wait(timeout=15)
