"""Backfill missing legacy caller types from an explicitly supplied workbook."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from taxpayer_profile.application.caller_type_backfill import (
    CALLER_TYPE_SOURCE_COLUMN,
    backfill_missing_caller_types,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "仅使用输入表的“咨询主体(大模型判断)”列，回填数据库中缺失的咨询主体。"
        )
    )
    parser.add_argument("input", type=Path, help="含回填来源列的 Excel 文件")
    parser.add_argument("--database", type=Path, required=True, help="待修复的 SQLite 数据库")
    parser.add_argument(
        "--apply", action="store_true", help="实际写入；省略时仅输出受影响范围"
    )
    parser.add_argument("--backup", type=Path, help="实际写入前创建的数据库备份路径")
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    database = args.database.expanduser().resolve()
    if not database.is_file():
        parser.error(f"数据库不存在：{database}")
    # This repair deliberately reads only its two audit columns. A legacy
    # source may lack the text required by *new* incremental analysis, but it
    # can still be a valid controlled source for caller-type correction.
    required_columns = ["业务编号", CALLER_TYPE_SOURCE_COLUMN]
    header = pd.read_excel(source, sheet_name=0, nrows=0)
    missing = [column for column in required_columns if column not in header.columns]
    if missing:
        parser.error(f"回填来源缺少字段：{'、'.join(missing)}")
    rows = pd.read_excel(source, sheet_name=0, usecols=required_columns).to_dict(
        orient="records"
    )

    if args.apply:
        backup = args.backup or database.with_name(
            f"{database.stem}.before-caller-type-backfill-"
            f"{datetime.now():%Y%m%d%H%M%S}{database.suffix}"
        )
        backup = backup.expanduser().resolve()
        if backup.exists():
            parser.error(f"备份路径已存在：{backup}")
        shutil.copy2(database, backup)
        print(f"已创建数据库备份：{backup}")

    summary = backfill_missing_caller_types(
        database_path=database,
        source_rows=rows,
        dry_run=not args.apply,
    )
    action = "已回填" if args.apply else "预览"
    print(
        f"{action}：来源有效业务编号={summary.source_type_count}，"
        f"可修复来电={summary.eligible_call_count}，"
        f"更新来电={summary.updated_call_count if args.apply else 0}，"
        f"更新画像={summary.updated_profile_count if args.apply else 0}。"
    )


if __name__ == "__main__":
    main()
