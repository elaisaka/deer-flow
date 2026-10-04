"""Durable plans with a separate confirmation authority and per-operation evidence."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from .policy import PolicyError, relative_parts
from .windows_files import (
    actual_path,
    file_handle,
    removable_directory,
    remove_empty,
    rename_no_replace,
    snapshot,
)


def now():
    return datetime.now(UTC).isoformat()


class Organizer:
    def __init__(self, policy, state_dir):
        self.policy = policy
        self.state_dir = Path(state_dir).absolute()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "plans.sqlite3"
        self.rename = rename_no_replace
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, document TEXT NOT NULL)")

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def save(self, plan):
        plan["updated_at"] = now()
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO plans VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                (plan["plan_id"], json.dumps(plan, ensure_ascii=False)),
            )

    def load(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise PolicyError("invalid_plan_id")
        with closing(self.connect()) as db:
            row = db.execute("SELECT document FROM plans WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise PolicyError("plan_not_found")
        return json.loads(row[0])

    @contextmanager
    def lock(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise PolicyError("invalid_plan_id")
        with (self.state_dir / f"{identifier}.lock").open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise PolicyError("plan_busy") from error
            else:
                import fcntl

                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise PolicyError("plan_busy") from error
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def check_root(self, plan):
        root = self.policy.settings.roots.get(plan["root_id"])
        if root is None or str(root) != plan["root_path"] or self.policy.identities[plan["root_id"]] != plan["root_identity"]:
            raise PolicyError("plan_root_changed")

    @contextmanager
    def parent(self, plan, relative, *, moving=False):
        self.check_root(plan)
        parts = relative_parts(relative)
        with self.policy._directory(plan["root_id"], parts[:-1], allow_file_moves=moving) as parent:
            target = parent / parts[-1]
            if target.is_relative_to(self.state_dir) or ".local-file-service" in [part.casefold() for part in target.parts]:
                raise PolicyError("service_data_protected")
            yield target

    def file_state(self, plan, relative):
        with self.parent(plan, relative) as path, file_handle(path) as handle:
            if actual_path(handle) != path:
                raise PolicyError("path_changed")
            return snapshot(handle)

    def destination(self, plan, item, stack=None):
        parts = relative_parts(item["target"])
        root = self.policy.settings.roots[plan["root_id"]]
        parent = root.joinpath(*parts[:-1])
        # Classification creates at most one leaf subdirectory below an existing base.
        if parent.exists():
            context = self.policy._directory(plan["root_id"], parts[:-1], allow_file_moves=stack is not None)
            if stack is None:
                with context:
                    pass
            else:
                stack.enter_context(context)
        else:
            with self.policy._directory(plan["root_id"], parts[:-2]):
                pass
        target = parent / parts[-1]
        if target.is_relative_to(self.state_dir) or ".local-file-service" in [part.casefold() for part in target.parts]:
            raise PolicyError("service_data_protected")
        if os.path.lexists(target):
            raise PolicyError("target_conflict")
        return target

    def preview(self, request):
        if set(request) - {
            "root_id",
            "directory",
            "files",
            "rule",
            "plan_id",
            "expected_version",
        }:
            raise PolicyError("invalid_arguments")
        root_id, directory, rule = (
            request.get("root_id"),
            request.get("directory", ""),
            request.get("rule"),
        )
        if not isinstance(root_id, str) or not isinstance(directory, str) or ("plan_id" in request and type(request.get("expected_version")) is not int):
            raise PolicyError("invalid_arguments")
        parts = relative_parts(directory, allow_empty=True)
        if not isinstance(rule, dict) or rule.get("kind") not in {"rename", "classify"}:
            raise PolicyError("invalid_rule")
        identifier = request.get("plan_id", uuid.uuid4().hex)
        with self.lock(identifier):
            previous = self.load(identifier) if "plan_id" in request else None
            if previous and (previous["status"] not in {"awaiting_confirmation", "confirmed", "cancelled"} or request.get("expected_version") != previous["version"]):
                raise PolicyError("plan_not_editable")
            with self.policy._directory(root_id, parts):
                names = request.get("files")
                if names is None:
                    entries = self.policy.list_directory(root_id, directory)["entries"]
                    names = [e["name"] for e in entries if e["kind"] == "file"]
                if not isinstance(names, list) or not 1 <= len(names) <= 100 or any(not isinstance(n, str) or len(relative_parts(n)) != 1 for n in names):
                    raise PolicyError("invalid_file_selection")
                if len({n.casefold() for n in names}) != len(names):
                    raise PolicyError("duplicate_source")
                names = sorted(names, key=str.casefold)
                plan = {
                    "ok": True,
                    "plan_id": identifier,
                    "version": previous["version"] + 1 if previous else 1,
                    "root_id": root_id,
                    "root_path": str(self.policy.settings.roots[root_id]),
                    "root_identity": self.policy.identities[root_id],
                    "directory": directory,
                    "recursive": False,
                    "rule": rule,
                    "created_at": now(),
                    "status": "awaiting_confirmation",
                    "confirmation": None,
                    "items": [],
                    "created_directories": [],
                }
                for index, name in enumerate(names):
                    source = "/".join((*parts, name))
                    item = {
                        "source": source,
                        "target": None,
                        "state": "pending",
                        "error": None,
                        "undo_state": None,
                        "undo_error": None,
                    }
                    try:
                        item["source_state"] = self.file_state(plan, source)
                        suffix, stem = (
                            PureWindowsPath(name).suffix,
                            PureWindowsPath(name).stem,
                        )
                        if rule["kind"] == "rename":
                            if set(rule) - {
                                "kind",
                                "prefix",
                                "suffix",
                                "numbering",
                                "omit_stem",
                            }:
                                raise PolicyError("invalid_rule")
                            prefix, ending = (
                                rule.get("prefix", ""),
                                rule.get("suffix", ""),
                            )
                            if not isinstance(prefix, str) or not isinstance(ending, str) or type(rule.get("omit_stem", False)) is not bool:
                                raise PolicyError("invalid_rule")
                            number = ""
                            if "numbering" in rule:
                                numbering = rule["numbering"]
                                if not isinstance(numbering, dict) or set(numbering) - {
                                    "start",
                                    "width",
                                }:
                                    raise PolicyError("invalid_rule")
                                start, width = (
                                    numbering.get("start", 1),
                                    numbering.get("width", 2),
                                )
                                if type(start) is not int or not 0 <= start <= 999999 or type(width) is not int or not 1 <= width <= 6:
                                    raise PolicyError("invalid_rule")
                                number = str(start + index).zfill(width) + "-"
                            target_name = prefix + number + ("" if rule.get("omit_stem") else stem) + ending + suffix
                            if len(relative_parts(target_name)) != 1:
                                raise PolicyError("invalid_filename")
                            target = "/".join((*parts, target_name))
                        else:
                            mapping = rule.get("mapping")
                            if set(rule) != {"kind", "mapping"} or not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
                                raise PolicyError("invalid_rule")
                            folded = {k.casefold(): v for k, v in mapping.items()}
                            if len(folded) != len(mapping):
                                raise PolicyError("duplicate_extension")
                            folder = folded.get(suffix.casefold())
                            if folder is None:
                                item.update(state="skipped", error="unmapped_extension")
                                plan["items"].append(item)
                                continue
                            if len(relative_parts(folder)) != 1:
                                raise PolicyError("single_subdirectory_required")
                            target = "/".join((*parts, folder, name))
                        item["target"] = target
                        if target == source:
                            item.update(state="skipped", error="unchanged")
                        else:
                            self.destination(plan, item)
                    except (PolicyError, OSError) as error:
                        item.update(
                            state="conflict",
                            error=getattr(error, "code", "filesystem_error"),
                        )
                    plan["items"].append(item)
                targets = {}
                sources = {i["source"].casefold() for i in plan["items"]}
                for item in plan["items"]:
                    if item["state"] == "pending":
                        key = item["target"].casefold()
                        targets.setdefault(key, []).append(item)
                        if key in sources:
                            item.update(state="conflict", error="occupied_source_target")
                for duplicates in targets.values():
                    if len(duplicates) > 1:
                        for item in duplicates:
                            item.update(state="conflict", error="duplicate_target")
                plan["digest"] = hashlib.sha256(
                    json.dumps(
                        {
                            k: plan[k]
                            for k in (
                                "version",
                                "root_id",
                                "root_path",
                                "root_identity",
                                "directory",
                                "rule",
                                "items",
                            )
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
                self.save(plan)
                return self.present(plan)

    def present(self, plan):
        result = json.loads(json.dumps(plan))
        result["preview_completed"] = True
        successes = [i for i in result["items"] if i["state"] == "succeeded"]
        result["verified"] = bool(successes) and all(i.get("actual_verified", False) for i in successes) and result["status"] in {"completed", "undone"}
        result["review_url"] = "http://localhost:2026/workspace/extensions/personal.file-organization/plans"
        result["can_undo"] = any(i["state"] == "succeeded" and i["undo_state"] != "undone" and i.get("actual_verified", False) for i in plan["items"])
        for item in result["items"]:
            item["source_path"] = str(Path(plan["root_path"]) / item["source"])
            item["target_path"] = str(Path(plan["root_path"]) / item["target"]) if item["target"] else None
        return result

    def precheck(self, plan, stack, *, handles=False):
        self.check_root(plan)
        if any(i["state"] == "conflict" for i in plan["items"]):
            raise PolicyError("plan_conflicts")
        opened = []
        for item in plan["items"]:
            if item["state"] != "pending":
                continue
            source = stack.enter_context(self.parent(plan, item["source"], moving=handles))
            handle = stack.enter_context(file_handle(source, rename=handles))
            if actual_path(handle) != source or snapshot(handle) != item["source_state"]:
                raise PolicyError("source_changed")
            self.destination(plan, item, stack)
            opened.append((item, handle))
        if not opened:
            raise PolicyError("no_operations")
        return opened

    def confirm(self, identifier, version, digest, actor):
        with self.lock(identifier):
            plan = self.load(identifier)
            if plan["version"] != version or plan["digest"] != digest:
                raise PolicyError("stale_plan_version")
            if plan["status"] not in {"awaiting_confirmation", "confirmed"}:
                raise PolicyError("plan_not_confirmable")
            with ExitStack() as stack:
                self.precheck(plan, stack)
            plan["confirmation"] = {
                "version": version,
                "digest": digest,
                "actor": actor,
                "confirmed_at": now(),
            }
            plan["status"] = "confirmed"
            self.save(plan)
            return self.present(plan)

    def cancel(self, identifier, version):
        with self.lock(identifier):
            plan = self.load(identifier)
            if plan["version"] != version or plan["status"] not in {
                "awaiting_confirmation",
                "confirmed",
                "cancelled",
            }:
                raise PolicyError("plan_not_cancellable")
            plan.update(status="cancelled", confirmation=None)
            self.save(plan)
            return self.present(plan)

    def get(self, identifier):
        with self.lock(identifier):
            plan = self.load(identifier)
            if plan["status"] in {"executing", "undoing"}:
                self.recover(plan)
            self.verify_records(plan)
            return self.present(plan)

    def verify_records(self, plan):
        """Historical completion and current file verification are separate facts."""
        for item in plan["items"]:
            if item["state"] != "succeeded":
                continue
            relative = item["source"] if item["undo_state"] == "undone" else item["target"]
            try:
                if self.file_state(plan, relative) != item["source_state"]:
                    raise PolicyError("record_file_changed")
                item.update(actual_verified=True, actual_error=None)
            except (PolicyError, OSError) as error:
                item.update(
                    actual_verified=False,
                    actual_error=getattr(error, "code", "filesystem_error"),
                )

    def list_plans(self):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id FROM plans ORDER BY rowid DESC LIMIT 100").fetchall()
        plans = []
        for (identifier,) in rows:
            try:
                plan = self.get(identifier)
                plans.append(
                    {
                        k: plan[k]
                        for k in (
                            "plan_id",
                            "version",
                            "digest",
                            "status",
                            "root_path",
                            "directory",
                            "created_at",
                        )
                    }
                )
            except PolicyError as error:
                if error.code != "plan_busy":
                    raise
                plans.append({"plan_id": identifier, "status": "busy"})
        return {"ok": True, "plans": plans}

    def recover(self, plan):
        undoing = plan["status"] == "undoing"
        for item in plan["items"]:
            if not undoing and item["state"] == "pending":
                item.update(state="skipped", error="interrupted_before_item")
            if item["undo_state"] == "applying" if undoing else item["state"] == "applying":
                source, target = (item["target"], item["source"]) if undoing else (item["source"], item["target"])
                try:
                    with self.parent(plan, source) as original:
                        if os.path.lexists(original):
                            raise PolicyError("interrupted_not_verified")
                    if self.file_state(plan, target) != item["source_state"]:
                        raise PolicyError("interrupted_not_verified")
                    if undoing:
                        item.update(undo_state="undone", undo_error=None)
                    else:
                        item.update(
                            state="succeeded",
                            error=None,
                            actual_path=str(Path(plan["root_path"]) / target),
                            result_state=item["source_state"],
                            recovered=True,
                        )
                except (PolicyError, OSError) as error:
                    if undoing:
                        item.update(
                            undo_state="blocked",
                            undo_error=getattr(error, "code", "filesystem_error"),
                        )
                    else:
                        item.update(
                            state="uncertain",
                            error=getattr(error, "code", "filesystem_error"),
                        )
        self.finish(plan, undo=undoing)

    def finish(self, plan, *, undo=False):
        if undo:
            actionable = [i for i in plan["items"] if i["state"] == "succeeded"]
            plan["status"] = "undone" if all(i["undo_state"] == "undone" for i in actionable) else "partial_undo"
        else:
            pending = [i for i in plan["items"] if i["error"] not in {"unmapped_extension", "unchanged"}]
            done = sum(i["state"] == "succeeded" for i in pending)
            plan["status"] = "completed" if done == len(pending) and done else "partial" if done else "failed"
        self.save(plan)

    def execute(self, identifier, version):
        with self.lock(identifier):
            plan = self.load(identifier)
            if plan["version"] != version:
                raise PolicyError("stale_plan_version")
            if plan["status"] == "executing":
                self.recover(plan)
                self.verify_records(plan)
                return self.present(plan)
            if plan["status"] in {
                "completed",
                "partial",
                "failed",
                "undone",
                "partial_undo",
            }:
                self.verify_records(plan)
                return self.present(plan)
            approval = plan["confirmation"]
            if plan["status"] != "confirmed" or not approval or approval["version"] != version or approval["digest"] != plan["digest"]:
                raise PolicyError("confirmation_required")
            with ExitStack() as stack:
                try:
                    opened = self.precheck(plan, stack, handles=True)
                except (PolicyError, OSError) as error:
                    for item in plan["items"]:
                        if item["state"] == "pending":
                            item.update(
                                state="skipped",
                                error=getattr(error, "code", "precheck_failed"),
                            )
                    plan["status"] = "failed"
                    self.save(plan)
                    return self.present(plan)
                plan["status"] = "executing"
                self.save(plan)
                failed = False
                for item, handle in opened:
                    if failed:
                        item.update(state="skipped", error="earlier_item_failed")
                        self.save(plan)
                        continue
                    try:
                        target = Path(plan["root_path"]) / item["target"]
                        if not target.parent.exists():
                            record = {
                                "path": str(target.parent.relative_to(Path(plan["root_path"]))).replace("\\", "/"),
                                "state": "creating",
                            }
                            plan["created_directories"].append(record)
                            self.save(plan)
                            made = self.policy.create_folder(plan["root_id"], record["path"])
                            record.update(
                                state="owned" if made["status"] == "created" else "existing",
                                identity=target.parent.stat().st_ino,
                            )
                            self.save(plan)
                        stack.enter_context(
                            self.policy._directory(
                                plan["root_id"],
                                relative_parts(item["target"])[:-1],
                                allow_file_moves=True,
                            )
                        )
                        item["state"] = "applying"
                        self.save(plan)  # Durable intent precedes the native rename.
                        self.rename(handle, target)
                        state = snapshot(handle)
                        if state != item["source_state"]:
                            raise PolicyError("verification_failed")
                        item.update(
                            state="succeeded",
                            error=None,
                            actual_path=str(actual_path(handle)),
                            result_state=state,
                            actual_verified=True,
                        )
                    except (PolicyError, OSError) as error:
                        # A syscall may have succeeded before verification raised.
                        # Keep intent if outcome is ambiguous; never lose evidence.
                        code = getattr(error, "code", "filesystem_error")
                        try:
                            if actual_path(handle) == target and snapshot(handle) == item["source_state"]:
                                item.update(
                                    state="succeeded",
                                    error=None,
                                    actual_path=str(target),
                                    result_state=item["source_state"],
                                    actual_verified=True,
                                    warning=code,
                                )
                            elif actual_path(handle) == Path(plan["root_path"]) / item["source"]:
                                item.update(state="failed", error=code)
                            else:
                                item.update(state="uncertain", error=code)
                        except (PolicyError, OSError):
                            item.update(state="uncertain", error=code)
                        failed = True
                    self.save(plan)
                self.finish(plan)
            return self.present(plan)

    def undo(self, identifier):
        with self.lock(identifier):
            plan = self.load(identifier)
            if plan["status"] == "undoing":
                self.recover(plan)
            if plan["status"] == "undone":
                self.verify_records(plan)
                return self.present(plan)
            if plan["status"] not in {"completed", "partial", "partial_undo", "failed"} or not plan["confirmation"]:
                raise PolicyError("plan_not_undoable")
            plan["status"] = "undoing"
            self.save(plan)
            for item in reversed(plan["items"]):
                if item["state"] != "succeeded" or item["undo_state"] == "undone":
                    continue
                try:
                    with (
                        self.parent(plan, item["target"], moving=True) as current,
                        self.parent(plan, item["source"], moving=True) as original,
                        file_handle(current, rename=True) as handle,
                    ):
                        if actual_path(handle) != current or snapshot(handle) != item["result_state"]:
                            raise PolicyError("file_changed_since_execution")
                        if os.path.lexists(original):
                            raise PolicyError("undo_target_conflict")
                        item["undo_state"] = "applying"
                        self.save(plan)
                        try:
                            self.rename(handle, original)
                        except (PolicyError, OSError):
                            if actual_path(handle) != original or snapshot(handle) != item["source_state"]:
                                raise
                        if snapshot(handle) != item["source_state"]:
                            raise PolicyError("verification_failed")
                        item.update(
                            undo_state="undone",
                            undo_error=None,
                            actual_path=str(actual_path(handle)),
                            actual_verified=True,
                        )
                except (PolicyError, OSError) as error:
                    item.update(
                        undo_state="blocked",
                        undo_error=getattr(error, "code", "filesystem_error"),
                    )
                self.save(plan)
            for record in reversed(plan["created_directories"]):
                if record["state"] != "owned":
                    continue
                try:
                    with self.parent(plan, record["path"] + "/placeholder") as placeholder:
                        directory = placeholder.parent
                    with (
                        self.parent(plan, record["path"]) as directory,
                        removable_directory(directory) as handle,
                    ):
                        if directory.stat().st_ino != record["identity"]:
                            raise PolicyError("directory_identity_changed")
                        remove_empty(handle, directory)
                    record["state"] = "removed"
                except (PolicyError, OSError) as error:
                    record["cleanup_error"] = getattr(error, "code", "filesystem_error")
                self.save(plan)
            self.finish(plan, undo=True)
            self.verify_records(plan)
            return self.present(plan)
