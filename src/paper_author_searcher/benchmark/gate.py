"""Release gate: every configured stratum must reach every configured metric threshold."""

from dataclasses import dataclass, field
from enum import Enum

from .settings import BenchmarkSettings


class GateStatus(str, Enum):
    PASSED = 'passed'
    FAILED = 'failed'
    NOT_CERTIFIABLE = 'not_certifiable'


EXIT_CODES = {GateStatus.PASSED: 0, GateStatus.FAILED: 1, GateStatus.NOT_CERTIFIABLE: 3}


@dataclass
class GateResult:
    status: GateStatus
    checks: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def to_dict(self) -> dict:
        return {
            'status': self.status.value,
            'exit_code': self.exit_code,
            'reasons': self.reasons,
            'failed_checks': [check for check in self.checks if not check['passed']],
            'checks': self.checks,
        }


class ReleaseGate:
    def __init__(self, settings: BenchmarkSettings) -> None:
        self.settings = settings

    def evaluate(self, aggregates: dict[tuple[str, str], dict]) -> GateResult:
        aggregation = self.settings.aggregation.value
        checks = []
        for (dimension, value), stratum in sorted(aggregates.items()):
            for rule in self.settings.gate_rules:
                metric = stratum['metrics'][rule.metric]
                observed = metric[aggregation]
                enough_people = metric['people_with_rate'] >= self.settings.minimum_people_per_stratum
                passed = observed is not None and observed >= rule.minimum and enough_people
                reason = ''
                if observed is None:
                    reason = 'metric unavailable'
                elif not enough_people:
                    reason = 'only {} people with a rate; need {}'.format(
                        metric['people_with_rate'], self.settings.minimum_people_per_stratum
                    )
                elif observed < rule.minimum:
                    reason = 'below threshold'
                checks.append(
                    {
                        'stratum': f'{dimension}={value}',
                        'metric': rule.metric,
                        'label': rule.label or rule.metric,
                        'aggregation': aggregation,
                        'observed': observed,
                        'minimum': rule.minimum,
                        'worst_person_rate': metric['minimum'],
                        'people_with_rate': metric['people_with_rate'],
                        'passed': passed,
                        'reason': reason,
                    }
                )
        reasons = []
        if any(not check['passed'] for check in checks):
            status = GateStatus.FAILED
            reasons.append(
                '{} of {} stratum checks failed'.format(sum(not check['passed'] for check in checks), len(checks))
            )
        else:
            status = GateStatus.PASSED
        provisional = sum(stratum['provisional_people'] for key, stratum in aggregates.items() if key[0] == 'overall')
        if provisional and self.settings.require_human_review:
            reasons.append(f'{provisional} people are scored against provisional (not human-reviewed) truth')
            if status is GateStatus.PASSED:
                status = GateStatus.NOT_CERTIFIABLE
        return GateResult(status, checks, reasons)
