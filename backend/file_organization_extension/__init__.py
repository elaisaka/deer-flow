"""Small trusted browser confirmation extension; no Agent or Gateway core imports."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from deerflow_extension_api import extension
from deerflow_extension_api.auth import resolve_principal
from deerflow_extension_api.plugins import (
    BackendAction,
    BrowserAssets,
    PluginContribution,
)
from fastapi import APIRouter, HTTPException, Request


class ApprovalClient:
    def __init__(self, credentials, *, transport=None):
        self.url = credentials["url"].rstrip("/")
        endpoint = urlparse(self.url)
        if endpoint.scheme != "http" or endpoint.hostname not in {"host.docker.internal", "127.0.0.1"} or endpoint.path or endpoint.query or endpoint.username:
            raise ValueError("Configure only the fixed local Windows service endpoint")
        self.token = credentials["token"]
        if "approval_token" in credentials:
            raise ValueError("Never mount human approval credentials into an Agent-capable Gateway")
        self.transport = transport

    async def call(self, action, payload, *, approval_code=None):
        if action not in {"list", "get", "confirm", "execute"}:
            raise ValueError("Unknown action")
        headers = {"Authorization": f"Bearer {self.token}"}
        if action == "confirm":
            if not isinstance(approval_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", approval_code):
                return {
                    "ok": False,
                    "verified": False,
                    "error": {"code": "human_approval_code_required"},
                }
            headers["X-File-Approval-Token"] = approval_code
        try:
            async with httpx.AsyncClient(timeout=120, trust_env=False, transport=self.transport) as client:
                response = await client.post(
                    f"{self.url}/v1/organization/{action}",
                    json=payload,
                    headers=headers,
                )
                result = response.json()
                if not isinstance(result, dict) or "ok" not in result:
                    raise ValueError("Invalid service result")
                return result
        except (httpx.HTTPError, ValueError):
            # No ambiguous request retries; caller queries the durable record.
            return {
                "ok": False,
                "verified": False,
                "error": {"code": "service_unavailable"},
            }

    def handler(self, action):
        async def handle(payload, context):
            if not context.principal.is_admin or context.principal.is_internal:
                raise PermissionError("The personal file service requires its administrator")
            if set(payload) != ({"plan_id"} if action == "get" else set()):
                raise ValueError("Invalid arguments")
            return await self.call(action, dict(payload))

        return handle


def confirmation_router(client, origins):
    router = APIRouter()

    @router.post("/api/file-organization/confirm")
    async def confirm(request: Request):
        principal = resolve_principal(request)
        # These attributes are stamped by existing host authentication middleware.
        # Internal run credentials, PATs and AUTH_DISABLED are never human approval.
        if getattr(request.state, "auth_source", None) != "session" or principal is None or not principal.is_admin or principal.is_internal or request.headers.get("origin") not in origins:
            raise HTTPException(403, "An authenticated administrator browser session is required")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2048:
                raise HTTPException(413, "Request too large")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"plan_id", "version", "digest", "approval_code"}
            or not isinstance(payload["plan_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", payload["plan_id"])
            or type(payload["version"]) is not int
            or payload["version"] < 1
            or not isinstance(payload["digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", payload["digest"])
        ):
            raise HTTPException(400, "Invalid plan version/digest")
        code = payload.pop("approval_code")
        result = await client.call("confirm", {**payload, "actor": principal.user_id}, approval_code=code)
        if not result.get("ok"):
            return result
        return await client.call("execute", {"plan_id": payload["plan_id"], "version": payload["version"]})

    return router


@extension(api="0.2.3", name="file-organization")
def install(registry, config):
    if config.get("enabled") is not True:
        return
    path = Path(config.get("credentials_path", ""))
    if not path.is_absolute():
        raise ValueError("Configure an absolute private credentials_path")
    client = ApprovalClient(json.loads(path.read_text(encoding="utf-8-sig")))
    origins = config.get("origins", ["http://localhost:2026", "http://127.0.0.1:2026"])
    if not isinstance(origins, list) or any(not isinstance(origin, str) for origin in origins):
        raise ValueError("Configure explicit browser origins")
    if (
        registry.plugin(
            PluginContribution(
                namespace="personal.file-organization",
                title="文件整理",
                description="查看确定性文件整理方案，由登录用户确认具体版本后执行。",
                enabled=True,
                frontend=BrowserAssets("file-organization.v1", Path(__file__).parent),
                backend=tuple(BackendAction(action, client.handler(action)) for action in ("list", "get")),
            )
        )
        is not True
    ):
        raise RuntimeError("The full-stack plugin host is required")
    registry.routers([confirmation_router(client, set(origins))])
