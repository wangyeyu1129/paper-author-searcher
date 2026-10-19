"""Decorator base for layers around the HTTP client (cache, budget, public-resource scheduling).

Each layer overrides only the operations it changes and forwards the rest explicitly, so the chain is visible to
type checking and to readers; nothing is reached through ``__getattr__`` magic. ``unwrap`` finds the network
client at the bottom of a chain.
"""

from collections.abc import Callable
from typing import Any


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


def unwrap(client: Any) -> Any:
    """The innermost client of a decorator chain (the network client itself when it is undecorated)."""
    seen = set()
    while isinstance(client, HttpClientDecorator) and id(client) not in seen:
        seen.add(id(client))
        client = client.client
    return client
