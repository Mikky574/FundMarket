from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
from fastapi import Cookie, HTTPException
from pydantic import BaseModel, Field

from src.config import settings
from src.quant_research.fund_data import get_fund_overview
from src.paths import PUBLIC_LEDGER_FEE_ROOT


DB_PATH = Path(settings.database_root).resolve() / "user_trading.sqlite3"
INITIAL_CASH = Decimal("100000.00")
MONEY = Decimal("0.01")
SHARES = Decimal("0.0001")
USERNAME = re.compile(r"^[\w\u4e00-\u9fff]{3,24}$")
MARKET_TZ = ZoneInfo("Asia/Shanghai")
_calendar_cache: tuple[datetime, list[date]] | None = None


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=24)
    password: str = Field(min_length=8, max_length=128)


class BuyOrder(BaseModel):
    fund_code: str = Field(pattern=r"^\d{6}$")
    amount: Decimal = Field(ge=Decimal("10"), le=INITIAL_CASH)


class SellOrder(BaseModel):
    fund_code: str = Field(pattern=r"^\d{6}$")
    shares: Decimal = Field(gt=0)


class PresetPosition(BaseModel):
    """A user-entered existing holding for the personal paper account only."""

    fund_code: str = Field(pattern=r"^\d{6}$")
    holding_amount: Decimal = Field(gt=0, decimal_places=2)
    pnl_direction: str = Field(pattern=r"^(profit|loss)$")
    pnl_percent: Decimal = Field(ge=0, lt=100, decimal_places=4)
    sell_fee_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _shares(value) -> Decimal:
    return Decimal(str(value)).quantize(SHARES, rounding=ROUND_HALF_UP)


def _fund_overview(code: str, force_refresh: bool = False) -> dict:
    """Keep callers/test doubles compatible while supporting explicit refresh."""
    if not force_refresh:
        return get_fund_overview(code)
    try:
        return get_fund_overview(code, force_refresh=True)
    except TypeError:
        return get_fund_overview(code)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
      expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS auth_failures(username TEXT PRIMARY KEY, failed_count INTEGER NOT NULL,
      last_failed_at TEXT NOT NULL, locked_until TEXT);
    CREATE TABLE IF NOT EXISTS accounts(user_id INTEGER PRIMARY KEY, initial_cash TEXT NOT NULL,
      cash_available TEXT NOT NULL, cash_frozen TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS positions(user_id INTEGER NOT NULL, fund_code TEXT NOT NULL,
      fund_name TEXT NOT NULL, shares TEXT NOT NULL, shares_frozen TEXT NOT NULL, cost_basis TEXT NOT NULL,
      first_confirmed_date TEXT NOT NULL, PRIMARY KEY(user_id, fund_code));
    CREATE TABLE IF NOT EXISTS position_lots(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      fund_code TEXT NOT NULL, buy_order_id TEXT NOT NULL, confirmation_date TEXT NOT NULL,
      shares_remaining TEXT NOT NULL, cost_remaining TEXT NOT NULL,
      UNIQUE(user_id, fund_code, buy_order_id));
    CREATE TABLE IF NOT EXISTS position_fee_settings(user_id INTEGER NOT NULL, fund_code TEXT NOT NULL,
      sell_fee_rate TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(user_id, fund_code));
    CREATE TABLE IF NOT EXISTS user_orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      side TEXT NOT NULL, status TEXT NOT NULL, fund_code TEXT NOT NULL, fund_name TEXT NOT NULL,
      gross_amount TEXT, shares TEXT, nav TEXT, fee TEXT, fee_rate TEXT, nav_date TEXT NOT NULL,
      confirmation_date TEXT, cancel_before TEXT NOT NULL, created_at TEXT NOT NULL, filled_at TEXT, cancelled_at TEXT);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(user_orders)")}
    if "confirmation_date" not in columns:
        db.execute("ALTER TABLE user_orders ADD COLUMN confirmation_date TEXT")
    db.execute("UPDATE user_orders SET confirmation_date=nav_date WHERE confirmation_date IS NULL")
    db.execute("""
      INSERT OR IGNORE INTO position_lots(user_id,fund_code,buy_order_id,confirmation_date,shares_remaining,cost_remaining)
      SELECT p.user_id,p.fund_code,'legacy-' || p.user_id || '-' || p.fund_code,p.first_confirmed_date,p.shares,p.cost_basis
      FROM positions p WHERE CAST(p.shares AS REAL)>0
        AND NOT EXISTS (SELECT 1 FROM position_lots l WHERE l.user_id=p.user_id AND l.fund_code=p.fund_code)
    """)
    db.commit()
    return db


def _password(password: str, salt: bytes, use_pepper: bool = True) -> str:
    pepper = settings.password_pepper if use_pepper else ""
    material = password.encode() + pepper.encode()
    return hashlib.scrypt(material, salt=salt, n=2**14, r=8, p=1).hex()


def register(credentials: Credentials) -> tuple[dict, str]:
    username = credentials.username.strip()
    if not USERNAME.fullmatch(username):
        raise HTTPException(422, "用户名只能包含中文、字母、数字或下划线，长度3–24位")
    salt = secrets.token_bytes(16)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        with _connect() as db:
            cursor = db.execute("INSERT INTO users(username,password_hash,salt,created_at) VALUES(?,?,?,?)",
                                (username, _password(credentials.password, salt), salt.hex(), now))
            user_id = cursor.lastrowid
            db.execute("INSERT INTO accounts VALUES(?,?,?,?,?)",
                       (user_id, str(INITIAL_CASH), str(INITIAL_CASH), "0.00", now))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "用户名已存在") from exc
    return _new_session(user_id, username)


def login(credentials: Credentials) -> tuple[dict, str]:
    username = credentials.username.strip()
    now = datetime.now().astimezone()
    with _connect() as db:
        failure = db.execute("SELECT * FROM auth_failures WHERE username=?", (username,)).fetchone()
        if failure and failure["locked_until"] and datetime.fromisoformat(failure["locked_until"]) > now:
            raise HTTPException(429, "登录失败次数过多，请15分钟后重试")
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    valid = False
    legacy_hash = False
    if row is not None:
        salt = bytes.fromhex(row["salt"])
        valid = hmac.compare_digest(row["password_hash"], _password(credentials.password, salt))
        if not valid and settings.password_pepper:
            legacy_hash = hmac.compare_digest(row["password_hash"], _password(credentials.password, salt, use_pepper=False))
            valid = legacy_hash
    if not valid:
        with _connect() as db:
            current = db.execute("SELECT failed_count FROM auth_failures WHERE username=?", (username,)).fetchone()
            count = (current["failed_count"] if current else 0) + 1
            locked_until = (now + timedelta(seconds=settings.auth_lock_seconds)).isoformat(timespec="seconds") if count >= settings.auth_max_attempts else None
            db.execute("INSERT INTO auth_failures(username,failed_count,last_failed_at,locked_until) VALUES(?,?,?,?) "
                       "ON CONFLICT(username) DO UPDATE SET failed_count=excluded.failed_count,last_failed_at=excluded.last_failed_at,locked_until=excluded.locked_until",
                       (username, count, now.isoformat(timespec="seconds"), locked_until))
        raise HTTPException(401, "用户名或密码错误")
    with _connect() as db:
        db.execute("DELETE FROM auth_failures WHERE username=?", (username,))
        if legacy_hash:
            db.execute("UPDATE users SET password_hash=? WHERE id=?",
                       (_password(credentials.password, bytes.fromhex(row["salt"])), row["id"]))
    return _new_session(row["id"], row["username"])


def _new_session(user_id: int, username: str) -> tuple[dict, str]:
    token = secrets.token_urlsafe(32)
    now = datetime.now().astimezone()
    with _connect() as db:
        db.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user_id,
                    (now + timedelta(days=30)).isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
    return {"id": user_id, "username": username}, token


def current_user(session: str | None = Cookie(None, alias="market_session")) -> dict:
    if not session:
        raise HTTPException(401, "请先登录")
    token_hash = hashlib.sha256(session.encode()).hexdigest()
    with _connect() as db:
        row = db.execute("SELECT u.id,u.username,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                         (token_hash,)).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) <= datetime.now().astimezone():
        raise HTTPException(401, "登录已过期")
    return {"id": row["id"], "username": row["username"], "token_hash": token_hash}


def logout(user: dict) -> None:
    with _connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (user["token_hash"],))


def _now() -> datetime:
    return datetime.now(MARKET_TZ)


def _trading_dates() -> list[date]:
    global _calendar_cache
    now = _now()
    if _calendar_cache and now - _calendar_cache[0] < timedelta(hours=12):
        return _calendar_cache[1]
    try:
        frame = ak.tool_trade_date_hist_sina()
        dates = sorted({date.fromisoformat(str(value)[:10]) for value in frame["trade_date"]})
    except Exception as exc:
        if _calendar_cache:
            return _calendar_cache[1]
        raise HTTPException(503, "交易日历暂时不可用，为避免休市日错误成交，当前暂停提交订单") from exc
    _calendar_cache = (now, dates)
    return dates


def _schedule(now: datetime, trading_dates: list[date] | None = None) -> tuple[str, str, str]:
    now = now.astimezone(MARKET_TZ)
    dates = trading_dates or _trading_dates()
    candidates = [item for item in dates if item >= now.date()]
    if not candidates:
        raise HTTPException(503, "交易日历没有覆盖当前日期")
    if now.date() in dates and now.time().replace(tzinfo=None) < time(15, 0):
        nav_day = now.date()
    else:
        nav_day = next((item for item in dates if item > now.date()), None)
    if nav_day is None:
        raise HTTPException(503, "交易日历没有覆盖下一个开放日")
    confirmation_day = next((item for item in dates if item > nav_day), None)
    if confirmation_day is None:
        raise HTTPException(503, "交易日历没有覆盖确认日")
    cutoff = datetime.combine(nav_day, time(15, 0), tzinfo=MARKET_TZ)
    return nav_day.isoformat(), confirmation_day.isoformat(), cutoff.isoformat(timespec="seconds")


def _fees(code: str) -> dict:
    path = PUBLIC_LEDGER_FEE_ROOT / f"{code}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"subscription_fee_rate": 0, "redemption": [
        {"min_days": 0, "max_days_exclusive": 7, "rate": 0.015},
        {"min_days": 7, "max_days_exclusive": 30, "rate": 0.005},
        {"min_days": 30, "max_days_exclusive": None, "rate": 0},
    ], "default_simulation": True}


def create_buy(user_id: int, request: BuyOrder) -> dict:
    overview = get_fund_overview(request.fund_code)
    amount = _money(request.amount)
    nav_date, confirmation_date, cancel_before = _schedule(_now())
    fee_rate = Decimal(str(_fees(request.fund_code)["subscription_fee_rate"]))
    with _connect() as db:
        account = db.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,)).fetchone()
        if amount > Decimal(account["cash_available"]):
            raise HTTPException(422, "可用资金不足")
        db.execute("UPDATE accounts SET cash_available=?,cash_frozen=? WHERE user_id=?",
                   (str(_money(Decimal(account["cash_available"]) - amount)),
                    str(_money(Decimal(account["cash_frozen"]) + amount)), user_id))
        cursor = db.execute("INSERT INTO user_orders(user_id,side,status,fund_code,fund_name,gross_amount,fee_rate,nav_date,confirmation_date,cancel_before,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (user_id, "BUY", "PENDING", request.fund_code, overview["name"], str(amount),
                             str(fee_rate), nav_date, confirmation_date, cancel_before, _now().isoformat(timespec="seconds")))
    return {"order_id": cursor.lastrowid, "status": "PENDING", "nav_date": nav_date,
            "confirmation_date": confirmation_date, "cancel_before": cancel_before}


def create_sell(user_id: int, request: SellOrder) -> dict:
    shares = _shares(request.shares)
    nav_date, confirmation_date, cancel_before = _schedule(_now())
    with _connect() as db:
        position = db.execute("SELECT * FROM positions WHERE user_id=? AND fund_code=?", (user_id, request.fund_code)).fetchone()
        if position is None or shares > Decimal(position["shares"]) - Decimal(position["shares_frozen"]):
            raise HTTPException(422, "可用基金份额不足")
        db.execute("UPDATE positions SET shares_frozen=? WHERE user_id=? AND fund_code=?",
                   (str(_shares(Decimal(position["shares_frozen"]) + shares)), user_id, request.fund_code))
        cursor = db.execute("INSERT INTO user_orders(user_id,side,status,fund_code,fund_name,shares,nav_date,confirmation_date,cancel_before,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (user_id, "SELL", "PENDING", request.fund_code, position["fund_name"], str(shares),
                             nav_date, confirmation_date, cancel_before, _now().isoformat(timespec="seconds")))
    return {"order_id": cursor.lastrowid, "status": "PENDING", "nav_date": nav_date,
            "confirmation_date": confirmation_date, "cancel_before": cancel_before}


def cancel_order(user_id: int, order_id: int) -> dict:
    now = _now()
    with _connect() as db:
        order = db.execute("SELECT * FROM user_orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
        if order is None:
            raise HTTPException(404, "订单不存在")
        if order["status"] != "PENDING" or now >= datetime.fromisoformat(order["cancel_before"]):
            raise HTTPException(422, "订单已进入净值确认阶段，不能撤销")
        if order["side"] == "BUY":
            account = db.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,)).fetchone()
            amount = Decimal(order["gross_amount"])
            db.execute("UPDATE accounts SET cash_available=?,cash_frozen=? WHERE user_id=?",
                       (str(_money(Decimal(account["cash_available"]) + amount)),
                        str(_money(Decimal(account["cash_frozen"]) - amount)), user_id))
        else:
            position = db.execute("SELECT * FROM positions WHERE user_id=? AND fund_code=?", (user_id, order["fund_code"])).fetchone()
            db.execute("UPDATE positions SET shares_frozen=? WHERE user_id=? AND fund_code=?",
                       (str(_shares(Decimal(position["shares_frozen"]) - Decimal(order["shares"]))), user_id, order["fund_code"]))
        db.execute("UPDATE user_orders SET status='CANCELLED',cancelled_at=? WHERE id=?",
                   (now.isoformat(timespec="seconds"), order_id))
    return {"order_id": order_id, "status": "CANCELLED"}


def _redemption_rate(code: str, held_days: int) -> Decimal:
    for item in _fees(code)["redemption"]:
        upper = item["max_days_exclusive"]
        if held_days >= item["min_days"] and (upper is None or held_days < upper):
            return Decimal(str(item["rate"]))
    return Decimal("0")


def preset_position(user_id: int, request: PresetPosition) -> dict:
    """Add an already-owned fund without ever touching the AI portfolio ledger."""
    fund = get_fund_overview(request.fund_code)
    nav = Decimal(str(fund["latest"]["nav"]))
    requested_amount = _money(request.holding_amount)
    shares = _shares(requested_amount / nav)
    market_value = _money(shares * nav)
    rate = request.pnl_percent / Decimal("100")
    divisor = Decimal("1") + rate if request.pnl_direction == "profit" else Decimal("1") - rate
    cost_basis = _money(market_value / divisor)
    fee_rate = Decimal("0") if request.sell_fee_percent is None else request.sell_fee_percent / Decimal("100")
    now = _now().isoformat(timespec="seconds")
    with _connect() as db:
        account = db.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,)).fetchone()
        existing = db.execute("SELECT 1 FROM positions WHERE user_id=? AND fund_code=?", (user_id, request.fund_code)).fetchone()
        if existing:
            raise HTTPException(409, "该基金已有持仓；为避免覆盖已有模拟交易，暂不重复预设")
        if cost_basis > Decimal(account["cash_available"]):
            raise HTTPException(422, "预设持仓成本超过个人模拟账户可用资金")
        db.execute("UPDATE accounts SET cash_available=? WHERE user_id=?",
                   (str(_money(Decimal(account["cash_available"]) - cost_basis)), user_id))
        db.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?)",
                   (user_id, request.fund_code, fund["name"], str(shares), "0.0000", str(cost_basis), now[:10]))
        db.execute("INSERT INTO position_lots(user_id,fund_code,buy_order_id,confirmation_date,shares_remaining,cost_remaining) VALUES(?,?,?,?,?,?)",
                   (user_id, request.fund_code, f"preset-{secrets.token_hex(8)}", now[:10], str(shares), str(cost_basis)))
        db.execute("INSERT INTO position_fee_settings(user_id,fund_code,sell_fee_rate,source) VALUES(?,?,?,?)",
                   (user_id, request.fund_code, str(fee_rate), "PRESET"))
    return {"fund_code": request.fund_code, "fund_name": fund["name"], "shares": str(shares),
            "market_value": str(market_value), "cost_basis": str(cost_basis),
            "pnl_percent": str(request.pnl_percent if request.pnl_direction == "profit" else -request.pnl_percent),
            "sell_fee_rate": str(fee_rate)}


def settle_pending(user_id: int, force_refresh: bool = False) -> None:
    now = _now()
    with _connect() as db:
        db.execute("UPDATE user_orders SET status='WAITING_NAV' WHERE user_id=? AND status='PENDING' AND cancel_before<=?",
                   (user_id, now.isoformat(timespec="seconds")))
        orders = db.execute("SELECT * FROM user_orders WHERE user_id=? AND status IN ('WAITING_NAV','WAITING_CONFIRMATION') ORDER BY id",
                            (user_id,)).fetchall()
    for order in orders:
        try:
            fund = _fund_overview(order["fund_code"], force_refresh=force_refresh)
            if fund["latest"]["date"] < order["nav_date"]:
                continue
            nav_row = next((x for x in reversed(fund["history"]) if x["date"] == order["nav_date"]), None)
            if nav_row is None:
                continue
            nav = Decimal(str(nav_row["nav"]))
            if now.date() < date.fromisoformat(order["confirmation_date"]):
                with _connect() as db:
                    db.execute("UPDATE user_orders SET status='WAITING_CONFIRMATION',nav=? WHERE id=?",
                               (str(nav), order["id"]))
                continue
            with _connect() as db:
                if order["side"] == "BUY":
                    gross, rate = Decimal(order["gross_amount"]), Decimal(order["fee_rate"] or "0")
                    fee, shares = _money(gross * rate), _shares((gross - _money(gross * rate)) / nav)
                    account = db.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,)).fetchone()
                    position = db.execute("SELECT * FROM positions WHERE user_id=? AND fund_code=?", (user_id, order["fund_code"])).fetchone()
                    if position:
                        db.execute("UPDATE positions SET shares=?,cost_basis=? WHERE user_id=? AND fund_code=?",
                                   (str(_shares(Decimal(position["shares"]) + shares)), str(_money(Decimal(position["cost_basis"]) + gross)), user_id, order["fund_code"]))
                    else:
                        db.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?)",
                                   (user_id, order["fund_code"], order["fund_name"], str(shares), "0.0000", str(gross), order["confirmation_date"]))
                    db.execute("INSERT INTO position_lots(user_id,fund_code,buy_order_id,confirmation_date,shares_remaining,cost_remaining) VALUES(?,?,?,?,?,?)",
                               (user_id, order["fund_code"], str(order["id"]), order["confirmation_date"],
                                str(shares), str(gross)))
                    db.execute("UPDATE accounts SET cash_frozen=? WHERE user_id=?",
                               (str(_money(Decimal(account["cash_frozen"]) - gross)), user_id))
                    db.execute("UPDATE user_orders SET status='FILLED',shares=?,nav=?,fee=?,filled_at=? WHERE id=?",
                               (str(shares), str(nav), str(fee), now.isoformat(timespec="seconds"), order["id"]))
                else:
                    position = db.execute("SELECT * FROM positions WHERE user_id=? AND fund_code=?", (user_id, order["fund_code"])).fetchone()
                    shares = Decimal(order["shares"]); remaining = shares
                    gross = _money(shares * nav); fee = Decimal("0"); sold_cost = Decimal("0")
                    lots = db.execute("SELECT * FROM position_lots WHERE user_id=? AND fund_code=? AND CAST(shares_remaining AS REAL)>0 ORDER BY confirmation_date,id",
                                      (user_id, order["fund_code"])).fetchall()
                    fee_setting = db.execute("SELECT sell_fee_rate FROM position_fee_settings WHERE user_id=? AND fund_code=?",
                                             (user_id, order["fund_code"])).fetchone()
                    for lot in lots:
                        if remaining <= 0:
                            break
                        available = Decimal(lot["shares_remaining"]); used = min(available, remaining)
                        held_days = (date.fromisoformat(order["confirmation_date"]) - date.fromisoformat(lot["confirmation_date"])).days
                        rate = Decimal(fee_setting["sell_fee_rate"]) if fee_setting else _redemption_rate(order["fund_code"], held_days)
                        lot_gross = _money(used * nav)
                        lot_fee = _money(lot_gross * rate)
                        lot_cost = _money(Decimal(lot["cost_remaining"]) * used / available)
                        fee += lot_fee; sold_cost += lot_cost; remaining -= used
                        db.execute("UPDATE position_lots SET shares_remaining=?,cost_remaining=? WHERE id=?",
                                   (str(_shares(available - used)),
                                    str(_money(Decimal(lot["cost_remaining"]) - lot_cost)), lot["id"]))
                    if remaining > 0:
                        raise ValueError("分批持仓份额不足，无法完成 FIFO 赎回")
                    fee = _money(fee); net = _money(gross - fee)
                    remaining_cost = _money(Decimal(position["cost_basis"]) - sold_cost)
                    account = db.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,)).fetchone()
                    db.execute("UPDATE accounts SET cash_available=? WHERE user_id=?", (str(_money(Decimal(account["cash_available"]) + net)), user_id))
                    db.execute("UPDATE positions SET shares=?,shares_frozen=?,cost_basis=? WHERE user_id=? AND fund_code=?",
                               (str(_shares(Decimal(position["shares"]) - shares)), str(_shares(Decimal(position["shares_frozen"]) - shares)), str(remaining_cost), user_id, order["fund_code"]))
                    db.execute("UPDATE user_orders SET status='FILLED',nav=?,fee=?,fee_rate=?,gross_amount=?,filled_at=? WHERE id=?",
                               (str(nav), str(fee), "FIFO_BY_LOT", str(gross), now.isoformat(timespec="seconds"), order["id"]))
        except Exception:
            continue


def portfolio(user_id: int, force_refresh: bool = False) -> dict:
    settle_pending(user_id, force_refresh=force_refresh)
    with _connect() as db:
        account = dict(db.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,)).fetchone())
        positions = [dict(x) for x in db.execute("SELECT * FROM positions WHERE user_id=? AND CAST(shares AS REAL)>0", (user_id,)).fetchall()]
        orders = [dict(x) for x in db.execute("SELECT * FROM user_orders WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)).fetchall()]
        fee_settings = {row["fund_code"]: row["sell_fee_rate"] for row in db.execute(
            "SELECT fund_code,sell_fee_rate FROM position_fee_settings WHERE user_id=?", (user_id,)
        ).fetchall()}
    now = _now()
    for order in orders:
        order["cancelable"] = order["status"] == "PENDING" and now < datetime.fromisoformat(order["cancel_before"])
    market_value = Decimal("0")
    for position in positions:
        try:
            fund = _fund_overview(position["fund_code"], force_refresh=force_refresh); nav = Decimal(str(fund["latest"]["nav"])); position["nav"] = str(nav); position["as_of"] = fund["latest"]["date"]
            position["market_value"] = str(_money(Decimal(position["shares"]) * nav)); position["pnl"] = str(_money(Decimal(position["market_value"]) - Decimal(position["cost_basis"])))
            position["return_percent"] = str((Decimal(position["pnl"]) / Decimal(position["cost_basis"]) * 100) if Decimal(position["cost_basis"]) else Decimal("0"))
            position["sell_fee_rate"] = fee_settings.get(position["fund_code"])
            market_value += Decimal(position["market_value"])
        except Exception:
            position.update({"nav": None, "as_of": None, "market_value": "0.00", "pnl": "0.00", "return_percent": "0", "sell_fee_rate": fee_settings.get(position["fund_code"])})
    total = _money(Decimal(account["cash_available"]) + Decimal(account["cash_frozen"]) + market_value)
    return {"refreshed_at": now.isoformat(timespec="seconds"), "forced_refresh": force_refresh,
            "account": account, "summary": {"total_assets": str(total), "market_value": str(_money(market_value)),
            "pnl": str(_money(total - INITIAL_CASH)), "return_percent": str((total / INITIAL_CASH - 1) * 100)},
            "positions": positions, "orders": orders,
            "rules": {"initial_cash": str(INITIAL_CASH), "cutoff": "基金开放日15:00",
                      "confirmation": "15:00前按当日净值，15:00后或休市日按下一开放日净值；普通基金通常下一交易日确认",
                      "income": "份额确认后展示持仓；经济收益从成交净值日后的净值变化开始，通常确认日当晚更新后首次可见",
                      "special_funds": "QDII、FOF、港股及暂停申购基金以各基金合同和公告为准",
                      "default_fee_notice": "未配置费率的基金使用模拟费率，不代表支付宝或基金公司的实际费率"}}
