"""Create an explanatory historical chart for the independent gold demo.

The chart deliberately separates values with incompatible units. It shows the
actual JD price, comparable normalised changes, and yield levels in separate
panels so crossings of unrelated lines are not mistaken for a relationship.
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.analysis import describe
from demos.gold_factor_lab.collector import collect_factor_panel


DEFAULT_OUTPUT = Path("data/gold_lab/reports/gold_history.html")
WIDTH = 1180
LEFT, RIGHT = 82, 48
PLOT_WIDTH = WIDTH - LEFT - RIGHT
SERIES = {
    "jd_zheshang_gold": ("京东浙商积存金", "#b45309"),
    "usd_cny": ("美元兑人民币", "#2563eb"),
    "broad_us_dollar": ("广义美元指数", "#7c3aed"),
    "wti_crude": ("WTI 原油", "#dc2626"),
    "us_10y_real_yield": ("美国 10 年实际利率", "#059669"),
    "us_10y_nominal_yield": ("美国 10 年名义利率", "#0f766e"),
}


def _date_number(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _x(value: str, *, start: float, end: float) -> float:
    return LEFT + (_date_number(value) - start) / (end - start or 1) * PLOT_WIDTH


def _domain(values: list[float]) -> tuple[float, float]:
    low, high = min(values), max(values)
    margin = (high - low) * 0.12 or max(abs(high) * 0.03, 1)
    return low - margin, high + margin


def _y(value: float, *, low: float, high: float, top: int, height: int) -> float:
    return top + height - (value - low) / (high - low) * height


def _polyline(rows: list[dict], *, start: float, end: float, low: float, high: float, top: int, height: int) -> str:
    return " ".join(
        f'{_x(row["observed_on"], start=start, end=end):.1f},{_y(float(row["value"]), low=low, high=high, top=top, height=height):.1f}'
        for row in rows
    )


def _legend(names: list[str], *, y: int) -> str:
    bits, x = [], LEFT
    for name in names:
        label, colour = SERIES[name]
        bits.append(f'<line x1="{x}" y1="{y}" x2="{x + 18}" y2="{y}" stroke="{colour}" class="legend-line"/>')
        bits.append(f'<text x="{x + 24}" y="{y + 4}" class="legend">{html.escape(label)}</text>')
        x += 24 + len(label) * 14
    return "".join(bits)


def _panel(title: str, series: dict[str, list[dict]], *, top: int, height: int, start: float, end: float, normalise: bool = False) -> str:
    adjusted: dict[str, list[dict]] = {}
    for name, rows in series.items():
        if rows:
            first = float(rows[0]["value"])
            adjusted[name] = [{**row, "value": float(row["value"]) / first * 100 if normalise else float(row["value"])} for row in rows]
    values = [float(row["value"]) for rows in adjusted.values() for row in rows]
    low, high = _domain(values)
    bottom = top + height
    grid = []
    for ratio in (0, 0.5, 1):
        value = low + (high - low) * ratio
        y = _y(value, low=low, high=high, top=top, height=height)
        grid.extend((f'<line x1="{LEFT}" y1="{y:.1f}" x2="{WIDTH - RIGHT}" y2="{y:.1f}" class="grid"/>', f'<text x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis-label">{value:.2f}</text>'))
    paths = [f'<polyline points="{_polyline(rows, start=start, end=end, low=low, high=high, top=top, height=height)}" stroke="{SERIES[name][1]}" class="series"/>' for name, rows in adjusted.items()]
    first_date = min(row["observed_on"] for rows in adjusted.values() for row in rows)
    last_date = max(row["observed_on"] for rows in adjusted.values() for row in rows)
    unit = "指数（各线起点 = 100）" if normalise else "原始单位"
    return (f'<text x="{LEFT}" y="{top - 35}" class="panel-title">{html.escape(title)}</text><text x="{WIDTH - RIGHT}" y="{top - 16}" text-anchor="end" class="axis-label">{unit}</text>{_legend(list(adjusted), y=top - 18)}{''.join(grid)}<line x1="{LEFT}" y1="{bottom}" x2="{WIDTH - RIGHT}" y2="{bottom}" class="axis"/>{''.join(paths)}<text x="{LEFT}" y="{bottom + 20}" class="axis-label">{first_date}</text><text x="{WIDTH - RIGHT}" y="{bottom + 20}" text-anchor="end" class="axis-label">{last_date}</text>')


def _change_text(name: str, rows: list[dict]) -> str:
    start, end = float(rows[0]["value"]), float(rows[-1]["value"])
    return f"{(end - start) * 100:+.1f} 个基点" if "yield" in name else f"{(end / start - 1) * 100:+.2f}%"


def build_html(panel: dict[str, list[dict]]) -> str:
    gold = panel["jd_zheshang_gold"]
    start = min(_date_number(row["observed_on"]) for rows in panel.values() for row in rows)
    end = max(_date_number(row["observed_on"]) for rows in panel.values() for row in rows)
    comparable = {name: panel[name] for name in ("jd_zheshang_gold", "usd_cny", "broad_us_dollar", "wti_crude") if panel.get(name)}
    yields = {name: panel[name] for name in ("us_10y_real_yield", "us_10y_nominal_yield") if panel.get(name)}
    height, gap = 190, 92
    charts = [_panel("1. 实际展示价格：京东浙商积存金（元/克）", {"jd_zheshang_gold": gold}, top=64, height=height, start=start, end=end), _panel("2. 同尺度比较：各变量相对期初的变化", comparable, top=64 + height + gap, height=height, start=start, end=end, normalise=True), _panel("3. 利率环境：美国 10 年利率（%）", yields, top=64 + 2 * (height + gap), height=height, start=start, end=end)]
    summary = describe(panel)
    changes = "".join(f'<li><b>{html.escape(SERIES[name][0])}</b>：{_change_text(name, rows)}</li>' for name, rows in panel.items() if name in SERIES and rows)
    correlations = "".join(
        f'<li>{html.escape(SERIES.get(name, (name, ""))[0])}：{diagnostic["contemporaneous_return_correlation"]:+.3f}</li>'
        for name, diagnostic in summary["factor_diagnostics"].items()
        if diagnostic["contemporaneous_return_correlation"] is not None
    ) or "<li>样本不足，未计算。</li>"
    svg_height = 3 * height + 2 * gap + 80
    return f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>黄金因素观察</title>
<style>body{{max-width:1180px;margin:28px auto;padding:0 18px;font-family:system-ui,"Microsoft YaHei",sans-serif;color:#172033;line-height:1.6}}h1{{margin-bottom:4px}}.sub{{color:#526071}}svg{{width:100%;height:auto;border:1px solid #dce3eb;border-radius:10px;background:#fff}}.grid{{stroke:#edf1f5}}.axis{{stroke:#aab5c1}}.series{{fill:none;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}}.panel-title{{font-size:16px;font-weight:700}}.axis-label,.legend{{font-size:11px;fill:#64748b}}.legend-line{{stroke-width:3}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin:18px 0}}.card{{border:1px solid #dce3eb;border-radius:10px;padding:14px;background:#f8fafc}}.warning{{border-left:4px solid #d97706;padding:12px 14px;background:#fff7ed}}ul{{padding-left:22px}}</style>
<h1>黄金升降因素观察（研究版）</h1><p class=\"sub\">区间：{html.escape(gold[0]["observed_on"])} 至 {html.escape(gold[-1]["observed_on"])}。图 1 保留实际价格；图 2 只比较“起点=100”后的相对涨跌；图 3 单独展示同为百分比的利率，避免不同单位的曲线互相误导。</p><svg viewBox=\"0 0 {WIDTH} {svg_height}\" role=\"img\" aria-label=\"黄金价格与影响因素\">{''.join(charts)}</svg>
<div class=\"cards\"><section class=\"card\"><h2>本段变化</h2><ul>{changes}</ul></section><section class=\"card\"><h2>观察到的同步关系</h2><p>以下是同日收益率相关系数，仅描述样本内共动，不是因果关系，也不是预测信号。</p><ul>{correlations}</ul></section></div>
<h2>如何解释黄金的升降</h2><ul><li><b>国际金价（美元/盎司）上升</b>，通常会抬高人民币计价黄金。</li><li><b>美元兑人民币上升</b>（人民币走弱），在国际金价不变时通常会抬高人民币金价；反之则形成压力。</li><li><b>实际利率上升</b>通常增加持有无息黄金的机会成本，常被视为利空背景；实际利率下降则相反。</li><li><b>广义美元走强</b>常与美元计价黄金承压同时出现，但在避险阶段两者也可能同涨。</li><li><b>原油</b>反映通胀和风险环境，方向并不固定，只适合作为背景变量。</li><li><b>渠道价差、手续费与报价规则</b>也会影响京东浙商积存金展示价，不能被外部宏观变量完全解释。</li></ul>
<p class=\"warning\"><b>当前结论边界：</b>本报告尚未接入连续的国际现货金（XAUUSD）与国内基准金（如 Au99.99）曲线。因此它可以展示“京东价格与汇率、美元、利率等因素是否同向”，但不能严谨地把本段涨跌归因到某一个因素。补齐两条基准金价后，才能将变化分解为国际金价、汇率和渠道价差三部分。</p><p class=\"sub\">数据：京东浙商积存金产品历史报价；FRED 宏观日度序列。数据频率与发布日期不同，缺失日不会被伪造或前填。</p></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    panel = collect_factor_panel(start=args.start, end=args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(panel), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
