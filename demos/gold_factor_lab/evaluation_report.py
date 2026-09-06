"""Render a self-contained visual report for a deterministic gold replay."""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.collector import collect_jd_history


WIDTH, HEIGHT, LEFT, RIGHT = 1120, 300, 64, 35


def _svg_line(points: list[tuple[float, float]], colour: str, width: int = 3) -> str:
    joined = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline points="{joined}" fill="none" stroke="{colour}" stroke-width="{width}" stroke-linejoin="round"/>'


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
        is_buy = trade["action"].startswith("BUY") or trade["action"].startswith("ADD")
        colour, symbol = ("#059669", "买") if is_buy else ("#dc2626", "卖")
        price = float(trade["price"])
        markers.append(
            f'<circle cx="{x(index):.1f}" cy="{y(price):.1f}" r="6" fill="{colour}"/>'
            f'<text x="{x(index):.1f}" y="{y(price) - 11:.1f}" text-anchor="middle" class="marker">{symbol}</text>'
        )
    return (
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" aria-label="京东浙商积存金价格与交易点">'
        f'<line x1="{LEFT}" y1="{HEIGHT - 35}" x2="{WIDTH - RIGHT}" y2="{HEIGHT - 35}" class="axis"/>'
        f'<text x="{LEFT - 8}" y="42" text-anchor="end" class="label">{high:.1f}</text>'
        f'<text x="{LEFT - 8}" y="{HEIGHT - 38}" text-anchor="end" class="label">{low:.1f}</text>'
        f'{_svg_line(points, "#b45309")} {"".join(markers)}'
        f'<text x="{LEFT}" y="{HEIGHT - 12}" class="label">{rows[0]["observed_on"]}</text>'
        f'<text x="{WIDTH - RIGHT}" y="{HEIGHT - 12}" text-anchor="end" class="label">{rows[-1]["observed_on"]}</text>'
        "</svg>"
    )


def build_html(result: dict, prices: list[dict]) -> str:
    """Build an HTML artifact from an already-produced replay result."""
    metrics, decisions = result["prediction_metrics"], result["decisions"]
    monthly = []
    for month in sorted({item["signal_day"][:7] for item in decisions}):
        rows = [item for item in decisions if item["signal_day"].startswith(month)]
        rules, executed = Counter(item["rule"] for item in rows), Counter(item["executed"] for item in rows)
        accuracy = 100 * sum(item["next_day_direction"] == item["actual_next_day_direction"] for item in rows) / len(rows)
        monthly.append(f"<tr><td>{month}</td><td>{len(rows)}</td><td>{rules['BUY']}</td><td>{executed['BUY'] + executed['SELL']}</td><td>{accuracy:.1f}%</td></tr>")
    strategy_return, hold_return = float(result["return_percent"]), float(result["buy_and_hold_return_percent"])
    tone = lambda value: "good" if value > 0 else "bad" if value < 0 else ""
    frozen_rule = html.escape(json.dumps(result["frozen_rule"], ensure_ascii=False))
    return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>黄金历史回放评估</title>
<style>
body{{max-width:1120px;margin:28px auto;padding:0 18px;font-family:system-ui,"Microsoft YaHei",sans-serif;color:#172033;background:#f8fafc}}
h1{{margin-bottom:4px}}.sub,.label{{color:#64748b}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.card,section{{background:#fff;border:1px solid #dbe3ec;border-radius:10px;padding:14px}}.value{{font-size:25px;font-weight:700}}.good{{color:#047857}}.bad{{color:#b91c1c}}
svg{{width:100%;height:auto;background:#fff;border:1px solid #dbe3ec;border-radius:10px}}.axis{{stroke:#aab5c1}}.label,.marker{{font-size:12px}}.marker{{font-weight:700}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e5e7eb;text-align:left;padding:8px}}.note{{border-left:4px solid #d97706;padding:12px;background:#fff7ed}}
</style>
<h1>黄金历史回放评估</h1><p class="sub">日线收盘后生成规则信号，下一条日线报价成交；买入费率 0%，实际卖出费率 0.4%。</p>
<div class="cards"><div class="card"><div class="sub">策略净收益</div><div class="value {tone(strategy_return)}">{strategy_return:+.2f}%</div><div>期末资产 {result['final_value']:,.2f} 元</div></div>
<div class="card"><div class="sub">买入并持有</div><div class="value {tone(hold_return)}">{hold_return:+.2f}%</div><div>期末资产 {result['buy_and_hold_final_value']:,.2f} 元</div></div>
<div class="card"><div class="sub">实际交易 / 手续费</div><div class="value">{result['trade_count']} / {result['fees_paid']:,.2f} 元</div><div>买入不收费</div></div>
<div class="card"><div class="sub">规则方向准确率</div><div class="value">{metrics['directional_accuracy_percent']:.2f}%</div><div>看涨精度 {metrics['up_call_precision_percent']:.2f}%</div></div></div>
<section><h2>京东浙商积存金价格与实际成交</h2>{_chart(prices, result['trades'])}<p class="sub">橙线为公开日线报价；绿色“买”、红色“卖”为模拟成交点。未出现成交点时，表示策略保持现金。</p></section>
<section><h2>按月判断与执行</h2><table><thead><tr><th>月份</th><th>判断天数</th><th>规则买入候选</th><th>实际成交</th><th>方向准确率</th></tr></thead><tbody>{''.join(monthly)}</tbody></table></section>
<p class="note"><b>如何阅读：</b>策略为 0.00% 不是盈利；它可能只是因规则没有触发而保持现金。方向准确率也不等同于可交易优势，必须结合费用、交易次数和未参与调参的验证期评估。</p>
<section><h2>冻结规则</h2><p>{frozen_rule}</p></section></html>"""


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
