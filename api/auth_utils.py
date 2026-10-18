from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time


SESSION_COOKIE = "ruleta_session"
SESSION_DAYS = 7


def _secret() -> bytes:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError("Falta la variable de entorno SESSION_SECRET.")
    return secret.encode("utf-8")


def create_session(username: str) -> str:
    payload = json.dumps(
        {"username": username, "expires": int(time.time()) + SESSION_DAYS * 86400},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def session_username(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    cookies = {}
    for item in cookie_header.split(";"):
        if "=" in item:
            key, value = item.strip().split("=", 1)
            cookies[key] = value
    token = cookies.get(SESSION_COOKIE)
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if payload.get("expires", 0) <= time.time():
        return None
    return payload.get("username")


def authorized(request_handler: object) -> bool:
    return session_username(
        request_handler.headers.get("Cookie")  # type: ignore[attr-defined]
    ) is not None
