from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path

try:
    from auth_utils import SESSION_COOKIE, authorized, create_session
except ImportError:
    from api.auth_utils import SESSION_COOKIE, authorized, create_session


ROOT = Path(__file__).resolve().parents[1]
LOGIN_PAGE = ROOT / "web" / "login.html"
DASHBOARD_PAGE = ROOT / "web" / "index.html"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        page = DASHBOARD_PAGE if authorized(self) else LOGIN_PAGE
        body = page.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "JSON inválido")
            return

        if data.get("action") == "logout":
            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
            )
            self.end_headers()
            return

        expected_username = os.environ.get("ADMIN_USERNAME", "admin")
        expected_password = os.environ.get("ADMIN_PASSWORD")
        if not expected_password or data.get("username") != expected_username:
            self.send_error(401, "Credenciales inválidas")
            return
        import hmac

        if not hmac.compare_digest(str(data.get("password", "")), expected_password):
            self.send_error(401, "Credenciales inválidas")
            return
        token = create_session(expected_username)
        self.send_response(200)
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Max-Age=604800; Path=/; HttpOnly; Secure; SameSite=Lax",
        )
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"authenticated":true}')

    def log_message(self, format: str, *args: object) -> None:
        return


handler = Handler
