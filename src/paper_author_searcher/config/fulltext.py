"""Full text: PDF link discovery and validation, downloads, storage."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PDFConfig:
    unpaywall_url: str = 'https://api.unpaywall.org/v2'
    crossref_url: str = 'https://api.crossref.org'
    crossref_search_results: int = 5
    semantic_scholar_batch_size: int = 100
    semantic_scholar_request_interval: float = 1.0
    semantic_scholar_cooldown: float = 30.0
    semantic_scholar_title_search: bool = True
    semantic_scholar_search_results: int = 5
    arxiv_url: str = 'https://export.arxiv.org/api/query'
    preview_bytes: int = 1024
    html_metadata_bytes: int = 65536
    maximum_redirects: int = 5
    arxiv_request_interval: float = 3.0
    arxiv_title_search: bool = True
    arxiv_search_results: int = 5
    catalog_preview_bytes: int = 2 * 1024 * 1024
    linked_page_limit: int = 5
    public_page_cache_entries: int = 256
    public_page_maximum_bytes: int = 1024 * 1024
    public_page_request_interval: float = 1.0
    public_page_cooldown: float = 120.0
    public_page_cache_seconds: float = 3600.0
    public_page_cache_bytes: int = 8 * 1024 * 1024
    source_maximum_cooldown: float = 900.0
    redirect_failure_budget: int = 3
    # Identity-bound links that failed transiently (host cooldown, timeout, 5xx, 429) are probed again
    # after the run, once their hosts' cooldowns expire; the wait for that is capped. 403/404 are final.
    transient_retry_rounds: int = 1
    transient_retry_maximum_wait: float = 120.0
    # Papers resolved concurrently; each source still paces its own hosts.
    lookup_workers: int = 4
    # Full collected records live in papers.json; reports keep a bibliographic view plus entry_index.
    # True embeds the full record (self-contained but duplicated) in the PDF report and manifest.
    embed_full_metadata: bool = False
    # Enabled PDF sources, in lookup order (names registered in source_catalog.PDF_SOURCES). Later sources
    # see the landing pages earlier ones found.
    sources: tuple[str, ...] = (
        'existing_metadata',
        'openalex',
        'semantic_scholar',
        'crossref',
        'unpaywall',
        'europepmc',
        'openaire',
        'nber',
        'arxiv',
        'journal_catalog',
        'public_pages',
        'homepage',
    )
    # Europe PMC full-text links accepted as PDFs: availability codes for open access ("OA") and free to read
    # ("F", e.g. a PubMed Central copy under a funder mandate). Subscription links ("S") are never followed.
    europepmc_free_codes: tuple[str, ...] = ('OA', 'F')
    # OpenAIRE Graph (no key): copies of a work in repositories it aggregates, used only when it rates the work's
    # best access right as one of these labels.
    openaire_url: str = 'https://api.openaire.eu/graph/v1/researchProducts'
    openaire_open_labels: tuple[str, ...] = ('OPEN',)
    # NBER working papers are free to download. Their DOIs carry the paper number ("10.3386/w31185"), which names
    # the PDF; no lookup request is needed.
    nber_doi_pattern: str = r'^10\.3386/(w\d+)$'
    nber_pdf_url_template: str = 'https://www.nber.org/system/files/working_papers/{number}/{number}.pdf'
    # Web search (the provider chosen by DiscoveryConfig.web_search) for verified papers still without a PDF once
    # every source above has answered. Works are searched newest first, each once, up to this many per run; 0 disables
    # it. A hit is only ever a candidate: the downloaded file's own title and authors decide (pdf_file_validation).
    web_search_maximum_papers: int = 25
    web_search_results: int = 10
    web_search_query: str = '{title} {authors}'
    web_search_query_authors: int = 3
    # A hit's URL names a file when this matches; any other hit is read as a landing page, if it names the paper.
    web_search_pdf_url_pattern: str = r'\.pdf(?:$|[?#/])|/pdf(?:$|[/?#])|/download(?:$|[/?#])|/bitstream/'
    web_search_landing_pages: int = 2
    # The service keeps to legal copies. Hosts of shadow libraries (their domains change, so by name) and of
    # third-party uploads are never queried.
    web_search_excluded_host_words: tuple[str, ...] = ('sci-hub', 'scihub', 'libgen', 'annas-archive', 'z-lib', 'zlib')
    web_search_excluded_hosts: tuple[str, ...] = (
        'academia.edu',
        'blogspot.com',
        'coursehero.com',
        'dokumen.pub',
        'github.com',
        'githubusercontent.com',
        'huggingface.co',
        'pdfdrive.com',
        'scribd.com',
        'studocu.com',
        'vdoc.pub',
        'wordpress.com',
    )
    # Answers that mean the key or its quota is unusable: the rest of the run does not search.
    web_search_stop_statuses: tuple[int, ...] = (401, 402, 403, 429, 432, 433)
    # A homepage citation names a paper without an identifier only by its full normalized title, of at least this
    # many words (a shorter title is common enough to appear inside another citation).
    homepage_title_minimum_words: int = 4
    bibliography_urls: tuple[str, ...] = ()
    journal_catalogs: tuple[tuple[str, str], ...] = (
        ('Journal of Machine Learning Research', 'https://jmlr.org/papers/'),
    )
    # Binding a URL to a paper without a shared DOI (pdf_identity.metadata_identity). A preprint is often dated a
    # year before its venue edition. An own arXiv edition whose title changed needs this many shared author
    # signatures (all of a shorter list).
    identity_year_tolerance: int = 1
    identity_shared_authors: int = 2
    version_preference: tuple[str, ...] = (
        'published_version',
        'accepted_manuscript',
        'preprint',
        'submitted_manuscript',
        'unknown',
    )

    def __post_init__(self) -> None:
        if self.preview_bytes < 5 or self.html_metadata_bytes < self.preview_bytes:
            raise ValueError('PDF preview must fit the signature and HTML limit must cover the preview')
        if self.maximum_redirects < 0 or self.arxiv_request_interval < 0:
            raise ValueError('Redirect and interval limits must be nonnegative')
        if self.arxiv_search_results < 1 or self.catalog_preview_bytes < self.html_metadata_bytes:
            raise ValueError('Search and catalog limits must be positive and sufficient')
        if not 1 <= self.semantic_scholar_batch_size <= 500:
            raise ValueError('Semantic Scholar batch size must be between 1 and 500')
        if self.semantic_scholar_request_interval < 0 or self.semantic_scholar_cooldown < 0:
            raise ValueError('Semantic Scholar request intervals must be nonnegative')
        if (
            min(
                self.crossref_search_results,
                self.semantic_scholar_search_results,
                self.linked_page_limit,
                self.public_page_cache_entries,
                self.redirect_failure_budget,
            )
            < 1
        ):
            raise ValueError('Discovery and cache limits must be positive')
        if self.web_search_maximum_papers < 0 or self.web_search_results < 1 or self.web_search_landing_pages < 0:
            raise ValueError('Web search limits must be nonnegative and return at least one result')
        if self.lookup_workers < 1:
            raise ValueError('lookup_workers must be positive')
        if self.transient_retry_rounds < 0 or self.transient_retry_maximum_wait < 0:
            raise ValueError('Transient retry rounds and wait must be nonnegative')
        if self.identity_year_tolerance < 0 or self.identity_shared_authors < 1:
            raise ValueError('identity_year_tolerance must be nonnegative and identity_shared_authors positive')
        if self.public_page_maximum_bytes < self.html_metadata_bytes:
            raise ValueError('Public page maximum must cover the initial metadata preview')
        if self.public_page_cache_bytes < self.public_page_maximum_bytes:
            raise ValueError('Page cache must accommodate at least one maximum-size response')
        if (
            min(
                self.public_page_request_interval,
                self.public_page_cooldown,
                self.public_page_cache_seconds,
                self.source_maximum_cooldown,
            )
            < 0
        ):
            raise ValueError('Scheduling and cache intervals must be nonnegative')


DEFAULT_PDF = PDFConfig()


@dataclass(frozen=True)
class DownloadConfig:
    maximum_file_bytes: int = 50 * 1024 * 1024
    chunk_bytes: int = 64 * 1024
    maximum_transfer_seconds: float = 300
    maximum_redirects: int = DEFAULT_PDF.maximum_redirects
    allowed_ports: tuple[int, ...] = (80, 443)
    filename_bytes: int = 180
    filename_hash_characters: int = 16
    parser_timeout: float = 30
    parser_memory_bytes: int = 768 * 1024 * 1024
    maximum_pages: int = 2000
    maximum_page_content_bytes: int = 8 * 1024 * 1024
    identity_pages: int = 2
    title_lines: int = 30
    maximum_extracted_characters: int = 20000
    minimum_title_words_without_doi: int = 4
    # Public PDFs whose URL metadata could not name the paper (a repository copy, a web search hit), downloaded per
    # paper when no bound URL confirms it. Such a file counts only if its own content shows the paper.
    maximum_content_checks: int = 6
    # Words text extraction glues to the end of a DOI ("10.1101/2023.12.14.571777doi: bioRxiv preprint"); one is
    # dropped when it follows a digit, so the printed DOI is not mistaken for another one.
    doi_glued_labels: tuple[str, ...] = ('doi', 'http', 'https', 'www')
    # Typesetting remnants LaTeX leaves in a PDF's title metadata: an \includegraphics option list and image path
    # ("[width=2em,height=2em]assets/logo.png AxBench: ..."). Removed before the title is compared.
    title_artifact_pattern: str = r'\[[^\]]*\]\s*\S+\.(?:png|jpe?g|pdf|eps|svg)\b'
    # A PDF title metadata value shorter than this is a record number, file stem or placeholder ("68605761",
    # "mcedit_fetal_heart_manuscript", "My title"), not another paper's title, so it never vetoes identity.
    metadata_title_minimum_words: int = 3
    # Words that end a title when extraction runs it into the next line ("<title> Authors: A, B").
    title_byline_labels: tuple[str, ...] = ('author', 'authors', 'by')
    footer_scan_bytes: int = 4096
    poster_minimum_edge_points: float = 1000
    slide_minimum_aspect_ratio: float = 1.2

    def __post_init__(self) -> None:
        if self.maximum_file_bytes < 1024 or self.chunk_bytes < 1024:
            raise ValueError('Download and chunk limits must be at least 1024 bytes')
        if self.filename_bytes < 64 or not 8 <= self.filename_hash_characters <= 64:
            raise ValueError('Filename limits cannot safely accommodate a stable identifier')
        if self.maximum_redirects < 0 or not self.allowed_ports:
            raise ValueError('Redirect and port policy is invalid')
        if any(not 1 <= port <= 65535 for port in self.allowed_ports):
            raise ValueError('Invalid allowed port')
        limits = (
            self.maximum_transfer_seconds,
            self.parser_timeout,
            self.parser_memory_bytes,
            self.maximum_pages,
            self.maximum_page_content_bytes,
            self.identity_pages,
            self.title_lines,
            self.maximum_extracted_characters,
            self.minimum_title_words_without_doi,
            self.maximum_content_checks,
            self.metadata_title_minimum_words,
            self.footer_scan_bytes,
            self.poster_minimum_edge_points,
            self.slide_minimum_aspect_ratio,
        )
        if any(limit <= 0 for limit in limits):
            raise ValueError('Parsing limits must be positive')


DEFAULT_DOWNLOAD = DownloadConfig()


@dataclass(frozen=True)
class StorageConfig:
    """How run artifacts are written."""

    # papers.json, pdf_urls.json, professor.json and the manifest are machine-read: write them without
    # indentation. report.json stays indented for people.
    compact_artifacts: bool = True
    # Share repeated strings when loading checkpoints: ~1/3 less memory, ~1.6x load time.
    intern_strings: bool = False


DEFAULT_STORAGE = StorageConfig()
