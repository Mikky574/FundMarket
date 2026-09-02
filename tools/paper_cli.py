from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.paths import PUBLIC_LEDGER_REPORTS_ROOT, PUBLIC_LEDGER_STATE_PATH
from src.qq_control.paper_ledger import PaperLedger, fetch_trading_dates, order_schedule_after_cutoff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=PUBLIC_LEDGER_STATE_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--start", required=True); init.add_argument("--end", required=True)
    buy = sub.add_parser("buy"); buy.add_argument("--id", required=True); buy.add_argument("--date", required=True)
    buy.add_argument("--code", required=True); buy.add_argument("--name", required=True); buy.add_argument("--amount", required=True)
    buy.add_argument("--fee", default="0"); buy.add_argument("--thesis", required=True); buy.add_argument("--evidence", action="append", default=[])
    buy.add_argument("--decision-id", required=True)
    settle = sub.add_parser("settle"); settle.add_argument("--as-of", required=True)
    sell = sub.add_parser("sell"); sell.add_argument("--id", required=True); sell.add_argument("--date", required=True)
    sell.add_argument("--code", required=True); sell.add_argument("--shares", required=True)
    sell.add_argument("--fee-file", type=Path, required=True); sell.add_argument("--thesis", required=True)
    sell.add_argument("--evidence", action="append", default=[]); sell.add_argument("--decision-id", required=True)
    decision = sub.add_parser("record-decision")
    decision.add_argument("--id", required=True); decision.add_argument("--date", required=True)
    decision.add_argument("--data-as-of"); decision.add_argument("--action", required=True,
        choices=["WATCH", "BUY", "ADD", "REDUCE", "SELL", "REBALANCE"])
    decision.add_argument("--market-observation", required=True); decision.add_argument("--reason", required=True)
    decision.add_argument("--counter-evidence", default=""); decision.add_argument("--invalidation-conditions", default="")
    decision.add_argument("--confidence", type=int, required=True); decision.add_argument("--evidence", action="append", default=[])
    decision.add_argument("--user-confirmation", required=True)
    annotation = sub.add_parser("annotate-decision")
    annotation.add_argument("--id", required=True); annotation.add_argument("--decision-id", required=True)
    annotation.add_argument("--status", required=True,
                            choices=["ACTIVE", "VOIDED", "VOIDED_DUPLICATE", "VOIDED_SUPERSEDED", "SUPERSEDED"])
    annotation.add_argument("--reason", required=True); annotation.add_argument("--user-confirmation", required=True)
    value = sub.add_parser("value"); value.add_argument("--date", required=True)
    checkpoint = sub.add_parser("checkpoint"); checkpoint.add_argument("--reason", required=True)
    correct = sub.add_parser("correct-schedule"); correct.add_argument("--id", required=True)
    correct.add_argument("--nav-date", required=True); correct.add_argument("--confirmation-date", required=True)
    correct.add_argument("--reason", required=True); correct.add_argument("--evidence", action="append", default=[])
    sub.add_parser("verify")
    daily = sub.add_parser("daily"); daily.add_argument("--date", required=True)
    daily.add_argument("--report-dir", type=Path, default=PUBLIC_LEDGER_REPORTS_ROOT)
    sub.add_parser("status")
    args = parser.parse_args(); ledger = PaperLedger(args.state)
    if args.command == "init": ledger.initialize(args.start, args.end, Decimal("100000"))
    elif args.command == "buy":
        dates = fetch_trading_dates(args.date, "2027-12-31")
        nav_date, confirmation_date = order_schedule_after_cutoff(args.date, dates)
        ledger.register_buy(args.id, args.date, nav_date, confirmation_date, args.code, args.name,
                            Decimal(args.amount), Decimal(args.fee), args.evidence, args.thesis, args.decision_id)
    elif args.command == "sell":
        dates = fetch_trading_dates(args.date, "2027-12-31")
        nav_date, confirmation_date = order_schedule_after_cutoff(args.date, dates)
        fee_data = json.loads(args.fee_file.read_text(encoding="utf-8"))
        ledger.register_sell(args.id, args.date, nav_date, confirmation_date, args.code,
                             Decimal(args.shares), fee_data["redemption"], args.evidence, args.thesis, args.decision_id)
    elif args.command == "record-decision":
        print(json.dumps(ledger.record_decision(
            args.id, args.date, args.action, args.market_observation, args.reason, args.confidence,
            args.evidence, args.counter_evidence, args.invalidation_conditions, args.data_as_of,
            args.user_confirmation
        ), ensure_ascii=False))
    elif args.command == "annotate-decision":
        print(json.dumps(ledger.annotate_decision(
            args.id, args.decision_id, args.status, args.reason, args.user_confirmation
        ), ensure_ascii=False))
    elif args.command == "settle":
        result = ledger.settle_due_buys(args.as_of) + ledger.settle_due_sells(args.as_of)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "value": print(json.dumps(ledger.record_official_valuation(args.date), ensure_ascii=False))
    elif args.command == "checkpoint": print(json.dumps({"state_sha256": ledger.checkpoint(args.reason)}, ensure_ascii=False))
    elif args.command == "correct-schedule":
        print(json.dumps(ledger.correct_order_schedule(args.id, args.nav_date, args.confirmation_date,
                                                       args.reason, args.evidence), ensure_ascii=False))
    elif args.command == "verify": print(json.dumps(ledger.verify_audit(), ensure_ascii=False))
    elif args.command == "daily":
        result = ledger.daily_close(args.date)
        report = ledger.write_daily_report(result, args.report_dir)
        print(json.dumps({"result": result, "report": str(report)}, ensure_ascii=False))
    print(json.dumps(ledger.summary(), ensure_ascii=False))


if __name__ == "__main__": main()
