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
    precedence_anchor_kinds: tuple[str, ...] = ('orcid', 'profile')
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
    # Minimum seconds between requests to a host, as its terms require (arXiv asks for 3 s). Applied below the
    # cache, so cached and replayed requests never wait.
    host_request_intervals: tuple[tuple[str, float], ...] = (('export.arxiv.org', 3.0),)


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


@dataclass(frozen=True)
class AnchorConfig:
    """Limits for turning caller hints (ORCID, homepage, known papers) into author-ID anchors."""

    # Anchor lookups per person. The page's citations are ranked by value first (see homepage_citations): the
    # person's own, recent, research-paper entries before others.
    maximum_anchor_papers: int = 25
    anchor_recent_years: int = 6
    non_paper_markers: tuple[str, ...] = (
        'talk',
        'keynote',
        'poster',
        'slides',
        'tutorial',
        'dataset',
        'software',
        'thesis',
        'dissertation',
        'abstract',
        'patent',
        'workshop proposal',
    )
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
    # A supplied ORCID's public works list is the person's own list, like a homepage (one request).
    orcid_listed_works: bool = True
    # OpenAlex assigns one ORCID per merged author cluster, and a cluster mixing namesakes can carry another
    # person's. A compatible author holding at least this many distinct works of the person's own list is anchored
    # despite such an ORCID (AnchorKind.CONTESTED); fewer, and the ORCID wins.
    minimum_contested_works: int = 3
    # Sources whose caller-supplied profile link (profile_links) anchors the linked author ID directly.
    profile_anchor_sources: tuple[str, ...] = ('openalex', 'semantic_scholar', 'dblp')


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
    # Department adjudication of collected contenders: one contender's own papers must name the requested
    # department in at least this share of their unit mentions (with enough mentions), every rival in at most
    # contender_rival_share. Without a requested department nothing is adjudicated.
    contender_department_adjudication: bool = True
    contender_minimum_share: float = 0.25
    contender_minimum_evidence: int = 5
    contender_rival_share: float = 0.05

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
    # A coauthor on the person's own papers (homepage or ORCID list, anchor papers) is trusted after this many
    # seed papers: PhD students and junior faculty rarely share two papers with anyone.
    own_paper_seed_papers_per_coauthor: int = 1
    minimum_known_coauthors: int = 2
    institution_similarity: float = 0.95
    maximum_coauthor_authors: int = 50
    paper_orcid_scopes: tuple[str, ...] = ('paper_authorship',)
    # Copied from the source's author profile onto every paper of that author ID: no per-paper evidence.
    author_level_orcid_scopes: tuple[str, ...] = ('resolved_author', 'author_profile')
    # Anchor kinds that make an author ID's past institutions (the identity's affiliation history) acceptable as
    # paper-level evidence. Paper anchors are excluded: they can attach a namesake's fragment.
    history_anchor_kinds: tuple[str, ...] = ('orcid', 'profile')
    # A work on the person's own list (homepage or supplied ORCID) with a compatible byline is theirs.
    verify_listed_works: bool = True
    # Sources whose author profiles are curated by people (DBLP disambiguates by hand), so an author ID there that
    # the person's own ORCID anchors vouches for every paper listing it. Elsewhere a profile ORCID is set per
    # merged cluster (OpenAlex's can be a namesake's) and is no paper-level evidence.
    curated_orcid_sources: tuple[str, ...] = ('dblp',)
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
class ListedWorksConfig:
    """Works the person lists themselves (homepage, supplied ORCID) that author-ID collection did not return.

    Each missing work is looked up once (DOI, arXiv ID or exact title) through ``sources`` in order, stopping at the
    first that has it, and enters the collection under the work's name-compatible author;
    OwnershipConfig.verify_listed_works decides whether the listing alone verifies it.
    """

    enabled: bool = True
    maximum_lookups: int = 100
    title_search_results: int = 3
    # Names registered in source_catalog.WORK_LOOKUPS. OpenAlex first (richest authorship), then sources that
    # index what it misses: Semantic Scholar (CS venues), Crossref (publisher records), arXiv (preprints).
    sources: tuple[str, ...] = (
        'openalex',
        'dblp',
        'catalogs',
        'semantic_scholar',
        'crossref',
        'arxiv',
        'europepmc',
        'core',
    )

    def __post_init__(self) -> None:
        if self.maximum_lookups < 0 or self.title_search_results < 1:
            raise ValueError('maximum_lookups must be nonnegative and title_search_results positive')


DEFAULT_LISTED_WORKS = ListedWorksConfig()


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
class FragmentConfig:
    """Collection-time merging of a person's split author profiles (fragment_merging.FragmentMerger)."""

    enabled: bool = True
    # Sources whose same-name candidates the resolver passes on as fragments (and the collector can page).
    sources: tuple[str, ...] = ('openalex', 'semantic_scholar')
    maximum_fragments: int = 12
    # A fragment joins with at least this many coauthors shared with the person's verified papers, whose weights
    # sum to at least minimum_score. A coauthor shared only by the person and this fragment weighs 1; one shared
    # by every fragment weighs nearly 0 (normalized inverse fragment frequency), so the score counts distinctive
    # shared coauthors.
    minimum_shared_coauthors: int = 2
    minimum_score: float = 2.0
    require_institution: bool = True
    institution_similarity: float = 0.95

    def __post_init__(self) -> None:
        if self.maximum_fragments < 0 or self.minimum_shared_coauthors < 1 or self.minimum_score < 0:
            raise ValueError('Fragment limits must be nonnegative and at least one coauthor must be shared')


DEFAULT_FRAGMENTS = FragmentConfig()


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
        if self.batch_size < 1 or not 1 <= self.compression_level <= 9:
            raise ValueError('batch_size must be positive and compression_level between 1 and 9')


DEFAULT_DBLP_DUMP = DblpDumpConfig()


@dataclass(frozen=True)
class ProfileLinkConfig:
    """Profile URLs a caller may supply (ORCID, Google Scholar, OpenAlex, Semantic Scholar, DBLP, OpenReview).

    Each pattern is ``(source, regular expression)``; the expression's ``id`` group is the source's identifier. A
    caller-supplied profile of an author-ID source anchors that ID directly; Google Scholar has no public API, so
    its user ID is kept as evidence only.
    """

    patterns: tuple[tuple[str, str], ...] = (
        ('orcid', r'orcid\.org/(?P<id>\d{4}-\d{4}-\d{4}-\d{3}[\dX])'),
        ('openalex', r'openalex\.org/(?:authors/)?(?P<id>A\d+)'),
        ('semantic_scholar', r'semanticscholar\.org/author/(?:[^/?#]+/)?(?P<id>\d+)'),
        ('dblp', r'dblp\.(?:org|uni-trier\.de)/pid/(?P<id>[\w/-]+?)(?:\.html|\.xml)?(?:[?#]|$)'),
        ('openreview', r'openreview\.net/profile\?id=(?P<id>~[\w.-]+)'),
        ('google_scholar', r'scholar\.google\.[a-z.]+/citations\?(?:[^#]*&)?user=(?P<id>[\w-]+)'),
    )


DEFAULT_PROFILE_LINKS = ProfileLinkConfig()


@dataclass(frozen=True)
class DiscoveryConfig:
    """Find what the caller did not supply: the person's ORCID and their homepage, publication page or CV.

    Discovered evidence is weaker than supplied evidence: an ORCID is adopted only when the registry returns
    exactly one compatible person at the requested institution, and a page only when its ranking score clears
    ``minimum_page_score`` (see identity_discovery.PageRanker).
    """

    enabled: bool = True
    # ORCID public registry search by name and institution (free, no key).
    orcid_search: bool = True
    orcid_search_rows: int = 10
    # A unique registry match is not a unique person: the researcher may have no ORCID while a namesake who was
    # once at the institution does. The match is adopted only when one of its own affiliation records (these ORCID
    # sections; students list their university under education) names the requested institution and is current
    # (no end year, or one not yet past), and, when a department was requested and this is set, an agreeing one.
    orcid_require_affiliation: bool = True
    orcid_affiliation_sections: tuple[str, ...] = ('employments', 'educations')
    orcid_require_department: bool = True
    # The registry's researcher URLs (personal websites) of the supplied or discovered ORCID become candidate pages.
    orcid_researcher_urls: bool = True
    # Web search provider registered in source_catalog.WEB_SEARCH ('brave', 'tavily', 'exa'); empty disables it.
    # Each provider needs its key (BRAVE_API_KEY, TAVILY_API_KEY, EXA_API_KEY); without it the step is skipped.
    web_search: str = 'brave'
    web_search_results: int = 10
    brave_url: str = 'https://api.search.brave.com/res/v1/web/search'
    tavily_url: str = 'https://api.tavily.com/search'
    exa_url: str = 'https://api.exa.ai/search'
    # Query templates; {name}, {university} and {department} are filled from the query.
    web_queries: tuple[str, ...] = ('"{name}" {university}', '"{name}" {university} publications')
    maximum_pages: int = 3
    minimum_page_score: float = 0.5
    # Page ranking weights (PageRanker): each feature is 0..1 and the score is their weighted sum.
    weight_name_in_title: float = 0.35
    weight_name_in_url: float = 0.15
    weight_institution: float = 0.30
    weight_publication_page: float = 0.15
    weight_academic_host: float = 0.10
    weight_orcid_listed: float = 0.50
    # Hosts that never are a person's own page (profiles aggregated by others, social networks, search engines).
    excluded_hosts: tuple[str, ...] = (
        'researchgate.net',
        'linkedin.com',
        'scholar.google.com',
        'facebook.com',
        'twitter.com',
        'x.com',
        'wikipedia.org',
        'semanticscholar.org',
        'dblp.org',
        'orcid.org',
        'openalex.org',
        'youtube.com',
        'ratemyprofessors.com',
        'zoominfo.com',
        'rocketreach.co',
    )
    academic_host_suffixes: tuple[str, ...] = ('.edu', '.ac.uk', '.edu.cn', '.ac.jp', '.ac.kr', '.edu.au', 'github.io')
    publication_markers: tuple[str, ...] = ('publication', 'papers', 'research', 'cv', 'vita', 'bibliography')
    # Links on a fetched page that lead to its publication list or CV (same site only), followed once.
    follow_publication_links: bool = True
    maximum_followed_links: int = 2

    def __post_init__(self) -> None:
        if self.orcid_search_rows < 1 or self.web_search_results < 1 or self.maximum_pages < 0:
            raise ValueError('Discovery limits must be positive')
        if self.maximum_followed_links < 0:
            raise ValueError('maximum_followed_links must be nonnegative')


DEFAULT_DISCOVERY = DiscoveryConfig()


@dataclass(frozen=True)
class ExtractionConfig:
    """LLM extraction of a structured publication list from a homepage, publication page or CV.

    Runs beside the rule-based citation parser (their citations are merged) when ``ANTHROPIC_API_KEY`` is set.
    Page text is split into chunks of at most ``chunk_characters`` on line boundaries; each chunk is one request.
    """

    enabled: bool = True
    model: str = 'claude-opus-5-5'
    effort: str = 'low'
    max_tokens: int = 16000
    chunk_characters: int = 24000
    maximum_chunks: int = 8
    # Extracted publications are cached by request (under the run's HTTP cache directory when it has one).
    cache: bool = True

    def __post_init__(self) -> None:
        if self.chunk_characters < 1000 or self.maximum_chunks < 1 or self.max_tokens < 1024:
            raise ValueError('Extraction chunks and token limits are too small')


DEFAULT_EXTRACTION = ExtractionConfig()


@dataclass(frozen=True)
class PrecisionGuardConfig:
    """Stricter ownership evidence where namesakes share institutions (Tsinghua has many Yang Lius).

    Applies only to identities whose name is common (see ``common_surnames``) and only to author IDs not anchored
    by an ``exempt_anchor_kinds`` anchor; every other identity keeps the ordinary rules.
    """

    enabled: bool = True
    # A file in the package's data directory, or a path; one lowercase surname per line.
    common_surnames: str = 'common_chinese_surnames.txt'
    maximum_name_tokens: int = 3
    exempt_anchor_kinds: tuple[str, ...] = ('orcid', 'profile')
    # Department phrases are the comma-separated parts of a raw affiliation string that name a unit.
    department_keywords: tuple[str, ...] = (
        'department',
        'dept',
        'school',
        'college',
        'faculty',
        'institute',
        'laboratory',
        'lab',
        'center',
        'centre',
        'division',
        'program',
    )
    department_stop_words: tuple[str, ...] = (
        'department',
        'dept',
        'of',
        'and',
        'the',
        'school',
        'college',
        'faculty',
        'for',
        'in',
        'division',
    )
    # Two department phrases agree when one's words contain the other's, or their word Jaccard reaches this.
    department_similarity: float = 0.6
    # Units named on the person's own anchor papers join the requested department when they cover this share of
    # those papers' unit mentions (at most maximum_home_departments of them).
    home_department_share: float = 0.1
    maximum_home_departments: int = 5
    # A fragment is foreign when it has this much department evidence and too little of it is the person's.
    # OpenAlex fragments are noisy (Yang Liu's own CS fragment names his department in only 16% of mentions).
    fragment_minimum_evidence: int = 5
    fragment_minimum_share: float = 0.1
    # Coauthors whose name collides easily (common surname, given names of at most this many letters, e.g.
    # "Hui Li", "Qi Liu") do not count as recurring coauthors.
    exclude_common_coauthors: bool = True
    collision_given_name_letters: int = 4

    def __post_init__(self) -> None:
        for name in ('department_similarity', 'home_department_share', 'fragment_minimum_share'):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f'{name} must be between 0 and 1')
        if self.fragment_minimum_evidence < 1 or self.maximum_name_tokens < 1:
            raise ValueError('fragment_minimum_evidence and maximum_name_tokens must be positive')


DEFAULT_PRECISION = PrecisionGuardConfig()


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
