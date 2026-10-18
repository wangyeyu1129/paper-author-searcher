"""Stratified recall/precision/full-text benchmark with a configurable release gate."""
from .gate import GateResult, GateStatus, ReleaseGate
from .roster import BenchmarkPerson, Roster, SearchInput, TruthSourceSpec
from .runner import BenchmarkRunner, SearchSystem, WorkflowSearchSystem
from .scoring import PersonScore, PersonScorer, aggregate
from .settings import BenchmarkSettings
from .truth_builder import TruthBuilder
from .truth_sources import TruthRecord, TruthSource, TruthSourceRegistry, default_registry

__all__ = [
    'BenchmarkPerson', 'BenchmarkRunner', 'BenchmarkSettings', 'GateResult', 'GateStatus', 'PersonScore',
    'PersonScorer', 'ReleaseGate', 'Roster', 'SearchInput', 'SearchSystem', 'TruthBuilder', 'TruthRecord',
    'TruthSource', 'TruthSourceRegistry', 'TruthSourceSpec', 'WorkflowSearchSystem', 'aggregate',
    'default_registry',
]
