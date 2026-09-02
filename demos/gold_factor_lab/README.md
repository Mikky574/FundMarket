# 黄金因子实验室 Demo

这是一个与网站、QQ、账本及生产评估数据完全隔离的只读实验。实时主线是京东浙商积存金分钟走势；日线与美国十年期名义/实际利率、广义美元指数、美元兑人民币、WTI 原油仅作为慢变量背景，不产生交易指令。

实时快照（默认仅取一次，不写文件）：

```powershell
.\.venv\Scripts\python.exe demos\gold_factor_lab\watch_live.py --samples 1
```

持续采集任务（独立 SQLite，未连接网站、QQ 或账本）：

```powershell
.\.venv\Scripts\python.exe demos\gold_factor_lab\live_task.py --once
```

历史曲线（生成独立 HTML）：

```powershell
.\.venv\Scripts\python.exe demos\gold_factor_lab\history_chart.py --start 2026-08-03 --end 2026-09-03
```

```powershell
.\.venv\Scripts\python.exe demos\gold_factor_lab\run_demo.py --start 2026-08-03 --end 2026-09-03
```

盲测回放（默认取当前日期前三个月的完整月份；信号在每日收盘后生成、以下一个日线报价成交；单边手续费为 0.4%）：

```powershell
# 先跑不调用模型的规则基线
.\.venv\Scripts\python.exe demos\gold_factor_lab\blind_replay.py --month 2026-06-01 --output data\gold_lab\evaluations\june_rule.json

# 由启动命令临时提供密钥；脚本不会读取或写入任何密钥文件
$env:DEEPSEEK_API_KEY = "<your-key>"
.\.venv\Scripts\python.exe demos\gold_factor_lab\blind_replay.py --month 2026-06-01 --deepseek --output data\gold_lab\evaluations\june_deepseek.json
Remove-Item Env:DEEPSEEK_API_KEY
```

DeepSeek 只会看到顺序编号的历史行（`day`），不会得到日历日期、之后的价格或未来数据。该回放使用“日线收盘后可知”的探索性假设；京东历史图没有提供可核验的原始发布时间，因此结果只能用于方法筛选，不能视作实盘盈利证明。

原始历史点默认只在本次采集时间可用，防止回测把后来取得的图表数据当作当时已知信息。任何“次日可用”的假设须在后续评估阶段单独版本化、验证，不能直接变成实盘规则。
