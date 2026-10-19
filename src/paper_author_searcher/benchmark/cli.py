"""paper-benchmark: build truth, run the searcher on every person, score by stratum, enforce the gate."""

import argparse
import logging
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.http import HttpClient, SourceError
from paper_author_searcher.jsonio import read_json, write_json
from paper_author_searcher.logging_config import add_logging_arguments, configure_from_arguments, log_isolated_failure
from paper_author_searcher.settings import load_settings
from paper_author_searcher.status import AnnotationState, OwnershipStatus, RunStatus, TruthState

from .gate import ReleaseGate
from .report import markdown_report
from .review import export_review, import_review
from .roster import Roster
from .runner import BenchmarkRunner, WorkflowSearchSystem, load_index
from .scoring import OVERALL, PersonScorer, aggregate, is_human_reviewed, load_truth, truth_paths
from .settings import BenchmarkSettings
from .snapshots import ArxivLookup, CrossrefLookup, OrcidFetcher, SnapshotCollector, missing_snapshots
from .truth_builder import TruthBuilder
from .truth_sources import default_registry

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path('data/benchmark/v1/benchmark.json')


def _context(args):
    settings = BenchmarkSettings.load(args.config)
    roster = Roster.load(settings.paths.roster)
    registry = default_registry()
    warnings = roster.validate(settings, registry.kinds())
    return settings, roster, registry, warnings


def _code_version() -> Optional[str]:
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def command_validate(args) -> int:
    settings, roster, _registry, warnings = _context(args)
    print(f'Roster {roster.dataset_id}: {len(roster.people)} people')
    for (dimension, value), count in sorted(roster.stratum_sizes(settings).items()):
        print(f'  {dimension}={value}: {count}')
    problems = 0
    for person in roster.people:
        missing = missing_snapshots(person, settings.paths.snapshots / person.person_id)
        truth = load_truth(settings, person.person_id)
        state = (
            TruthState.MISSING
            if truth is None
            else (AnnotationState.HUMAN_REVIEWED if is_human_reviewed(truth) else 'pending')
        )
        if missing or truth is None:
            problems += 1
            print(f'  {person.person_id}: snapshots missing={missing} truth={state}')
    for warning in warnings:
        logger.warning(warning)
    return 0 if not problems and not warnings else 2


def command_fetch(args) -> int:
    settings, roster, registry, _warnings = _context(args)
    client = HttpClient(timeout=args.timeout, retries=args.retries)
    collector = SnapshotCollector(
        settings,
        registry,
        OrcidFetcher(settings, client),
        None if args.no_crossref else CrossrefLookup(settings, client, load_settings().credentials.crossref_email),
        None if args.no_arxiv else ArxivLookup(settings, client),
    )
    failures = 0
    for person in roster.select(args.person):
        try:
            print(collector.collect(person), flush=True)
        except (SourceError, OSError, ValueError) as exc:
            failures += 1
            log_isolated_failure(logger, 'Snapshot collection failed for %s: %s', person.person_id, exc)
    return 0 if not failures else 2


def command_build(args) -> int:
    settings, roster, registry, _warnings = _context(args)
    builder = TruthBuilder(settings, registry)
    settings.paths.truth.mkdir(parents=True, exist_ok=True)
    for person in roster.select(args.person):
        missing = missing_snapshots(person, settings.paths.snapshots / person.person_id)
        if missing:
            logger.warning('%s: skipped, missing snapshots %s', person.person_id, missing)
            continue
        truth = builder.build(person)
        _reviewed, pending = truth_paths(settings, person.person_id)
        write_json(pending, truth)
        flags = sum(bool(paper['review_flags']) for paper in truth['papers'])
        print(
            '{}: {} publications ({} flagged), excluded {}'.format(
                person.person_id, len(truth['papers']), flags, truth['metadata']['excluded_counts']
            )
        )
    return 0


def command_review_export(args) -> int:
    settings, _roster, _registry, _warnings = _context(args)
    reviewed, pending = truth_paths(settings, args.person)
    source = reviewed if reviewed.exists() else pending
    truth = read_json(source)
    suggestions = []
    if args.suggest_from_run:
        from .review import system_suggestions

        args.suggest_status = args.suggest_status or [OwnershipStatus.VERIFIED]
        entry = load_index(settings, args.suggest_from_run)['people'].get(args.person) or {}
        if entry.get('report'):
            report_dir = settings.paths.runs / args.suggest_from_run / entry['report']
            artifacts = read_json(report_dir / 'report.json').get('artifacts') or {}
            if artifacts.get('papers'):
                person = roster_person(settings, args.person)
                suggestions = system_suggestions(
                    read_json(report_dir / artifacts['papers']),
                    truth,
                    person.window(settings),
                    args.suggest_from_run,
                    args.suggestion_limit,
                    tuple(args.suggest_status),
                )
    count = export_review(truth, args.output, suggestions)
    print(f'Exported {count} rows ({len(suggestions)} system suggestions) from {source} to {args.output}')
    return 0


def roster_person(settings, person_id):
    return Roster.load(settings.paths.roster).select([person_id])[0]


def command_review_import(args) -> int:
    settings, _roster, _registry, _warnings = _context(args)
    reviewed_path, pending_path = truth_paths(settings, args.person)
    truth = read_json(reviewed_path if reviewed_path.exists() else pending_path)
    result = import_review(
        truth,
        args.sheet,
        args.reviewer,
        args.complete_positive_list,
        allow_pending=args.allow_pending,
        review_state=args.review_state,
    )
    target = (
        reviewed_path
        if result['metadata']['annotation_state']
        in (AnnotationState.HUMAN_REVIEWED, AnnotationState.ASSISTANT_REVIEWED)
        else pending_path
    )
    write_json(target, result)
    print('Saved {} ({})'.format(target, result['metadata']['review_progress']))
    return 0


def command_run(args) -> int:
    settings, roster, _registry, warnings = _context(args)
    for warning in warnings:
        logger.warning(warning)
    run = settings.run if args.mode is None else replace(settings.run, mode=args.mode)
    if args.input_profile:
        run = replace(run, input_profile=args.input_profile)
    runner = BenchmarkRunner(settings, WorkflowSearchSystem(run, settings), progress=logger.info)
    run_id = args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    index = runner.run(
        roster.select(args.person), run_id, {'code_version': _code_version(), 'label': args.label}, rerun=args.rerun
    )
    print(f'Run {run_id} saved to {settings.paths.runs / run_id}')
    if index.get('api_budget'):
        print('API budget: {}'.format(index['api_budget']))
    statuses = [entry['status'] for entry in index['people'].values()]
    return 0 if RunStatus.FAILED not in statuses else 2


def score_run(settings: BenchmarkSettings, roster: Roster, run_id: str, person_ids: List[str]) -> dict:
    index = load_index(settings, run_id)
    scorer = PersonScorer(settings)
    run_root = settings.paths.runs / run_id
    people = roster.select(person_ids)
    scores = [scorer.score(person, run_root, index['people'].get(person.person_id)) for person in people]
    aggregates = aggregate(scores, settings)
    gate = ReleaseGate(settings).evaluate(aggregates)
    ordered = [OVERALL] + [(dimension, value) for dimension, values in settings.dimensions.items() for value in values]
    return {
        'dataset_id': settings.dataset_id,
        'run_id': run_id,
        'scored_at': datetime.now(timezone.utc).isoformat(),
        'run_settings': index.get('run_settings'),
        'run_metadata': index.get('metadata'),
        'gate': gate.to_dict(),
        'strata': [dict(stratum='{}={}'.format(*key), **aggregates[key]) for key in ordered],
        'people': [score.to_dict() for score in scores],
    }


def command_score(args) -> int:
    settings, roster, _registry, _warnings = _context(args)
    scored = score_run(settings, roster, args.run_id, args.person)
    settings.paths.reports.mkdir(parents=True, exist_ok=True)
    write_json(settings.paths.reports / (args.run_id + '.scores.json'), scored)
    markdown = markdown_report(scored, settings)
    (settings.paths.reports / (args.run_id + '.md')).write_text(markdown, encoding='utf-8')
    print(markdown)
    return scored['gate']['exit_code'] if args.enforce_gate else 0


def command_diagnose(args) -> int:
    from paper_author_searcher.http_budget import ledger_summary

    from .identity_diagnosis import IdentityDiagnoser, WorkLister, markdown

    settings, roster, _registry, _warnings = _context(args)
    cache_dir = args.http_cache_dir or settings.run.http_cache_dir
    run = replace(settings.run, mode='metadata', http_cache_dir=cache_dir, http_cache_mode=args.http_cache_mode)
    container = ServiceContainer(load_settings(run), RunOptions.from_namespace(run))
    service = container.settings
    resolver = container.resolver
    lister = WorkLister(
        container.http_client,
        service.sources.openalex_url,
        service.sources.semantic_scholar_url,
        {
            'openalex': service.credentials.openalex_api_key,
            'semantic_scholar': service.credentials.semantic_scholar_api_key,
        },
    )
    diagnoser = IdentityDiagnoser(resolver.rank, lister)
    index = load_index(settings, args.run_id)
    diagnoses = []
    for person in roster.select(args.person):
        entry = index['people'].get(person.person_id) or {}
        selected = None
        if entry.get('report'):
            report_dir = settings.paths.runs / args.run_id / entry['report']
            artifacts = read_json(report_dir / 'report.json').get('artifacts') or {}
            if artifacts.get('papers'):
                collection = read_json(report_dir / artifacts['papers']).get('collection') or {}
                selected = collection.get('selected_author_ids')
        diagnosis = diagnoser.diagnose(
            person,
            load_truth(settings, person.person_id),
            person.window(settings),
            selected,
            entry.get('status', 'not_run'),
        )
        diagnoses.append(diagnosis)
        print(
            '{person_id}: {diagnosis} (truth {truth_publications}, in candidates '
            '{truth_found_in_any_candidate}, bearing {truth_bearing_candidates})'.format(**diagnosis),
            flush=True,
        )
    settings.paths.reports.mkdir(parents=True, exist_ok=True)
    write_json(settings.paths.reports / (args.run_id + '.identity.json'), {'run_id': args.run_id, 'people': diagnoses})
    (settings.paths.reports / (args.run_id + '.identity.md')).write_text(markdown(diagnoses), encoding='utf-8')
    print(f'API budget: {ledger_summary(run.cost_ledger)}')
    return 0


def command_calibrate(args) -> int:
    from .calibration import IdentityCalibrator, markdown

    settings, roster, _registry, _warnings = _context(args)
    cache_dir = args.http_cache_dir or settings.run.http_cache_dir
    if not cache_dir:
        raise ValueError('Calibration replays recorded responses; configure run.http_cache_dir')
    diagnoses = {
        item['person_id']: item
        for item in read_json(settings.paths.reports / (args.diagnosis_run + '.identity.json'))['people']
    }

    run = replace(settings.run, retries=0, http_cache_dir=cache_dir, http_cache_mode=args.http_cache_mode)

    # Calibration replays recorded responses many times; it never asks the (metered) LLM adjudicator.
    base = ServiceContainer(load_settings(run), replace(RunOptions.from_namespace(run), llm_adjudication=False))
    service = base.settings

    def resolver_factory(config):
        return base.with_settings(replace(service, matching=config)).resolver

    calibration = settings.calibration
    tune = [person for person in roster.people if person.split == calibration.tune_split]
    holdout = [person for person in roster.people if person.split == calibration.holdout_split]
    calibrator = IdentityCalibrator(resolver_factory, settings.run.ambiguity_policy)
    report = calibrator.search(service.matching, calibration.grid, tune, holdout, diagnoses, calibration.minimum_purity)
    settings.paths.reports.mkdir(parents=True, exist_ok=True)
    write_json(settings.paths.reports / (args.diagnosis_run + '.calibration.json'), report)
    text = markdown(report)
    (settings.paths.reports / (args.diagnosis_run + '.calibration.md')).write_text(text, encoding='utf-8')
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest='command', required=True)

    commands.add_parser('validate', help='Check roster, snapshots and truth state').set_defaults(
        handler=command_validate
    )

    fetch = commands.add_parser('fetch-sources', help='Capture ORCID works and Crossref DOI candidates (network)')
    fetch.add_argument('--person', action='append', default=[])
    fetch.add_argument('--no-crossref', action='store_true')
    fetch.add_argument('--no-arxiv', action='store_true')
    fetch.add_argument('--timeout', type=float)
    fetch.add_argument('--retries', type=int)
    fetch.set_defaults(handler=command_fetch)

    build = commands.add_parser('build-truth', help='Build pending truth from snapshots (offline)')
    build.add_argument('--person', action='append', default=[])
    build.set_defaults(handler=command_build)

    export = commands.add_parser('review-export', help='Write a review spreadsheet for one person')
    export.add_argument('--person', required=True)
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--suggest-from-run', help='Append papers this run found that are not in truth, as leads')
    export.add_argument('--suggestion-limit', type=int, default=80)
    export.add_argument(
        '--suggest-status',
        action='append',
        choices=OwnershipStatus.values(),
        default=None,
        help='System ownership statuses to include as leads (default: verified)',
    )
    export.set_defaults(handler=command_review_export)

    review = commands.add_parser('review-import', help='Apply reviewer decisions from a spreadsheet')
    review.add_argument('--person', required=True)
    review.add_argument('--sheet', type=Path, required=True)
    review.add_argument('--reviewer', required=True)
    review.add_argument(
        '--complete-positive-list',
        action='store_true',
        help='Reviewer asserts every in-scope publication is listed (enables formal precision)',
    )
    review.add_argument('--allow-pending', action='store_true', help='Save partial progress')
    review.add_argument(
        '--review-state',
        default=AnnotationState.HUMAN_REVIEWED.value,
        choices=(AnnotationState.HUMAN_REVIEWED.value, AnnotationState.ASSISTANT_REVIEWED.value),
        help='assistant_reviewed records an AI review; it is scored but never certifies the gate',
    )
    review.set_defaults(handler=command_review_import)

    run = commands.add_parser('run', help='Run the search system for every person (network)')
    run.add_argument('--run-id')
    run.add_argument('--person', action='append', default=[])
    run.add_argument('--mode', choices=('metadata', 'pdf-urls', 'full'))
    run.add_argument(
        '--input-profile',
        choices=('minimal', 'with_hints'),
        help='Override run.input_profile: pass roster hints (ORCID, homepage, known papers) or not',
    )
    run.add_argument('--label', default='')
    run.add_argument('--rerun', action='store_true', help='Repeat people that already finished in this run')
    run.set_defaults(handler=command_run)

    score = commands.add_parser('score', help='Score a run by stratum and evaluate the release gate (offline)')
    score.add_argument('--run-id', required=True)
    score.add_argument('--person', action='append', default=[])
    score.add_argument(
        '--enforce-gate', action='store_true', help='Exit 0 passed, 1 failed, 3 not certifiable (provisional truth)'
    )
    score.set_defaults(handler=command_score)

    diagnose = commands.add_parser(
        'diagnose-identity', help='Label identity failures against truth (network, cached; evaluation only)'
    )
    diagnose.add_argument('--run-id', required=True)
    diagnose.add_argument('--person', action='append', default=[])
    diagnose.add_argument('--http-cache-dir', help='Defaults to run.http_cache_dir in the config')
    diagnose.add_argument('--http-cache-mode', default='record', choices=('record', 'replay', 'refresh'))
    diagnose.set_defaults(handler=command_diagnose)

    calibrate = commands.add_parser(
        'calibrate',
        help='Tune identity parameters on dev people over recorded '
        'responses and report them on test people (offline)',
    )
    calibrate.add_argument(
        '--diagnosis-run', required=True, help='Run whose diagnose-identity report labels candidates'
    )
    calibrate.add_argument('--http-cache-dir')
    calibrate.add_argument('--http-cache-mode', default='replay', choices=('record', 'replay'))
    calibrate.set_defaults(handler=command_calibrate)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    try:
        return args.handler(args)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
