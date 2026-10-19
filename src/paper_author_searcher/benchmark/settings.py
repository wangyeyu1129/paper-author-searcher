"""Benchmark configuration loaded from the dataset's own JSON file; no thresholds live in code."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from paper_author_searcher.config import AmbiguityPolicy


class Aggregation(str, Enum):
    MACRO = 'macro'
    MICRO = 'micro'


@dataclass(frozen=True)
class GateRule:
    """A release condition: every stratum's aggregated metric must reach ``minimum``.

    ``aggregation`` overrides the gate's default, so one metric can be required both per person (macro) and
    pooled over publications (micro).
    """

    metric: str
    minimum: float
    label: str = ''
    aggregation: Aggregation | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.minimum <= 1:
            raise ValueError('Gate minimum must be a rate between 0 and 1')

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'GateRule':
        values = dict(data)
        if values.get('aggregation') is not None:
            values['aggregation'] = Aggregation(values['aggregation'])
        return cls(**values)


@dataclass(frozen=True)
class TypePolicy:
    """Which publications count. ``included``/``excluded`` use the truth vocabulary; ``non_publication_output``
    lists system ``paper_type`` values that are not publications, so such output is never judged."""

    included: tuple[str, ...]
    excluded: tuple[str, ...]
    unknown_included: bool = True
    non_publication_output: tuple[str, ...] = ()

    def includes(self, work_type: str | None) -> bool:
        if work_type in self.included:
            return True
        if work_type in self.excluded:
            return False
        return self.unknown_included


@dataclass(frozen=True)
class RunSettings:
    """Attribute names match the workflow CLI so the existing composition root can be reused."""

    mode: str = 'full'
    timeout: float | None = None
    retries: int | None = None
    max_pages: int | None = None
    page_size: int | None = None
    max_pdf_papers: int | None = None
    max_downloads: int | None = None
    max_file_mb: float | None = None
    include_needs_review: bool = False
    unpaywall_email: str | None = None
    bibliography_url: tuple[str, ...] = ()
    verbose: bool = False
    http_cache_dir: str | None = None
    http_cache_mode: str | None = None
    ambiguity_policy: str = AmbiguityPolicy.COLLECT_CONTENDERS
    # Optional service settings file (see paper_author_searcher.settings); None uses $PAPER_SEARCH_SETTINGS.
    settings: str | None = None
    llm_adjudication: bool = False
    # "minimal": name, university, department only. "with_hints": also the roster's ORCID/homepage/known papers.
    input_profile: str = 'minimal'
    # One shared cap for the whole benchmark run (all people), in estimated USD of metered API requests.
    max_cost_usd: float | None = 0.5
    cost_ledger: str = 'benchmark'


@dataclass(frozen=True)
class SourceSettings:
    orcid_api_url: str = 'https://pub.orcid.org/v3.0'
    orcid_bulk_size: int = 100
    crossref_api_url: str = 'https://api.crossref.org'
    crossref_rows: int = 5
    crossref_year_tolerance: int = 1
    arxiv_api_url: str = 'https://export.arxiv.org/api/query'
    arxiv_rows: int = 5
    arxiv_delay_seconds: float = 3.0


@dataclass(frozen=True)
class CalibrationSettings:
    """Identity calibration: choose matching parameters on dev people, report them on held-out test people."""

    splits: tuple[str, ...] = ('dev', 'test')
    tune_split: str = 'dev'
    holdout_split: str = 'test'
    minimum_purity: float = 0.95
    grid: Mapping[str, tuple[object, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkPaths:
    roster: Path
    snapshots: Path
    truth: Path
    runs: Path
    reports: Path


@dataclass(frozen=True)
class BenchmarkSettings:
    dataset_id: str
    year_start: int
    year_end: int
    dimensions: Mapping[str, tuple[str, ...]]
    gate_rules: tuple[GateRule, ...]
    reported_metrics: tuple[str, ...]
    paths: BenchmarkPaths
    type_policy: TypePolicy
    aggregation: Aggregation = Aggregation.MACRO
    minimum_people_per_stratum: int = 2
    # Per metric: people with fewer truth publications are left out of that metric's stratum aggregate, because
    # one publication would swing a macro average by tens of points.
    minimum_truth_positives: Mapping[str, int] = field(default_factory=dict)
    require_human_review: bool = True
    provisional_closed_world: bool = True
    version_grouping_year_tolerance: int = 2
    metadata_year_tolerance: int = 1
    run: RunSettings = field(default_factory=RunSettings)
    sources: SourceSettings = field(default_factory=SourceSettings)
    calibration: CalibrationSettings = field(default_factory=CalibrationSettings)

    def __post_init__(self) -> None:
        if self.year_start > self.year_end:
            raise ValueError('Benchmark year_start must not exceed year_end')
        if not self.dimensions or any(not values for values in self.dimensions.values()):
            raise ValueError('Benchmark dimensions need at least one value each')
        if self.minimum_people_per_stratum < 1:
            raise ValueError('minimum_people_per_stratum must be positive')
        unknown = {rule.metric for rule in self.gate_rules} - set(self.reported_metrics)
        if unknown:
            raise ValueError(f'Gate metrics must also be reported: {sorted(unknown)}')
        unknown = set(self.minimum_truth_positives) - set(self.reported_metrics)
        if unknown:
            raise ValueError(f'minimum_truth_positives names unreported metrics: {sorted(unknown)}')
        if any(value < 0 for value in self.minimum_truth_positives.values()):
            raise ValueError('minimum_truth_positives values must be nonnegative')

    def rule_aggregation(self, rule: GateRule) -> Aggregation:
        return rule.aggregation or self.aggregation

    @classmethod
    def load(cls, path: Path) -> 'BenchmarkSettings':
        path = Path(path).resolve()
        with path.open(encoding='utf-8') as handle:
            data = json.load(handle)
        return cls.from_dict(data, path.parent)

    @classmethod
    def from_dict(cls, data: dict, base: Path) -> 'BenchmarkSettings':
        if not isinstance(data, dict) or data.get('benchmark_schema_version') != 1:
            raise ValueError('Benchmark config requires benchmark_schema_version=1')
        window = data['year_window']
        paths = {key: (base / value).resolve() for key, value in data['paths'].items()}
        policy = data['publication_types']
        return cls(
            dataset_id=data['dataset_id'],
            year_start=int(window['start']),
            year_end=int(window['end']),
            dimensions={key: tuple(values) for key, values in data['dimensions'].items()},
            gate_rules=tuple(GateRule.from_dict(rule) for rule in data['gate']['rules']),
            reported_metrics=tuple(data['reported_metrics']),
            paths=BenchmarkPaths(**paths),
            type_policy=TypePolicy(
                tuple(policy['included']),
                tuple(policy['excluded']),
                bool(policy.get('unknown_included', True)),
                tuple(policy.get('non_publication_output') or ()),
            ),
            aggregation=Aggregation(data['gate'].get('aggregation', Aggregation.MACRO.value)),
            minimum_people_per_stratum=int(data['gate'].get('minimum_people_per_stratum', 2)),
            minimum_truth_positives={
                metric: int(value) for metric, value in (data['gate'].get('minimum_truth_positives') or {}).items()
            },
            require_human_review=bool(data['gate'].get('require_human_review', True)),
            provisional_closed_world=bool(data.get('provisional_closed_world', True)),
            version_grouping_year_tolerance=int(data.get('version_grouping_year_tolerance', 2)),
            metadata_year_tolerance=int(data.get('metadata_year_tolerance', 1)),
            run=_run_settings(data.get('run') or {}, base),
            sources=SourceSettings(**(data.get('sources') or {})),
            calibration=_calibration(data.get('calibration') or {}),
        )


def _calibration(values: dict[str, Any]) -> CalibrationSettings:
    values = dict(values)
    if 'splits' in values:
        values['splits'] = tuple(values['splits'])
    values['grid'] = {name: tuple(options) for name, options in (values.get('grid') or {}).items()}
    return CalibrationSettings(**values)


def _run_settings(values: dict[str, Any], base: Path) -> RunSettings:
    values = dict(values)
    values['bibliography_url'] = tuple(values.get('bibliography_url') or ())
    if values.get('http_cache_dir'):
        values['http_cache_dir'] = str((base / str(values['http_cache_dir'])).resolve())
    return RunSettings(**values)
