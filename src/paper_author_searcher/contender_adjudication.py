"""Settle an unconfirmed identity from the competing profiles' own papers.

Without anchors, namesakes at one school can tie on name and school (Xiang Zhou at Harvard: three profiles at 0.87,
and his homepage now answers 403). Their papers were collected anyway as contenders; the requested department then
separates them when exactly one contender's papers name that department and no rival's do.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .config import DEFAULT_COLLECTION, CollectionConfig
from .models import PaperRecord, ProfessorProfile
from .paper_authorship import canonical_author_id
from .precision_guards import DepartmentMatcher, target_affiliations

# Comparing evidence takes two people with some.
MINIMUM_EVIDENCE_PEOPLE = 2


@dataclass(frozen=True)
class ContenderEvidence:
    author_ids: Mapping[str, Sequence[str]]
    mentions: int
    matching: int

    @property
    def share(self) -> float:
        return self.matching / self.mentions if self.mentions else 0.0


class DepartmentAdjudicator:
    def __init__(self, config: CollectionConfig = DEFAULT_COLLECTION, departments: DepartmentMatcher | None = None):
        self.config = config
        self.departments = departments or DepartmentMatcher()

    def evidence(self, profile: ProfessorProfile, records: Iterable[PaperRecord]) -> list[ContenderEvidence]:
        records = list(records)
        result = []
        for author_ids in profile.contender_ids:
            wanted = {
                (source, canonical_author_id(source, value)) for source, ids in author_ids.items() for value in ids
            }
            mentions = matching = 0
            for record in records:
                target = canonical_author_id(record.source, record.source_author_id)
                if (record.source, target) not in wanted:
                    continue
                for author in record.author_details:
                    if canonical_author_id(record.source, author.get('author_id')) != target:
                        continue
                    for affiliation in target_affiliations(record, author):
                        for phrase in set(self.departments.phrases(affiliation)):
                            mentions += 1
                            matching += self.departments.agree(phrase, profile.requested_department or '')
            result.append(ContenderEvidence(author_ids, mentions, matching))
        return result

    def choose(self, profile: ProfessorProfile, records: Iterable[PaperRecord]) -> ContenderEvidence | None:
        """The one contender whose papers name the requested department, when every rival's barely do."""
        if not self.config.contender_department_adjudication or not profile.requested_department:
            return None
        evidence = self.evidence(profile, records)
        if len(evidence) < MINIMUM_EVIDENCE_PEOPLE:
            return None
        winners = [
            item
            for item in evidence
            if item.mentions >= self.config.contender_minimum_evidence
            and item.share >= self.config.contender_minimum_share
        ]
        if len(winners) != 1:
            return None
        winner = winners[0]
        if any(item is not winner and item.share > self.config.contender_rival_share for item in evidence):
            return None
        return winner
