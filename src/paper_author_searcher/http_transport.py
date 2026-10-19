"""Keep-alive HTTP transport for metadata API calls.

``urllib.request.urlopen`` opens a new TCP (and TLS) connection for every request and closes it afterwards. A
collection run pages through thousands of OpenAlex / Semantic Scholar responses on a handful of hosts, so the
handshakes dominate latency. This transport keeps one HTTP/1.1 connection per (scheme, host, port) per thread and
reuses it, while presenting the same contract ``urlopen`` did: a context-managed response with ``status``,
``headers``, ``read`` and ``geturl``, and ``urllib.error.HTTPError`` for non-2xx answers.

Public-resource probes and PDF downloads do not use this transport: they keep the DNS-pinned safety opener.
"""

import http.client
import io
import ssl
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, getproxies, proxy_bypass, urlopen

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Credentials a redirect must never carry to another host.
SENSITIVE_HEADERS = frozenset({'authorization', 'x-api-key', 'cookie', 'proxy-authorization'})
ERROR_BODY_BYTES = 64 * 1024
# A reused connection the server already closed fails like this; such a request is retried once on a fresh one.
STALE_CONNECTION_ERRORS = (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError)

ConnectionKey = Tuple[str, str, int]


class PooledResponse:
    """``urlopen``-compatible view of a response whose connection returns to the pool once fully read."""

    def __init__(self, response: http.client.HTTPResponse, url: str, discard: Any) -> None:
        self._response = response
        self._url = url
        self._discard = discard
        self.status = response.status
        self.headers = response.headers

    def read(self, amount: Optional[int] = None) -> bytes:
        return self._response.read(amount)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        # A partially read body would corrupt the next response on this connection.
        if not self._response.isclosed():
            self._discard()
        self._response.close()

    def __enter__(self) -> 'PooledResponse':
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class KeepAliveTransport:
    """Per-thread pools of persistent connections; safe to share between threads."""

    def __init__(
        self,
        maximum_redirects: int = 5,
        context: Optional[ssl.SSLContext] = None,
        proxies: Optional[Dict[str, str]] = None,
    ) -> None:
        if maximum_redirects < 0:
            raise ValueError('maximum_redirects must be nonnegative')
        self.maximum_redirects = maximum_redirects
        self.context = context or ssl.create_default_context()
        self.proxies = getproxies() if proxies is None else proxies
        self._local = threading.local()
        self._lock = threading.Lock()
        self._all: List[http.client.HTTPConnection] = []
        self.statistics = {'connections_opened': 0, 'connections_reused': 0, 'stale_reconnects': 0, 'redirects': 0}

    def open(self, request: Request, timeout: Optional[float] = None) -> Any:
        if self._proxied(request.full_url):
            return urlopen(request, timeout=timeout)  # proxies are urllib's job; keep its behaviour exactly
        method, url, body = request.get_method(), request.full_url, request.data
        headers = dict(request.header_items())
        for _ in range(self.maximum_redirects + 1):
            key, path = self._target(url)
            response = self._send(key, method, path, body, headers, timeout)
            location = response.getheader('Location')
            if response.status in REDIRECT_STATUSES and location:
                self._drain(key, response)
                target = urljoin(url, location)
                if urlsplit(target).scheme not in ('http', 'https'):
                    raise HTTPError(url, response.status, 'Redirect to a non-HTTP URL', response.headers, None)
                if urlsplit(target).netloc != urlsplit(url).netloc:
                    headers = {name: value for name, value in headers.items() if name.lower() not in SENSITIVE_HEADERS}
                if response.status == 303 or (response.status in (301, 302) and method == 'POST'):
                    method, body = 'GET', None
                    headers = {name: value for name, value in headers.items() if name.lower() != 'content-type'}
                url = target
                self._count('redirects')
                continue
            if not 200 <= response.status < 300:
                payload = response.read(ERROR_BODY_BYTES)
                if not response.isclosed():
                    self._discard(key)
                raise HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(payload))
            return PooledResponse(response, url, lambda key=key: self._discard(key))
        raise HTTPError(url, 310, 'Too many redirects', None, None)  # type: ignore[arg-type]

    def close(self) -> None:
        """Close every pooled connection (all threads)."""
        with self._lock:
            connections, self._all = self._all, []
        for connection in connections:
            connection.close()

    # Internals ---------------------------------------------------------------------------------------------------

    def _proxied(self, url: str) -> bool:
        parts = urlsplit(url)
        return bool(self.proxies.get(parts.scheme)) and not proxy_bypass(parts.hostname or '')

    @staticmethod
    def _target(url: str) -> Tuple[ConnectionKey, str]:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError(f'Unsupported URL: {url}')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
        return (parts.scheme, parts.hostname, port), path

    def _pool(self) -> Dict[ConnectionKey, http.client.HTTPConnection]:
        pool = getattr(self._local, 'pool', None)
        if pool is None:
            pool = self._local.pool = {}
        return pool

    def _send(
        self,
        key: ConnectionKey,
        method: str,
        path: str,
        body: Any,
        headers: Dict[str, str],
        timeout: Optional[float],
    ) -> http.client.HTTPResponse:
        pool = self._pool()
        connection = pool.get(key)
        reused = connection is not None
        if connection is None:
            connection = pool[key] = self._connect(key, timeout)
        else:
            self._count('connections_reused')
        connection.timeout = timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)
        try:
            connection.request(method, path, body, headers)
            return connection.getresponse()
        except STALE_CONNECTION_ERRORS:
            self._discard(key)
            if not reused:
                raise
            self._count('stale_reconnects')
            connection = pool[key] = self._connect(key, timeout)
            connection.request(method, path, body, headers)
            return connection.getresponse()
        except BaseException:
            self._discard(key)
            raise

    def _connect(self, key: ConnectionKey, timeout: Optional[float]) -> http.client.HTTPConnection:
        scheme, host, port = key
        connection: http.client.HTTPConnection
        if scheme == 'https':
            connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=self.context)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        with self._lock:
            self._all.append(connection)
        self._count('connections_opened')
        return connection

    def _drain(self, key: ConnectionKey, response: http.client.HTTPResponse) -> None:
        response.read(ERROR_BODY_BYTES)
        if not response.isclosed():
            self._discard(key)

    def _discard(self, key: ConnectionKey) -> None:
        connection = self._pool().pop(key, None)
        if connection is not None:
            connection.close()
            with self._lock:
                if connection in self._all:
                    self._all.remove(connection)

    def _count(self, name: str) -> None:
        with self._lock:
            self.statistics[name] += 1
