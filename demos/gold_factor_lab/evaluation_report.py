"""Render a self-contained visual report for a gold blind-replay result."""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.collector import collect_jd_history


WIDTH, HEIGHT, LEFT, RIGHT = 1120, 300, 64, 35


def _svg_line(points: list[tuple[float, float]], colour: str, width: int = 3) -> str:
    return f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="{colour}" stroke-width="{width}" stroke-linejoin="round"/>'


def _chart(rows: list[dict], trades: list[dict]) -> str:
    prices = [float(row["value"]) for row in rows]
    low, high = min(prices), max(prices)
    margin = (high - low) * 0.1 or 1
    low, high = low - margin, high + margin
    x = lambda index: LEFT + index * (WIDTH - LEFT - RIGHT) / max(1, len(rows) - 1)
    y = lambda value: 35 + (high - value) / (high - low) * (HEIGHT - 70)
    points = [(x(index), y(float(row["value"]))) for index, row in enumerate(rows)]
    by_day = {row["observed_on"]: index for index, row in enumerate(rows)}
    markers = []
    for trade in trades:
        index = by_day.get(trade["fill_day"])
        if index is None:
            continue
        colour = "#059669" if trade["action"] == "BUY" else "#dc2626"
        symbol = "买" if trade["action"] == "BUY" else "卖"
        markers.append(f'<circle cx="{x(index):.1f}" cy="{y(float(trade["price"])):.1f}" r="6" fill="{colour}"/><text x="{x(index):.1f}" y="{y(float(trade["price"])) - 11:.1f}" text-anchor="middle" class="marker">{symbol}</text>')
    return f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" aria-label="京东浙商积存金价格和交易点"><line x1="{LEFT}" y1="{HEIGHT - 35}" x2="{WIDTH - RIGHT}" y2="{HEIGHT - 35}" class="axis"/><text x="{LEFT - 8}" y="42" text-anchor="end" class="label">{high:.1f}</text><text x="{LEFT - 8}" y="{HEIGHT - 38}" text-anchor="end" class="label">{low:.1f}</text>{_svg_line(points, "#b45309")} {"".join(markers)}<text x="{LEFT}" y="{HEIGHT - 12}" class="label">{rows[0]["observed_on"]}</text><text x="{WIDTH - RIGHT}" y="{HEIGHT - 12}" text-anchor="end" class="label">{rows[-1]["observed_on"]}</text></svg>'


def build_html(result: dict, prices: list[dict]) -> str:
    metrics = result["prediction_metrics"]
    decisions = result["decisions"]
    months = sorted({item["signal_day"][:7] for item in decisions})
    monthly = []
    for month in months:
        rows = [item for item in decisions if item["signal_day"].startswith(month)]
        rules, executed = Counter(item["rule"] for item in rows), Counter(item["executed"] for item in rows)
        accuracy = sum(item["next_day_direction"] == item["actual_next_day_direction"] for item in rows) / len(rows) * 100
        monthly.append(f'<tr><td>{month}</td><td>{len(rows)}</td><td>{rules["BUY"]}</td><td>{executed["BUY"] + executed["SELL"]}</td><td>{accuracy:.1f}%</td></tr>')
    strategy_return, hold_return = float(result["return_percent"]), float(result["buy_and_hold_return_percent"])
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金盲测评估</title>
<style>body{{max-width:1120px;margin:28px auto;padding:0 18px;font-family:system-ui,"Microsoft YaHei",sans-serif;color:#172033;background:#f8fafc}}h1{{margin-bottom:4px}}.sub,.label{{color:#64748b}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}.card,section{{background:#fff;border:1px solid #dbe3ec;border-radius:10px;padding:14px}}.value{{font-size:25px;font-weight:700}}.good{{color:#047857}}.bad{{color:#b91c1c}}svg{{width:100%;height:auto;background:#fff;border:1px solid #dbe3ec;border-radius:10px}}.axis{{stroke:#aab5c1}}.label,.marker{{font-size:12px}}.marker{{font-weight:700}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e5e7eb;text-align:left;padding:8px}}.note{{border-left:4px solid #d97706;padding:12px;background:#fff7ed}}</style>
<h1>黄金盲测评估：6–8 月</h1><p class="sub">日线收盘后决策、下一条日线成交；买入费 0%，卖出费 0.4%。八月为规则开发后首次留出期。</p>
<div class="cards"><div class="card"><div class="sub">策略净收益</div><div class="value {'good' if strategy_return > 0 else 'bad' if strategy_return < 0 else ''}">{strategy_return:+.2f}%</div><div>最终 {result['final_value']:,.2f} 元</div></div><div class="card"><div class="sub">买入持有</div><div class="value {'good' if hold_return > 0 else 'bad'}">{hold_return:+.2f}%</div><div>最终 {result['buy_and_hold_final_value']:,.2f} 元</div></div><div class="card"><div class="sub">实际交易 / 手续费</div><div class="value">{result['trade_count']} / {result['fees_paid']:,.2f} 元</div><div>买入不收费</div></div><div class="card"><div class="sub">模型方向准确率</div><div class="value">{metrics['directional_accuracy_percent']:.2f}%</div><div>看涨正确率 {metrics['up_call_precision_percent']:.2f}%</div></div></div>
<section><h2>京东浙商积存金价格与实际成交</h2>{_chart(prices, result['trades'])}<p class="sub">橙线为公开日线报价；绿色“买”、红色“卖”为实际模拟成交点。本轮没有成交点，表示策略保持现金。</p></section>
<section><h2>按月判断与执行</h2><table><thead><tr><th>月份</th><th>预测天数</th><th>规则买入候选</th><th>实际成交</th><th>方向准确率</th></tr></thead><tbody>{''.join(monthly)}</tbody></table></section>
<p class="note"><b>如何阅读：</b>策略的 0.00% 不是盈利，而是因规则与模型门槛没有同时通过而保持空仓；它仅说明在这段总体下跌区间中避免了买入持有的回撤。整体方向准确率低于 50%，尚不能证明 DeepSeek 具有可交易的预测优势。</p>
<section><h2>冻结规则</h2><p>{html.escape(json.dumps(result['frozen_rule'], ensure_ascii=False))}</p></section></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    period = result["target_period"]
    rows = [row for row in collect_jd_history(period_type="m6") if period["start"] <= row["observed_on"] <= period["end"]]
    if not rows:
        raise RuntimeError("current JD chart no longer covers the evaluation period")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(result, rows), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
