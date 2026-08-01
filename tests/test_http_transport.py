from __future__ import annotations

import json
import logging
from datetime import datetime
from email.message import Message
from io import BytesIO
from types import SimpleNamespace

from taxpayer_profile.presentation.http import handler_factory, route_access_policy


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
    css_status, css_headers, stylesheet = request("GET", "/styles.css")
    ui_status, ui_headers, ui_script = request("GET", "/ui.js")
    api_status, api_headers, api_script = request("GET", "/api-client.js")
    script_status, script_headers, script = request("GET", "/app.js")

    assert html_status == 200
    assert html_headers["Content-Type"] == "text/html; charset=utf-8"
    assert html.count(b'<link rel="stylesheet" href="/styles.css">') == 1
    assert b"<style>" not in html
    assert html.count(b'<script src="/ui.js"></script>') == 1
    assert html.count(b'<script src="/api-client.js"></script>') == 1
    assert html.count(b'<script src="/app.js"></script>') == 1
    assert html.index(b'<link rel="stylesheet" href="/styles.css">') < html.index(
        b'<script src="/ui.js"></script>'
    ) < html.index(
        b'<script src="/api-client.js"></script>'
    ) < html.index(
        b'<script src="/app.js"></script>'
    )
    assert css_status == 200
    assert css_headers["Content-Type"] == "text/css; charset=utf-8"
    assert b":root" in stylesheet
    assert b"@media" in stylesheet
    assert ui_status == 200
    assert ui_headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"window.TaxpayerUI" in ui_script
    assert api_status == 200
    assert api_headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"window.TaxpayerAPI" in api_script
    assert b"class ApiError extends Error" in api_script
    assert b"requestJson" in api_script
    assert script_status == 200
    assert script_headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"window.TaxpayerUI" in script
    assert b"window.TaxpayerAPI" in script
    assert b"fetch(" not in script
    assert b"error.status === 401" in script
    assert b"url !== '/api/auth/login'" in script
    assert b"const text =" not in script
    assert b"function loadDashboard" in script
    assert b"restoreSession();" in script


def test_dashboard_presentation_module_is_loaded_before_app_orchestration() -> None:
    html_status, _, html = request("GET", "/")
    dashboard_status, dashboard_headers, dashboard_script = request(
        "GET", "/dashboard-ui.js"
    )
    script_status, _, script = request("GET", "/app.js")

    assert html_status == dashboard_status == script_status == 200
    assert html.count(b'<script src="/dashboard-ui.js"></script>') == 1
    assert html.index(b'<script src="/api-client.js"></script>') < html.index(
        b'<script src="/dashboard-ui.js"></script>'
    ) < html.index(b'<script src="/app.js"></script>')
    assert dashboard_headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"window.TaxpayerDashboard" in dashboard_script
    assert b"function render(data)" in dashboard_script
    assert b"fetch(" not in dashboard_script
    assert b"innerHTML" not in dashboard_script
    assert b"function renderDonut" not in script
    assert b"function loadDashboard" in script
    assert b"TaxpayerDashboard.render" in script


def test_feature_presentation_modules_are_loaded_before_app_orchestration() -> None:
    html_status, _, html = request("GET", "/")
    modules = {
        "/history-ui.js": b"window.TaxpayerHistory",
        "/workbench-ui.js": b"window.TaxpayerWorkbench",
        "/user-management-ui.js": b"window.TaxpayerUserManagement",
        "/showcase-ui.js": b"window.TaxpayerShowcase",
        "/showcase-graph-ui.js": b"window.TaxpayerShowcaseGraph",
    }

    assert html_status == 200
    previous = html.index(b'<script src="/dashboard-ui.js"></script>')
    for path, namespace in modules.items():
        status, headers, source = request("GET", path)
        marker = f'<script src="{path}"></script>'.encode()
        assert status == 200
        assert headers["Content-Type"] == "text/javascript; charset=utf-8"
        assert html.count(marker) == 1
        assert previous < html.index(marker) < html.index(b'<script src="/app.js"></script>')
        assert namespace in source
        assert b"fetch(" not in source
        assert b"innerHTML" not in source
        previous = html.index(marker)

    _, _, script = request("GET", "/app.js")
    assert b"function historyRow" not in script
    assert b"function renderOverview" not in script
    assert b"function renderClassificationCatalog" not in script
    assert b"function replaceProfileOptions" not in script
    assert b"function draw(" not in script
    assert b"async function loadUsers" in script
    assert b"TaxpayerHistory.renderPage" in script
    assert b"TaxpayerWorkbench.renderProfile" in script
    assert b"TaxpayerUserManagement.renderUsers" in script
    assert b"TaxpayerShowcase.renderClassificationCatalog" in script
    assert b"TaxpayerShowcase.replaceProfileOptions" in script
    assert b"TaxpayerShowcaseGraph.createGraph" in script
    assert b"state.graphCleanup = window.TaxpayerShowcaseGraph.createGraph" in script

    _, _, graph_script = request("GET", "/showcase-graph-ui.js")
    assert b"function createGraph" in graph_script
    assert b"return () =>" in graph_script


def test_frontend_treats_api_business_values_as_text() -> None:
    ui_status, _, ui_script = request("GET", "/ui.js")
    api_status, _, api_script = request("GET", "/api-client.js")
    dashboard_status, _, dashboard_script = request("GET", "/dashboard-ui.js")
    history_script_status, _, history_script = request("GET", "/history-ui.js")
    workbench_status, _, workbench_script = request("GET", "/workbench-ui.js")
    user_status, _, user_script = request("GET", "/user-management-ui.js")
    showcase_status, _, showcase_script = request("GET", "/showcase-ui.js")
    graph_status, _, graph_script = request("GET", "/showcase-graph-ui.js")
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

    assert (
        ui_status
        == api_status
        == dashboard_status
        == history_script_status
        == workbench_status
        == user_status
        == showcase_status
        == graph_status
        == script_status
        == 200
    )
    frontend_scripts = (
        ui_script
        + api_script
        + dashboard_script
        + history_script
        + workbench_script
        + user_script
        + showcase_script
        + graph_script
        + script
    )
    assert b"innerHTML" not in frontend_scripts
    assert b"textContent" in frontend_scripts
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


def test_route_access_policy_keeps_public_authenticated_and_admin_boundaries() -> None:
    assert route_access_policy("GET", "/") == "public"
    assert route_access_policy("POST", "/api/auth/login") == "public"
    assert route_access_policy("GET", "/api/dashboard") == "authenticated"
    assert route_access_policy("POST", "/api/history") == "authenticated"
    assert route_access_policy("GET", "/api/showcase/catalog") == "admin"
    assert route_access_policy("POST", "/api/showcase") == "admin"
    assert route_access_policy("POST", "/api/unknown") is None


def test_unknown_api_routes_keep_authentication_before_not_found() -> None:
    get_denied, _, _ = request("GET", "/api/unknown")
    get_missing, _, _ = request(
        "GET", "/api/unknown", headers={"Cookie": "tp_session=agent-token"}
    )
    post_denied, _, _ = request(
        "POST", "/api/unknown", body=b"{}"
    )
    post_missing, _, _ = request(
        "POST",
        "/api/unknown",
        body=b"{}",
        headers={"Cookie": "tp_session=agent-token"},
    )

    assert (get_denied, get_missing) == (401, 404)
    assert (post_denied, post_missing) == (401, 404)


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


def test_forbidden_request_emits_a_safe_structured_event(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="taxpayer_profile.presentation.http"):
        status, _, _ = request(
            "GET",
            "/api/users",
            headers={"Cookie": "tp_session=agent-token"},
        )

    assert status == 403
    record = next(record for record in caplog.records if record.msg == "http.request_denied")
    assert record.event_fields == {
        "path": "/api/users",
        "reason": "insufficient_role",
        "required_access": "admin",
    }
