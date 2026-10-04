"""Synthetic, persistent document management; no model or user materials."""

import os
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from knowledge_base_extension.store import KnowledgeError, KnowledgeStore


def synthetic_pdf(texts):
    """A tiny generated PDF with real page/content objects and a valid xref."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    page_ids = []
    for text in texts:
        page = len(objects) + 1
        page_ids.append(page)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /Contents {page + 1} 0 R >>".encode())
        content = f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{' '.join(f'{n} 0 R' for n in page_ids)}] >>".encode()
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


@pytest.mark.parametrize(
    "name,data,status,error",
    [
        ("a.txt", b"Redis text", "ready", None),
        ("a.md", b"# Redis\n<script>alert(1)</script>", "ready", None),
        ("a.pdf", synthetic_pdf(["Redis page one", "Page two"]), "ready", None),
        ("a.pdf", b"broken PDF", "failed", "damaged_document"),
        ("a.pdf", synthetic_pdf([""]), "failed", "no_effective_text"),
        ("a.txt", b"", "failed", "empty_file"),
        ("a.txt", b"\xff", "failed", "invalid_utf8"),
    ],
)
def test_formats_and_parse_states(tmp_path, name, data, status, error):
    store = KnowledgeStore(tmp_path, parser_dependencies=os.environ.get("KNOWLEDGE_PARSER_DEPS"))
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    result = store.import_file("owner", kb, name, data, {"type": "upload"})
    assert result["status"] == status and result["error"] == error
    if status == "ready":
        pages = store.content("owner", result["document_id"])["pages"]
        if name.endswith("pdf"):
            assert [p["page"] for p in pages] == [1, 2]
            assert "Redis page one" in pages[0]["text"] and "Page two" in pages[1]["text"]
        else:
            assert pages[0] == {"page": None, "text": data.decode()}
    if name.endswith("pdf"):
        assert store.document("owner", result["document_id"])["versions"][0]["parser_version"].startswith("pypdf-6.19.0/")


def test_actual_image_only_pdf_and_page_limit(tmp_path):
    import sys

    dependencies = os.environ.get("KNOWLEDGE_PARSER_DEPS")
    if dependencies:
        sys.path.insert(0, dependencies)
    try:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    finally:
        if dependencies:
            sys.path.remove(dependencies)
    import io

    writer = PdfWriter()
    page = writer.add_blank_page(100, 100)
    image = DecodedStreamObject()
    image.set_data(b"\xff\xff\xff")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/I1"): writer._add_object(image)})})
    stream = DecodedStreamObject()
    stream.set_data(b"q 100 0 0 100 0 0 cm /I1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    raw = io.BytesIO()
    writer.write(raw)
    store = KnowledgeStore(tmp_path, parser_dependencies=dependencies)
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    result = store.import_file("owner", kb, "scan.pdf", raw.getvalue(), {})
    assert result["status"] == "failed" and result["error"] == "no_effective_text"
    result = store.import_file("owner", kb, "too-many-pages.pdf", synthetic_pdf(["page"] * 201), {})
    assert result["error"] == "page_limit"


def test_limits_and_cross_base_duplicate_policy(tmp_path):
    store = KnowledgeStore(tmp_path)
    a, b = [store.create("owner", name)["knowledge_base_id"] for name in ("A", "B")]
    with pytest.raises(KnowledgeError, match="unsupported_format"):
        store.import_file("owner", a, "a.exe", b"MZ", {})
    with pytest.raises(KnowledgeError, match="file_size_limit"):
        store.import_file("owner", a, "a.txt", b"a" * (10 * 1024 * 1024 + 1), {})
    first = store.import_file("owner", a, "a.txt", b"data", {})
    second = store.import_file("owner", b, "a.txt", b"data", {})
    assert first["document_id"] != second["document_id"]
    store.rename("owner", a, "Renamed")
    assert store.bases("owner")["knowledge_bases"][0]["knowledge_base_id"] == a


def test_interrupted_processing_and_cleanup_retry(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    result = store.import_file("owner", kb, "a.txt", b"data", {})
    with sqlite3.connect(tmp_path / "knowledge.sqlite3") as db:
        db.execute("UPDATE versions SET status='processing'")
    recovered = KnowledgeStore(tmp_path)
    assert recovered.document("owner", result["document_id"])["versions"][0]["error"] == "processing_interrupted"
    preview = recovered.deletion_preview("owner", "knowledge_base", kb)
    import knowledge_base_extension.store as module

    real_remove = module.shutil.rmtree
    monkeypatch.setattr(module.shutil, "rmtree", lambda p: (_ for _ in ()).throw(OSError("busy")))
    assert recovered.delete("owner", "knowledge_base", kb, preview["digest"])["cleanup_pending"] == 1
    assert recovered.bases("owner")["knowledge_bases"] == []
    monkeypatch.setattr(module.shutil, "rmtree", real_remove)
    assert recovered.cleanup("owner")["cleanup"][0]["status"] == "done"


def test_parser_timeout_is_recorded(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    kb = store.create("owner", "Redis")["knowledge_base_id"]

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("parser", 15)

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert store.import_file("owner", kb, "a.txt", b"data", {})["error"] == "parse_timeout"


def test_display_name_separate_from_format_and_failed_cleanup_dedup(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    first = store.import_file("owner", kb, "Redis basics", b"one", {"original_name": "one.txt"}, format_suffix=".txt")
    update = store.import_file("owner", kb, "renamed-source.txt", b"two", {}, document_id=first["document_id"], expected_revision=1)
    assert update["status"] == "ready"
    assert store.document("owner", first["document_id"])["name"] == "Redis basics"
    import knowledge_base_extension.store as module

    monkeypatch.setattr(module.shutil, "rmtree", lambda p: (_ for _ in ()).throw(OSError("busy")))
    preview = store.deletion_preview("owner", "document", first["document_id"])
    assert store.delete("owner", "document", first["document_id"], preview["digest"])["cleanup_pending"] == 2
    replacement = store.import_file("owner", kb, "replacement.txt", b"one", {})
    assert replacement["document_id"] != first["document_id"] and replacement["status"] == "ready"


def test_cleanup_action_is_owner_scoped(tmp_path, monkeypatch):
    import knowledge_base_extension.store as module

    store = KnowledgeStore(tmp_path)
    bases = [store.create(owner, "Redis")["knowledge_base_id"] for owner in ("owner", "other")]
    docs = [store.import_file(owner, kb, "a.txt", b"content", {}) for owner, kb in zip(("owner", "other"), bases, strict=True)]
    original = module.shutil.rmtree
    monkeypatch.setattr(module.shutil, "rmtree", lambda p: (_ for _ in ()).throw(OSError("busy")))
    for owner, doc in zip(("owner", "other"), docs, strict=True):
        preview = store.deletion_preview(owner, "document", doc["document_id"])
        assert store.delete(owner, "document", doc["document_id"], preview["digest"])["cleanup_pending"] == 1
    monkeypatch.setattr(module.shutil, "rmtree", original)
    assert store.cleanup("owner")["cleanup"][0]["status"] == "done"
    assert not (tmp_path / "objects" / docs[0]["version_id"]).exists()
    assert (tmp_path / "objects" / docs[1]["version_id"]).is_dir()


def test_failed_pdf_update_preserves_available_version(tmp_path):
    store = KnowledgeStore(tmp_path, parser_dependencies=os.environ.get("KNOWLEDGE_PARSER_DEPS"))
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    first = store.import_file("owner", kb, "a.pdf", synthetic_pdf(["Version one"]), {})
    result = store.import_file("owner", kb, "a.pdf", b"broken", {}, document_id=first["document_id"], expected_revision=1)
    assert result["status"] == "failed"
    assert "Version one" in store.content("owner", first["document_id"])["pages"][0]["text"]
    listed = store.documents("owner", kb)["documents"][0]
    assert listed["status"] == "failed" and listed["version_id"] == result["version_id"]
    assert listed["current_version_id"] == first["version_id"]


def test_versions_dedup_ownership_and_restart(tmp_path):
    store = KnowledgeStore(tmp_path)
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    first = store.import_file("owner", kb, "note.md", b"# Redis\nData", {"type": "upload"})
    doc = first["document_id"]
    assert first["status"] == "ready"
    assert store.import_file("owner", kb, "different.md", b"# Redis\nData", {"type": "upload"})["status"] == "duplicate"
    with pytest.raises(KnowledgeError, match="name_conflict"):
        store.import_file("owner", kb, "note.md", b"other", {"type": "upload"})
    updated = store.import_file("owner", kb, "note.md", b"new", {"type": "upload"}, document_id=doc, expected_revision=1)
    assert updated["document_id"] == doc and updated["version_id"] != first["version_id"]
    failed = store.import_file("owner", kb, "note.md", b"\xff", {"type": "upload"}, document_id=doc, expected_revision=2)
    assert failed["status"] == "failed"
    restarted = KnowledgeStore(tmp_path)
    assert restarted.content("owner", doc)["pages"][0]["text"] == "new"
    assert len(restarted.document("owner", doc)["versions"]) == 3
    with pytest.raises(KnowledgeError, match="not_found"):
        restarted.document("other", doc)


def test_concurrent_duplicate(tmp_path):
    store = KnowledgeStore(tmp_path)
    kb = store.create("owner", "Redis")["knowledge_base_id"]

    def run(_):
        return KnowledgeStore(tmp_path).import_file("owner", kb, "a.txt", b"same", {"type": "upload"})

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert sorted(r["status"] for r in results) == ["duplicate", "duplicate", "ready"]
    assert len(store.documents("owner", kb)["documents"]) == 1


def test_delete_is_snapshot_bound_and_does_not_touch_source(tmp_path):
    original = tmp_path / "original.txt"
    original.write_bytes(b"source")
    store = KnowledgeStore(tmp_path / "store")
    kb = store.create("owner", "Redis")["knowledge_base_id"]
    result = store.import_file("owner", kb, "original.txt", original.read_bytes(), {"type": "windows", "actual_path": str(original)})
    doc = result["document_id"]
    preview = store.deletion_preview("owner", "document", doc)
    store.rename_document("owner", doc, "renamed.txt")
    with pytest.raises(KnowledgeError, match="stale_confirmation"):
        store.delete("owner", "document", doc, preview["digest"])
    preview = store.deletion_preview("owner", "document", doc)
    assert store.delete("owner", "document", doc, preview["digest"])["status"] == "deleted"
    assert original.read_bytes() == b"source"
    assert store.documents("owner", kb)["documents"] == []
    with pytest.raises(KnowledgeError, match="not_found"):
        store.content("owner", doc)
    assert not list((tmp_path / "store" / "objects").glob("*/*"))
