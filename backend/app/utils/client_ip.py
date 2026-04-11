"""app.utils.client_ip

Client IP extraction helpers.

This repo supports deployments behind reverse proxies (Traefik) and optionally
Cloudflare. When ``TRUST_X_FORWARDED_FOR`` is enabled we treat specific proxy
headers as authoritative.

Important:
- Only enable ``TRUST_X_FORWARDED_FOR`` when the app is behind a trusted proxy
  that overwrites/sanitizes forwarding headers. Otherwise clients can spoof
  these headers and bypass anti-abuse controls.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request


def _coerce_ip(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def get_client_ip(
    request: Request,
    *,
    trust_x_forwarded_for: bool = False,
) -> str | None:
    """Return the best-effort client IP for this request.

    When *trust_x_forwarded_for* is True, we prefer Cloudflare's
    ``CF-Connecting-IP`` when present, otherwise we use ``X-Forwarded-For``.

    For X-Forwarded-For we select the *leftmost* entry (original client IP)
    under the assumption that a trusted proxy overwrites/sanitizes the header.
    """

    if trust_x_forwarded_for:
        # Cloudflare: set by Cloudflare -> origin.
        cf_ip = _coerce_ip(request.headers.get("cf-connecting-ip"))
        if cf_ip:
            return cf_ip

        # Common reverse-proxy header.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            # Prefer the leftmost (original client).
            for part in parts:
                ip = _coerce_ip(part)
                if ip:
                    return ip

        x_real = _coerce_ip(request.headers.get("x-real-ip"))
        if x_real:
            return x_real

    # Safe fallback: the direct TCP peer.
    return request.client.host if request.client is not None else None
