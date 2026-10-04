"""Owner-bound durable metadata, immutable versions and retryable tombstone cleanup."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
_locks = {}
_locks_guard = threading.Lock()


class KnowledgeError(Exception):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise KnowledgeError("invalid_id")
    return value


def display_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value) or any(c in value for c in "/\\"):
        raise KnowledgeError("invalid_name")
    return value.strip()


class KnowledgeStore:
    def __init__(self, root, *, parser_dependencies=None, timeout=15):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self.root / "objects"
        self.objects.mkdir(exist_ok=True)
        self.dependencies = parser_dependencies
        self.timeout = timeout
        with _locks_guard:
            self.lock = _locks.setdefault(str(self.root), threading.RLock())
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bases(id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, base_id TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, current_version TEXT, created REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS versions(
                    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, base_id TEXT NOT NULL,
                    hash TEXT NOT NULL, size INTEGER NOT NULL, format TEXT NOT NULL,
                    source TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
                    error TEXT, parser_version TEXT NOT NULL, raw_locator TEXT NOT NULL,
                    text_locator TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS content_identity ON versions(base_id,hash) WHERE status!='deleted';
                CREATE TABLE IF NOT EXISTS cleanup(version_id TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS invalidations(id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, version_id TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL);
            """)
            # The process-wide and OS lock means no surviving importer is active.
            db.execute("UPDATE versions SET status='failed', error='processing_interrupted' WHERE status='processing'")
            self._cleanup(db)

    @contextmanager
    def transaction(self):
        with self.lock, (self.root / "store.lock").open("a+b") as lockfile:
            if os.name == "nt":
                import msvcrt

                lockfile.seek(0)
                lockfile.write(b"0")
                lockfile.flush()
                lockfile.seek(0)
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lockfile, fcntl.LOCK_EX)
            db = sqlite3.connect(self.root / "knowledge.sqlite3", timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
                if os.name == "nt":
                    lockfile.seek(0)
                    msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lockfile, fcntl.LOCK_UN)

    def _base(self, db, owner, base):
        row = db.execute("SELECT * FROM bases WHERE id=? AND owner=? AND deleted=0", (identifier(base), owner)).fetchone()
        if row is None:
            raise KnowledgeError("not_found")
        return dict(row)

    def _doc(self, db, owner, doc):
        row = db.execute("SELECT * FROM documents WHERE id=? AND deleted=0", (identifier(doc),)).fetchone()
        if row is None:
            raise KnowledgeError("not_found")
        self._base(db, owner, row["base_id"])
        return dict(row)

    def bases(self, owner):
        with self.transaction() as db:
            return {"ok": True, "knowledge_bases": [dict(r) for r in db.execute("SELECT id AS knowledge_base_id,name,revision,created FROM bases WHERE owner=? AND deleted=0 ORDER BY created", (owner,))]}

    def create(self, owner, name):
        name = display_name(name)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM bases WHERE owner=? AND lower(name)=lower(?) AND deleted=0", (owner, name)).fetchone():
                raise KnowledgeError("name_conflict")
            base = uuid.uuid4().hex
            db.execute("INSERT INTO bases(id,owner,name,created) VALUES(?,?,?,?)", (base, owner, name, time.time()))
        return {"ok": True, "knowledge_base_id": base, "name": name}

    def rename(self, owner, base, name):
        name = display_name(name)
        with self.transaction() as db:
            self._base(db, owner, base)
            if db.execute("SELECT 1 FROM bases WHERE owner=? AND lower(name)=lower(?) AND id<>? AND deleted=0", (owner, name, base)).fetchone():
                raise KnowledgeError("name_conflict")
            db.execute("UPDATE bases SET name=?,revision=revision+1 WHERE id=?", (name, base))
        return {"ok": True}

    def rename_document(self, owner, doc, name):
        name = display_name(name)
        with self.transaction() as db:
            row = self._doc(db, owner, doc)
            self._check_name(db, row["base_id"], name, doc)
            db.execute("UPDATE documents SET name=?,revision=revision+1 WHERE id=?", (name, doc))
            db.execute("UPDATE bases SET revision=revision+1 WHERE id=?", (row["base_id"],))
        return {"ok": True, "document_id": doc}

    def _check_name(self, db, base, name, doc=None):
        names = db.execute("SELECT id,name FROM documents WHERE base_id=? AND deleted=0", (base,))
        if any(r["id"] != doc and r["name"].casefold() == name.casefold() for r in names):
            raise KnowledgeError("name_conflict")

    def documents(self, owner, base):
        with self.transaction() as db:
            self._base(db, owner, base)
            rows = db.execute(
                """SELECT d.id AS document_id,d.name,d.revision,d.current_version AS current_version_id,
                (SELECT id FROM versions WHERE doc_id=d.id ORDER BY created DESC LIMIT 1) AS version_id,
                (SELECT status FROM versions WHERE doc_id=d.id ORDER BY created DESC LIMIT 1) AS status,
                (SELECT error FROM versions WHERE doc_id=d.id ORDER BY created DESC LIMIT 1) AS error
                FROM documents d WHERE base_id=? AND deleted=0 ORDER BY created""",
                (base,),
            )
            return {"ok": True, "knowledge_base_id": base, "documents": [dict(r) for r in rows]}

    def document(self, owner, doc):
        with self.transaction() as db:
            row = self._doc(db, owner, doc)
            versions = []
            for version in db.execute("SELECT * FROM versions WHERE doc_id=? ORDER BY created DESC", (doc,)):
                version = dict(version)
                version.update(version_id=version["id"], document_id=version["doc_id"], knowledge_base_id=version["base_id"], content_sha256=version["hash"], imported_at=version["created"])
                version["source"] = json.loads(version["source"])
                versions.append(version)
            return {"ok": True, "document_id": doc, "knowledge_base_id": row["base_id"], "name": row["name"], "revision": row["revision"], "current_version_id": row["current_version"], "versions": versions, "indexed": False}

    def content(self, owner, doc, version_id=None):
        with self.transaction() as db:
            row = self._doc(db, owner, doc)
            selected = version_id or row["current_version"]
            if not selected:
                raise KnowledgeError("no_available_version")
            version = db.execute("SELECT * FROM versions WHERE id=? AND doc_id=?", (identifier(selected), doc)).fetchone()
            if not version or version["status"] != "ready":
                raise KnowledgeError("version_not_ready")
            data = json.loads((self.root / version["text_locator"]).read_text(encoding="utf-8"))
            return {"ok": True, "document_id": doc, "version_id": selected, "pages": data["pages"], "indexed": False, "content_is_untrusted_data": True}

    def _parse(self, raw, suffix, output):
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG") if key in os.environ}
        if self.dependencies:
            env["PYTHONPATH"] = str(self.dependencies)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("parser.py")), str(raw), suffix, str(output)], timeout=self.timeout, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if result.returncode or not output.exists():
                return {"status": "failed", "error": "parser_worker_failed", "parser_version": "text-v1", "pages": []}
            return json.loads(output.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired:
            return {"status": "failed", "error": "parse_timeout", "parser_version": "text-v1", "pages": []}

    def import_file(self, owner, base, name, content, source, *, document_id=None, expected_revision=None, format_suffix=None):
        name = display_name(name)
        suffix = format_suffix or Path(name).suffix.lower()
        if suffix not in {".pdf", ".md", ".txt"}:
            raise KnowledgeError("unsupported_format")
        if len(content) > MAX_BYTES:
            raise KnowledgeError("file_size_limit")
        digest = hashlib.sha256(content).hexdigest()
        with self.transaction() as db:
            self._base(db, owner, base)
            if document_id:
                doc = self._doc(db, owner, document_id)
                if doc["base_id"] != base or type(expected_revision) is not int or doc["revision"] != expected_revision:
                    raise KnowledgeError("stale_confirmation")
            duplicate = db.execute("SELECT v.id,v.doc_id,v.status FROM versions v JOIN documents d ON d.id=v.doc_id WHERE v.base_id=? AND v.hash=? AND d.deleted=0", (base, digest)).fetchone()
            if duplicate:
                if document_id and duplicate["doc_id"] != document_id:
                    raise KnowledgeError("content_belongs_to_other_document")
                return {"ok": True, "status": "duplicate", "document_id": duplicate["doc_id"], "version_id": duplicate["id"], "parse_status": duplicate["status"], "indexed": False}
            self._check_name(db, base, name if not document_id else doc["name"], document_id)
            doc_id, version = document_id or uuid.uuid4().hex, uuid.uuid4().hex
            directory = self.objects / version
            directory.mkdir()
            raw, text = directory / "original", directory / "parsed.json"
            try:
                with raw.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not document_id:
                    db.execute("INSERT INTO documents(id,base_id,name,created) VALUES(?,?,?,?)", (doc_id, base, name, time.time()))
                db.execute(
                    "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (version, doc_id, base, digest, len(content), suffix, json.dumps(source, ensure_ascii=False), time.time(), "processing", None, "text-v1", str(raw.relative_to(self.root)), str(text.relative_to(self.root))),
                )
                if document_id:
                    db.execute("UPDATE documents SET revision=revision+1 WHERE id=?", (doc_id,))
                db.execute("UPDATE bases SET revision=revision+1 WHERE id=?", (base,))
                db.commit()  # Durable processing evidence before invoking disposable parser.
                parsed = self._parse(raw, suffix, text)
                with text.open("w", encoding="utf-8") as stream:
                    json.dump(parsed, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE versions SET status=?,error=?,parser_version=? WHERE id=?", (parsed["status"], parsed["error"], parsed["parser_version"], version))
                if parsed["status"] == "ready":
                    if document_id and doc["current_version"]:
                        self._invalidate(db, doc_id, doc["current_version"], "superseded")
                    db.execute("UPDATE documents SET current_version=? WHERE id=?", (version, doc_id))
            except BaseException:
                # Once committed, preserve evidence for restart recovery; never
                # delete an original which a processing record still references.
                if not db.execute("SELECT 1 FROM versions WHERE id=?", (version,)).fetchone():
                    shutil.rmtree(directory)
                raise
            return {"ok": True, "status": parsed["status"], "error": parsed["error"], "document_id": doc_id, "version_id": version, "knowledge_base_id": base, "indexed": False}

    def _invalidate(self, db, doc, version, reason):
        db.execute("INSERT INTO invalidations VALUES(?,?,?,?,?)", (uuid.uuid4().hex, doc, version, reason, time.time()))

    def _preview(self, db, owner, kind, target):
        if kind == "document":
            row = self._doc(db, owner, target)
            docs = [row]
            name = row["name"]
        elif kind == "knowledge_base":
            row = self._base(db, owner, target)
            docs = [dict(r) for r in db.execute("SELECT * FROM documents WHERE base_id=? AND deleted=0 ORDER BY id", (target,))]
            name = row["name"]
        else:
            raise KnowledgeError("invalid_kind")
        snapshot = {"kind": kind, "target": target, "owner": owner, "revision": row["revision"], "documents": [(d["id"], d["revision"], d["current_version"]) for d in docs]}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        return {
            "ok": True,
            "kind": kind,
            "target": target,
            "name": name,
            "digest": digest,
            "documents": [{"document_id": d["id"], "name": d["name"], "revision": d["revision"]} for d in docs],
            "deletion_scope": "Only knowledge-store copies and records; Windows originals remain unchanged",
        }

    def deletion_preview(self, owner, kind, target):
        with self.transaction() as db:
            return self._preview(db, owner, kind, target)

    def delete(self, owner, kind, target, digest):
        # Internal provider operation; only the human-only router can expose it.
        with self.transaction() as db:
            preview = self._preview(db, owner, kind, target)
            if preview["digest"] != digest:
                raise KnowledgeError("stale_confirmation")
            for item in preview["documents"]:
                doc = item["document_id"]
                db.execute("UPDATE documents SET deleted=1,revision=revision+1 WHERE id=?", (doc,))
                for version in db.execute("SELECT id FROM versions WHERE doc_id=?", (doc,)).fetchall():
                    self._invalidate(db, doc, version["id"], "deleted")
                    db.execute("INSERT OR IGNORE INTO cleanup VALUES(?,'pending',NULL)", (version["id"],))
                db.execute("UPDATE versions SET status='deleted' WHERE doc_id=?", (doc,))
            if kind == "knowledge_base":
                db.execute("UPDATE bases SET deleted=1,revision=revision+1 WHERE id=?", (target,))
            elif preview["documents"]:
                base = db.execute("SELECT base_id FROM documents WHERE id=?", (target,)).fetchone()[0]
                db.execute("UPDATE bases SET revision=revision+1 WHERE id=?", (base,))
            db.commit()  # Access is revoked even if physical cleanup fails.
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, owner)
            pending = db.execute(
                "SELECT COUNT(DISTINCT c.version_id) FROM cleanup c JOIN invalidations i ON i.version_id=c.version_id JOIN documents d ON d.id=i.doc_id JOIN bases b ON b.id=d.base_id WHERE c.status!='done' AND b.owner=?", (owner,)
            ).fetchone()[0]
            return {"ok": True, "status": "deleted", "cleanup_pending": pending, "windows_originals_deleted": False}

    def _cleanup(self, db, owner=None):
        rows = (
            db.execute("SELECT * FROM cleanup WHERE status!='done'").fetchall()
            if owner is None
            else db.execute("SELECT DISTINCT c.* FROM cleanup c JOIN invalidations i ON i.version_id=c.version_id JOIN documents d ON d.id=i.doc_id JOIN bases b ON b.id=d.base_id WHERE c.status!='done' AND b.owner=?", (owner,)).fetchall()
        )
        for row in rows:
            try:
                target = self.objects / identifier(row["version_id"])
                if target.is_symlink():
                    raise OSError("unsafe_object")
                if target.exists():
                    shutil.rmtree(target)
                db.execute("DELETE FROM versions WHERE id=?", (row["version_id"],))
                db.execute("UPDATE cleanup SET status='done',error=NULL WHERE version_id=?", (row["version_id"],))
            except OSError:
                db.execute("UPDATE cleanup SET status='pending',error='cleanup_failed' WHERE version_id=?", (row["version_id"],))

    def cleanup(self, owner):
        with self.transaction() as db:
            self._cleanup(db, owner)
            rows = db.execute(
                "SELECT c.version_id,c.status,c.error FROM cleanup c JOIN invalidations i ON i.version_id=c.version_id JOIN documents d ON d.id=i.doc_id JOIN bases b ON b.id=d.base_id WHERE b.owner=? GROUP BY c.version_id", (owner,)
            )
            return {"ok": True, "cleanup": [dict(r) for r in rows]}
