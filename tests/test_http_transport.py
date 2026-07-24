from __future__ import annotations

import json
from datetime import datetime
from email.message import Message
from io import BytesIO
from types import SimpleNamespace

from taxpayer_profile.presentation.http import handler_factory


class FakeAuth:
    def authenticate(self, token: str | None):
        if token not in {"agent-token", "admin-token"}:
            return None
        role = "admin" if token == "admin-token" else "agent"
        now = datetime(2026, 7, 24)
        return SimpleNamespace(
            id=1,
            username=role,
            display_name=role,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    def login(
        self, username: object, password: object
    ) -> tuple[str, dict[str, object]]:
        if username != "agent" or password != "password":
            raise ValueError("用户名或密码错误")
        return "agent-token", {"username": "agent", "role": "agent"}

    def logout(self, token: str | None) -> None:
        del token

    def list_users(self) -> list[dict[str, object]]:
        return []


class FakeApplication:
    auth = FakeAuth()

    def dashboard_summary(self) -> dict[str, object]:
        return {"total_calls": 3}


def request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    base_handler = handler_factory(FakeApplication())

    class MemoryHandler(base_handler):
        """Drive the production handler without requiring a network socket."""

        def __init__(self) -> None:
            self.path = path
            self.rfile = BytesIO(body or b"")
            self.wfile = BytesIO()
            self.headers = Message()
            for name, value in (headers or {}).items():
                self.headers[name] = value
            if body is not None:
                self.headers["Content-Length"] = str(len(body))
            self.response_status = 0
            self.response_headers: dict[str, str] = {}

        def send_response(
            self, code: int, message: str | None = None
        ) -> None:
            del message
            self.response_status = code

        def send_header(self, keyword: str, value: str) -> None:
            self.response_headers[keyword] = value

        def end_headers(self) -> None:
            pass

    handler = MemoryHandler()
    getattr(handler, f"do_{method}")()
    return handler.response_status, handler.response_headers, handler.wfile.getvalue()


def test_static_assets_are_served_from_the_canonical_files() -> None:
    html_status, html_headers, html = request("GET", "/")
    script_status, script_headers, script = request("GET", "/app.js")

    assert html_status == 200
    assert html_headers["Content-Type"] == "text/html; charset=utf-8"
    assert b'<script src="/app.js"></script>' in html
    assert script_status == 200
    assert script_headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"restoreSession();" in script


def test_authentication_boundary_and_session_cookie_contract() -> None:
    denied_status, _, denied_body = request("GET", "/api/dashboard")
    login_status, login_headers, login_body = request(
        "POST",
        "/api/auth/login",
        body=json.dumps({"username": "agent", "password": "password"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    allowed_status, _, allowed_body = request(
        "GET",
        "/api/dashboard",
        headers={"Cookie": "tp_session=agent-token"},
    )

    assert denied_status == 401
    assert json.loads(denied_body) == {"error": "请先登录"}
    assert login_status == 200
    assert json.loads(login_body)["user"]["role"] == "agent"
    assert "HttpOnly" in login_headers["Set-Cookie"]
    assert "SameSite=Strict" in login_headers["Set-Cookie"]
    assert allowed_status == 200
    assert json.loads(allowed_body) == {"total_calls": 3}
