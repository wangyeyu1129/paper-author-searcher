"""Prepare public-source bibliography references for human review, never invent labels."""

import argparse
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_MATCHING, DEFAULT_PDF
from .http import HttpClient, SourceError
from .logging_config import add_logging_arguments, configure_from_arguments
from .matching import name_similarity
from .paper_authorship import byline_forms
from .paper_providers import parse_year
from .pdf_providers import CitationMetadata
from .workflow import read_json, write_json

logger = logging.getLogger(__name__)


def prepare_reference(pages, professor_name, client=None):
    client = client or HttpClient()
    entries = []
    errors = []
    for page in pages:
        url = page['url']
        try:
            response = client.probe(
                url, DEFAULT_PDF.preview_bytes, DEFAULT_PDF.catalog_preview_bytes, DEFAULT_PDF.maximum_redirects
            )
            if response.content_type not in ('text/html', 'application/xhtml+xml'):
                raise SourceError('Reference page did not return HTML metadata')
            if len(response.prefix) >= DEFAULT_PDF.catalog_preview_bytes:
                raise SourceError('Reference HTML exceeds the configured preview limit')
            parser = CitationMetadata()
            parser.feed(response.prefix.decode('utf-8', errors='replace'))
            values = parser.values
            titles = list(dict.fromkeys(values.get('citation_title') or []))
            if len(titles) != 1:
                raise SourceError('Reference page lacks a unique citation title')
            original_authors = values.get('citation_author') or []
            authors = [byline_forms(author)[-1] for author in original_authors]
            date = (values.get('citation_publication_date') or values.get('citation_date') or [''])[0]
            year = parse_year(date[:4])
            doi = (values.get('citation_doi') or [None])[0]
            arxiv = (values.get('citation_arxiv_id') or [None])[0]
            if not doi and (not authors or year is None):
                raise SourceError('Reference page lacks a DOI or complete title/year/authors metadata')
            compatible = any(
                name_similarity(professor_name, name) >= DEFAULT_MATCHING.compatible_name for name in authors
            )
            entries.append(
                {
                    'evaluation_id': 'reference-' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:16],
                    'ownership': 'uncertain',
                    'proposed_ownership': 'belongs' if compatible else 'uncertain',
                    'annotation_status': 'pending_human_review',
                    'title': titles[0],
                    'authors': authors,
                    'year': year,
                    'original_authors': original_authors,
                    **({'doi': doi} if doi else {}),
                    **({'external_ids': {'arxiv': arxiv}} if arxiv else {}),
                    'annotation_evidence': [
                        'Public publisher/preprint citation metadata: ' + response.url,
                        'Bibliography/source context: ' + page.get('context', ''),
                    ],
                    'reference_url': response.url,
                    'observed_pdf_links': values.get('citation_pdf_url') or [],
                }
            )
        except (SourceError, ValueError, TypeError) as exc:
            errors.append({'url': url, 'error': str(exc)})
    return {
        'schema_version': 1,
        'complete_positive_list': False,
        'metadata': {
            'annotation_state': 'pending_human_review',
            'reviewer': '',
            'reviewed_at': '',
            'prepared_at': datetime.now(timezone.utc).isoformat(),
            'professor_name': professor_name,
            'scope': 'Explicit input bibliography pages; not a complete professor bibliography',
            'provenance': 'Citation metadata extracted from public source pages; not human adjudication',
        },
        'papers': entries,
        'import_errors': errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare publisher metadata references for human ground truth review')
    parser.add_argument('--pages', type=Path, required=True, help='JSON object with pages=[{url,context}]')
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--retries', type=int)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    if args.pages.resolve() == args.output.resolve():
        parser.error('Output must differ from source input')
    try:
        saved = read_json(args.pages)
        report = prepare_reference(saved['pages'], args.name, HttpClient(timeout=args.timeout, retries=args.retries))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(
        'Prepared {} pending references; {} import errors. No human labels were supplied.'.format(
            len(report['papers']), len(report['import_errors'])
        )
    )
    return 2 if report['import_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
