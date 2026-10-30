"""Paper collection: ambiguity policy, ownership rules, listed works, common-name guards, deduplication."""

from dataclasses import dataclass

from paper_author_searcher.status import StrEnum


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
class PrecisionGuardConfig:
    """Stricter ownership evidence where namesakes share institutions (Tsinghua has many Yang Lius).

    Applies only to identities whose name is common (see ``common_surnames``) and only to author IDs not anchored
    by an ``exempt_anchor_kinds`` anchor; every other identity keeps the ordinary rules. The paper-level unit check
    exempts only ``unit_exempt_anchor_kinds`` anchors.
    """

    enabled: bool = True
    # A file in the package's data directory, or a path; one lowercase surname per line.
    common_surnames: str = 'common_chinese_surnames.txt'
    maximum_name_tokens: int = 3
    exempt_anchor_kinds: tuple[str, ...] = ('orcid', 'profile')
    # A database's ORCID belongs to its author cluster, and the cluster can hold a same-school namesake's papers
    # (OpenAlex filed a Tsinghua electronics and an industrial-engineering paper under Yang Liu's ORCID), so a paper
    # whose own byline names another unit is not claimed on the institution alone even under an ORCID anchor.
    unit_exempt_anchor_kinds: tuple[str, ...] = ()
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
