"""Merge a person's split author profiles at collection time.

Bibliographic databases split one researcher into several author IDs (OpenAlex keeps Huan Zhang's UIUC work in
several ORCID-less fragments, while his ORCID sits on a namesake cluster). The resolver can only link fragments from
the evidence of a few top works; after collection the person's own verified papers give a far richer coauthor
network. ``FragmentMerger`` scores each remaining same-name fragment against it:

- shared coauthors are weighted by normalized inverse fragment frequency, ``ln((N + 1) / df) / ln((N + 1) / 2)``
  over the person and N fragments: a coauthor only the person and one fragment share weighs 1, one who appears in
  every namesake's fragment nearly 0, so the score counts distinctive shared coauthors whatever N is;
- the fragment must also share an institution with the person (when configured) and carry no other ORCID;
- an accepted fragment's coauthors join the network and the remaining fragments are scored again until nothing
  changes (a closure over the fragment graph, so chains of fragments that only link through each other merge).

The collector pages each candidate fragment once (``Fragment.with_papers`` adds its own papers' evidence) and keeps
the papers of accepted fragments only; they are verified like any other: merging widens what is collected, never
what is verified.
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .config import DEFAULT_FRAGMENTS, DEFAULT_MATCHING, FragmentConfig, MatchingConfig
from .matching import best_similarity, canonical_orcid, institution_similarity, name_similarity, normalize
from .models import AuthorCandidate, PaperRecord, ProfessorProfile
from .paper_authorship import canonical_author_id


@dataclass(frozen=True)
class Fragment:
    source: str
    author_id: str
    coauthors: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    affiliations: tuple[str, ...] = ()
    orcid: str | None = None

    @classmethod
    def from_candidate(cls, candidate: AuthorCandidate) -> 'Fragment':
        own = {normalize(name) for name in [candidate.name, *candidate.aliases]}
        return cls(
            source=candidate.source,
            author_id=str(candidate.author_id),
            coauthors=tuple(sorted({normalize(name) for name in candidate.coauthors} - own - {''})),
            topics=tuple(sorted({normalize(topic) for topic in candidate.topics} - {''})),
            affiliations=tuple(sorted(set(candidate.affiliations) | set(candidate.current_affiliations))),
            orcid=canonical_orcid(candidate.orcid) if candidate.orcid else None,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'Fragment':
        return cls(
            source=str(data['source']),
            author_id=str(data['author_id']),
            coauthors=tuple(data.get('coauthors') or ()),
            topics=tuple(data.get('topics') or ()),
            affiliations=tuple(data.get('affiliations') or ()),
            orcid=data.get('orcid'),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_papers(self, records: Iterable[PaperRecord], own_names: Iterable[str]) -> 'Fragment':
        """This fragment with the evidence of its own papers: their coauthors, and the institutions its author ID
        carries on them. The resolver enriches only a few candidates, so most fragments arrive without either."""
        own = {normalize(name) for name in own_names}
        coauthors, institutions = set(self.coauthors), set(self.affiliations)
        for record in records:
            coauthors |= {normalize(name) for name in record.authors} - own - {''}
            for detail in record.author_details:
                if canonical_author_id(self.source, detail.get('author_id')) == self.author_id:
                    institutions |= {
                        item['display_name'] for item in detail.get('institutions') or [] if item.get('display_name')
                    }
        return replace(self, coauthors=tuple(sorted(coauthors)), affiliations=tuple(sorted(institutions)))


@dataclass
class FragmentDecision:
    fragment: Fragment
    accepted: bool
    score: float = 0.0
    shared_coauthors: list[str] = field(default_factory=list)
    reason: str = ''

    def evidence(self) -> str:
        names = ', '.join(self.shared_coauthors[:5])
        return (
            f'Split profile {self.fragment.source}:{self.fragment.author_id} merged: '
            f'{len(self.shared_coauthors)} shared coauthors (weighted {self.score:.2f}: {names}); {self.reason}'
        )


def split_fragments(
    profile: ProfessorProfile,
    others: Iterable[Sequence[AuthorCandidate]],
    config: FragmentConfig = DEFAULT_FRAGMENTS,
    matching: MatchingConfig = DEFAULT_MATCHING,
) -> list[Fragment]:
    """Fragments the collector may merge: other candidates from the profile's merge sources whose name is the
    requested one and whose ORCID, if any, is not another person's. Best-matching names first."""
    wanted = profile.requested_name or profile.name
    orcid = canonical_orcid(profile.orcid) if profile.orcid else None
    scored = []
    for group in others:
        for candidate in group:
            if candidate.source not in config.sources:
                continue
            similarity = best_similarity(
                [wanted], [candidate.name, *candidate.aliases], lambda a, b: name_similarity(a, b, matching)
            )
            conflict = orcid and candidate.orcid and canonical_orcid(candidate.orcid) != orcid
            if similarity >= matching.compatible_name and not conflict:
                scored.append((-similarity, candidate.source, str(candidate.author_id), candidate))
    scored.sort(key=lambda item: item[:3])
    return [Fragment.from_candidate(item[3]) for item in scored[: config.maximum_fragments]]


def person_coauthors(profile: ProfessorProfile, records: Iterable[PaperRecord]) -> set[str]:
    own = {normalize(name) for name in profile.target_names()}
    return {normalize(name) for record in records for name in record.authors} - own - {''}


class FragmentMerger:
    def __init__(self, config: FragmentConfig = DEFAULT_FRAGMENTS, matching: MatchingConfig = DEFAULT_MATCHING):
        self.config, self.matching = config, matching

    def select(
        self, profile: ProfessorProfile, verified: Iterable[PaperRecord], fragments: Sequence[Fragment]
    ) -> list[FragmentDecision]:
        """One decision per fragment, accepted ones in the order they joined."""
        network = person_coauthors(profile, verified)
        weights = self._weights(network, fragments)
        schools = [
            school
            for school in [profile.requested_university, *profile.matched_affiliations, profile.university]
            if school
        ]
        pending = list(fragments)
        decisions: list[FragmentDecision] = []
        changed = True
        while changed and pending:
            changed = False
            for fragment in list(pending):
                decision = self._decide(fragment, network, weights, schools, profile)
                if decision.accepted:
                    decisions.append(decision)
                    pending.remove(fragment)
                    network |= set(fragment.coauthors)
                    changed = True
        for fragment in pending:
            decisions.append(self._decide(fragment, network, weights, schools, profile))
        return decisions

    def _weights(self, network: set[str], fragments: Sequence[Fragment]) -> dict[str, float]:
        """Normalized inverse fragment frequency over the person and every fragment (fixed before merging). A
        shared coauthor is in at least two of the sets, so ``ln((N + 1) / 2)`` is the largest weight."""
        frequency: Counter[str] = Counter(network)
        for fragment in fragments:
            frequency.update(set(fragment.coauthors))
        total = len(fragments) + 1
        largest = math.log((total + 1) / 2)
        return {name: min(1.0, math.log((total + 1) / count) / largest) for name, count in frequency.items()}

    def _decide(
        self,
        fragment: Fragment,
        network: set[str],
        weights: Mapping[str, float],
        schools: Sequence[str],
        profile: ProfessorProfile,
    ) -> FragmentDecision:
        shared = sorted(network & set(fragment.coauthors), key=lambda name: (-weights.get(name, 0.0), name))
        score = round(sum(weights.get(name, 0.0) for name in shared), 3)
        decision = FragmentDecision(fragment, False, score, shared)
        orcid = canonical_orcid(profile.orcid) if profile.orcid else None
        if orcid and fragment.orcid and fragment.orcid != orcid:
            decision.reason = 'another ORCID'
        elif len(shared) < self.config.minimum_shared_coauthors or score < self.config.minimum_score:
            decision.reason = 'too little shared coauthor evidence'
        elif self.config.require_institution and not self._same_institution(fragment, schools):
            decision.reason = 'no shared institution'
        else:
            decision.accepted = True
            decision.reason = 'shared institution' if self.config.require_institution else 'coauthors only'
        return decision

    def _same_institution(self, fragment: Fragment, schools: Sequence[str]) -> bool:
        similarity = best_similarity(
            schools, fragment.affiliations, lambda a, b: institution_similarity(a, b, self.matching)
        )
        return similarity >= self.config.institution_similarity
