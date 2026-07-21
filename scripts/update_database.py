"""Analyze and ingest one or more independent raw workbooks."""

from __future__ import annotations

import argparse
from pathlib import Path

from taxpayer_profile.config import PROJECT_ROOT, load_settings
from taxpayer_profile.excel_reader import InputMode
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.processor import process_raw_directory, process_workbook
from taxpayer_profile.security import PhoneProtector


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将独立的新 Excel 当作原始数据重新分析并增量更新数据库。"
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="指定一个新 Excel；省略时扫描 data/raw/",
    )
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    hash_key, encryption_key = settings.require_phone_keys()
    if not settings.llm_configured:
        raise RuntimeError("原始数据分析需要配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
    client = OpenAICompatibleClient(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model  # type: ignore[arg-type]
    )
    protector = PhoneProtector(hash_key, encryption_key)
    database = args.database or settings.database_path
    if args.input is not None:
        summaries = [
            process_workbook(
                input_path=args.input,
                database_path=database,
                protector=protector,
                llm_client=client,
                input_mode=InputMode.RAW_ANALYSIS,
                progress_callback=lambda current, total, status: print(
                    f"[{current}/{total}] {status}", flush=True
                ),
            )
        ]
    else:
        summaries = process_raw_directory(
            raw_directory=PROJECT_ROOT / "data/raw",
            database_path=database,
            protector=protector,
            llm_client=client,
            progress_callback=lambda current, total, status: print(
                f"[{current}/{total}] {status}", flush=True
            ),
        )
    for summary in summaries:
        state = "已处理过" if summary.already_processed else "完成"
        print(
            f"{summary.input_filename}: {state}，新增={summary.new_call_count}，"
            f"跳过={summary.skipped_call_count}，冲突={summary.conflict_count}，"
            f"失败={summary.failed_count}"
        )


if __name__ == "__main__":
    main()
