from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

from .status import (
    AccessStatus,
    DownloadStatus,
    FetchStatus,
    FormatStatus,
    IdentityStatus,
    OwnershipStatus,
    PDFValidationStatus,
    ProviderStatus,
    ResolutionStatus,
    UniversityStatus,
)


@dataclass
class ProfessorQuery:
    name: str
    university: str
    department: Optional[str] = None
    research_interests: List[str] = field(default_factory=list)
    university_aliases: List[str] = field(default_factory=list)
    # Optional identity hints; any of them sharply reduces namesake ambiguity.
    orcid: Optional[str] = None
    homepage: Optional[str] = None
    email: Optional[str] = None
    known_papers: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.name.strip() or not self.university.strip():
            raise ValueError('name and university are required')

    def has_hints(self):
        return bool(self.orcid or self.homepage or self.known_papers)


@dataclass
class AuthorCandidate:
    source: str
    author_id: str
    name: str
    affiliations: List[str] = field(default_factory=list)
    current_affiliations: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    departments: List[str] = field(default_factory=list)
    orcid: Optional[str] = None
    paper_ids: List[str] = field(default_factory=list)
    url: Optional[str] = None
    coauthors: List[str] = field(default_factory=list)
    metadata_errors: List[str] = field(default_factory=list)


@dataclass(frozen=True, order=True)
class AnchorEvidence:
    """Why one source author ID is tied to the requested researcher, independently of name similarity."""

    source: str
    author_id: str
    reason: str

    def __str__(self) -> str:
        return f'{self.source} {self.author_id}: {self.reason}'

    @classmethod
    def coerce(cls, value: Union['AnchorEvidence', Mapping[str, Any], str]) -> 'AnchorEvidence':
        """Accept the structured form, its JSON dict, or the "<source> <id>: <reason>" text of older checkpoints."""
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(str(value['source']), str(value['author_id']), str(value.get('reason', '')))
        head, separator, reason = str(value).partition(':')
        source, _, author_id = head.strip().partition(' ')
        if not separator or not source or not author_id.strip():
            raise ValueError(f'Unrecognised identity anchor: {value!r}')
        return cls(source, author_id.strip(), reason.strip())


@dataclass
class ProfessorProfile:
    name: str
    affiliations: List[str]
    author_ids: Dict[str, str]
    identity_confidence: float
    status: str
    evidence: List[str]
    orcid: Optional[str] = None
    research_interests: List[str] = field(default_factory=list)
    university: Optional[str] = None
    university_status: str = UniversityStatus.UNKNOWN
    match_score: float = 0.0
    requested_university: Optional[str] = None
    matched_affiliations: List[str] = field(default_factory=list)
    author_id_aliases: Dict[str, List[str]] = field(default_factory=dict)
    name_aliases: List[str] = field(default_factory=list)
    identity_anchors: List[AnchorEvidence] = field(default_factory=list)
    adjudication: Optional[str] = None

    def __post_init__(self) -> None:
        self.identity_anchors = [AnchorEvidence.coerce(item) for item in self.identity_anchors]


@dataclass
class ResolutionResult:
    profile: Optional[ProfessorProfile]
    candidates: List[ProfessorProfile]
    source_errors: Dict[str, str] = field(default_factory=dict)

    ambiguity: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class PaperRecord:
    title: str
    authors: List[str]
    year: Optional[int]
    doi: Optional[str]
    venue: Optional[str]
    abstract: Optional[str]
    source: str
    source_paper_id: str
    source_author_id: str
    paper_url: Optional[str]
    publication_type: Optional[str] = None
    author_details: List[dict] = field(default_factory=list)
    target_author_present: Optional[bool] = None
    attribution_evidence: List[str] = field(default_factory=list)
    external_ids: Dict[str, str] = field(default_factory=dict)
    raw_metadata: dict = field(default_factory=dict)
    authors_complete: Optional[bool] = None
    metadata_errors: List[str] = field(default_factory=list)
    ownership_status: str = OwnershipStatus.NEEDS_REVIEW
    ownership_evidence: List[str] = field(default_factory=list)
    paper_type: str = 'unknown'
    type_evidence: List[str] = field(default_factory=list)


@dataclass
class Paper(PaperRecord):
    records: List[PaperRecord] = field(default_factory=list)
    deduplication_evidence: List[str] = field(default_factory=list)
    related_versions: List[dict] = field(default_factory=list)
    source_identifiers: List[dict] = field(default_factory=list)


@dataclass
class PaperSourceResult:
    source: str
    author_id: Optional[str]
    papers: List[PaperRecord] = field(default_factory=list)
    complete: bool = False
    pages_fetched: int = 0
    records_received: int = 0
    rejected_records: List[dict] = field(default_factory=list)
    error: Optional[str] = None
    pagination_complete: bool = False
    metadata_errors: List[dict] = field(default_factory=list)


@dataclass
class PaperCollectionResult:
    papers: List[Paper]
    sources: List[PaperSourceResult]
    complete: bool
    selected_author_ids: Dict[str, List[str]]
    identity_confirmation: str
    professor_identity_status: str = ResolutionStatus.UNKNOWN

    def to_dict(self, academic_only=False, ownership_statuses=None):
        from .paper_types import ACADEMIC_TYPES

        data = asdict(self)
        # Source records already appear in the deduplicated papers.
        for source in data['sources']:
            records = source.pop('papers')
            source['ownership_counts'] = dict.fromkeys(OwnershipStatus.values(), 0)
            for record in records:
                source['ownership_counts'][record['ownership_status']] += 1
            if source['pagination_complete']:
                if source['complete']:
                    source['fetch_status'] = FetchStatus.COMPLETE
                elif source['rejected_records']:
                    source['fetch_status'] = FetchStatus.COMPLETE_WITH_PARSE_ISSUES
                else:
                    source['fetch_status'] = FetchStatus.COMPLETE_WITH_METADATA_ISSUES
            elif source['records_received']:
                source['fetch_status'] = FetchStatus.PARTIAL
            else:
                source['fetch_status'] = FetchStatus.UNAVAILABLE
        data['paper_count'] = len(self.papers)
        data['count_scope'] = 'retrieved_metadata_not_total_publications'
        counts = dict.fromkeys(OwnershipStatus.values(), 0)
        record_counts = dict(counts)
        authorship_counts = {
            'with_author_details': 0,
            'with_external_ids': 0,
            'target_present': 0,
            'target_absent': 0,
            'target_unknown': 0,
        }
        for paper in self.papers:
            counts[paper.ownership_status] += 1
            for record in paper.records:
                record_counts[record.ownership_status] += 1
                authorship_counts['with_author_details'] += bool(record.author_details)
                authorship_counts['with_external_ids'] += bool(record.external_ids)
                if record.target_author_present is True:
                    authorship_counts['target_present'] += 1
                elif record.target_author_present is False:
                    authorship_counts['target_absent'] += 1
                else:
                    authorship_counts['target_unknown'] += 1
        data['statistics'] = {
            'raw_records_received': sum(source.records_received for source in self.sources),
            'retained_source_records': sum(len(paper.records) for paper in self.papers),
            'deduplicated_entries': len(self.papers),
            'ownership_counts': counts,
            'source_record_ownership_counts': record_counts,
            'source_record_authorship_counts': authorship_counts,
            'metadata_enrichment_errors': sum(len(source.metadata_errors) for source in self.sources),
            'unparsed_records': sum(len(source.rejected_records) for source in self.sources),
        }
        data['verified_paper_count'] = counts[OwnershipStatus.VERIFIED]
        if academic_only or ownership_statuses:
            data['all_papers'] = data['papers']
            visible = []
            for entry in data['all_papers']:
                if academic_only and entry['paper_type'] not in ACADEMIC_TYPES:
                    continue
                if ownership_statuses and entry['ownership_status'] not in ownership_statuses:
                    continue
                visible.append(entry)
            data['papers'] = visible
        data['visible_paper_count'] = len(data['papers'])
        return data


@dataclass
class PDFCandidate:
    url: str
    source: str
    version_type: str = 'unknown'
    access_basis: str = ''
    license: Optional[str] = None
    identity_status: str = IdentityStatus.UNVERIFIED
    identity_evidence: List[str] = field(default_factory=list)
    provenance: List[dict] = field(default_factory=list)
    access_status: str = AccessStatus.UNKNOWN
    validation_status: str = PDFValidationStatus.UNVERIFIED
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    bytes_read: int = 0
    pdf_signature_confirmed: bool = False
    validation_evidence: List[str] = field(default_factory=list)
    error_details: List[str] = field(default_factory=list)
    request_context: dict = field(default_factory=dict)
    query_error_type: Optional[str] = None


@dataclass
class PDFProviderResult:
    source: str
    candidates: List[PDFCandidate] = field(default_factory=list)
    status: str = ProviderStatus.OK
    errors: List[str] = field(default_factory=list)
    landing_pages: List[dict] = field(default_factory=list)
    diagnostics: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)


@dataclass
class PaperPDFResult:
    entry_index: int
    title: str
    doi: Optional[str]
    ownership_status: str
    ownership_evidence: List[str] = field(default_factory=list)
    pdf_candidates: List[PDFCandidate] = field(default_factory=list)
    best_pdf_url: Optional[str] = None
    pdf_source: Optional[str] = None
    access_status: str = AccessStatus.UNKNOWN
    validation_status: str = PDFValidationStatus.NOT_PROCESSED
    version_type: str = 'unknown'
    error_details: List[dict] = field(default_factory=list)
    providers: List[dict] = field(default_factory=list)
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    source: Optional[str] = None
    source_paper_id: Optional[str] = None
    external_ids: dict = field(default_factory=dict)
    source_author_id: Optional[str] = None
    paper_metadata: dict = field(default_factory=dict)
    failure_reasons: List[str] = field(default_factory=list)
    candidate_decisions: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)


@dataclass
class PDFFileValidation:
    format_status: str = FormatStatus.UNVERIFIED
    identity_status: str = IdentityStatus.NEEDS_REVIEW
    validation_status: str = PDFValidationStatus.NEEDS_REVIEW
    page_count: int = 0
    extracted_titles: List[str] = field(default_factory=list)
    extracted_dois: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    error_details: List[str] = field(default_factory=list)
    active_content_present: bool = False
    document_type: str = 'unknown'
    document_type_evidence: List[str] = field(default_factory=list)
    observations: dict = field(default_factory=dict)


@dataclass
class PaperDownloadResult:
    paper_key: str
    title: str
    ownership_status: str
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    source: Optional[str] = None
    pdf_source: Optional[str] = None
    pdf_url: Optional[str] = None
    final_url: Optional[str] = None
    local_filename: Optional[str] = None
    download_status: str = DownloadStatus.NOT_PROCESSED
    validation_status: str = PDFValidationStatus.NOT_CHECKED
    format_status: str = FormatStatus.NOT_CHECKED
    identity_status: str = IdentityStatus.NOT_CHECKED
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    version_type: str = 'unknown'
    error_details: List[str] = field(default_factory=list)
    attempts: List[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    history: List[dict] = field(default_factory=list)
    duplicate_of: Optional[str] = None
    resolver_error_details: List[dict] = field(default_factory=list)
    ownership_evidence: List[str] = field(default_factory=list)
    access_basis: str = ''
    license: Optional[str] = None
    pdf_provenance: List[dict] = field(default_factory=list)
    source_paper_id: Optional[str] = None
    source_author_id: Optional[str] = None
    external_ids: dict = field(default_factory=dict)
    paper_metadata: dict = field(default_factory=dict)
    failure_reasons: List[str] = field(default_factory=list)
    document_type: str = 'unknown'
    search_sufficiently_satisfied: Optional[bool] = False
    search_satisfaction_reason: str = 'no_integrity_checked_local_file'


@dataclass
class PDFResolutionResult:
    papers: List[PaperPDFResult]
    statistics: dict

    def to_dict(self):
        return {
            'schema_version': 1,
            'validation_scope': 'bounded_file_signature_and_source_metadata_identity_not_full_text',
            'statistics': self.statistics,
            'papers': [asdict(paper) for paper in self.papers],
        }
