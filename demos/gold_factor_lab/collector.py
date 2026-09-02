"""Public-data collectors used only by the gold-factor demo.

This module has no dependency on the production app, QQ bridge, ledger, or
evaluation store.  It returns observations in memory; callers decide whether
and where an approved experiment should persist them.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from zoneinfo import ZoneInfo

import httpx


SHANGHAI = ZoneInfo("Asia/Shanghai")
JD_PRODUCT_SKU = "1961543816"
JD_LATEST_URL = "https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice"
JD_MONTH_URL = "https://ms.jr.jd.com/gw2/generic/hj/h5/m/cfGetQuotesPriceKLine"
JD_INTRADAY_URL = "https://ms.jr.jd.com/gw2/generic/hj/h5/m/cfGetPriceTrendChart"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
HEADERS = {
    "User-Agent": "FundMarket-gold-factor-lab/0.1",
    "Accept": "application/json,text/csv",
    "Origin": "https://m.jdjygold.com",
    "Referer": "https://m.jdjygold.com/",
}

# These series are deliberately a compact, interpretable starting set.  They
# are drivers, not price forecasts.  The value of DEXCHUS is USD per CNY, so it
# is inverted during normalisation to CNY per USD.
FRED_FACTORS = (
    ("us_10y_nominal_yield", "DGS10", "percent", False),
    ("us_10y_real_yield", "DFII10", "percent", False),
    ("broad_us_dollar", "DTWEXBGS", "index", False),
    ("usd_cny", "DEXCHUS", "cny_per_usd", True),
    ("wti_crude", "DCOILWTICO", "usd_per_barrel", False),
)


class CollectionError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(SHANGHAI).replace(microsecond=0)


def _positive_decimal(value: object, field: str) -> float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CollectionError(f"invalid {field}: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise CollectionError(f"invalid {field}: {value!r}")
    return float(number)


def collect_jd_latest(*, retrieved_at: datetime | None = None) -> dict:
    """Fetch one exact current product quote without depending on app settings."""
    received = retrieved_at or _now()
    response = httpx.get(JD_LATEST_URL, params={"productSku": JD_PRODUCT_SKU}, headers=HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()
    item = payload.get("resultData", {}).get("datas", {}) if isinstance(payload, dict) else {}
    if not payload.get("success") or not isinstance(item, dict):
        raise CollectionError("JD ZheShang latest quote was unavailable")
    try:
        source_at = datetime.fromtimestamp(int(str(item["time"])) / 1000, tz=SHANGHAI).replace(microsecond=0)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise CollectionError("JD ZheShang latest quote contains an invalid timestamp") from exc
    return {
        "source": "jd_zheshang_latest",
        "source_at": source_at.isoformat(),
        "retrieved_at": received.isoformat(),
        "value": _positive_decimal(item.get("price"), "JD latest price"),
        "unit": "cny_per_gram",
        "availability_basis": "observed_live_quote",
        "up_and_down_rate": item.get("upAndDownRate"),
    }


def collect_jd_month(*, retrieved_at: datetime | None = None) -> list[dict]:
    """Fetch the vendor's daily one-month chart for the JD ZheShang product."""
    received = retrieved_at or _now()
    response = httpx.post(
        JD_MONTH_URL,
        json={"productSku": JD_PRODUCT_SKU, "periodType": "m1"},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("resultData", {}).get("data", {}).get("line", []) if isinstance(payload, dict) else []
    if not payload.get("success") or not isinstance(rows, list) or not rows:
        raise CollectionError("JD ZheShang one-month chart was unavailable")
    output = []
    for item in rows:
        try:
            observed_on = datetime.strptime(str(item["date"]), "%Y%m%d").date()
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectionError("JD ZheShang chart contains an invalid date") from exc
        output.append({
            "series": "jd_zheshang_gold",
            "observed_on": observed_on.isoformat(),
            "value": _positive_decimal(item.get("price"), "JD price"),
            "unit": "cny_per_gram",
            "source": "jd_zheshang_public_chart",
            "retrieved_at": received.isoformat(),
            # The chart has no original publication timestamp.  In strict mode
            # a historical point can only be used from this collection moment.
            "available_at": received.isoformat(),
            "availability_basis": "retrieved_after_the_fact",
        })
    return output


def collect_jd_intraday(*, retrieved_at: datetime | None = None) -> list[dict]:
    """Fetch the current trading day's public minute/tick trend.

    The endpoint currently returns intraday points only.  It must therefore be
    polled and archived by a future approved data store if we want a multi-day
    minute-level research set; the demo deliberately keeps no local data.
    """
    received = retrieved_at or _now()
    response = httpx.post(
        JD_INTRADAY_URL,
        json={"appChannel": "11", "beginTime": "", "priceType": "buy", "productSku": JD_PRODUCT_SKU},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("resultData", {}).get("data", {}).get("dataList", []) if isinstance(payload, dict) else []
    if not payload.get("success") or not isinstance(rows, list) or not rows:
        raise CollectionError("JD ZheShang intraday trend was unavailable")
    output = []
    for item in rows:
        try:
            source_at = datetime.strptime(str(item["goldPriceTime"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectionError("JD ZheShang intraday trend contains an invalid timestamp") from exc
        output.append({
            "series": "jd_zheshang_gold",
            "source_at": source_at.isoformat(),
            "value": _positive_decimal(item.get("goldPrice"), "JD intraday price"),
            "unit": "cny_per_gram",
            "source": "jd_zheshang_public_intraday_chart",
            "retrieved_at": received.isoformat(),
            "available_at": received.isoformat(),
            "availability_basis": "observed_live_quote",
            "is_high": bool(item.get("highKey")),
            "is_low": bool(item.get("lowKey")),
        })
    return output


def collect_fred_factor(name: str, fred_id: str, unit: str, invert: bool, *, start: date,
                        end: date, retrieved_at: datetime | None = None) -> list[dict]:
    """Fetch a daily FRED series without assuming its original release timestamp."""
    received = retrieved_at or _now()
    response = httpx.get(
        FRED_CSV_URL,
        params={"id": fred_id, "cos": start.isoformat(), "coe": end.isoformat()},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    reader = csv.DictReader(StringIO(response.text))
    output = []
    for row in reader:
        value = row.get(fred_id)
        if not value or value == ".":
            continue
        try:
            observed_on = date.fromisoformat(str(row["observation_date"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectionError(f"FRED {fred_id} contains an invalid date") from exc
        if not start <= observed_on <= end:
            continue
        number = _positive_decimal(value, fred_id)
        if invert:
            number = 1 / number
        output.append({
            "series": name,
            "observed_on": observed_on.isoformat(),
            "value": number,
            "unit": unit,
            "source": f"fred:{fred_id}",
            "retrieved_at": received.isoformat(),
            "available_at": received.isoformat(),
            "availability_basis": "retrieved_after_the_fact",
        })
    if not output:
        raise CollectionError(f"FRED {fred_id} returned no observations in the requested date range")
    return output


def collect_factor_panel(*, start: date, end: date, retrieved_at: datetime | None = None) -> dict[str, list[dict]]:
    """Collect the JD execution-price series and five macro driver series."""
    if start > end:
        raise ValueError("start must not be after end")
    received = retrieved_at or _now()
    panel = {"jd_zheshang_gold": collect_jd_month(retrieved_at=received)}
    for name, fred_id, unit, invert in FRED_FACTORS:
        panel[name] = collect_fred_factor(
            name, fred_id, unit, invert, start=start, end=end, retrieved_at=received,
        )
    return panel


def assumed_next_session_availability(observed_on: date) -> str:
    """Exploratory-only availability contract for later point-in-time tests.

    It deliberately exposes a foreign-market close no earlier than 09:00 China
    time on the next calendar day.  It is never used in the strict raw output.
    """
    return datetime.combine(observed_on + timedelta(days=1), time(9), tzinfo=SHANGHAI).isoformat()
