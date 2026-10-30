"""Settle a close contest between same-school namesakes by what their own papers print.

Name and school cannot separate namesakes at one school: Yi Cui and Yi Shun Cui both score 1.0 on name and on
Stanford. Their bylines can. Of Yi Cui's sampled works, hundreds print "Department of Materials Science and
Engineering, Stanford University", against a handful of Yi Shun Cui's. A profile's evidence is therefore the
number of its works whose byline names the requested department at the requested school. The leader must have
enough of them and dominate every rival. A namesake with comparable evidence (two Jing Huangs at Stanford CS with
one such work each) leaves the contest open, and so does thin evidence: a wrong pick attributes someone else's
papers to the person.

Some departments never appear in a byline (Harvard bioengineering signs as the School of Engineering and Applied
Sciences). When no contender has department evidence, works printing the school alone may decide instead
(``school_fallback``, off by default). A senior namesake at the school wins that tier on tenure alone: it chose an
immunologist over Stanford CS student Jing Huang, whose sampled papers rarely print Stanford.

When bylines settle nothing, the contenders' own curated records may: DBLP affiliation notes and ORCID entries
are kept by people, not inferred from papers. The real Yang Liu's DBLP note reads "Tsinghua University,
Department of Computer Science and Technology"; his namesake's reads "Tsinghua University, Institute for AI
Industry Research". Exactly one contender must hold a current position at the school in the requested department
(or, failing any such, in a broader unit related to it), and no rival may have more department bylines once
they count (``minimum_works``).

Each kind of evidence is one layer in ``contest_layers``; ``BylineDisambiguator`` runs them in order and the first
verdict wins.
"""

from collections.abc import Callable, Sequence

from .config import DEFAULT_DISAMBIGUATION, DEFAULT_MATCHING, DisambiguationConfig, MatchingConfig
from .contest_layers import (
    BylineCounter,
    BylineEvidence,
    BylineVerdict,
    Contest,
    ContestLayer,
    CuratedPositions,
    DepartmentBylines,
    SchoolBylines,
)
from .curated_positions import PositionFit, PositionMatcher
from .models import AuthorCandidate, CuratedPosition, ProfessorProfile, ProfessorQuery
from .precision_guards import DepartmentMatcher
from .status import UniversityStatus

__all__ = ['BylineDisambiguator', 'BylineEvidence', 'BylineVerdict']


class BylineDisambiguator:
    def __init__(
        self,
        config: DisambiguationConfig = DEFAULT_DISAMBIGUATION,
        matching: MatchingConfig = DEFAULT_MATCHING,
        departments: DepartmentMatcher | None = None,
        orcid_positions: Callable[[str], list[CuratedPosition]] | None = None,
        layers: Sequence[ContestLayer] | None = None,
    ) -> None:
        self.config = config
        self.matching = matching
        self.departments = departments or DepartmentMatcher()
        self.positions = PositionMatcher(config, matching, self.departments)
        self.counter = BylineCounter(matching, self.departments)
        # Reads an ORCID record's positions (orcid_affiliations.OrcidAffiliations.positions); None reads none.
        self.orcid_positions = orcid_positions
        self.layers = list(layers) if layers is not None else self.default_layers()

    def default_layers(self) -> list[ContestLayer]:
        """Department bylines, then curated positions, then (if enabled) bylines that print the school alone."""
        layers: list[ContestLayer] = [DepartmentBylines(self.config)]
        if self.config.curated_positions:
            layers.append(
                CuratedPositions(self.config, self.matching, self.positions, self.counter, self.orcid_positions)
            )
        if self.config.school_fallback:
            layers.append(SchoolBylines(self.config))
        return layers

    def evidence(self, query: ProfessorQuery, group: Sequence[AuthorCandidate]) -> BylineEvidence:
        return self.counter.evidence(query, group)

    def choose(
        self, query: ProfessorQuery, scored: Sequence[tuple[ProfessorProfile, Sequence[AuthorCandidate]]]
    ) -> BylineVerdict | None:
        """The contender the first deciding layer favours in the close, unanchored contest at the top, if any."""
        if not self.config.enabled or len(scored) < 2:
            return None
        entrants = self._entrants(scored)
        if len(entrants) < 2:
            return None
        bylines = {index: self.evidence(query, group) for index, group in entrants}
        contest = Contest(query, scored, entrants, bylines, {index: item.department for index, item in bylines.items()})
        for layer in self.layers:
            verdict = layer.decide(contest)
            if verdict is not None:
                return verdict
        return None

    def fit(self, position: CuratedPosition, query: ProfessorQuery) -> PositionFit:
        return self.positions.fit(position, query)

    def _entrants(self, scored: Sequence[tuple[ProfessorProfile, Sequence[AuthorCandidate]]]) -> list:
        """Contenders near the leader at the requested school; none if an anchor already speaks for one."""
        leader = scored[0][0].match_score
        contest = [
            (index, group)
            for index, (profile, group) in enumerate(scored)
            if profile.match_score >= leader - self.config.contest_band
            and profile.university_status == UniversityStatus.MATCHED
        ]
        if any(scored[index][0].identity_anchors for index, _ in contest):
            return []
        return contest
