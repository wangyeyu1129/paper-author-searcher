"""Record/replay decorator for metadata GET requests, so identity runs can be repeated offline."""

import hashlib
import json
import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Union
from urllib.parse import urlencode

from .http import SourceError

SECRET_PARAMETERS = frozenset({'api_key', 'apikey', 'key', 'token', 'mailto', 'email'})


class CacheMode(str, Enum):
    RECORD = 'record'  # read hits, fetch and store misses
    REPLAY = 'replay'  # read hits only; a miss is a SourceError
    REFRESH = 'refresh'  # always fetch and overwrite


class CachingHttpClient:
    """Wraps an HttpClient; only ``get`` is cached. Probes, posts and downloads pass straight through.

    Secrets never reach the cache key or the stored file; failed requests are not cached.
    """

    def __init__(
        self,
        client,
        directory: Path,
        mode: CacheMode = CacheMode.RECORD,
        ignored_parameters: Iterable[str] = SECRET_PARAMETERS,
    ) -> None:
        self.client = client
        self.directory = Path(directory)
        self.mode = CacheMode(mode)
        self.ignored_parameters = frozenset(name.lower() for name in ignored_parameters)
        self.statistics = {'hits': 0, 'misses': 0, 'stored': 0}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def key(self, url: str, params: Optional[dict], xml: bool) -> str:
        public = sorted(
            (name, str(value))
            for name, value in (params or {}).items()
            if value is not None and name.lower() not in self.ignored_parameters
        )
        material = url + '?' + urlencode(public) + ('#xml' if xml else '')
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

    def get(self, url, params=None, headers=None, xml=False):
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


def cached_client(client, directory: Optional[Union[str, Path]], mode: Optional[str]):
    """Return the client unchanged when no cache directory is configured."""
    if not directory:
        return client
    return CachingHttpClient(client, Path(directory), CacheMode(mode or CacheMode.RECORD.value))
