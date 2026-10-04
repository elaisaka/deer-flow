"""Actual Windows bounded reads, native locks, reparse refusal and private-data isolation."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.local_file_service.imports import read_selected_file
from services.local_file_service.policy import FilePolicy, PolicyError, Settings
from services.local_file_service.server import create_app

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows execution boundary")
TOKEN, HUMAN = "op-" + "a" * 40, "human-" + "b" * 40


def policy(root, **kwargs):
    return FilePolicy(Settings({"study": root}, TOKEN, **kwargs))


@pytest.mark.parametrize("name", ["a.txt", "a.md", "a.pdf"])
def test_selected_supported_file_and_case(tmp_path, name):
    (tmp_path / name).write_bytes(b"synthetic")
    result = read_selected_file(policy(tmp_path), "study", name.upper())
    assert result["verified"] and base64.b64decode(result["content_base64"]) == b"synthetic"
    assert Path(result["actual_path"]) == tmp_path / name
    assert (tmp_path / name).read_bytes() == b"synthetic"


@pytest.mark.parametrize("path", ["../a.txt", "C:\\a.txt", "\\a.txt", "a.txt:stream", "folder./a.txt", "NUL.txt"])
def test_paths_denied(tmp_path, path):
    with pytest.raises(PolicyError):
        read_selected_file(policy(tmp_path), "study", path)


def test_private_unsupported_size_and_wrong_root(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "code.txt").write_bytes(b"synthetic secret")
    engine = policy(tmp_path, private_paths=(private,))
    with pytest.raises(PolicyError, match="private_service_data_denied"):
        read_selected_file(engine, "study", "private/code.txt")
    (tmp_path / "a.exe").write_bytes(b"MZ")
    with pytest.raises(PolicyError, match="unsupported_format"):
        read_selected_file(engine, "study", "a.exe")
    (tmp_path / "big.txt").write_bytes(b"a" * (10 * 1024 * 1024 + 1))
    with pytest.raises(PolicyError, match="file_size_limit"):
        read_selected_file(engine, "study", "big.txt")
    with pytest.raises(PolicyError, match="unauthorized_root"):
        read_selected_file(engine, "desktop", "a.txt")


def test_junction_and_symlink(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"outside")
    link = root / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(PolicyError, match="reparse_point"):
            read_selected_file(policy(root), "study", "junction/a.txt")
    finally:
        link.rmdir()
    assert (outside / "a.txt").read_bytes() == b"outside"


def test_real_symlink(tmp_path):
    source, link = tmp_path / "a.txt", tmp_path / "link.txt"
    source.write_bytes(b"synthetic")
    try:
        link.symlink_to(source)
    except OSError as error:
        pytest.skip(f"Actual symlink creation unavailable: WinError {getattr(error, 'winerror', None)}")
    with pytest.raises(PolicyError, match="reparse_point"):
        read_selected_file(policy(tmp_path), "study", "link.txt")


def test_changes_rejected_and_writers_blocked(tmp_path, monkeypatch):
    import services.local_file_service.imports as module

    source = tmp_path / "a.txt"
    source.write_bytes(b"synthetic")
    original = module.snapshot
    calls = []

    def snapshot(handle):
        with pytest.raises(OSError):
            source.write_bytes(b"unsafe concurrent writer")
        value = original(handle)
        calls.append(1)
        if len(calls) == 2:
            value["mtime"] += 1
        return value

    monkeypatch.setattr(module, "snapshot", snapshot)
    with pytest.raises(PolicyError, match="source_changed"):
        read_selected_file(policy(tmp_path), "study", "a.txt")
    assert source.read_bytes() == b"synthetic"


def test_http_auth_and_separate_confirmation(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"data")
    with TestClient(create_app(Settings({"study": tmp_path}, TOKEN, approval_token=HUMAN, state_dir=tmp_path.parent / "private-ledger"))) as client:
        data = {"root_id": "study", "relative_path": "a.txt"}
        assert client.post("/v1/import/read", json=data).status_code == 401
        assert client.post("/v1/import/read", json=data, headers={"Authorization": "Bearer wrong"}).status_code == 401
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        assert client.post("/v1/import/read", json=data).json()["verified"]
        body = {"digest": "a" * 64, "actor": "owner"}
        assert client.post("/v1/knowledge/confirm", json={**body, "confirmed": True}).status_code == 403
        assert client.post("/v1/knowledge/confirm", json=body, headers={"X-File-Approval-Token": TOKEN}).status_code == 403
        assert client.post("/v1/knowledge/confirm", json=body, headers={"X-File-Approval-Token": HUMAN}).json()["ok"]
