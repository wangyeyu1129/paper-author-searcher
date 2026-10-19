"""Score each person with the existing evaluator, then aggregate by stratum."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Protocol

from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher
from paper_author_searcher.jsonio import read_json
from paper_author_searcher.performance_evaluation import PerformanceEvaluator
from paper_author_searcher.status import AnnotationState, TruthState

from .metrics import BenchmarkMetric, MetricInput, benchmark_metrics
from .roster import BenchmarkPerson
from .settings import Aggregation, BenchmarkSettings

OVERALL = ('overall', 'all')
EMPTY_COLLECTION: dict[str, Any] = {'collection': {'all_papers': [], 'sources': []}}


def truth_paths(settings: BenchmarkSettings, person_id: str) -> tuple[Path, Path]:
    """(human-reviewed, pending) truth files for one person."""
    return settings.paths.truth / (person_id + '.json'), settings.paths.truth / (person_id + '.pending.json')


def load_truth(settings: BenchmarkSettings, person_id: str) -> dict | None:
    for path in truth_paths(settings, person_id):
        if path.exists():
            return read_json(path)
    return None


class OutputScope(Protocol):
    """Decides whether one system output entry is judged at all. ``matched`` lazily reports a truth match."""

    def keep(self, paper: dict, matched: Callable[[], bool]) -> bool: ...


@dataclass(frozen=True)
class WindowScope:
    """Judge only output inside the truth window; outside it, keep entries that match a truth publication.

    Without this, a closed-world score would count every verified paper from other years as a false positive.
    """

    start: int
    end: int

    def keep(self, paper: dict, matched: Callable[[], bool]) -> bool:
        records = paper.get('records') or [paper]
        if any(record.get('year') is not None and self.start <= record['year'] <= self.end for record in records):
            return True
        return matched()


@dataclass(frozen=True)
class PublicationScope:
    """Output that is not a publication (a dataset file, a book review) is neither a hit nor a false positive."""

    non_publication_types: frozenset[str]

    def keep(self, paper: dict, matched: Callable[[], bool]) -> bool:
        return paper.get('paper_type') not in self.non_publication_types


def scope_collection(collection: dict, truth: dict, scopes: Sequence[OutputScope]) -> dict:
    """Keep the output entries every scope accepts."""
    matcher = GroundTruthMatcher(GroundTruth.from_dict(truth))
    # Copy only the two containers that are reassigned; paper dicts are shared (scoring never mutates them).
    scoped = dict(collection)
    inner = scoped
    if isinstance(scoped.get('collection'), dict):
        inner = scoped['collection'] = dict(scoped['collection'])
    papers = inner.get('all_papers', inner.get('papers')) or []

    def keep(paper: dict) -> bool:
        records = paper.get('records') or [paper]

        @cache
        def matched() -> bool:
            return any(matcher.match(record).decision != 'unmatched' for record in records)

        return all(scope.keep(paper, matched) for scope in scopes)

    inner['all_papers'] = [paper for paper in papers if keep(paper)]
    inner['papers'] = inner['all_papers']
    return scoped


def scope_to_window(collection: dict, truth: dict, window: tuple[int, int]) -> dict:
    return scope_collection(collection, truth, [WindowScope(*window)])


def output_scopes(settings: BenchmarkSettings, window: tuple[int, int]) -> list[OutputScope]:
    scopes: list[OutputScope] = [WindowScope(*window)]
    if settings.type_policy.non_publication_output:
        scopes.append(PublicationScope(frozenset(settings.type_policy.non_publication_output)))
    return scopes


def truth_state(truth: dict) -> str:
    metadata = truth.get('metadata') or {}
    if is_human_reviewed(truth):
        return TruthState.HUMAN_REVIEWED
    if (
        metadata.get('annotation_state') == AnnotationState.ASSISTANT_REVIEWED
        and str(metadata.get('reviewer', '')).strip()
    ):
        return TruthState.ASSISTANT_REVIEWED
    return TruthState.PROVISIONAL


def is_human_reviewed(truth: dict) -> bool:
    metadata = truth.get('metadata') or {}
    return bool(
        metadata.get('annotation_state') == AnnotationState.HUMAN_REVIEWED
        and str(metadata.get('reviewer', '')).strip()
        and str(metadata.get('reviewed_at', '')).strip()
    )


@dataclass
class PersonScore:
    person_id: str
    strata: Mapping[str, str]
    truth_state: str
    run_status: str
    metrics: dict[str, dict] = field(default_factory=dict)
    positives: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'person_id': self.person_id,
            'strata': dict(self.strata),
            'truth_state': self.truth_state,
            'run_status': self.run_status,
            'truth_positives': self.positives,
            'metrics': self.metrics,
            'notes': self.notes,
        }


class PersonScorer:
    def __init__(
        self,
        settings: BenchmarkSettings,
        evaluator: PerformanceEvaluator | None = None,
        metrics: Mapping[str, BenchmarkMetric] | None = None,
    ) -> None:
        self.settings = settings
        self.evaluator = evaluator or PerformanceEvaluator()
        self.metrics = benchmark_metrics(settings) if metrics is None else metrics

    def score(self, person: BenchmarkPerson, run_root: Path, index_entry: dict | None) -> PersonScore:
        truth = load_truth(self.settings, person.person_id)
        if truth is None:
            return PersonScore(
                person.person_id,
                person.strata,
                TruthState.MISSING,
                _status(index_entry),
                {name: _unavailable() for name in self.settings.reported_metrics},
                notes=['No truth file; build it with build-truth'],
            )
        index_entry = index_entry or {}
        state = truth_state(truth)
        formal = state == AnnotationState.HUMAN_REVIEWED
        scoring = deepcopy(truth)
        notes = []
        if state == AnnotationState.ASSISTANT_REVIEWED:
            # Reviewed labels, but by an assistant: measured like reviewed truth, never certified by the gate.
            notes.append('Assistant-reviewed truth: labels reviewed by an AI assistant, not certified')
        elif not formal and self.settings.provisional_closed_world:
            # Provisional precision treats the source union as complete, so it is a lower bound.
            scoring['complete_positive_list'] = True
            notes.append('Provisional truth: proposed labels, closed-world precision is a lower bound')
        collection, pdf_report, manifest, manifest_path = EMPTY_COLLECTION, None, None, None
        report_dir = run_root / index_entry['report'] if index_entry.get('report') else None
        fulltext_dir = run_root / index_entry['fulltext_report'] if index_entry.get('fulltext_report') else report_dir
        if report_dir is not None:
            artifacts = read_json(report_dir / 'report.json').get('artifacts') or {}
            if artifacts.get('papers'):
                collection = read_json(report_dir / artifacts['papers'])
        if fulltext_dir is not None:
            artifacts = read_json(fulltext_dir / 'report.json').get('artifacts') or {}
            if artifacts.get('pdf_urls'):
                pdf_report = read_json(fulltext_dir / artifacts['pdf_urls'])
            if artifacts.get('manifest'):
                manifest_path = fulltext_dir / artifacts['manifest']
                manifest = read_json(manifest_path)
        if collection is EMPTY_COLLECTION:
            notes.append('Run produced no paper collection; every truth publication counts as missed')
        else:
            collection = scope_collection(
                collection, scoring, output_scopes(self.settings, person.window(self.settings))
            )
        result = self.evaluator.evaluate(
            collection, scoring, pdf_report, manifest, manifest_path, reference_only=not formal
        )
        positives = result['ownership_evaluation']['ground_truth']['positive_publications']
        produced = MetricInput(None if collection is EMPTY_COLLECTION else collection, result, positives)
        metrics = {}
        for name in self.settings.reported_metrics:
            if name in self.metrics:
                metrics[name] = self.metrics[name].compute(produced)
                continue
            if name not in result['metrics']:
                raise ValueError('Unknown reported metric: ' + name)
            metric = result['metrics'][name]
            metrics[name] = {
                'numerator': metric['numerator'],
                'denominator': metric['denominator'],
                'rate': metric['rate'],
                'available': metric['available'],
            }
        return PersonScore(person.person_id, person.strata, state, _status(index_entry), metrics, positives, notes)


def _status(entry: dict | None) -> str:
    return (entry or {}).get('status', 'not_run')


def _unavailable() -> dict:
    return {'numerator': 0, 'denominator': 0, 'rate': None, 'available': False}


def aggregate(scores: list[PersonScore], settings: BenchmarkSettings) -> dict[tuple[str, str], dict]:
    strata: dict[tuple[str, str], list[PersonScore]] = {OVERALL: list(scores)}
    for dimension, values in settings.dimensions.items():
        for value in values:
            strata[(dimension, value)] = [score for score in scores if score.strata.get(dimension) == value]
    result = {}
    for key, members in strata.items():
        metrics = {}
        for name in settings.reported_metrics:
            floor = settings.minimum_truth_positives.get(name, 0)
            eligible = [member for member in members if member.positives >= floor]
            available = [member.metrics[name] for member in eligible if member.metrics[name]['available']]
            rates = [value['rate'] for value in available if value['rate'] is not None]
            numerator = sum(value['numerator'] for value in available)
            denominator = sum(value['denominator'] for value in available)
            metrics[name] = {
                Aggregation.MACRO.value: sum(rates) / len(rates) if rates else None,
                Aggregation.MICRO.value: numerator / denominator if denominator else None,
                'people_with_rate': len(rates),
                'minimum': min(rates) if rates else None,
                'numerator': numerator,
                'denominator': denominator,
                'below_truth_minimum': len(members) - len(eligible),
            }
        result[key] = {
            'people': len(members),
            'metrics': metrics,
            'provisional_people': sum(member.truth_state != TruthState.HUMAN_REVIEWED for member in members),
        }
    return result
