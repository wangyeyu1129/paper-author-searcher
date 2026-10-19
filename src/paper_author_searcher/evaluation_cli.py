"""Offline evaluation CLI; importing this module does not contact any API."""

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from .evaluation import Evaluator
from .evaluation_matching import GroundTruth
from .logging_config import add_logging_arguments, configure_from_arguments
from .status import AnnotationState

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Evaluate a saved collection against human ground truth')
    parser.add_argument('--result', type=Path, required=True, help='Saved resolver/collection JSON')
    parser.add_argument('--ground-truth', type=Path, required=True, help='Human-adjudicated ground truth JSON')
    parser.add_argument('--output', type=Path, help='Save detailed evaluation report as JSON')
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    try:
        saved = json.loads(args.result.read_text(encoding='utf-8'))
        truth_data = json.loads(args.ground_truth.read_text(encoding='utf-8'))
        metadata = truth_data.get('metadata') or {}
        if 'formal_evaluation_ready' in metadata:
            raise ValueError(
                'Pilot reference sets require the independent result audit; '
                'use python -m scripts.ground_truth.evaluate_pilot'
            )
        if metadata.get('annotation_state') in (
            AnnotationState.CANDIDATE_COLLECTION,
            AnnotationState.PARTIALLY_HUMAN_REVIEWED,
            AnnotationState.REVIEW_INCOMPLETE,
        ):
            raise ValueError('Ground Truth human review is unfinished; formal Precision/Recall is unavailable')
        truth = GroundTruth.from_dict(truth_data)
        report = Evaluator().evaluate(saved, truth)
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    deduplication = report['deduplication']
    if deduplication is not None:
        deduplication = {
            key: value for key, value in deduplication.items() if key not in ('incorrect_merges', 'missed_duplicates')
        }
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
