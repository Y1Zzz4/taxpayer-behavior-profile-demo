"""Run the localhost agent service-assistance web application."""

from __future__ import annotations

import argparse
from pathlib import Path

from taxpayer_profile.config import load_settings
from taxpayer_profile.database import create_schema, make_engine
from taxpayer_profile.security import PhoneProtector
from taxpayer_profile.web_app import DemoService, run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 12366 坐席接待辅助演示系统。")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    settings = load_settings()
    hash_key, encryption_key = settings.require_phone_keys()
    database = args.database or settings.database_path
    create_schema(make_engine(database))
    service = DemoService(
        database_path=database,
        protector=PhoneProtector(hash_key, encryption_key),
        settings=settings,
    )
    run_server(service=service, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
