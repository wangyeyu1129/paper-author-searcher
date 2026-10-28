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
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .config import DEFAULT_DISAMBIGUATION, DEFAULT_MATCHING, DisambiguationConfig, MatchingConfig
from .logging_config import log_isolated_failure
from .matching import canonical_orcid, institution_mentioned, institution_similarity, name_similarity
from .models import AuthorCandidate, CuratedPosition, ProfessorProfile, ProfessorQuery
from .precision_guards import DepartmentMatcher
from .status import UniversityStatus

logger = logging.getLogger(__name__)


class PositionFit(IntEnum):
    NONE = 0
    BROADER_UNIT = 1  # a school or faculty whose words relate to the requested department
    DEPARTMENT = 2


class _Abstain(Exception):
    """A rival's curated record could not be read, so no contender can be shown to be the only one."""


@dataclass(frozen=True)
class BylineEvidence:
    department: int
    school: int


@dataclass(frozen=True)
class BylineVerdict:
    """The contender (index into the scored list) the bylines favour, and why."""

    index: int
    note: str
    # Author IDs whose own curated record (a DBLP note) placed the person there: paper ownership may trust them
    # like an ORCID-anchored profile. Empty for byline verdicts and for positions read from ORCID.
    curated_author_ids: dict[str, list[str]] = field(default_factory=dict)


class BylineDisambiguator:
    def __init__(
        self,
        config: DisambiguationConfig = DEFAULT_DISAMBIGUATION,
        matching: MatchingConfig = DEFAULT_MATCHING,
        departments: DepartmentMatcher | None = None,
        orcid_positions: Callable[[str], list[CuratedPosition]] | None = None,
    ) -> None:
        self.config = config
        self.matching = matching
        self.departments = departments or DepartmentMatcher()
        # Reads an ORCID record's positions (orcid_affiliations.OrcidAffiliations.positions); None reads none.
        self.orcid_positions = orcid_positions

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
        departments = {index: item.department for index, item in evidence.items()}
        if any(departments.values()):
            verdict = self._dominant(departments, 'department', query)
            if verdict is not None:
                return verdict
        verdict = self._curated(query, scored, contest, departments)
        if verdict is not None or any(departments.values()) or not self.config.school_fallback:
            return verdict
        schools = {index: item.school for index, item in evidence.items()}
        return self._dominant(schools, 'school', query) if any(schools.values()) else None

    def fit(self, position: CuratedPosition, query: ProfessorQuery) -> PositionFit:
        """How well one curated position places the person at the requested school and department."""
        schools = [school for school in (query.university, *query.university_aliases) if school]
        at_school = any(
            institution_similarity(school, institution, self.matching) >= self.matching.matched_school
            for school in schools
            for institution in position.institutions
        )
        if not position.current or not at_school:
            return PositionFit.NONE
        if not query.department:
            return PositionFit.DEPARTMENT
        # A position naming no unit says nothing about the department: it neither fits nor rules out.
        if any(self.departments.agree(unit, query.department) for unit in position.units):
            return PositionFit.DEPARTMENT
        if any(self._broader_unit(unit, query.department) for unit in position.units):
            return PositionFit.BROADER_UNIT
        return PositionFit.NONE

    def _curated(
        self,
        query: ProfessorQuery,
        scored: Sequence[tuple[ProfessorProfile, Any]],
        contest: list[tuple[int, Any]],
        departments: dict[int, int],
    ) -> BylineVerdict | None:
        if not self.config.curated_positions:
            return None
        reach = self._curated_reach(query, scored, {index for index, _ in contest})
        if reach is None:
            return None
        try:
            positions = self._contest_positions(contest)
        except _Abstain:
            return None
        groups = dict(contest) | dict(reach)
        for index, group in reach:
            positions[index] = [position for candidate in group for position in candidate.positions]
            departments[index] = self.evidence(query, group).department
        fits = {
            index: max((self.fit(position, query) for position in held), default=PositionFit.NONE)
            for index, held in positions.items()
        }
        for level in (PositionFit.DEPARTMENT, PositionFit.BROADER_UNIT):
            holders = [index for index, value in fits.items() if value == level]
            if not holders:
                continue
            if len(holders) > 1:
                return None
            winner = holders[0]
            if self._bylines_favour_rival(winner, departments):
                return None
            unit = query.department if level == PositionFit.DEPARTMENT else 'a broader unit'
            curated: dict[str, list[str]] = {}
            for candidate in groups[winner]:
                if any(self.fit(position, query) == level for position in candidate.positions):
                    curated.setdefault(candidate.source, []).append(candidate.author_id)
            return BylineVerdict(
                winner,
                f'Curated position: the only contender whose own record (DBLP note or ORCID) places it at '
                f'{query.university} in {unit} now',
                curated,
            )
        return None

    def _curated_reach(
        self, query: ProfessorQuery, scored: Sequence[tuple[ProfessorProfile, Any]], contest: set[int]
    ) -> list[tuple[int, Any]] | None:
        """Candidates behind the contest band, within ``curated_band``, whose own record names the department.

        None when one of them is anchored: an anchor outranks any position record.
        """
        leader = scored[0][0].match_score
        reach = []
        for index, (profile, group) in enumerate(scored):
            if index in contest or profile.match_score < leader - self.config.curated_band:
                continue
            if profile.university_status != UniversityStatus.MATCHED or not self._names_agree(query, profile):
                continue
            held = [position for candidate in group for position in candidate.positions]
            if any(self.fit(position, query) == PositionFit.DEPARTMENT for position in held):
                if profile.identity_anchors:
                    return None
                reach.append((index, group))
        return reach

    def _names_agree(self, query: ProfessorQuery, profile: ProfessorProfile) -> bool:
        return any(
            name_similarity(query.name, name, self.matching) >= self.matching.compatible_name
            for name in [profile.name, *profile.name_aliases]
        )

    def _contest_positions(self, contest: list[tuple[int, Any]]) -> dict[int, list[CuratedPosition]]:
        orcids = {index: sorted(self._orcids(group)) for index, group in contest}
        if len({orcid for values in orcids.values() for orcid in values}) > self.config.maximum_orcid_lookups:
            raise _Abstain
        records: dict[str, list[CuratedPosition]] = {}
        positions = {}
        for index, group in contest:
            held = [position for candidate in group for position in candidate.positions]
            for orcid in orcids[index] if self.orcid_positions is not None else ():
                if orcid not in records:
                    records[orcid] = self._read_orcid(orcid)
                held.extend(records[orcid])
            positions[index] = held
        return positions

    @staticmethod
    def _orcids(group: Sequence[AuthorCandidate]) -> set[str]:
        return {orcid for orcid in (canonical_orcid(candidate.orcid) for candidate in group) if orcid}

    def _read_orcid(self, orcid: str) -> list[CuratedPosition]:
        assert self.orcid_positions is not None
        try:
            return self.orcid_positions(orcid)
        except Exception as exc:
            log_isolated_failure(logger, 'ORCID record %s unreadable: %s', orcid, exc)
            raise _Abstain from exc

    def _bylines_favour_rival(self, winner: int, departments: dict[int, int]) -> bool:
        """A rival's bylines name the department often enough to count, and more often than the record holder's."""
        strongest = max((count for index, count in departments.items() if index != winner), default=0)
        return strongest >= self.config.minimum_works and strongest > departments[winner]

    def _broader_unit(self, unit: str, department: str) -> bool:
        """A school or faculty whose distinctive words relate to the department's ("engineering", "bioengineering")."""
        words = unit.lower().split()
        if not any(word in self.config.parent_unit_keywords for word in words):
            return False
        length = self.config.related_word_length
        unit_words = [word for word in self.departments.words(unit) if len(word) >= length]
        wanted = [word for word in self.departments.words(department) if len(word) >= length]
        return any(left in right or right in left for left in unit_words for right in wanted)

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
