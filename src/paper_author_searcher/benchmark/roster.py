"""Benchmark people: the search input, stratum labels and where independent truth comes from."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from paper_author_searcher.models import ProfessorQuery

from .settings import BenchmarkSettings

PERSON_ID = re.compile(r'^[a-z0-9][a-z0-9_]{1,63}$')


@dataclass(frozen=True)
class SearchInput:
    """Only what a production caller supplies; truth URLs are never passed to the searcher."""

    name: str
    university: str
    department: Optional[str] = None
    research: Tuple[str, ...] = ()
    university_aliases: Tuple[str, ...] = ()
    # Optional hints a production caller may have (used only when the run's input profile asks for them).
    orcid: Optional[str] = None
    homepage: Optional[str] = None
    known_papers: Tuple[str, ...] = ()

    def to_query(self, with_hints: bool = False) -> ProfessorQuery:
        hints = {}
        if with_hints:
            hints = {'orcid': self.orcid, 'homepage': self.homepage, 'known_papers': list(self.known_papers)}
        return ProfessorQuery(
            self.name, self.university, self.department, list(self.research), list(self.university_aliases), **hints
        )


@dataclass(frozen=True)
class TruthSourceSpec:
    """``locator`` is an ORCID iD for live sources or a snapshot file name; ``url`` is the public origin."""

    kind: str
    locator: str
    url: str = ''
    note: str = ''


@dataclass(frozen=True)
class BenchmarkPerson:
    person_id: str
    search_input: SearchInput
    strata: Mapping[str, str]
    truth_sources: Tuple[TruthSourceSpec, ...]
    identity_evidence: Tuple[str, ...] = ()
    orcid: Optional[str] = None
    year_start: Optional[int] = None
    year_end: Optional[int] = None
    notes: str = ''
    split: Optional[str] = None

    def window(self, settings: BenchmarkSettings) -> Tuple[int, int]:
        return (self.year_start or settings.year_start, self.year_end or settings.year_end)

    @classmethod
    def from_dict(cls, data: dict) -> 'BenchmarkPerson':
        query = data['search_input']
        return cls(
            person_id=data['person_id'],
            search_input=SearchInput(
                query['name'],
                query['university'],
                query.get('department'),
                tuple(query.get('research') or ()),
                tuple(query.get('university_aliases') or ()),
                orcid=(query.get('hints') or {}).get('orcid'),
                homepage=(query.get('hints') or {}).get('homepage'),
                known_papers=tuple((query.get('hints') or {}).get('known_papers') or ()),
            ),
            strata=dict(data['strata']),
            truth_sources=tuple(TruthSourceSpec(**source) for source in data.get('truth_sources') or ()),
            identity_evidence=tuple(data.get('identity_evidence') or ()),
            orcid=data.get('orcid'),
            year_start=data.get('year_start'),
            year_end=data.get('year_end'),
            notes=data.get('notes', ''),
            split=data.get('split'),
        )


@dataclass(frozen=True)
class Roster:
    dataset_id: str
    people: Tuple[BenchmarkPerson, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)

    def select(self, person_ids: Iterable[str] = ()) -> List[BenchmarkPerson]:
        wanted = set(person_ids)
        if not wanted:
            return list(self.people)
        unknown = wanted - {person.person_id for person in self.people}
        if unknown:
            raise ValueError(f'Unknown benchmark people: {sorted(unknown)}')
        return [person for person in self.people if person.person_id in wanted]

    def validate(self, settings: BenchmarkSettings, known_source_kinds: Iterable[str]) -> List[str]:
        """Raise on structural errors; return warnings for strata below the configured size."""
        if self.dataset_id != settings.dataset_id:
            raise ValueError('Roster dataset_id does not match the benchmark config')
        kinds = set(known_source_kinds)
        seen = set()
        for person in self.people:
            if not PERSON_ID.match(person.person_id) or person.person_id in seen:
                raise ValueError(f'Invalid or duplicate person_id: {person.person_id}')
            seen.add(person.person_id)
            if not person.search_input.name.strip() or not person.search_input.university.strip():
                raise ValueError(f'{person.person_id} needs a name and university')
            if set(person.strata) != set(settings.dimensions):
                raise ValueError(f'{person.person_id} must be labelled on every dimension')
            for dimension, value in person.strata.items():
                if value not in settings.dimensions[dimension]:
                    raise ValueError(f'{person.person_id}: {value} is not a {dimension} value')
            if not person.truth_sources:
                raise ValueError(f'{person.person_id} needs at least one truth source')
            for source in person.truth_sources:
                if source.kind not in kinds:
                    raise ValueError(f'{person.person_id}: unknown truth source kind {source.kind}')
            if person.split is not None and person.split not in settings.calibration.splits:
                raise ValueError(f'{person.person_id}: split must be one of {settings.calibration.splits}')
            start, end = person.window(settings)
            if start > end:
                raise ValueError(f'{person.person_id} has an empty year window')
        return [
            f'Stratum {dimension}={value} has {count} people; the gate needs {settings.minimum_people_per_stratum}'
            for (dimension, value), count in sorted(self.stratum_sizes(settings).items())
            if count < settings.minimum_people_per_stratum
        ]

    def stratum_sizes(self, settings: BenchmarkSettings) -> Dict[Tuple[str, str], int]:
        sizes = {(dimension, value): 0 for dimension, values in settings.dimensions.items() for value in values}
        for person in self.people:
            for dimension, value in person.strata.items():
                sizes[(dimension, value)] += 1
        return sizes

    @classmethod
    def load(cls, path: Path) -> 'Roster':
        with Path(path).open(encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get('roster_schema_version') != 1:
            raise ValueError('Roster requires roster_schema_version=1')
        return cls(
            data['dataset_id'],
            tuple(BenchmarkPerson.from_dict(person) for person in data['people']),
            data.get('metadata') or {},
        )
