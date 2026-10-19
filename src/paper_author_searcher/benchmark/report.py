"""Readable summaries of a scored benchmark run."""

from paper_author_searcher.status import TruthState

from .settings import BenchmarkSettings


def _rate(value: float | None) -> str:
    return '–' if value is None else f'{value:.1%}'


def markdown_report(scored: dict, settings: BenchmarkSettings) -> str:
    gate = scored['gate']
    aggregation = settings.aggregation.value
    metrics = list(settings.reported_metrics)
    # Marks come from the gate's own checks, so the table and the verdict cannot disagree.
    failed = {(check['stratum'], check['metric']) for check in gate['failed_checks']}
    extra_aggregations: dict[str, list[str]] = {}
    for rule in settings.gate_rules:
        value = settings.rule_aggregation(rule).value
        if value != aggregation and value not in extra_aggregations.setdefault(rule.metric, []):
            extra_aggregations[rule.metric].append(value)
    lines: list[str] = [
        '# Benchmark {} — run {}'.format(scored['dataset_id'], scored['run_id']),
        '',
        '- Gate: **{}** (exit code {})'.format(gate['status'], gate['exit_code']),
        f'- Aggregation: {aggregation} over people; minimum {settings.minimum_people_per_stratum} people per stratum',
        '- Thresholds: '
        + ', '.join(
            f'{rule.metric} ({settings.rule_aggregation(rule).value}) ≥ {rule.minimum:.0%}'
            for rule in settings.gate_rules
        ),
        '- Truth: {} human-reviewed, {} assistant-reviewed, {} provisional, {} missing'.format(
            *(sum(person['truth_state'] == state for person in scored['people']) for state in TruthState)
        ),
    ]
    if settings.minimum_truth_positives:
        lines.append(
            '- Truth floor (people below it are left out of that metric): '
            + ', '.join(
                f'{metric} ≥ {floor} publications' for metric, floor in settings.minimum_truth_positives.items()
            )
        )
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
            extra = ''.join(f' · {name} {_rate(values[name])}' for name in extra_aggregations.get(metric, []))
            mark = ' ✗' if (stratum['stratum'], metric) in failed else ''
            cells.append('{} (min {}){}{}'.format(_rate(values[aggregation]), _rate(values['minimum']), extra, mark))
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
