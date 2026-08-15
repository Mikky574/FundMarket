"""Shared HTTP dependencies for API boundary modules."""

from fastapi import HTTPException, Request


def require_local_bridge(request: Request) -> None:
    """Restrict privileged bridge capabilities to loopback callers."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "local bridge only")
