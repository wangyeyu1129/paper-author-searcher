"""Bounded resource probes plus metadata identity, never complete PDF retrieval."""

import re

from .config import DEFAULT_PDF
from .http import HttpClient, SourceError, has_pdf_signature
from .pdf_identity import arxiv_id, arxiv_ids, same_arxiv_version


class PDFURLValidator:
    def __init__(self, client=None, config=DEFAULT_PDF):
        self.client = client or HttpClient()
        self.config = config

    def validate(self, candidate, paper=None):
        try:
            response = self.client.probe(
                candidate.url,
                self.config.preview_bytes,
                maximum_redirects=self.config.maximum_redirects,
            )
        except (SourceError, ValueError) as exc:
            candidate.access_status = 'unreachable'
            candidate.http_status = getattr(exc, 'status_code', None)
            candidate.query_error_type = getattr(exc, 'error_type', 'source_error')
            candidate.request_context = getattr(exc, 'request_context', {})
            candidate.error_details.append(str(exc))
            return candidate
        candidate.request_context = response.request_context
        candidate.final_url = response.url
        candidate.http_status = response.status
        candidate.content_type = response.content_type
        candidate.bytes_read = len(response.prefix)
        if not 200 <= response.status < 300:
            candidate.access_status = 'unreachable'
            candidate.error_details.append(f'HTTP {response.status}')
            return candidate
        candidate.access_status = 'publicly_accessible'
        if response.status == 206 and not self._starts_at_zero(response.headers):
            candidate.error_details.append('Partial response does not confirm byte offset zero')
            return candidate
        if response.status == 206:
            candidate.validation_evidence.append('Content-Range confirms a partial response beginning at byte zero')
        if response.content_type in ('text/html', 'application/xhtml+xml'):
            candidate.validation_status = 'invalid'
            candidate.error_details.append('Resource is HTML, not a PDF (possibly an access interstitial)')
            return candidate
        if not has_pdf_signature(response.prefix):
            candidate.error_details.append('PDF file signature not confirmed; MIME type alone is insufficient')
            return candidate
        candidate.pdf_signature_confirmed = True
        candidate.validation_evidence.append('Bounded GET returned a PDF file signature')
        if self._arxiv_redirect_conflicts(candidate, paper):
            candidate.identity_status = 'conflicting'
            candidate.identity_evidence.append('PDF URL or redirect conflicts with the known arXiv paper/version')
        if candidate.identity_status == 'conflicting':
            candidate.validation_status = 'invalid'
            candidate.error_details.append('PDF is accessible but paper identity conflicts')
        elif candidate.identity_status == 'verified' and candidate.access_basis:
            candidate.validation_status = 'verified'
            candidate.validation_evidence.append(
                'Correspondence established by public source metadata, not full-text analysis'
            )
        else:
            candidate.error_details.append('PDF signature confirmed, but paper correspondence remains unverified')
        return candidate

    def _starts_at_zero(self, headers):
        values = {key.lower(): value for key, value in headers.items()}
        return bool(re.match(r'^bytes\s+0-\d+/', values.get('content-range', ''), flags=re.IGNORECASE))

    def _arxiv_redirect_conflicts(self, candidate, paper):
        expected = arxiv_id(candidate.url)
        returned = arxiv_id(candidate.final_url)
        if expected and returned and not same_arxiv_version(expected, returned):
            return True
        known_ids = arxiv_ids(paper) if paper else []
        linked_id = returned or expected
        if linked_id and known_ids:
            known_papers = {re.sub(r'v\d+$', '', value) for value in known_ids}
            if re.sub(r'v\d+$', '', linked_id) not in known_papers:
                return True
        for record in candidate.provenance:
            arxiv = record.get('provider') == 'arxiv'
            if arxiv and returned and not same_arxiv_version(record['source_paper_id'], returned):
                return True
        return False
