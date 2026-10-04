"""Create phase-two private authority outside the repository and authorized roots."""

import argparse
import json
import os
import secrets
from pathlib import Path

from .configure import mcp_entry
from .policy import FilePolicy, Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-config", type=Path, default=Path(".local-file-service/config.json"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", "")) / "DeerFlow" / "local-files-phase2",
    )
    args = parser.parse_args()
    try:
        original = Settings.load(args.service_config)
        FilePolicy(original).roots()
        output = args.output.absolute()
        project = Path(__file__).resolve().parents[2]
        if output.is_relative_to(project) or any(output.is_relative_to(root) for root in original.roots.values()):
            raise ValueError("Approval credentials must be outside the repository and authorized roots")
        output.mkdir(parents=True, exist_ok=True)
        paths = [
            output / name
            for name in (
                "service-config.json",
                "approval-client.json",
                "mcp-server.json",
                "approval-code.txt",
            )
        ]
        if any(path.exists() for path in paths):
            raise ValueError("Private configuration already exists; preserve it or use a different output")
        data = json.loads(args.service_config.read_text(encoding="utf-8-sig"))
        data["organization"] = {
            "approval_token": secrets.token_urlsafe(48),
            "state_dir": str(output / "organization"),
        }
        client = {
            "url": f"http://host.docker.internal:{original.port}",
            "token": original.token,
        }
        for path, document in zip(
            paths[:3],
            [data, client, {"windows-local-files": mcp_entry(original)}],
            strict=True,
        ):
            with path.open("x", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, indent=2)
        with paths[3].open("x", encoding="utf-8") as stream:
            stream.write(data["organization"]["approval_token"])
    except (ValueError, OSError):
        parser.exit(
            1,
            "Setup failed. Check existing private configs, output location and authorized roots; no existing files were replaced.\n",
        )
    print(f"Private phase-two configuration created at {output}. No credentials printed. See docs/FILE_ORGANIZATION.md.")


if __name__ == "__main__":
    main()
