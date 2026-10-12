"""Shared bounded page probes, per-host scheduling and negative-cache policy."""
import time
from collections import OrderedDict
from dataclasses import replace
from urllib.parse import urldefrag, urlsplit

from .config import DEFAULT_PDF
from .http import HttpClient, SourceError


class PublicResourceRequests:
    def __init__(self, client, config=DEFAULT_PDF):
        self.client = client
        self.config = config
        self.cache = OrderedDict()
        self.hosts = {}
        self.redirects = OrderedDict()
        self.redirect_targets = {}
        self.route_failures = {}
        self.events = []
        self.deferrals = []
        self.statistics = {'requests': 0, 'cache_hits': 0, 'cooldown_skips': 0,
                           'rate_limit_responses': 0, 'expanded_page_reads': 0,
                           'request_failures': 0, 'cache_response_hits': 0, 'cache_error_hits': 0,
                           'deferred_before_request': 0, 'deferred_after_redirect': 0,
                           'redirect_route_cache_hits': 0, 'source_backpressure_skips': 0}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _request(self, url, maximum_bytes, html_bytes, maximum_redirects, allow_source_backpressure=False):
        hostname = urlsplit(url).hostname or ''
        host = self.hosts.setdefault(hostname, {'last': None, 'until': 0, 'failures': 0, 'error': None})
        now = time.monotonic()
        self._guard_before_request(url, allow_source_backpressure)
        if host['last'] is not None:
            remaining = self.config.public_page_request_interval - (now - host['last'])
            if remaining > 0:
                time.sleep(remaining)
        host['last'] = time.monotonic()
        self.statistics['requests'] += 1
        previous_url = [url]

        def guard_redirect(destination):
            self._remember_redirect(previous_url[0], destination)
            previous_url[0] = destination
            self._before_redirect(destination)

        try:
            if isinstance(self.client, HttpClient):
                response = self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects,
                                             redirect_guard=guard_redirect)
            else:
                response = self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects)
            host['failures'] = 0
            final_host = self.hosts.get(urlsplit(response.url).hostname or '')
            if final_host:
                final_host['failures'] = 0
            self.route_failures.pop(hostname, None)
            return replace(response, request_context={'outcome': 'network_response', 'cache_status': 'miss',
                                                      'request_attempted': True})
        except SourceError as exc:
            affected = exc.endpoint_host or hostname
            if exc.error_type == 'source_deferred':
                self._record_route_failure(url, affected)
                raise
            self.statistics['request_failures'] += 1
            exc.request_context = {'outcome': 'request_failure', 'cache_status': 'miss',
                                   'request_attempted': True, 'endpoint_host': affected}
            self.events.append({'host': affected, 'error_type': exc.error_type, 'status_code': exc.status_code})
            if exc.status_code == 429 or exc.error_type in ('timeout', 'network_error', 'server_error'):
                failed_host = self.hosts.setdefault(affected, {'last': None, 'until': 0, 'failures': 0, 'error': None})
                failed_host['failures'] += 1
                backoff = self.config.public_page_cooldown * (2 ** min(failed_host['failures'] - 1, 10))
                delay = max(min(backoff, self.config.source_maximum_cooldown), exc.retry_after or 0)
                failed_host.update(until=time.monotonic() + delay, error=exc)
                if exc.status_code == 429:
                    self.statistics['rate_limit_responses'] += 1
                self._record_route_failure(url, affected)
            raise

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
            context = {'outcome': 'cooldown_skip', 'cache_status': 'not_queried',
                       'request_attempted': request_started, 'destination_request_attempted': False,
                       'endpoint_host': hostname, 'route_kind': route_kind}
            self.deferrals.append(dict(context, cause=error.error_type))
            raise SourceError('Host temporarily deferred after {} (no new request to this host)'.format(error.error_type),
                              status_code=error.status_code, error_type='source_deferred',
                              retry_after=host['until'] - time.monotonic(), endpoint_host=hostname,
                              request_context=context)

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

    def _before_redirect(self, url):
        self._guard_redirect(url, request_started=True)
        hostname = urlsplit(url).hostname or ''
        host = self.hosts.setdefault(hostname, {'last': None, 'until': 0, 'failures': 0, 'error': None})
        if host['last'] is not None:
            remaining = self.config.public_page_request_interval - (time.monotonic() - host['last'])
            if remaining > 0:
                time.sleep(remaining)
        host['last'] = time.monotonic()

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5, allow_source_backpressure=False):
        key = urldefrag(url)[0]
        cached = self.cache.get(key)
        if cached is not None:
            expires, response = cached
            if expires <= time.monotonic():
                del self.cache[key]
            elif not isinstance(response, Exception) and (html_bytes == 0 or cached[1].content_type not in (
                    'text/html', 'application/xhtml+xml') or len(response.prefix) >= html_bytes or response.body_complete):
                self.statistics['cache_hits'] += 1
                self.statistics['cache_response_hits'] += 1
                self.cache.move_to_end(key)
                cached_response = replace(response, request_context={
                    'outcome': 'cached_response', 'cache_status': 'hit', 'request_attempted': False})
                return self._view(cached_response, maximum_bytes, html_bytes)
            elif isinstance(response, Exception):
                self.statistics['cache_hits'] += 1
                self.statistics['cache_error_hits'] += 1
                raise SourceError(str(response), response.status_code, response.error_type,
                                  response.retry_after, response.endpoint_host, request_context={
                                      'outcome': 'cached_failure', 'cache_status': 'hit',
                                      'request_attempted': False, 'endpoint_host': response.endpoint_host})
        try:
            response = self._request(url, maximum_bytes, html_bytes, maximum_redirects, allow_source_backpressure)
            html = response.content_type in ('text/html', 'application/xhtml+xml')
            if html and html_bytes and len(response.prefix) < html_bytes:
                response = replace(response, body_complete=True)
            can_expand = 0 < html_bytes < self.config.public_page_maximum_bytes
            if can_expand and html and not response.body_complete and len(response.prefix) >= html_bytes:
                self.statistics['expanded_page_reads'] += 1
                response = self._request(url, maximum_bytes, self.config.public_page_maximum_bytes,
                                         maximum_redirects, allow_source_backpressure)
                if len(response.prefix) < self.config.public_page_maximum_bytes:
                    response = replace(response, body_complete=True)
        except SourceError as exc:
            # Transient failures are governed by the host cooldown, not permanent
            # URL absence. Definitive per-URL failures may be cached briefly.
            if exc.status_code in (403, 404, 410):
                self._cache(key, exc)
            raise
        self._cache(key, response)
        final_key = urldefrag(response.url)[0]
        if final_key != key:
            self._cache(final_key, response)
        return self._view(response, maximum_bytes, html_bytes)

    def _cache(self, key, response):
        self.cache[key] = (time.monotonic() + self.config.public_page_cache_seconds, response)
        self.cache.move_to_end(key)
        while len(self.cache) > self.config.public_page_cache_entries:
            self.cache.popitem(last=False)
        while sum(len(value.prefix) for _, value in self.cache.values() if not isinstance(value, Exception)) > self.config.public_page_cache_bytes:
            self.cache.popitem(last=False)

    @staticmethod
    def _view(response, maximum_bytes, html_bytes):
        if html_bytes and response.content_type in ('text/html', 'application/xhtml+xml'):
            return response
        return replace(response, prefix=response.prefix[:maximum_bytes])
