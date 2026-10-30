"""The layers that settle a contest between same-school namesakes, one class per kind of evidence.

Each layer looks at a ``Contest`` and either names the winner (a ``BylineVerdict``) or passes (None). The
disambiguator runs them as a chain of responsibility: the first verdict wins, and a new kind of evidence is a new
``ContestLayer`` added to the chain, with no change to the existing ones. Layers may add what they learn to the
contest (``CuratedPositions`` counts the bylines of contenders it brings in), and later layers see it.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import DisambiguationConfig, MatchingConfig
from .curated_positions import PositionFit, PositionMatcher
from .logging_config import log_isolated_failure
from .matching import canonical_orcid, institution_mentioned, name_similarity
from .models import AuthorCandidate, CuratedPosition, ProfessorProfile, ProfessorQuery
from .precision_guards import DepartmentMatcher
from .status import UniversityStatus

logger = logging.getLogger(__name__)

Scored = Sequence[tuple[ProfessorProfile, Sequence[AuthorCandidate]]]


@dataclass(frozen=True)
class BylineEvidence:
    department: int
    school: int


@dataclass(frozen=True)
class BylineVerdict:
    """The contender (index into the scored list) the evidence favours, and why."""

    index: int
    note: str
    # Author IDs whose own curated record (a DBLP note) placed the person there: paper ownership may trust them
    # like an ORCID-anchored profile. Empty for byline verdicts and for positions read from ORCID.
    curated_author_ids: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Contest:
    """A close, unanchored contest at the requested school, and the byline evidence gathered about it."""

    query: ProfessorQuery
    scored: Scored
    # (index into scored, candidate group) of every contender within the contest band.
    entrants: list[tuple[int, Any]]
    bylines: dict[int, BylineEvidence]
    # Department byline counts per contender; a layer that brings in more contenders adds theirs.
    departments: dict[int, int]


class ContestLayer(Protocol):
    def decide(self, contest: Contest) -> BylineVerdict | None: ...


class BylineCounter:
    """Counts a candidate group's sampled works whose byline prints the requested school (and department)."""

    def __init__(self, matching: MatchingConfig, departments: DepartmentMatcher) -> None:
        self.matching = matching
        self.departments = departments

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

    def _names_school(self, line: str, schools: Sequence[str]) -> bool:
        return any(institution_mentioned(school, line, self.matching) for school in schools if school)

    def _names_department(self, line: str, department: str) -> bool:
        return any(self.departments.agree(phrase, department) for phrase in self.departments.phrases(line))


def dominant(
    counts: dict[int, int], tier: str, query: ProfessorQuery, config: DisambiguationConfig
) -> BylineVerdict | None:
    """The leader, if it has ``minimum_works`` and ``dominance_ratio`` times the strongest rival's count."""
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    (winner, best), (_, rival) = ranked[0], ranked[1]
    if best < config.minimum_works or best < config.dominance_ratio * rival:
        return None
    place = f'{query.department}, {query.university}' if tier == 'department' else query.university
    return BylineVerdict(
        winner,
        f'Byline disambiguation: {best} sampled works print {place} on this profile, '
        f'at most {rival} on any rival profile',
    )


class DepartmentBylines:
    """The contender whose works print the requested department at the school far more often than any rival's."""

    def __init__(self, config: DisambiguationConfig) -> None:
        self.config = config

    def decide(self, contest: Contest) -> BylineVerdict | None:
        if not any(contest.departments.values()):
            return None
        return dominant(contest.departments, 'department', contest.query, self.config)


class _Abstain(Exception):
    """A rival's curated record could not be read, so no contender can be shown to be the only one."""


class CuratedPositions:
    """The only contender whose own curated record (DBLP note, ORCID position) places it in the department now.

    Contenders just behind the contest band (``curated_band``) whose own record names the department join the
    contest here. Exactly one contender may hold such a position (or, failing any, one in a broader unit), and no
    rival may have more department bylines once they count (``minimum_works``).
    """

    def __init__(
        self,
        config: DisambiguationConfig,
        matching: MatchingConfig,
        positions: PositionMatcher,
        counter: BylineCounter,
        orcid_positions: Callable[[str], list[CuratedPosition]] | None = None,
    ) -> None:
        self.config = config
        self.matching = matching
        self.positions = positions
        self.counter = counter
        # Reads an ORCID record's positions (orcid_affiliations.OrcidAffiliations.positions); None reads none.
        self.orcid_positions = orcid_positions

    def decide(self, contest: Contest) -> BylineVerdict | None:
        query, departments = contest.query, contest.departments
        reach = self._reach(query, contest.scored, {index for index, _ in contest.entrants})
        if reach is None:
            return None
        try:
            positions = self._contest_positions(contest.entrants)
        except _Abstain:
            return None
        groups = dict(contest.entrants) | dict(reach)
        for index, group in reach:
            positions[index] = [position for candidate in group for position in candidate.positions]
            departments[index] = self.counter.evidence(query, group).department
        fits = {
            index: max((self.positions.fit(position, query) for position in held), default=PositionFit.NONE)
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
                if any(self.positions.fit(position, query) == level for position in candidate.positions):
                    curated.setdefault(candidate.source, []).append(candidate.author_id)
            return BylineVerdict(
                winner,
                f'Curated position: the only contender whose own record (DBLP note or ORCID) places it at '
                f'{query.university} in {unit} now',
                curated,
            )
        return None

    def _reach(self, query: ProfessorQuery, scored: Scored, contest: set[int]) -> list[tuple[int, Any]] | None:
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
            if any(self.positions.fit(position, query) == PositionFit.DEPARTMENT for position in held):
                if profile.identity_anchors:
                    return None
                reach.append((index, group))
        return reach

    def _names_agree(self, query: ProfessorQuery, profile: ProfessorProfile) -> bool:
        return any(
            name_similarity(query.name, name, self.matching) >= self.matching.compatible_name
            for name in [profile.name, *profile.name_aliases]
        )

    def _contest_positions(self, entrants: list[tuple[int, Any]]) -> dict[int, list[CuratedPosition]]:
        orcids = {index: sorted(self._orcids(group)) for index, group in entrants}
        if len({orcid for values in orcids.values() for orcid in values}) > self.config.maximum_orcid_lookups:
            raise _Abstain
        records: dict[str, list[CuratedPosition]] = {}
        positions = {}
        for index, group in entrants:
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


class SchoolBylines:
    """When no contender's works print the department, the one whose works print the school far more often.

    Off by default (``school_fallback``): a senior namesake wins this tier on tenure alone.
    """

    def __init__(self, config: DisambiguationConfig) -> None:
        self.config = config

    def decide(self, contest: Contest) -> BylineVerdict | None:
        if any(contest.departments.values()):
            return None
        schools = {index: item.school for index, item in contest.bylines.items()}
        return dominant(schools, 'school', contest.query, self.config) if any(schools.values()) else None
