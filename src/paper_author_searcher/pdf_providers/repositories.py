"""Preprint and working-paper repositories: arXiv and NBER."""

import re

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError
from paper_author_searcher.matching import normalize_title
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pacing import RequestPacer
from paper_author_searcher.pdf_identity import (
    arxiv_id,
    arxiv_ids,
    metadata_identity,
    paper_dois,
    related_dois,
    same_arxiv_version,
    source_records,
)
from paper_author_searcher.status import IdentityStatus, ProviderStatus

from .base import MetadataPDFProvider, make_candidate


class NBERPDFProvider(MetadataPDFProvider):
    """NBER working-paper PDFs, named by the NBER DOI of the paper or of an edition related to it.

    Deduplication relates an edition only on the same normalized title and compatible authors, so the working
    paper of a later journal article is bound to it; the downloaded file must still show the title.
    """

    name = 'nber'

    def __init__(self, client, config=DEFAULT_PDF):
        super().__init__(client)
        self.config = config

    def lookups(self, paper):
        own = [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]
        numbers = [self._number(doi) for doi in [*own, *related_dois(paper)]]
        return list(dict.fromkeys(number for number in numbers if number))

    def lookup(self, paper, identifier):
        own = {self._number(doi[len('doi:') :]) for doi in paper_dois(paper)}
        if identifier in own:
            identity = (IdentityStatus.VERIFIED, ['Exact DOI agreement with saved paper metadata'])
        else:
            identity = (
                IdentityStatus.VERIFIED,
                ['NBER DOI of an edition deduplication related to this paper (same normalized title and authors)'],
            )
        url = self.config.nber_pdf_url_template.format(number=identifier)
        provenance = {'doi': '10.3386/' + identifier, 'source_paper_id': identifier}
        candidate = make_candidate(
            url, self.name, identity, provenance, 'NBER working papers are free to download', 'submittedVersion'
        )
        return [candidate], []

    def _number(self, doi):
        match = re.match(self.config.nber_doi_pattern, doi or '', flags=re.IGNORECASE)
        return match.group(1).lower() if match else None


class ArxivPDFProvider:
    name = 'arxiv'
    atom = '{http://www.w3.org/2005/Atom}'
    arxiv = '{http://arxiv.org/schemas/atom}'

    def __init__(self, client, config=DEFAULT_PDF):
        self.client = client
        self.config = config
        self.pacer = RequestPacer(config.arxiv_request_interval)

    def find(self, paper):
        identifiers = arxiv_ids(paper)
        search = not identifiers and self.config.arxiv_title_search and bool(paper.get('title'))
        if not identifiers and not search:
            return PDFProviderResult(self.name, status=ProviderStatus.NOT_APPLICABLE)
        result = PDFProviderResult(self.name)
        try:
            self._wait_for_interval()
            params = {'id_list': ','.join(identifiers)}
            if search:
                title = ' '.join(paper['title'].replace('"', ' ').split())
                params = {'search_query': 'ti:"' + title + '"', 'max_results': self.config.arxiv_search_results}
            feed = self.client.get(self.config.arxiv_url, params=params, xml=True)
            entries = feed.findall(self.atom + 'entry')
            if not entries and not search:
                raise SourceError('arXiv returned no matching entries')
            for entry in entries:
                if search and normalize_title(entry.findtext(self.atom + 'title') or '') != normalize_title(
                    paper['title']
                ):
                    continue
                result.candidates.extend(self._entry_candidates(paper, identifiers, entry, search))
        except (SourceError, ValueError, TypeError) as exc:
            result.status = ProviderStatus.ERROR
            result.errors.append(str(exc))
        return result

    def _wait_for_interval(self):
        self.pacer.wait()

    def _entry_candidates(self, paper, requested, entry, search=False):
        identifier = arxiv_id(entry.findtext(self.atom + 'id'))
        if not identifier or (not search and not any(same_arxiv_version(value, identifier) for value in requested)):
            raise SourceError('arXiv entry does not match the requested identifier/version')
        title = ' '.join((entry.findtext(self.atom + 'title') or '').split())
        doi = entry.findtext(self.arxiv + 'doi')
        # A supplied arXiv ID identifies a version even when the journal DOI is absent.
        bound_paper = dict(
            paper,
            records=[*list(source_records(paper)), {'source': 'arxiv', 'source_paper_id': identifier}],
        )
        published = entry.findtext(self.atom + 'published') or ''
        authors = [author.findtext(self.atom + 'name') or '' for author in entry.findall(self.atom + 'author')]
        identity = metadata_identity(
            paper if search else bound_paper,
            self.name,
            identifier,
            doi,
            title,
            published[:4],
            authors,
            config=self.config,
        )
        candidates = []
        for link in entry.findall(self.atom + 'link'):
            if link.get('type') != 'application/pdf' or not link.get('href'):
                continue
            candidate = make_candidate(
                link.get('href'),
                self.name,
                identity,
                {
                    'source_paper_id': identifier,
                    'metadata_url': self.config.arxiv_url,
                    'doi': doi,
                    'metadata_title': title,
                    'metadata_year': published[:4],
                    'metadata_authors': authors,
                    'requested_ids': requested,
                },
                'arXiv API PDF link',
            )
            candidate.version_type = 'preprint'
            candidate.provenance[0]['version_type'] = 'preprint'
            candidates.append(candidate)
        return candidates
