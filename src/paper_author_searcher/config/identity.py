"""Identity resolution: name matching, anchors, namesake disambiguation, discovery and extraction."""

from dataclasses import dataclass

# British spellings a title may carry where another edition, a homepage or the PDF has the American one (or the two
# forms of one word), as stems of normalized words. Compared titles are rewritten to the right-hand form.
TITLE_SPELLINGS: tuple[tuple[str, str], ...] = (
    ('well being', 'wellbeing'),
    *(
        (f'{stem}our', f'{stem}or')
        for stem in ('behavi', 'col', 'fav', 'flav', 'harb', 'hon', 'hum', 'lab', 'neighb', 'od', 'rum', 'tum', 'vap')
    ),
    *(
        (f'{stem}is', f'{stem}iz')
        for stem in (
            'categor',
            'character',
            'general',
            'global',
            'industrial',
            'maxim',
            'minim',
            'mobil',
            'normal',
            'optim',
            'organ',
            'parameter',
            'penal',
            'priorit',
            'privat',
            'random',
            'real',
            'recogn',
            'regular',
            'special',
            'stabil',
            'summar',
            'urban',
            'util',
            'visual',
        )
    ),
    ('analys', 'analyz'),
    ('ageing', 'aging'),
    ('catalogue', 'catalog'),
    ('centre', 'center'),
    ('defence', 'defense'),
    ('fibre', 'fiber'),
    ('grey', 'gray'),
    ('judgement', 'judgment'),
    ('labell', 'label'),
    ('licence', 'license'),
    ('modell', 'model'),
    ('programme', 'program'),
    ('signall', 'signal'),
    ('theatre', 'theater'),
    ('travell', 'travel'),
)


# Smaller limits could not hold one publication and its answer.
MINIMUM_CHUNK_CHARACTERS = 1000
MINIMUM_ANSWER_TOKENS = 1024


@dataclass(frozen=True)
class MatchingConfig:
    compatible_name: float = 0.9
    shared_school: float = 0.95
    matched_school: float = 0.8
    resolved_confidence: float = 0.8
    ambiguity_margin: float = 0.03  # calibrated 2026-10-18: holdout coverage 33% -> 45%, purity 100%
    # The margin was calibrated on queries that name a department. Without one, every namesake at the school scores
    # alike (26 Yang Lius at Tsinghua, the top two 0.032 apart), so a score margin alone does not settle a contest
    # between two school-matched profiles; anchors, bylines or adjudication still can.
    margin_requires_department: bool = True
    review_cap: float = 0.69
    confidence_cap: float = 0.99
    confidence_precision: int = 3
    middle_name_similarity: float = 0.97
    initial_similarity: float = 0.78
    reordered_school_similarity: float = 0.98
    # Shared works link two source profiles: a work is shared when its DOI, arXiv ID or normalized title agrees
    # (identity.shared_works), so arXiv-only output links as well as journal papers do.
    minimum_shared_dois: int = 2
    # A title shorter than this many words ("Introduction", "Editorial") is too generic to identify a work.
    work_title_minimum_words: int = 5
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
    # A citation's link to a PDF (its path ends in a suffix or holds a marker) is the author's copy of that paper,
    # recorded for the PDF source 'homepage'. A longer block is a container of several citations, not one.
    pdf_link_suffixes: tuple[str, ...] = ('.pdf',)
    pdf_link_path_markers: tuple[str, ...] = ('/pdf/',)
    listed_pdf_citation_characters: int = 1500
    maximum_listed_pdfs: int = 2000
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


@dataclass(frozen=True)
class DisambiguationConfig:
    """Settle a close, unanchored contest between namesakes by their own bylines (byline_disambiguation).

    A profile's evidence is the number of its sampled works whose byline prints the requested department at the
    requested school; when no contender has any, optionally the works printing the school alone. The leader wins
    with at least ``minimum_works`` and ``dominance_ratio`` times the strongest rival's count; otherwise the
    contest stays open.
    """

    enabled: bool = True
    # Contenders scoring within this distance of the leader take part (the real Feng Zhang scored 0.02 below a
    # one-paper namesake at MIT).
    contest_band: float = 0.05
    minimum_works: int = 5
    dominance_ratio: float = 3.0
    # Departments a byline never prints (Harvard bioengineering signs as "School of Engineering and Applied
    # Sciences") may fall back to works printing the school; never used when any contender has department
    # evidence. Off by default: in the 2026-10-27 replay it settled nobody correctly (Jia Liu 33 vs 25 against a
    # Harvard Medical namesake) and settled Stanford CS student Jing Huang on an immunologist's 21 Stanford works.
    school_fallback: bool = False
    # When bylines settle nothing, a position the contender's own curated record states (an unlabelled DBLP
    # affiliation note, a current ORCID employment or education) may: exactly one contender's record must place
    # it at the requested school in the requested department. A broader unit ("School of Engineering and Applied
    # Sciences" for bioengineering) counts only when no contender names the department itself, and only when
    # its words relate to the department's. Rivals' records are all read, or the tier abstains.
    curated_positions: bool = True
    # A candidate this far behind the leader also enters that tier, but only when its own record already names
    # the requested department at the school: a cross-source link bonus lifted Tsinghua's AI-institute Yang Liu
    # 0.065 above the DBLP profile that names Computer Science and Technology.
    curated_band: float = 0.1
    parent_unit_keywords: tuple[str, ...] = ('school', 'faculty', 'college')
    # Distinct ORCID records read per contest (two requests each); a contest with more abstains.
    maximum_orcid_lookups: int = 20
    # Unit words this long or longer relate when one contains the other ("engineering" in "bioengineering").
    related_word_length: int = 5

    def __post_init__(self) -> None:
        if self.curated_band < self.contest_band:
            raise ValueError('curated_band must be at least contest_band')
        if self.maximum_orcid_lookups < 0:
            raise ValueError('maximum_orcid_lookups must not be negative')
        if self.contest_band < 0:
            raise ValueError('contest_band must not be negative')
        if self.minimum_works < 1:
            raise ValueError('minimum_works must be positive')
        if self.dominance_ratio < 1:
            raise ValueError('dominance_ratio must be at least 1')


DEFAULT_MATCHING = MatchingConfig()
DEFAULT_ANCHORS = AnchorConfig()
DEFAULT_DISAMBIGUATION = DisambiguationConfig()


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
    # With a local DBLP index: the one name-compatible DBLP profile whose own note places the person at the
    # requested school and department (curated_positions) gives its homepage URLs and its ORCID. Two such profiles,
    # or none, give nothing. Needs a requested department.
    dblp_profiles: bool = True
    # Web search providers registered in source_catalog.WEB_SEARCH, in order of preference; the first whose key is
    # set runs (TAVILY_API_KEY, BRAVE_API_KEY, EXA_API_KEY). Empty disables web search.
    web_search: tuple[str, ...] = ('tavily', 'brave', 'exa')
    web_search_results: int = 10
    brave_url: str = 'https://api.search.brave.com/res/v1/web/search'
    tavily_url: str = 'https://api.tavily.com/search'
    exa_url: str = 'https://api.exa.ai/search'
    # Query templates; {name}, {university} and {department} are filled from the query.
    web_queries: tuple[str, ...] = (
        '"{name}" {university}',
        '"{name}" {university} publications',
        '"{name}" lab publications list',
    )
    maximum_pages: int = 5
    minimum_page_score: float = 0.5
    # Page ranking weights (PageRanker): each feature is 0..1 and the score is their weighted sum.
    weight_name_in_title: float = 0.35
    weight_name_in_url: float = 0.15
    weight_institution: float = 0.30
    weight_publication_page: float = 0.15
    weight_academic_host: float = 0.10
    weight_orcid_listed: float = 0.50
    weight_dblp_listed: float = 0.50
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
        # Profile and index sites a DBLP record links besides the person's homepage.
        'openreview.net',
        'ams.org',
        'acm.org',
        'scopus.com',
        'zbmath.org',
        'wikidata.org',
        'mathgenealogy.org',
        'github.com',
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

    Off by default: the rule-based citation parser needs no paid service. When enabled it runs beside that parser
    (their citations are merged), given ``ANTHROPIC_API_KEY``.
    Page text is split into chunks of at most ``chunk_characters`` on line boundaries; each chunk is one request.
    """

    enabled: bool = False
    model: str = 'claude-opus-5-5'
    effort: str = 'low'
    max_tokens: int = 16000
    chunk_characters: int = 24000
    maximum_chunks: int = 8
    # Extracted publications are cached by request (under the run's HTTP cache directory when it has one).
    cache: bool = True
    # After pdfminer (DownloadConfig.front_page_layouts), Claude may read the first pages of a file still unplaced;
    # the printed title, authors and DOI go through the same identity rules as extracted text.
    read_front_pages: bool = False
    front_pages: int = 1
    front_page_max_tokens: int = 4000
    # Claude's spend per run: past it, pages keep only the rule-based parse. The prices (USD per million tokens) are
    # those of `model`; change them together.
    maximum_cost_usd: float = 1.0
    input_usd_per_mtok: float = 4.0
    output_usd_per_mtok: float = 20.0
    cache_read_usd_per_mtok: float = 0.2
    cache_write_usd_per_mtok: float = 5.0

    def __post_init__(self) -> None:
        if (
            self.chunk_characters < MINIMUM_CHUNK_CHARACTERS
            or self.maximum_chunks < 1
            or self.max_tokens < MINIMUM_ANSWER_TOKENS
        ):
            raise ValueError('Extraction chunks and token limits are too small')


DEFAULT_EXTRACTION = ExtractionConfig()
