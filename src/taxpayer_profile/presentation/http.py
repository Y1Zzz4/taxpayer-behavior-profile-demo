"""HTTP transport adapter for the local service-assistance application."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from taxpayer_profile.auth import AuthService, user_payload
from taxpayer_profile.config import PROJECT_ROOT
from taxpayer_profile.models import SystemUser

WEB_ROOT = PROJECT_ROOT / "web"
MAX_REQUEST_BYTES = 16_384
SESSION_COOKIE = "tp_session"
LOGGER = logging.getLogger(__name__)


class HttpApplication(Protocol):
    """Use cases consumed by the HTTP adapter, independent of implementation."""

    @property
    def auth(self) -> AuthService: ...

    def initialize_auth(self) -> None: ...

    def lookup_profile(self, phone: object) -> dict[str, object] | None: ...

    def generate_advice(self, phone: object) -> dict[str, object]: ...

    def dashboard_summary(self) -> dict[str, object]: ...

    def profile_showcase_catalog(
        self, *, query: object = "", limit: object = 5
    ) -> dict[str, object]: ...

    def profile_showcase(
        self, *, profile_key: object, scenario: object = "baseline"
    ) -> dict[str, object]: ...

    def history_page(
        self,
        *,
        page: object = 1,
        page_size: object = 10,
        phone: object | None = None,
    ) -> dict[str, object]: ...

    def history_detail(self, business_id: object) -> dict[str, object] | None: ...


def handler_factory(
    service: HttpApplication,
) -> type[BaseHTTPRequestHandler]:
    """Bind one application-service instance to the standard-library server."""

    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "TaxpayerProfileDemo/0.2"

        def _json(
            self,
            status: int,
            payload: object,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(content)

        def _static(self, filename: str, content_type: str) -> None:
            content = (WEB_ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _read_json(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("请求长度无效") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("请求内容为空或过大")
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("请求必须是有效 JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("请求内容必须是 JSON 对象")
            return body

        def _token(self) -> str | None:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            item = cookie.get(SESSION_COOKIE)
            return item.value if item else None

        def _require_user(self, *, admin: bool = False) -> SystemUser | None:
            user = service.auth.authenticate(self._token())
            if user is None:
                self._error(401, "请先登录")
                return None
            if admin and user.role != "admin":
                self._error(403, "当前账号无权访问该功能")
                return None
            return user

        def _run_request(self, method: str, operation: Callable[[], None]) -> None:
            """Convert unexpected application failures into one stable response."""

            try:
                operation()
            except Exception:
                LOGGER.exception(
                    "http.request_failed",
                    extra={
                        "event_name": "http.request_failed",
                        "event_fields": {
                            "method": method,
                            "path": urlparse(self.path).path,
                        },
                    },
                )
                self._error(500, "服务器处理请求失败")

        def _dispatch_get(self) -> None:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            if path == "/":
                self._static("index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
                return
            if path == "/api/auth/me":
                user = self._require_user()
                if user is not None:
                    self._json(200, {"user": user_payload(user)})
                return
            admin_only = path in {"/api/showcase/catalog", "/api/users"}
            if self._require_user(admin=admin_only) is None:
                return
            if path == "/api/dashboard":
                self._json(200, service.dashboard_summary())
            elif path == "/api/showcase/catalog":
                parameters = parse_qs(parsed_url.query)
                try:
                    self._json(
                        200,
                        service.profile_showcase_catalog(
                            query=parameters.get("q", [""])[0],
                            limit=parameters.get("limit", ["5"])[0],
                        ),
                    )
                except ValueError as exc:
                    self._error(400, str(exc))
            elif path == "/api/users":
                self._json(200, {"items": service.auth.list_users()})
            else:
                self._error(404, "接口不存在")

        def _dispatch_post(self) -> None:
            path = urlparse(self.path).path
            try:
                body = self._read_json()
            except ValueError as exc:
                self._error(400, str(exc))
                return
            if path == "/api/auth/login":
                try:
                    token, user = service.auth.login(
                        body.get("username"), body.get("password")
                    )
                    cookie = (
                        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
                        "SameSite=Strict; Max-Age=28800"
                    )
                    self._json(200, {"user": user}, headers={"Set-Cookie": cookie})
                except ValueError as exc:
                    self._error(401, str(exc))
                return
            if path == "/api/auth/logout":
                service.auth.logout(self._token())
                self._json(
                    200,
                    {"ok": True},
                    headers={
                        "Set-Cookie": (
                            f"{SESSION_COOKIE}=; Path=/; HttpOnly; "
                            "SameSite=Strict; Max-Age=0"
                        )
                    },
                )
                return
            admin_only = path in {
                "/api/showcase",
                "/api/users/create",
                "/api/users/update",
            }
            if self._require_user(admin=admin_only) is None:
                return
            try:
                if path == "/api/profile":
                    if body.get("phone") is None:
                        raise ValueError("缺少来电号码")
                    profile = service.lookup_profile(body["phone"])
                    self._json(200, {"found": profile is not None, "profile": profile})
                elif path == "/api/advice":
                    if body.get("phone") is None:
                        raise ValueError("缺少来电号码")
                    self._json(200, service.generate_advice(body["phone"]))
                elif path == "/api/history":
                    self._json(
                        200,
                        service.history_page(
                            page=body.get("page", 1),
                            page_size=body.get("page_size", 10),
                            phone=body.get("phone"),
                        ),
                    )
                elif path == "/api/history/detail":
                    detail = service.history_detail(body.get("business_id"))
                    self._json(200, {"found": detail is not None, "detail": detail})
                elif path == "/api/showcase":
                    self._json(
                        200,
                        service.profile_showcase(
                            profile_key=body.get("profile_key"),
                            scenario=body.get("scenario", "baseline"),
                        ),
                    )
                elif path == "/api/users/create":
                    self._json(
                        200,
                        {
                            "user": service.auth.create_user(
                                username=body.get("username"),
                                display_name=body.get("display_name"),
                                password=body.get("password"),
                                role=body.get("role"),
                            )
                        },
                    )
                elif path == "/api/users/update":
                    self._json(
                        200,
                        {
                            "user": service.auth.update_user(
                                user_id=body.get("user_id"),
                                display_name=body.get("display_name"),
                                role=body.get("role"),
                                is_active=body.get("is_active"),
                                password=body.get("password"),
                            )
                        },
                    )
                else:
                    self._error(404, "接口不存在")
            except ValueError as exc:
                self._error(400, str(exc))

        def do_GET(self) -> None:  # noqa: N802
            self._run_request("GET", self._dispatch_get)

        def do_POST(self) -> None:  # noqa: N802
            self._run_request("POST", self._dispatch_post)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return DemoHandler


def run_server(
    *, service: HttpApplication, host: str = "127.0.0.1", port: int = 8000
) -> None:
    """Initialize authentication and serve the local HTTP application."""

    service.initialize_auth()
    server = ThreadingHTTPServer((host, port), handler_factory(service))
    print(f"12366坐席服务辅助系统已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
