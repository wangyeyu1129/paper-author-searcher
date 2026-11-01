"""Turn caller hints into author-ID anchors: the source IDs that actually wrote papers known to be the person's.

An anchor is independent of the name search, so it separates namesakes and ties split profiles together.
"""

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from html import unescape
from typing import Any, Protocol
from urllib.parse import quote, urljoin, urlsplit

from .config import DEFAULT_ANCHORS, DEFAULT_DISCOVERY, DEFAULT_MATCHING, DEFAULT_SOURCES
from .evaluation_matching import bibliographic_name_agrees
from .homepage_citations import (
    ARXIV_IN_TEXT,
    DOI_IN_TEXT,
    CitationParser,
    CitationRanker,
    DocumentParser,
    HomepageCitation,
    HomepageReader,
    HtmlCitationParser,
    PdfCitationParser,
    is_pdf,
    mentions,
    pdf_text,
)
from .html_support import LinkExtractor, decode_response
from .http import HttpClient, SourceError
from .identity_discovery import Discovery, NoDiscovery
from .matching import (
    ARXIV_DOI_PREFIX,
    canonical_doi,
    canonical_orcid,
    name_similarity,
    normalize,
    normalize_title,
    school_named,
    work_keys,
)
from .models import AnchorEvidence
from .profile_links import ORCID_SOURCE, ProfileLink, ProfileLinkParser
from .publication_extraction import html_text
from .status import AnchorKind

logger = logging.getLogger(__name__)

# Added by the resolver itself when a candidate record carries the supplied ORCID; not an anchor-finder result.
SUPPLIED_ORCID_REASON = 'ORCID matches the supplied ORCID'
ARXIV_HINT = re.compile(r'^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$', re.IGNORECASE)


@dataclass
class IdentityAnchors:
    author_ids: dict[str, set[str]] = field(default_factory=dict)
    orcid: str | None = None
    evidence: list[AnchorEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Work keys (matching.work_keys) of every paper the person's own page lists, beyond the anchor budget.
    listed_works: list[str] = field(default_factory=list)
    # What discovery found (identity_discovery.Discovery.to_dict), recorded so a replay can restore it.
    discovered: dict[str, Any] = field(default_factory=dict)
    # Citations on the person's pages that link a PDF (listed_pdf), for the PDF source 'homepage'.
    listed_pdfs: list[dict[str, Any]] = field(default_factory=list)
    # Listed works whose only compatible author carries another ORCID: (source, author ID) -> {work key: reason}.
    contested: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def add(
        self, source: str, author_id: str, reason: str, kind: AnchorKind = AnchorKind.PAPER, work: str = ''
    ) -> None:
        self.author_ids.setdefault(source, set()).add(str(author_id))
        self.evidence.append(AnchorEvidence(source, str(author_id), reason, kind, work))

    def contest(self, source: str, author_id: str, reason: str, work: str) -> None:
        self.contested.setdefault((source, str(author_id)), {})[work or reason] = reason

    def settle_contested(self, minimum_works: int) -> None:
        """Anchor the contested authors that hold at least ``minimum_works`` distinct listed works."""
        for (source, author_id), works in self.contested.items():
            if len(works) >= minimum_works:
                for work, reason in works.items():
                    self.add(source, author_id, reason, AnchorKind.CONTESTED, work if work != reason else '')
        self.contested = {}

    def orcid_outweighed(self, source: str, author_id: str) -> bool:
        """Whether the person's own works outweigh the ORCID this author ID carries in its database."""
        return any(item.kind == AnchorKind.CONTESTED for item in self.evidence_for(source, author_id))

    def evidence_for(self, source: str, author_id: str) -> list[AnchorEvidence]:
        return [item for item in self.evidence if item.source == source and item.author_id == str(author_id)]

    def contains(self, source: str, author_id: str) -> bool:
        return str(author_id) in self.author_ids.get(source, set())

    def __bool__(self) -> bool:
        return bool(self.author_ids or self.orcid)


class ListedWorkSource(Protocol):
    """Adds works to the person's own list (``IdentityAnchors.listed_works``), e.g. an OpenReview profile."""

    name: str

    def works(self, query) -> set[str]: ...


class AnchorFinder(Protocol):
    def find(self, query) -> IdentityAnchors: ...


class Discoverer(Protocol):
    def discover(self, query) -> Discovery: ...


@dataclass(frozen=True)
class PageToRead:
    url: str
    # A page the caller supplied may be a personal page listing only the person's works; on a discovered page (and
    # on a lab page) only the citations that name the person count.
    strict: bool
    # A search hit may be a namesake's page: without the requested school in its text it is not read for papers.
    must_name_school: bool = False


@dataclass(frozen=True)
class _ReadPage:
    citations: list[HomepageCitation]
    text: str


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


def own_citations(citations: list[HomepageCitation], name: str, strict: bool = False) -> list[HomepageCitation]:
    """On a lab page only the citations naming the person are theirs; a supplied personal page lists only theirs."""
    named = [citation for citation in citations if mentions(name, citation.text)]
    return named if named or strict else citations


def _listed_works(citations: list[HomepageCitation], name: str) -> set[str]:
    own = own_citations(citations, name)
    keys: set[str] = set()
    for citation in own:
        keys |= work_keys(citation.dois, citation.arxiv_ids, citation.titles)
    return keys


def listed_pdfs(citations: list[HomepageCitation], page: str, config=DEFAULT_ANCHORS) -> list[dict[str, Any]]:
    """One record per PDF link of a single citation; a block longer than one citation holds several, so its links
    are not tied to any one of them."""
    records: dict[str, dict[str, Any]] = {}
    for citation in citations:
        if not citation.pdf_links or len(citation.text) > config.listed_pdf_citation_characters:
            continue
        for url in citation.pdf_links:
            records.setdefault(
                url,
                {
                    'url': url,
                    'page': page,
                    'citation': citation.text,
                    'dois': list(citation.dois),
                    'arxiv_ids': list(citation.arxiv_ids),
                },
            )
    return list(records.values())


def _key(dois: Iterable[str] = (), arxiv_ids: Iterable[str] = (), titles: Iterable[str] = ()) -> str:
    return next(iter(sorted(work_keys(dois, arxiv_ids, titles))), '')


def _merge(*lists: list[str]) -> list[str]:
    return list(dict.fromkeys(value for values in lists for value in values))


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
        citation_parser: CitationParser | None = None,
        pdf_parser: DocumentParser | None = None,
        ranker: CitationRanker | None = None,
        listed_sources: Sequence[ListedWorkSource] = (),
        discovery: Discoverer | None = None,
        profile_links: ProfileLinkParser | None = None,
        discovery_config=DEFAULT_DISCOVERY,
    ) -> None:
        self.client = client
        self.discovery = discovery or NoDiscovery()
        self.profile_links = profile_links or ProfileLinkParser()
        self.discovery_config = discovery_config
        self.citation_parser = citation_parser or HtmlCitationParser(anchor_config)
        self.reader = HomepageReader(self.citation_parser, pdf_parser or PdfCitationParser(anchor_config))
        self.ranker = ranker or CitationRanker(anchor_config)
        self.listed_sources = list(listed_sources)
        self.openalex_url = source_config.openalex_url.rstrip('/')
        self.orcid_url = source_config.orcid_api_url.rstrip('/')
        self.semantic_scholar_url = source_config.semantic_scholar_url.rstrip('/')
        self.matching = matching_config
        self.config = anchor_config
        self.openalex_key = openalex_api_key
        self.semantic_scholar_key = semantic_scholar_api_key

    def find(self, query) -> IdentityAnchors:
        links = self.profile_links.parse_all(query.links())
        supplied_orcid = query.orcid or next((link.identifier for link in links if link.source == ORCID_SOURCE), None)
        if supplied_orcid and not query.orcid:
            query = replace(query, orcid=supplied_orcid)
        discovery = self.discovery.discover(query)
        orcid = canonical_orcid(supplied_orcid) if supplied_orcid else discovery.orcid
        anchors = IdentityAnchors(orcid=orcid, discovered=discovery.to_dict())
        anchors.errors.extend(f'discovery {error}' for error in discovery.errors)
        self._profile_anchors(anchors, links)
        if anchors.orcid:
            self._guard(anchors, self._by_orcid, query, anchors.orcid)
            if self.config.orcid_listed_works:
                self._guard(anchors, self._orcid_works, query, anchors.orcid)
        hints = paper_hints(list(query.known_papers))
        page_titles: list[str] = []
        supplied = query.pages()
        pages = [PageToRead(url, strict=False) for url in supplied]
        pages += [
            PageToRead(page.url, strict=True, must_name_school=not page.vouched)
            for page in discovery.best_pages(self.discovery_config.maximum_pages_read)
            if page.url not in supplied
        ]
        room = self.discovery_config.maximum_pages
        for page in pages:
            if page.strict and room < 1:
                break
            try:
                read = self._read_page(page.url)
                if page.must_name_school and not self._names_school(query, page.url, read.text):
                    anchors.discovered['evidence'].append(f'Page {page.url} not used: it does not name the school')
                    continue
                citations = self.ranker.rank(read.citations, query.name)
            except (SourceError, ValueError) as exc:
                anchors.errors.append(f'page {page.url}: {exc}')
                continue
            room -= page.strict
            # Every citation's PDF stays available: a personal page's citations often omit the person's own name
            # ("with A and B"), and a PDF only ever serves a paper its citation names and its content shows.
            pdf_citations = citations
            if page.strict:
                citations = own_citations(citations, query.name, strict=True)
            # The caller's own hints first, then the page's citations that name the person, then the rest.
            hints['doi'] = _merge(hints['doi'], [doi.lower() for item in citations for doi in item.dois])
            hints['arxiv'] = _merge(hints['arxiv'], [arxiv for item in citations for arxiv in item.arxiv_ids])
            page_titles = _merge(page_titles, [title for item in citations for title in item.titles])
            anchors.listed_works = sorted(set(anchors.listed_works) | _listed_works(citations, query.name))
            known = {item['url'] for item in anchors.listed_pdfs}
            new = [item for item in listed_pdfs(pdf_citations, page.url, self.config) if item['url'] not in known]
            anchors.listed_pdfs += new[: max(0, self.config.maximum_listed_pdfs - len(anchors.listed_pdfs))]
        for source in self.listed_sources:
            try:
                anchors.listed_works = sorted(set(anchors.listed_works) | source.works(query))
            except (SourceError, ValueError) as exc:
                anchors.errors.append(f'{source.name}: {exc}')
        budget = self.config.maximum_anchor_papers
        dois, arxiv_ids = hints['doi'][:budget], hints['arxiv'][:budget]
        # A page that links no identifiers (titles only) is still usable: exact-title lookups fill the budget left.
        title_budget = min(self.config.maximum_title_anchors, max(0, budget - len(dois) - len(arxiv_ids)))
        titles = hints['title'][:budget] + page_titles[:title_budget]
        for doi in dois:
            self._guard(anchors, self._by_doi, query, doi)
        for arxiv in arxiv_ids:
            self._guard(anchors, self._by_arxiv, query, arxiv)
        for title in titles:
            self._guard(anchors, self._by_title, query, title)
        anchors.settle_contested(self.config.minimum_contested_works)
        return anchors

    @staticmethod
    def _guard(anchors, lookup, query, value) -> None:
        try:
            lookup(anchors, query, value)
        except SourceError as exc:
            anchors.errors.append(f'{value}: {exc}')

    def _profile_anchors(self, anchors: IdentityAnchors, links: list[ProfileLink]) -> None:
        """A caller-supplied profile link of an author-ID source anchors that ID as given."""
        for link in links:
            if link.source in self.config.profile_anchor_sources:
                anchors.add(link.source, link.identifier, 'profile link ' + link.url, AnchorKind.PROFILE)

    def _names_school(self, query, url: str, text: str) -> bool:
        # Host words count as printed in capitals, so economics.mit.edu names MIT.
        host = (urlsplit(url).hostname or '').replace('.', ' ').upper()
        return school_named([query.university, *query.university_aliases], f'{host}\n{text}', self.matching)

    def _read_page(self, url: str) -> _ReadPage:
        """The page's citations and text, plus those of the publication list or CV it links to on the same site."""
        page, html, served = self._read_citations(url)
        if html is None or not self.discovery_config.follow_publication_links:
            return page
        citations, texts = list(page.citations), [page.text]
        for link in publication_links(html, served, self.discovery_config)[
            : self.discovery_config.maximum_followed_links
        ]:
            try:
                followed = self._read_citations(link)[0]
            except (SourceError, ValueError) as exc:
                logger.debug('Publication link %s unreadable: %s', link, exc)
                continue
            citations += followed.citations
            texts.append(followed.text)
        return _ReadPage(citations, '\n'.join(texts))

    def _read_citations(self, url: str) -> tuple[_ReadPage, str | None, str]:
        """One page's citations and text, its HTML (None for a PDF) for following, and the URL it was served from."""
        # One limit for both kinds: an HTML page's text and a PDF CV's whole body.
        limit = self.config.homepage_bytes
        response = self.client.probe(url, limit, limit, self.config.maximum_redirects)
        decoded: list[str] = []

        def decode() -> str:
            decoded.append(decode_response(response))
            return decoded[-1]

        citations = self.reader.citations(response.content_type, response.prefix, decode)
        # Relative links resolve against the page as served, after redirects.
        base = getattr(response, 'url', None) or url
        citations = [
            replace(item, pdf_links=tuple(urljoin(base, unescape(link)) for link in item.pdf_links))
            for item in citations
        ]
        html = decoded[-1] if decoded else None
        pdf = is_pdf(response.content_type, response.prefix)
        text = pdf_text(response.prefix) if pdf else html_text(html or decode())
        return _ReadPage(citations, text), html, base

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
            anchors.add('openalex', item['id'].rsplit('/', 1)[-1], 'ORCID ' + orcid, AnchorKind.ORCID)

    def _orcid_works(self, anchors, query, orcid) -> None:
        """The ORCID record's works (identifiers and titles) join the person's own list."""
        data = self.client.get(f'{self.orcid_url}/{orcid}/works', None, {'Accept': 'application/json'})
        keys: set[str] = set()
        for group in data.get('group') or []:
            identifiers = (group.get('external-ids') or {}).get('external-id') or []
            dois = [item['external-id-value'] for item in identifiers if item.get('external-id-type') == 'doi']
            arxiv = [item['external-id-value'] for item in identifiers if item.get('external-id-type') == 'arxiv']
            summaries = group.get('work-summary') or [{}]
            title = (((summaries[0] or {}).get('title') or {}).get('title') or {}).get('value')
            keys |= work_keys(dois, arxiv, [] if dois or arxiv else [title] if title else [])
        anchors.listed_works = sorted(set(anchors.listed_works) | keys)

    def _openalex_authorships(self, anchors, query, work, reason, work_key='') -> None:
        """Anchor the work's compatible authors. One whose author record carries another ORCID is only contested
        (see ``IdentityAnchors.settle_contested``), and only when no compatible author agrees with the ORCID."""
        agreeing: list[str] = []
        conflicting: list[str] = []
        for authorship in work.get('authorships') or []:
            author = authorship.get('author') or {}
            names = [author.get('display_name') or '', authorship.get('raw_author_name') or '']
            if not author.get('id') or not any(self._compatible(query, name) for name in names):
                continue
            orcid = canonical_orcid(author.get('orcid')) if author.get('orcid') else None
            target = conflicting if anchors.orcid and orcid and orcid != anchors.orcid else agreeing
            target.append(author['id'].rsplit('/', 1)[-1])
        for author_id in agreeing:
            anchors.add('openalex', author_id, reason, AnchorKind.PAPER, work_key)
        for author_id in conflicting if not agreeing else []:
            anchors.contest('openalex', author_id, reason, work_key)

    def _by_doi(self, anchors, query, doi) -> None:
        work = self.client.get(
            self.openalex_url + '/works/doi:' + quote(doi, safe='/'),
            {'select': 'id,doi,authorships', 'api_key': self.openalex_key},
        )
        self._openalex_authorships(anchors, query, work, 'author of DOI ' + doi, _key(dois=[doi]))
        self._guard(anchors, self._by_semantic_scholar_paper, query, 'DOI:' + doi)

    def _by_arxiv(self, anchors, query, arxiv) -> None:
        # OpenAlex indexes arXiv preprints under their DataCite DOI; Semantic Scholar takes the arXiv ID.
        doi = ARXIV_DOI_PREFIX + arxiv.lower()
        work = self.client.get(
            self.openalex_url + '/works/doi:' + quote(doi, safe='/'),
            {'select': 'id,doi,authorships', 'api_key': self.openalex_key},
        )
        self._openalex_authorships(anchors, query, work, 'author of arXiv ' + arxiv, _key(arxiv_ids=[arxiv]))
        self._guard(anchors, self._by_semantic_scholar_paper, query, 'ARXIV:' + arxiv)

    def _by_semantic_scholar_paper(self, anchors, query, paper_id) -> None:
        headers = {'x-api-key': self.semantic_scholar_key} if self.semantic_scholar_key else None
        data = self.client.get(
            '{}/paper/{}'.format(self.semantic_scholar_url, quote(paper_id, safe=':/')),
            {'fields': 'title,authors'},
            headers,
        )
        namespace, _, value = paper_id.partition(':')
        work_key = _key(dois=[value]) if namespace == 'DOI' else _key(arxiv_ids=[value])
        for author in data.get('authors') or []:
            if author.get('authorId') and self._compatible(query, author.get('name')):
                anchors.add('semantic_scholar', author['authorId'], 'author of ' + paper_id, AnchorKind.PAPER, work_key)

    def _by_title(self, anchors, query, title) -> None:
        data = self.client.get(
            self.openalex_url + '/works',
            {
                'filter': 'title.search:' + title.replace(',', ' '),
                'per-page': self.config.title_search_results,
                'select': 'id,title,doi,authorships',
                'api_key': self.openalex_key,
            },
        )
        for work in data.get('results') or []:
            if normalize_title(work.get('title') or '') == normalize_title(title):
                self._openalex_authorships(
                    anchors, query, work, f'author of title "{title[:80]}"', _key(titles=[title])
                )
                # Anchor Semantic Scholar as well, as a DOI hint does: once anchors exist, an unanchored ID cannot
                # corroborate the other database, so a one-sided anchor would cost the cross-source evidence.
                doi = canonical_doi(work['doi']).removeprefix('doi:') if work.get('doi') else None
                if doi:
                    self._guard(anchors, self._by_semantic_scholar_paper, query, 'DOI:' + doi)


class RecordedAnchorFinder:
    """Replays the anchors an earlier run found, read from its saved identity, so an offline replay reproduces
    them without fetching homepages or anchor papers again."""

    def __init__(
        self,
        evidence: Iterable[AnchorEvidence],
        listed_works: Iterable[str] = (),
        discovered: Mapping | None = None,
        listed_pdfs: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.evidence = [item for item in evidence if item.reason != SUPPLIED_ORCID_REASON]
        self.listed_works = sorted(set(listed_works))
        self.discovered = dict(discovered or {})
        self.listed_pdfs = [dict(item) for item in listed_pdfs]

    @classmethod
    def from_identity(cls, identity: Mapping[str, Any]) -> 'RecordedAnchorFinder':
        profiles = [identity.get('profile') or {}, *(identity.get('candidates') or [])]
        recorded = [item for profile in profiles for item in (profile or {}).get('identity_anchors') or []]
        evidence = [AnchorEvidence.coerce(item) for item in recorded]
        listed = [key for profile in profiles for key in (profile or {}).get('listed_works') or []]
        discovered: Mapping[str, Any] = next(
            (profile['discovered'] for profile in profiles if (profile or {}).get('discovered')), {}
        )
        pdfs: list[Mapping[str, Any]] = next(
            (profile['listed_pdfs'] for profile in profiles if (profile or {}).get('listed_pdfs')), []
        )
        return cls(dict.fromkeys(evidence), listed, discovered, pdfs)

    def find(self, query) -> IdentityAnchors:
        orcid = query.orcid or self.discovered.get('orcid')
        anchors = IdentityAnchors(orcid=canonical_orcid(orcid) if orcid else None, discovered=dict(self.discovered))
        for item in self.evidence:
            anchors.add(item.source, item.author_id, item.reason, item.kind, item.work)
        anchors.listed_works = list(self.listed_works)
        anchors.listed_pdfs = [dict(item) for item in self.listed_pdfs]
        return anchors


class NoAnchors:
    def find(self, query) -> IdentityAnchors:
        return IdentityAnchors()


def publication_links(html: str, base_url: str, config=DEFAULT_DISCOVERY) -> list[str]:
    """Same-site links whose text or path names a publication list or CV, in page order."""
    extractor = LinkExtractor()
    extractor.feed(html)
    extractor.close()
    host = urlsplit(base_url).hostname
    markers = tuple(normalize(marker) for marker in config.publication_markers)
    found = []
    for link in extractor.links:
        url = urljoin(base_url, str(link.get('href') or '')).split('#', 1)[0]
        parts = urlsplit(url)
        label = normalize(f'{link.get("text") or ""} {parts.path}')
        same_site = parts.scheme in ('http', 'https') and parts.hostname == host and url != base_url
        if same_site and any(marker in label.split() or marker in parts.path.lower() for marker in markers):
            found.append(url)
    return list(dict.fromkeys(found))
