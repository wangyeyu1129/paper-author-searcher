"""Bounded Semantic Scholar requests, caching and source-local cooldowns."""
import time
from urllib.parse import quote

from .config import DEFAULT_PDF, DEFAULT_SOURCES
from .http import SourceError


class SemanticScholarRequests:
    fields = 'title,year,authors,externalIds,openAccessPdf,url'

    def __init__(self, client, api_key=None, config=DEFAULT_PDF, sources=DEFAULT_SOURCES):
        self.client = client
        self.api_key = api_key
        self.config = config
        self.sources = sources
        self.cache = {}
        self.origins = {}
        self.batch_errors = {}
        self.last_request = None
        self.cooldown_until = 0.0
        self.deferred_error = None
        self.blocked_error = None
        self.failure_streak = 0
        self.events = []
        self.statistics = {'batch_requests': 0, 'individual_requests': 0, 'title_search_requests': 0,
                           'cooldown_skips': 0, 'authentication_skips': 0, 'work_cache_hits': 0,
                           'title_cache_hits': 0, 'request_failures': 0}

    def url(self, identifier):
        return self.sources.semantic_scholar_url + '/paper/' + quote(identifier, safe=':')

    def _call(self, request, operation):
        if self.blocked_error is not None:
            self.statistics['authentication_skips'] += 1
            raise SourceError('Semantic Scholar unavailable after an authentication/access error (no new request)',
                              status_code=self.blocked_error.status_code, error_type='source_deferred',
                              request_context={'outcome': 'source_unavailable_skip', 'request_attempted': False})
        if time.monotonic() < self.cooldown_until:
            self.statistics['cooldown_skips'] += 1
            raise SourceError('Semantic Scholar temporarily deferred (no new request)',
                              status_code=self.deferred_error.status_code, error_type='source_deferred',
                              retry_after=self.cooldown_until - time.monotonic(),
                              request_context={'outcome': 'cooldown_skip', 'request_attempted': False})
        if self.last_request is not None:
            remaining = self.config.semantic_scholar_request_interval - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()
        self.statistics[operation] += 1
        try:
            result = request()
            self.failure_streak = 0
            return result
        except SourceError as exc:
            self.statistics['request_failures'] += 1
            exc.request_context = {'outcome': 'request_failure', 'request_attempted': True}
            self.events.append({'operation': operation, 'error_type': exc.error_type, 'status_code': exc.status_code})
            if exc.status_code in (401, 403):
                self.blocked_error = exc
            elif exc.status_code == 429 or exc.error_type in (
                    'timeout', 'network_error', 'server_error', 'invalid_content_type', 'empty_response'):
                self.failure_streak += 1
                delay = self.config.semantic_scholar_cooldown * (2 ** min(self.failure_streak - 1, 10))
                wait = max(min(delay, self.config.source_maximum_cooldown), exc.retry_after or 0)
                self.cooldown_until = time.monotonic() + wait
                self.deferred_error = exc
            raise

    def _headers(self):
        return {'x-api-key': self.api_key} if self.api_key else None

    def prefetch(self, identifiers):
        pending = [value for value in dict.fromkeys(identifiers) if value not in self.cache]
        size = self.config.semantic_scholar_batch_size
        url = self.sources.semantic_scholar_url + '/paper/batch'
        for start in range(0, len(pending), size):
            batch = pending[start:start + size]
            try:
                response = self._call(lambda: self.client.post_json(
                    url, {'ids': batch}, params={'fields': self.fields},
                    headers=self._headers(), expected_type=list), 'batch_requests')
                if not isinstance(response, list) or len(response) != len(batch):
                    raise SourceError('Semantic Scholar batch response has an unexpected length',
                                      error_type='malformed_response')
                if any(item is not None and (not isinstance(item, dict) or item.get('error')) for item in response):
                    raise SourceError('Semantic Scholar batch response contains an invalid item',
                                      error_type='malformed_response')
                for identifier, work in zip(batch, response):
                    self.cache[identifier] = work
                    self.origins[identifier] = 'batch'
            except (SourceError, ValueError, TypeError) as exc:
                for identifier in batch:
                    self.batch_errors[identifier] = str(exc)
                # Individual lookups can recover from a malformed/unsupported batch.
                if self.blocked_error is not None or time.monotonic() < self.cooldown_until:
                    break

    def work(self, identifier):
        if identifier not in self.cache:
            try:
                response = self._call(lambda: self.client.get(
                    self.url(identifier), params={'fields': self.fields}, headers=self._headers()), 'individual_requests')
            except SourceError as exc:
                if exc.status_code == 404:
                    self.cache[identifier] = None
                    self.origins[identifier] = 'individual'
                raise
            if not isinstance(response, dict) or response.get('error'):
                raise SourceError('Semantic Scholar paper response must be an object', error_type='malformed_response')
            self.cache[identifier] = response
            self.origins[identifier] = 'individual'
        else:
            self.statistics['work_cache_hits'] += 1
        work = self.cache[identifier]
        if work is None:
            raise SourceError('Semantic Scholar has no indexed paper for this identifier', status_code=404)
        return work

    def search(self, title):
        key = 'title:' + title
        if key not in self.cache:
            url = self.sources.semantic_scholar_url + '/paper/search'
            response = self._call(lambda: self.client.get(
                url, params={'query': title, 'limit': self.config.semantic_scholar_search_results,
                             'fields': self.fields}, headers=self._headers()), 'title_search_requests')
            items = response.get('data') if isinstance(response, dict) else None
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise SourceError('Semantic Scholar search response must contain a paper list',
                                  error_type='malformed_response')
            self.cache[key] = items
        else:
            self.statistics['title_cache_hits'] += 1
        return self.cache[key]

    def diagnostics(self, identifier):
        result = {'lookup_method': self.origins.get(identifier, 'individual')}
        if identifier in self.batch_errors:
            result['batch_error'] = self.batch_errors[identifier]
        if self.deferred_error is not None and time.monotonic() < self.cooldown_until:
            result['source_cooldown_active'] = True
        return result
