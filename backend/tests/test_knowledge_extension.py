"""Browser-only authority, actual host CSRF, owner-bound tools and validated transport."""

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from deerflow_extension_api.auth import ExtensionPrincipal
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_base_extension import KnowledgeService, browser_router, install
from knowledge_base_extension.local import LocalClient
from knowledge_base_extension.store import KnowledgeStore

ORIGIN = "http://localhost:2026"
HUMAN = "human-" + "b" * 40


def service(tmp_path, transport=None):
    return KnowledgeService(KnowledgeStore(tmp_path), LocalClient({"url": "http://host.docker.internal:8765", "token": "operation"}, transport))


def app_for(service, monkeypatch, *, source="session", principal=None):
    import app.gateway.csrf_middleware as csrf

    monkeypatch.setattr(csrf, "is_auth_disabled", lambda: False)
    app = FastAPI()
    app.state.deerflow_extension_principal_resolver = lambda request: principal or ExtensionPrincipal("owner", is_admin=True)

    @app.middleware("http")
    async def session(request, call_next):
        request.state.auth_source = source
        return await call_next(request)

    app.add_middleware(csrf.CSRFMiddleware)
    app.include_router(browser_router(service, {ORIGIN}))
    return app


@pytest.mark.parametrize(
    "source,csrf,origin,expected",
    [
        ("session", "correct", ORIGIN, "deleted"),
        ("pat", "correct", ORIGIN, 403),
        ("internal", "correct", ORIGIN, 403),
        ("auth_disabled", "correct", ORIGIN, 403),
        ("session", None, ORIGIN, 403),
        ("session", "wrong", ORIGIN, 403),
        ("session", "correct", "https://evil.example", 403),
        ("session", "correct", "", 403),
    ],
)
def test_deletion_requires_browser_confirmation_without_windows_key(tmp_path, monkeypatch, source, csrf, origin, expected):
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ConnectError("Windows service is unavailable", request=request)

    s = service(tmp_path, httpx.MockTransport(unavailable))
    kb = s.store.create("owner", "Redis")["knowledge_base_id"]
    preview = s.store.deletion_preview("owner", "knowledge_base", kb)
    with TestClient(app_for(s, monkeypatch, source=source)) as browser:
        browser.cookies.set("csrf_token", "correct")
        headers = {"Origin": origin}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        response = browser.post("/api/personal-knowledge/delete", headers=headers, json={"kind": "knowledge_base", "target": kb, "digest": preview["digest"]})
    if isinstance(expected, int):
        assert response.status_code == expected
    elif expected == "deleted":
        assert response.json()["status"] == "deleted"
    else:
        assert response.json()["error"]["code"] == expected
    if expected != "deleted":
        assert len(s.store.bases("owner")["knowledge_bases"]) == 1 and not requests
    assert not requests


def test_deletion_preserves_ownership_snapshot_and_request_shape(tmp_path, monkeypatch):
    s = service(tmp_path)
    kb = s.store.create("owner", "Redis")["knowledge_base_id"]
    doc = s.store.import_file("owner", kb, "a.txt", b"Synthetic", {"type": "upload"})["document_id"]
    preview = s.store.deletion_preview("owner", "document", doc)
    payload = {"kind": "document", "target": doc, "digest": preview["digest"]}
    headers = {"Origin": ORIGIN, "X-CSRF-Token": "correct"}
    with TestClient(app_for(s, monkeypatch, principal=ExtensionPrincipal("other", is_admin=True))) as browser:
        browser.cookies.set("csrf_token", "correct")
        assert browser.post("/api/personal-knowledge/delete", headers=headers, json=payload).json()["error"]["code"] == "not_found"
    with TestClient(app_for(s, monkeypatch, principal=ExtensionPrincipal("owner"))) as browser:
        browser.cookies.set("csrf_token", "correct")
        assert browser.post("/api/personal-knowledge/delete", headers=headers, json=payload).status_code == 403
    with TestClient(app_for(s, monkeypatch)) as browser:
        browser.cookies.set("csrf_token", "correct")
        for extra in ({"confirmed": True}, {"approval_code": HUMAN}):
            assert browser.post("/api/personal-knowledge/delete", headers=headers, json={**payload, **extra}).json()["error"]["code"] == "invalid_arguments"
        s.store.rename_document("owner", doc, "renamed.txt")
        assert browser.post("/api/personal-knowledge/delete", headers=headers, json=payload).json()["error"]["code"] == "stale_confirmation"
        payload["digest"] = s.store.deletion_preview("owner", "document", doc)["digest"]
        assert browser.post("/api/personal-knowledge/delete", headers=headers, json=payload).json()["status"] == "deleted"
        assert browser.post("/api/personal-knowledge/delete", headers=headers, json=payload).json()["error"]["code"] == "not_found"
    assert not s.store.documents("owner", kb)["documents"]


def test_browser_upload_update_failure_and_wrong_owner(tmp_path, monkeypatch):
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ConnectError("Windows service is unavailable", request=request)

    s = service(tmp_path, httpx.MockTransport(unavailable))
    kb = s.store.create("owner", "Redis")["knowledge_base_id"]
    with TestClient(app_for(s, monkeypatch)) as browser:
        browser.cookies.set("csrf_token", "correct")
        headers = {"Origin": ORIGIN, "X-CSRF-Token": "correct"}
        first = browser.post("/api/personal-knowledge/import", headers=headers, data={"knowledge_base_id": kb}, files={"file": ("a.txt", b"Version one")}).json()
        assert first["status"] == "ready"
        doc = first["document_id"]
        update = browser.post("/api/personal-knowledge/import", headers=headers, data={"knowledge_base_id": kb, "document_id": doc, "expected_revision": "1"}, files={"file": ("a.txt", b"\xff")}).json()
        assert update["status"] == "failed" and s.store.content("owner", doc)["pages"][0]["text"] == "Version one"
        data = {"knowledge_base_id": kb, "document_id": doc, "expected_revision": "2"}
        success = browser.post("/api/personal-knowledge/import", headers=headers, data=data, files={"file": ("new.txt", b"Version two")}).json()
        assert success["status"] == "ready" and success["document_id"] == doc and success["version_id"] != first["version_id"]
        assert s.store.content("owner", doc)["pages"][0]["text"] == "Version two"
        assert s.store.content("owner", doc, first["version_id"])["pages"][0]["text"] == "Version one"
        repeated = browser.post("/api/personal-knowledge/import", headers=headers, data=data, files={"file": ("new.txt", b"Version two")}).json()
        assert repeated["error"]["code"] == "stale_confirmation"
        for extra in ({"confirmed": "true"}, {"approval_code": HUMAN}):
            rejected = browser.post("/api/personal-knowledge/import", headers=headers, data={**data, "expected_revision": "3", **extra}, files={"file": ("a.txt", b"Unconfirmed")}).json()
            assert rejected["error"]["code"] == "invalid_arguments"
        missing = browser.post("/api/personal-knowledge/import", headers=headers, data={"knowledge_base_id": kb, "document_id": doc}, files={"file": ("a.txt", b"Missing revision")}).json()
        assert missing["error"]["code"] == "stale_confirmation"
        assert s.store.document("owner", doc)["revision"] == 3
        other = s.store.create("another", "Other")["knowledge_base_id"]
        result = browser.post("/api/personal-knowledge/import", headers=headers, data={"knowledge_base_id": other}, files={"file": ("a.txt", b"private")}).json()
        assert result["error"]["code"] == "not_found"
        other_doc = s.store.import_file("another", other, "b.txt", b"Other owner", {"type": "upload"})["document_id"]
        denied = browser.post("/api/personal-knowledge/import", headers=headers, data={"knowledge_base_id": other, "document_id": other_doc, "expected_revision": "1"}, files={"file": ("b.txt", b"Unauthorized")}).json()
        assert denied["error"]["code"] == "not_found"
        assert s.store.content("another", other_doc)["pages"][0]["text"] == "Other owner"
        oversized = browser.post("/api/personal-knowledge/import", headers=headers, data={"knowledge_base_id": kb}, files={"file": ("a.txt", b"a" * (10 * 1024 * 1024 + 1))})
        assert oversized.json()["error"]["code"] == "file_size_limit"
    assert not requests


@pytest.mark.parametrize("source,csrf,origin", [("pat", "correct", ORIGIN), ("session", "wrong", ORIGIN), ("session", "correct", "https://evil.example")])
def test_update_requires_authenticated_browser_confirmation(tmp_path, monkeypatch, source, csrf, origin):
    s = service(tmp_path)
    kb = s.store.create("owner", "Redis")["knowledge_base_id"]
    doc = s.store.import_file("owner", kb, "a.txt", b"Original", {"type": "upload"})["document_id"]
    with TestClient(app_for(s, monkeypatch, source=source)) as browser:
        browser.cookies.set("csrf_token", "correct")
        response = browser.post("/api/personal-knowledge/import", headers={"Origin": origin, "X-CSRF-Token": csrf}, data={"knowledge_base_id": kb, "document_id": doc, "expected_revision": "1"}, files={"file": ("a.txt", b"Unauthorized")})
        assert response.status_code == 403
    assert s.store.document("owner", doc)["revision"] == 1 and s.store.content("owner", doc)["pages"][0]["text"] == "Original"


@pytest.mark.asyncio
async def test_model_schema_cannot_delete_confirm_or_update(tmp_path):
    captured = []

    class Registry:
        def plugin(self, contribution):
            captured.append(contribution)
            return True

        def routers(self, routers):
            pass

        def service(self, service):
            pass

    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"url": "http://host.docker.internal:8765", "token": "synthetic"}), encoding="utf-8")
    install(Registry(), {"enabled": True, "data_dir": str(tmp_path / "store"), "credentials_path": str(credentials)})
    contribution = captured[0]
    assert {t.name for t in contribution.tools} == {"knowledge_bases", "knowledge_documents", "knowledge_document", "knowledge_import_local", "knowledge_index_status", "knowledge_search", "knowledge_answer"}
    assert "delete" not in {b.name for b in contribution.backend}
    s = service(tmp_path / "other")
    context = SimpleNamespace(principal=ExtensionPrincipal("owner", is_admin=True))
    assert (await s.handler("import_local")({"knowledge_base_id": "a" * 32, "root_id": "study", "relative_path": "a.txt", "confirmed": True}, context))["error"]["code"] == "invalid_arguments"
    with pytest.raises(PermissionError):
        await s.handler("bases")({}, SimpleNamespace(principal=ExtensionPrincipal("untrusted")))
    with pytest.raises(PermissionError):
        await s.handler("bases")({}, SimpleNamespace(principal=None))


@pytest.mark.asyncio
async def test_local_transport_offline_integrity_and_import_ownership(tmp_path):
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    s = service(tmp_path, httpx.MockTransport(offline))
    context = SimpleNamespace(principal=ExtensionPrincipal("owner", is_admin=True))
    assert (await s.handler("local_roots")({}, context))["error"]["code"] == "service_unavailable"
    kb = s.store.create("other", "Private")["knowledge_base_id"]
    assert (await s.handler("import_local")({"knowledge_base_id": kb, "root_id": "study", "relative_path": "a.txt"}, context))["error"]["code"] == "not_found"

    def corrupt(request):
        return httpx.Response(200, json={"ok": True, "verified": True, "content_base64": "ZGF0YQ==", "sha256": hashlib.sha256(b"other").hexdigest(), "size": 4})

    client = LocalClient({"url": "http://host.docker.internal:8765", "token": "synthetic"}, httpx.MockTransport(corrupt))
    with pytest.raises(Exception, match="invalid_service_response"):
        await client.read("study", "a.txt")


@pytest.mark.asyncio
async def test_actual_tool_node_uses_owner_without_browser_admin_authority(tmp_path):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    from deerflow.extensions.plugin_tools import build_plugin_tools
    from deerflow.extensions.registry import ExtensionRegistry

    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"url": "http://host.docker.internal:8765", "token": "synthetic"}), encoding="utf-8")
    store = KnowledgeStore(tmp_path / "store")
    owned = store.create("trusted-run-owner", "Redis")["knowledge_base_id"]
    other = store.create("other-owner", "Private")["knowledge_base_id"]
    registry = ExtensionRegistry()
    with registry.attributed_to("knowledge-test"):
        install(registry, {"enabled": True, "data_dir": str(tmp_path / "store"), "credentials_path": str(credentials)})
    tools = build_plugin_tools(registry.build())
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    agent = graph.compile()

    async def invoke(declaration, args):
        tool = next(t for t in tools if f"_{declaration}_" in t.name)
        result = await agent.ainvoke({"messages": [AIMessage(content="", tool_calls=[{"id": "test-call", "name": tool.name, "args": args}])]}, context={"user_id": "trusted-run-owner", "thread_id": "synthetic-thread"})
        return json.loads(result["messages"][-1].content)

    assert (await invoke("knowledge_bases", {}))["knowledge_bases"][0]["knowledge_base_id"] == owned
    assert (await invoke("knowledge_documents", {"knowledge_base_id": owned}))["ok"]
    assert (await invoke("knowledge_documents", {"knowledge_base_id": other}))["error"]["code"] == "not_found"
    assert (await invoke("knowledge_import_local", {"knowledge_base_id": other, "root_id": "study", "relative_path": "a.txt"}))["error"]["code"] == "not_found"
