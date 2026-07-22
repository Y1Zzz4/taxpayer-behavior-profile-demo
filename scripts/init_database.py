"""Build the database in two explicit stages: trusted history, then raw calls."""

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
            "创建数据库：2026-06-01 至 09 复用可信分析字段，"
            "2026-06-10 及之后的记录调用模型重新分析。"
        )
    )
    parser.add_argument(
        "input", nargs="?", type=Path, default=PROJECT_ROOT / "data/raw/raw_data.xlsx"
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="删除指定的现有数据库后全量重建",
    )
    parser.add_argument(
        "--history-only",
        action="store_true",
        help="仅构建 1—9 日历史基底，用于本地规则预览",
    )
    parser.add_argument(
        "--analyze-history-with-model",
        action="store_true",
        help="在复用 1—9 日已有字段的同时，调用模型补齐画像字段",
    )
    args = parser.parse_args()
    settings = load_settings()
    hash_key, encryption_key = settings.require_phone_keys()
    model_required = not args.history_only or args.analyze_history_with_model
    if model_required and not settings.llm_configured:
        raise RuntimeError("当前构建方式需要配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
    client = (
        OpenAICompatibleClient(
            settings.llm_base_url,  # type: ignore[arg-type]
            settings.llm_api_key,  # type: ignore[arg-type]
            settings.llm_model,  # type: ignore[arg-type]
        )
        if settings.llm_configured
        else None
    )
    database = (args.database or settings.database_path).expanduser().resolve()
    if args.rebuild and database.exists():
        database.unlink()

    def progress(stage: str):
        def report(current: int, total: int, status: str) -> None:
            noteworthy = status in {
                "模型分析失败",
                "处理失败",
                "输入字段无效",
                "业务编号冲突",
            }
            if status != "开始处理" and (
                noteworthy or current == 1 or current == total or current % 500 == 0
            ):
                print(f"[{stage} {current}/{total}] {status}", flush=True)

        return report

    trusted_summary = process_workbook(
        input_path=args.input,
        database_path=database,
        protector=PhoneProtector(hash_key, encryption_key),
        llm_client=client if args.analyze_history_with_model else None,
        input_mode=InputMode.TRUSTED_IMPORT,
        end_date=date(2026, 6, 9),
        progress_callback=progress("历史基底"),
    )
    print(
        f"历史基底完成：新增={trusted_summary.new_call_count}，"
        f"跳过={trusted_summary.skipped_call_count}，失败={trusted_summary.failed_count}"
    )

    if args.history_only:
        preview_type = "模型补齐预览库" if args.analyze_history_with_model else "本地规则预览库"
        print(f"{preview_type}已生成：{database}")
        return

    assert client is not None
    raw_summary = process_workbook(
        input_path=args.input,
        database_path=database,
        protector=PhoneProtector(hash_key, encryption_key),
        llm_client=client,
        input_mode=InputMode.RAW_ANALYSIS,
        start_date=date(2026, 6, 10),
        progress_callback=progress("增量分析"),
    )
    print(
        f"增量分析完成：新增={raw_summary.new_call_count}，"
        f"跳过={raw_summary.skipped_call_count}，冲突={raw_summary.conflict_count}，"
        f"失败={raw_summary.failed_count}"
    )


if __name__ == "__main__":
    main()
