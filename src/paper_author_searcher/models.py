from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional


@dataclass
class ProfessorQuery:
    name: str
    university: str
    department: Optional[str] = None
    research_interests: List[str] = field(default_factory=list)
    university_aliases: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.name.strip() or not self.university.strip():
            raise ValueError("name and university are required")


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
    university_status: str = "unknown"
    match_score: float = 0.0
    requested_university: Optional[str] = None
    matched_affiliations: List[str] = field(default_factory=list)
    author_id_aliases: Dict[str, List[str]] = field(default_factory=dict)
    name_aliases: List[str] = field(default_factory=list)


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
    ownership_status: str = "needs_review"
    ownership_evidence: List[str] = field(default_factory=list)
    paper_type: str = "unknown"
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
    professor_identity_status: str = "unknown"

    def to_dict(self, academic_only=False, ownership_statuses=None):
        from .paper_types import ACADEMIC_TYPES

        data = asdict(self)
        # Source records already appear in the deduplicated papers.
        for source in data['sources']:
            records = source.pop('papers')
            source['ownership_counts'] = {'verified': 0, 'needs_review': 0, 'rejected': 0}
            for record in records:
                source['ownership_counts'][record['ownership_status']] += 1
            if source['pagination_complete']:
                if source['complete']:
                    source['fetch_status'] = 'complete'
                elif source['rejected_records']:
                    source['fetch_status'] = 'complete_with_parse_issues'
                else:
                    source['fetch_status'] = 'complete_with_metadata_issues'
            elif source['records_received']:
                source['fetch_status'] = 'partial'
            else:
                source['fetch_status'] = 'unavailable'
        data['paper_count'] = len(self.papers)
        data['count_scope'] = 'retrieved_metadata_not_total_publications'
        counts = {'verified': 0, 'needs_review': 0, 'rejected': 0}
        record_counts = dict(counts)
        authorship_counts = {
            'with_author_details': 0, 'with_external_ids': 0,
            'target_present': 0, 'target_absent': 0, 'target_unknown': 0,
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
        data['verified_paper_count'] = counts['verified']
        if academic_only or ownership_statuses:
            data['all_papers'] = data['papers']
            visible = []
            for paper in data['all_papers']:
                if academic_only and paper['paper_type'] not in ACADEMIC_TYPES:
                    continue
                if ownership_statuses and paper['ownership_status'] not in ownership_statuses:
                    continue
                visible.append(paper)
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
    identity_status: str = 'unverified'
    identity_evidence: List[str] = field(default_factory=list)
    provenance: List[dict] = field(default_factory=list)
    access_status: str = 'unknown'
    validation_status: str = 'unverified'
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    bytes_read: int = 0
    pdf_signature_confirmed: bool = False
    validation_evidence: List[str] = field(default_factory=list)
    error_details: List[str] = field(default_factory=list)


@dataclass
class PDFProviderResult:
    source: str
    candidates: List[PDFCandidate] = field(default_factory=list)
    status: str = 'ok'
    errors: List[str] = field(default_factory=list)
    landing_pages: List[dict] = field(default_factory=list)
    diagnostics: List[dict] = field(default_factory=list)


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
    access_status: str = 'unknown'
    validation_status: str = 'not_processed'
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


@dataclass
class PDFFileValidation:
    format_status: str = 'unverified'
    identity_status: str = 'needs_review'
    validation_status: str = 'needs_review'
    page_count: int = 0
    extracted_titles: List[str] = field(default_factory=list)
    extracted_dois: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    error_details: List[str] = field(default_factory=list)
    active_content_present: bool = False


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
    download_status: str = 'not_processed'
    validation_status: str = 'not_checked'
    format_status: str = 'not_checked'
    identity_status: str = 'not_checked'
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
