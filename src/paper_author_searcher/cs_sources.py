"""Computer-science sources beyond the author-ID databases.

CS papers appear first (often only) on arXiv and in OpenReview, and OpenAlex often lacks their author affiliations.

- ``ArxivAuthorSearch`` collects the person's recent preprints by name. Every namesake's preprints come back too,
  so the records carry no evidence of their own: ownership verifies them only through recurring coauthors, a
  paper-level affiliation or the person's own list, and the precision guard applies to common names as usual.
- ``OpenReviewProfiles`` finds the person's OpenReview profile (maintained by its owner) by name and the requested
  institution in its history, and adds the profile's papers to the person's own list. OpenReview refuses guests, so
  this needs an account token; without one it contributes nothing.

DBLP is not used: its API answers automated clients with an anti-bot page, and only its official data dumps may be
used instead.
"""

import logging
from datetime import date
from typing import Any

from .config import DEFAULT_CS_SOURCES, DEFAULT_MATCHING, CSSourcesConfig, MatchingConfig
from .listed_works import ATOM, AuthorMatcher, arxiv_entry_record
from .logging_config import log_isolated_failure
from .matching import best_similarity, institution_similarity, normalize, work_keys
from .models import PaperSourceResult, ProfessorProfile
from .profile_links import ProfileLinkParser

logger = logging.getLogger(__name__)

OPENSEARCH = '{http://a9.com/-/spec/opensearch/1.1/}'


class ArxivAuthorSearch:
    name = 'arxiv'

    def __init__(
        self,
        client: Any,
        base_url: str,
        config: CSSourcesConfig = DEFAULT_CS_SOURCES,
        matching: MatchingConfig = DEFAULT_MATCHING,
        current_year: int | None = None,
    ) -> None:
        self.client, self.base_url, self.config = client, base_url, config
        self.authors = AuthorMatcher(matching)
        self.current_year = current_year or date.today().year

    def author_key(self, profile: ProfessorProfile) -> str:
        return 'name:' + normalize(profile.requested_name or profile.name)

    def query(self, profile: ProfessorProfile) -> str:
        start = self.current_year - self.config.arxiv_recent_years + 1
        name = (profile.requested_name or profile.name).replace('"', ' ')
        return f'au:"{name}" AND submittedDate:[{start}01010000 TO {self.current_year}12312359]'

    def collect(self, profile: ProfessorProfile) -> PaperSourceResult:
        key = self.author_key(profile)
        result = PaperSourceResult(source=self.name, author_id=key)
        try:
            offset = 0
            while offset < self.config.arxiv_maximum_results:
                feed = self.client.get(
                    self.base_url,
                    params={
                        'search_query': self.query(profile),
                        'start': offset,
                        'max_results': min(self.config.arxiv_page_size, self.config.arxiv_maximum_results - offset),
                        'sortBy': 'submittedDate',
                    },
                    xml=True,
                )
                entries = feed.findall(ATOM + 'entry')
                result.pages_fetched += 1
                for entry in entries:
                    result.records_received += 1
                    record = arxiv_entry_record(
                        entry,
                        lambda details: self.authors.single(profile, ((d['author_id'], [d['name']]) for d in details)),
                        key,
                    )
                    if record is not None:
                        record.attribution_evidence.append('Found by arXiv author-name search; no author ID')
                        result.papers.append(record)
                total = int(feed.findtext(OPENSEARCH + 'totalResults') or 0)
                offset += len(entries)
                if not entries or offset >= total:
                    result.pagination_complete = True
                    break
            result.complete = result.pagination_complete
        except Exception as exc:
            log_isolated_failure(logger, 'arXiv author search failed for %s: %s', key, exc)
            result.error = str(exc)
        return result


class OpenReviewProfiles:
    name = 'openreview'

    def __init__(
        self,
        client: Any,
        token: str | None,
        config: CSSourcesConfig = DEFAULT_CS_SOURCES,
        matching: MatchingConfig = DEFAULT_MATCHING,
        profile_links: ProfileLinkParser | None = None,
    ) -> None:
        self.client, self.token, self.config, self.matching = client, token, config, matching
        self.authors = AuthorMatcher(matching)
        self.profile_links = profile_links or ProfileLinkParser()

    def works(self, query: Any) -> set[str]:
        if not self.token or not self.config.openreview:
            return set()
        profile_id = self.profile_id(query)
        if profile_id is None:
            return set()
        notes = self._get('/notes', {'content.authorids': profile_id, 'limit': self.config.openreview_maximum_notes})
        keys: set[str] = set()
        for note in notes.get('notes') or []:
            content = note.get('content') or {}
            title = _value(content.get('title'))
            links = ' '.join(str(_value(content.get(field)) or '') for field in ('pdf', 'html'))
            arxiv = [part.split('/')[0] for part in links.split('arxiv.org/abs/')[1:]]
            keys |= work_keys([], arxiv, [title] if title else [])
        return keys

    def profile_id(self, query: Any) -> str | None:
        """The caller's OpenReview link, else the one profile with a compatible name and the requested institution
        in its history."""
        links = self.profile_links.parse_all(getattr(query, 'links', list)())
        supplied = next((link.identifier for link in links if link.source == self.name), None)
        if supplied:
            return supplied
        data = self._get('/profiles/search', {'fullname': query.name})
        universities = [query.university, *getattr(query, 'university_aliases', [])]
        matches = []
        for profile in data.get('profiles') or []:
            content = profile.get('content') or {}
            names = [item.get('fullname') or '' for item in content.get('names') or []]
            institutions = [(item.get('institution') or {}).get('name') or '' for item in content.get('history') or []]
            school = best_similarity(
                universities, institutions, lambda a, b: institution_similarity(a, b, self.matching)
            )
            if school >= self.matching.matched_school and self.authors.compatible_names([query.name], names):
                matches.append(profile.get('id'))
        return matches[0] if len(set(matches)) == 1 else None

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        try:
            return self.client.get(
                self.config.openreview_url.rstrip('/') + path, params, {'Authorization': 'Bearer ' + str(self.token)}
            )
        except Exception as exc:
            log_isolated_failure(logger, 'OpenReview %s failed: %s', path, exc)
            return {}


def _value(field: Any) -> Any:
    """OpenReview API v2 wraps note fields as {'value': ...}."""
    return field.get('value') if isinstance(field, dict) else field
