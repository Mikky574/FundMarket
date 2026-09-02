"""Read-only public data adapter for JD ZheShang accumulated-gold prices.

The adapter intentionally does not persist observations.  Historical evaluation
storage is a separate, explicitly audited step because the vendor chart does
not expose the original publication time of each daily point.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx


PRODUCT_SKU = "1961543816"
SOURCE = "jd_zheshang_accumulated_gold"
TIMEZONE = ZoneInfo("Asia/Shanghai")
LATEST_URL = "https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice"
MONTH_URL = "https://ms.jr.jd.com/gw2/generic/hj/h5/m/cfGetQuotesPriceKLine"
REQUEST_HEADERS = {
    "User-Agent": "market-analysis-research/1.0",
    "Accept": "application/json",
    "Origin": "https://m.jdjygold.com",
    "Referer": "https://m.jdjygold.com/",
}


class GoldPriceDataError(RuntimeError):
    """The upstream response was unavailable or did not contain usable price data."""


def _decimal(value: object, field: str) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GoldPriceDataError(f"invalid {field}: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise GoldPriceDataError(f"invalid {field}: {value!r}")
    return format(parsed, "f")


def _response_json(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("success"):
        raise GoldPriceDataError("JD ZheShang price API returned an unsuccessful response")
    return payload


def _now() -> datetime:
    return datetime.now(TIMEZONE).replace(microsecond=0)


def fetch_latest(*, retrieved_at: datetime | None = None) -> dict:
    """Fetch the current public quote, preserving both source and receive times."""
    received = retrieved_at or _now()
    response = httpx.get(
        LATEST_URL,
        params={"productSku": PRODUCT_SKU},
        headers=REQUEST_HEADERS,
        timeout=15,
    )
    data = _response_json(response).get("resultData", {}).get("datas", {})
    if not isinstance(data, dict):
        raise GoldPriceDataError("JD ZheShang latest quote payload is missing datas")
    try:
        source_at = datetime.fromtimestamp(int(str(data["time"])) / 1000, tz=TIMEZONE).replace(microsecond=0)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise GoldPriceDataError("JD ZheShang latest quote is missing a valid timestamp") from exc
    return {
        "instrument": PRODUCT_SKU,
        "source": SOURCE,
        "price_yuan_per_gram": _decimal(data.get("price"), "price"),
        "source_at": source_at.isoformat(),
        "retrieved_at": received.isoformat(),
        "available_at": received.isoformat(),
        "availability_basis": "observed_live_quote",
        "up_and_down_rate": data.get("upAndDownRate"),
    }


def fetch_one_month_history(*, retrieved_at: datetime | None = None,
                            historical_availability: str = "strict") -> list[dict]:
    """Fetch the vendor's one-month daily chart without pretending it is tick data.

    ``strict`` makes old chart points available only when this collector retrieved
    them, preventing hindsight leakage.  ``assumed_eod`` is for exploratory
    backtests only: it assumes each daily chart point was available at 23:59 on
    the quoted date and labels that assumption in every record.
    """
    if historical_availability not in {"strict", "assumed_eod"}:
        raise ValueError("historical_availability must be 'strict' or 'assumed_eod'")
    received = retrieved_at or _now()
    response = httpx.post(
        MONTH_URL,
        json={"productSku": PRODUCT_SKU, "periodType": "m1"},
        headers=REQUEST_HEADERS,
        timeout=15,
    )
    data = _response_json(response).get("resultData", {}).get("data", {})
    rows = data.get("line") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise GoldPriceDataError("JD ZheShang one-month chart returned no daily rows")
    normalized: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            raise GoldPriceDataError("JD ZheShang one-month chart contains an invalid row")
        try:
            as_of = datetime.strptime(str(item["date"]), "%Y%m%d").date()
        except (KeyError, TypeError, ValueError) as exc:
            raise GoldPriceDataError("JD ZheShang one-month chart contains an invalid date") from exc
        available_at = received
        basis = "retrieved_after_the_fact"
        if historical_availability == "assumed_eod":
            available_at = datetime.combine(as_of, time(23, 59), tzinfo=TIMEZONE)
            basis = "assumed_vendor_daily_close_not_verified"
        normalized.append({
            "instrument": PRODUCT_SKU,
            "source": SOURCE,
            "as_of": as_of.isoformat(),
            "price_yuan_per_gram": _decimal(item.get("price"), "price"),
            "source_return": item.get("raisePercent"),
            "retrieved_at": received.isoformat(),
            "available_at": available_at.isoformat(),
            "availability_basis": basis,
        })
    return normalized
