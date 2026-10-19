"""Metrics the benchmark computes itself, on top of the PerformanceEvaluator's per-publication metrics.

Each metric is a strategy with a stable name; ``benchmark_metrics`` builds the configured set, so adding a metric
means adding a class and registering it, not editing the scorer.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from .settings import BenchmarkSettings

RESOLVED_CONFIRMATIONS = frozenset({'resolver_resolved', 'manual_author_id'})


@dataclass(frozen=True)
class MetricInput:
    """What one person's run produced, as the scorer sees it."""

    collection: Mapping[str, Any] | None
    evaluation: Mapping[str, Any]
    truth_positives: int


def rate(numerator: int, denominator: int) -> dict:
    return {
        'numerator': numerator,
        'denominator': denominator,
        'rate': numerator / denominator if denominator else None,
        'available': True,
    }


def identity_resolved(collection: Mapping[str, Any] | None) -> bool:
    inner = (collection or {}).get('collection') or {}
    return inner.get('identity_confirmation') in RESOLVED_CONFIRMATIONS


class BenchmarkMetric(ABC):
    name: ClassVar[str]

    @abstractmethod
    def compute(self, data: MetricInput) -> dict:
        """Return ``{'numerator', 'denominator', 'rate', 'available'}`` for one person."""


class IdentityResolutionRate(BenchmarkMetric):
    """1/1 when the system committed to an identity it confirmed itself; 0/1 when it stopped or only collected
    competing profiles. Measured per person, so the stratum macro is the share of people resolved."""

    name = 'identity_resolution_rate'

    def compute(self, data: MetricInput) -> dict:
        return rate(int(identity_resolved(data.collection)), 1)


class IdentityPrecision(BenchmarkMetric):
    """Of the people the system resolved, the share resolved to the right person.

    A resolved identity is right when its collected papers include at least ``minimum_truth_share`` of the
    person's truth publications. Resolving to a namesake is worse than not resolving: the output then describes
    someone else. People not resolved, or without truth, have no rate.
    """

    name = 'identity_precision'

    def __init__(self, minimum_truth_share: float) -> None:
        if not 0 < minimum_truth_share <= 1:
            raise ValueError('minimum_truth_share must be in (0, 1]')
        self.minimum_truth_share = minimum_truth_share

    def compute(self, data: MetricInput) -> dict:
        if not identity_resolved(data.collection) or not data.truth_positives:
            return rate(0, 0)
        discovered = data.evaluation['metrics']['paper_discovery_recall']['numerator']
        return rate(int(discovered >= self.minimum_truth_share * data.truth_positives), 1)


def benchmark_metrics(settings: BenchmarkSettings) -> dict[str, BenchmarkMetric]:
    metrics: list[BenchmarkMetric] = [
        IdentityResolutionRate(),
        IdentityPrecision(settings.identity.minimum_truth_share),
    ]
    return {metric.name: metric for metric in metrics}
