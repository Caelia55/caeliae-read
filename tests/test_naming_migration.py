from __future__ import annotations

import asyncio
import importlib.metadata
import os
from pathlib import Path
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


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in NAMES:
        for prefix in ("CAELIAE_READ_", "COREAD_"):
            monkeypatch.delenv(prefix + name, raising=False)
    config._reported.clear()


def test_defaults_preserve_data_filename():
    settings = config.get_settings()
    assert settings.database_path == config.PROJECT_ROOT / "data" / "coread.sqlite3"
    assert settings.api_port == 8765


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
    root = Path(__file__).resolve().parents[1]
    entries = [
        (sys.executable, ["-m", "caeliae_read.mcp.server"]),
        (sys.executable, ["-m", "coread_core.mcp.server"]),
        (str(scripts / "caeliae-read-mcp.exe"), []),
        (str(scripts / "coread-mcp.exe"), []),
        ("cmd.exe", ["/d", "/c", str(root / "start-caeliae-read-mcp.cmd")]),
        ("cmd.exe", ["/d", "/c", str(root / "start-coread-mcp.cmd")]),
    ]
    for command, args in entries:
        assert asyncio.run(inspect_entry(command, args, settings.data_root)) == expected
    assert asyncio.run(inspect_entry(sys.executable, ["-m", "coread_core.mcp.server"], settings.data_root, old_only=True)) == expected


def test_console_entries_delegate_to_single_implementation():
    distribution = importlib.metadata.distribution("caeliae-read")
    entries = {entry.name: entry for entry in distribution.entry_points}
    assert entries["caeliae-read-api"].load() is entries["coread-api"].load()
    assert entries["caeliae-read-mcp"].load() is entries["coread-mcp"].load()


@pytest.mark.parametrize("module", ["caeliae_read.mcp.server", "coread_core.mcp.server", "caeliae_read.api.app"])
def test_conflicting_data_roots_fail_before_creating_storage(tmp_path, module):
    env = os.environ.copy()
    new, old = tmp_path / "new", tmp_path / "old"
    env.update(CAELIAE_READ_DATA_ROOT=str(new), COREAD_DATA_ROOT=str(old))
    result = subprocess.run([sys.executable, "-m", module], env=env, capture_output=True, timeout=15)
    assert result.returncode != 0 and result.stdout == b""
    assert b"configuration conflict" in result.stderr
    assert str(tmp_path).encode() not in result.stderr
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
