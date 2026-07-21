"""Create a private local .env with new phone-protection keys."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    destination = PROJECT_ROOT / ".env"
    if destination.exists():
        print("本地 .env 已存在，未修改。")
        return
    content = "\n".join(
        [
            "# Local secrets generated for this workspace. Do not commit.",
            "LLM_BASE_URL=",
            "LLM_API_KEY=",
            "LLM_MODEL=",
            f"PHONE_HASH_KEY={secrets.token_urlsafe(48)}",
            f"PHONE_ENCRYPTION_KEY={Fernet.generate_key().decode('ascii')}",
            "DATABASE_PATH=data/database/taxpayer_profiles.sqlite3",
            "",
        ]
    )
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
        env_file.write(content)
    print("已创建权限为 0600 的本地 .env；密钥值未输出。")


if __name__ == "__main__":
    main()
