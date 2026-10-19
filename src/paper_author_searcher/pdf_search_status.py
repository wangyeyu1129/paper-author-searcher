"""Keep source completeness separate from obtaining a confirmed complete paper."""

from typing import Optional, Tuple

from .json_types import JSONList, JSONObject
from .status import FormatStatus, IdentityStatus, OwnershipStatus, PDFValidationStatus, ProviderStatus

SEARCH_COMPLETION_DEFINITION = 'configured_provider_status_v1'
COMPLETE_PROVIDER_STATUSES = {ProviderStatus.OK, ProviderStatus.NOT_FOUND, ProviderStatus.NOT_APPLICABLE}


def provider_search_complete(provider: JSONObject) -> bool:
    return provider.get('status') in COMPLETE_PROVIDER_STATUSES


def paper_search_complete(providers: JSONList) -> bool:
    return bool(providers) and all(provider_search_complete(provider) for provider in providers)


def search_satisfaction(paper: JSONObject, file_integrity_confirmed: bool) -> Tuple[Optional[bool], str]:
    """A URL probe or a trusted domain cannot satisfy the full-paper requirement."""
    if paper.get('ownership_status') != OwnershipStatus.VERIFIED:
        return False, 'paper_ownership_not_verified'
    if not file_integrity_confirmed:
        return False, 'no_integrity_checked_local_file'
    if paper.get('format_status') != FormatStatus.VALID:
        return False, 'pdf_format_not_validated'
    if (
        paper.get('identity_status') != IdentityStatus.VERIFIED
        or paper.get('validation_status') != PDFValidationStatus.VERIFIED
    ):
        return False, 'pdf_content_not_confirmed'
    document_type = paper.get('document_type') or (paper.get('validation') or {}).get('document_type')
    if document_type in (None, 'unknown'):
        return None, 'document_type_unconfirmed'
    if document_type not in ('full_text', 'preprint'):
        return False, 'not_a_complete_paper_document'
    return True, 'verified_ownership_format_content_and_complete_document'
