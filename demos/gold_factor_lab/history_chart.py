"""Create a self-contained historical chart for the independent gold demo."""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.collector import collect_factor_panel


DEFAULT_OUTPUT = Path("data/gold_lab/reports/gold_history.html")


def _line(rows: list[dict], *, width: int, top: int, height: int) -> str:
    values = [float(row["value"]) for row in rows]
    low, high = min(values), max(values)
    spread = high - low or 1
    points = []
    for index, value in enumerate(values):
        x = 50 + index * (width - 70) / max(1, len(values) - 1)
        y = top + height - 20 - (value - low) / spread * (height - 40)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def build_html(panel: dict[str, list[dict]]) -> str:
    gold = panel["jd_zheshang_gold"]
    width, panel_height = 1200, 220
    charts = [("京东浙商积存金（元/克）", gold, "#d97706")]
    for name in ("usd_cny", "broad_us_dollar", "us_10y_real_yield", "us_10y_nominal_yield", "wti_crude"):
        if panel.get(name):
            charts.append((name, panel[name], "#2563eb"))
    svg_height = 40 + panel_height * len(charts)
    groups = []
    for index, (title, rows, colour) in enumerate(charts):
        top = index * panel_height
        values = [float(row["value"]) for row in rows]
        groups.append(
            f'<text x="50" y="{top + 22}" class="title">{html.escape(title)}</text>'
            f'<text x="1060" y="{top + 22}" class="label">{min(values):.4g} — {max(values):.4g}</text>'
            f'<line x1="50" y1="{top + panel_height - 20}" x2="1130" y2="{top + panel_height - 20}" class="axis"/>'
            f'<polyline points="{_line(rows, width=width, top=top, height=panel_height)}" stroke="{colour}" class="series"/>'
            f'<text x="50" y="{top + panel_height - 4}" class="label">{rows[0]["observed_on"]}</text>'
            f'<text x="1060" y="{top + panel_height - 4}" class="label">{rows[-1]["observed_on"]}</text>'
        )
    metadata = {
        name: {"rows": len(rows), "source": rows[0]["source"] if rows else None}
        for name, rows in panel.items()
    }
    return f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>黄金历史曲线</title>
<style>body{{font-family:system-ui;margin:24px;color:#1f2937}}.series{{fill:none;stroke-width:2}}.axis{{stroke:#d1d5db}}.title{{font-size:16px;font-weight:600}}.label{{font-size:12px;fill:#6b7280}}pre{{background:#f3f4f6;padding:12px}}</style>
<h1>独立黄金因子 Demo：历史曲线</h1><p>图表只用于研究。京东线为官方产品近月日线；其余为日度慢变量，并非分钟行情。</p>
<svg width=\"{width}\" height=\"{svg_height}\" viewBox=\"0 0 {width} {svg_height}\">{''.join(groups)}</svg>
<h2>来源与覆盖</h2><pre>{html.escape(json.dumps(metadata, ensure_ascii=False, indent=2))}</pre></html>"""


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
