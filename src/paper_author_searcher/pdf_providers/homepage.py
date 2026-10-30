"""PDFs the person's own publication page links next to a citation of the paper."""

from typing import Any

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.matching import ARXIV_DOI_PREFIX, canonical_doi, normalize
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_identity import arxiv_id, edition_arxiv_ids, paper_dois, related_dois
from paper_author_searcher.status import IdentityStatus, ProviderStatus

from .base import make_candidate


class HomepagePDFProvider:
    """PDFs the person's own publication page links from a citation of the paper.

    The resolved profile records those citations (identity_anchors.listed_pdfs). A citation names the paper by its
    DOI, its arXiv ID, or its full title in the citation text; one naming another DOI is another work. A PDF linked
    from citations of several papers in the collection is tied to none, and the downloaded file must still show the
    title. No request is made.
    """

    name = 'homepage'

    def __init__(self, config=DEFAULT_PDF):
        self.config = config
        self.listed: list[dict[str, Any]] = []
        self.ambiguous: set[str] = set()

    def prepare_profile(self, profile):
        self.listed = [item for item in (profile or {}).get('listed_pdfs') or [] if item.get('url')]

    def prepare(self, papers):
        works: dict[str, set[str]] = {}
        for paper in papers:
            for item, _ in self._matches(paper):
                works.setdefault(item['url'], set()).add(normalize(paper.get('title') or ''))
        self.ambiguous = {url for url, titles in works.items() if len(titles) > 1}

    def find(self, paper):
        result = PDFProviderResult(self.name)
        for item, reason in self._matches(paper):
            if item['url'] in self.ambiguous:
                result.diagnostics.append({'url': item['url'], 'reason': 'linked from citations of several papers'})
                continue
            provenance = {'page_url': item.get('page'), 'citation': item.get('citation')}
            result.candidates.append(
                make_candidate(
                    item['url'],
                    self.name,
                    (IdentityStatus.VERIFIED, [reason]),
                    provenance,
                    "Linked from the person's own publication page",
                )
            )
        if not result.candidates and not result.diagnostics:
            result.status = ProviderStatus.NOT_APPLICABLE
        return result

    def _matches(self, paper):
        dois = {doi[len('doi:') :] for doi in paper_dois(paper)} | set(related_dois(paper))
        editions = set(edition_arxiv_ids(paper))
        title = normalize(paper.get('title') or '')
        long_title = len(title.split()) >= self.config.homepage_title_minimum_words
        for item in self.listed:
            listed_dois = {canonical_doi(doi)[len('doi:') :] for doi in item.get('dois') or []}
            listed_arxiv = {arxiv_id(value) for value in item.get('arxiv_ids') or []} - {None}
            if listed_dois & dois:
                yield item, "The person's publication page links this PDF from a citation with the paper's DOI"
            elif listed_arxiv & editions:
                yield item, "The person's publication page links this PDF from a citation with the paper's arXiv ID"
            elif (
                long_title
                and not (dois and {doi for doi in listed_dois if not doi.startswith(ARXIV_DOI_PREFIX)})
                and title in normalize(item.get('citation') or '')
            ):
                yield item, "The person's publication page links this PDF from a citation with the paper's full title"
