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
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .config import DEFAULT_DISAMBIGUATION, DEFAULT_MATCHING, DisambiguationConfig, MatchingConfig
from .matching import institution_mentioned
from .models import AuthorCandidate, ProfessorProfile, ProfessorQuery
from .precision_guards import DepartmentMatcher
from .status import UniversityStatus


@dataclass(frozen=True)
class BylineEvidence:
    department: int
    school: int


@dataclass(frozen=True)
class BylineVerdict:
    """The contender (index into the scored list) the bylines favour, and why."""

    index: int
    note: str


class BylineDisambiguator:
    def __init__(
        self,
        config: DisambiguationConfig = DEFAULT_DISAMBIGUATION,
        matching: MatchingConfig = DEFAULT_MATCHING,
        departments: DepartmentMatcher | None = None,
    ) -> None:
        self.config = config
        self.matching = matching
        self.departments = departments or DepartmentMatcher()

    def evidence(self, query: ProfessorQuery, group: Sequence[AuthorCandidate]) -> BylineEvidence:
        schools = [query.university, *query.university_aliases]
        department = school = 0
        for candidate in group:
            for byline in candidate.bylines:
                at_school = [line for line in byline if self._names_school(line, schools)]
                if not at_school:
                    continue
                school += 1
                if query.department and any(self._names_department(line, query.department) for line in at_school):
                    department += 1
        return BylineEvidence(department, school)

    def choose(
        self, query: ProfessorQuery, scored: Sequence[tuple[ProfessorProfile, Sequence[AuthorCandidate]]]
    ) -> BylineVerdict | None:
        """The contender whose bylines dominate the close, unanchored contest at the top, if one does."""
        if not self.config.enabled or len(scored) < 2:
            return None
        contest = self._contest(scored)
        if len(contest) < 2:
            return None
        evidence = {index: self.evidence(query, group) for index, group in contest}
        tiers = [('department', lambda item: item.department)]
        if self.config.school_fallback:
            tiers.append(('school', lambda item: item.school))
        for tier, count in tiers:
            counts = {index: count(item) for index, item in evidence.items()}
            if not any(counts.values()):
                continue
            return self._dominant(counts, tier, query)
        return None

    def _contest(self, scored: Sequence[tuple[ProfessorProfile, Any]]) -> list[tuple[int, Any]]:
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

    def _dominant(self, counts: dict[int, int], tier: str, query: ProfessorQuery) -> BylineVerdict | None:
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        (winner, best), (_, rival) = ranked[0], ranked[1]
        if best < self.config.minimum_works or best < self.config.dominance_ratio * rival:
            return None
        place = f'{query.department}, {query.university}' if tier == 'department' else query.university
        return BylineVerdict(
            winner,
            f'Byline disambiguation: {best} sampled works print {place} on this profile, '
            f'at most {rival} on any rival profile',
        )

    def _names_school(self, line: str, schools: Sequence[str]) -> bool:
        return any(institution_mentioned(school, line, self.matching) for school in schools if school)

    def _names_department(self, line: str, department: str) -> bool:
        return any(self.departments.agree(phrase, department) for phrase in self.departments.phrases(line))
