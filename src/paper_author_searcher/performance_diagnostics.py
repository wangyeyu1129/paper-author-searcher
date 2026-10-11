"""Explain abstention and access failures without changing validation decisions."""
import re
from collections import Counter

from .config import DEFAULT_OWNERSHIP
from .paper_authorship import canonical_author_id


def pdf_failure_reasons(paper):
    reasons = set()
    status = paper.get('validation_status')
    download = paper.get('download_status', '')
    if status == 'not_processed' or download == 'skipped_limit':
        reasons.add('not_processed_limit')
    if status == 'skipped_ownership' or download == 'skipped_ownership':
        reasons.add('ownership_not_eligible')
    providers = paper.get('providers') or []
    if any(provider.get('status') in ('error', 'partial') for provider in providers):
        reasons.add('source_query_failed')
    if any(provider.get('status') == 'disabled' for provider in providers):
        reasons.add('provider_not_configured')
    for provider in providers:
        for diagnostic in provider.get('diagnostics') or []:
            kind = diagnostic.get('error_type')
            if kind == 'not_found':
                reasons.add('metadata_record_not_found')
            elif kind in ('rate_limited', 'timeout', 'network_error', 'server_error', 'access_restricted',
                          'authentication_required'):
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
        if code == 403:
            reasons.add('access_restricted')
        elif code == 404:
            reasons.add('url_not_found')
        elif code == 429:
            reasons.add('rate_limited')
        elif code is not None and 500 <= code <= 599:
            reasons.add('server_error')
        if candidate.get('identity_status') in ('conflicting', 'rejected'):
            reasons.add('source_identity_mismatch')
        if candidate.get('content_type') in ('text/html', 'application/xhtml+xml'):
            reasons.add('not_pdf_response')
        if candidate.get('access_status') == 'unreachable' and code is None:
            reasons.add('network_error')
        if candidate.get('pdf_signature_confirmed') and candidate.get('identity_status') == 'unverified':
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
    if paper.get('identity_status') == 'rejected':
        reasons.add('downloaded_paper_identity_mismatch')
    elif paper.get('format_status') == 'valid' and paper.get('identity_status') == 'needs_review':
        reasons.add('downloaded_paper_identity_unconfirmed')
    if paper.get('format_status') in ('invalid', 'unverified'):
        reasons.add('downloaded_pdf_format_unconfirmed')
    if download == 'failed':
        reasons.add('download_failed')
    if status in ('not_found', 'unverified') and not candidates:
        incomplete = reasons.intersection({'source_query_failed', 'provider_not_configured', 'network_error'})
        reasons.add('search_incomplete' if incomplete else 'no_public_pdf_found_in_queried_sources')
    if status == 'unverified' and candidates and not reasons:
        reasons.add('pdf_or_identity_unconfirmed')
    return sorted(reasons)


def review_reasons(paper, config=DEFAULT_OWNERSHIP):
    if paper.get('ownership_status') != 'needs_review':
        return []
    reasons = set()
    records = [paper] + (paper.get('records') or [])
    for record in records:
        details = record.get('author_details') or []
        expected = canonical_author_id(record.get('source'), record.get('source_author_id'))
        target = [author for author in details
                  if expected and canonical_author_id(record.get('source'), author.get('author_id')) == expected]
        evidence = ' '.join(record.get('ownership_evidence') or [])
        if not details:
            reasons.add('missing_paper_author_details')
        elif not target:
            reasons.add('missing_or_conflicting_target_author_id')
        if target:
            if not any(author.get('institutions') and author.get('affiliation_scope') != 'author_profile' for author in target):
                reasons.add('missing_paper_level_affiliation')
            if not any(author.get('orcid') or author.get('orcids') for author in target):
                reasons.add('missing_target_orcid')
        if not record.get('title') or not record.get('source_paper_id'):
            reasons.add('missing_publication_metadata')
        if record.get('authors_complete') is False:
            reasons.add('incomplete_author_list')
        if max(len(details), len(record.get('authors') or [])) > config.maximum_coauthor_authors:
            reasons.add('large_author_list')
        if record.get('paper_type') in config.nonstandard_types or 'Nonstandard' in evidence:
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


def ownership_diagnostics(papers):
    counts = Counter()
    entries = []
    for index, paper in enumerate(papers):
        if paper.get('ownership_status') != 'needs_review':
            continue
        reasons = review_reasons(paper)
        counts.update(reasons)
        entries.append({'entry_index': index, 'title': paper.get('title'), 'doi': paper.get('doi'),
                        'reason_codes': reasons, 'ownership_evidence': paper.get('ownership_evidence') or []})
    return {'needs_review_entries': len(entries), 'reason_counts': dict(counts), 'papers': entries,
            'count_note': 'Reason categories overlap; these are observed evidence gaps, not new ownership decisions.'}
