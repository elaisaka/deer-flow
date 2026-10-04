"""The Windows execution boundary. Reject links and hold ancestors against replacement."""

from __future__ import annotations

import ctypes
import json
import os
import re
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


class PolicyError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Settings:
    roots: dict[str, Path]
    token: str
    host: str = "127.0.0.1"
    port: int = 8765
    approval_token: str = ""
    state_dir: Path | None = None
    private_paths: tuple[Path, ...] = ()

    @classmethod
    def load(cls, path: Path) -> Settings:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object")
        token = data.get("token", "")
        if not isinstance(token, str) or token.startswith("REPLACE_") or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("Configure a random token of at least 32 URL-safe characters")
        roots = data.get("roots")
        if not isinstance(roots, dict) or not 1 <= len(roots) <= 20:
            raise ValueError("Configure between 1 and 20 authorized roots")
        parsed = {}
        for name, raw in roots.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name) or not isinstance(raw, str):
                raise ValueError("Invalid root configuration")
            win = PureWindowsPath(raw)
            if not Path(raw).is_absolute() or (os.name == "nt" and (not win.drive or win.drive.startswith("\\"))):
                raise ValueError("Roots must be absolute local paths, not network shares")
            parsed[name] = Path(raw)
        host, port = data.get("host", "127.0.0.1"), data.get("port", 8765)
        if host not in {"127.0.0.1", "0.0.0.0"} or type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid listen address/port")
        organization = data.get("organization", {})
        if not isinstance(organization, dict):
            raise ValueError("Invalid organization settings")
        approval = organization.get("approval_token", "")
        if approval and (not isinstance(approval, str) or approval.startswith("REPLACE_") or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", approval) or approval == token):
            raise ValueError("Approval requires a separate random token")
        state_dir = Path(organization.get("state_dir", str(path.absolute().parent / "organization")))
        if not state_dir.is_absolute():
            raise ValueError("Organization state directory must be absolute")
        if approval and any(path.absolute().is_relative_to(root) or state_dir.is_relative_to(root) for root in parsed.values()):
            raise ValueError("Human authority and execution ledger must be outside all authorized roots")
        return cls(parsed, token, host, port, approval, state_dir, (path.absolute().parent,))


if os.name == "nt":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


@contextmanager
def locked_directory(path: Path, *, allow_file_moves=False):
    if os.name != "nt":
        raise PolicyError("windows_required")
    # OPEN_REPARSE_POINT: inspect the link itself. BACKUP_SEMANTICS: open directories.
    # Share read only: prevent rename/delete AND writable reparse-point handles.
    # FILE_LIST_DIRECTORY participates in Windows sharing checks; attributes-only
    # handles do not protect against rename even with FILE_SHARE_DELETE omitted.
    # Organization alone uses write sharing with handle-relative native renames.
    # DELETE sharing stays disabled. Full-path mutations must retain the default.
    handle = kernel.CreateFileW(str(path), 0x81, 3 if allow_file_moves else 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise PolicyError({2: "not_found", 3: "not_found", 32: "directory_busy"}.get(error, "access_denied"))
    try:
        info = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise PolicyError("verification_failed")
        if info.attributes & 0x400:
            raise PolicyError("reparse_point")
        if not info.attributes & 0x10:
            raise PolicyError("file_conflict")
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not size or size >= len(buffer):
            raise PolicyError("verification_failed")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            raise PolicyError("network_path_denied")
        actual = Path(value.removeprefix("\\\\?\\"))
        yield actual
    finally:
        kernel.CloseHandle(handle)


def relative_parts(value: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if value == "" and allow_empty:
        return ()
    if not isinstance(value, str) or len(value) > 1000:
        raise PolicyError("invalid_path")
    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.root:
        raise PolicyError("absolute_path_denied")
    parts = value.replace("\\", "/").split("/")
    for part in parts:
        if not part or part in {".", ".."} or part.endswith((".", " ")) or len(part) > 200 or any(ord(c) < 32 or c in '<>:"|?*' for c in part) or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", part, re.I):
            raise PolicyError("invalid_path")
    return tuple(parts)


class FilePolicy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.identities = {}
        # Bind root identity at startup; later substitution by an ordinary directory also fails.
        for name in settings.roots:
            with self._directory(name, ()) as path:
                self.identities[name] = path.stat().st_ino

    @contextmanager
    def _directory(self, root_id: str, parts: tuple[str, ...], *, allow_file_moves=False):
        if root_id not in self.settings.roots:
            raise PolicyError("unauthorized_root")
        root = self.settings.roots[root_id]
        with ExitStack() as stack:
            # Walk from the drive root, preventing an authorized path's ancestor from redirecting it.
            current = Path(root.anchor)
            actual = stack.enter_context(locked_directory(current, allow_file_moves=allow_file_moves))
            for part in root.parts[1:]:
                current /= part
                actual = stack.enter_context(locked_directory(current, allow_file_moves=allow_file_moves))
            if actual != root:
                raise PolicyError("root_path_changed")
            if root_id in self.identities and actual.stat().st_ino != self.identities[root_id]:
                raise PolicyError("root_identity_changed")
            for part in parts:
                current /= part
                actual = stack.enter_context(locked_directory(current, allow_file_moves=allow_file_moves))
                if not actual.is_relative_to(root):
                    raise PolicyError("outside_authorized_root")
            yield actual

    def roots(self) -> dict:
        roots = []
        for name in self.settings.roots:
            with self._directory(name, ()) as path:
                roots.append({"root_id": name, "actual_path": str(path), "verified": True})
        return {
            "ok": True,
            "execution_host": "windows",
            "roots": roots,
            "knowledge_import": {"formats": [".pdf", ".md", ".txt"], "max_bytes": 10 * 1024 * 1024, "selected_files_only": True},
            "capabilities": ["list_directory", "create_folder"]
            + (
                [
                    "organization_preview",
                    "organization_execute_after_human_confirmation",
                    "organization_records",
                    "organization_undo",
                ]
                if self.settings.approval_token
                else []
            ),
        }

    def list_directory(self, root_id: str, relative_path: str = "") -> dict:
        with self._directory(root_id, relative_parts(relative_path, allow_empty=True)) as path:
            entries = []
            with os.scandir(path) as iterator:
                for entry in iterator:
                    if len(entries) == 1000:
                        raise PolicyError("listing_limit_exceeded")
                    stat = entry.stat(follow_symlinks=False)
                    blocked = bool(getattr(stat, "st_file_attributes", 0) & 0x400)
                    kind = "link_blocked" if blocked else "directory" if entry.is_dir(follow_symlinks=False) else "file"
                    entries.append({"name": entry.name, "kind": kind})
            return {
                "ok": True,
                "actual_path": str(path),
                "entries": sorted(entries, key=lambda e: e["name"].casefold()),
                "verified": True,
            }

    def create_folder(self, root_id: str, relative_path: str) -> dict:
        parts = relative_parts(relative_path)
        with self._directory(root_id, parts[:-1]) as parent:
            target = parent / parts[-1]
            try:
                target.mkdir()  # Single level only; never overwrite and never create missing ancestors.
                status = "created"
            except FileExistsError:
                status = "already_exists"
            with locked_directory(target) as actual:
                if not actual.is_relative_to(self.settings.roots[root_id]) or not actual.is_dir():
                    raise PolicyError("verification_failed")
                return {
                    "ok": True,
                    "status": status,
                    "root_id": root_id,
                    "actual_path": str(actual),
                    "verified": True,
                    "execution_host": "windows",
                }
