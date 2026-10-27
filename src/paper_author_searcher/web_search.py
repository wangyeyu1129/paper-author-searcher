"""Web search APIs behind one protocol, for finding a researcher's homepage, publication page or CV.

Each adapter maps its provider's response to ``SearchHit``; which one runs is configuration
(``DiscoveryConfig.web_search``, registered in ``source_catalog.WEB_SEARCH``). A provider without its API key is
not built at all, so discovery simply skips the web step.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ''
    snippet: str = ''


class WebSearch(Protocol):
    name: str

    def search(self, query: str, count: int) -> list[SearchHit]: ...


def _hits(items: Iterable[Mapping[str, Any]], snippet_field: str) -> list[SearchHit]:
    return [
        SearchHit(str(item['url']), str(item.get('title') or ''), str(item.get(snippet_field) or ''))
        for item in items
        if item.get('url')
    ]


class BraveSearch:
    name = 'brave'

    def __init__(self, client: Any, url: str, api_key: str) -> None:
        self.client, self.url, self.api_key = client, url, api_key

    def search(self, query: str, count: int) -> list[SearchHit]:
        data = self.client.get(
            self.url,
            {'q': query, 'count': count},
            {'Accept': 'application/json', 'X-Subscription-Token': self.api_key},
        )
        return _hits(((data or {}).get('web') or {}).get('results') or [], 'description')


class TavilySearch:
    name = 'tavily'

    def __init__(self, client: Any, url: str, api_key: str) -> None:
        self.client, self.url, self.api_key = client, url, api_key

    def search(self, query: str, count: int) -> list[SearchHit]:
        data = self.client.post_json(
            self.url, {'query': query, 'max_results': count}, headers={'Authorization': 'Bearer ' + self.api_key}
        )
        return _hits((data or {}).get('results') or [], 'content')


class ExaSearch:
    name = 'exa'

    def __init__(self, client: Any, url: str, api_key: str) -> None:
        self.client, self.url, self.api_key = client, url, api_key

    def search(self, query: str, count: int) -> list[SearchHit]:
        payload = {'query': query, 'numResults': count}
        data = self.client.post_json(self.url, payload, headers={'x-api-key': self.api_key})
        return _hits((data or {}).get('results') or [], 'text')
