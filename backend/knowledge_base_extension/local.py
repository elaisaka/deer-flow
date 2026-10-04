"""Fixed Windows import transport; never return raw transfer bytes to the model."""

import base64
import hashlib
import re
from urllib.parse import urlparse

import httpx

from .store import MAX_BYTES, KnowledgeError


class LocalClient:
    def __init__(self, credentials, transport=None):
        self.url = credentials["url"].rstrip("/")
        url = urlparse(self.url)
        if url.scheme != "http" or url.hostname not in {"host.docker.internal", "127.0.0.1"} or url.path or url.query or url.username or "approval_token" in credentials:
            raise ValueError("Only a fixed local endpoint and operation credentials are allowed")
        self.token = credentials["token"]
        self.transport = transport

    async def call(self, path, payload=None, code=None):
        if path not in {"/v1/roots", "/v1/list-directory", "/v1/import/read", "/v1/knowledge/confirm"}:
            raise KnowledgeError("invalid_action")
        headers = {"Authorization": f"Bearer {self.token}"}
        if path.endswith("confirm"):
            if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", code):
                raise KnowledgeError("human_approval_code_required")
            headers["X-File-Approval-Token"] = code
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False, transport=self.transport) as client:
                async with client.stream("GET" if path == "/v1/roots" else "POST", self.url + path, json=payload, headers=headers) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES * 4 // 3 + 8192:
                            raise KnowledgeError("transfer_size_limit")
                    import json

                    data = json.loads(content)
            if not isinstance(data, dict) or not data.get("ok"):
                raise KnowledgeError(data.get("error", {}).get("code", "invalid_service_response") if isinstance(data, dict) else "invalid_service_response")
            return data
        except (httpx.HTTPError, ValueError):
            raise KnowledgeError("service_unavailable") from None

    async def read(self, root_id, relative_path):
        result = await self.call("/v1/import/read", {"root_id": root_id, "relative_path": relative_path})
        try:
            data = base64.b64decode(result["content_base64"], validate=True)
            if result.get("verified") is not True or len(data) > MAX_BYTES or len(data) != result["size"] or hashlib.sha256(data).hexdigest() != result["sha256"]:
                raise ValueError("Invalid transfer")
            return result["name"], data, {"type": "windows", "root_id": root_id, "relative_path": relative_path, "actual_path": result["actual_path"], "snapshot_sha256": result["sha256"]}
        except (ValueError, KeyError, TypeError):
            raise KnowledgeError("invalid_service_response") from None
