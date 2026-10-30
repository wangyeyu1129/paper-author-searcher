"""PDFs a web search finds for a paper the metadata sources could not supply."""

import re
from urllib.parse import urlsplit

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError
from paper_author_searcher.matching import normalize
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.status import IdentityStatus, ProviderStatus

from .base import make_candidate

UNBOUND = (IdentityStatus.UNVERIFIED, ['A web search hit; only the content of the file can tie it to the paper'])


class WebSearchPDFProvider:
    """Searches for one paper by title. Runs only after the other sources (PDFResolver fallbacks).

    Hits are never trusted: a PDF URL stays an unverified candidate that the downloader checks by content, and any
    other hit that names the paper is read as a landing page by ``pages``, which reports the PDFs the page declares.
    """

    name = 'web_search'

    def __init__(self, search, pages, config=DEFAULT_PDF):
        self.search = search
        self.pages = pages
        self.config = config
        self.pdf_url = re.compile(config.web_search_pdf_url_pattern, re.IGNORECASE)
        self.stopped = False

    def find(self, paper):
        result = PDFProviderResult(self.name)
        title = paper.get('title')
        if not title or self.stopped:
            result.status = ProviderStatus.NOT_APPLICABLE
            return result
        query = self._query(paper)
        try:
            hits = self.search.search(query, self.config.web_search_results)
        except (SourceError, ValueError, TypeError) as exc:
            self.stopped = getattr(exc, 'status_code', None) in self.config.web_search_stop_statuses
            result.status = ProviderStatus.ERROR
            result.errors.append(str(exc))
            return result
        hits = [hit for hit in hits if self._allowed(hit.url)]
        result.diagnostics.append({'provider': self.search.name, 'query': query, 'hits': len(hits)})
        pages = []
        for hit in hits:
            if self.pdf_url.search(hit.url):
                metadata = {'metadata_url': self.search.name, 'query': query, 'hit_title': hit.title}
                result.candidates.append(make_candidate(hit.url, self.name, UNBOUND, metadata, 'Public web search hit'))
            elif normalize(title) in normalize(f'{hit.title} {hit.snippet}'):
                pages.append(hit.url)
        self._read_pages(paper, pages[: self.config.web_search_landing_pages], result)
        return result

    def _query(self, paper):
        authors = paper.get('authors') or ['']
        return ' '.join(self.config.web_search_query.format(title=paper['title'], author=authors[0]).split())

    def _allowed(self, url):
        host = (urlsplit(url).hostname or '').lower()
        if any(word in host for word in self.config.web_search_excluded_host_words):
            return False
        return not any(
            host == excluded or host.endswith('.' + excluded) for excluded in self.config.web_search_excluded_hosts
        )

    def _read_pages(self, paper, urls, result):
        if not urls:
            return
        # Only these pages: an empty record hides the paper's own pages, which public_pages has already read.
        lookup = dict(paper, records=[{}], _pdf_landing_pages=[{'landing_page_url': url} for url in urls])
        found = self.pages.find(lookup)
        result.candidates.extend(found.candidates)
        result.diagnostics.extend(found.diagnostics)
