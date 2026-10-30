"""Metadata sources: HTTP behaviour, source endpoints, CS sources, name searches, local catalogs and the DBLP dump."""

from dataclasses import dataclass

MAXIMUM_ZLIB_LEVEL = 9


@dataclass(frozen=True)
class HttpConfig:
    timeout: float = 15
    retries: int = 2
    response_limit: int = 8_000_000
    html_preview_bytes: int = 1000
    maximum_retry_delay: float = 5
    retry_backoff_base: float = 2
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    user_agent: str = 'paper-author-searcher/0.1'
    # Minimum seconds between requests to a host, as its terms require (arXiv asks for 3 s). Applied below the
    # cache, so cached and replayed requests never wait.
    host_request_intervals: tuple[tuple[str, float], ...] = (('export.arxiv.org', 3.0),)
    # A host's 429 Retry-After holds back its later requests for at most this long (a daily quota says hours).
    maximum_host_hold_seconds: float = 60.0


@dataclass(frozen=True)
class SourceConfig:
    openalex_url: str = 'https://api.openalex.org'
    semantic_scholar_url: str = 'https://api.semanticscholar.org/graph/v1'
    dblp_url: str = 'https://dblp.org'
    author_limit: int = 25
    dblp_author_limit: int = 10
    enrichment_limit: int = 8
    works_limit: int = 50
    # Name-only search returns the 25 most prominent namesakes; for common names the target is often absent.
    institution_scoped_search: bool = True
    institution_limit: int = 2
    # Enabled identity sources, in order (names registered in source_catalog.IDENTITY_SOURCES).
    identity_sources: tuple[str, ...] = ('openalex', 'semantic_scholar', 'dblp')
    orcid_api_url: str = 'https://pub.orcid.org/v3.0'
    crossref_url: str = 'https://api.crossref.org'
    arxiv_url: str = 'https://export.arxiv.org/api/query'
    europepmc_url: str = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
    core_url: str = 'https://api.core.ac.uk/v3'


DEFAULT_HTTP = HttpConfig()
DEFAULT_SOURCES = SourceConfig()


@dataclass(frozen=True)
class CSSourcesConfig:
    """Computer-science sources beyond the author-ID databases.

    arXiv author search collects the person's recent preprints by name; they still need ownership evidence
    (affiliation, recurring coauthors, the person's own list). OpenReview profiles are maintained by their owners,
    so a profile matching the name and the requested institution contributes its papers to the person's own list;
    its API needs an account token (credentials.openreview_token), without which it is skipped.
    """

    arxiv_author_search: bool = True
    arxiv_recent_years: int = 6
    arxiv_page_size: int = 100
    arxiv_maximum_results: int = 300
    openreview: bool = True
    openreview_url: str = 'https://api2.openreview.net'
    openreview_maximum_notes: int = 1000

    def __post_init__(self) -> None:
        if min(self.arxiv_recent_years, self.arxiv_page_size, self.openreview_maximum_notes) < 1:
            raise ValueError('arXiv years, page size and OpenReview note limit must be positive')
        if self.arxiv_maximum_results < 0:
            raise ValueError('arXiv result cap must be nonnegative')


DEFAULT_CS_SOURCES = CSSourcesConfig()


@dataclass(frozen=True)
class NameSearchConfig:
    """Sources searched by the person's name and institution after the author-ID collection (registered in
    ``source_catalog.NAME_SEARCHES``, run in this order). Their records carry no author ID of the person: each
    names the one author whose name is the person's (and whose ORCID, when printed, is theirs), and ownership
    verifies it like any other record."""

    sources: tuple[str, ...] = ('arxiv', 'europepmc', 'crossref', 'repositories')
    # Publication years searched, counting back from the current one.
    recent_years: int = 6
    page_size: int = 100
    # Europe PMC's full ("core") records are large: a smaller page keeps each response under the HTTP size limit.
    europepmc_page_size: int = 25
    maximum_results: int = 500
    # Crossref ranks by relevance and does not filter by affiliation: keep only works where the person's own
    # byline entry prints the requested institution.
    crossref_require_affiliation: bool = True

    def __post_init__(self) -> None:
        if min(self.recent_years, self.page_size, self.europepmc_page_size, self.maximum_results) < 1:
            raise ValueError('Name search limits must be positive')


DEFAULT_NAME_SEARCHES = NameSearchConfig()


@dataclass(frozen=True)
class CatalogConfig:
    """Venue proceedings (ACL Anthology, PMLR, CVF) and institutional repositories (OAI-PMH) as one local store.

    ``paper-catalog-index`` downloads each source's bulk listing once and indexes its entries by work key and
    author name; with ``store_path`` set, listed works are looked up there (``WORK_LOOKUPS['catalogs']``) and the
    repositories of the requested institution are searched by name (``NAME_SEARCHES['repositories']``).
    """

    # Empty: no store. Set it, e.g. PAPER_SEARCH__CATALOGS__STORE_PATH, once built.
    store_path: str = ''
    sources: tuple[str, ...] = ('acl_anthology', 'pmlr', 'cvf', 'oai_pmh')
    acl_anthology_url: str = 'https://aclanthology.org/anthology.bib.gz'
    pmlr_bibliography_url: str = 'https://proceedings.mlr.press/v{volume}/assets/bib/bibliography.bib'
    # ICML 2020-2025 and AISTATS 2021-2025.
    pmlr_volumes: tuple[int, ...] = (119, 139, 162, 202, 235, 267, 130, 151, 206, 238, 258)
    cvf_url: str = 'https://openaccess.thecvf.com'
    cvf_conferences: tuple[str, ...] = (
        'CVPR2021',
        'CVPR2022',
        'CVPR2023',
        'CVPR2024',
        'CVPR2025',
        'ICCV2021',
        'ICCV2023',
        'ICCV2025',
        'WACV2024',
        'WACV2025',
    )
    # OAI-PMH repositories as (institution, endpoint, set); an empty set harvests the whole repository.
    oai_repositories: tuple[tuple[str, ...], ...] = ()
    # Harvest records added or changed since this date (YYYY-MM-DD); empty harvests everything.
    oai_from: str = ''
    oai_maximum_pages: int = 2000
    # Largest listing today: the ACL Anthology export, 13.5 MB. The download reads into a buffer of this size.
    maximum_download_bytes: int = 64_000_000

    def __post_init__(self) -> None:
        if self.maximum_download_bytes < 1 or self.oai_maximum_pages < 1:
            raise ValueError('Catalog download and harvest limits must be positive')
        if any(len(repository) not in (2, 3) for repository in self.oai_repositories):
            raise ValueError('oai_repositories entries are (institution, endpoint[, set])')


DEFAULT_CATALOGS = CatalogConfig()


@dataclass(frozen=True)
class DblpDumpConfig:
    """DBLP from its official data dump (https://dblp.org/xml/dblp.xml.gz) instead of the API, which answers
    automated clients with an anti-bot page. ``paper-dblp-index`` builds an SQLite index from the dump; while the
    index exists, the DBLP identity, paper and listed-work sources read it and never call dblp.org."""

    # Empty: no index (the DBLP API is used). Set it, e.g. PAPER_SEARCH__DBLP_DUMP__INDEX_PATH, once built.
    index_path: str = ''
    # Publication elements indexed (person pages are the 'www' elements keyed 'homepages/').
    record_tags: tuple[str, ...] = (
        'article',
        'inproceedings',
        'proceedings',
        'book',
        'incollection',
        'phdthesis',
        'mastersthesis',
    )
    # Child elements kept per record; the rest (urls, crossrefs, page numbers) is dropped to keep the index small.
    kept_fields: tuple[str, ...] = ('author', 'editor', 'title', 'year', 'journal', 'booktitle', 'ee', 'volume')
    # Rows written per transaction while building.
    batch_size: int = 50000
    # zlib level for the stored XML (1 fastest .. 9 smallest).
    compression_level: int = 6

    def __post_init__(self) -> None:
        if self.batch_size < 1 or not 1 <= self.compression_level <= MAXIMUM_ZLIB_LEVEL:
            raise ValueError('batch_size must be positive and compression_level between 1 and 9')


DEFAULT_DBLP_DUMP = DblpDumpConfig()
