"""Closed vocabularies of the diagnostic codes reports carry, and the one classifier for recorded errors.

The codes explain abstentions and failures; they never change a decision. Each vocabulary is a ``str`` enum, so a
code compares, sorts and serialises exactly like its value and reports saved before this module existed read back
unchanged.

A recorded error is classified from its structured fields (``error_type``, ``status_code``) when the producer kept
them, and from its message otherwise: older reports, and errors that only ever existed as text, still classify.
"""

import re
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from .http_status import is_server_error
from .status import StrEnum


class ErrorCategory(StrEnum):
    """Why a workflow error was recorded (``report.outcomes.error_observation_counts``)."""

    HISTORICAL_SOURCE_ISSUE = 'historical_source_issue'  # recorded by a stage this run reused
    NETWORK_FAILURE = 'network_failure'
    SOURCE_UNAVAILABLE = 'source_unavailable'
    QUERY_INCOMPLETE = 'query_incomplete'
    RESOURCE_UNAVAILABLE = 'resource_unavailable'
    STAGE_ERROR = 'stage_error'


class PdfFailureReason(StrEnum):
    """Why a paper has no verified PDF (``failure_reasons`` on PDF results and download records)."""

    NOT_PROCESSED_LIMIT = 'not_processed_limit'
    OWNERSHIP_NOT_ELIGIBLE = 'ownership_not_eligible'
    SOURCE_QUERY_FAILED = 'source_query_failed'
    SEARCH_INCOMPLETE = 'search_incomplete'
    PROVIDER_NOT_CONFIGURED = 'provider_not_configured'
    URL_NOT_FOUND = 'url_not_found'
    METADATA_RECORD_NOT_FOUND = 'metadata_record_not_found'
    SOURCE_TEMPORARILY_DEFERRED = 'source_temporarily_deferred'
    RATE_LIMITED = 'rate_limited'
    TIMEOUT = 'timeout'
    NETWORK_ERROR = 'network_error'
    SERVER_ERROR = 'server_error'
    ACCESS_RESTRICTED = 'access_restricted'
    AUTHENTICATION_REQUIRED = 'authentication_required'
    INVALID_METADATA_RESPONSE = 'invalid_metadata_response'
    SOURCE_IDENTITY_MISMATCH = 'source_identity_mismatch'
    NOT_PDF_RESPONSE = 'not_pdf_response'
    INSUFFICIENT_PDF_IDENTITY_EVIDENCE = 'insufficient_pdf_identity_evidence'
    REDIRECT_FAILURE = 'redirect_failure'
    DOWNLOADED_PAPER_IDENTITY_MISMATCH = 'downloaded_paper_identity_mismatch'
    DOWNLOADED_PAPER_IDENTITY_UNCONFIRMED = 'downloaded_paper_identity_unconfirmed'
    DOWNLOADED_PDF_FORMAT_UNCONFIRMED = 'downloaded_pdf_format_unconfirmed'
    DOWNLOAD_FAILED = 'download_failed'
    NON_FULL_TEXT_DOCUMENT = 'non_full_text_document'
    NO_PUBLIC_PDF_FOUND = 'no_public_pdf_found_in_queried_sources'
    PDF_OR_IDENTITY_UNCONFIRMED = 'pdf_or_identity_unconfirmed'


class ReviewReason(StrEnum):
    """The evidence a needs-review paper lacks (``needs_review_diagnostics``)."""

    MISSING_PAPER_AUTHOR_DETAILS = 'missing_paper_author_details'
    MISSING_TARGET_AUTHOR_ID = 'missing_or_conflicting_target_author_id'
    MISSING_PAPER_LEVEL_AFFILIATION = 'missing_paper_level_affiliation'
    MISSING_TARGET_ORCID = 'missing_target_orcid'
    MISSING_PUBLICATION_METADATA = 'missing_publication_metadata'
    INCOMPLETE_AUTHOR_LIST = 'incomplete_author_list'
    LARGE_AUTHOR_LIST = 'large_author_list'
    NOT_A_RESEARCH_PUBLICATION = 'not_a_research_publication'
    NONSTANDARD_PUBLICATION_TYPE = 'nonstandard_or_conflicting_publication_type'
    AUTHOR_IDENTITY_CONFLICT = 'author_identity_conflict'
    INCOMPATIBLE_BYLINE = 'incompatible_or_insufficient_byline'
    PROFILE_ORCID_NOT_INDEPENDENT = 'profile_orcid_not_independent_paper_evidence'
    PROFESSOR_IDENTITY_UNCONFIRMED = 'professor_identity_unconfirmed'
    INSUFFICIENT_EVIDENCE = 'insufficient_independent_identity_evidence'


# Source error types (``SourceError.error_type``) that each name a PDF failure reason of the same value.
PASSED_THROUGH_ERROR_TYPES = frozenset(
    {
        PdfFailureReason.RATE_LIMITED,
        PdfFailureReason.TIMEOUT,
        PdfFailureReason.NETWORK_ERROR,
        PdfFailureReason.SERVER_ERROR,
        PdfFailureReason.ACCESS_RESTRICTED,
        PdfFailureReason.AUTHENTICATION_REQUIRED,
    }
)
INVALID_RESPONSE_ERROR_TYPES = frozenset({'malformed_response', 'invalid_content_type', 'empty_response'})
NOT_FOUND_ERROR_TYPE = 'not_found'
DEFERRED_ERROR_TYPE = 'source_deferred'

# Message fragments that mark a network failure in an error recorded only as text.
NETWORK_MESSAGES = ('timed out', 'timeout', 'name resolution', 'nodename')
TLS_MESSAGES = ('certificate verify',)
UNAVAILABLE_STATUS = re.compile(r'http\s+(?:401|403|429|5\d\d)\b')
GONE_STATUS = re.compile(r'http\s+(?:404|410)\b')
INCOMPLETE_MESSAGES = ('incomplete', 'deferred', 'limit reached')
UNEXPECTED_FORMAT_MESSAGE = 'expected json'
UNAVAILABLE_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS})
GONE_STATUSES = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.GONE})
NETWORK_ERROR_TYPES = frozenset({'timeout', 'network_error'})


def error_fields(exc: BaseException) -> dict[str, Any]:
    """The structured fields worth keeping beside an error's message: its type and HTTP status, when it has them."""
    fields: dict[str, Any] = {}
    for name in ('error_type', 'status_code'):
        value = getattr(exc, name, None)
        if value is not None:
            fields[name] = value
    return fields


def mentions_status(text: str, status: int) -> bool:
    return re.search(r'http\s+' + str(status) + r'\b', text) is not None


def mentions_network_failure(text: str) -> bool:
    return any(fragment in text for fragment in NETWORK_MESSAGES)


def _structured_category(error: Mapping[str, Any]) -> ErrorCategory | None:
    status = error.get('status_code')
    if status is not None:
        if status in UNAVAILABLE_STATUSES or is_server_error(status):
            return ErrorCategory.SOURCE_UNAVAILABLE
        if status in GONE_STATUSES:
            return ErrorCategory.RESOURCE_UNAVAILABLE
    if error.get('error_type') in NETWORK_ERROR_TYPES:
        return ErrorCategory.NETWORK_FAILURE
    return None


def _message_category(text: str) -> ErrorCategory:
    if mentions_network_failure(text) or any(fragment in text for fragment in TLS_MESSAGES):
        return ErrorCategory.NETWORK_FAILURE
    if UNAVAILABLE_STATUS.search(text) or UNEXPECTED_FORMAT_MESSAGE in text:
        return ErrorCategory.SOURCE_UNAVAILABLE
    if any(fragment in text for fragment in INCOMPLETE_MESSAGES):
        return ErrorCategory.QUERY_INCOMPLETE
    if GONE_STATUS.search(text):
        return ErrorCategory.RESOURCE_UNAVAILABLE
    return ErrorCategory.STAGE_ERROR


def error_category(error: Mapping[str, Any]) -> ErrorCategory:
    """One recorded workflow error's category: its structured fields first, then its message."""
    return _structured_category(error) or _message_category(str(error.get('error') or '').lower())
