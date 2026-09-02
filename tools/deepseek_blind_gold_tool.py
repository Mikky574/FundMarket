"""Sandbox-callable bridge to the local, date-free DeepSeek gold capability.

The tool reads one JSON request from stdin and writes one JSON response to
stdout. It has no credential handling: authentication to DeepSeek stays inside
the already configured localhost research service.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx


DEFAULT_URL = "http://127.0.0.1:8000/api/v1/internal/research/gold-blind-decision"
ALLOWED_TOP_LEVEL = {"position", "rule_candidate", "observations"}


def invoke(payload: dict, *, url: str = DEFAULT_URL) -> dict:
    """Validate the narrow contract, then invoke the local capability."""
    if not isinstance(payload, dict) or set(payload) != ALLOWED_TOP_LEVEL:
        raise ValueError("payload must contain only position, rule_candidate, observations")
    response = httpx.post(url, json=payload, timeout=75)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("local DeepSeek capability returned a non-object response")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Call the local DeepSeek blind-gold capability from stdin JSON.")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    try:
        payload = json.loads(sys.stdin.read())
        print(json.dumps(invoke(payload, url=args.url), ensure_ascii=False))
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
    except httpx.HTTPError as exc:
        print(json.dumps({"ok": False, "error": f"local capability request failed: {exc}"}, ensure_ascii=False))
        raise SystemExit(3)


if __name__ == "__main__":
    main()
