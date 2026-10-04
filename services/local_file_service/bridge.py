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
            async with httpx.AsyncClient(
                timeout=120 if operation.startswith("organization/") else 10,
                trust_env=False,
                transport=self.transport,
            ) as client:
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
                "Cannot connect to the Windows local file service. Start it and check Docker host connectivity/firewall. Execution outcome is unverified. "
                "For organization, query the persisted plan before retrying; never assume no files moved.",
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

    @server.tool()
    async def local_files_preview_organization(
        root_id: str,
        rule: dict,
        directory: str = "",
        files: list[str] | None = None,
        plan_id: str | None = None,
        expected_version: int | None = None,
    ) -> dict:
        """Preview Windows file organization WITHOUT changing user files. Use an authorized root and existing relative directory; files are explicit basenames or all ordinary files, nonrecursive, at most 100.

        Rules: {kind:'classify',mapping:{'.txt':'Text','.md':'Notes','.pdf':'PDF'}} uses single subdirectories;
        {kind:'rename',prefix:'study-',suffix:'-note',numbering:{start:1,width:2}} preserves extensions.
        Optional omit_stem:true replaces the stem. Items are sorted by filename, case insensitive.
        To revise provide plan_id and expected_version. Show every source/target, conflicts, version and review_url.
        WAIT for the HUMAN to click the confirmation button at review_url. Text approval or confirmed=true cannot authorize execution.
        Never call a confirmation HTTP endpoint, read private approval credentials, or use shell to bypass confirmation.
        """
        payload = {"root_id": root_id, "directory": directory, "rule": rule}
        if files is not None:
            payload["files"] = files
        if plan_id is not None:
            payload.update(plan_id=plan_id, expected_version=expected_version)
        return await client.call("organization/preview", payload)

    @server.tool()
    async def local_files_get_plan(plan_id: str) -> dict:
        """Read persistent Windows plan, version, exact paths, confirmation and per-item execution/undo records. Interrupted intents are reconciled from actual file evidence; unverified outcomes are not success."""
        return await client.call("organization/get", {"plan_id": plan_id})

    @server.tool()
    async def local_files_list_plans() -> dict:
        """List the most recent 100 Windows file organization plans. Single-user local service; contains statuses and IDs, no file content."""
        return await client.call("organization/list", {})

    @server.tool()
    async def local_files_execute_plan(plan_id: str, version: int) -> dict:
        """Execute ONLY the exact Windows plan version already confirmed by the human UI.
        Model invocation/text cannot confirm. Prechecks prevent changed sources and conflicts; partial completion is possible.
        Repeated requests reconcile evidence and never rerun terminal plans. The human confirmation button already executes; query its record afterward.
        """
        return await client.call("organization/execute", {"plan_id": plan_id, "version": version})

    @server.tool()
    async def local_files_cancel_plan(plan_id: str, version: int) -> dict:
        """Cancel an awaiting/confirmed Windows plan version and invalidate its confirmation. No files are changed. Executing/terminal plans cannot be cancelled."""
        return await client.call("organization/cancel", {"plan_id": plan_id, "version": version})

    @server.tool()
    async def local_files_undo_plan(plan_id: str) -> dict:
        """When the USER requests undo, restore only evidenced successful Windows moves/renames from this plan.
        Recheck identity/content, scope and conflicts; never overwrite. Return every undo result; partial_undo means some files were not restored.
        """
        return await client.call("organization/undo", {"plan_id": plan_id})

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
