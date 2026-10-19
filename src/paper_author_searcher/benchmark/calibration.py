"""Identity calibration on recorded API responses: tune matching parameters on dev people, check on test people.

Each candidate configuration re-runs only the resolver against the replay cache, then scores the chosen author IDs
with the diagnosis labels (which candidate profiles hold the person's truth papers):

- coverage: share of truth hits found in all candidates that the chosen IDs reach;
- purity:   share of chosen, diagnosed IDs that belong to truth-bearing profiles.

Only resolved identities feed verification, so the objective is resolved coverage subject to a purity floor.
"""

import itertools
from copy import deepcopy
from dataclasses import dataclass, replace
from statistics import mean
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from paper_author_searcher.config import AmbiguityPolicy, MatchingConfig
from paper_author_searcher.models import AuthorCandidate, ProfessorQuery
from paper_author_searcher.resolver import ProfessorResolver
from paper_author_searcher.status import ResolutionStatus, UniversityStatus
from paper_author_searcher.workflow import contender_profile

from .identity_diagnosis import is_inconclusive

AuthorKey = Tuple[str, str]


@dataclass
class IdentityOutcome:
    person_id: str
    status: str
    coverage: Optional[float]
    purity: Optional[float]
    selected: List[str]

    def to_dict(self) -> dict:
        return {
            'person_id': self.person_id,
            'status': self.status,
            'coverage': self.coverage,
            'purity': self.purity,
            'selected': self.selected,
        }


def labelled_profiles(diagnosis: dict) -> List[Tuple[Set[AuthorKey], int, int]]:
    profiles = []
    for candidate in diagnosis.get('candidates') or []:
        keys = {(source, str(value)) for source, values in candidate['author_ids'].items() for value in values}
        profiles.append((keys, candidate['truth_hits'], candidate.get('window_works', 0)))
    return profiles


def score_selection(selected: Set[AuthorKey], diagnosis: dict) -> Tuple[Optional[float], Optional[float]]:
    profiles = labelled_profiles(diagnosis)
    total = sum(hits for _, hits, _ in profiles)
    reached = sum(hits for keys, hits, _ in profiles if keys & selected)
    # Only IDs with works in the window can be judged: a fragment holding only older papers is not evidence
    # of a different person.
    judged = [(keys, hits) for keys, hits, works in profiles if works]
    known = [key for key in selected if any(key in keys for keys, _ in judged)]
    bearing = [key for key in known if any(key in keys and hits for keys, hits in judged)]
    coverage = reached / total if total else None
    purity = len(bearing) / len(known) if known else None
    return coverage, purity


class MemoizedProvider:
    """Search results do not depend on the tuned scoring parameters, so each query is searched once."""

    def __init__(self, provider: Any, memory: dict) -> None:
        self.provider = provider
        self.name = provider.name
        self.memory = memory

    def search(self, query: ProfessorQuery) -> List[AuthorCandidate]:
        key = (self.name, query.name, query.university)
        if key not in self.memory:
            self.memory[key] = self.provider.search(query)
        return deepcopy(self.memory[key])

    def lookup(self, author_id: str) -> AuthorCandidate:
        key = (self.name, 'lookup', author_id)
        if key not in self.memory:
            self.memory[key] = self.provider.lookup(author_id)
        return deepcopy(self.memory[key])


def memoized(resolver: ProfessorResolver, memory: dict) -> ProfessorResolver:
    resolver.providers = [MemoizedProvider(provider, memory) for provider in resolver.providers]
    return resolver


class IdentityCalibrator:
    def __init__(
        self,
        resolver_factory: Callable[[MatchingConfig], ProfessorResolver],
        policy: str = 'stop',
        maximum_contenders: int = 3,
    ) -> None:
        self.resolver_factory = resolver_factory
        self.memory: dict = {}
        self.policy = policy
        self.maximum_contenders = maximum_contenders

    def evaluate(
        self, config: MatchingConfig, people: Iterable, diagnoses: Mapping[str, dict]
    ) -> List[IdentityOutcome]:
        resolver = memoized(self.resolver_factory(config), self.memory)
        outcomes = []
        for person in people:
            diagnosis = diagnoses.get(person.person_id)
            if not diagnosis or diagnosis.get('diagnosis') == 'no_truth' or is_inconclusive(diagnosis):
                continue
            result = resolver.resolve(person.search_input.to_query())
            profile, status = result.profile, 'none'
            if profile is not None and profile.status == ResolutionStatus.RESOLVED:
                status = ResolutionStatus.RESOLVED
            elif (
                profile is not None
                and self.policy == AmbiguityPolicy.COLLECT_CONTENDERS
                and profile.university_status != UniversityStatus.MISMATCH
            ):
                profile, status = (
                    contender_profile(result, self.maximum_contenders, config.ambiguity_margin),
                    ResolutionStatus.AMBIGUOUS_CONTENDERS,
                )
            else:
                profile = None
            selected = set()
            if profile is not None:
                selected = {
                    (source, str(value)) for source, values in profile.author_id_aliases.items() for value in values
                }
                selected |= {(source, str(value)) for source, value in profile.author_ids.items()}
            coverage, purity = score_selection(selected, diagnosis)
            outcomes.append(
                IdentityOutcome(
                    person.person_id, status, coverage, purity, sorted('{}:{}'.format(*key) for key in selected)
                )
            )
        return outcomes

    @staticmethod
    def summary(outcomes: Sequence[IdentityOutcome]) -> dict:
        resolved = [outcome for outcome in outcomes if outcome.status == ResolutionStatus.RESOLVED]
        coverages = [
            outcome.coverage if outcome.status == ResolutionStatus.RESOLVED else 0.0
            for outcome in outcomes
            if outcome.coverage is not None
        ]
        purities = [outcome.purity for outcome in resolved if outcome.purity is not None]
        return {
            'people': len(outcomes),
            'resolved': len(resolved),
            'resolved_coverage': mean(coverages) if coverages else None,
            'resolved_purity': mean(purities) if purities else None,
        }

    def search(
        self,
        base: MatchingConfig,
        grid: Mapping[str, Sequence[object]],
        tune: List,
        holdout: List,
        diagnoses: Mapping[str, dict],
        minimum_purity: float,
    ) -> dict:
        names = sorted(grid)
        trials = []
        for values in itertools.product(*(grid[name] for name in names)) if names else [()]:
            overrides: Dict[str, Any] = dict(zip(names, values))
            config = replace(base, **overrides)
            summary = self.summary(self.evaluate(config, tune, diagnoses))
            trials.append({'parameters': overrides, 'tune': summary})
        eligible = [
            trial
            for trial in trials
            if trial['tune']['resolved_purity'] is None or trial['tune']['resolved_purity'] >= minimum_purity
        ]
        pool = eligible or trials

        def rank(trial: dict) -> Tuple[float, ...]:
            tune = trial['tune']
            return (tune['resolved_coverage'] or 0.0, tune['resolved_purity'] or 0.0, -_distance(trial, base))

        best = max(pool, key=rank)
        baseline_outcomes = self.evaluate(base, holdout, diagnoses)
        best_outcomes = self.evaluate(replace(base, **best['parameters']), holdout, diagnoses)
        return {
            'grid': {name: list(grid[name]) for name in names},
            'minimum_purity': minimum_purity,
            'purity_floor_met': bool(eligible),
            'baseline': {
                'parameters': {name: getattr(base, name) for name in names},
                'tune': self.summary(self.evaluate(base, tune, diagnoses)),
                'holdout': self.summary(baseline_outcomes),
            },
            'recommended': {
                'parameters': best['parameters'],
                'tune': best['tune'],
                'holdout': self.summary(best_outcomes),
            },
            'holdout_people': [outcome.to_dict() for outcome in best_outcomes],
            'trials': trials,
        }


def _distance(trial: dict, base: MatchingConfig) -> int:
    """Prefer the smallest change from the shipped defaults among equally good trials."""
    return sum(getattr(base, name) != value for name, value in trial['parameters'].items())


def markdown(report: dict) -> str:
    def row(label: str, summary: dict) -> str:
        return '| {} | {} | {} | {} | {} |'.format(
            label,
            summary['people'],
            summary['resolved'],
            _rate(summary['resolved_coverage']),
            _rate(summary['resolved_purity']),
        )

    lines = [
        '# Identity calibration',
        '',
        '- Recommended parameters: `{}`'.format(report['recommended']['parameters']),
        '- Baseline parameters: `{}`'.format(report['baseline']['parameters']),
        '- Purity floor {:.0%} met by some trial: {}'.format(report['minimum_purity'], report['purity_floor_met']),
        '',
        '| Set | People | Resolved | Resolved coverage | Resolved purity |',
        '|---|---:|---:|---:|---:|',
        row('baseline / tune', report['baseline']['tune']),
        row('baseline / holdout', report['baseline']['holdout']),
        row('recommended / tune', report['recommended']['tune']),
        row('recommended / holdout', report['recommended']['holdout']),
        '',
    ]
    return '\n'.join(lines)


def _rate(value: Optional[float]) -> str:
    return '–' if value is None else f'{value:.1%}'
