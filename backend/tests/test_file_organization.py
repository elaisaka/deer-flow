"""Windows file execution uses temporary roots; confirmation is a separate authority."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.local_file_service.organization import Organizer
from services.local_file_service.policy import FilePolicy, PolicyError, Settings
from services.local_file_service.server import create_app

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native file-handle execution")
TOKEN = "test-token-" + "a" * 40
APPROVAL = "approval-" + "b" * 40


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for name in ("a.txt", "b.md", "c.pdf"):
        (root / name).write_bytes(b"synthetic test data: " + name.encode())
    settings = Settings({"study": root}, TOKEN, approval_token=APPROVAL, state_dir=tmp_path / "private")
    return root, settings, Organizer(FilePolicy(settings), settings.state_dir)


def preview(engine, **changes):
    return engine.preview(
        {
            "root_id": "study",
            "directory": "",
            "files": ["a.txt", "b.md", "c.pdf"],
            "rule": {
                "kind": "classify",
                "mapping": {".txt": "Text", ".md": "Notes", ".pdf": "PDF"},
            },
            **changes,
        }
    )


def confirm(engine, plan):
    return engine.confirm(plan["plan_id"], plan["version"], plan["digest"], "test-user")


def test_preview_confirm_execute_restart_undo(setup):
    root, settings, engine = setup
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    plan = preview(engine)
    assert plan["status"] == "awaiting_confirmation"
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    with pytest.raises(PolicyError, match="confirmation_required"):
        engine.execute(plan["plan_id"], 1)
    confirm(engine, plan)
    done = engine.execute(plan["plan_id"], 1)
    assert done["status"] == "completed" and all(i["state"] == "succeeded" for i in done["items"])
    assert (root / "Text/a.txt").read_bytes() == before["a.txt"]
    restarted = Organizer(FilePolicy(settings), settings.state_dir)
    assert restarted.get(plan["plan_id"])["status"] == "completed"
    assert restarted.execute(plan["plan_id"], 1)["status"] == "completed"
    assert restarted.undo(plan["plan_id"])["status"] == "undone"
    assert restarted.undo(plan["plan_id"])["status"] == "undone"
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_rename_preserves_extensions(setup):
    root, _, engine = setup
    plan = preview(
        engine,
        rule={
            "kind": "rename",
            "prefix": "study-",
            "suffix": "-note",
            "numbering": {"start": 1, "width": 2},
        },
    )
    confirm(engine, plan)
    result = engine.execute(plan["plan_id"], 1)
    assert result["status"] == "completed"
    assert {p.name for p in root.iterdir()} == {
        "study-01-a-note.txt",
        "study-02-b-note.md",
        "study-03-c-note.pdf",
    }
    assert engine.undo(plan["plan_id"])["status"] == "undone"


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "rename", "prefix": "../"},
        {"kind": "rename", "prefix": "NUL", "omit_stem": True},
        {"kind": "classify", "mapping": {".txt": "../outside"}},
        {"kind": "classify", "mapping": {".txt": "C:\\outside"}},
    ],
)
def test_illegal_targets_report_conflicts(setup, rule):
    _, _, engine = setup
    plan = preview(engine, rule=rule)
    assert any(item["state"] == "conflict" for item in plan["items"])
    with pytest.raises(PolicyError, match="plan_conflicts"):
        confirm(engine, plan)


def test_case_conflict_no_overwrite(setup):
    root, _, engine = setup
    (root / "TEXT").mkdir()
    (root / "TEXT/a.txt").write_bytes(b"preserve conflict")
    plan = preview(engine)
    assert plan["items"][0]["state"] == "conflict"
    with pytest.raises(PolicyError):
        confirm(engine, plan)
    assert (root / "TEXT/a.txt").read_bytes() == b"preserve conflict"


@pytest.mark.parametrize("after_confirmation", [False, True])
def test_source_changed(setup, after_confirmation):
    root, _, engine = setup
    plan = preview(engine)
    if after_confirmation:
        confirm(engine, plan)
    (root / "a.txt").write_bytes(b"changed")
    if after_confirmation:
        result = engine.execute(plan["plan_id"], 1)
        assert result["status"] == "failed"
    else:
        with pytest.raises(PolicyError, match="source_changed"):
            confirm(engine, plan)
    assert (root / "a.txt").is_file() and not (root / "Text").exists()


def test_revision_invalidates_confirmation(setup):
    _, _, engine = setup
    plan = preview(engine)
    confirm(engine, plan)
    revised = preview(
        engine,
        plan_id=plan["plan_id"],
        expected_version=1,
        rule={"kind": "rename", "prefix": "new-"},
    )
    assert revised["version"] == 2 and revised["confirmation"] is None
    with pytest.raises(PolicyError):
        engine.execute(plan["plan_id"], 1)
    with pytest.raises(PolicyError, match="confirmation_required"):
        engine.execute(plan["plan_id"], 2)
    with pytest.raises(PolicyError):
        confirm(engine, plan)


def test_partial_failure_and_undo(setup, monkeypatch):
    root, _, engine = setup
    plan = preview(engine)
    confirm(engine, plan)
    original = engine.rename
    count = 0

    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic failure")
        return original(*args)

    monkeypatch.setattr(engine, "rename", fail_second)
    result = engine.execute(plan["plan_id"], 1)
    assert result["status"] == "partial"
    assert [i["state"] for i in result["items"]] == ["succeeded", "failed", "skipped"]
    assert engine.execute(plan["plan_id"], 1)["status"] == "partial"
    monkeypatch.setattr(engine, "rename", original)
    assert engine.undo(plan["plan_id"])["status"] == "undone"
    assert {p.name for p in root.iterdir()} == {"a.txt", "b.md", "c.pdf"}


def test_crash_after_rename_reconciles_on_restart(setup, monkeypatch):
    root, settings, engine = setup
    plan = preview(engine)
    confirm(engine, plan)
    original = engine.rename

    def crash(*args):
        original(*args)
        raise SystemExit("simulated process crash")

    monkeypatch.setattr(engine, "rename", crash)
    with pytest.raises(SystemExit):
        engine.execute(plan["plan_id"], 1)
    restarted = Organizer(FilePolicy(settings), settings.state_dir)
    recovered = restarted.get(plan["plan_id"])
    assert recovered["status"] == "partial" and recovered["items"][0]["state"] == "succeeded"
    assert restarted.undo(plan["plan_id"])["status"] == "undone"
    assert (root / "a.txt").is_file()


@pytest.mark.parametrize("change", ["content", "replacement", "conflict", "moved"])
def test_undo_rejects_changed_file(setup, change):
    root, _, engine = setup
    plan = preview(engine, files=["a.txt"])
    confirm(engine, plan)
    engine.execute(plan["plan_id"], 1)
    target = root / "Text/a.txt"
    if change == "content":
        target.write_bytes(b"modified")
    elif change == "replacement":
        target.rename(root / "original.txt")
        target.write_bytes(b"replacement")
    elif change == "conflict":
        (root / "a.txt").write_bytes(b"original path occupied")
    else:
        target.rename(root / "elsewhere.txt")
    result = engine.undo(plan["plan_id"])
    assert result["status"] == "partial_undo"
    assert result["items"][0]["undo_error"]


def test_concurrent_same_plan(setup, monkeypatch):
    _, settings, engine = setup
    other_instance = Organizer(FilePolicy(settings), settings.state_dir)
    plan = preview(engine)
    confirm(engine, plan)
    entered, release = threading.Event(), threading.Event()
    original = engine.rename

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(engine, "rename", pause)
    results = []
    worker = threading.Thread(target=lambda: results.append(engine.execute(plan["plan_id"], 1)))
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(PolicyError, match="plan_busy"):
            other_instance.execute(plan["plan_id"], 1)
    finally:
        release.set()
        worker.join(10)
    assert results[0]["status"] == "completed"


def test_http_confirmation_separate_authority(setup):
    _, settings, _ = setup
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/organization/preview", json={}).status_code == 401
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        plan = client.post(
            "/v1/organization/preview",
            json={
                "root_id": "study",
                "directory": "",
                "files": ["a.txt"],
                "rule": {"kind": "rename", "prefix": "x-"},
            },
        ).json()
        payload = {
            "plan_id": plan["plan_id"],
            "version": 1,
            "digest": plan["digest"],
            "actor": "test-user",
        }
        assert client.post("/v1/organization/confirm", json=payload).status_code == 403
        rejected = client.post(
            "/v1/organization/execute",
            json={"plan_id": plan["plan_id"], "version": 1, "confirmed": True},
        )
        assert rejected.status_code == 400
        client.headers["X-File-Approval-Token"] = APPROVAL
        assert client.post("/v1/organization/confirm", json=payload).json()["status"] == "confirmed"
        assert (
            client.post(
                "/v1/organization/execute",
                json={"plan_id": plan["plan_id"], "version": 1},
            ).json()["status"]
            == "completed"
        )


@pytest.mark.parametrize("when", ["preview", "execute", "undo"])
def test_real_junction_cannot_redirect(setup, tmp_path, when):
    root, _, engine = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"outside must stay")
    plan = None
    if when != "preview":
        plan = preview(engine, files=["a.txt"])
        confirm(engine, plan)
    if when == "undo":
        assert engine.execute(plan["plan_id"], 1)["status"] == "completed"
        (root / "Text").rename(root / "OriginalText")
    junction = root / "Text"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=True,
    )
    try:
        if when == "preview":
            assert preview(engine, files=["a.txt"])["items"][0]["error"] == "reparse_point"
        elif when == "execute":
            assert engine.execute(plan["plan_id"], 1)["status"] == "failed"
            assert (root / "a.txt").is_file()
        else:
            assert engine.undo(plan["plan_id"])["status"] == "partial_undo"
        assert (outside / "a.txt").read_bytes() == b"outside must stay"
    finally:
        junction.rmdir()  # Remove the junction itself, never recursively its target.


def test_real_symlink_source_rejected(setup, tmp_path):
    root, _, engine = setup
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"untouched")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlink creation unavailable: winerror={error.winerror}")
    plan = preview(engine, files=["link.txt"])
    assert plan["items"][0]["error"] == "reparse_point"
    assert outside.read_bytes() == b"untouched"


@pytest.mark.parametrize("change", ["missing", "same_metadata", "new_target"])
def test_recheck_before_execution(setup, change):
    root, _, engine = setup
    plan = preview(engine, files=["a.txt"])
    confirm(engine, plan)
    source = root / "a.txt"
    if change == "missing":
        source.unlink()
    elif change == "same_metadata":
        previous = source.stat()
        source.write_bytes(b"x" * previous.st_size)
        os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    else:
        (root / "Text").mkdir()
        (root / "Text/a.txt").write_bytes(b"do not overwrite")
    result = engine.execute(plan["plan_id"], 1)
    assert result["status"] == "failed" and result["items"][0]["state"] == "skipped"
    if change == "new_target":
        assert (root / "Text/a.txt").read_bytes() == b"do not overwrite"


def test_cancelled_plan_and_duplicate_destinations(setup):
    _, _, engine = setup
    plan = preview(engine)
    confirm(engine, plan)
    assert engine.cancel(plan["plan_id"], 1)["status"] == "cancelled"
    with pytest.raises(PolicyError, match="confirmation_required"):
        engine.execute(plan["plan_id"], 1)
    duplicate = preview(
        engine,
        files=["a.txt", "other.txt"],
        rule={"kind": "rename", "prefix": "shared", "omit_stem": True},
    )
    # Missing source is represented as a conflict, never silently omitted.
    assert duplicate["items"][1]["error"] == "source_missing"


def test_duplicate_target_case_only_and_source_occupancy(setup):
    root, _, engine = setup
    (root / "other.txt").write_bytes(b"other")
    duplicate = preview(
        engine,
        files=["a.txt", "other.txt"],
        rule={"kind": "rename", "prefix": "shared", "omit_stem": True},
    )
    assert all(i["error"] == "duplicate_target" for i in duplicate["items"])
    occupied = preview(
        engine,
        files=["a.txt"],
        rule={"kind": "rename", "prefix": "A", "omit_stem": True},
    )
    assert occupied["items"][0]["state"] == "conflict"


def test_hardlink_rejected_and_private_data_protected(setup):
    root, _, engine = setup
    os.link(root / "a.txt", root / "hard.txt")
    assert preview(engine, files=["hard.txt"])["items"][0]["error"] == "hardlinks_not_supported"
    (root / ".local-file-service").mkdir()
    (root / ".local-file-service/private.json").write_bytes(b"credential placeholder")
    protected = preview(engine, directory=".local-file-service", files=["private.json"])
    assert protected["items"][0]["error"] == "service_data_protected"


def test_after_syscall_error_keeps_success_evidence(setup, monkeypatch):
    root, _, engine = setup
    plan = preview(engine)
    confirm(engine, plan)
    original = engine.rename

    def moved_then_error(*args):
        original(*args)
        raise OSError("after successful syscall")

    monkeypatch.setattr(engine, "rename", moved_then_error)
    result = engine.execute(plan["plan_id"], 1)
    assert result["status"] == "partial" and result["items"][0]["state"] == "succeeded"
    assert engine.undo(plan["plan_id"])["status"] == "undone"
    assert (root / "a.txt").is_file()


def test_directory_cleanup_only_owned_empty(setup):
    root, _, engine = setup
    (root / "Text").mkdir()
    plan = preview(engine)
    confirm(engine, plan)
    engine.execute(plan["plan_id"], 1)
    (root / "Notes/unrelated.txt").write_bytes(b"keep")
    result = engine.undo(plan["plan_id"])
    assert result["status"] == "undone"
    assert (root / "Text").is_dir()  # Preexisting directories are never owned.
    assert (root / "Notes/unrelated.txt").read_bytes() == b"keep"
    assert not (root / "PDF").exists()


def test_terminal_retry_verifies_actual_file_state(setup):
    root, _, engine = setup
    plan = preview(engine, files=["a.txt"])
    confirm(engine, plan)
    assert engine.execute(plan["plan_id"], 1)["verified"] is True
    (root / "Text/a.txt").write_bytes(b"later changed")
    repeated = engine.execute(plan["plan_id"], 1)
    assert repeated["status"] == "completed" and repeated["verified"] is False
    assert repeated["items"][0]["actual_error"] == "record_file_changed"


@pytest.mark.parametrize("inside_native", [False, True])
def test_inflight_destination_junction_does_not_escape(setup, tmp_path, monkeypatch, inside_native):
    """Inject a reparse point after parent checks, while its handle is held."""
    import ctypes
    import struct
    from ctypes import wintypes

    from services.local_file_service.policy import kernel

    root, _, engine = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    plan = preview(engine, files=["a.txt"])
    confirm(engine, plan)
    original = engine.rename

    def insert_junction(handle, target):
        write = kernel.CreateFileW(str(target.parent), 0x40000000, 3, None, 3, 0x02200000, None)
        sub = ("\\??\\" + str(outside)).encode("utf-16-le")
        printable = str(outside).encode("utf-16-le")
        data = struct.pack("<HHHH", 0, len(sub), len(sub) + 2, len(printable)) + sub + b"\0\0" + printable + b"\0\0"
        buffer = ctypes.create_string_buffer(struct.pack("<IHH", 0xA0000003, len(data), 0) + data)
        received = wintypes.DWORD()
        kernel.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel.DeviceIoControl.restype = wintypes.BOOL
        try:
            assert kernel.DeviceIoControl(
                write,
                0x900A4,
                buffer,
                len(buffer.raw) - 1,
                None,
                0,
                ctypes.byref(received),
                None,
            )
        finally:
            kernel.CloseHandle(write)
        if not inside_native:
            return original(handle, target)

    if inside_native:
        from services.local_file_service.windows_files import native

        original_native = native.NtSetInformationFile

        def inject_at_syscall(*args):
            insert_junction(None, root / "Text/a.txt")
            return original_native(*args)

        monkeypatch.setattr(native, "NtSetInformationFile", inject_at_syscall)
    else:
        monkeypatch.setattr(engine, "rename", insert_junction)
    try:
        result = engine.execute(plan["plan_id"], 1)
        assert result["status"] == "failed"
        assert list(outside.iterdir()) == [] and (root / "a.txt").is_file()
    finally:
        (root / "Text").rmdir()


def test_unicode_native_rename_and_undo(setup):
    root, _, engine = setup
    plan = preview(engine, files=["a.txt"], rule={"kind": "rename", "prefix": "学习🌿📖-"})
    confirm(engine, plan)
    assert engine.execute(plan["plan_id"], 1)["status"] == "completed"
    assert (root / "学习🌿📖-a.txt").is_file()
    assert engine.undo(plan["plan_id"])["status"] == "undone"


def test_root_identity_changes_after_restart(setup):
    root, settings, engine = setup
    plan = preview(engine)
    confirm(engine, plan)
    root.rename(root.with_name("original-root"))
    root.mkdir()
    for name in ("a.txt", "b.md", "c.pdf"):
        (root / name).write_bytes(b"replacement")
    restarted = Organizer(FilePolicy(settings), settings.state_dir)
    result = restarted.execute(plan["plan_id"], 1)
    assert result["status"] == "failed"
    assert all(i["error"] == "plan_root_changed" for i in result["items"])


def test_native_target_race_never_overwrites(setup, monkeypatch):
    root, _, engine = setup
    plan = preview(engine, files=["a.txt"])
    confirm(engine, plan)
    original = engine.rename

    def occupied(handle, target):
        target.write_bytes(b"race winner must stay")
        return original(handle, target)

    monkeypatch.setattr(engine, "rename", occupied)
    result = engine.execute(plan["plan_id"], 1)
    assert result["status"] == "failed" and result["items"][0]["error"] == "target_conflict"
    assert (root / "a.txt").is_file() and (root / "Text/a.txt").read_bytes() == b"race winner must stay"


def test_private_configuration_and_gateway_credential_separation(setup, tmp_path):
    root, _, _ = setup
    original = tmp_path / "phase-one.json"
    original.write_text(json.dumps({"token": TOKEN, "roots": {"study": str(root)}}), encoding="utf-8")
    before = original.read_bytes()
    output = tmp_path / "authority"
    result = subprocess.run(
        [sys.executable, "-m", "services.local_file_service.configure_organization", "--service-config", str(original), "--output", str(output)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    )
    settings = Settings.load(output / "service-config.json")
    assert settings.approval_token != TOKEN and len(settings.approval_token) >= 32
    client = json.loads((output / "approval-client.json").read_text())
    assert set(client) == {"url", "token"} and client["token"] == TOKEN
    assert original.read_bytes() == before
    assert TOKEN not in result.stdout and settings.approval_token not in result.stdout
    repeated = subprocess.run(result.args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert repeated.returncode == 1 and Settings.load(output / "service-config.json").approval_token == settings.approval_token


@pytest.mark.parametrize("unsafe", ["config_in_root", "ledger_in_root", "placeholder"])
def test_unsafe_confirmation_configuration_rejected(setup, tmp_path, unsafe):
    root, _, _ = setup
    path = root / "config.json" if unsafe == "config_in_root" else tmp_path / "config.json"
    data = {
        "token": TOKEN,
        "roots": {"study": str(root)},
        "organization": {"approval_token": "REPLACE_WITH_DISTINCT_RANDOM_APPROVAL_TOKEN" if unsafe == "placeholder" else APPROVAL, "state_dir": str(root / "ledger" if unsafe == "ledger_in_root" else tmp_path / "ledger")},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load(path)
