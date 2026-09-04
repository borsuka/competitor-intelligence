"""The one place an outbound request to a user-supplied URL is actually sent.

:func:`app.scraping.urls.assert_safe_url` resolves DNS itself and refuses every private
destination, but validating a *name* and then handing that same name to the HTTP client
leaves a window open: the second lookup is free to answer differently from the first.
That is DNS rebinding, and it defeats any guard that checks names rather than
destinations.

:func:`send` closes the window by connecting to the address the guard checked. The
hostname is still sent in the ``Host`` header and as the TLS server name, so virtual
hosting and certificate verification behave exactly as they would without pinning — the
only thing that changes is which address the socket goes to.

Every request the application makes on a URL a user chose goes through here: page
fetches, ``robots.txt``, sitemaps and alert webhooks.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.scraping.urls import ResolvedTarget, assert_safe_url, is_ip_literal, pinned_url


def _host_header(target: ResolvedTarget, url: str) -> str:
    """The authority the server should see, which is the name rather than the address."""
    default_port = 443 if urlparse(url).scheme.lower() == "https" else 80
    if target.port == default_port:
        return target.hostname
    return f"{target.hostname}:{target.port}"


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    target: ResolvedTarget | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Validate ``url``, then send the request to the address that was validated.

    ``target`` lets a caller that has already validated the URL — the fetcher does, to
    pick a throttle key — pass the result in rather than paying for a second lookup.
    """
    resolved = target if target is not None else assert_safe_url(url)

    if is_ip_literal(resolved.hostname):
        # Nothing was resolved, so there is nothing to rebind.
        return await client.request(method, url, **kwargs)

    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("Host", _host_header(resolved, url))
    extensions = dict(kwargs.pop("extensions", None) or {})
    extensions.setdefault("sni_hostname", resolved.hostname)

    last_error: Exception | None = None
    for address in resolved.ip_addresses:
        request = client.build_request(
            method,
            pinned_url(url, address),
            headers=headers,
            extensions=extensions,
            **kwargs,
        )
        try:
            return await client.send(request)
        except httpx.ConnectError as exc:
            # A name with both an A and an AAAA record is only reachable over whichever
            # family this machine actually has, so try the rest before giving up. Only
            # connection failures fall through: retrying a timeout would multiply the
            # crawl's worst case by the number of records.
            last_error = exc

    if last_error is None:  # pragma: no cover - the guard guarantees an address
        last_error = httpx.ConnectError("That hostname resolved to no usable address.")
    raise last_error


__all__ = ["send"]
