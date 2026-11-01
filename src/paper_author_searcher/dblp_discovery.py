"""Discover a person's homepage and ORCID from their own DBLP record, without a web search.

DBLP person records are curated: an affiliation note ("Tsinghua University, Department of Computer Science and
Technology") and links to the person's homepage and ORCID. With only a name, school and department, the one
name-compatible DBLP profile whose current note names that school and department is the person; its homepage then
gives the publication list a caller would otherwise have to supply, and its ORCID the precedence anchor.

Two such profiles, or none, give nothing: a wrong homepage would claim another person's papers.
"""

import logging
from typing import Any, Protocol

from .config import DEFAULT_MATCHING, MatchingConfig
from .curated_positions import PositionFit, PositionMatcher
from .dblp_format import base_name
from .identity_discovery import DiscoveredPage, Discovery, PageRanker, compatible_name
from .matching import canonical_orcid
from .models import AuthorCandidate
from .web_search import SearchHit

logger = logging.getLogger(__name__)


class AuthorSearch(Protocol):
    def search(self, query: Any) -> list[AuthorCandidate]: ...


class DblpProfileDiscovery:
    name = 'dblp_profiles'

    def __init__(
        self,
        dblp: AuthorSearch,
        positions: PositionMatcher,
        ranker: PageRanker,
        matching: MatchingConfig = DEFAULT_MATCHING,
    ) -> None:
        self.dblp, self.positions, self.ranker, self.matching = dblp, positions, ranker, matching

    def discover(self, query: Any, found: Discovery) -> None:
        if not query.department:
            return
        holders = [
            candidate
            for candidate in self.dblp.search(query)
            if self._names_agree(query, candidate)
            and self.positions.best(candidate.positions, query) == PositionFit.DEPARTMENT
        ]
        if len(holders) != 1:
            if holders:
                found.evidence.append(
                    f'DBLP profiles not used: {len(holders)} place {query.name} at {query.university}, '
                    f'{query.department}'
                )
            return
        profile = holders[0]
        reason = f'DBLP {profile.author_id}: its own note places the person at {query.university}, {query.department}'
        found.evidence.append(reason)
        if not query.orcid and not found.orcid and profile.orcid:
            found.orcid = canonical_orcid(profile.orcid)
            found.evidence.append(f'ORCID {found.orcid} from DBLP {profile.author_id}')
        for url in profile.links:
            if self.ranker.excluded(url):
                continue
            score = self.ranker.score(query, SearchHit(url, ''), self.ranker.config.weight_dblp_listed)
            found.add_page(DiscoveredPage(url, score, f'DBLP {profile.author_id} homepage', vouched=True))

    def _names_agree(self, query: Any, candidate: AuthorCandidate) -> bool:
        names = [candidate.name, *candidate.aliases]
        return any(compatible_name(query.name, base_name(name), self.matching) for name in names)
