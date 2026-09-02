"""Owner-run verifier for local data-provider credentials.

This script is deliberately safe to commit: it expects a local JSON file at
runtime, never prints its contents, and does not persist tokens anywhere.
Run it locally; do not paste tokens into chat, source code, or Git.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TUSHARE_URL = "https://api.tushare.pro"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
REQUIRED_KEYS = ("Tushare Token", "Alpha Vantage Token")


def _load_tokens(path: Path) -> dict[str, str]:
    """Read a user-provided local config without echoing secret values."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON config: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("config root must be an object")
    missing = [key for key in REQUIRED_KEYS if not isinstance(value.get(key), str) or not value[key].strip()]
    if missing:
        raise RuntimeError("missing required configured provider token")
    return {key: value[key].strip() for key in REQUIRED_KEYS}


def _post_json(url: str, payload: dict) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:  # nosec B310 - owner-selected official provider endpoint
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("provider returned a non-object response")
    return value


def _get_json(url: str, parameters: dict[str, str]) -> dict:
    from urllib.parse import urlencode
    request = Request(f"{url}?{urlencode(parameters)}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=20) as response:  # nosec B310 - owner-selected official provider endpoint
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("provider returned a non-object response")
    return value


def verify_tushare(token: str) -> tuple[bool, str]:
    """Check the configured Tushare route without mistaking entitlement for a bad token."""
    try:
        response = _post_json(TUSHARE_URL, {
            "api_name": "fut_basic", "token": token, "params": {"exchange": "SHFE"},
            "fields": "ts_code,name",
        })
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        return False, f"request failed ({type(exc).__name__})"
    code = response.get("code")
    if code == 0:
        return True, "authentication and basic SHFE futures access verified"
    if code == 40203:
        return False, (
            "token reached Tushare but this account lacks fut_basic permission "
            "(the provider currently requires at least 2000 points); this does not prove the token is invalid"
        )
    return False, f"provider rejected the request (code={code!r})"


def verify_alpha_vantage(token: str) -> tuple[bool, str]:
    """Verify that the key can retrieve a gold-spot response without exposing it."""
    try:
        response = _get_json(ALPHA_VANTAGE_URL, {
            "function": "GOLD_SILVER_SPOT", "symbol": "GOLD", "apikey": token,
        })
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        return False, f"request failed ({type(exc).__name__})"
    if "Error Message" in response or "Information" in response or "Note" in response:
        return False, "provider rejected the request or current plan/quota does not permit it"
    return True, "gold-spot endpoint returned a usable response"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True, help="local JSON containing provider tokens")
    args = parser.parse_args()
    try:
        tokens = _load_tokens(args.config)
    except RuntimeError as exc:
        print(f"CONFIG: FAIL — {exc}")
        raise SystemExit(2)
    checks = {
        "Tushare": verify_tushare(tokens["Tushare Token"]),
        "Alpha Vantage": verify_alpha_vantage(tokens["Alpha Vantage Token"]),
    }
    failed = False
    for provider, (ok, message) in checks.items():
        print(f"{provider}: {'OK' if ok else 'FAIL'} — {message}")
        failed = failed or not ok
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
