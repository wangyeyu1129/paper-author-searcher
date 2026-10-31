"""Whether a person's name is effectively theirs alone, so an author ID's papers need no further proof.

A paper under the person's confirmed author ID usually still needs paper-level evidence, because sources merge
namesakes into one ID. That danger needs namesakes: when nearly all the works under this exact name sit under the
person's own IDs and the name does not collide easily, the requirement only loses young researchers' real papers.
"""

import logging
from collections.abc import Callable, Iterable
from typing import Any

from .http import SourceError
from .matching import normalize

logger = logging.getLogger(__name__)


class NameRarity:
    def __init__(
        self,
        client: Any,
        base_url: str,
        api_key: str | None,
        collides_easily: Callable[[str], bool],
        minimum_share: float,
        results: int = 25,
    ) -> None:
        self.client = client
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.collides_easily = collides_easily
        self.minimum_share = minimum_share
        self.results = results

    def rare(self, name: str, own_ids: Iterable[str]) -> bool:
        """Whether ``own_ids`` hold at least ``minimum_share`` of the works of all authors with exactly this name."""
        if self.collides_easily(name):
            return False
        try:
            data = self.client.get(
                self.base_url + '/authors',
                {
                    'search': name,
                    'per-page': self.results,
                    'select': 'id,display_name,works_count',
                    'api_key': self.api_key,
                },
            )
        except SourceError as error:
            logger.info('Rarity of %s not established: %s', name, error)
            return False
        own = {str(value).rstrip('/').rsplit('/', 1)[-1] for value in own_ids}
        wanted = normalize(name)
        mine = total = 0
        for author in data.get('results') or []:
            if normalize(author.get('display_name') or '') != wanted:
                continue
            works = author.get('works_count') or 0
            total += works
            if str(author.get('id') or '').rsplit('/', 1)[-1] in own:
                mine += works
        return total > 0 and mine / total >= self.minimum_share
