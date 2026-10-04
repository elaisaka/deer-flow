"""Independent Windows service: finite file tools and separately authorized confirmation."""

import argparse
import hmac
import json
import sqlite3
from pathlib import Path

import anyio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .imports import read_selected_file
from .organization import Organizer
from .policy import FilePolicy, PolicyError, Settings


def failure(code: str) -> dict:
    return {"ok": False, "verified": False, "error": {"code": code}}


def create_app(settings: Settings):
    policy = FilePolicy(settings)
    organizer = Organizer(policy, settings.state_dir) if settings.state_dir and settings.approval_token else None

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
            if not isinstance(data, dict):
                raise PolicyError("invalid_arguments")
            if request.url.path == "/v1/import/read":
                if set(data) != {"root_id", "relative_path"} or not all(isinstance(v, str) for v in data.values()):
                    raise PolicyError("invalid_arguments")
                return JSONResponse(await anyio.to_thread.run_sync(read_selected_file, policy, data["root_id"], data["relative_path"]))
            if request.url.path == "/v1/knowledge/confirm":
                supplied = request.headers.getlist("x-file-approval-token")
                if not settings.approval_token or len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode(), settings.approval_token.encode()):
                    return JSONResponse(failure("trusted_confirmation_required"), status_code=403)
                if set(data) != {"digest", "actor"} or not isinstance(data["digest"], str) or len(data["digest"]) != 64 or not isinstance(data["actor"], str) or not 1 <= len(data["actor"]) <= 128:
                    raise PolicyError("invalid_arguments")
                # Verifies a user-supplied authority. No Windows files are deleted,
                # no reusable approval is issued and no secret is persisted.
                return JSONResponse({"ok": True, "digest": data["digest"]})
            if request.url.path.startswith("/v1/organization/"):
                if organizer is None:
                    raise PolicyError("organization_not_configured")
                action = request.path_params["action"]
                if action == "preview":
                    return JSONResponse(await anyio.to_thread.run_sync(organizer.preview, data))
                fields = {
                    "get": {"plan_id"},
                    "list": set(),
                    "execute": {"plan_id", "version"},
                    "undo": {"plan_id"},
                    "cancel": {"plan_id", "version"},
                    "confirm": {"plan_id", "version", "digest", "actor"},
                }
                if action not in fields or set(data) != fields[action] or ("version" in data and type(data["version"]) is not int):
                    raise PolicyError("invalid_arguments")
                if action == "confirm":
                    supplied = request.headers.getlist("x-file-approval-token")
                    if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode(), settings.approval_token.encode()):
                        return JSONResponse(failure("trusted_confirmation_required"), status_code=403)
                    if not isinstance(data["actor"], str) or not 1 <= len(data["actor"]) <= 128 or not isinstance(data["digest"], str):
                        raise PolicyError("invalid_arguments")
                    arguments = (
                        data["plan_id"],
                        data["version"],
                        data["digest"],
                        data["actor"],
                    )
                else:
                    arguments = tuple(data[key] for key in ("plan_id", "version") if key in data)
                method = organizer.list_plans if action == "list" else getattr(organizer, action)
                return JSONResponse(await anyio.to_thread.run_sync(method, *arguments))
            if set(data) - {"root_id", "relative_path"}:
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
        except sqlite3.Error:
            return JSONResponse(failure("record_store_error"), status_code=503)

    app = Starlette(
        routes=[
            Route("/v1/roots", execute),
            Route("/v1/list-directory", execute, methods=["POST"]),
            Route("/v1/create-folder", execute, methods=["POST"]),
            Route("/v1/organization/{action}", execute, methods=["POST"]),
            Route("/v1/import/read", execute, methods=["POST"]),
            Route("/v1/knowledge/confirm", execute, methods=["POST"]),
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
