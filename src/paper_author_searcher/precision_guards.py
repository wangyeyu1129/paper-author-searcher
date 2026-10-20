"""Stricter ownership evidence for common names, applied per author-ID fragment.

At one university a common name belongs to several researchers, and OpenAlex files some of the person's papers
under a namesake's author ID. Homepage anchors then pull the namesake's whole fragment into the identity: Yang Liu
(Tsinghua CS) gained Tsinghua vehicle-engineering and chemical-engineering fragments, whose papers the institution
rule verified because the paper-level institution was "Tsinghua University". The guard keeps such evidence honest:

- the institution must agree at department level, not just university level;
- a fragment whose department profile is not the person's is foreign: its papers can be claimed one by one through
  paper-level evidence (a matching paper ORCID), never wholesale through institution, coauthor or cross-source rules;
- a paper whose own institution contradicts the confirmed one cannot be upgraded by coauthors or a second source;
- common-name coauthors ("Hui Li") do not count as recurring coauthors.

It applies only to common names and only to fragments without an ORCID anchor (``PrecisionGuardConfig``); the
null context below leaves every other identity on the ordinary rules.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any

from .config import DEFAULT_OWNERSHIP, DEFAULT_PRECISION, PrecisionGuardConfig
from .matching import institution_similarity, normalize, work_keys
from .models import AnchorEvidence, Paper, PaperRecord, ProfessorProfile
from .paper_authorship import canonical_author_id

DATA_PACKAGE = 'paper_author_searcher.data'


class CommonNamePolicy:
    """A name is common when one of its (few) tokens is a frequent surname."""

    def __init__(self, surnames: Iterable[str], maximum_tokens: int) -> None:
        self.surnames = frozenset(normalize(surname) for surname in surnames if surname)
        self.maximum_tokens = maximum_tokens

    @classmethod
    def from_config(cls, config: PrecisionGuardConfig) -> 'CommonNamePolicy':
        return cls(_read_list(config.common_surnames), config.maximum_name_tokens)

    def is_common(self, name: str | None) -> bool:
        tokens = normalize(name or '').split()
        return 0 < len(tokens) <= self.maximum_tokens and any(token in self.surnames for token in tokens)

    def collides_easily(self, name: str | None, given_letters: int) -> bool:
        """A common surname with a short given name ("Hui Li"): many different people share it."""
        tokens = normalize(name or '').split()
        if not self.is_common(name):
            return False
        for index, token in enumerate(tokens):
            if token in self.surnames:
                given = ''.join(tokens[:index] + tokens[index + 1 :])
                if 0 < len(given) <= given_letters:
                    return True
        return False


class DepartmentMatcher:
    """Extracts unit phrases from raw affiliation strings and decides whether two phrases name the same unit."""

    def __init__(self, config: PrecisionGuardConfig = DEFAULT_PRECISION) -> None:
        self.config = config
        self.keywords = frozenset(config.department_keywords)
        self.stop_words = frozenset(config.department_stop_words)

    def phrases(self, affiliation: str) -> list[str]:
        found = []
        for part in affiliation.split(','):
            words = normalize(part).split()
            if words and self.keywords.intersection(words):
                found.append(' '.join(words))
        return found

    def words(self, phrase: str) -> frozenset[str]:
        return frozenset(word for word in normalize(phrase).split() if word not in self.stop_words)

    def agree(self, phrase: str, target: str) -> bool:
        left, right = self.words(phrase), self.words(target)
        if not left or not right:
            return False
        if left <= right or right <= left:
            return True
        return len(left & right) / len(left | right) >= self.config.department_similarity

    def agrees_with_any(self, phrase: str, targets: Iterable[str]) -> bool:
        return any(self.agree(phrase, target) for target in targets)


class FragmentVerdict(str, Enum):
    HOME = 'home'
    FOREIGN = 'foreign'
    UNPROFILED = 'unprofiled'  # too little department evidence to judge


@dataclass
class FragmentProfile:
    source: str
    author_id: str
    departments: Counter = field(default_factory=Counter)
    anchors: int = 0
    verdict: FragmentVerdict = FragmentVerdict.UNPROFILED

    @property
    def evidence(self) -> int:
        return sum(self.departments.values())


def target_affiliations(record: PaperRecord, author: Mapping[str, Any]) -> list[str]:
    """The paper-level raw affiliation strings of one author entry (never author-profile copies)."""
    if author.get('affiliation_scope') == 'author_profile':
        return []
    return [
        item['raw_affiliation_string']
        for item in author.get('affiliations') or []
        if item.get('raw_affiliation_string')
    ]


class GuardContext:
    """Null object: an identity the guard does not cover keeps every ordinary rule."""

    def guarded(self, record: PaperRecord) -> bool:
        return False

    def foreign(self, record: PaperRecord) -> bool:
        return False

    def department_agrees(self, record: PaperRecord, author: Mapping[str, Any]) -> bool:
        return True

    def contradicted(self, record: PaperRecord, details: Sequence[Mapping[str, Any]]) -> bool:
        return False

    def counts_as_coauthor(self, record: PaperRecord, name: str) -> bool:
        return True

    def describe(self) -> list[str]:
        return []


class ActiveGuard(GuardContext):
    def __init__(
        self,
        config: PrecisionGuardConfig,
        names: CommonNamePolicy,
        departments: DepartmentMatcher,
        profile: ProfessorProfile,
        fragments: dict[tuple[str, str], FragmentProfile],
        target_departments: list[str],
        exempt: set[tuple[str, str]],
        institution_similarity: float,
        confirmed_coauthors: frozenset[str] = frozenset(),
        departments_observed: bool = True,
        history: frozenset[str] = frozenset(),
    ) -> None:
        self.config = config
        self.institution_similarity = institution_similarity
        # Unit-level checks need units seen on the person's own papers; the requested department alone misjudges
        # people who moved, so without them the checks stay neutral.
        self.departments_observed = departments_observed
        # Institutions on the person's own papers (anchor works, listed works).
        self.history = history
        self.names = names
        self.departments = departments
        self.profile = profile
        self.fragments = fragments
        self.target_departments = target_departments
        self.exempt = exempt
        # Names on the person's own papers: trusted even when they collide easily (an advisor called "Lu Lu").
        self.confirmed_coauthors = confirmed_coauthors

    @staticmethod
    def _key(record: PaperRecord) -> tuple[str, str]:
        return record.source, canonical_author_id(record.source, record.source_author_id)

    def guarded(self, record: PaperRecord) -> bool:
        return self._key(record) not in self.exempt

    def foreign(self, record: PaperRecord) -> bool:
        fragment = self.fragments.get(self._key(record))
        return self.guarded(record) and fragment is not None and fragment.verdict is FragmentVerdict.FOREIGN

    def department_agrees(self, record: PaperRecord, author: Mapping[str, Any]) -> bool:
        """Unit-level agreement; an affiliation that names no unit is neutral, one naming another unit is not."""
        if not self.guarded(record) or not self.departments_observed:
            return True
        phrases = [
            phrase
            for affiliation in target_affiliations(record, author)
            for phrase in self.departments.phrases(affiliation)
        ]
        return not phrases or any(
            self.departments.agrees_with_any(phrase, self.target_departments) for phrase in phrases
        )

    def contradicted(self, record: PaperRecord, details: Sequence[Mapping[str, Any]]) -> bool:
        if not self.guarded(record):
            return False
        current = list(self.profile.matched_affiliations) or (
            [self.profile.university] if self.profile.university else []
        )
        # The reliable history is what the person's own papers show; anything else would penalise past positions.
        confirmed = [*current, *self.history]
        observed = paper_institutions(details)
        if not current or not observed:
            return False
        threshold = self.institution_similarity
        return not any(institution_similarity(seen, known) >= threshold for seen in observed for known in confirmed)

    def counts_as_coauthor(self, record: PaperRecord, name: str) -> bool:
        if not (self.config.exclude_common_coauthors and self.guarded(record)):
            return True
        if normalize(name) in self.confirmed_coauthors:
            return True
        return not self.names.collides_easily(name, self.config.collision_given_name_letters)

    def describe(self) -> list[str]:
        foreign = sorted(
            f'{key[0]}:{key[1]}' for key, item in self.fragments.items() if item.verdict is FragmentVerdict.FOREIGN
        )
        lines = ['Common-name precision guard active; departments: ' + ('; '.join(self.target_departments) or 'none')]
        if foreign:
            lines.append('Foreign fragments (papers claimed one by one only): ' + ', '.join(foreign))
        return lines


class PrecisionGuard:
    """Builds the per-identity guard context before ownership validation."""

    def __init__(
        self,
        config: PrecisionGuardConfig = DEFAULT_PRECISION,
        names: CommonNamePolicy | None = None,
        departments: DepartmentMatcher | None = None,
        institution_similarity: float = DEFAULT_OWNERSHIP.institution_similarity,
    ) -> None:
        self.config = config
        # The ownership rule's own threshold, so "contradicts" is the exact opposite of "agrees".
        self.institution_similarity = institution_similarity
        self.names = names or CommonNamePolicy.from_config(config)
        self.departments = departments or DepartmentMatcher(config)

    def prepare(self, papers: Sequence[Paper], profile: ProfessorProfile) -> GuardContext:
        if not self.config.enabled or not self.names.is_common(profile.requested_name or profile.name):
            return GuardContext()
        anchors = [AnchorEvidence.coerce(item) for item in profile.identity_anchors]
        exempt = {
            (anchor.source, canonical_author_id(anchor.source, anchor.author_id))
            for anchor in anchors
            if anchor.kind in self.config.exempt_anchor_kinds
        }
        fragments = self._fragments(papers, profile, anchors)
        own = self._own_records(papers, profile, anchors)
        targets, observed = self._target_departments(profile, own)
        # Fragments are judged only against units seen on the person's own papers: a requested department alone
        # misjudges people who moved (Huan Zhang's CS papers predate his ECE appointment).
        for fragment in fragments.values():
            fragment.verdict = self._verdict(fragment, targets) if observed else FragmentVerdict.UNPROFILED
        return ActiveGuard(
            self.config,
            self.names,
            self.departments,
            profile,
            fragments,
            targets,
            exempt,
            self.institution_similarity,
            frozenset(normalize(name) for record in own for name in record.authors),
            observed,
            frozenset(institution for record in own for institution in paper_institutions(_targets(record))),
        )

    def _fragments(
        self, papers: Sequence[Paper], profile: ProfessorProfile, anchors: Sequence[AnchorEvidence]
    ) -> dict[tuple[str, str], FragmentProfile]:
        fragments: dict[tuple[str, str], FragmentProfile] = {}
        for paper in papers:
            for record in paper.records:
                key = (record.source, canonical_author_id(record.source, record.source_author_id))
                fragment = fragments.setdefault(key, FragmentProfile(*key))
                for author in record.author_details:
                    if canonical_author_id(record.source, author.get('author_id')) != key[1]:
                        continue
                    for affiliation in target_affiliations(record, author):
                        fragment.departments.update(set(self.departments.phrases(affiliation)))
        for anchor in anchors:
            key = (anchor.source, canonical_author_id(anchor.source, anchor.author_id))
            if key in fragments:
                fragments[key].anchors += 1
        return fragments

    @staticmethod
    def _own_records(
        papers: Sequence[Paper], profile: ProfessorProfile, anchors: Sequence[AnchorEvidence]
    ) -> list[PaperRecord]:
        """Records of the person's own papers: the works their anchors came from and their page lists."""
        own_works = {anchor.work for anchor in anchors if anchor.work} | set(profile.listed_works)
        return [record for paper in papers for record in paper.records if record_work_keys(record) & own_works]

    def _target_departments(self, profile: ProfessorProfile, own: Sequence[PaperRecord]) -> tuple[list[str], bool]:
        """The requested department plus the units named on the person's own papers. Whole fragments are no
        guide: an anchor can sit on a namesake's fragment that OpenAlex filed one of the person's papers under."""
        targets = [profile.requested_department] if profile.requested_department else []
        mentions: Counter = Counter()
        for record in own:
            target = canonical_author_id(record.source, record.source_author_id)
            for author in record.author_details:
                if canonical_author_id(record.source, author.get('author_id')) == target:
                    for affiliation in target_affiliations(record, author):
                        mentions.update(set(self.departments.phrases(affiliation)))
        total = sum(mentions.values())
        observed = [
            phrase
            for phrase, count in mentions.most_common(self.config.maximum_home_departments)
            if count / total >= self.config.home_department_share
        ]
        return list(dict.fromkeys(targets + observed)), bool(observed)

    def _verdict(self, fragment: FragmentProfile, targets: Sequence[str]) -> FragmentVerdict:
        if not targets or fragment.evidence < self.config.fragment_minimum_evidence:
            return FragmentVerdict.UNPROFILED
        matching = sum(
            count for phrase, count in fragment.departments.items() if self.departments.agrees_with_any(phrase, targets)
        )
        share = matching / fragment.evidence
        return FragmentVerdict.HOME if share >= self.config.fragment_minimum_share else FragmentVerdict.FOREIGN


def paper_institutions(details: Iterable[Mapping[str, Any]]) -> list[str]:
    """Paper-level institution names of author entries (author-profile copies say nothing about a paper)."""
    return [
        institution['display_name']
        for author in details
        if author.get('affiliation_scope') != 'author_profile'
        for institution in author.get('institutions') or []
        if institution.get('display_name')
    ]


def _targets(record: PaperRecord) -> list[Mapping[str, Any]]:
    target = canonical_author_id(record.source, record.source_author_id)
    return [
        author
        for author in record.author_details
        if canonical_author_id(record.source, author.get('author_id')) == target
    ]


def record_work_keys(record: PaperRecord) -> set[str]:
    external = (record.external_ids or {}).get('arxiv')
    return work_keys([record.doi] if record.doi else [], [external] if external else [], [record.title])


def _read_list(location: str) -> list[str]:
    path = Path(location)
    text = (
        path.read_text(encoding='utf-8')
        if path.is_file()
        else (resources.files(DATA_PACKAGE).joinpath(location).read_text(encoding='utf-8'))
    )
    return [line.strip().lower() for line in text.splitlines() if line.strip() and not line.startswith('#')]
