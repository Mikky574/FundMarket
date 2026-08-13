"""Compare the two latest disclosed fund holding reports without inferring undisclosed trades."""
import argparse
import json
from datetime import datetime
from pathlib import Path

import akshare as ak


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frame = ak.fund_portfolio_hold_em(args.code, args.year)
    quarters = sorted(frame["季度"].unique())
    if len(quarters) < 2:
        raise ValueError("至少需要两个季度的披露数据")
    previous_name, latest_name = quarters[-2], quarters[-1]

    def rows(quarter):
        data = frame[frame["季度"] == quarter]
        return {str(r["股票代码"]).zfill(6): {"name": str(r["股票名称"]), "weight": float(r["占净值比例"]),
                                                  "shares_10k": float(r["持股数"]), "value_10k": float(r["持仓市值"])}
                for _, r in data.iterrows()}

    previous, latest = rows(previous_name), rows(latest_name)
    changes = []
    for symbol in sorted(set(previous) | set(latest)):
        before, after = previous.get(symbol), latest.get(symbol)
        old_weight = before["weight"] if before else 0.0
        new_weight = after["weight"] if after else 0.0
        status = "NEW" if before is None else "EXIT" if after is None else "INCREASE" if new_weight > old_weight else "DECREASE" if new_weight < old_weight else "UNCHANGED"
        changes.append({"symbol": symbol, "name": (after or before)["name"], "status": status,
                        "previous_weight": old_weight, "latest_weight": new_weight,
                        "weight_change_pp": round(new_weight - old_weight, 2),
                        "previous_shares_10k": before["shares_10k"] if before else 0,
                        "latest_shares_10k": after["shares_10k"] if after else 0})
    changes.sort(key=lambda x: abs(x["weight_change_pp"]), reverse=True)
    payload = {
        "snapshot_at": datetime.now().astimezone().isoformat(timespec="seconds"), "fund_code": args.code,
        "previous_report": previous_name, "latest_report": latest_name,
        "previous_top10_weight": round(sum(x["weight"] for x in previous.values()), 2),
        "latest_top10_weight": round(sum(x["weight"] for x in latest.values()), 2),
        "changes": changes,
        "disclosure_warning": "季报持仓有披露延迟；本文件仅比较已披露报告，不代表当前实时持仓。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"reports": [previous_name, latest_name], "top10_weights": [payload["previous_top10_weight"], payload["latest_top10_weight"]],
                      "largest_changes": [(x["name"], x["weight_change_pp"]) for x in changes[:10]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
