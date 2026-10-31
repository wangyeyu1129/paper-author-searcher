"""Decorator base for layers around the HTTP client (cache, budget, public-resource scheduling).

Each layer overrides only the operations it changes and forwards the rest explicitly, so the chain is visible to
type checking and to readers; nothing is reached through ``__getattr__`` magic. ``unwrap`` finds the network
client at the bottom of a chain.
"""

from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from .http import SourceError
from .pacing import Pacer, RequestPacer


class HttpClientDecorator:
    """Forwards every client operation and attribute to ``self.client``; subclasses override what they change."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        return self.client.get(url, params, headers, xml)

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        return self.client.post_json(url, payload, params=params, headers=headers, expected_type=expected_type)

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any:
        if redirect_guard is None:
            return self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects)
        return self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard=redirect_guard)

    def stream_pdf(self, url: str, destination: Any, config: Any) -> Any:
        return self.client.stream_pdf(url, destination, config)

    def serves_probe_from_cache(self, url: str, maximum_bytes: int = 1024, html_bytes: int = 0) -> bool:
        return serves_probe_from_cache(self.client, url, maximum_bytes, html_bytes)

    def close(self) -> None:
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()

    @property
    def timeout(self) -> Any:
        return getattr(self.client, 'timeout', None)

    @property
    def retries(self) -> Any:
        return getattr(self.client, 'retries', None)

    @property
    def transport_statistics(self) -> Any:
        return getattr(self.client, 'transport_statistics', None)


def serves_probe_from_cache(client: Any, url: str, maximum_bytes: int = 1024, html_bytes: int = 0) -> bool:
    """Whether a probe through this client would be answered from a cache below it (False for plain clients)."""
    # Looked up on the type: a client that merely answers every attribute (a test double) declares no cache.
    if not callable(getattr(type(client), 'serves_probe_from_cache', None)):
        return False
    return client.serves_probe_from_cache(url, maximum_bytes, html_bytes) is True


def unwrap(client: Any) -> Any:
    """The innermost client of a decorator chain (the network client itself when it is undecorated)."""
    seen = set()
    while isinstance(client, HttpClientDecorator) and id(client) not in seen:
        seen.add(id(client))
        client = client.client
    return client


class PacedHttpClient(HttpClientDecorator):
    """Keeps each configured host's minimum request interval. It belongs directly above the network client, below
    the cache, so only requests that really go out wait. A pacer shared between processes keeps the interval across
    all of them. When a host answers 429 with Retry-After, the host is held back for that long, up to
    ``maximum_hold`` seconds: a daily quota's hours-long answer must not put every search to sleep."""

    def __init__(
        self,
        client: Any,
        intervals: Iterable[tuple[str, float]],
        pacer_factory: Callable[[float], Pacer] = RequestPacer,
        maximum_hold: float = 60.0,
    ) -> None:
        super().__init__(client)
        self.pacers = {host.lower(): pacer_factory(interval) for host, interval in intervals if interval > 0}
        self.maximum_hold = maximum_hold

    def _wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or '').lower()
        pacer = self.pacers.get(host)
        if pacer is not None:
            pacer.wait(host)

    def _note_refusal(self, url: str, error: SourceError) -> None:
        host = (urlsplit(url).hostname or '').lower()
        pacer = self.pacers.get(host)
        if pacer is not None and error.status_code == HTTPStatus.TOO_MANY_REQUESTS and error.retry_after:
            pacer.hold(host, min(float(error.retry_after), self.maximum_hold))

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        self._wait(url)
        try:
            return self.client.get(url, params, headers, xml)
        except SourceError as error:
            self._note_refusal(url, error)
            raise

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        self._wait(url)
        try:
            return super().post_json(url, payload, params, headers, expected_type)
        except SourceError as error:
            self._note_refusal(url, error)
            raise

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any:
        self._wait(url)
        return super().probe(url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard)
