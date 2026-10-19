"""Repeat source discovery and downloads on a saved, unchanged paper cohort."""

import argparse
import logging
import math
from pathlib import Path

from .http import HttpClient
from .logging_config import add_logging_arguments, configure_from_arguments
from .pdf_discovery_comparison import _cohort, _metadata_fingerprint, compare_runs, summarize_runs
from .pdf_downloader import PDFDownloader, paper_key
from .pdf_resolver import PDFResolver
from .settings import load_settings, validate_contact_email
from .status import OwnershipStatus, RunStatus
from .workflow import PaperSearchWorkflow, read_json, write_json
from .workflow_cli import ProgressReporter

logger = logging.getLogger(__name__)


def benchmark_inputs(baseline_directory):
    root = Path(baseline_directory)
    selected = _cohort(read_json(root / 'pdf_urls.json'))
    archive = read_json(root / 'papers.json')
    collection = archive.get('collection', archive)
    papers = collection.get('all_papers', collection.get('papers'))
    if not isinstance(papers, list):
        raise ValueError('Baseline must contain the original Collector paper list')
    if any(paper.get('ownership_status') != OwnershipStatus.VERIFIED for paper in selected.values()):
        raise ValueError('This benchmark requires a baseline with only verified ownership in its processed cohort')
    eligible = [paper for paper in papers if paper.get('ownership_status') == OwnershipStatus.VERIFIED][: len(selected)]
    if {paper_key(paper) for paper in eligible} != set(selected):
        raise ValueError('Original Collector ordering does not reproduce the baseline processed cohort')
    original_metadata = {paper_key(paper): paper for paper in eligible}
    if _metadata_fingerprint(original_metadata) != _metadata_fingerprint(selected):
        raise ValueError('Original Collector metadata differs from the baseline processed cohort')
    return archive, len(selected)


def run_benchmark(baseline_directory, output_directory, timeout, retries, progress=None, settings=None):
    settings = settings or load_settings()
    if not math.isfinite(timeout) or timeout <= 0 or retries < 0:
        raise ValueError('Timeout must be finite and positive; retries must be nonnegative')
    if not validate_contact_email(settings.credentials.unpaywall_email):
        raise ValueError('Set UNPAYWALL_EMAIL in your terminal before starting this benchmark')
    archive, cohort_size = benchmark_inputs(baseline_directory)
    baseline = summarize_runs([baseline_directory])
    client = HttpClient(timeout=timeout, retries=retries, config=settings.http)
    workflow = PaperSearchWorkflow(
        pdf_resolver=PDFResolver(client=client, config=settings.pdf, credentials=settings.credentials),
        downloader=PDFDownloader(client=client, config=settings.download),
        progress=progress,
    )
    report = workflow.run(saved=archive, output_dir=output_directory, mode='full', maximum_pdf_papers=cohort_size)
    root = Path(report['output_directory'])
    if report['run_status'] not in (RunStatus.COMPLETED, RunStatus.PARTIAL):
        raise RuntimeError('Benchmark did not finish; inspect {}'.format(root / 'report.json'))
    comparison = compare_runs([baseline_directory], [root])
    comparison['benchmark_configuration'] = {
        'timeout': timeout,
        'retries': retries,
        'cohort_size': cohort_size,
        'unpaywall_contact_configured': True,
        'baseline_http_configuration': (baseline['runs'][0]['statistics']).get('http_configuration'),
        'baseline_configuration_note': 'Older reports may omit timeout/retry settings. '
        'Use the original terminal command settings; absent settings are not inferred.',
        'method': 'Same saved Collector metadata and ownership; fresh discovery and downloads in an isolated directory',
    }
    write_json(root / 'comparison.json', comparison)
    return root, comparison


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-run', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, required=True, help='Use the baseline HTTP timeout')
    parser.add_argument('--retries', type=int, required=True, help='Use the baseline retry count')
    parser.add_argument('--verbose', action='store_true')
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    try:
        root, comparison = run_benchmark(
            args.baseline_run, args.output_dir, args.timeout, args.retries, ProgressReporter(args.verbose)
        )
    except KeyboardInterrupt:
        logger.warning('Interrupted; stage checkpoints remain in the output directory.')
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    metrics = (
        'verified_pdf_url_coverage',
        'valid_downloaded_file_coverage',
        'content_confirmed_coverage',
        'needs_review_ratio_among_valid_files',
        'search_completion_rate',
    )
    for metric in metrics:
        before = comparison['before'][metric]
        after = comparison['after'][metric]
        print(
            '{}: {}/{} -> {}/{}'.format(
                metric, before['numerator'], before['denominator'], after['numerator'], after['denominator']
            )
        )
    print(f'Comparison and stage reports: {root}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
