"""Record/replay decorator for metadata GETs and link probes, so identity and full-text runs can be repeated offline."""

import base64
import hashlib
import json
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .http import SourceError, URLProbe
from .http_decorators import HttpClientDecorator

SECRET_PARAMETERS = frozenset({'api_key', 'apikey', 'key', 'token', 'mailto', 'email'})
# Response headers a probe consumer reads; cookies and the rest are never stored.
PROBE_HEADERS = frozenset({'content-type', 'content-length', 'content-range', 'retry-after'})
# Failures that describe this process (scheduler state, a replay), not the remote resource.
UNRECORDED_PROBE_ERRORS = frozenset({'source_deferred', 'cache_miss', 'offline_replay'})


class CacheMode(str, Enum):
    RECORD = 'record'  # read hits, fetch and store misses
    REPLAY = 'replay'  # read hits only; a miss is a SourceError
    REFRESH = 'refresh'  # always fetch and overwrite


class CachingHttpClient(HttpClientDecorator):
    """Wraps an HttpClient; ``get`` and ``probe`` are cached. Posts and downloads pass straight through.

    Secrets never reach the cache key or the stored file; failed GETs are not cached.

    A probe is stored with its redirect hops and its outcome, failures included, so a replay answers exactly as
    the recorded run did. Recording serves stored successes but probes a stored failure again, since a failure
    may have been transient; only a replay serves recorded failures.
    """

    def __init__(
        self,
        client: Any,
        directory: Path,
        mode: CacheMode = CacheMode.RECORD,
        ignored_parameters: Iterable[str] = SECRET_PARAMETERS,
    ) -> None:
        super().__init__(client)
        self.directory = Path(directory)
        self.mode = CacheMode(mode)
        self.ignored_parameters = frozenset(name.lower() for name in ignored_parameters)
        self.statistics = {'hits': 0, 'misses': 0, 'stored': 0, 'probe_hits': 0, 'probe_misses': 0, 'probe_stored': 0}

    def key(self, url: str, params: dict | None, xml: bool) -> str:
        public = sorted(
            (name, str(value))
            for name, value in (params or {}).items()
            if value is not None and name.lower() not in self.ignored_parameters
        )
        material = url + '?' + urlencode(public) + ('#xml' if xml else '')
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        key = self.key(url, params, xml)
        path = self.directory / key[:2] / (key + '.json')
        if self.mode is not CacheMode.REFRESH and path.exists():
            self.statistics['hits'] += 1
            with path.open(encoding='utf-8') as handle:
                stored = json.load(handle)
            return ET.fromstring(stored['body']) if xml else stored['body']
        self.statistics['misses'] += 1
        if self.mode is CacheMode.REPLAY:
            raise SourceError('Replay cache miss for ' + url, error_type='cache_miss')
        body = self.client.get(url, params, headers, xml)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(
                {'url': url, 'xml': xml, 'body': ET.tostring(body, encoding='unicode') if xml else body},
                handle,
                ensure_ascii=False,
            )
        temporary.replace(path)
        self.statistics['stored'] += 1
        return body

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Any = None,
    ) -> Any:
        path = self._probe_path(url)
        stored = self._servable_probe(path, maximum_bytes, html_bytes)
        if stored is not None:
            self.statistics['probe_hits'] += 1
            for hop in stored['hops'][:maximum_redirects]:
                if redirect_guard is not None:
                    redirect_guard(hop)
            if 'error' in stored:
                raise SourceError(**stored['error'])
            return _stored_response(stored, maximum_bytes, html_bytes)
        self.statistics['probe_misses'] += 1
        if self.mode is CacheMode.REPLAY:
            raise SourceError('Replay cache miss for ' + url, error_type='cache_miss')
        hops: list[str] = []

        def record_hop(destination: str) -> None:
            hops.append(destination)
            if redirect_guard is not None:
                redirect_guard(destination)

        try:
            response = super().probe(url, maximum_bytes, html_bytes, maximum_redirects, record_hop)
        except SourceError as exc:
            if exc.error_type not in UNRECORDED_PROBE_ERRORS:
                self._store(path, _probe_failure(url, hops, exc))
                self.statistics['probe_stored'] += 1
            raise
        self._store(path, _probe_success(url, hops, response, maximum_bytes, html_bytes))
        self.statistics['probe_stored'] += 1
        return response

    def serves_probe_from_cache(self, url: str, maximum_bytes: int = 1024, html_bytes: int = 0) -> bool:
        """Whether ``probe`` would answer from the cache, so a scheduler above need not pace a network request."""
        return self._servable_probe(self._probe_path(url), maximum_bytes, html_bytes) is not None

    def _probe_path(self, url: str) -> Path:
        key = hashlib.sha256(('probe ' + url).encode('utf-8')).hexdigest()
        return self.directory / key[:2] / (key + '.probe.json')

    def _servable_probe(self, path: Path, maximum_bytes: int, html_bytes: int) -> dict[str, Any] | None:
        if self.mode is CacheMode.REFRESH or not path.exists():
            return None
        try:
            with path.open(encoding='utf-8') as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return None
        if 'error' in stored:
            return stored if self.mode is CacheMode.REPLAY else None
        prefix_length = stored['prefix_length']
        return stored if stored['body_complete'] or prefix_length >= _limit(stored, maximum_bytes, html_bytes) else None

    @staticmethod
    def _store(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(record, handle, ensure_ascii=False)
        temporary.replace(path)


def _limit(stored: dict[str, Any], maximum_bytes: int, html_bytes: int) -> int:
    """The prefix a live probe would read for this request (HTML pages read up to ``html_bytes``)."""
    html = stored['content_type'] in ('text/html', 'application/xhtml+xml')
    return html_bytes if html and html_bytes else maximum_bytes


def _probe_success(url: str, hops: list[str], response: Any, maximum_bytes: int, html_bytes: int) -> dict[str, Any]:
    return {
        'url': url,
        'hops': hops,
        'final_url': response.url,
        'status': response.status,
        'content_type': response.content_type,
        'headers': {name: value for name, value in response.headers.items() if name.lower() in PROBE_HEADERS},
        'prefix': base64.b64encode(zlib.compress(response.prefix)).decode('ascii'),
        'prefix_length': len(response.prefix),
        'body_complete': response.body_complete,
    }


def _probe_failure(url: str, hops: list[str], exc: SourceError) -> dict[str, Any]:
    error = {
        'message': str(exc),
        'status_code': exc.status_code,
        'error_type': exc.error_type,
        'retry_after': exc.retry_after,
        'endpoint_host': exc.endpoint_host,
    }
    return {'url': url, 'hops': hops, 'error': error}


def _stored_response(stored: dict[str, Any], maximum_bytes: int, html_bytes: int) -> URLProbe:
    prefix = zlib.decompress(base64.b64decode(stored['prefix']))
    limit = _limit(stored, maximum_bytes, html_bytes)
    return URLProbe(
        stored['final_url'],
        stored['status'],
        stored['content_type'],
        dict(stored['headers']),
        prefix[:limit],
        stored['body_complete'] and len(prefix) <= limit,
    )


class OfflineHttpClient(HttpClientDecorator):
    """Sits below a replay cache: any request that would reach the network fails, so a replay is offline by
    construction (homepage probes, posts and downloads included), not by the luck of a complete cache."""

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        raise self._refused(url)

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        raise self._refused(url)

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Any = None,
    ) -> Any:
        raise self._refused(url)

    def stream_pdf(self, url: str, destination: Any, config: Any) -> Any:
        raise self._refused(url)

    @staticmethod
    def _refused(url: str) -> SourceError:
        return SourceError('Offline replay: no network request for ' + url, error_type='offline_replay')


def cached_client(client: Any, directory: str | Path | None, mode: str | None) -> Any:
    """Return the client unchanged when no cache directory is configured; a replay never touches the network."""
    if not directory:
        return client
    cache_mode = CacheMode(mode or CacheMode.RECORD.value)
    if cache_mode is CacheMode.REPLAY:
        client = OfflineHttpClient(client)
    return CachingHttpClient(client, Path(directory), cache_mode)
