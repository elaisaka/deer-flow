"""Exclusive native file handles, no-replace rename, and identity/content snapshots."""

import ctypes
import hashlib
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from .policy import PolicyError, locked_directory

MAX_FILE_BYTES = 256 * 1024 * 1024

if os.name == "nt":
    from .policy import AttributeTag, kernel

    class FileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("modified", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_hi", wintypes.DWORD),
            ("size_lo", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("id_hi", wintypes.DWORD),
            ("id_lo", wintypes.DWORD),
        ]

    kernel.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInfo),
    ]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetFilePointerEx.restype = wintypes.BOOL

    native = ctypes.WinDLL("ntdll")
    native.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    native.NtSetInformationFile.restype = ctypes.c_long

    class IoStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]


def actual_path(handle) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not size or size >= len(buffer):
        raise PolicyError("verification_failed")
    return Path(buffer.value.removeprefix("\\\\?\\"))


@contextmanager
def file_handle(path: Path, *, rename=False):
    if os.name != "nt":
        raise PolicyError("windows_required")
    access = 0x81 | (0x10000 if rename else 0)
    handle = kernel.CreateFileW(str(path), access, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise PolicyError({2: "source_missing", 3: "source_missing", 32: "file_busy"}.get(ctypes.get_last_error(), "file_access_denied"))
    try:
        tag = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            raise PolicyError("verification_failed")
        if tag.attributes & 0x400:
            raise PolicyError("reparse_point")
        if tag.attributes & 0x10:
            raise PolicyError("ordinary_files_only")
        yield handle
    finally:
        kernel.CloseHandle(handle)


def snapshot(handle) -> dict:
    info = FileInfo()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise PolicyError("verification_failed")
    size = info.size_hi << 32 | info.size_lo
    if info.links != 1:
        raise PolicyError("hardlinks_not_supported")
    if size > MAX_FILE_BYTES:
        raise PolicyError("file_size_limit")
    if not kernel.SetFilePointerEx(handle, 0, None, 0):
        raise PolicyError("verification_failed")
    digest = hashlib.sha256()
    buffer, read = ctypes.create_string_buffer(65536), wintypes.DWORD()
    while True:
        if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
            raise PolicyError("verification_failed")
        if not read.value:
            break
        digest.update(buffer.raw[: read.value])
    return {
        "volume": info.volume,
        "file_id": info.id_hi << 32 | info.id_lo,
        "size": size,
        "mtime": info.modified.dwHighDateTime << 32 | info.modified.dwLowDateTime,
        "created": info.created.dwHighDateTime << 32 | info.created.dwLowDateTime,
        "sha256": digest.hexdigest(),
    }


def rename_no_replace(handle, target: Path):
    # A relative kernel rename rooted at the opened directory does not traverse
    # a junction inserted after validation. A reparse directory fails closed.
    name = target.name
    encoded_length = len(name.encode("utf-16-le"))

    class RenameInfo(ctypes.Structure):
        _fields_ = [
            ("replace", wintypes.BOOLEAN),
            ("root", wintypes.HANDLE),
            ("length", wintypes.DWORD),
            ("name", wintypes.WCHAR * (encoded_length // 2 + 1)),
        ]

    with locked_directory(target.parent, allow_file_moves=True):
        parent = kernel.CreateFileW(str(target.parent), 0x81, 3, None, 3, 0x02200000, None)
        if parent == ctypes.c_void_p(-1).value:
            raise PolicyError("directory_busy")
        try:
            tag = AttributeTag()
            if not kernel.GetFileInformationByHandleEx(parent, 9, ctypes.byref(tag), ctypes.sizeof(tag)) or tag.attributes & 0x400 or actual_path(parent) != target.parent:
                raise PolicyError("reparse_point")
            value = RenameInfo(0, parent, encoded_length, name)
            status = (
                native.NtSetInformationFile(
                    handle,
                    ctypes.byref(IoStatus()),
                    ctypes.byref(value),
                    RenameInfo.name.offset + value.length,
                    10,
                )
                & 0xFFFFFFFF
            )
            if status:
                raise PolicyError(
                    {
                        0xC0000035: "target_conflict",
                        0xC00000D4: "cross_volume_denied",
                        0xC0000280: "reparse_point",
                    }.get(status, "rename_failed")
                )
        finally:
            kernel.CloseHandle(parent)
    if actual_path(handle) != target:
        raise PolicyError("verification_failed")


@contextmanager
def removable_directory(path: Path):
    """Protect a recorded directory, then remove only this same empty object."""
    handle = kernel.CreateFileW(str(path), 0x10081, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise PolicyError("directory_busy")
    try:
        tag = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)) or tag.attributes & 0x400 or not tag.attributes & 0x10:
            raise PolicyError("reparse_point")
        yield handle
    finally:
        kernel.CloseHandle(handle)


def remove_empty(handle, path: Path):
    with os.scandir(path) as contents:
        if next(contents, None) is not None:
            raise PolicyError("directory_not_empty")
    value = wintypes.BOOL(True)
    if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(value), ctypes.sizeof(value)):
        raise PolicyError("directory_remove_failed")
