"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    phone_hash_key: str | None
    phone_encryption_key: str | None
    default_admin_username: str = "admin"
    default_admin_password: str = "Admin@12366"
    default_agent_username: str = "agent"
    default_agent_password: str = "Agent@12366"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    def require_phone_keys(self) -> tuple[str, str]:
        if not self.phone_hash_key or not self.phone_encryption_key:
            raise RuntimeError(
                "缺少 PHONE_HASH_KEY 或 PHONE_ENCRYPTION_KEY，请在本地 .env 中配置"
            )
        return self.phone_hash_key, self.phone_encryption_key


def load_settings(env_file: Path | None = None) -> Settings:
    """Load local configuration without logging any secret values."""

    load_dotenv(env_file or PROJECT_ROOT / ".env")
    database_path = Path(
        os.getenv(
            "DATABASE_PATH",
            str(PROJECT_ROOT / "data/database/taxpayer_profiles.sqlite3"),
        )
    ).expanduser()
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return Settings(
        database_path=database_path,
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_model=os.getenv("LLM_MODEL"),
        phone_hash_key=os.getenv("PHONE_HASH_KEY"),
        phone_encryption_key=os.getenv("PHONE_ENCRYPTION_KEY"),
        default_admin_username=os.getenv("DEFAULT_ADMIN_USERNAME", "admin"),
        default_admin_password=os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@12366"),
        default_agent_username=os.getenv("DEFAULT_AGENT_USERNAME", "agent"),
        default_agent_password=os.getenv("DEFAULT_AGENT_PASSWORD", "Agent@12366"),
    )
