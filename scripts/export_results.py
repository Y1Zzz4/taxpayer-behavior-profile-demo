"""Export the three human-readable result worksheets."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from taxpayer_profile.config import PROJECT_ROOT, load_settings
from taxpayer_profile.exporter import export_results
from taxpayer_profile.security import PhoneProtector


def main() -> None:
    parser = argparse.ArgumentParser(description="导出号码画像、来电轨迹和更新摘要。")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    hash_key, encryption_key = settings.require_phone_keys()
    output = args.output or PROJECT_ROOT / "data/output" / (
        f"taxpayer_profiles_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    )
    path = export_results(
        database_path=args.database or settings.database_path,
        output_path=output,
        protector=PhoneProtector(hash_key, encryption_key),
    )
    print(f"导出完成：{path}")


if __name__ == "__main__":
    main()

