"""Bounded, disposable text extraction worker. Never render HTML or execute links."""

import json
import os
import sys
from pathlib import Path

MAX_TEXT = 4 * 1024 * 1024


def extract(path, suffix):
    if suffix not in {".pdf", ".md", ".txt"}:
        raise ValueError("unsupported_format")
    if not path.stat().st_size:
        raise ValueError("empty_file")
    if suffix == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        if len(reader.pages) > 200:
            raise ValueError("page_limit")
        pages, total = [], 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            total += len(text.encode("utf-8"))
            if total > MAX_TEXT:
                raise ValueError("text_limit")
            pages.append({"page": number, "text": text})
        version = f"pypdf-{pypdf.__version__}/text-v1"
    else:
        text = path.read_bytes().decode("utf-8-sig")
        if len(text.encode("utf-8")) > MAX_TEXT:
            raise ValueError("text_limit")
        if "\x00" in text:
            raise ValueError("invalid_text")
        pages = [{"page": None, "text": text}]
        version = "utf8-text-v1"
    if not any(p["text"].strip() for p in pages):
        raise ValueError("no_effective_text")
    return {"status": "ready", "pages": pages, "parser_version": version, "error": None}


def main():
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
    version = "utf8-text-v1"
    try:
        if sys.argv[2] == ".pdf":
            version = "pypdf-unavailable/text-v1"
            import pypdf

            version = f"pypdf-{pypdf.__version__}/text-v1"
        result = extract(Path(sys.argv[1]), sys.argv[2])
    except ImportError:
        result = {"status": "failed", "error": "parser_dependency_missing", "parser_version": version, "pages": []}
    except UnicodeError:
        result = {"status": "failed", "error": "invalid_utf8", "parser_version": "utf8-text-v1", "pages": []}
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and str(error) in {"unsupported_format", "empty_file", "encrypted_pdf", "page_limit", "text_limit", "invalid_text", "no_effective_text"} else "damaged_document"
        result = {"status": "failed", "error": code, "parser_version": version, "pages": []}
    Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
