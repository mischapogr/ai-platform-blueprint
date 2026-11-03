"""Single-owner local session; not multi-tenant identity management."""

import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import HTTPException, Request


def secret():
    return (
        Path(os.getenv("WORKSPACE_TOKEN_FILE", "/run/secrets/workspace_token")).read_text().strip()
    )


def same_origin(request: Request):
    origin = request.headers.get("origin")
    allowed = {
        value.strip()
        for value in os.getenv(
            "WORKSPACE_ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080"
        ).split(",")
        if value.strip()
    }
    if origin and origin not in allowed:
        raise HTTPException(403, "Origin not allowed")


def issue_session():
    expires = str(int(time.time()) + 8 * 3600)
    signature = hmac.new(secret().encode(), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def require_session(request: Request):
    same_origin(request)
    value = request.cookies.get("platform_session", "")
    try:
        expires, signature = value.split(".", 1)
        expected = hmac.new(secret().encode(), expires.encode(), hashlib.sha256).hexdigest()
        if int(expires) > time.time() and hmac.compare_digest(signature, expected):
            return
    except (ValueError, OSError):
        pass
    raise HTTPException(401, "Unlock this local workspace first")
