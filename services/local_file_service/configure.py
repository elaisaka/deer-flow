"""Generate private configs without printing credentials or changing existing files."""

import argparse
import json
import secrets
from pathlib import Path

from .policy import FilePolicy, Settings


def mcp_entry(settings: Settings) -> dict:
    return {
        "enabled": True,
        "type": "stdio",
        "command": "/app/backend/.venv/bin/python",
        "args": ["/opt/deerflow-local-files/services/local_file_service/run_bridge.py"],
        "env": {
            "LOCAL_FILE_SERVICE_URL": f"http://host.docker.internal:{settings.port}",
            "LOCAL_FILE_SERVICE_TOKEN": settings.token,
        },
        "tool_name_prefix": True,
        "session_init_timeout": 20,
        "tool_call_timeout": 180,
        "description": "Windows 授权目录工具；调用 get_roots 获取实际目录，仅以 verified=true 的执行结果报告成功。服务断开时说明连接错误，不猜测云端或桌面能力。",
        "routing": {
            "mode": "prefer",
            "priority": 100,
            "keywords": [
                "授权目录",
                "新建文件夹",
                "Windows",
                "test-folder",
                "桌面",
                "文件整理",
                "分类",
                "重命名",
                "预览",
                "撤销",
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="Existing authorized Windows directory")
    parser.add_argument("--root-id", default="study")
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path, default=Path(".local-file-service"))
    args = parser.parse_args()
    config = {
        "host": args.host,
        "port": args.port,
        "token": secrets.token_urlsafe(48),
        "roots": {args.root_id: str(args.root.absolute())},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    config_path = args.output / "config.json"
    entry_path = args.output / "mcp-server.json"
    if config_path.exists() or entry_path.exists():
        parser.exit(
            1,
            "Private config already exists; edit it explicitly or choose another output directory.\n",
        )
    try:
        # Exclusive creation: concurrent setup never replaces an existing token.
        with config_path.open("x", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
        settings = Settings.load(config_path)
        FilePolicy(settings).roots()
        with entry_path.open("x", encoding="utf-8") as stream:
            json.dump(
                {"windows-local-files": mcp_entry(settings)},
                stream,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        parser.exit(
            1,
            "Setup failed; verify Windows root/config. No existing config was overwritten.\n",
        )
    print("Private config and MCP entry created. Credentials were not printed. See docs/LOCAL_FILE_SERVICE.md.")


if __name__ == "__main__":
    main()
