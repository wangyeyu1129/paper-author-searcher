"""Whether a position from a person's own curated record (a DBLP note, an ORCID entry) places them at the requested
school and department.

Shared by everything that reads such records: byline disambiguation settles namesake contests with it, and DBLP
profile discovery picks the one profile at the school and department to read a homepage and ORCID from.
"""

from enum import IntEnum

from .config import DEFAULT_DISAMBIGUATION, DEFAULT_MATCHING, DisambiguationConfig, MatchingConfig
from .matching import institution_similarity
from .models import CuratedPosition, ProfessorQuery
from .precision_guards import DepartmentMatcher


class PositionFit(IntEnum):
    NONE = 0
    BROADER_UNIT = 1  # a school or faculty whose words relate to the requested department
    DEPARTMENT = 2


class PositionMatcher:
    def __init__(
        self,
        config: DisambiguationConfig = DEFAULT_DISAMBIGUATION,
        matching: MatchingConfig = DEFAULT_MATCHING,
        departments: DepartmentMatcher | None = None,
    ) -> None:
        self.config = config
        self.matching = matching
        self.departments = departments or DepartmentMatcher()

    def fit(self, position: CuratedPosition, query: ProfessorQuery) -> PositionFit:
        """How well one curated position places the person at the requested school and department now."""
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

    def best(self, positions: list[CuratedPosition], query: ProfessorQuery) -> PositionFit:
        return max((self.fit(position, query) for position in positions), default=PositionFit.NONE)

    def _broader_unit(self, unit: str, department: str) -> bool:
        """A school or faculty whose distinctive words relate to the department's ("engineering", "bioengineering")."""
        words = unit.lower().split()
        if not any(word in self.config.parent_unit_keywords for word in words):
            return False
        length = self.config.related_word_length
        unit_words = [word for word in self.departments.words(unit) if len(word) >= length]
        wanted = [word for word in self.departments.words(department) if len(word) >= length]
        return any(left in right or right in left for left in unit_words for right in wanted)
