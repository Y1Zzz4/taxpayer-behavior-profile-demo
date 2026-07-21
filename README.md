# 12366纳税缴费服务热线来电画像与坐席辅助系统

本项目用于验证12366纳税缴费服务热线来电数据的号码级整合、历史画像构建和坐席服务辅助效果，适用于涉税费咨询、信息查询、服务投诉、违法举报和意见建议等服务场景。系统从 Excel 导入来电记录，按来电号码形成历史轨迹；接到新来电时，前端可以快速展示历史信息，并由大模型实时生成个性化接待策略。

## 主要功能

- 从单日或多日 Excel 读取来电数据；
- 使用大模型提取核心问题、咨询主体、企业身份、解决状态、服务信号、业务熟练度等结构化字段；
- 按来电号码聚合历史来电、重复咨询、未解决事项、工单和服务情况；
- 支持新 Excel 文件增量导入和重复文件跳过；
- 输入来电号码后即时展示历史画像；
- 根据号码历史实时生成坐席接待策略；
- 分页查看脱敏后的历史来电轨迹；
- 可视化查看画像规模、来电趋势、事项分类和服务情况；
- 模型不可用时提供规则化辅助建议；
- 将号码画像、来电轨迹和更新结果导出为 Excel。

系统生成的是服务过程辅助信息，不提供具体税费政策答案，也不用于执法判断或坐席绩效评价。

## 处理流程

```text
来电 Excel
    ↓
字段读取与文本脱敏
    ↓
大模型结构化分析
    ↓
来电轨迹与号码画像
    ↓
前端输入来电号码
    ├── 即时展示历史信息
    └── 实时生成坐席接待策略
```

## 项目结构

```text
data/
  raw/          原始 Excel
  database/     SQLite 数据库
  output/       Excel 导出结果
prompts/        模型提示词
scripts/        初始化、增量、导出和服务启动脚本
src/            核心业务代码
tests/          自动化测试
web/            坐席辅助页面
```

## 环境要求

- Python 3.10 或以上；
- 可访问 OpenAI 兼容的大模型接口；
- Windows 11 + WSL Ubuntu、Linux 或 macOS。

安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 配置

复制 `.env.example` 为 `.env`，填写：

```text
LLM_BASE_URL=大模型接口地址
LLM_API_KEY=大模型密钥
LLM_MODEL=模型名称
PHONE_HASH_KEY=号码查询哈希密钥
PHONE_ENCRYPTION_KEY=号码加密密钥
DATABASE_PATH=data/database/taxpayer_profiles.sqlite3
```

可以生成本地号码保护密钥：

```bash
.venv/bin/python scripts/generate_local_secrets.py
```

`.env`、原始数据、数据库和导出结果不会提交到代码仓库。

## 输入数据

Excel 至少应包含：

- `业务编号`
- `来电号码`
- `登记日期`

分析过程主要使用：

- `通话开始时间`
- `通话结束时间`
- `转写结果`
- `业务内容`
- `答复内容`
- `登记处理方式`
- `申请人员身份`

人工登记的业务内容和答复内容用于确定本通主问题和主要办理信息，转写内容用于补充对话过程、有效问答和服务信号。

来电号码不限制固定位数，但必须为数字；空格、横线和括号等常见分隔符会在查询前统一处理。

## 构建数据库

首次构建默认读取 `data/raw/raw_data.xlsx`：

```bash
.venv/bin/python scripts/init_database.py
```

当前初始化样例约定 1—9 日记录作为已有分析历史，10 日记录重新调用模型分析，并在前期历史基础上形成增量画像。

指定输入和数据库：

```bash
.venv/bin/python scripts/init_database.py data/raw/raw_data.xlsx \
  --database data/database/taxpayer_profiles.sqlite3
```

## 增量导入

导入一个新的 Excel：

```bash
.venv/bin/python scripts/update_database.py data/raw/new_calls.xlsx \
  --database data/database/taxpayer_profiles.sqlite3
```

省略文件参数时，程序按日期顺序扫描 `data/raw/`：

```bash
.venv/bin/python scripts/update_database.py
```

相同文件会通过文件指纹跳过；相同业务编号不会重复写入。模型分析失败的记录可在后续运行中继续重试。

## 启动坐席辅助页面

```bash
.venv/bin/python scripts/run_demo.py \
  --database data/database/taxpayer_profiles.sqlite3
```

浏览器访问：

```text
http://127.0.0.1:8000
```

如果端口已被占用：

```bash
.venv/bin/python scripts/run_demo.py \
  --database data/database/taxpayer_profiles.sqlite3 \
  --port 8001
```

页面左侧提供三个功能入口：

- `来电服务工作台`：历史画像优先返回，实时接待策略随后生成；
- `画像数据概览`：查看画像规模、来电趋势和数据库主要特征；
- `历史来电记录`：分页查看全部来电，或按完整号码安全筛选。

模型未在交互时限内返回时，画像仍可正常查看，并自动显示规则化辅助建议。历史来电列表仅展示脱敏号码。

## 导出结果

```bash
.venv/bin/python scripts/export_results.py \
  --database data/database/taxpayer_profiles.sqlite3 \
  --output data/output/taxpayer_profiles.xlsx
```

导出文件包含：

- `号码画像`
- `来电轨迹`
- `更新摘要`

导出结果含完整来电号码，应按敏感数据管理。

## 数据存储

SQLite 使用三张核心表：

- `caller_profiles`：号码级历史画像；
- `call_trajectories`：每通来电的结构化轨迹；
- `update_logs`：文件批次和处理结果。

坐席接待建议不会写入画像表，而是在查询时根据最新历史实时生成。

数据库不保存原始转写、业务内容、答复内容、录音路径、纳税人名称和社会信用代码。号码使用 HMAC 建立查询索引，并使用 Fernet 加密保存；发送给大模型的文本会先处理常见号码、身份证号、社会信用代码、邮箱和账号信息。

## 测试

```bash
.venv/bin/python -m pytest -q
```

测试覆盖数据读取、号码保护、字段抽取、画像聚合、重复来电、增量幂等、Excel 导出和实时接待策略等主要流程。

## 使用边界

- 当前版本面向单机演示和效果验证；
- 一个来电号码对应一个历史画像；
- 大模型结果需要结合实际业务进行人工判断；
- 正式部署前需要补充账号权限、访问审计和敏感信息展示控制；
- 具体政策答复应接入独立、可追溯的政策知识服务。
