"""Readable summaries of a scored benchmark run."""

from typing import List, Optional

from paper_author_searcher.status import TruthState

from .settings import BenchmarkSettings


def _rate(value: Optional[float]) -> str:
    return '–' if value is None else f'{value:.1%}'


def markdown_report(scored: dict, settings: BenchmarkSettings) -> str:
    gate = scored['gate']
    aggregation = settings.aggregation.value
    metrics = list(settings.reported_metrics)
    thresholds = {rule.metric: rule.minimum for rule in settings.gate_rules}
    lines: List[str] = [
        '# Benchmark {} — run {}'.format(scored['dataset_id'], scored['run_id']),
        '',
        '- Gate: **{}** (exit code {})'.format(gate['status'], gate['exit_code']),
        f'- Aggregation: {aggregation} over people; minimum {settings.minimum_people_per_stratum} people per stratum',
        '- Thresholds: ' + ', '.join(f'{metric} ≥ {minimum:.0%}' for metric, minimum in thresholds.items()),
        '- Truth: {} human-reviewed, {} assistant-reviewed, {} provisional, {} missing'.format(
            *(sum(person['truth_state'] == state for person in scored['people']) for state in TruthState)
        ),
    ]
    lines.extend('- ' + reason for reason in gate['reasons'])
    lines += [
        '',
        '## Strata',
        '',
        '| Stratum | People | ' + ' | '.join(metrics) + ' |',
        '|---|---:|' + '---:|' * len(metrics),
    ]
    for stratum in scored['strata']:
        cells = []
        for metric in metrics:
            values = stratum['metrics'][metric]
            mark = ''
            if metric in thresholds and (values[aggregation] is None or values[aggregation] < thresholds[metric]):
                mark = ' ✗'
            cells.append('{} (min {}){}'.format(_rate(values[aggregation]), _rate(values['minimum']), mark))
        lines.append('| {} | {} | {} |'.format(stratum['stratum'], stratum['people'], ' | '.join(cells)))
    lines += [
        '',
        '## People',
        '',
        '| Person | Strata | Truth | Run | Positives | ' + ' | '.join(metrics) + ' |',
        '|---|---|---|---|---:|' + '---:|' * len(metrics),
    ]
    for person in scored['people']:
        lines.append(
            '| {} | {} | {} | {} | {} | {} |'.format(
                person['person_id'],
                ', '.join(person['strata'].values()),
                person['truth_state'],
                person['run_status'],
                person['truth_positives'],
                ' | '.join(
                    '{} ({}/{})'.format(
                        _rate(person['metrics'][metric]['rate']),
                        person['metrics'][metric]['numerator'],
                        person['metrics'][metric]['denominator'],
                    )
                    for metric in metrics
                ),
            )
        )
    lines += [
        '',
        'Provisional truth uses proposed labels from researcher-maintained sources and is not a release '
        'certification. Precision under provisional truth treats the source union as complete and is a '
        'lower bound.',
        '',
    ]
    return '\n'.join(lines)
