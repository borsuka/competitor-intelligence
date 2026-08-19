"""SSRF guard and URL normalisation.

The most security-critical unit tests in the codebase: a regression here turns the
crawler into an internal network scanner.
"""

from __future__ import annotations

import pytest

from app.core.errors import UnsafeURLError
from app.scraping.urls import (
    assert_safe_url,
    extract_domain,
    is_probably_binary,
    normalize_url,
    same_site,
    url_hash,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://LOCALHOST/admin",
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0.0.0.0/",
        "http://10.0.0.1/",
        "http://172.16.5.4/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP/Azure metadata
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://something.internal/",
        "http://printer.local/",
        "http://intranet/",  # single label
    ],
)
def test_private_and_internal_destinations_are_refused(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
    ],
)
def test_non_http_schemes_are_refused(url: str) -> None:
    with pytest.raises(UnsafeURLError) as exc:
        assert_safe_url(url)
    assert exc.value.code in {"scheme_not_allowed", "missing_hostname"}


def test_credentials_in_url_are_refused() -> None:
    with pytest.raises(UnsafeURLError) as exc:
        assert_safe_url("http://admin:hunter2@example.com/")
    assert exc.value.code == "credentials_in_url"


@pytest.mark.parametrize("url", ["http://example.com:8080/", "https://example.com:22/"])
def test_non_standard_ports_are_refused(url: str) -> None:
    with pytest.raises(UnsafeURLError) as exc:
        assert_safe_url(url)
    assert exc.value.code == "port_not_allowed"


def test_error_does_not_leak_the_resolved_address() -> None:
    """A helpful error message would turn the crawler into a network scanner."""
    with pytest.raises(UnsafeURLError) as exc:
        assert_safe_url("http://192.168.1.50/")
    assert "192.168" not in exc.value.message


class TestNormalization:
    def test_lowercases_scheme_and_host_and_drops_fragment(self) -> None:
        assert normalize_url("HTTPS://Example.COM/Path#section") == "https://example.com/Path"

    def test_preserves_path_case(self) -> None:
        """Paths are case-sensitive; folding them would merge distinct pages."""
        assert normalize_url("https://example.com/CaseSensitive") != normalize_url(
            "https://example.com/casesensitive"
        )

    def test_strips_tracking_parameters_and_sorts_the_rest(self) -> None:
        assert (
            normalize_url("https://example.com/p?utm_source=x&b=2&a=1&fbclid=z")
            == "https://example.com/p?a=1&b=2"
        )

    def test_strips_trailing_slash_except_on_root(self) -> None:
        assert normalize_url("https://example.com/pricing/") == "https://example.com/pricing"
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_hash_is_stable_across_equivalent_urls(self) -> None:
        assert url_hash("https://example.com/a/?utm_source=nl") == url_hash("https://example.com/a")


class TestDomains:
    def test_strips_www(self) -> None:
        assert extract_domain("https://www.example.co.uk/x") == "example.co.uk"

    def test_accepts_bare_hostname(self) -> None:
        assert extract_domain("example.com") == "example.com"

    def test_same_site_matches_subdomains(self) -> None:
        assert same_site("https://blog.example.com/post", "example.com")
        assert same_site("https://example.com/", "example.com")

    def test_same_site_rejects_lookalike_domains(self) -> None:
        assert not same_site("https://notexample.com/", "example.com")
        assert not same_site("https://example.com.evil.net/", "example.com")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/brochure.pdf", True),
        ("https://example.com/logo.svg", True),
        ("https://example.com/app.js", True),
        ("https://example.com/pricing", False),
        ("https://example.com/blog/how-we-price", False),
    ],
)
def test_binary_prefilter(url: str, expected: bool) -> None:
    assert is_probably_binary(url) is expected


class TestEscapeHatch:
    """`SCRAPER_ALLOW_PRIVATE_NETWORKS` exists so end-to-end tests can crawl a fixture
    site on localhost. It is the one switch that disables the guard, so its blast radius
    is worth pinning down."""

    def test_disabled_by_default(self, settings) -> None:
        assert settings.scraper_allow_private_networks is False

    def test_when_enabled_it_permits_localhost_and_odd_ports(self, monkeypatch) -> None:
        from app.core.config import get_settings

        monkeypatch.setenv("SCRAPER_ALLOW_PRIVATE_NETWORKS", "true")
        get_settings.cache_clear()

        target = assert_safe_url("http://127.0.0.1:4319/")
        assert target.port == 4319

    def test_production_refuses_to_start_with_it_enabled(self, monkeypatch) -> None:
        """The guarantee that makes the flag safe to have at all."""
        import pytest as _pytest

        from app.core.config import Settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "a" * 48)
        monkeypatch.setenv("COOKIE_SECURE", "true")
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db.internal:5432/x")
        monkeypatch.setenv("SCRAPER_ALLOW_PRIVATE_NETWORKS", "true")

        with _pytest.raises(ValueError, match="SCRAPER_ALLOW_PRIVATE_NETWORKS"):
            Settings()


class TestFaviconUrl:
    """The favicon URL is built from the competitor's own origin.

    Assuming https on the default port produces a URL the browser cannot reach for any
    site served over plain HTTP or on another port, and the request hangs until it times
    out rather than failing fast — which stalls the page that embeds it.
    """

    def test_keeps_the_site_scheme(self) -> None:
        from app.services.competitors import favicon_url_for

        assert favicon_url_for("http://example.org/") == "http://example.org/favicon.ico"
        assert favicon_url_for("https://example.org/") == "https://example.org/favicon.ico"

    def test_keeps_a_non_default_port(self) -> None:
        from app.services.competitors import favicon_url_for

        assert favicon_url_for("http://127.0.0.1:4319/") == "http://127.0.0.1:4319/favicon.ico"

    def test_drops_the_path(self) -> None:
        from app.services.competitors import favicon_url_for

        assert (
            favicon_url_for("https://shop.example.com/pricing")
            == "https://shop.example.com/favicon.ico"
        )
