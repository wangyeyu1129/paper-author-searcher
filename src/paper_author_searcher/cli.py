"""Command-line presentation; identity decisions belong to the resolver."""

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from .json_types import JSONList, JSONObject
from .logging_config import add_logging_arguments, configure_from_arguments
from .models import PaperCollectionResult, ProfessorProfile, ResolutionResult
from .paper_collector import IdentityConfirmationRequired, PaperCollector
from .paper_reanalysis import reanalyze_saved_result, saved_collection_profile
from .status import OwnershipStatus, ResolutionStatus
from .workflow_cli import (
    add_hint_arguments,
    add_workflow_arguments,
    build_container,
    query_from_args,
    run_arguments,
)

logger = logging.getLogger(__name__)

SUMMARY_EVIDENCE_LIMIT = 6
SUCCESS_EXIT_CODE = 0
REVIEW_EXIT_CODE = 2


def summary(result: ResolutionResult) -> JSONObject:
    profile = result.profile
    if profile is None:
        return {
            'name': None,
            'university': None,
            'author_ids': {},
            'confidence': 0,
            'status': ResolutionStatus.NEEDS_REVIEW,
            'evidence': ['No candidate returned'],
            'source_errors': result.source_errors,
        }
    output = {
        'name': profile.name,
        'university': profile.university,
        'author_ids': profile.author_ids,
        'confidence': profile.identity_confidence,
        'match_score': profile.match_score,
        'requested_university': profile.requested_university,
        'matched_affiliations': profile.matched_affiliations,
        'author_id_aliases': profile.author_id_aliases,
        'orcid': profile.orcid,
        'name_aliases': profile.name_aliases,
        'requested_name': profile.requested_name,
        'status': profile.status,
        'evidence': profile.evidence[:SUMMARY_EVIDENCE_LIMIT],
    }
    if result.ambiguity:
        output['ambiguity'] = result.ambiguity
    if result.source_errors:
        output['source_errors'] = result.source_errors
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve a professor's scholarly author identities",
    )
    parser.add_argument('--name')
    parser.add_argument('--university')
    parser.add_argument('--department')
    parser.add_argument('--research', action='append', default=[])
    parser.add_argument('--university-alias', action='append', default=[])
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Include all candidates and source diagnostics',
    )
    parser.add_argument('--collect-papers', action='store_true', help='Collect confirmed author metadata')
    parser.add_argument('--list-candidates', action='store_true', help='Show identity candidates with all IDs')
    parser.add_argument('--select-author', metavar='SOURCE:AUTHOR_ID', help='Manually confirm one candidate ID')
    parser.add_argument('--output', type=Path, help='Save the result as UTF-8 JSON')
    parser.add_argument('--academic-only', action='store_true', help='Filter visible papers; retain full archive')
    parser.add_argument('--ownership-status', action='append', choices=OwnershipStatus.values())
    saved_input = parser.add_mutually_exclusive_group()
    saved_input.add_argument('--reanalyze-json', type=Path, help='Re-evaluate a saved result without network calls')
    saved_input.add_argument(
        '--refresh-json', type=Path, help='Fetch fresh metadata using previously confirmed Author IDs'
    )

    add_hint_arguments(parser)
    add_workflow_arguments(parser)
    return parser


def _selected_profile(result: ResolutionResult, selection: str) -> tuple[ProfessorProfile, tuple[str, str]]:
    if ':' not in selection:
        raise IdentityConfirmationRequired('Use SOURCE:AUTHOR_ID for --select-author')
    source, author_id = selection.split(':', 1)
    for profile in result.candidates:
        ids = profile.author_id_aliases.get(source, [])
        if author_id in ids or profile.author_ids.get(source) == author_id:
            return profile, (source, author_id)
    raise IdentityConfirmationRequired('Selected Author ID is absent from resolver candidates')


def _candidate_summaries(result: ResolutionResult) -> JSONList:
    candidates = []
    for profile in result.candidates:
        candidates.append(
            {
                'name': profile.name,
                'author_ids': profile.author_ids,
                'author_id_aliases': profile.author_id_aliases,
                'match_score': profile.match_score,
                'status': profile.status,
                'matched_affiliations': profile.matched_affiliations,
            }
        )
    return candidates


def _collect_output(
    result: ResolutionResult,
    selected_author: str | None,
    output: JSONObject,
    collector: PaperCollector,
    academic_only: bool = False,
    ownership_statuses: Sequence[str] | None = None,
) -> int:
    try:
        profile = result.profile
        selection = None
        if selected_author:
            profile, selection = _selected_profile(result, selected_author)
        if profile is None:
            raise IdentityConfirmationRequired('No identity candidate available')
        collection = collector.collect(profile, selected_author=selection)
    except IdentityConfirmationRequired as exc:
        output['collection_error'] = str(exc)
        output['candidates'] = _candidate_summaries(result)
        return REVIEW_EXIT_CODE
    output['collection'] = collection.to_dict(academic_only, ownership_statuses)
    output['collection_identity'] = {
        'name': profile.name,
        'author_ids': collection.selected_author_ids,
        'confirmation': collection.identity_confirmation,
        'status': profile.status,
        'orcid': profile.orcid,
        'name_aliases': profile.name_aliases,
        'requested_name': profile.requested_name,
    }
    if collection.complete:
        return SUCCESS_EXIT_CODE
    return REVIEW_EXIT_CODE


def _write_output(output: JSONObject, path: Path | None) -> None:
    serialized = json.dumps(output, ensure_ascii=False, indent=2)
    if path:
        path.write_text(serialized + '\n', encoding='utf-8')
    print(serialized)


def _refresh_collection(saved: JSONObject, collector: PaperCollector) -> PaperCollectionResult:
    profile = saved_collection_profile(saved)
    stored = saved['collection']
    selection = None
    if stored.get('identity_confirmation') == 'manual_author_id':
        selected = stored.get('selected_author_ids') or {}
        if len(selected) != 1:
            raise IdentityConfirmationRequired('Saved manual selection must contain one source and one Author ID')
        source, author_ids = next(iter(selected.items()))
        if len(author_ids) != 1:
            raise IdentityConfirmationRequired('Saved manual selection must contain exactly one Author ID')
        selection = (source, author_ids[0])
    return collector.collect(profile, selected_author=selection)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    if args.mode or args.input:
        if args.collect_papers or args.refresh_json or args.reanalyze_json or args.output:
            parser.error('Workflow mode uses --output-dir and --input; do not combine with legacy collection options')
        return run_arguments(parser, args)
    if args.refresh_json:
        if args.collect_papers or args.select_author:
            parser.error('--refresh-json uses the saved confirmed identity; do not combine with live selection')
        try:
            saved = json.loads(args.refresh_json.read_text(encoding='utf-8'))
            collection = _refresh_collection(saved, build_container(args).collector)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        saved['collection'] = collection.to_dict(args.academic_only, args.ownership_status)
        _write_output(saved, args.output)
        return SUCCESS_EXIT_CODE if collection.complete else REVIEW_EXIT_CODE
    if args.reanalyze_json:
        if args.collect_papers or args.select_author:
            parser.error('--reanalyze-json cannot be combined with live collection')
        saved = json.loads(args.reanalyze_json.read_text(encoding='utf-8'))
        settings = build_container(args).settings
        collection = reanalyze_saved_result(saved, settings.ownership, settings.deduplication)
        saved['collection'] = collection.to_dict(args.academic_only, args.ownership_status)
        _write_output(saved, args.output)
        return SUCCESS_EXIT_CODE if collection.complete else REVIEW_EXIT_CODE
    if not args.name or not args.university:
        parser.error('--name and --university are required for live resolution')
    if (args.academic_only or args.ownership_status) and not args.collect_papers:
        parser.error('Paper filters require --collect-papers or --reanalyze-json')
    if args.select_author and not args.collect_papers:
        parser.error('--select-author requires --collect-papers')
    container = build_container(args)
    result = container.resolver.resolve(query_from_args(args))
    output = summary(result)
    if args.verbose:
        output = result.to_dict()
    exit_code = REVIEW_EXIT_CODE
    if result.profile and result.profile.status == ResolutionStatus.RESOLVED:
        exit_code = SUCCESS_EXIT_CODE
    if args.list_candidates:
        output['candidates'] = _candidate_summaries(result)
    if args.collect_papers:
        exit_code = _collect_output(
            result,
            args.select_author,
            output,
            container.collector,
            args.academic_only,
            args.ownership_status,
        )
    _write_output(output, args.output)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
