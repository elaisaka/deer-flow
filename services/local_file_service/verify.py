"""Exercise the real DeerFlow MCP loader inside Docker, then verify on Windows."""

import argparse
import json
import subprocess
from pathlib import Path

PROBE = """
import asyncio, json, sys
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.mcp.tools import get_mcp_tools
async def main():
    request = json.load(sys.stdin)
    config = ExtensionsConfig(mcpServers=request['entry'])
    tools = await get_mcp_tools(config)
    results = {}
    for name, arguments in request['calls']:
        tool = next(t for t in tools if t.name.endswith(name))
        results[name] = await tool.ainvoke(arguments)
    print(json.dumps(results, ensure_ascii=False, default=str))
asyncio.run(main())
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", type=Path, default=Path(".local-file-service/mcp-server.json"))
    parser.add_argument("--container", default="deer-flow-gateway")
    parser.add_argument("--root-id", default="study")
    parser.add_argument("--create", help="Explicitly create a folder in the configured authorized root")
    args = parser.parse_args()
    calls = [("local_files_get_roots", {})]
    if args.create:
        calls.append(
            (
                "local_files_create_folder",
                {"root_id": args.root_id, "relative_path": args.create},
            )
        )
    payload = {
        "entry": json.loads(args.entry.read_text(encoding="utf-8-sig")),
        "calls": calls,
    }
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            args.container,
            "/app/backend/.venv/bin/python",
            "-c",
            PROBE,
        ],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode:
        parser.exit(
            1,
            "MCP bridge probe failed; check overlay mount and Gateway dependencies. Credentials suppressed.\n",
        )
    raw = json.loads(result.stdout)
    results = {name: json.loads(next(block["text"] for block in blocks if block["type"] == "text")) for name, blocks in raw.items()}
    if args.create:
        created = results["local_files_create_folder"]
        if created.get("ok") and created.get("verified"):
            created["windows_directory_exists"] = Path(created["actual_path"]).is_dir()
            if not created["windows_directory_exists"]:
                parser.exit(1, "Returned directory does not exist on Windows.\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not all(result.get("ok") for result in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
