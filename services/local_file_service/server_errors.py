"""Error envelope shared without importing the Windows server into the bridge."""


def failure(code: str, message: str) -> dict:
    return {"ok": False, "verified": False, "error": {"code": code, "message": message}}
