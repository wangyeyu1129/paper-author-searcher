"""Score each person with the existing evaluator, then aggregate by stratum."""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher
from paper_author_searcher.jsonio import read_json
from paper_author_searcher.performance_evaluation import PerformanceEvaluator
from paper_author_searcher.status import AnnotationState, TruthState

from .roster import BenchmarkPerson
from .settings import Aggregation, BenchmarkSettings

OVERALL = ('overall', 'all')
RESOLVED_CONFIRMATIONS = frozenset({'resolver_resolved', 'manual_author_id'})


def identity_resolution(collection: Optional[dict]) -> dict:
    """1/1 when the system committed to an identity it confirmed itself; 0/1 when it stopped or only collected
    competing profiles. Measured per person, so the stratum macro is the share of people resolved."""
    inner = (collection or {}).get('collection') or {}
    resolved = inner.get('identity_confirmation') in RESOLVED_CONFIRMATIONS
    return {'numerator': int(resolved), 'denominator': 1, 'rate': float(resolved), 'available': True}


# Metrics the benchmark computes itself rather than reading from PerformanceEvaluator.
BENCHMARK_METRICS = {'identity_resolution_rate': identity_resolution}
EMPTY_COLLECTION = {'collection': {'all_papers': [], 'sources': []}}


def truth_paths(settings: BenchmarkSettings, person_id: str) -> Tuple[Path, Path]:
    """(human-reviewed, pending) truth files for one person."""
    return settings.paths.truth / (person_id + '.json'), settings.paths.truth / (person_id + '.pending.json')


def load_truth(settings: BenchmarkSettings, person_id: str) -> Optional[dict]:
    for path in truth_paths(settings, person_id):
        if path.exists():
            return read_json(path)
    return None


def scope_to_window(collection: dict, truth: dict, window: Tuple[int, int]) -> dict:
    """Judge only output inside the truth window; outside it, keep entries that match a truth publication.

    Without this, a closed-world score would count every verified paper from other years as a false positive.
    """
    start, end = window
    matcher = GroundTruthMatcher(GroundTruth.from_dict(truth))
    scoped = deepcopy(collection)
    inner = scoped.get('collection', scoped)
    papers = inner.get('all_papers', inner.get('papers')) or []

    def keep(paper: dict) -> bool:
        records = paper.get('records') or [paper]
        if any(record.get('year') is not None and start <= record['year'] <= end for record in records):
            return True
        return any(matcher.match(record).decision != 'unmatched' for record in records)

    inner['all_papers'] = [paper for paper in papers if keep(paper)]
    inner['papers'] = inner['all_papers']
    return scoped


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
    metrics: Dict[str, dict] = field(default_factory=dict)
    positives: int = 0
    notes: List[str] = field(default_factory=list)

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
    def __init__(self, settings: BenchmarkSettings, evaluator: Optional[PerformanceEvaluator] = None) -> None:
        self.settings = settings
        self.evaluator = evaluator or PerformanceEvaluator()

    def score(self, person: BenchmarkPerson, run_root: Path, index_entry: Optional[dict]) -> PersonScore:
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
            collection = scope_to_window(collection, scoring, person.window(self.settings))
        result = self.evaluator.evaluate(
            collection, scoring, pdf_report, manifest, manifest_path, reference_only=not formal
        )
        metrics = {}
        for name in self.settings.reported_metrics:
            if name in BENCHMARK_METRICS:
                metrics[name] = BENCHMARK_METRICS[name](None if collection is EMPTY_COLLECTION else collection)
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
        positives = result['ownership_evaluation']['ground_truth']['positive_publications']
        return PersonScore(person.person_id, person.strata, state, _status(index_entry), metrics, positives, notes)


def _status(entry: Optional[dict]) -> str:
    return (entry or {}).get('status', 'not_run')


def _unavailable() -> dict:
    return {'numerator': 0, 'denominator': 0, 'rate': None, 'available': False}


def aggregate(scores: List[PersonScore], settings: BenchmarkSettings) -> Dict[Tuple[str, str], dict]:
    strata: Dict[Tuple[str, str], List[PersonScore]] = {OVERALL: list(scores)}
    for dimension, values in settings.dimensions.items():
        for value in values:
            strata[(dimension, value)] = [score for score in scores if score.strata.get(dimension) == value]
    result = {}
    for key, members in strata.items():
        metrics = {}
        for name in settings.reported_metrics:
            values = [member.metrics[name] for member in members if member.metrics[name]['available']]
            rates = [value['rate'] for value in values if value['rate'] is not None]
            numerator = sum(value['numerator'] for value in values)
            denominator = sum(value['denominator'] for value in values)
            metrics[name] = {
                Aggregation.MACRO.value: sum(rates) / len(rates) if rates else None,
                Aggregation.MICRO.value: numerator / denominator if denominator else None,
                'people_with_rate': len(rates),
                'minimum': min(rates) if rates else None,
                'numerator': numerator,
                'denominator': denominator,
            }
        result[key] = {
            'people': len(members),
            'metrics': metrics,
            'provisional_people': sum(member.truth_state != TruthState.HUMAN_REVIEWED for member in members),
        }
    return result
