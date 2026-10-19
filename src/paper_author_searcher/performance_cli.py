"""Compare saved stages; this evaluation command never requests scholarly APIs."""

import argparse
import logging
from pathlib import Path

from .jsonio import read_json, write_json
from .logging_config import add_logging_arguments, configure_from_arguments
from .performance_evaluation import PerformanceEvaluator, compare_performance

logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Evaluate discovery, ownership, PDF availability and actual downloads')
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--ground-truth', type=Path, required=True)
    parser.add_argument('--pdf-report', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument(
        '--reference-only', action='store_true', help='Provisional source reference; never claims human GT attainment'
    )
    parser.add_argument('--before', type=Path, help='Previous performance report with the identical reference set')
    parser.add_argument('--output', type=Path, required=True)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    inputs = [
        path for path in (args.collection, args.ground_truth, args.pdf_report, args.manifest, args.before) if path
    ]
    if args.output.resolve() in [path.resolve() for path in inputs]:
        parser.error('Output must differ from input artifacts')
    try:
        report = PerformanceEvaluator().evaluate(
            read_json(args.collection),
            read_json(args.ground_truth),
            read_json(args.pdf_report) if args.pdf_report else None,
            read_json(args.manifest) if args.manifest else None,
            args.manifest,
            reference_only=args.reference_only,
        )
        if args.before:
            report['comparison'] = compare_performance(read_json(args.before), report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print('Scope: {}'.format(report['metric_scope']))
    for key, metric in report['metrics'].items():
        value = '{:.1%}'.format(metric['rate']) if metric['rate'] is not None else 'not assessable'
        print('{}: {} ({}/{})'.format(key, value, metric['numerator'], metric['denominator']))
    print('Human Ground Truth 80% target formally met: {}'.format(report['recall_target']['formally_met']))
    print(f'Report: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
