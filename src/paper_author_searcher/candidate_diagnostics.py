"""Inspect unconfirmed author bibliographies separately; never authorize downloads."""

import argparse
import logging
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Sequence

from .composition import RunOptions, ServiceContainer
from .config import DEFAULT_DEDUPLICATION, DeduplicationConfig
from .json_types import JSONObject
from .jsonio import read_json, write_json
from .logging_config import add_logging_arguments, configure_from_arguments, log_isolated_failure
from .models import PaperCollectionResult, PaperSourceResult, ProfessorProfile
from .paper_deduplication import deduplicate_papers
from .settings import add_settings_argument, load_settings
from .status import OwnershipStatus, ResolutionStatus
from .workflow import _resolution

logger = logging.getLogger(__name__)


def collect_candidate(
    profile: ProfessorProfile, providers: Sequence[Any], deduplication: DeduplicationConfig = DEFAULT_DEDUPLICATION
) -> JSONObject:
    sources = []
    records = []
    for provider in providers:
        author_id = profile.author_ids.get(provider.name)
        if not author_id:
            continue
        try:
            fetched = provider.collect(author_id)
        except Exception as exc:
            log_isolated_failure(logger, 'Paper source %s failed for author %s: %s', provider.name, author_id, exc)
            fetched = PaperSourceResult(provider.name, author_id, error=str(exc))
        sources.append(fetched)
        records.extend(fetched.papers)
    papers = deduplicate_papers(records, deduplication)
    for paper in papers:
        evidence = 'Professor identity unconfirmed; candidate-specific diagnostic only'
        paper.ownership_status = OwnershipStatus.NEEDS_REVIEW
        paper.ownership_evidence.append(evidence)
        for record in paper.records:
            record.ownership_status = OwnershipStatus.NEEDS_REVIEW
            record.ownership_evidence.append(evidence)
    identity = deepcopy(profile)
    identity.status = ResolutionStatus.AMBIGUOUS
    collection = PaperCollectionResult(
        papers,
        sources,
        bool(sources) and all(source.complete for source in sources),
        {source: [author] for source, author in profile.author_ids.items()},
        'unconfirmed_candidate_diagnostic',
        ResolutionStatus.AMBIGUOUS,
    )
    return {
        'schema_version': 1,
        'diagnostic_scope': 'unconfirmed_candidate_never_auto_selected',
        'profile': asdict(identity),
        'original_candidate_profile': asdict(profile),
        'collection': collection.to_dict(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Collect competing candidate records separately without confirming identity'
    )
    add_settings_argument(parser)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--retries', type=int)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    try:
        result = _resolution(read_json(args.identity))
        container = ServiceContainer(load_settings(args), RunOptions(timeout=args.timeout, retries=args.retries))
        providers = container.paper_providers
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for index, profile in enumerate(result.candidates[:2], 1):
            data = collect_candidate(profile, providers, container.settings.deduplication)
            path = args.output_dir / (f'candidate_{index}.json')
            if path.resolve() == args.identity.resolve():
                raise ValueError('Output must differ from identity checkpoint')
            write_json(path, data)
            print(
                '{}: {} records; identity remains ambiguous'.format(
                    path, data['collection']['statistics']['raw_records_received']
                )
            )
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
