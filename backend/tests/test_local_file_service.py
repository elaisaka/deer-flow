"""Real Windows execution; portable HTTP/bridge contracts use no model or credentials."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.local_file_service.bridge import FileServiceClient, build_mcp
from services.local_file_service.policy import FilePolicy, PolicyError, Settings
from services.local_file_service.server import create_app

TOKEN = "test-token-" + "a" * 40
windows = pytest.mark.skipif(os.name != "nt", reason="Windows handle/reparse execution boundary")


def settings(root):
    return Settings({"study": root}, TOKEN)


@windows
def test_create_retry_and_conflict(tmp_path):
    policy = FilePolicy(settings(tmp_path))
    first = policy.create_folder("study", "test-folder")
    assert first["status"] == "created"
    assert first["verified"] and Path(first["actual_path"]).is_dir()
    assert policy.create_folder("study", "test-folder")["status"] == "already_exists"
    (tmp_path / "file").write_text("preserve", encoding="utf-8")
    with pytest.raises(PolicyError, match="file_conflict"):
        policy.create_folder("study", "file")
    assert (tmp_path / "file").read_text(encoding="utf-8") == "preserve"
    assert policy.roots()["roots"][0]["actual_path"] == str(tmp_path.resolve())
    assert {e["name"] for e in policy.list_directory("study")["entries"]} == {
        "file",
        "test-folder",
    }


@windows
@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "a/../../escape",
        "C:\\escape",
        "\\escape",
        "C:escape",
        "\\\\server\\share",
        "folder.",
        "folder ",
        "NUL",
        "COM1.txt",
        "a:stream",
        "a//b",
        "a/<b>",
    ],
)
def test_reject_paths(tmp_path, path):
    with pytest.raises(PolicyError):
        FilePolicy(settings(tmp_path)).create_folder("study", path)


@windows
def test_unknown_root_and_missing_parent(tmp_path):
    policy = FilePolicy(settings(tmp_path))
    with pytest.raises(PolicyError, match="unauthorized_root"):
        policy.create_folder("desktop", "x")
    with pytest.raises(PolicyError, match="not_found"):
        policy.create_folder("study", "missing/child")
    assert not (tmp_path / "missing").exists()


@windows
def test_junction_escape_and_root_replacement(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    policy = FilePolicy(settings(root))
    junction = root / "link"
    # Native junction creation requires no symlink privilege; all paths are temporary.
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        for action in (policy.create_folder, policy.list_directory):
            with pytest.raises(PolicyError, match="reparse_point"):
                action("study", "link/escape" if action == policy.create_folder else "link")
        assert policy.list_directory("study")["entries"][0]["kind"] == "link_blocked"
        assert not (outside / "escape").exists()
    finally:
        junction.rmdir()  # Remove the junction only, never recursively delete its target.
    root.rmdir()
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(PolicyError, match="reparse_point"):
            policy.create_folder("study", "escape")
    finally:
        root.rmdir()


@windows
def test_directory_handles_prevent_rename(tmp_path):
    from services.local_file_service.policy import locked_directory

    root = tmp_path / "root"
    root.mkdir()
    with locked_directory(root):
        with pytest.raises(OSError):
            root.rename(tmp_path / "replaced")


def test_config_fail_closed(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"roots": {"study": str(tmp_path)}, "token": "short"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        Settings.load(config)
    config.write_text(json.dumps({"roots": {"study": "relative"}, "token": TOKEN}), encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load(config)


@windows
def test_http_auth_finite_routes_and_real_creation(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        payload = {"root_id": "study", "relative_path": "real"}
        for headers in (
            {},
            {"Authorization": "Bearer wrong"},
            {"Authorization": f"Bearer {TOKEN}", "Origin": "https://evil.example"},
        ):
            assert client.post("/v1/create-folder", json=payload, headers=headers).status_code in (401, 403)
        assert not (tmp_path / "real").exists()
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        result = client.post("/v1/create-folder", json=payload)
        assert result.status_code == 200 and result.json()["verified"]
        assert (tmp_path / "real").is_dir()
        assert client.post("/v1/shell", json={"command": "whoami"}).status_code == 404
        assert client.post("/v1/create-folder", json={**payload, "extra": "x"}).status_code == 400
        denied = client.post("/v1/create-folder", json={**payload, "relative_path": "../escape"})
        assert denied.status_code == 400 and not denied.json()["ok"]


def test_bridge_unavailable_and_bad_auth():
    async def unavailable(request):
        raise httpx.ConnectError("synthetic offline", request=request)

    bridge = FileServiceClient(
        "http://host.docker.internal:8765",
        TOKEN,
        transport=httpx.MockTransport(unavailable),
    )
    result = asyncio.run(bridge.call("create-folder", {"root_id": "study", "relative_path": "x"}))
    assert result["error"]["code"] == "service_unavailable" and not result["ok"]
    bridge = FileServiceClient(
        "http://host.docker.internal:8765",
        TOKEN,
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    assert asyncio.run(bridge.call("roots"))["error"]["code"] == "authentication_failed"


def test_bridge_discovery_offline():
    server = build_mcp(FileServiceClient("http://host.docker.internal:8765", TOKEN))
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {
        "local_files_get_roots",
        "local_files_list_directory",
        "local_files_create_folder",
    } <= names
    assert len(names) == 9 and not any("confirm" in name for name in names)
    assert all("Windows" in t.description for t in tools)


@windows
def test_symbolic_link_escape(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "root"
    outside.mkdir()
    root.mkdir()
    link = root / "symbolic"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege/developer mode unavailable; real junction test still runs")
    try:
        with pytest.raises(PolicyError, match="reparse_point"):
            FilePolicy(settings(root)).create_folder("study", "symbolic/escape")
        assert not (outside / "escape").exists()
    finally:
        link.unlink()


@windows
def test_root_identity_change(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    policy = FilePolicy(settings(root))
    root.rename(tmp_path / "original")
    root.mkdir()
    with pytest.raises(PolicyError, match="root_identity_changed"):
        policy.create_folder("study", "escape")


@pytest.mark.parametrize("operation,arguments", [("roots", None), ("organization/get", {"plan_id": "a" * 32})])
def test_bridge_connection_refused(operation, arguments):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        result = asyncio.run(FileServiceClient(f"http://127.0.0.1:{port}", TOKEN).call(operation, arguments))
    assert not result["ok"] and result["error"]["code"] == "service_unavailable"


def test_private_mcp_entry_matches_overlay(tmp_path):
    from services.local_file_service.configure import mcp_entry

    entry = mcp_entry(settings(tmp_path))
    assert entry["type"] == "stdio"
    assert entry["command"] == "/app/backend/.venv/bin/python"
    assert entry["env"]["LOCAL_FILE_SERVICE_TOKEN"] == TOKEN
    assert entry["env"]["LOCAL_FILE_SERVICE_URL"] == "http://host.docker.internal:8765"
    root = Path(__file__).resolve().parents[2]
    assert "/opt/deerflow-local-files/services:ro" in (root / "docker/docker-compose.local-files.yaml").read_text(encoding="utf-8")
