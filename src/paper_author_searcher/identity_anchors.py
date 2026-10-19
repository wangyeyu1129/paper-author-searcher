"""Turn caller hints into author-ID anchors: the source IDs that actually wrote papers known to be the person's.

An anchor is independent of the name search, so it separates namesakes and ties split profiles together.
"""

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Protocol
from urllib.parse import quote

from .config import DEFAULT_ANCHORS, DEFAULT_MATCHING, DEFAULT_SOURCES
from .evaluation_matching import bibliographic_name_agrees
from .html_support import decode_response
from .http import HttpClient, SourceError
from .matching import canonical_orcid, name_similarity, normalize
from .models import AnchorEvidence

DOI_IN_TEXT = re.compile(r'\b10\.\d{4,9}/[^\s"\'<>)\]]+', re.IGNORECASE)
ARXIV_IN_TEXT = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})', re.IGNORECASE)
ARXIV_DOI_PREFIX = '10.48550/arxiv.'
ARXIV_HINT = re.compile(r'^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$', re.IGNORECASE)


@dataclass
class IdentityAnchors:
    author_ids: dict[str, set[str]] = field(default_factory=dict)
    orcid: str | None = None
    evidence: list[AnchorEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, source: str, author_id: str, reason: str) -> None:
        self.author_ids.setdefault(source, set()).add(str(author_id))
        self.evidence.append(AnchorEvidence(source, str(author_id), reason))

    def evidence_for(self, source: str, author_id: str) -> list[AnchorEvidence]:
        return [item for item in self.evidence if item.source == source and item.author_id == str(author_id)]

    def contains(self, source: str, author_id: str) -> bool:
        return str(author_id) in self.author_ids.get(source, set())

    def __bool__(self) -> bool:
        return bool(self.author_ids or self.orcid)


class AnchorFinder(Protocol):
    def find(self, query) -> IdentityAnchors: ...


def paper_hints(values: list[str]) -> dict[str, list[str]]:
    """Split free-form known papers into DOIs, arXiv IDs and titles."""
    hints: dict[str, list[str]] = {'doi': [], 'arxiv': [], 'title': []}
    for value in values:
        value = value.strip()
        doi = DOI_IN_TEXT.search(value)
        arxiv = ARXIV_HINT.match(value) or ARXIV_IN_TEXT.search(value)
        if doi:
            hints['doi'].append(doi.group(0).rstrip('.,;').lower())
        elif arxiv:
            hints['arxiv'].append(arxiv.group(1))
        elif value:
            hints['title'].append(value)
    return hints


def homepage_papers(html: str) -> list[str]:
    """DOIs and arXiv IDs linked from a researcher's page, in order of appearance."""
    text = unescape(html)
    found = [match.group(0).rstrip('.,;') for match in DOI_IN_TEXT.finditer(text)]
    found += [match.group(1) for match in ARXIV_IN_TEXT.finditer(text)]
    return list(dict.fromkeys(found))


class ScholarlyAnchorFinder:
    """Resolve hints through OpenAlex and Semantic Scholar paper/author lookups."""

    def __init__(
        self,
        client: HttpClient,
        source_config=DEFAULT_SOURCES,
        matching_config=DEFAULT_MATCHING,
        anchor_config=DEFAULT_ANCHORS,
        openalex_api_key: str | None = None,
        semantic_scholar_api_key: str | None = None,
    ) -> None:
        self.client = client
        self.openalex_url = source_config.openalex_url.rstrip('/')
        self.semantic_scholar_url = source_config.semantic_scholar_url.rstrip('/')
        self.matching = matching_config
        self.config = anchor_config
        self.openalex_key = openalex_api_key
        self.semantic_scholar_key = semantic_scholar_api_key

    def find(self, query) -> IdentityAnchors:
        anchors = IdentityAnchors(orcid=canonical_orcid(query.orcid) if query.orcid else None)
        if anchors.orcid:
            self._guard(anchors, self._by_orcid, query, anchors.orcid)
        values = list(query.known_papers)
        if query.homepage:
            try:
                values += homepage_papers(self._read_page(query.homepage))
            except (SourceError, ValueError) as exc:
                anchors.errors.append(f'homepage: {exc}')
        hints = paper_hints(values)
        budget = self.config.maximum_anchor_papers
        for doi in hints['doi'][:budget]:
            self._guard(anchors, self._by_doi, query, doi)
        for arxiv in hints['arxiv'][:budget]:
            self._guard(anchors, self._by_arxiv, query, arxiv)
        for title in hints['title'][:budget]:
            self._guard(anchors, self._by_title, query, title)
        return anchors

    @staticmethod
    def _guard(anchors, lookup, query, value) -> None:
        try:
            lookup(anchors, query, value)
        except SourceError as exc:
            anchors.errors.append(f'{value}: {exc}')

    def _read_page(self, url: str) -> str:
        response = self.client.probe(url, 1024, self.config.homepage_bytes, self.config.maximum_redirects)
        return decode_response(response)

    def _compatible(self, query, name: str | None) -> bool:
        if not name:
            return False
        # Paper bylines often abbreviate given names ("M. Lin"), which plain similarity rejects.
        return name_similarity(
            query.name, name, self.matching
        ) >= self.matching.compatible_name or bibliographic_name_agrees(query.name, name)

    def _by_orcid(self, anchors, query, orcid) -> None:
        data = self.client.get(
            self.openalex_url + '/authors',
            {'filter': 'orcid:' + orcid, 'select': 'id,display_name', 'api_key': self.openalex_key},
        )
        for item in data.get('results') or []:
            anchors.add('openalex', item['id'].rsplit('/', 1)[-1], 'ORCID ' + orcid)

    def _openalex_authorships(self, anchors, query, work, reason) -> None:
        for authorship in work.get('authorships') or []:
            author = authorship.get('author') or {}
            orcid = canonical_orcid(author.get('orcid')) if author.get('orcid') else None
            if anchors.orcid and orcid and orcid != anchors.orcid:
                continue
            names = [author.get('display_name') or '', authorship.get('raw_author_name') or '']
            if any(self._compatible(query, name) for name in names) and author.get('id'):
                anchors.add('openalex', author['id'].rsplit('/', 1)[-1], reason)

    def _by_doi(self, anchors, query, doi) -> None:
        work = self.client.get(
            self.openalex_url + '/works/doi:' + quote(doi, safe='/'),
            {'select': 'id,doi,authorships', 'api_key': self.openalex_key},
        )
        self._openalex_authorships(anchors, query, work, 'author of DOI ' + doi)
        self._guard(anchors, self._by_semantic_scholar_paper, query, 'DOI:' + doi)

    def _by_arxiv(self, anchors, query, arxiv) -> None:
        # OpenAlex indexes arXiv preprints under their DataCite DOI; Semantic Scholar takes the arXiv ID.
        doi = ARXIV_DOI_PREFIX + arxiv.lower()
        work = self.client.get(
            self.openalex_url + '/works/doi:' + quote(doi, safe='/'),
            {'select': 'id,doi,authorships', 'api_key': self.openalex_key},
        )
        self._openalex_authorships(anchors, query, work, 'author of arXiv ' + arxiv)
        self._guard(anchors, self._by_semantic_scholar_paper, query, 'ARXIV:' + arxiv)

    def _by_semantic_scholar_paper(self, anchors, query, paper_id) -> None:
        headers = {'x-api-key': self.semantic_scholar_key} if self.semantic_scholar_key else None
        data = self.client.get(
            '{}/paper/{}'.format(self.semantic_scholar_url, quote(paper_id, safe=':/')),
            {'fields': 'title,authors'},
            headers,
        )
        for author in data.get('authors') or []:
            if author.get('authorId') and self._compatible(query, author.get('name')):
                anchors.add('semantic_scholar', author['authorId'], 'author of ' + paper_id)

    def _by_title(self, anchors, query, title) -> None:
        data = self.client.get(
            self.openalex_url + '/works',
            {
                'filter': 'title.search:' + title.replace(',', ' '),
                'per-page': self.config.title_search_results,
                'select': 'id,title,authorships',
                'api_key': self.openalex_key,
            },
        )
        for work in data.get('results') or []:
            if normalize(work.get('title') or '') == normalize(title):
                self._openalex_authorships(anchors, query, work, f'author of title "{title[:80]}"')


class NoAnchors:
    def find(self, query) -> IdentityAnchors:
        return IdentityAnchors()
