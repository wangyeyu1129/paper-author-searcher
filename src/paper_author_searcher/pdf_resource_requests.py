"""Shared bounded page probes, per-host scheduling and negative-cache policy."""

import threading
import time
from collections import OrderedDict
from dataclasses import replace
from urllib.parse import urldefrag, urlsplit

from .config import DEFAULT_PDF
from .http import HttpClient, SourceError
from .http_decorators import HttpClientDecorator, unwrap
from .pacing import RequestPacer
from .status import CacheStatus, RequestOutcome


class PublicResourceRequests(HttpClientDecorator):
    """Schedules public-resource probes: per-host pacing and cooldowns, redirect routes, a bounded page cache."""

    def __init__(self, client, config=DEFAULT_PDF):
        super().__init__(client)
        self.config = config
        self.cache = OrderedDict()
        self.cache_bytes = 0
        # Guards all scheduler state; never held during network I/O.
        self._lock = threading.RLock()
        self.pacer = RequestPacer(config.public_page_request_interval)
        self.hosts = {}
        self.redirects = OrderedDict()
        self.redirect_targets = {}
        self.route_failures = {}
        self.events = []
        self.deferrals = []
        self.statistics = {
            'requests': 0,
            'cache_hits': 0,
            'cooldown_skips': 0,
            'rate_limit_responses': 0,
            'expanded_page_reads': 0,
            'request_failures': 0,
            'cache_response_hits': 0,
            'cache_error_hits': 0,
            'deferred_before_request': 0,
            'deferred_after_redirect': 0,
            'redirect_route_cache_hits': 0,
            'source_backpressure_skips': 0,
        }

    def _request(self, url, maximum_bytes, html_bytes, maximum_redirects, allow_source_backpressure=False):
        hostname = urlsplit(url).hostname or ''
        with self._lock:
            self._host(hostname)
            self._guard_before_request(url, allow_source_backpressure)
            self.statistics['requests'] += 1
        self.pacer.wait(hostname)
        previous_url = [url]

        def guard_redirect(destination):
            with self._lock:
                self._remember_redirect(previous_url[0], destination)
                previous_url[0] = destination
            self._before_redirect(destination)

        try:
            # The network client re-checks every redirect hop through this guard (also through cache/budget).
            if isinstance(unwrap(self.client), HttpClient):
                response = self.client.probe(
                    url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard=guard_redirect
                )
            else:
                response = self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects)
        except SourceError as exc:
            with self._lock:
                self._record_failure(url, hostname, exc)
            raise
        with self._lock:
            self.hosts[hostname]['failures'] = 0
            final_host = self.hosts.get(urlsplit(response.url).hostname or '')
            if final_host:
                final_host['failures'] = 0
            self.route_failures.pop(hostname, None)
        return replace(
            response,
            request_context={
                'outcome': RequestOutcome.NETWORK_RESPONSE,
                'cache_status': CacheStatus.MISS,
                'request_attempted': True,
            },
        )

    def cooldown_remaining(self, hostnames):
        """Seconds until none of these hosts is cooling down (0 when none is)."""
        now = time.monotonic()
        with self._lock:
            waits = [self.hosts[name]['until'] - now for name in hostnames if name in self.hosts]
        return max([0.0, *waits])

    def _host(self, hostname):
        return self.hosts.setdefault(hostname, {'until': 0, 'failures': 0, 'error': None})

    def _before_redirect(self, url):
        """Each redirect hop honours the destination host's cooldown and pacing."""
        hostname = urlsplit(url).hostname or ''
        with self._lock:
            self._guard_redirect(url, request_started=True)
            self._host(hostname)
        self.pacer.wait(hostname)

    def _record_failure(self, url, hostname, exc):
        affected = exc.endpoint_host or hostname
        if exc.error_type == 'source_deferred':
            self._record_route_failure(url, affected)
            return
        self.statistics['request_failures'] += 1
        exc.request_context = {
            'outcome': RequestOutcome.REQUEST_FAILURE,
            'cache_status': CacheStatus.MISS,
            'request_attempted': True,
            'endpoint_host': affected,
        }
        self.events.append({'host': affected, 'error_type': exc.error_type, 'status_code': exc.status_code})
        if exc.status_code == 429 or exc.error_type in ('timeout', 'network_error', 'server_error'):
            failed_host = self._host(affected)
            failed_host['failures'] += 1
            backoff = self.config.public_page_cooldown * (2 ** min(failed_host['failures'] - 1, 10))
            delay = max(min(backoff, self.config.source_maximum_cooldown), exc.retry_after or 0)
            failed_host.update(until=time.monotonic() + delay, error=exc)
            if exc.status_code == 429:
                self.statistics['rate_limit_responses'] += 1
            self._record_route_failure(url, affected)

    def _guard_redirect(self, url, request_started=False, route_kind=None):
        hostname = urlsplit(url).hostname or ''
        self._guard_host(hostname, request_started, route_kind)

    def _guard_host(self, hostname, request_started=False, route_kind=None):
        host = self.hosts.get(hostname)
        if host and time.monotonic() < host['until']:
            self.statistics['cooldown_skips'] += 1
            phase = 'deferred_after_redirect' if request_started else 'deferred_before_request'
            self.statistics[phase] += 1
            error = host['error']
            context = {
                'outcome': RequestOutcome.COOLDOWN_SKIP,
                'cache_status': CacheStatus.NOT_QUERIED,
                'request_attempted': request_started,
                'destination_request_attempted': False,
                'endpoint_host': hostname,
                'route_kind': route_kind,
            }
            self.deferrals.append(dict(context, cause=error.error_type))
            raise SourceError(
                f'Host temporarily deferred after {error.error_type} (no new request to this host)',
                status_code=error.status_code,
                error_type='source_deferred',
                retry_after=host['until'] - time.monotonic(),
                endpoint_host=hostname,
                request_context=context,
            )

    def _guard_before_request(self, url, allow_source_backpressure):
        self._guard_redirect(url)
        for destination in self._known_redirects(url):
            try:
                self._guard_redirect(destination, route_kind='observed_redirect')
            except SourceError:
                self.statistics['redirect_route_cache_hits'] += 1
                raise
        hostname = urlsplit(url).hostname or ''
        route = self.route_failures.get(hostname)
        if not allow_source_backpressure or not route or len(route['urls']) < self.config.redirect_failure_budget:
            return
        # A multi-destination gateway is not a single unavailable source route.
        if self.redirect_targets.get(hostname) != {route['destination']}:
            return
        try:
            self._guard_host(route['destination'], route_kind='source_backpressure')
        except SourceError:
            self.statistics['source_backpressure_skips'] += 1
            raise

    def _remember_redirect(self, origin, destination):
        origin_host, destination_host = urlsplit(origin).hostname, urlsplit(destination).hostname
        if origin_host != destination_host:
            self.redirect_targets.setdefault(origin_host, set()).add(destination_host)
        key = urldefrag(origin)[0]
        self.redirects[key] = (time.monotonic() + self.config.public_page_cache_seconds, destination)
        self.redirects.move_to_end(key)
        while len(self.redirects) > self.config.public_page_cache_entries:
            self.redirects.popitem(last=False)

    def _known_redirects(self, url):
        visited = set()
        for _ in range(self.config.maximum_redirects):
            key = urldefrag(url)[0]
            route = self.redirects.get(key)
            if not route or key in visited:
                return
            expires, destination = route
            if expires <= time.monotonic():
                del self.redirects[key]
                return
            visited.add(key)
            yield destination
            url = destination

    def _record_route_failure(self, origin, destination_host):
        hostname = urlsplit(origin).hostname or ''
        if hostname == destination_host:
            return
        destinations = {urlsplit(url).hostname for url in self._known_redirects(origin)}
        if destination_host not in destinations:
            return
        route = self.route_failures.get(hostname)
        if not route or route['destination'] != destination_host:
            route = {'destination': destination_host, 'urls': set()}
            self.route_failures[hostname] = route
        if len(route['urls']) < self.config.redirect_failure_budget:
            route['urls'].add(urldefrag(origin)[0])

    def probe(
        self,
        url,
        maximum_bytes=1024,
        html_bytes=0,
        maximum_redirects=5,
        redirect_guard=None,  # the scheduler installs its own per-hop guard
        allow_source_backpressure=False,
    ):
        key = urldefrag(url)[0]
        with self._lock:
            cached = self._cached(key, html_bytes)
        if cached is not None:
            return self._view(cached, maximum_bytes, html_bytes)
        try:
            response = self._request(url, maximum_bytes, html_bytes, maximum_redirects, allow_source_backpressure)
            html = response.content_type in ('text/html', 'application/xhtml+xml')
            if html and html_bytes and len(response.prefix) < html_bytes:
                response = replace(response, body_complete=True)
            can_expand = 0 < html_bytes < self.config.public_page_maximum_bytes
            if can_expand and html and not response.body_complete and len(response.prefix) >= html_bytes:
                with self._lock:
                    self.statistics['expanded_page_reads'] += 1
                response = self._request(
                    url,
                    maximum_bytes,
                    self.config.public_page_maximum_bytes,
                    maximum_redirects,
                    allow_source_backpressure,
                )
                if len(response.prefix) < self.config.public_page_maximum_bytes:
                    response = replace(response, body_complete=True)
        except SourceError as exc:
            # Transient failures are governed by the host cooldown, not permanent
            # URL absence. Definitive per-URL failures may be cached briefly.
            if exc.status_code in (403, 404, 410):
                with self._lock:
                    self._cache(key, exc)
            raise
        with self._lock:
            self._cache(key, response)
            final_key = urldefrag(response.url)[0]
            if final_key != key:
                self._cache(final_key, response)
        return self._view(response, maximum_bytes, html_bytes)

    def _cached(self, key, html_bytes):
        """A fresh cached response usable for this read, or raise its cached definitive failure (lock held)."""
        cached = self.cache.get(key)
        if cached is None:
            return None
        expires, response = cached
        if expires <= time.monotonic():
            self._evict(key)
            return None
        if isinstance(response, SourceError):  # only definitive per-URL failures are cached
            self.statistics['cache_hits'] += 1
            self.statistics['cache_error_hits'] += 1
            raise SourceError(
                str(response),
                response.status_code,
                response.error_type,
                response.retry_after,
                response.endpoint_host,
                request_context={
                    'outcome': RequestOutcome.CACHED_FAILURE,
                    'cache_status': CacheStatus.HIT,
                    'request_attempted': False,
                    'endpoint_host': response.endpoint_host,
                },
            )
        sufficient = (
            html_bytes == 0
            or response.content_type not in ('text/html', 'application/xhtml+xml')
            or len(response.prefix) >= html_bytes
            or response.body_complete
        )
        if not sufficient:
            return None
        self.statistics['cache_hits'] += 1
        self.statistics['cache_response_hits'] += 1
        self.cache.move_to_end(key)
        return replace(
            response,
            request_context={
                'outcome': RequestOutcome.CACHED_RESPONSE,
                'cache_status': CacheStatus.HIT,
                'request_attempted': False,
            },
        )

    def _cache(self, key, response):
        """LRU insert bounded by entry count and total cached bytes, tracked incrementally (lock held)."""
        self._evict(key)
        self.cache[key] = (time.monotonic() + self.config.public_page_cache_seconds, response)
        self.cache_bytes += _cached_size(response)
        while self.cache and (
            len(self.cache) > self.config.public_page_cache_entries
            or self.cache_bytes > self.config.public_page_cache_bytes
        ):
            _, (_, evicted) = self.cache.popitem(last=False)
            self.cache_bytes -= _cached_size(evicted)

    def _evict(self, key):
        entry = self.cache.pop(key, None)
        if entry is not None:
            self.cache_bytes -= _cached_size(entry[1])

    @staticmethod
    def _view(response, maximum_bytes, html_bytes):
        if html_bytes and response.content_type in ('text/html', 'application/xhtml+xml'):
            return response
        return replace(response, prefix=response.prefix[:maximum_bytes])


def _cached_size(value):
    return 0 if isinstance(value, Exception) else len(value.prefix)
