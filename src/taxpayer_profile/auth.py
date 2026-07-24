"""Small database-backed authentication service for the localhost demo."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from taxpayer_profile.models import AuthSession, SystemUser

PASSWORD_ITERATIONS = 210_000
SESSION_HOURS = 8
ROLES = {"admin", "agent"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _optional_boolean(value: object | None, *, field_label: str) -> bool | None:
    """Accept JSON boolean values without truthiness coercion.

    Strings such as ``"false"`` are truthy in Python and must not silently
    reverse an administrative action.
    """

    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{field_label}必须是布尔值")
    return value


def user_payload(user: SystemUser) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "role_label": "管理员" if user.role == "admin" else "坐席",
        "is_active": user.is_active,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


class AuthService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def ensure_default_users(
        self,
        *,
        admin_username: str,
        admin_password: str,
        agent_username: str,
        agent_password: str,
    ) -> None:
        defaults = (
            (admin_username, "系统管理员", admin_password, "admin"),
            (agent_username, "12366坐席", agent_password, "agent"),
        )
        with self.sessions.begin() as session:
            for username, display_name, password, role in defaults:
                normalized = username.strip()
                existing = session.scalar(
                    select(SystemUser).where(SystemUser.username == normalized)
                )
                if existing is None:
                    session.add(
                        SystemUser(
                            username=normalized,
                            display_name=display_name,
                            password_hash=hash_password(password),
                            role=role,
                            is_active=True,
                        )
                    )

    def login(self, username: object, password: object) -> tuple[str, dict[str, object]]:
        name = str(username or "").strip()
        secret = str(password or "")
        with self.sessions.begin() as session:
            user = session.scalar(select(SystemUser).where(SystemUser.username == name))
            if (
                user is None
                or not user.is_active
                or not verify_password(secret, user.password_hash)
            ):
                raise ValueError("用户名或密码错误")
            raw_token = secrets.token_urlsafe(32)
            now = _now()
            session.add(
                AuthSession(
                    token_hash=_token_hash(raw_token),
                    user_id=user.id,
                    created_at=now,
                    expires_at=now + timedelta(hours=SESSION_HOURS),
                    last_seen_at=now,
                )
            )
            return raw_token, user_payload(user)

    def authenticate(self, token: str | None) -> SystemUser | None:
        if not token:
            return None
        now = _now()
        with self.sessions.begin() as session:
            auth_session = session.get(AuthSession, _token_hash(token))
            if auth_session is None or auth_session.expires_at <= now:
                if auth_session is not None:
                    session.delete(auth_session)
                return None
            user = session.get(SystemUser, auth_session.user_id)
            if user is None or not user.is_active:
                session.delete(auth_session)
                return None
            auth_session.last_seen_at = now
            session.expunge(user)
            return user

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.sessions.begin() as session:
            auth_session = session.get(AuthSession, _token_hash(token))
            if auth_session is not None:
                session.delete(auth_session)

    def list_users(self) -> list[dict[str, object]]:
        with self.sessions() as session:
            users = session.scalars(select(SystemUser).order_by(SystemUser.id)).all()
            return [user_payload(user) for user in users]

    def create_user(
        self,
        *,
        username: object,
        display_name: object,
        password: object,
        role: object,
    ) -> dict[str, object]:
        name = str(username or "").strip()
        display = str(display_name or "").strip()
        role_value = str(role or "").strip()
        if not name or len(name) > 80:
            raise ValueError("用户名不能为空且不得超过 80 个字符")
        if not display or len(display) > 100:
            raise ValueError("显示名称不能为空且不得超过 100 个字符")
        if role_value not in ROLES:
            raise ValueError("角色只能是管理员或坐席")
        with self.sessions.begin() as session:
            if session.scalar(select(SystemUser).where(SystemUser.username == name)):
                raise ValueError("用户名已存在")
            user = SystemUser(
                username=name,
                display_name=display,
                password_hash=hash_password(str(password or "")),
                role=role_value,
                is_active=True,
                updated_at=_now(),
            )
            session.add(user)
            session.flush()
            return user_payload(user)

    def update_user(
        self,
        *,
        user_id: object,
        display_name: object | None = None,
        role: object | None = None,
        is_active: object | None = None,
        password: object | None = None,
    ) -> dict[str, object]:
        try:
            identifier = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("用户编号无效") from exc
        active_value = _optional_boolean(is_active, field_label="启用状态")
        with self.sessions.begin() as session:
            user = session.get(SystemUser, identifier)
            if user is None:
                raise ValueError("用户不存在")
            active_admins = session.scalar(
                select(func.count())
                .select_from(SystemUser)
                .where(SystemUser.role == "admin", SystemUser.is_active.is_(True))
            ) or 0
            if display_name is not None:
                display = str(display_name).strip()
                if not display:
                    raise ValueError("显示名称不能为空")
                user.display_name = display
            if role is not None:
                role_value = str(role).strip()
                if role_value not in ROLES:
                    raise ValueError("角色只能是管理员或坐席")
                if (
                    user.role == "admin"
                    and user.is_active
                    and role_value != "admin"
                    and active_admins <= 1
                ):
                    raise ValueError("至少需要保留一个启用的管理员")
                user.role = role_value
            if active_value is not None:
                if (
                    user.role == "admin"
                    and user.is_active
                    and not active_value
                    and active_admins <= 1
                ):
                    raise ValueError("至少需要保留一个启用的管理员")
                user.is_active = active_value
                if not active_value:
                    for item in session.scalars(
                        select(AuthSession).where(AuthSession.user_id == user.id)
                    ).all():
                        session.delete(item)
            if password is not None and str(password):
                user.password_hash = hash_password(str(password))
                for item in session.scalars(
                    select(AuthSession).where(AuthSession.user_id == user.id)
                ).all():
                    session.delete(item)
            user.updated_at = _now()
            session.flush()
            return user_payload(user)
