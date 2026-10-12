"""Read saved traces to explain incomplete searches without making new requests."""
import argparse
from collections import Counter
from pathlib import Path

from .pdf_discovery_comparison import _cohort, compare_runs
from .pdf_search_status import paper_search_complete, provider_search_complete
from .workflow import read_json, write_json


def solely_deferred(paper):
    incomplete = [provider for provider in paper.get('providers') or [] if not provider_search_complete(provider)]
    if not incomplete:
        return False
    for provider in incomplete:
        warnings = [item for item in provider.get('warnings') or [] if item.get('incomplete')]
        if provider.get('errors') or not warnings:
            return False
        if any(item.get('code') != 'source_deferred' for item in warnings):
            return False
        kinds = {item.get('error_type') for item in provider.get('diagnostics') or [] if item.get('error_type')}
        if kinds - {'source_deferred', 'not_found'}:
            return False
    return True


def _resource_statistics(statistics):
    if not statistics:
        return {'available': False, 'note': 'Legacy report has no public-resource request trace'}
    events = statistics.get('events') or []
    failures = [event for event in events if event.get('error_type') != 'source_deferred']
    legacy_deferrals = [event for event in events if event.get('error_type') == 'source_deferred']
    deferrals = statistics.get('deferrals')
    if deferrals is None:
        deferrals = legacy_deferrals
    return {
        'available': True, 'logical_probe_calls_started': statistics.get('requests'),
        'terminal_request_failures': statistics.get('request_failures', len(failures)),
        'failure_types': dict(Counter(event.get('error_type') for event in failures)),
        'failure_hosts': dict(Counter(event.get('host') for event in failures)),
        'terminal_429_failures': sum(event.get('status_code') == 429 for event in failures),
        'cooldown_skips': statistics.get('cooldown_skips', 0),
        'recorded_redirect_deferrals': statistics.get('deferred_after_redirect', len(legacy_deferrals)),
        'cache_hits': statistics.get('cache_hits', 0),
        'cache_response_hits': statistics.get('cache_response_hits'),
        'cache_error_hits': statistics.get('cache_error_hits'),
        'expanded_page_reads': statistics.get('expanded_page_reads', 0),
        'deferral_hosts': dict(Counter(event.get('host') or event.get('endpoint_host') for event in deferrals)),
        'scope_note': 'Terminal failures count logical probe calls. Historical traces do not reveal every HTTP retry/hop. '
                      'Legacy source_deferred events are excluded from request failures; legacy cache outcomes cannot be split.',
    }


def _trace_summary(pdf_report, papers):
    statuses = {}
    warning_counts = Counter()
    warning_papers = Counter()
    for paper in papers.values():
        codes = set()
        for provider in paper.get('providers') or []:
            statuses.setdefault(provider['source'], Counter())[provider.get('status')] += 1
            for warning in provider.get('warnings') or []:
                if warning.get('incomplete'):
                    warning_counts[warning['code']] += 1
                    codes.add(warning['code'])
        warning_papers.update(codes)
    statistics = pdf_report.get('statistics') or {}
    requests = statistics.get('provider_request_statistics') or {}
    semantic = requests.get('semantic_scholar') or {}
    semantic_failures = semantic.get('request_failures')
    if semantic_failures is None and 'events' in semantic:
        semantic_failures = len(semantic['events'])
    return {
        'provider_status_counts': {source: dict(counts) for source, counts in statuses.items()},
        'complete_papers': sum(paper_search_complete(paper.get('providers') or []) for paper in papers.values()),
        'incomplete_only_due_to_deferral_papers': sum(solely_deferred(paper) for paper in papers.values()),
        'incomplete_warning_observations': dict(warning_counts), 'papers_with_warning': dict(warning_papers),
        'public_resources': _resource_statistics(requests.get('public_resources')),
        'semantic_scholar': {
            'terminal_request_failures': semantic_failures,
            'cooldown_skips': semantic.get('cooldown_skips'), 'work_cache_hits': semantic.get('work_cache_hits'),
            'title_cache_hits': semantic.get('title_cache_hits'),
        },
        'http_configuration': statistics.get('http_configuration'),
        'http_transport_statistics': statistics.get('http_transport_statistics'),
    }


def diagnose_search(before_directory, after_directory):
    comparison = compare_runs([before_directory], [after_directory])
    before_report = read_json(Path(before_directory) / 'pdf_urls.json')
    after_report = read_json(Path(after_directory) / 'pdf_urls.json')
    before, after = _cohort(before_report), _cohort(after_report)
    before_complete = {key for key, paper in before.items() if paper_search_complete(paper.get('providers') or [])}
    after_complete = {key for key, paper in after.items() if paper_search_complete(paper.get('providers') or [])}
    lost = []
    for key in sorted(before_complete - after_complete):
        paper = after[key]
        lost.append({'paper_key': key, 'title': paper.get('title'), 'solely_deferred': solely_deferred(paper),
                     'incomplete_providers': [provider for provider in paper.get('providers') or []
                                              if not provider_search_complete(provider)]})
    return {
        'report_type': 'offline_pdf_search_diagnostics', 'new_network_requests': 0,
        'comparison': comparison, 'before_trace': _trace_summary(before_report, before),
        'after_trace': _trace_summary(after_report, after),
        'completeness_transition': {
            'remained_complete': len(before_complete & after_complete),
            'became_complete': len(after_complete - before_complete), 'lost_complete': len(lost),
            'lost_complete_only_due_to_deferral': sum(paper['solely_deferred'] for paper in lost),
            'lost_complete_papers': lost,
        },
        'attribution_note': 'Deferral-only means the recorded reason for incompleteness is cooling, '
                            'not that the skipped query would have succeeded. Search satisfaction does not change completion.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = [root / name for root in (args.before, args.after)
              for name in ('pdf_urls.json', 'manifest.json', 'report.json', 'comparison.json', 'papers.json')]
    if args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error('Diagnostic output must not overwrite original artifacts')
    try:
        result = diagnose_search(args.before, args.after)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print('Completeness transitions: retained={}, gained={}, lost={}, lost solely to deferral={}'.format(
        result['completeness_transition']['remained_complete'], result['completeness_transition']['became_complete'],
        result['completeness_transition']['lost_complete'], result['completeness_transition']['lost_complete_only_due_to_deferral']))
    print('Offline report: {}'.format(args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
