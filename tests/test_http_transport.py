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

    def history_page(
        self,
        *,
        page: object = 1,
        page_size: object = 10,
        phone: object | None = None,
    ) -> dict[str, object]:
        del page, page_size, phone
        return {
            "page": 1,
            "total_pages": 1,
            "total": 1,
            "filtered": False,
            "items": [
                {
                    "business_id": "BIZ-HTML",
                    "core_question": '<img src=x onerror="alert(1)">',
                }
            ],
        }


def request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    application: object | None = None,
) -> tuple[int, dict[str, str], bytes]:
    selected_application = application if application is not None else FakeApplication()
    base_handler = handler_factory(selected_application)  # type: ignore[arg-type]

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
    style_status, style_headers, style = request("GET", "/workbench.css")

    assert html_status == 200
    assert html_headers["Content-Type"] == "text/html; charset=utf-8"
    assert b'<script src="/app.js"></script>' in html
    assert script_status == 200
    assert script_headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"restoreSession();" in script
    assert style_status == 200
    assert style_headers["Content-Type"] == "text/css; charset=utf-8"
    assert b".agent-shell" in style


def test_frontend_treats_api_business_values_as_text() -> None:
    script_status, _, script = request("GET", "/app.js")
    history_status, _, history = request(
        "POST",
        "/api/history",
        body=json.dumps({"page": 1}).encode(),
        headers={
            "Content-Type": "application/json",
            "Cookie": "tp_session=agent-token",
        },
    )

    assert script_status == 200
    assert b"innerHTML" not in script
    assert b"textContent" in script
    assert history_status == 200
    assert json.loads(history)["items"][0]["core_question"] == (
        '<img src=x onerror="alert(1)">'
    )


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


def test_unexpected_application_error_returns_stable_json_response() -> None:
    class FailingApplication(FakeApplication):
        def dashboard_summary(self) -> dict[str, object]:
            raise RuntimeError("internal detail must not reach the browser")

    status, headers, body = request(
        "GET",
        "/api/dashboard",
        headers={"Cookie": "tp_session=agent-token"},
        application=FailingApplication(),
    )

    assert status == 500
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"error": "服务器处理请求失败"}
    assert b"internal detail" not in body
