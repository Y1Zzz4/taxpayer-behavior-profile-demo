import pandas as pd
import json
from config import *
USE_YESTERDAY_ALERT = False   # True：启用昨日预警逻辑；False：完全关闭
# config_path = r"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/config.json"
# with open(config_path, 'r') as f:
#     config = json.load(f)

# today = config["today"]         # 202XXXXX
# date_time = config["date_time"] # 202X-XX-XX
# date = config["date"]           # XX月XX日

import sys
import os

# 获取当前文件的绝对路径
current_file_path = os.path.abspath(__file__)
# 获取当前文件所在目录
current_dir = os.path.dirname(current_file_path)
# 获取上一级目录
parent_dir = os.path.dirname(current_dir)

# 将父目录添加到系统路径
sys.path.insert(0, parent_dir)

# 现在可以导入public_config.py
import public_config


# ========== 1. 路径与基本配置 ==========
input_files = [
    f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{TARGET_DATE}/{TARGET_DATE}含核心问题.xlsx"
]
output_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{TARGET_DATE}/多日重复来电预警合并结果{TARGET_DATE}.xlsx'
sheetname = 'Sheet1'
yesterday_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{YESTERDAY_DATE}/多日重复来电预警合并结果9.15.xlsx"   # 昨日文件
# cd /cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/cxy-workplace/repeated_call/rolling
# python rolling_repeated_call.py

# 需要排除的电话号码列表
EXCLUDED_NUMBERS = {
    "01012366", "02212366", "031112366", "035112366", "047112366", "02412366", "043112366",
    "045112366", "02112366", "02512366", "057112366", "055112366", "059112366", "079112366",
    "053112366", "037112366", "02712366", "073112366", "02012366", "077112366", "089812366",
    "02312366", "02812366", "085112366", "087112366", "089112366", "02912366", "093112366",
    "097112366", "095112366", "099112366", "041112366", "057412366", "059212366", "053212366",
    "075512366", "59719427", "26035651", "32798000", "63024011", "22208000", "26035555", "12345",
    # 无区号版本
    "1012366", "2212366", "31112366", "35112366", "47112366", "2412366", "43112366",
    "45112366", "2112366", "2512366", "57112366", "55112366", "59112366", "79112366",
    "53112366", "37112366", "2712366", "73112366", "2012366", "77112366", "89812366",
    "2312366", "2812366", "85112366", "87112366", "89112366", "2912366", "93112366",
    "97112366", "95112366", "99112366", "41112366", "57412366", "59212366", "53212366",
    "75512366", "12366"
}

# 生成排除号码列表（包括去除首位0的版本）
EXCLUDED_NUMBERS_WITHOUT_LEADING_ZERO = set()
for num in EXCLUDED_NUMBERS:
    EXCLUDED_NUMBERS_WITHOUT_LEADING_ZERO.add(num)
    if num.startswith('0'):
        EXCLUDED_NUMBERS_WITHOUT_LEADING_ZERO.add(num[1:])

# ========== 2. 合并当天文件 ==========
print("开始合并指定文件...")
dfs = []
for file in input_files:
    try:
        dfs.append(pd.read_excel(file, sheet_name=sheetname, engine='openpyxl'))
    except Exception as e:
        print(f"警告: 无法读取文件 {file}，错误: {e}")
if not dfs:
    raise ValueError("没有找到可读取的Excel文件")

df_raw = pd.concat(dfs, ignore_index=True)
df_raw['转写结果'] = df_raw['转写结果'].fillna(df_raw['业务内容'])
df = df_raw.copy()

# ========== 3. 调整列顺序 ==========
prepend_cols = ["通话开始时间", "来电号码", "大模型核心问题"]
df = df[prepend_cols + [c for c in df.columns if c not in prepend_cols]]

# ========== 4. 号码清洗 ==========
def clean_number(x):
    try:
        num = str(x).strip()
        if '.' in num:
            num = num.split('.')[0]
        return num
    except:
        return ""

df = df.dropna(subset=["来电号码", "通话开始时间"])
df["来电号码"] = df["来电号码"].apply(clean_number)
df = df[df["来电号码"] != ""]
df = df[~df["来电号码"].isin(EXCLUDED_NUMBERS_WITHOUT_LEADING_ZERO)]
df["通话开始时间"] = pd.to_datetime(df["通话开始时间"])
df["日期"] = df["通话开始时间"].dt.date

# 剔除空核心问题
df = df.dropna(subset=["大模型核心问题", "通话开始时间", "来电号码", "日期"])


# ========== 5. 计算今日预警号码 ==========
last_file_df = dfs[-1].copy()  # 最后一个文件 DataFrame
last_file_df["来电号码"] = last_file_df["来电号码"].apply(clean_number)
last_file_df = last_file_df[
    (last_file_df["来电号码"] != "") &
    (~last_file_df["来电号码"].isin(EXCLUDED_NUMBERS_WITHOUT_LEADING_ZERO))
]
last_file_df["通话开始时间"] = pd.to_datetime(last_file_df["通话开始时间"])
last_file_df["日期"] = last_file_df["通话开始时间"].dt.date
last_file_df = last_file_df.dropna(subset=["大模型核心问题", "通话开始时间", "来电号码", "日期"])
max_day_dict = (
    last_file_df.groupby(["来电号码", "日期"])
    .size()
    .reset_index(name="count")
    .groupby("来电号码")["count"]
    .max()
    .to_dict()
)

# 2) 全量数据做 max_3day
daily_all = (
    df.groupby(["来电号码", "日期"])
    .size()
    .reset_index(name="count")
)
max_3day_dict = (
    daily_all.sort_values(["来电号码", "日期"])
    .groupby("来电号码")["count"]
    .rolling(3, min_periods=1)
    .sum()
    .groupby(level=0)
    .max()
    .to_dict()
)

alert_numbers = set()
for num in max_day_dict.keys() | max_3day_dict.keys():
    max_day = max_day_dict.get(num, 0)
    max_3day = max_3day_dict.get(num, 0)
    if (max_day >= 10 or max_3day >= 16) or \
       (7 <= max_day <= 9 and 7 <= max_3day <= 15) or \
       (4 < max_day <= 6 and max_3day <= 10):
        alert_numbers.add(num)

# ========== 6. 读取昨日号码 ==========
if USE_YESTERDAY_ALERT:
    try:
        df_yes = pd.read_excel(yesterday_file, sheet_name=sheetname, engine='openpyxl')
        df_yes["来电号码"] = df_yes["来电号码"].astype(str).apply(clean_number)
        yes_numbers = set(df_yes["来电号码"].unique())
    except Exception as e:
        print(f"⚠️ 无法读取昨日文件 {yesterday_file}，所有号码视为未预警：{e}")
        yes_numbers = set()
else:
    yes_numbers = set()

# ========== 7. 合并号码集合 & 捞取记录 ==========
today_all_numbers = set(last_file_df["来电号码"].unique())
force_numbers = {num for num in today_all_numbers if num in yes_numbers}
final_numbers = alert_numbers | force_numbers   # 并集：今日+昨日

# 用合并后的号码集合一次性捞记录
df_raw["来电号码"] = df_raw["来电号码"].apply(clean_number)
df_raw["通话开始时间"] = pd.to_datetime(df_raw["通话开始时间"], errors='coerce')

# 只要号码在 final_numbers 中，就全部保留
filtered_df = df_raw[df_raw["来电号码"].isin(final_numbers)].copy()

# ========== 8. 新增昨日预警列 ==========
filtered_df["昨日预警"] = filtered_df["来电号码"].isin(yes_numbers)
filtered_df["日期"] = filtered_df["通话开始时间"].dt.date
# ========== 9. 保存结果 ==========
filtered_df = filtered_df.sort_values(by=["来电号码", "通话开始时间"])
filtered_df.to_excel(output_file, index=False)
filtered_df.to_excel(f"./output/{SINGLE_DAY_OUTPUT_FILENAME}", index=False)
print(f"✅ 已保存筛选结果（含昨日预警列）至：{output_file}")