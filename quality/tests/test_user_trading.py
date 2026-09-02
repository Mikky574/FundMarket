from fastapi.testclient import TestClient
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import src.web_user.trading as trading
from src.main import app


def test_schedule_uses_real_open_days_and_strict_15_cutoff():
    tz = ZoneInfo("Asia/Shanghai")
    dates = [date(2026, 7, 24), date(2026, 7, 27), date(2026, 7, 28)]
    before = trading._schedule(datetime(2026, 7, 24, 14, 59, tzinfo=tz), dates)
    at_cutoff = trading._schedule(datetime(2026, 7, 24, 15, 0, tzinfo=tz), dates)
    weekend = trading._schedule(datetime(2026, 7, 25, 10, 0, tzinfo=tz), dates)
    assert before[:2] == ("2026-07-24", "2026-07-27")
    assert at_cutoff[:2] == ("2026-07-27", "2026-07-28")
    assert weekend[:2] == ("2026-07-27", "2026-07-28")


def test_register_buy_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "DB_PATH", tmp_path / "users.sqlite3")
    monkeypatch.setattr(trading, "get_fund_overview", lambda code: {
        "code": code, "name": "测试基金", "latest": {"date": "2026-07-24", "nav": 1.0},
        "history": [{"date": "2026-07-24", "nav": 1.0}],
    })
    client = TestClient(app)
    registered = client.post("/api/v1/auth/register", json={"username": "测试用户", "password": "password123"})
    assert registered.status_code == 200
    assert client.get("/api/v1/auth/me").json()["username"] == "测试用户"

    order = client.post("/api/v1/user/orders/buy", json={"fund_code": "018099", "amount": "20000"})
    assert order.status_code == 200
    portfolio = client.get("/api/v1/user/portfolio").json()
    assert portfolio["account"]["cash_available"] == "80000.00"
    assert portfolio["account"]["cash_frozen"] == "20000.00"

    cancelled = client.post(f"/api/v1/user/orders/{order.json()['order_id']}/cancel")
    assert cancelled.json()["status"] == "CANCELLED"
    portfolio = client.get("/api/v1/user/portfolio").json()
    assert portfolio["account"]["cash_available"] == "100000.00"
    assert portfolio["account"]["cash_frozen"] == "0.00"


def test_order_locks_at_cutoff_and_waits_for_exact_nav_and_confirmation(tmp_path, monkeypatch):
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(trading, "DB_PATH", tmp_path / "users.sqlite3")
    monkeypatch.setattr(trading, "_schedule", lambda now: (
        "2026-07-27", "2026-07-28", "2026-07-27T15:00:00+08:00"))
    clock = {"now": datetime(2026, 7, 27, 14, 0, tzinfo=tz)}
    monkeypatch.setattr(trading, "_now", lambda: clock["now"])
    fund = {"code": "018099", "name": "测试基金", "latest": {"date": "2026-07-28", "nav": 1.2},
            "history": [{"date": "2026-07-28", "nav": 1.2}]}
    monkeypatch.setattr(trading, "get_fund_overview", lambda code: fund)
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"username": "规则测试用户", "password": "password123"})
    order_id = client.post("/api/v1/user/orders/buy", json={"fund_code": "018099", "amount": "20000"}).json()["order_id"]

    clock["now"] = datetime(2026, 7, 27, 15, 0, tzinfo=tz)
    assert client.post(f"/api/v1/user/orders/{order_id}/cancel").status_code == 422
    waiting_nav = client.get("/api/v1/user/portfolio").json()
    assert waiting_nav["orders"][0]["status"] == "WAITING_NAV"
    assert waiting_nav["orders"][0]["cancelable"] is False
    assert waiting_nav["positions"] == []  # 不能用 7 月 28 日净值替代缺失的 7 月 27 日净值

    fund["history"].insert(0, {"date": "2026-07-27", "nav": 1.0})
    assert client.get("/api/v1/user/portfolio").json()["orders"][0]["status"] == "WAITING_CONFIRMATION"
    clock["now"] = datetime(2026, 7, 28, 10, 0, tzinfo=tz)
    confirmed = client.get("/api/v1/user/portfolio").json()
    assert confirmed["orders"][0]["status"] == "FILLED"
    assert confirmed["orders"][0]["nav"] == "1.0"
    assert confirmed["positions"][0]["first_confirmed_date"] == "2026-07-28"


def test_wrong_password_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "DB_PATH", tmp_path / "users.sqlite3")
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"username": "another_user", "password": "password123"})
    client.post("/api/v1/auth/logout")
    response = client.post("/api/v1/auth/login", json={"username": "another_user", "password": "wrong-pass"})
    assert response.status_code == 401


def test_passwords_are_salted_and_never_stored_as_plaintext(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite3"
    monkeypatch.setattr(trading, "DB_PATH", path)
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"username": "secure_user_1", "password": "same-password"})
    client.post("/api/v1/auth/register", json={"username": "secure_user_2", "password": "same-password"})
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT password_hash,salt FROM users ORDER BY id").fetchall()
    assert all(password_hash != "same-password" for password_hash, _ in rows)
    assert rows[0][0] != rows[1][0]
    assert rows[0][1] != rows[1][1]


def test_repeated_login_failures_are_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "DB_PATH", tmp_path / "users.sqlite3")
    monkeypatch.setattr(trading.settings, "auth_max_attempts", 2)
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"username": "locked_user", "password": "correct-password"})
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"username": "locked_user", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "locked_user", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "locked_user", "password": "correct-password"}).status_code == 429


def test_preset_holding_uses_entered_pnl_and_optional_zero_sell_fee(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "DB_PATH", tmp_path / "users.sqlite3")
    monkeypatch.setattr(trading, "get_fund_overview", lambda code: {
        "code": code, "name": "预设测试基金", "latest": {"date": "2026-07-31", "nav": 2},
        "history": [{"date": "2026-07-31", "nav": 2}],
    })
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"username": "preset_user", "password": "password123"})
    response = client.post("/api/v1/user/positions/preset", json={
        "fund_code": "018099", "holding_amount": "2000", "pnl_direction": "profit", "pnl_percent": "25",
        "sell_fee_percent": None,
    })
    assert response.status_code == 200
    assert response.json()["cost_basis"] == "1600.00"
    portfolio = client.get("/api/v1/user/portfolio").json()
    position = portfolio["positions"][0]
    assert position["market_value"] == "2000.00"
    assert position["pnl"] == "400.00"
    assert position["return_percent"] == "25.00"
    assert position["sell_fee_rate"] == "0"
    assert portfolio["account"]["cash_available"] == "98400.00"
