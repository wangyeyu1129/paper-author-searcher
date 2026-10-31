"""Where OpenAlex places a person: the institutions of their author ID, and those printed on a paper's byline."""

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .http import SourceError

logger = logging.getLogger(__name__)

DOI_PREFIX = 'https://doi.org/'


class AuthorshipPlaces:
    def __init__(self, client: Any, base_url: str, api_key: str | None, batch_size: int = 40) -> None:
        self.client = client
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.batch_size = batch_size

    def history(self, author_ids: Iterable[str]) -> set[str]:
        """Every institution on these author IDs. Empty when a source cannot say, which proves nothing."""
        places: set[str] = set()
        for author_id in author_ids:
            try:
                data = self.client.get(
                    f'{self.base_url}/authors/{author_id}',
                    {'select': 'affiliations,last_known_institutions', 'api_key': self.api_key},
                )
            except SourceError as error:
                logger.info('Institutions of %s not established: %s', author_id, error)
                return set()
            held = [item.get('institution') or {} for item in data.get('affiliations') or []]
            held += data.get('last_known_institutions') or []
            places.update(item['display_name'] for item in held if item.get('display_name'))
        return places

    def on_bylines(self, dois: Sequence[str], is_target: Callable[[str], bool]) -> dict[str, set[str]]:
        """DOI to the institutions printed for the byline names ``is_target`` accepts; papers without any are absent."""
        found: dict[str, set[str]] = {}
        for start in range(0, len(dois), self.batch_size):
            batch = dois[start : start + self.batch_size]
            try:
                data = self.client.get(
                    self.base_url + '/works',
                    {
                        'filter': 'doi:' + '|'.join(batch),
                        'per-page': self.batch_size,
                        'select': 'doi,authorships',
                        'api_key': self.api_key,
                    },
                )
            except SourceError as error:
                logger.info('Bylines of %s papers not established: %s', len(batch), error)
                continue
            for work in data.get('results') or []:
                places = {
                    institution['display_name']
                    for authorship in work.get('authorships') or []
                    if is_target((authorship.get('author') or {}).get('display_name') or '')
                    for institution in authorship.get('institutions') or []
                    if institution.get('display_name')
                }
                if places:
                    found[(work.get('doi') or '').lower().removeprefix(DOI_PREFIX)] = places
        return found
