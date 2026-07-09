from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """Return client IP from request.

    V1: uses direct connection IP only.
    Before production deploy behind a reverse proxy, upgrade this to parse
    the X-Forwarded-For header — but ONLY after validating the proxy origin
    to prevent IP spoofing.
    """
    return request.client.host if request.client else None
