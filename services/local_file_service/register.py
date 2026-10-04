"""Merge only this MCP server using the Gateway's established locking/write contracts."""

import argparse
import json
import subprocess
from pathlib import Path

# Executed in the already-running Gateway, where its mounted config and dependencies live.
REGISTER = """
import json, sys
from pathlib import Path
from deerflow.config.extensions_config import (
    ExtensionsConfig, atomic_write_extensions_config,
    extensions_config_file_lock, extensions_config_write_lock,
)
entry = json.load(sys.stdin)
path = ExtensionsConfig.resolve_config_path()
if path is None:
    raise SystemExit('Gateway extensions config is unavailable')
with extensions_config_write_lock, extensions_config_file_lock(Path(path)):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    servers = data.setdefault('mcpServers', {})
    if 'windows-local-files' in servers:
        raise SystemExit('Server already exists; edit/disable it explicitly instead of replacing it')
    servers.update(entry)
    atomic_write_extensions_config(Path(path), data)
print('Windows local file MCP registered; existing config preserved. Open a new chat.')
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", type=Path, default=Path(".local-file-service/mcp-server.json"))
    parser.add_argument("--container", default="deer-flow-gateway")
    args = parser.parse_args()
    try:
        entry = json.loads(args.entry.read_text(encoding="utf-8-sig"))
        if set(entry) != {"windows-local-files"}:
            raise ValueError("Unexpected entry")
        # No shell interpolation; credentials travel over stdin, not command line/log output.
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                args.container,
                "/app/backend/.venv/bin/python",
                "-c",
                REGISTER,
            ],
            input=json.dumps(entry),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except (OSError, ValueError):
        parser.exit(1, "Cannot load private entry or connect to Docker.\n")
    if result.returncode:
        # Dependency exceptions must not dump the input configuration/credentials.
        parser.exit(
            1,
            "Registration failed (server may already exist). Check mounted config and Gateway runtime.\n",
        )
    print("Windows local file MCP registered; other configuration preserved. Open a new chat.")


if __name__ == "__main__":
    main()
