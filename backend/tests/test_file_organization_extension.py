"""The confirmation authority is admitted only through an authenticated browser session."""

from types import SimpleNamespace

import httpx
import pytest
from deerflow_extension_api.auth import ExtensionPrincipal
from fastapi import FastAPI
from fastapi.testclient import TestClient

from file_organization_extension import ApprovalClient, confirmation_router


@pytest.mark.parametrize(
    "source,admin,origin,expected",
    [
        ("session", True, "http://localhost:2026", 200),
        ("pat", True, "http://localhost:2026", 403),
        ("internal", True, "http://localhost:2026", 403),
        ("auth_disabled", True, "http://localhost:2026", 403),
        ("session", False, "http://localhost:2026", 403),
        ("session", True, "https://evil.example", 403),
        ("session", True, None, 403),
    ],
)
def test_confirmation_requires_session_admin_origin(source, admin, origin, expected):
    requests = []

    def service(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "status": "confirmed" if request.url.path.endswith("confirm") else "completed",
            },
        )

    client = ApprovalClient(
        {"url": "http://host.docker.internal:8765", "token": "operation"},
        transport=httpx.MockTransport(service),
    )
    app = FastAPI()
    app.state.deerflow_extension_principal_resolver = lambda request: ExtensionPrincipal("real-owner", is_admin=admin)

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_source = source
        return await call_next(request)

    app.include_router(confirmation_router(client, {"http://localhost:2026"}))
    with TestClient(app) as browser:
        response = browser.post(
            "/api/file-organization/confirm",
            headers={"Origin": origin} if origin else {},
            json={
                "plan_id": "a" * 32,
                "version": 1,
                "digest": "b" * 64,
                "approval_code": "human-" + "c" * 40,
            },
        )
    assert response.status_code == expected
    if expected == 200:
        import json

        assert json.loads(requests[0].content)["actor"] == "real-owner"
        assert requests[0].headers["X-File-Approval-Token"] == "human-" + "c" * 40
        assert "approval_code" not in json.loads(requests[0].content)
        assert "X-File-Approval-Token" not in requests[1].headers
    else:
        assert requests == []


@pytest.mark.asyncio
async def test_service_unavailable_has_no_success_claim():
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    client = ApprovalClient(
        {"url": "http://host.docker.internal:8765", "token": "operation"},
        transport=httpx.MockTransport(offline),
    )
    result = await client.call("get", {"plan_id": "a" * 32})
    assert result == {
        "ok": False,
        "verified": False,
        "error": {"code": "service_unavailable"},
    }


@pytest.mark.asyncio
async def test_backend_action_does_not_confer_confirmation_authority():
    client = ApprovalClient({"url": "http://host.docker.internal:8765", "token": "operation"})
    context = SimpleNamespace(principal=ExtensionPrincipal("model", is_admin=False))
    with pytest.raises(PermissionError):
        await client.handler("list")({}, context)


def test_credentials_cannot_contain_approval_authority():
    with pytest.raises(ValueError, match="Never mount human approval"):
        ApprovalClient({"url": "http://host.docker.internal:8765", "token": "operation", "approval_token": "do-not-mount"})


@pytest.mark.asyncio
async def test_model_confirmation_without_human_code_is_denied():
    requests = []

    def service(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = ApprovalClient({"url": "http://host.docker.internal:8765", "token": "operation"}, transport=httpx.MockTransport(service))
    result = await client.call("confirm", {"plan_id": "a" * 32, "version": 1, "digest": "b" * 64})
    assert result["error"]["code"] == "human_approval_code_required" and requests == []


@pytest.mark.parametrize("csrf", [None, "wrong", "correct"])
def test_confirmation_uses_real_host_csrf_boundary(monkeypatch, csrf):
    import app.gateway.csrf_middleware as host_csrf

    monkeypatch.setattr(host_csrf, "is_auth_disabled", lambda: False)
    requests = []

    def service(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "status": "completed"})

    client = ApprovalClient({"url": "http://host.docker.internal:8765", "token": "operation"}, transport=httpx.MockTransport(service))
    app = FastAPI()
    app.state.deerflow_extension_principal_resolver = lambda request: ExtensionPrincipal("owner", is_admin=True)

    @app.middleware("http")
    async def session(request, call_next):
        request.state.auth_source = "session"
        return await call_next(request)

    app.add_middleware(host_csrf.CSRFMiddleware)
    app.include_router(confirmation_router(client, {"http://localhost:2026"}))
    headers = {"Origin": "http://localhost:2026"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    with TestClient(app) as browser:
        browser.cookies.set("csrf_token", "correct")
        response = browser.post("/api/file-organization/confirm", headers=headers, json={"plan_id": "a" * 32, "version": 1, "digest": "b" * 64, "approval_code": "human-" + "c" * 40})
    assert response.status_code == (200 if csrf == "correct" else 403)
    assert len(requests) == (2 if csrf == "correct" else 0)
