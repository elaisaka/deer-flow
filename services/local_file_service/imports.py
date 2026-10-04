"""Finite, bounded transfer of a selected supported ordinary file, never arbitrary download."""

import base64
import ctypes
import hashlib
from ctypes import wintypes
from pathlib import Path

from .policy import PolicyError, relative_parts
from .windows_files import actual_path, file_handle, snapshot

MAX_IMPORT_BYTES = 10 * 1024 * 1024


def read_selected_file(policy, root_id, relative_path):
    parts = relative_parts(relative_path)
    if Path(parts[-1]).suffix.lower() not in {".pdf", ".md", ".txt"}:
        raise PolicyError("unsupported_format")
    with policy._directory(root_id, parts[:-1]) as parent:
        target = parent / parts[-1]
        excluded = [Path(__file__).resolve().parents[2], *policy.settings.private_paths]
        if policy.settings.state_dir:
            excluded.append(policy.settings.state_dir)
        if any(target.is_relative_to(path) for path in excluded):
            raise PolicyError("private_service_data_denied")
        with file_handle(target) as handle:
            if actual_path(handle) != target:
                raise PolicyError("outside_authorized_root")
            # Bound before hashing; only an exclusively read-shared file handle
            # is used for BOTH verification and transfer. Writers are rejected.
            from .windows_files import FileInfo, kernel

            info = FileInfo()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise PolicyError("verification_failed")
            if info.size_hi << 32 | info.size_lo > MAX_IMPORT_BYTES:
                raise PolicyError("file_size_limit")
            before = snapshot(handle)
            if not kernel.SetFilePointerEx(handle, 0, None, 0):
                raise PolicyError("verification_failed")
            content = bytearray()
            buffer, received = ctypes.create_string_buffer(65536), wintypes.DWORD()
            while True:
                if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(received), None):
                    raise PolicyError("verification_failed")
                if not received.value:
                    break
                content.extend(buffer.raw[: received.value])
                if len(content) > MAX_IMPORT_BYTES:
                    raise PolicyError("file_size_limit")
            after = snapshot(handle)
            if before != after or hashlib.sha256(content).hexdigest() != before["sha256"] or actual_path(handle) != target:
                raise PolicyError("source_changed")
            return {
                "ok": True,
                "verified": True,
                "format": target.suffix.lower(),
                "name": target.name,
                "root_id": root_id,
                "relative_path": relative_path,
                "actual_path": str(target),
                "size": len(content),
                "sha256": before["sha256"],
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
