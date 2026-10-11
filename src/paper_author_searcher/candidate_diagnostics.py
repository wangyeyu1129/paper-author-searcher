"""Inspect unconfirmed author bibliographies separately; never authorize downloads."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from .http import HttpClient
from .models import PaperCollectionResult, PaperSourceResult
from .paper_deduplication import deduplicate_papers
from .paper_providers import OpenAlexPaperProvider, SemanticScholarPaperProvider, DBLPPaperProvider
from .providers import OpenAlexProvider, SemanticScholarProvider, DBLPProvider
from .workflow import _resolution, read_json, write_json


def collect_candidate(profile, providers):
    sources = []
    records = []
    for provider in providers:
        author_id = profile.author_ids.get(provider.name)
        if not author_id:
            continue
        try:
            fetched = provider.collect(author_id)
        except Exception as exc:
            fetched = PaperSourceResult(provider.name, author_id, error=str(exc))
        sources.append(fetched)
        records.extend(fetched.papers)
    papers = deduplicate_papers(records)
    for paper in papers:
        evidence = 'Professor identity unconfirmed; candidate-specific diagnostic only'
        paper.ownership_status = 'needs_review'
        paper.ownership_evidence.append(evidence)
        for record in paper.records:
            record.ownership_status = 'needs_review'
            record.ownership_evidence.append(evidence)
    identity = deepcopy(profile)
    identity.status = 'ambiguous'
    collection = PaperCollectionResult(
        papers, sources, bool(sources) and all(source.complete for source in sources),
        {source: [author] for source, author in profile.author_ids.items()},
        'unconfirmed_candidate_diagnostic', 'ambiguous')
    return {'schema_version': 1, 'diagnostic_scope': 'unconfirmed_candidate_never_auto_selected',
            'profile': asdict(identity), 'original_candidate_profile': asdict(profile),
            'collection': collection.to_dict()}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect competing candidate records separately without confirming identity')
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--retries', type=int)
    args = parser.parse_args(argv)
    try:
        result = _resolution(read_json(args.identity))
        client = HttpClient(timeout=args.timeout, retries=args.retries)
        providers = [OpenAlexPaperProvider(OpenAlexProvider(client=client)),
                     SemanticScholarPaperProvider(SemanticScholarProvider(client=client)),
                     DBLPPaperProvider(DBLPProvider(client=client))]
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for index, profile in enumerate(result.candidates[:2], 1):
            data = collect_candidate(profile, providers)
            path = args.output_dir / ('candidate_{}.json'.format(index))
            if path.resolve() == args.identity.resolve():
                raise ValueError('Output must differ from identity checkpoint')
            write_json(path, data)
            print('{}: {} records; identity remains ambiguous'.format(
                path, data['collection']['statistics']['raw_records_received']))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
