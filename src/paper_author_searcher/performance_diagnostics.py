"""Explain abstention and access failures without changing validation decisions."""

import re
import typing
from collections import Counter

from .config import DEFAULT_OWNERSHIP, OwnershipConfig
from .json_types import JSONList, JSONObject
from .paper_authorship import canonical_author_id
from .status import (
    AccessStatus,
    DownloadStatus,
    FormatStatus,
    IdentityStatus,
    OwnershipStatus,
    PDFValidationStatus,
    ProviderStatus,
    StageStatus,
)


def workflow_outcomes(report: JSONObject) -> JSONObject:
    reused = {item.get('stage') for item in report.get('stages') or [] if item.get('status') == StageStatus.REUSED}
    counts: typing.Counter[str] = Counter()
    for error in report.get('errors') or []:
        text = str(error.get('error') or '').lower()
        stage = error.get('stage')
        if stage in reused:
            category = 'historical_source_issue'
        elif any(
            value in text for value in ('timed out', 'timeout', 'certificate verify', 'name resolution', 'nodename')
        ):
            category = 'network_failure'
        elif re.search(r'http\s+(?:401|403|429|5\d\d)\b', text) or 'expected json' in text:
            category = 'source_unavailable'
        elif 'incomplete' in text or 'deferred' in text or 'limit reached' in text:
            category = 'query_incomplete'
        elif re.search(r'http\s+(?:404|410)\b', text):
            category = 'resource_unavailable'
        else:
            category = 'stage_error'
        error['category'] = category
        counts[category] += 1
    warnings = Counter(item.get('code', 'warning') for item in report.get('warnings') or [])
    request_outcomes: typing.Counter[str] = Counter()
    for observation in (report.get('errors') or []) + (report.get('warnings') or []):
        context = observation.get('request_context') or {}
        if context.get('outcome'):
            request_outcomes[context['outcome']] += 1
    decisions = Counter(item.get('category', 'candidate_filtered') for item in report.get('candidate_decisions') or [])
    download = report.get('download_statistics') or {}
    return {
        'error_observation_counts': dict(counts),
        'warning_observation_counts': dict(warnings),
        'request_outcome_observation_counts': dict(request_outcomes),
        'candidate_decision_counts': dict(decisions),
        'failed_download_papers': download.get('failed_papers', 0),
        'rejected_content_papers': download.get('rejected_content_papers', 0),
        'non_full_document_papers': download.get('non_full_document_papers', 0),
        'count_note': 'Per-paper observations can repeat cached/deferred causes. '
        'Actual logical request failures are in provider_request_statistics.events.',
    }


def pdf_failure_reasons(paper: JSONObject) -> list[str]:
    reasons = set()
    status = paper.get('validation_status')
    download = paper.get('download_status', '')
    if status == PDFValidationStatus.NOT_PROCESSED or download == 'skipped_limit':
        reasons.add('not_processed_limit')
    if status == PDFValidationStatus.SKIPPED_OWNERSHIP or download == DownloadStatus.SKIPPED_OWNERSHIP:
        reasons.add('ownership_not_eligible')
    providers = paper.get('providers') or []
    if any(
        provider.get('status') == ProviderStatus.ERROR
        or (provider.get('status') == ProviderStatus.PARTIAL and provider.get('errors'))
        for provider in providers
    ):
        reasons.add('source_query_failed')
    if any(provider.get('status') == ProviderStatus.PARTIAL for provider in providers):
        reasons.add('search_incomplete')
    if any(provider.get('status') == ProviderStatus.DISABLED for provider in providers):
        reasons.add('provider_not_configured')
    for provider in providers:
        for diagnostic in provider.get('diagnostics') or []:
            kind = diagnostic.get('error_type')
            if kind == 'not_found':
                reasons.add(
                    'url_not_found' if diagnostic.get('context') == 'public_resource' else 'metadata_record_not_found'
                )
            elif kind == 'source_deferred':
                reasons.add('source_temporarily_deferred')
            elif kind in (
                'rate_limited',
                'timeout',
                'network_error',
                'server_error',
                'access_restricted',
                'authentication_required',
            ):
                reasons.add(kind)
            elif kind in ('malformed_response', 'invalid_content_type', 'empty_response'):
                reasons.add('invalid_metadata_response')
            if diagnostic.get('source_cooldown_active'):
                reasons.add('source_temporarily_deferred')
    candidates = paper.get('pdf_candidates') or []
    messages = []
    for error in paper.get('error_details') or []:
        messages.append(str(error.get('error', '')) if isinstance(error, dict) else str(error))
    observations = candidates + (paper.get('attempts') or [])
    for candidate in observations:
        code = candidate.get('http_status')
        if candidate.get('query_error_type') == 'source_deferred':
            reasons.add('source_temporarily_deferred')
        elif code == 403:
            reasons.add('access_restricted')
        elif code == 404:
            reasons.add('url_not_found')
        elif code == 429:
            reasons.add('rate_limited')
        elif code is not None and 500 <= code <= 599:
            reasons.add('server_error')
        if candidate.get('identity_status') in (IdentityStatus.CONFLICTING, IdentityStatus.REJECTED):
            reasons.add('source_identity_mismatch')
        if candidate.get('content_type') in ('text/html', 'application/xhtml+xml'):
            reasons.add('not_pdf_response')
        if candidate.get('access_status') == AccessStatus.UNREACHABLE and code is None:
            reasons.add('network_error')
        if candidate.get('pdf_signature_confirmed') and candidate.get('identity_status') == IdentityStatus.UNVERIFIED:
            reasons.add('insufficient_pdf_identity_evidence')
    text = ' '.join(messages).lower()
    for code, label in ((403, 'access_restricted'), (429, 'rate_limited')):
        if re.search(r'http\s+' + str(code) + r'\b', text):
            reasons.add(label)
    if 'metadata_record_not_found' not in reasons and re.search(r'http\s+404\b', text):
        reasons.add('url_not_found')
    if any(word in text for word in ('timed out', 'timeout', 'name resolution', 'nodename')):
        reasons.add('network_error')
    if 'http 302' in text or 'redirect' in text:
        reasons.add('redirect_failure')
    if paper.get('identity_status') == IdentityStatus.REJECTED:
        reasons.add('downloaded_paper_identity_mismatch')
    elif (
        paper.get('format_status') == FormatStatus.VALID and paper.get('identity_status') == IdentityStatus.NEEDS_REVIEW
    ):
        reasons.add('downloaded_paper_identity_unconfirmed')
    if paper.get('format_status') in (FormatStatus.INVALID, FormatStatus.UNVERIFIED):
        reasons.add('downloaded_pdf_format_unconfirmed')
    if download == DownloadStatus.FAILED:
        reasons.add('download_failed')
    if paper.get('document_type') in ('poster', 'slides', 'supplementary_material'):
        reasons.add('non_full_text_document')
    if status in (PDFValidationStatus.NOT_FOUND, PDFValidationStatus.UNVERIFIED) and not candidates:
        incomplete = reasons.intersection(
            {'source_query_failed', 'provider_not_configured', 'network_error', 'search_incomplete'}
        )
        reasons.add('search_incomplete' if incomplete else 'no_public_pdf_found_in_queried_sources')
    if status == PDFValidationStatus.UNVERIFIED and candidates and not reasons:
        reasons.add('pdf_or_identity_unconfirmed')
    return sorted(reasons)


def review_reasons(paper: JSONObject, config: OwnershipConfig = DEFAULT_OWNERSHIP) -> list[str]:
    if paper.get('ownership_status') != OwnershipStatus.NEEDS_REVIEW:
        return []
    reasons = set()
    records = [paper] + (paper.get('records') or [])
    for record in records:
        details = record.get('author_details') or []
        expected = canonical_author_id(record.get('source'), record.get('source_author_id'))
        target = [
            author
            for author in details
            if expected and canonical_author_id(record.get('source'), author.get('author_id')) == expected
        ]
        evidence = ' '.join(record.get('ownership_evidence') or [])
        if not details:
            reasons.add('missing_paper_author_details')
        elif not target:
            reasons.add('missing_or_conflicting_target_author_id')
        if target:
            if not any(
                author.get('institutions') and author.get('affiliation_scope') != 'author_profile' for author in target
            ):
                reasons.add('missing_paper_level_affiliation')
            if not any(author.get('orcid') or author.get('orcids') for author in target):
                reasons.add('missing_target_orcid')
        if not record.get('title') or not record.get('source_paper_id'):
            reasons.add('missing_publication_metadata')
        if record.get('authors_complete') is False:
            reasons.add('incomplete_author_list')
        if max(len(details), len(record.get('authors') or [])) > config.maximum_coauthor_authors:
            reasons.add('large_author_list')
        if record.get('paper_type') in config.non_publication_types:
            reasons.add('not_a_research_publication')
        elif record.get('paper_type') in config.nonstandard_types or 'Nonstandard' in evidence:
            reasons.add('nonstandard_or_conflicting_publication_type')
        if 'ORCID conflict' in evidence or 'conflicts with confirmed professor ORCID' in evidence:
            reasons.add('author_identity_conflict')
        if 'Insufficient compatible target byline' in evidence:
            reasons.add('incompatible_or_insufficient_byline')
        if 'not independent paper authorship evidence' in evidence:
            reasons.add('profile_orcid_not_independent_paper_evidence')
        if 'identity unconfirmed' in evidence.lower():
            reasons.add('professor_identity_unconfirmed')
    if not reasons:
        reasons.add('insufficient_independent_identity_evidence')
    return sorted(reasons)


def ownership_diagnostics(papers: JSONList) -> JSONObject:
    counts: typing.Counter[str] = Counter()
    entries = []
    for index, paper in enumerate(papers):
        if paper.get('ownership_status') != OwnershipStatus.NEEDS_REVIEW:
            continue
        reasons = review_reasons(paper)
        counts.update(reasons)
        entries.append(
            {
                'entry_index': index,
                'title': paper.get('title'),
                'doi': paper.get('doi'),
                'reason_codes': reasons,
                'ownership_evidence': paper.get('ownership_evidence') or [],
            }
        )
    return {
        'needs_review_entries': len(entries),
        'reason_counts': dict(counts),
        'papers': entries,
        'count_note': 'Reason categories overlap; these are observed evidence gaps, not new ownership decisions.',
    }
