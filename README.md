# 12366 纳税服务热线坐席服务辅助系统

本系统面向纳税服务热线服务场景，对增量来电记录进行结构化分析，维护号码维度的
近期服务画像，并为坐席提供历史信息查询和接待辅助。

系统输出用于辅助坐席了解历史服务情况和选择沟通方式，不替代税费政策认定、业务
审批或人工判断。

## 核心能力

- 增量处理来电记录，提取标准化业务信息并更新号码画像；
- 查询近期来电、重点咨询事项、解决情况和服务事实；
- 基于脱敏历史信息生成实时接待建议；
- 提供画像统计、历史记录和规则说明页面；
- 提供管理员与坐席两类访问权限；
- 导出来电轨迹、画像结果和批次处理摘要。

## 技术架构

项目采用 Python、SQLite 和原生 Web 技术，面向单机部署场景。主要模块如下：

| 模块 | 职责 |
| --- | --- |
| `application` | 增量处理编排、查询用例和响应契约 |
| `ingestion` | 输入适配、字段复用策略、指纹和标准行契约 |
| `analysis` | 模型调用、结果校验、缓存、并发和重复诉求分析 |
| `profiles` | 号码画像的确定性聚合 |
| `persistence` | SQLite 版本迁移 |
| `presentation` | HTTP 路由、鉴权边界和静态资源响应 |
| `web` | 浏览器端页面和交互 |

数据库变更通过 `PRAGMA user_version` 管理。启动和增量处理时会自动执行已注册的
前向迁移，并拒绝打开高于当前程序支持版本的数据库。

## 环境要求

- Python 3.10 或更高版本；
- 可访问的 OpenAI 兼容模型服务；
- 支持 SQLite 的本地文件系统。

安装项目及运行依赖：

```bash
python -m pip install -e .
```

如需运行测试：

```bash
python -m pip install -e ".[dev]"
```

## 配置

复制 `.env.example` 为 `.env`，并为当前环境设置独立配置：

| 环境变量 | 用途 |
| --- | --- |
| `LLM_BASE_URL` | OpenAI 兼容模型服务地址 |
| `LLM_API_KEY` | 模型服务凭据 |
| `LLM_MODEL` | 模型名称 |
| `PHONE_HASH_KEY` | 号码检索使用的 HMAC 密钥 |
| `PHONE_ENCRYPTION_KEY` | 号码加密使用的 Fernet 密钥 |
| `DATABASE_PATH` | SQLite 数据库路径 |
| `DEFAULT_ADMIN_USERNAME` | 初始管理员用户名 |
| `DEFAULT_ADMIN_PASSWORD` | 初始管理员密码 |
| `DEFAULT_AGENT_USERNAME` | 初始坐席用户名 |
| `DEFAULT_AGENT_PASSWORD` | 初始坐席密码 |

可使用以下命令在本地生成号码保护密钥：

```bash
python scripts/generate_local_secrets.py
```

首次部署前必须替换示例配置中的所有占位值。

## 增量输入契约

每个输入文件表示一个独立增量批次。默认 Excel 适配器读取第一个工作表，至少要求
以下字段：

| 字段 | 说明 |
| --- | --- |
| `业务编号` | 单通记录的稳定唯一标识 |
| `来电号码` | 号码画像关联键 |
| `登记日期` | 记录登记时间 |

系统会接收白名单内的原始登记事实。以下已有分析字段允许在增量批次中复用：

- `大模型核心问题`；
- `一级专题类别`；
- `二级标签`。

`申请人员身份`仅作为身份判断证据，不直接作为最终分析结论。其他分析字段由当前
规则或模型重新生成；输入文件不需要包含历史流程中的可选判断字段。

批次处理遵循以下一致性规则：

- 相同批次指纹且已成功完成时，直接幂等跳过；
- 相同业务编号且来源内容一致时，跳过该记录；
- 相同业务编号但来源内容变化时，拒绝覆盖并写入冲突审计；
- 画像、来电轨迹、批次日志和冲突记录在同一数据库事务中提交；
- 连续模型失败触发熔断时，不写入未完成批次的业务数据。

## 执行增量更新

```bash
python scripts/update_database.py /path/to/incremental.xlsx \
  --database /path/to/profiles.sqlite3
```

模型请求默认使用有限并发和本地断点缓存。可通过 `--workers` 设置并发数，通过
`--cache` 指定缓存路径，或通过 `--no-cache` 禁用缓存。

批次开始、完成、行处理失败和熔断事件以单行 JSON 输出。运行日志不记录电话号码、
转写内容、业务原文或模型提示词。

## 启动服务

```bash
python scripts/run_demo.py \
  --database /path/to/profiles.sqlite3 \
  --host 127.0.0.1 \
  --port 8000
```

启动后访问 `http://127.0.0.1:8000`。

当前 HTTP 服务用于受控单机环境。需要跨主机访问时，应在完成安全评估后配置反向
代理、TLS、网络访问控制、日志留存和数据库备份，不应直接将内置服务暴露到公网。

## 导出结果

```bash
python scripts/export_results.py \
  --database /path/to/profiles.sqlite3 \
  --output /path/to/output.xlsx
```

导出文件包含号码画像、来电轨迹和批次更新摘要。导出结果可能包含业务数据，应按
所在组织的数据管理要求保存和传递。

## 扩展输入格式

新增表格来源时，实现
`taxpayer_profile.ingestion.contracts.TabularInputAdapter`：

- `identify()` 返回非敏感来源名称和稳定的 SHA-256 指纹；
- `read_rows()` 将来源数据映射为标准字段字典。

通过 `process_workbook(..., input_adapter=adapter)` 注入适配器即可复用现有模型
分析、幂等检查、冲突审计、画像聚合和 SQLite 事务，无需修改应用编排逻辑。

## 安全与数据边界

- 来电号码使用密钥哈希进行关联检索，并加密保存原值；
- 发送模型前对常见标识信息进行脱敏；
- 冲突审计只保存业务编号和双方指纹，不保存冲突原文；
- 实时接待建议不写入画像数据库；
- 系统不根据画像直接生成具体税费政策结论；
- SQLite 数据库、模型缓存、导出文件和 `.env` 均应视为受保护数据。

## 验证

运行完整测试：

```bash
pytest -q
```

检查 Python 和前端脚本语法：

```bash
python -m compileall -q src scripts
node --check web/app.js
```
