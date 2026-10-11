"""Offline evaluation CLI; importing this module does not contact any API."""
import argparse
import json
from pathlib import Path

from .evaluation import Evaluator
from .evaluation_matching import GroundTruth


def main(argv=None):
    parser = argparse.ArgumentParser(description='Evaluate a saved collection against human ground truth')
    parser.add_argument('--result', type=Path, required=True, help='Saved resolver/collection JSON')
    parser.add_argument('--ground-truth', type=Path, required=True, help='Human-adjudicated ground truth JSON')
    parser.add_argument('--output', type=Path, help='Save detailed evaluation report as JSON')
    args = parser.parse_args(argv)
    try:
        saved = json.loads(args.result.read_text(encoding='utf-8'))
        truth = GroundTruth.from_dict(json.loads(args.ground_truth.read_text(encoding='utf-8')))
        report = Evaluator().evaluate(saved, truth)
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    deduplication = report['deduplication']
    if deduplication is not None:
        deduplication = {key: value for key, value in deduplication.items()
                         if key not in ('incorrect_merges', 'missed_duplicates')}
    retrieval = dict(report['retrieval'])
    retrieval['sources'] = [
        {key: value for key, value in source.items() if key != 'rejected_records'}
        for source in retrieval.get('sources', [])
    ]
    brief = {
        'professor_resolver': report['professor_resolver'],
        'ownership': report['ownership'],
        'all_candidates': report['all_candidates'],
        'by_status': report['by_status'],
        'deduplication': deduplication,
        'retrieval': retrieval,
        'judgment_coverage': report['judgment_coverage'],
        'manual_confirmation_count': len(report['manual_confirmation']),
        'detailed_report': str(args.output) if args.output else None,
    }
    if not args.output:
        brief = report
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
