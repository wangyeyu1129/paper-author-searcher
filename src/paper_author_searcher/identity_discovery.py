"""Discover identity evidence the caller did not supply: the person's ORCID and their own publication pages.

Discovery runs as a chain of ``DiscoveryStep`` objects over one shared ``Discovery`` result, so later steps see what
earlier ones found (the ORCID registry's researcher URLs need the ORCID a registry search found). Discovered
evidence is held to a stricter standard than supplied evidence:

- an ORCID is adopted only when the registry returns exactly one person with a compatible name at the requested
  institution;
- a page is used only when ``PageRanker`` scores it at least ``DiscoveryConfig.minimum_page_score``, and only the
  citations on it that name the person count (see identity_anchors).
"""

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from .config import DEFAULT_DISCOVERY, DEFAULT_MATCHING, DEFAULT_SOURCES, DiscoveryConfig, MatchingConfig, SourceConfig
from .evaluation_matching import bibliographic_name_agrees
from .http import SourceError
from .logging_config import log_isolated_failure
from .matching import (
    best_similarity,
    canonical_orcid,
    institution_similarity,
    name_similarity,
    normalize,
    split_name,
)
from .orcid_affiliations import ORCID_JSON, OrcidAffiliations
from .web_search import SearchHit, WebSearch

logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class DiscoveredPage:
    url: str
    score: float
    provenance: str


@dataclass
class Discovery:
    """What discovery found for one query; ``to_dict`` is what a run records (and a replay restores)."""

    orcid: str | None = None
    pages: list[DiscoveredPage] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_page(self, page: DiscoveredPage) -> None:
        known = {item.url for item in self.pages}
        if page.url not in known:
            self.pages.append(page)

    def best_pages(self, limit: int) -> list[DiscoveredPage]:
        return sorted(self.pages, key=lambda page: (-page.score, page.url))[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            'orcid': self.orcid,
            'pages': [{'url': page.url, 'score': page.score, 'provenance': page.provenance} for page in self.pages],
            'evidence': list(self.evidence),
        }


class DiscoveryStep(Protocol):
    name: str

    def discover(self, query: Any, found: Discovery) -> None: ...


class IdentityDiscovery:
    """Runs each step in order; a failing step is recorded and skipped, never fatal."""

    def __init__(self, steps: Sequence[DiscoveryStep]) -> None:
        self.steps = list(steps)

    def discover(self, query: Any) -> Discovery:
        found = Discovery()
        for step in self.steps:
            try:
                step.discover(query, found)
            except (SourceError, ValueError, KeyError, TypeError) as exc:
                log_isolated_failure(logger, 'Discovery step %s failed: %s', step.name, exc)
                found.errors.append(f'{step.name}: {exc}')
        return found


class NoDiscovery:
    def discover(self, query: Any) -> Discovery:
        return Discovery()


def compatible_name(wanted: str, found: str, matching: MatchingConfig) -> bool:
    return bool(found) and (
        name_similarity(wanted, found, matching) >= matching.compatible_name or bibliographic_name_agrees(wanted, found)
    )


def _quoted(value: str) -> str:
    return '"' + value.replace('"', ' ').strip() + '"'


class OrcidRegistrySearch:
    """The ORCID public registry's expanded search, by name and institution (and by email when one is given)."""

    name = 'orcid_search'

    def __init__(
        self,
        client: Any,
        source_config: SourceConfig = DEFAULT_SOURCES,
        config: DiscoveryConfig = DEFAULT_DISCOVERY,
        matching: MatchingConfig = DEFAULT_MATCHING,
        department_agrees: Callable[[str, str], bool] | None = None,
        name_is_common: Callable[[str], bool] | None = None,
        current_year: int | None = None,
    ) -> None:
        self.client, self.config, self.matching = client, config, matching
        self.records = OrcidAffiliations(client, source_config, config.orcid_affiliation_sections, current_year)
        self.url = self.records.base_url + '/expanded-search/'
        # (printed department, requested department) -> same unit; without one, departments are not compared.
        self.department_agrees = department_agrees
        # Without namesakes to tell apart, a department worded differently is no reason to doubt the record.
        self.name_is_common = name_is_common

    def discover(self, query: Any, found: Discovery) -> None:
        if query.orcid or found.orcid:
            return
        for search in self.queries(query):
            matches = self.matches(query, self._results(search))
            if len(matches) == 1:
                reason = self.unconfirmed(query, matches[0])
                if reason:
                    found.evidence.append(f'ORCID {matches[0]} not adopted: {reason}')
                    return
                found.orcid = matches[0]
                found.evidence.append(f'ORCID {matches[0]}: the only registry match for {search}')
                return
            if matches:
                found.evidence.append(f'ORCID not adopted: {len(matches)} registry matches for {search}')
                return

    def queries(self, query: Any) -> list[str]:
        given, family = split_name(query.name)
        by_name = f'family-name:{_quoted(family)}' + (f' AND given-names:{_quoted(given)}' if given else '')
        searches = [f'email:{_quoted(query.email)}'] if getattr(query, 'email', None) else []
        universities = [query.university, *getattr(query, 'university_aliases', [])]
        searches += [f'{by_name} AND affiliation-org-name:{_quoted(school)}' for school in universities if school]
        return searches

    def matches(self, query: Any, results: Iterable[dict]) -> list[str]:
        """Distinct ORCIDs whose name is the person's and whose institutions include the requested one."""
        universities = [query.university, *getattr(query, 'university_aliases', [])]
        orcids = []
        for result in results:
            names = [
                ' '.join(part for part in (result.get('given-names'), result.get('family-names')) if part),
                result.get('credit-name') or '',
                *(result.get('other-name') or []),
            ]
            institutions = result.get('institution-name') or []
            school = best_similarity(
                universities, institutions, lambda a, b: institution_similarity(a, b, self.matching)
            )
            if school >= self.matching.matched_school and any(
                compatible_name(query.name, name, self.matching) for name in names
            ):
                orcids.append(canonical_orcid(result.get('orcid-id')))
        return sorted({orcid for orcid in orcids if orcid})

    def unconfirmed(self, query: Any, orcid: str) -> str | None:
        """Why the record's own affiliations do not confirm the person (None: they do, or no check is set)."""
        if not self.config.orcid_require_affiliation:
            return None
        universities = [query.university, *getattr(query, 'university_aliases', [])]
        departments = [
            affiliation.get('department-name') or ''
            for affiliation in self.affiliations(orcid)
            if self._current(affiliation)
            and best_similarity(
                universities,
                [(affiliation.get('organization') or {}).get('name') or ''],
                lambda a, b: institution_similarity(a, b, self.matching),
            )
            >= self.matching.matched_school
        ]
        if not departments:
            return 'no current affiliation at the requested institution'
        wanted = getattr(query, 'department', None)
        if not wanted or not self.config.orcid_require_department or self.department_agrees is None:
            return None
        if self.name_is_common is not None and not self.name_is_common(query.name):
            return None
        if any(department and self.department_agrees(department, wanted) for department in departments):
            return None
        return f'no current affiliation in {wanted}'

    def affiliations(self, orcid: str) -> list[dict]:
        return self.records.summaries(orcid)

    def _current(self, affiliation: dict) -> bool:
        return self.records.current(affiliation)

    def _results(self, search: str) -> list[dict]:
        data = self.client.get(self.url, {'q': search, 'rows': self.config.orcid_search_rows}, ORCID_JSON)
        return list((data or {}).get('expanded-result') or [])


class PageRanker:
    """Scores a candidate page as the person's own: each feature is 0..1 and the score is their weighted sum.

    Features: the person's name in the page title (all tokens 1, family name 0.5) and in the URL, the requested
    institution in the title or snippet (token containment, or the host is the institution's email domain), a
    publication-list or CV marker, and an academic host. Excluded hosts (aggregators, social networks) score 0.
    """

    def __init__(
        self, config: DiscoveryConfig = DEFAULT_DISCOVERY, matching: MatchingConfig = DEFAULT_MATCHING
    ) -> None:
        self.config, self.matching = config, matching
        self.stop_words = set(matching.school_stop_words)

    def excluded(self, url: str) -> bool:
        host = urlsplit(url).hostname or ''
        return not host or any(host == item or host.endswith('.' + item) for item in self.config.excluded_hosts)

    def features(self, query: Any, hit: SearchHit) -> dict[str, float]:
        parts = urlsplit(hit.url)
        host = (parts.hostname or '').lower()
        url_text = normalize(f'{host} {parts.path}'.replace('-', ' ').replace('_', ' ').replace('.', ' '))
        title = set(normalize(hit.title).split())
        tokens = normalize(query.name).split()
        family = tokens[-1] if tokens else ''
        return {
            'name_in_title': 1.0 if tokens and set(tokens) <= title else 0.5 if family in title else 0.0,
            'name_in_url': sum(token in url_text for token in tokens) / len(tokens) if tokens else 0.0,
            'institution': self._institution(query, host, f'{hit.title} {hit.snippet}'),
            'publication_page': float(
                any(marker in normalize(f'{parts.path} {hit.title}') for marker in self.config.publication_markers)
            ),
            'academic_host': float(any(host.endswith(suffix) for suffix in self.config.academic_host_suffixes)),
        }

    def score(self, query: Any, hit: SearchHit, bonus: float = 0.0) -> float:
        if self.excluded(hit.url):
            return 0.0
        features = self.features(query, hit)
        weights = {name: getattr(self.config, 'weight_' + name) for name in features}
        return round(sum(weights[name] * value for name, value in features.items()) + bonus, 3)

    def _institution(self, query: Any, host: str, text: str) -> float:
        email = getattr(query, 'email', None) or ''
        domain = email.rpartition('@')[2].lower()
        if domain and (host == domain or host.endswith('.' + domain)):
            return 1.0
        words = set(normalize(text).split())
        containment = [
            len(wanted & words) / len(wanted)
            for school in [query.university, *getattr(query, 'university_aliases', [])]
            if (wanted := set(normalize(school).split()) - self.stop_words)
        ]
        return max(containment, default=0.0)


class OrcidResearcherUrls:
    """The personal websites the person lists on their own ORCID record (supplied or discovered)."""

    name = 'orcid_researcher_urls'

    def __init__(self, client: Any, ranker: PageRanker, source_config: SourceConfig = DEFAULT_SOURCES) -> None:
        self.client, self.ranker = client, ranker
        self.url = source_config.orcid_api_url.rstrip('/')

    def discover(self, query: Any, found: Discovery) -> None:
        orcid = canonical_orcid(query.orcid) if query.orcid else found.orcid
        if not orcid:
            return
        data = self.client.get(f'{self.url}/{orcid}/researcher-urls', None, ORCID_JSON)
        for item in (data or {}).get('researcher-url') or []:
            url = ((item.get('url') or {}).get('value') or '').strip()
            if not url or self.ranker.excluded(url):
                continue
            hit = SearchHit(url, item.get('url-name') or '')
            score = self.ranker.score(query, hit, self.ranker.config.weight_orcid_listed)
            found.add_page(DiscoveredPage(url, score, f'ORCID {orcid} researcher URL'))


class WebPageSearch:
    """Searches the web for the person's pages and keeps the hits ``PageRanker`` scores highest."""

    name = 'web_search'

    def __init__(self, search: WebSearch, ranker: PageRanker, config: DiscoveryConfig = DEFAULT_DISCOVERY) -> None:
        self.search, self.ranker, self.config = search, ranker, config

    def discover(self, query: Any, found: Discovery) -> None:
        values = {'name': query.name, 'university': query.university, 'department': query.department or ''}
        for template in self.config.web_queries:
            text = ' '.join(template.format(**values).split())
            for hit in self.search.search(text, self.config.web_search_results):
                score = self.ranker.score(query, hit)
                if score >= self.config.minimum_page_score:
                    found.add_page(DiscoveredPage(hit.url, score, f'{self.search.name} search: {text}'))
