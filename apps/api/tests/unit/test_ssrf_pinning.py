"""The connection goes to the address the guard checked, not to a fresh lookup.

Validating a hostname and then handing that hostname to the HTTP client leaves a window
in which DNS can answer differently the second time. These tests pin the behaviour that
closes it, because the window is invisible in normal use: everything works either way
until someone attacks it.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import UnsafeURLError
from app.scraping.safe_http import send
from app.scraping.urls import is_ip_literal, pinned_url


class TestPinnedUrl:
    def test_replaces_the_host_and_keeps_everything_else(self) -> None:
        assert (
            pinned_url("https://example.com/pricing?plan=pro", "93.184.216.34")
            == "https://93.184.216.34/pricing?plan=pro"
        )

    def test_keeps_an_explicit_port(self) -> None:
        assert pinned_url("http://example.com:8080/a", "93.184.216.34") == (
            "http://93.184.216.34:8080/a"
        )

    def test_brackets_an_ipv6_address(self) -> None:
        assert pinned_url("https://example.com/", "2606:2800:220:1:248:1893:25c8:1946") == (
            "https://[2606:2800:220:1:248:1893:25c8:1946]/"
        )

    def test_recognises_literals(self) -> None:
        assert is_ip_literal("93.184.216.34")
        assert is_ip_literal("[::1]")
        assert not is_ip_literal("example.com")


@pytest.fixture
def resolves_to(monkeypatch):
    """Make DNS answer with the addresses a test names, once."""

    def _install(*addresses: str):
        monkeypatch.setattr(
            "app.scraping.urls._resolve_all", lambda hostname, port: tuple(addresses)
        )

    return _install


def _recorder(status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text="ok")

    return seen, handler


class TestSend:
    @pytest.mark.asyncio
    async def test_connects_to_the_validated_address_and_keeps_the_name(self, resolves_to) -> None:
        resolves_to("93.184.216.34")
        seen, handler = _recorder()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await send(client, "GET", "https://example.com/pricing")

        assert response.status_code == 200
        request = seen[0]
        # The socket goes to the checked address...
        assert str(request.url) == "https://93.184.216.34/pricing"
        # ...while the server and the certificate still see the name they expect.
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"

    @pytest.mark.asyncio
    async def test_the_name_is_resolved_once_and_not_looked_up_again(self, monkeypatch) -> None:
        """The rebinding attack itself: DNS answers differently the second time."""
        answers = iter([("93.184.216.34",), ("169.254.169.254",)])
        monkeypatch.setattr("app.scraping.urls._resolve_all", lambda hostname, port: next(answers))
        seen, handler = _recorder()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await send(client, "GET", "https://example.com/")

        # Handing the name to the client instead would have spent the second answer, and
        # the socket would have gone to the metadata endpoint.
        assert str(seen[0].url) == "https://93.184.216.34/"
        assert next(answers, None) == ("169.254.169.254",)

    @pytest.mark.asyncio
    async def test_a_private_answer_is_still_refused(self, resolves_to) -> None:
        resolves_to("169.254.169.254")
        seen, handler = _recorder()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(UnsafeURLError):
                await send(client, "GET", "https://example.com/")

        assert seen == []

    @pytest.mark.asyncio
    async def test_a_literal_address_is_sent_unchanged(self, resolves_to) -> None:
        seen, handler = _recorder()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await send(client, "GET", "http://93.184.216.34/robots.txt")

        assert str(seen[0].url) == "http://93.184.216.34/robots.txt"

    @pytest.mark.asyncio
    async def test_falls_through_to_the_next_address_when_one_is_unreachable(
        self, resolves_to
    ) -> None:
        """A name with an A and an AAAA record must not fail on a machine with one stack."""
        resolves_to("2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34")
        attempted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempted.append(request.url.host)
            if ":" in request.url.host:
                raise httpx.ConnectError("network is unreachable", request=request)
            return httpx.Response(200, text="ok")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await send(client, "GET", "https://example.com/")

        assert response.status_code == 200
        assert len(attempted) == 2

    @pytest.mark.asyncio
    async def test_a_timeout_is_not_retried_against_every_record(self, resolves_to) -> None:
        resolves_to("93.184.216.34", "93.184.216.35")
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectTimeout("timed out", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.ConnectTimeout):
                await send(client, "GET", "https://example.com/")

        assert attempts == 1
