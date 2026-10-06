"""Prepare public synthetic demo files in a new absolute directory, without service calls."""

import argparse
import hashlib
import json
from pathlib import Path

from .evaluate import FIXTURES
from .fixtures import synthetic_pdf


def prepare(output):
    output = Path(output)
    if not output.is_absolute():
        raise ValueError("Use an absolute dedicated test directory")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Use an empty directory; existing data is never replaced")
    dataset = json.loads((FIXTURES / "questions.json").read_text(encoding="utf-8"))
    files = {p.name: p.read_bytes() for p in FIXTURES.iterdir() if p.suffix in {".md", ".txt", ".json"}}
    files["redis.pdf"] = synthetic_pdf(["Synthetic demo: Redis keys and values.", "Synthetic demo: TTL -1 means no expiry; TTL -2 means missing key."])
    manifest = {
        "dataset": dataset["version"],
        "synthetic_only": True,
        "acceptance_executor": "user_or_explicitly_authorized_test_agent",
        "deliberately_untrusted": ["conflict.md", "injection.md"],
        "initial_import": ["rdb.md", "aof.md", "keys.md", "types.md", "operations.md", "versioned.txt", "redis.pdf"],
        "evaluation_only": ["conflict.md", "injection.md", "questions.json", "learning-cases.json"],
        "update_only": ["versioned-update.txt"],
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        "human_actions_required": ["file_execution_confirmation", "learning_answers", "note_import_approval", "mistake_collection", "review_self_assessment", "document_update_or_delete"],
    }
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    for name, data in sorted(files.items()):
        with (output / name).open("xb") as stream:
            stream.write(data)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = prepare(args.output)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"output": str(args.output), "dataset": manifest["dataset"], "files": len(manifest["sha256"]), "service_calls": 0}))


if __name__ == "__main__":
    main()
