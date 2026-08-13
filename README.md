# China Stock API

一套可直接运行的中国 A 股行情 REST API，覆盖沪市、深市和北交所。当前数据适配器使用 AkShare；接口层与数据源解耦，后续可以换成已授权的交易所、券商或商业行情源。

> 数据仅供学习、研究和内部分析，不构成投资建议。公开数据源可能限流或变更；生产/交易用途应购买合规授权并遵守数据源条款。

## 启动

要求 Python 3.11+：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

打开 `http://127.0.0.1:8000` 查看股票可视化看板；打开 `http://127.0.0.1:8000/docs` 可在 Swagger 页面试用接口。

也可以使用 Docker：

```bash
docker build -t china-stock-api .
docker run --rm -p 8000:8000 china-stock-api
```

## 接口

```text
GET /health
GET /api/v1/stocks?q=浦发&limit=20
GET /api/v1/stocks/600000/quote
GET /api/v1/stocks/SH.600000/history?start=2026-01-01&end=2026-07-01&period=daily&adjust=qfq
```

代码支持 `600000`、`SH600000`、`SH.600000`、`SH:600000` 四种输入。历史行情的 `adjust` 可选：`qfq`（前复权）、`hfq`（后复权）或空字符串（不复权）。

Python 调用示例：

```python
import requests

base = "http://127.0.0.1:8000/api/v1"
quote = requests.get(f"{base}/stocks/600000/quote", timeout=30).json()
history = requests.get(
    f"{base}/stocks/600000/history",
    params={"start": "2026-01-01", "end": "2026-07-01", "adjust": "qfq"},
    timeout=30,
).json()
print(quote)
print(history["data"][-1])
```

## 定时市场情报（量化 + DeepSeek）

`scripts/market_intelligence_worker.py` 每小时生成一次只读研究包，并调用
`deepseek-v4-flash` 汇总量化快照和可选 RSS 新闻。它不操作公共 AI 账本，也不产生订单。

在 `市场分析/.env` 中配置（密钥优先用环境变量；本机已有的上级 `deepseek-Api.txt` 仅作本地回退）：

```text
DEEPSEEK_API_KEY=...
# 多个 RSS 用英文逗号分隔；留空则摘要会明确没有新闻数据。
MARKET_NEWS_RSS_URLS=https://example.com/market.rss
```

启动：

```powershell
python scripts/market_intelligence_worker.py
```

QQ/Codex 和其他消费者只能通过 `GET /api/v1/research/market-intelligence` 读取最新情报。接口不提供触发分析、提问 DeepSeek 或修改公共 AI 账本的能力。

公共 AI 的顶层审查只允许 QQ/Codex 桥接器调用以下命令生成**未持久化草案**：

```powershell
python scripts/public_ai_draft.py --question "审查当前组合，给出下一步草案"
```

该命令会因市场证据过期而拒绝运行；它不会写入 `paper/state.json`、不会创建订单，也不保存决策。用户看完草案并明确确认后，仍只能用 `paper_cli.py record-decision --user-confirmation ...` 记录决策，再由关联 `--decision-id` 的买卖命令登记订单。

市场研究快照会保留最近 14 天，供复算和走查；更早的可再生量化快照会在下一次成功刷新后自动清理。公共 AI 账本、决策和审计记录不会被该清理机制删除。

## 目录结构

```text
app/main.py                  HTTP 接口与参数校验
app/service.py               代码标准化、缓存和业务逻辑
app/providers/base.py        数据源协议
app/providers/akshare_provider.py  AkShare 适配器
app/models.py                统一响应模型
tests/                       不访问外网的单元测试
```

实时行情缓存默认 15 秒，历史行情缓存默认 5 分钟，可复制 `.env.example` 为 `.env` 后调整。若用于生产环境，建议再加入 Redis、API Key 鉴权、请求限流、监控和商业行情源。
