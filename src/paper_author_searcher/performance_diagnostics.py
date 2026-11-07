"""Explain abstention and access failures without changing validation decisions."""

import typing
from collections import Counter
from http import HTTPStatus

from . import ownership_evidence as phrases
from .config import DEFAULT_OWNERSHIP, OwnershipConfig
from .diagnostic_codes import (
    DEFERRED_ERROR_TYPE,
    INVALID_RESPONSE_ERROR_TYPES,
    NOT_FOUND_ERROR_TYPE,
    PASSED_THROUGH_ERROR_TYPES,
    ErrorCategory,
    PdfFailureReason,
    ReviewReason,
    error_category,
    mentions_network_failure,
    mentions_status,
)
from .http_status import is_server_error
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

# PDF failure reasons that mean the queried sources were not all searched, so "nothing found" is not established.
INCOMPLETE_SEARCH_REASONS = frozenset(
    {
        PdfFailureReason.SOURCE_QUERY_FAILED,
        PdfFailureReason.PROVIDER_NOT_CONFIGURED,
        PdfFailureReason.NETWORK_ERROR,
        PdfFailureReason.SEARCH_INCOMPLETE,
    }
)


def workflow_outcomes(report: JSONObject) -> JSONObject:
    reused = {item.get('stage') for item in report.get('stages') or [] if item.get('status') == StageStatus.REUSED}
    counts: typing.Counter[str] = Counter()
    for error in report.get('errors') or []:
        category = ErrorCategory.HISTORICAL_SOURCE_ISSUE if error.get('stage') in reused else error_category(error)
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
    reasons: set[str] = set()
    status = paper.get('validation_status')
    download = paper.get('download_status', '')
    if status == PDFValidationStatus.NOT_PROCESSED or download == 'skipped_limit':
        reasons.add(PdfFailureReason.NOT_PROCESSED_LIMIT)
    if status == PDFValidationStatus.SKIPPED_OWNERSHIP or download == DownloadStatus.SKIPPED_OWNERSHIP:
        reasons.add(PdfFailureReason.OWNERSHIP_NOT_ELIGIBLE)
    providers = paper.get('providers') or []
    if any(
        provider.get('status') == ProviderStatus.ERROR
        or (provider.get('status') == ProviderStatus.PARTIAL and provider.get('errors'))
        for provider in providers
    ):
        reasons.add(PdfFailureReason.SOURCE_QUERY_FAILED)
    if any(provider.get('status') == ProviderStatus.PARTIAL for provider in providers):
        reasons.add(PdfFailureReason.SEARCH_INCOMPLETE)
    if any(provider.get('status') == ProviderStatus.DISABLED for provider in providers):
        reasons.add(PdfFailureReason.PROVIDER_NOT_CONFIGURED)
    for provider in providers:
        for diagnostic in provider.get('diagnostics') or []:
            kind = diagnostic.get('error_type')
            if kind == NOT_FOUND_ERROR_TYPE:
                public = diagnostic.get('context') == 'public_resource'
                reasons.add(PdfFailureReason.URL_NOT_FOUND if public else PdfFailureReason.METADATA_RECORD_NOT_FOUND)
            elif kind == DEFERRED_ERROR_TYPE:
                reasons.add(PdfFailureReason.SOURCE_TEMPORARILY_DEFERRED)
            elif kind in PASSED_THROUGH_ERROR_TYPES:
                reasons.add(PdfFailureReason(kind))
            elif kind in INVALID_RESPONSE_ERROR_TYPES:
                reasons.add(PdfFailureReason.INVALID_METADATA_RESPONSE)
            if diagnostic.get('source_cooldown_active'):
                reasons.add(PdfFailureReason.SOURCE_TEMPORARILY_DEFERRED)
    candidates = paper.get('pdf_candidates') or []
    messages = []
    for error in paper.get('error_details') or []:
        messages.append(str(error.get('error', '')) if isinstance(error, dict) else str(error))
    observations = candidates + (paper.get('attempts') or [])
    for candidate in observations:
        code = candidate.get('http_status')
        if candidate.get('query_error_type') == DEFERRED_ERROR_TYPE:
            reasons.add(PdfFailureReason.SOURCE_TEMPORARILY_DEFERRED)
        elif code == HTTPStatus.FORBIDDEN:
            reasons.add(PdfFailureReason.ACCESS_RESTRICTED)
        elif code == HTTPStatus.NOT_FOUND:
            reasons.add(PdfFailureReason.URL_NOT_FOUND)
        elif code == HTTPStatus.TOO_MANY_REQUESTS:
            reasons.add(PdfFailureReason.RATE_LIMITED)
        elif code is not None and is_server_error(code):
            reasons.add(PdfFailureReason.SERVER_ERROR)
        if candidate.get('identity_status') in (IdentityStatus.CONFLICTING, IdentityStatus.REJECTED):
            reasons.add(PdfFailureReason.SOURCE_IDENTITY_MISMATCH)
        if candidate.get('content_type') in ('text/html', 'application/xhtml+xml'):
            reasons.add(PdfFailureReason.NOT_PDF_RESPONSE)
        if candidate.get('access_status') == AccessStatus.UNREACHABLE and code is None:
            reasons.add(PdfFailureReason.NETWORK_ERROR)
        if candidate.get('pdf_signature_confirmed') and candidate.get('identity_status') == IdentityStatus.UNVERIFIED:
            reasons.add(PdfFailureReason.INSUFFICIENT_PDF_IDENTITY_EVIDENCE)
    text = ' '.join(messages).lower()
    for code, label in (
        (HTTPStatus.FORBIDDEN, PdfFailureReason.ACCESS_RESTRICTED),
        (HTTPStatus.TOO_MANY_REQUESTS, PdfFailureReason.RATE_LIMITED),
    ):
        if mentions_status(text, code):
            reasons.add(label)
    if PdfFailureReason.METADATA_RECORD_NOT_FOUND not in reasons and mentions_status(text, HTTPStatus.NOT_FOUND):
        reasons.add(PdfFailureReason.URL_NOT_FOUND)
    if mentions_network_failure(text):
        reasons.add(PdfFailureReason.NETWORK_ERROR)
    if mentions_status(text, HTTPStatus.FOUND) or 'redirect' in text:
        reasons.add(PdfFailureReason.REDIRECT_FAILURE)
    if paper.get('identity_status') == IdentityStatus.REJECTED:
        reasons.add(PdfFailureReason.DOWNLOADED_PAPER_IDENTITY_MISMATCH)
    elif (
        paper.get('format_status') == FormatStatus.VALID and paper.get('identity_status') == IdentityStatus.NEEDS_REVIEW
    ):
        reasons.add(PdfFailureReason.DOWNLOADED_PAPER_IDENTITY_UNCONFIRMED)
    if paper.get('format_status') in (FormatStatus.INVALID, FormatStatus.UNVERIFIED):
        reasons.add(PdfFailureReason.DOWNLOADED_PDF_FORMAT_UNCONFIRMED)
    if download == DownloadStatus.FAILED:
        reasons.add(PdfFailureReason.DOWNLOAD_FAILED)
    if paper.get('document_type') in ('poster', 'slides', 'supplementary_material'):
        reasons.add(PdfFailureReason.NON_FULL_TEXT_DOCUMENT)
    if status in (PDFValidationStatus.NOT_FOUND, PDFValidationStatus.UNVERIFIED) and not candidates:
        incomplete = reasons.intersection(INCOMPLETE_SEARCH_REASONS)
        reasons.add(PdfFailureReason.SEARCH_INCOMPLETE if incomplete else PdfFailureReason.NO_PUBLIC_PDF_FOUND)
    if status == PDFValidationStatus.UNVERIFIED and candidates and not reasons:
        reasons.add(PdfFailureReason.PDF_OR_IDENTITY_UNCONFIRMED)
    return sorted(reasons)


def review_reasons(paper: JSONObject, config: OwnershipConfig = DEFAULT_OWNERSHIP) -> list[str]:
    if paper.get('ownership_status') != OwnershipStatus.NEEDS_REVIEW:
        return []
    reasons: set[str] = set()
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
            reasons.add(ReviewReason.MISSING_PAPER_AUTHOR_DETAILS)
        elif not target:
            reasons.add(ReviewReason.MISSING_TARGET_AUTHOR_ID)
        if target:
            if not any(
                author.get('institutions') and author.get('affiliation_scope') != 'author_profile' for author in target
            ):
                reasons.add(ReviewReason.MISSING_PAPER_LEVEL_AFFILIATION)
            if not any(author.get('orcid') or author.get('orcids') for author in target):
                reasons.add(ReviewReason.MISSING_TARGET_ORCID)
        if not record.get('title') or not record.get('source_paper_id'):
            reasons.add(ReviewReason.MISSING_PUBLICATION_METADATA)
        if record.get('authors_complete') is False:
            reasons.add(ReviewReason.INCOMPLETE_AUTHOR_LIST)
        if max(len(details), len(record.get('authors') or [])) > config.maximum_coauthor_authors:
            reasons.add(ReviewReason.LARGE_AUTHOR_LIST)
        if record.get('paper_type') in config.non_publication_types:
            reasons.add(ReviewReason.NOT_A_RESEARCH_PUBLICATION)
        elif record.get('paper_type') in config.nonstandard_types or phrases.NONSTANDARD in evidence:
            reasons.add(ReviewReason.NONSTANDARD_PUBLICATION_TYPE)
        if phrases.ORCID_CONFLICT in evidence or phrases.CONFIRMED_ORCID_CONFLICT in evidence:
            reasons.add(ReviewReason.AUTHOR_IDENTITY_CONFLICT)
        if phrases.INSUFFICIENT_BYLINE in evidence:
            reasons.add(ReviewReason.INCOMPATIBLE_BYLINE)
        if phrases.PROFILE_ORCID_NOT_INDEPENDENT in evidence:
            reasons.add(ReviewReason.PROFILE_ORCID_NOT_INDEPENDENT)
        if phrases.IDENTITY_UNCONFIRMED in evidence.lower():
            reasons.add(ReviewReason.PROFESSOR_IDENTITY_UNCONFIRMED)
    if not reasons:
        reasons.add(ReviewReason.INSUFFICIENT_EVIDENCE)
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
