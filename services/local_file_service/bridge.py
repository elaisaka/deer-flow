"""A static stdio MCP catalog in Docker; all filesystem execution stays on Windows."""

import os

import httpx
from mcp.server.fastmcp import FastMCP

from .server_errors import failure


class FileServiceClient:
    def __init__(self, url: str, token: str, *, transport=None):
        self.url, self.token, self.transport = url.rstrip("/"), token, transport

    async def call(self, operation: str, arguments: dict | None = None) -> dict:
        if not self.token:
            return failure(
                "authentication_not_configured",
                "Configure the local file service token; no operation was executed.",
            )
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False, transport=self.transport) as client:
                response = await client.request(
                    "GET" if arguments is None else "POST",
                    f"{self.url}/v1/{operation}",
                    json=arguments,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            if response.status_code in (401, 403):
                return failure(
                    "authentication_failed",
                    "Windows service rejected identity; check matching private tokens.",
                )
            result = response.json()
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                return failure(
                    "invalid_service_response",
                    "Windows service returned an invalid result; success is unverified.",
                )
            return result
        except httpx.RequestError:
            return failure(
                "service_unavailable",
                "Cannot connect to the Windows local file service. Start it and check Docker host connectivity/firewall. Execution outcome is unverified; retry the same folder safely after reconnecting.",
            )
        except ValueError:
            return failure(
                "invalid_service_response",
                "Windows service returned an invalid result; success is unverified.",
            )


def build_mcp(client: FileServiceClient) -> FastMCP:
    server = FastMCP(
        "windows-local-files",
        instructions=(
            "Use these tools for Windows authorized folders. First get roots; never guess desktop paths or execution location. "
            "Declare success only when ok=true and verified=true; show actual_path and status. On errors report error.code; never substitute sandbox shell commands."
        ),
    )

    @server.tool()
    async def local_files_get_roots() -> dict:
        """Get verified Windows authorized root IDs, actual paths, and limited capabilities. Use before file operations; desktop is allowed only if explicitly listed."""
        return await client.call("roots")

    @server.tool()
    async def local_files_list_directory(root_id: str, relative_path: str = "") -> dict:
        """List files/folders in a Windows authorized root. Use a returned root_id and a relative path (empty means root). Links are blocked; errors do not imply cloud execution."""
        return await client.call("list-directory", {"root_id": root_id, "relative_path": relative_path})

    @server.tool()
    async def local_files_create_folder(root_id: str, relative_path: str) -> dict:
        """Really create one Windows folder under a returned authorized root_id.

        Relative path only; parent must exist. Only ok=true AND verified=true establish success:
        report actual_path and created/already_exists. On errors explain the code;
        never claim creation or run sandbox Shell instead.
        """
        return await client.call("create-folder", {"root_id": root_id, "relative_path": relative_path})

    return server


def main():
    build_mcp(
        FileServiceClient(
            os.environ.get("LOCAL_FILE_SERVICE_URL", "http://host.docker.internal:8765"),
            os.environ.get("LOCAL_FILE_SERVICE_TOKEN", ""),
        )
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
