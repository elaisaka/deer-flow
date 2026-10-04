"""Independent Windows HTTP service, exposing only three authenticated operations."""

import argparse
import hmac
import json
from pathlib import Path

import anyio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .policy import FilePolicy, PolicyError, Settings


def failure(code: str) -> dict:
    return {"ok": False, "verified": False, "error": {"code": code}}


def create_app(settings: Settings):
    policy = FilePolicy(settings)

    async def execute(request: Request):
        try:
            if request.method == "GET":
                return JSONResponse(await anyio.to_thread.run_sync(policy.roots))
            # Bound reads even for chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return JSONResponse(failure("request_too_large"), status_code=413)
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) - {"root_id", "relative_path"}:
                raise PolicyError("invalid_arguments")
            root_id, path = data.get("root_id"), data.get("relative_path", "")
            if not isinstance(root_id, str) or not isinstance(path, str):
                raise PolicyError("invalid_arguments")
            action = policy.create_folder if request.url.path == "/v1/create-folder" else policy.list_directory
            return JSONResponse(await anyio.to_thread.run_sync(action, root_id, path))
        except (ValueError, UnicodeError):
            return JSONResponse(failure("invalid_json"), status_code=400)
        except PolicyError as error:
            return JSONResponse(failure(error.code), status_code=400)
        except OSError:
            return JSONResponse(failure("filesystem_error"), status_code=400)

    app = Starlette(
        routes=[
            Route("/v1/roots", execute),
            Route("/v1/list-directory", execute, methods=["POST"]),
            Route("/v1/create-folder", execute, methods=["POST"]),
        ]
    )

    class AuthBoundary:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                request = Request(scope)
                # No browser-origin requests, duplicate credentials, or cross-site preflights.
                if "origin" in request.headers:
                    return await JSONResponse(failure("browser_origin_denied"), status_code=403)(scope, receive, send)
                auth = request.headers.getlist("authorization")
                expected = f"Bearer {settings.token}".encode("ascii")
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("utf-8"), expected):
                    return await JSONResponse(failure("authentication_failed"), status_code=401)(scope, receive, send)
            await self.inner(scope, receive, send)

    return AuthBoundary(app)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = Settings.load(args.config)
        app = create_app(settings)
    except (ValueError, OSError, PolicyError):
        parser.exit(
            1,
            "Local file service configuration/root validation failed; check private config and Windows paths.\n",
        )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
