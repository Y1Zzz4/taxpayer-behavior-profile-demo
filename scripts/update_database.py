"""Analyze and ingest an independent incremental workbook."""

from __future__ import annotations

import argparse
import atexit
from pathlib import Path

from taxpayer_profile.config import PROJECT_ROOT, load_settings
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.processor import process_workbook
from taxpayer_profile.security import PhoneProtector


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按显式字段复用策略分析一个新 Excel，并增量更新画像数据库。"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="指定一个待增量分析的新 Excel",
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--cache",
        type=Path,
        default=PROJECT_ROOT / "data/cache/model_extractions.sqlite3",
    )
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers 必须在1—16之间")
    settings = load_settings()
    hash_key, encryption_key = settings.require_phone_keys()
    if not settings.llm_configured:
        raise RuntimeError("增量分析需要配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
    client = OpenAICompatibleClient(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model  # type: ignore[arg-type]
    )
    atexit.register(client.close)
    protector = PhoneProtector(hash_key, encryption_key)
    database = (args.database or settings.database_path).expanduser().resolve()
    cache_path = None if args.no_cache else args.cache.expanduser().resolve()
    if cache_path == database:
        parser.error("模型缓存文件不能与画像数据库使用同一路径")
    summaries = [
        process_workbook(
            input_path=args.input,
            database_path=database,
            protector=protector,
            llm_client=client,
            input_mode=InputMode.INCREMENTAL,
            model_workers=args.workers,
            extraction_cache_path=cache_path,
            progress_callback=lambda current, total, status: print(
                f"[{current}/{total}] {status}", flush=True
            ),
        )
    ]
    for summary in summaries:
        state = "已处理过" if summary.already_processed else "完成"
        print(
            f"{summary.input_filename}: {state}，新增={summary.new_call_count}，"
            f"跳过={summary.skipped_call_count}，冲突={summary.conflict_count}，"
            f"失败={summary.failed_count}"
        )


if __name__ == "__main__":
    main()
