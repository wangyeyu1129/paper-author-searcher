"""Stratified recall/precision/full-text benchmark with a configurable release gate."""

from .gate import GateResult, GateStatus, ReleaseGate
from .metrics import BenchmarkMetric, MetricInput, benchmark_metrics
from .roster import BenchmarkPerson, Roster, SearchInput, TruthSourceSpec
from .runner import BenchmarkRunner, SearchSystem, WorkflowSearchSystem
from .scoring import OutputScope, PersonScore, PersonScorer, aggregate, scope_collection
from .settings import BenchmarkSettings
from .truth_builder import TruthBuilder
from .truth_sources import TruthRecord, TruthSource, TruthSourceRegistry, default_registry

__all__ = [
    'BenchmarkMetric',
    'BenchmarkPerson',
    'BenchmarkRunner',
    'BenchmarkSettings',
    'GateResult',
    'GateStatus',
    'MetricInput',
    'OutputScope',
    'PersonScore',
    'PersonScorer',
    'ReleaseGate',
    'Roster',
    'SearchInput',
    'SearchSystem',
    'TruthBuilder',
    'TruthRecord',
    'TruthSource',
    'TruthSourceRegistry',
    'TruthSourceSpec',
    'WorkflowSearchSystem',
    'aggregate',
    'benchmark_metrics',
    'default_registry',
    'scope_collection',
]
