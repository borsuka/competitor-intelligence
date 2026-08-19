"""URL normalisation and the SSRF guard.

The user supplies competitor URLs, so this module is the application's main
server-side request forgery boundary.  Every outbound fetch — including every redirect
hop — passes through :func:`assert_safe_url` before a socket is opened.

The guard resolves DNS itself rather than trusting the hostname, because
``evil.example.com`` is free to resolve to ``169.254.169.254``.  It also refuses to
resolve at all if *any* returned address is private: a DNS rebinding attack that returns
one public and one private record must not be a coin flip.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, urlunparse

from app.core.config import get_settings
from app.core.errors import UnsafeURLError

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})

# Hostname suffixes that only ever exist inside a private network.
BLOCKED_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".intranet",
    ".corp",
    ".home",
    ".lan",
    ".test",
    ".example",
    ".invalid",
)

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

# Query parameters that are tracking noise: stripping them keeps the URL hash stable so
# the same page is not stored twice.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "ref",
        "referrer",
        "mc_cid",
        "mc_eid",
        "_ga",
        "igshid",
    }
)


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A URL that has passed the guard, with the addresses it resolved to."""

    url: str
    hostname: str
    port: int
    ip_addresses: tuple[str, ...]


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that is not a routable public address.

    ``is_global`` alone is not enough: it is False for some ranges we want blocked and
    the explicit checks document *why* each range is refused.
    """
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # covers 169.254.0.0/16, i.e. cloud metadata
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    if isinstance(ip, ipaddress.IPv4Address):
        # Carrier-grade NAT: routable on some networks, never a legitimate target here.
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
        # Benchmarking and documentation ranges.
        if ip in ipaddress.ip_network("198.18.0.0/15"):
            return True
    else:
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) would otherwise sneak past the v6 checks.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return _is_disallowed_ip(mapped)
        if ip.is_site_local:  # fec0::/10
            return True
        if ip in ipaddress.ip_network("fc00::/7"):  # unique local addresses
            return True
    return False


def _resolve_all(hostname: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(
            "That hostname could not be resolved.", code="dns_resolution_failed"
        ) from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeURLError("That hostname could not be resolved.", code="dns_resolution_failed")
    return tuple(sorted(addresses))


def assert_safe_url(url: str) -> ResolvedTarget:
    """Validate a URL for outbound fetching, or raise :class:`UnsafeURLError`.

    Deliberately does not tell the caller *which* private address was found — that turns
    the crawler into an internal network scanner with a helpful error channel.
    """
    settings = get_settings()

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http and https URLs can be fetched.", code="scheme_not_allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError(
            "URLs containing credentials are not accepted.", code="credentials_in_url"
        )

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise UnsafeURLError("The URL has no hostname.", code="missing_hostname")

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError(
            "Only the standard http and https ports can be fetched.", code="port_not_allowed"
        )

    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeURLError("That host cannot be fetched.", code="host_not_allowed")

    # A bare IP literal is checked directly; there is nothing to resolve.
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal = None

    if settings.scraper_allow_private_networks:
        # Test-only. Refused in production by Settings.validate_production.
        addresses = (hostname,) if literal else _resolve_all(hostname, port)
        return ResolvedTarget(url=url, hostname=hostname, port=port, ip_addresses=addresses)

    if literal is not None:
        if _is_disallowed_ip(literal):
            raise UnsafeURLError("That host cannot be fetched.", code="private_address")
        return ResolvedTarget(url=url, hostname=hostname, port=port, ip_addresses=(str(literal),))

    if "." not in hostname:
        # Single-label hostnames only resolve through internal search domains.
        raise UnsafeURLError("That host cannot be fetched.", code="host_not_allowed")

    addresses = _resolve_all(hostname, port)
    for address in addresses:
        if _is_disallowed_ip(ipaddress.ip_address(address)):
            raise UnsafeURLError("That host cannot be fetched.", code="private_address")

    return ResolvedTarget(url=url, hostname=hostname, port=port, ip_addresses=addresses)


def normalize_url(url: str, *, keep_query: bool = True) -> str:
    """Canonical form used for deduplication.

    Lower-cases scheme and host, drops the fragment and tracking parameters, sorts the
    remaining query, and removes a trailing slash on non-root paths.
    """
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return url.strip()

    netloc = hostname
    if parsed.port and parsed.port not in ALLOWED_PORTS:
        netloc = f"{hostname}:{parsed.port}"

    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query = ""
    if keep_query and parsed.query:
        pairs = [
            (key, value)
            for key, value in sorted(_parse_qsl(parsed.query))
            if key.lower() not in TRACKING_PARAMS
        ]
        query = urlencode(pairs)

    return urlunparse((scheme, netloc, path, "", query, ""))


def _parse_qsl(query: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl

    return parse_qsl(query, keep_blank_values=True)


def url_hash(url: str) -> str:
    """Stable identifier for a normalised URL, used as a unique key."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def extract_domain(url: str) -> str:
    """Registrable-ish host: lower-cased, ``www.`` removed.

    Not a public-suffix lookup — that needs a maintained PSL and the extra precision buys
    nothing here, where the value is only a per-organization dedupe key.
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return hostname.removeprefix("www.")


def same_site(url: str, base_domain: str) -> bool:
    """True when ``url`` belongs to ``base_domain`` or one of its subdomains."""
    host = extract_domain(url)
    return host == base_domain or host.endswith(f".{base_domain}")


def is_probably_binary(url: str) -> bool:
    """Cheap pre-filter so the crawler does not spend its budget on PDFs and images."""
    lowered = urlparse(url).path.lower()
    return lowered.endswith(
        (
            ".pdf",
            ".zip",
            ".rar",
            ".7z",
            ".gz",
            ".tar",
            ".dmg",
            ".exe",
            ".msi",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".svg",
            ".ico",
            ".avif",
            ".mp4",
            ".mp3",
            ".wav",
            ".mov",
            ".avi",
            ".webm",
            ".css",
            ".js",
            ".json",
            ".xml",
            ".rss",
            ".atom",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
        )
    )


__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "ResolvedTarget",
    "assert_safe_url",
    "extract_domain",
    "is_probably_binary",
    "normalize_url",
    "same_site",
    "url_hash",
]
