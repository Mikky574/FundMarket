# 历史评估数据采集

评估数据和生产行情、用户账户、公共 AI 账本完全隔离。每条记录必须包含
`available_at`（带时区的首次可见时间）；没有该字段的新闻或行情不进入评估集。

## 基金净值

以保守规则导入：基金某日净值在当天 20:00（Asia/Shanghai）后才对回放 AI 可见。

```powershell
.\.venv\Scripts\python.exe scripts\collect_evaluation_market.py `
  --fund 018099 --fund 007467 --start 2024-01-01 --end 2026-08-13
```

重复运行相同来源与相同数值是幂等的；同一基金同一日期出现不同数据会拒绝覆盖，
必须保留原始记录并另行核验来源。

## 新闻与公告

先人工或采集程序整理 UTF-8 NDJSON；一行一条新闻。`published_at` 可以早于
`available_at`，但回放只使用 `available_at` 不晚于回放时点的内容。

```json
{"source":"中国证监会","url":"https://example.invalid/notice","title":"公告标题","body":"公告正文或合规摘要","published_at":"2025-01-15T09:00:00+08:00","available_at":"2025-01-15T09:00:00+08:00","entities":["保险","018099"],"event_type":"policy","reliability":"primary"}
```

导入：

```powershell
.\.venv\Scripts\python.exe scripts\collect_evaluation_news.py --input .\historical-news.ndjson
```

优先级：交易所、基金公司、监管机构、统计局、央行等一手来源；媒体新闻仅作线索，
应标为 `secondary`。无发布时间、无链接、无法确认来源的材料不应进入正式评估集。

持续收集 RSS（只适合后续新增新闻；历史新闻仍使用 NDJSON 回填）：

```powershell
.\.venv\Scripts\python.exe scripts\collect_evaluation_rss.py --url https://example.com/feed.xml
```

## 基准与行业指数

```powershell
.\.venv\Scripts\python.exe scripts\collect_evaluation_indexes.py `
  --benchmark 000300 --industry 保险 --industry 半导体 `
  --start 2024-01-01 --end 2026-08-14
```

基准和行业收盘值按收盘后 18:00 才对回放可见；基金净值按 20:00 才对回放可见。
如果数据源不可访问，脚本会失败而不写入不完整或猜测的数据，稍后重试即可。

## 使用约束

评估 API 只返回指定 `as_of` 当天已经可见的行情与新闻。未来净值仅由评分接口
在 T+1、T+5、T+20 等结果已存在时读取，绝不进入 AI 的分析上下文。
