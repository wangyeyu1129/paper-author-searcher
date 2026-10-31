"""What else a school is called: callers write "London School of Economics", sources "...and Political Science".

The source's own record of the institution supplies the other names, so no list of schools has to be kept here.
"""

import logging
from dataclasses import replace
from typing import Any

from .config import DEFAULT_MATCHING, MatchingConfig
from .http import SourceError
from .matching import normalize
from .models import ProfessorQuery

logger = logging.getLogger(__name__)

SEARCH_RESULTS = 3


class InstitutionNames:
    def __init__(
        self, client: Any, base_url: str, api_key: str | None = None, config: MatchingConfig = DEFAULT_MATCHING
    ) -> None:
        self.client = client
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.config = config

    def expand(self, query: ProfessorQuery) -> ProfessorQuery:
        """The query with the school's other names among its aliases; unchanged when none can be established."""
        try:
            data = self.client.get(
                self.base_url + '/institutions',
                {
                    'search': query.university,
                    'per-page': SEARCH_RESULTS,
                    'select': 'display_name,display_name_alternatives,display_name_acronyms',
                    'api_key': self.api_key,
                },
            )
        except SourceError as error:
            logger.info('Other names of %s not looked up: %s', query.university, error)
            return query
        known = {normalize(name) for name in [query.university, *query.university_aliases]}
        for item in data.get('results') or []:
            names = [item.get('display_name') or '', *(item.get('display_name_alternatives') or [])]
            if not self._same_school(query.university, names, item.get('display_name_acronyms') or []):
                continue
            extra = [name for name in dict.fromkeys(names) if name and normalize(name) not in known]
            return replace(query, university_aliases=[*query.university_aliases, *extra]) if extra else query
        return query

    def _same_school(self, requested: str, names: list[str], acronyms: list[str]) -> bool:
        """The request is the record's acronym, or every distinctive word it has is in one of the record's names."""
        if normalize(requested) in {normalize(name) for name in acronyms}:
            return True
        wanted = set(normalize(requested).split()) - set(self.config.school_stop_words)
        return bool(wanted) and any(wanted <= set(normalize(name).split()) for name in names)
