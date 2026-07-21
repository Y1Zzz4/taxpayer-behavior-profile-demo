"""Build the database from trusted history plus raw 2026-06-10 rows."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from taxpayer_profile.config import PROJECT_ROOT, load_settings
from taxpayer_profile.excel_reader import InputMode
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.processor import process_workbook
from taxpayer_profile.security import PhoneProtector


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "创建数据库：2026-06-01 至 09 导入可信分析字段，"
            "之后的记录忽略分析列并重新分析。"
        )
    )
    parser.add_argument(
        "input", nargs="?", type=Path, default=PROJECT_ROOT / "data/raw/raw_data.xlsx"
    )
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    hash_key, encryption_key = settings.require_phone_keys()
    if not settings.llm_configured:
        raise RuntimeError("初始化需要配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
    client = OpenAICompatibleClient(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model  # type: ignore[arg-type]
    )
    summary = process_workbook(
        input_path=args.input,
        database_path=args.database or settings.database_path,
        protector=PhoneProtector(hash_key, encryption_key),
        llm_client=client,
        input_mode=InputMode.BOOTSTRAP_MIXED,
        trusted_through=date(2026, 6, 9),
        progress_callback=lambda current, total, status: print(
            f"[{current}/{total}] {status}", flush=True
        ),
    )
    print(
        f"初始化完成：新增={summary.new_call_count}，跳过={summary.skipped_call_count}，"
        f"冲突={summary.conflict_count}，失败={summary.failed_count}"
    )


if __name__ == "__main__":
    main()
