from pathlib import Path

import pytest

from taxpayer_profile.auth import AuthService, hash_password, verify_password
from taxpayer_profile.database import create_schema, make_engine, make_session_factory


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("Example@123")
    second = hash_password("Example@123")
    assert first != second
    assert verify_password("Example@123", first)
    assert not verify_password("wrong-password", first)


def test_default_roles_sessions_and_last_admin_guard(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "auth.sqlite3")
    create_schema(engine)
    service = AuthService(make_session_factory(engine))
    service.ensure_default_users(
        admin_username="admin",
        admin_password="Admin@12366",
        agent_username="agent",
        agent_password="Agent@12366",
    )

    token, agent = service.login("agent", "Agent@12366")
    assert agent["role"] == "agent"
    assert service.authenticate(token).role == "agent"  # type: ignore[union-attr]
    service.logout(token)
    assert service.authenticate(token) is None

    users = service.list_users()
    admin = next(item for item in users if item["role"] == "admin")
    with pytest.raises(ValueError, match="至少需要保留一个"):
        service.update_user(user_id=admin["id"], role="agent")


def test_user_active_state_requires_a_real_boolean(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "auth.sqlite3")
    create_schema(engine)
    service = AuthService(make_session_factory(engine))
    user = service.create_user(
        username="agent",
        display_name="测试坐席",
        password="Agent@12366",
        role="agent",
    )

    for invalid in ("false", "true", 0, 1, [], {}):
        with pytest.raises(ValueError, match="启用状态必须是布尔值"):
            service.update_user(user_id=user["id"], is_active=invalid)
    assert service.update_user(user_id=user["id"], is_active=False)["is_active"] is False
    assert service.update_user(user_id=user["id"], is_active=True)["is_active"] is True
