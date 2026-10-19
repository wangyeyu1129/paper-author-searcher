"""Injectable defaults; matching policy is independent of source adapters."""

from dataclasses import dataclass

from .status import StrEnum


@dataclass(frozen=True)
class MatchingConfig:
    compatible_name: float = 0.9
    shared_school: float = 0.95
    matched_school: float = 0.8
    resolved_confidence: float = 0.8
    ambiguity_margin: float = 0.03  # calibrated 2026-10-18: holdout coverage 33% -> 45%, purity 100%
    review_cap: float = 0.69
    confidence_cap: float = 0.99
    confidence_precision: int = 3
    middle_name_similarity: float = 0.97
    initial_similarity: float = 0.78
    reordered_school_similarity: float = 0.98
    minimum_shared_dois: int = 2
    coauthors_with_doi: int = 2
    coauthors_with_school: int = 3
    topics_with_school: int = 2
    name_weight: float = 0.55
    school_weight: float = 0.30
    current_school_weight: float = 0.02
    school_mismatch_penalty: float = 0.20
    department_weight: float = 0.025
    research_weight: float = 0.025
    orcid_bonus: float = 0.08
    doi_base_bonus: float = 0.04
    doi_coverage_bonus: float = 0.04
    doi_coverage_target: int = 10
    contextual_link_bonus: float = 0.01
    # Independent identity anchors (caller-supplied ORCID or papers the person is known to have written).
    anchor_bonus: float = 0.15
    # Anchor kinds strong enough to act as a rule rather than a weight: a candidate holding one (and no conflicting
    # ORCID) ranks first, is not contested by candidates without one, and resolves on a compatible name.
    precedence_anchor_kinds: tuple[str, ...] = ('orcid',)
    orcid_conflict_penalty: float = 0.30
    # Optional LLM adjudication of close contests is accepted only at or above this stated confidence.
    adjudication_minimum_confidence: float = 0.8
    # Same-source split profiles (one person indexed under several IDs) merge only on strong shared context.
    merge_split_profiles: bool = True
    split_profile_coauthors: int = 5
    split_profile_topics: int = 2
    # Size-aware guard: shared coauthors / the smaller coauthor list. Five shared names mean little between two
    # profiles with hundreds of collaborators each (large consortia); 0 disables the guard.
    split_profile_coauthor_overlap: float = 0.1
    school_stop_words: tuple[str, ...] = (
        'university',
        'of',
        'the',
        'at',
        'universite',
        'universität',
    )

    def __post_init__(self) -> None:
        if self.doi_coverage_target <= 0:
            raise ValueError('doi_coverage_target must be positive')


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


@dataclass(frozen=True)
class AnchorConfig:
    """Limits for turning caller hints (ORCID, homepage, known papers) into author-ID anchors."""

    maximum_anchor_papers: int = 15
    homepage_bytes: int = 2_000_000
    maximum_redirects: int = 5
    title_search_results: int = 3
    # Homepage parsing: each block element is one citation; emphasised or quoted spans of title length are titles.
    citation_block_tags: tuple[str, ...] = ('li', 'p', 'tr', 'dd', 'dt', 'div')
    title_tags: tuple[str, ...] = ('a', 'b', 'strong', 'i', 'em', 'cite')
    title_minimum_words: int = 4
    title_maximum_characters: int = 300
    # Title lookups (one search request each) only fill what identifier anchors left of maximum_anchor_papers.
    maximum_title_anchors: int = 10


DEFAULT_MATCHING = MatchingConfig()
DEFAULT_ANCHORS = AnchorConfig()
DEFAULT_HTTP = HttpConfig()
DEFAULT_SOURCES = SourceConfig()


class AmbiguityPolicy(StrEnum):
    STOP = 'stop'
    COLLECT_CONTENDERS = 'collect_contenders'


AMBIGUITY_POLICIES = AmbiguityPolicy.values()


class RawMetadataPolicy(StrEnum):
    FULL = 'full'  # everything the source returned (audit, reanalysis)
    MINIMAL = 'minimal'  # only fields later stages read
    NONE = 'none'


@dataclass(frozen=True)
class CollectionConfig:
    openalex_page_size: int = 100
    semantic_scholar_page_size: int = 100
    maximum_pages: int | None = None
    semantic_scholar_author_identity: bool = True
    expand_truncated_openalex_authors: bool = True
    # "stop": an unconfirmed identity halts collection. "collect_contenders": collect the close, school-compatible
    # contenders instead; nothing they yield can be verified, it is all routed to review.
    ambiguity_policy: str = 'stop'
    maximum_contenders: int = 3
    # Author IDs of one source paged at once (a merged profile can hold many IDs per source). Semantic Scholar
    # and DBLP keep to one: their unpaced listing APIs answer bursts with 429s. Unlisted sources get one.
    source_concurrency: tuple[tuple[str, int], ...] = (('openalex', 4), ('semantic_scholar', 1), ('dblp', 1))
    # How much of each raw API payload papers.json keeps (see raw_metadata.py).
    raw_metadata: str = RawMetadataPolicy.FULL

    def __post_init__(self) -> None:
        if self.ambiguity_policy not in AMBIGUITY_POLICIES:
            raise ValueError(f'ambiguity_policy must be one of {AMBIGUITY_POLICIES}')
        if self.maximum_contenders < 1:
            raise ValueError('maximum_contenders must be positive')
        if self.raw_metadata not in RawMetadataPolicy.values():
            raise ValueError(f'raw_metadata must be one of {RawMetadataPolicy.values()}')
        if any(limit < 1 for _, limit in self.source_concurrency):
            raise ValueError('source_concurrency limits must be positive')
        if not 1 <= self.openalex_page_size <= 200:
            raise ValueError('openalex_page_size must be between 1 and 200')
        if not 1 <= self.semantic_scholar_page_size <= 1000:
            raise ValueError('semantic_scholar_page_size must be between 1 and 1000')
        if self.maximum_pages is not None and self.maximum_pages < 1:
            raise ValueError('maximum_pages must be positive or None')


DEFAULT_COLLECTION = CollectionConfig()


@dataclass(frozen=True)
class OwnershipConfig:
    name_similarity: float = 0.9
    incompatible_name: float = 0.5
    minimum_shared_coauthors: int = 1
    seed_papers_per_coauthor: int = 2
    minimum_known_coauthors: int = 2
    institution_similarity: float = 0.95
    maximum_coauthor_authors: int = 50
    paper_orcid_scopes: tuple[str, ...] = ('paper_authorship',)
    # Copied from the source's author profile onto every paper of that author ID: no per-paper evidence.
    author_level_orcid_scopes: tuple[str, ...] = ('resolved_author', 'author_profile')
    # Anchor kinds that make an author ID's past institutions (the identity's affiliation history) acceptable as
    # paper-level evidence. Paper anchors are excluded: they can attach a namesake's fragment.
    history_anchor_kinds: tuple[str, ...] = ('orcid',)
    # An identity listing more institutions than this is a merge of several people (OpenAlex filed many Wei Lus,
    # 89 institutions, under one ORCID-bearing author ID), so its history vouches for nobody.
    maximum_history_institutions: int = 12
    nonstandard_types: tuple[str, ...] = (
        'proceedings',
        'peer_review',
        'paratext',
        'supplementary_materials',
        'reference_entry',
        'editorial',
    )
    # Records that are not research publications: kept for review, never verified, so they stay out of the output
    # and the PDF stage. Values are paper_types.TYPE_LABELS categories.
    non_publication_types: tuple[str, ...] = (
        'dataset',
        'book_review',
        'dissertation',
        'conference_abstract',
    )
    nonstandard_title_prefixes: tuple[str, ...] = (
        'peer review',
        'reviewer report',
        'decision letter',
        'author response',
        'proceedings of',
    )


@dataclass(frozen=True)
class DeduplicationConfig:
    author_similarity: float = 0.9
    minimum_shared_authors: int = 2
    author_overlap: float = 0.8
    allow_author_initials: bool = True


DEFAULT_OWNERSHIP = OwnershipConfig()
DEFAULT_DEDUPLICATION = DeduplicationConfig()


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
        'arxiv',
        'journal_catalog',
        'public_pages',
    )
    bibliography_urls: tuple[str, ...] = ()
    journal_catalogs: tuple[tuple[str, str], ...] = (
        ('Journal of Machine Learning Research', 'https://jmlr.org/papers/'),
    )
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
        if self.lookup_workers < 1:
            raise ValueError('lookup_workers must be positive')
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
