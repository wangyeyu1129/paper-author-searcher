import typing
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Union

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
    StrEnum,
    UniversityStatus,
)

_STATUS_FIELDS: dict[type, tuple[tuple[str, type[StrEnum]], ...]] = {}


def _status_fields(cls: type) -> tuple[tuple[str, type[StrEnum]], ...]:
    """(field, enum) pairs of a dataclass, resolved once per class."""
    found = _STATUS_FIELDS.get(cls)
    if found is None:
        hints = typing.get_type_hints(cls)
        found = _STATUS_FIELDS[cls] = tuple(
            (item.name, hints[item.name])
            for item in fields(cls)
            if isinstance(hints[item.name], type) and issubclass(hints[item.name], StrEnum)
        )
    return found


class ValidatedStatuses:
    """Mixin: status fields hold their enum member; an unknown value is an error, not a silent new state."""

    __slots__ = ()  # keeps slotted subclasses free of a per-instance __dict__

    def __post_init__(self) -> None:
        for name, enum in _status_fields(type(self)):
            value = getattr(self, name)
            if isinstance(value, enum):
                continue
            try:
                setattr(self, name, enum(value))
            except ValueError:
                raise ValueError(
                    f'{type(self).__name__}.{name}: {value!r} is not one of {", ".join(enum.values())}'
                ) from None


@dataclass
class ProfessorQuery:
    name: str
    university: str
    department: str | None = None
    research_interests: list[str] = field(default_factory=list)
    university_aliases: list[str] = field(default_factory=list)
    # Optional identity hints; any of them sharply reduces namesake ambiguity.
    orcid: str | None = None
    homepage: str | None = None
    email: str | None = None
    known_papers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.university.strip():
            raise ValueError('name and university are required')

    def has_hints(self) -> bool:
        return bool(self.orcid or self.homepage or self.known_papers)


@dataclass(slots=True)
class AuthorCandidate:
    source: str
    author_id: str
    name: str
    affiliations: list[str] = field(default_factory=list)
    current_affiliations: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    orcid: str | None = None
    paper_ids: list[str] = field(default_factory=list)
    url: str | None = None
    coauthors: list[str] = field(default_factory=list)
    metadata_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, order=True, slots=True)
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
class ProfessorProfile(ValidatedStatuses):
    name: str
    affiliations: list[str]
    author_ids: dict[str, str]
    identity_confidence: float
    status: ResolutionStatus
    evidence: list[str]
    orcid: str | None = None
    research_interests: list[str] = field(default_factory=list)
    university: str | None = None
    university_status: UniversityStatus = UniversityStatus.UNKNOWN
    match_score: float = 0.0
    requested_university: str | None = None
    matched_affiliations: list[str] = field(default_factory=list)
    author_id_aliases: dict[str, list[str]] = field(default_factory=dict)
    name_aliases: list[str] = field(default_factory=list)
    identity_anchors: list[AnchorEvidence] = field(default_factory=list)
    adjudication: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.identity_anchors = [AnchorEvidence.coerce(item) for item in self.identity_anchors]


@dataclass
class ResolutionResult:
    profile: ProfessorProfile | None
    candidates: list[ProfessorProfile]
    source_errors: dict[str, str] = field(default_factory=dict)

    ambiguity: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PaperRecord(ValidatedStatuses):
    title: str
    authors: list[str]
    year: int | None
    doi: str | None
    venue: str | None
    abstract: str | None
    source: str
    source_paper_id: str
    source_author_id: str
    paper_url: str | None
    publication_type: str | None = None
    author_details: list[dict] = field(default_factory=list)
    target_author_present: bool | None = None
    attribution_evidence: list[str] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    raw_metadata: dict = field(default_factory=dict)
    authors_complete: bool | None = None
    metadata_errors: list[str] = field(default_factory=list)
    ownership_status: OwnershipStatus = OwnershipStatus.NEEDS_REVIEW
    ownership_evidence: list[str] = field(default_factory=list)
    paper_type: str = 'unknown'
    type_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Paper(PaperRecord):
    records: list[PaperRecord] = field(default_factory=list)
    deduplication_evidence: list[str] = field(default_factory=list)
    related_versions: list[dict] = field(default_factory=list)
    source_identifiers: list[dict] = field(default_factory=list)


@dataclass
class PaperSourceResult:
    source: str
    author_id: str | None
    papers: list[PaperRecord] = field(default_factory=list)
    complete: bool = False
    pages_fetched: int = 0
    records_received: int = 0
    rejected_records: list[dict] = field(default_factory=list)
    error: str | None = None
    pagination_complete: bool = False
    metadata_errors: list[dict] = field(default_factory=list)


@dataclass
class PaperCollectionResult(ValidatedStatuses):
    papers: list[Paper]
    sources: list[PaperSourceResult]
    complete: bool
    selected_author_ids: dict[str, list[str]]
    identity_confirmation: str
    professor_identity_status: ResolutionStatus = ResolutionStatus.UNKNOWN

    def to_dict(self, academic_only: bool = False, ownership_statuses: Sequence[str] | None = None) -> dict[str, Any]:
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


@dataclass(slots=True)
class PDFCandidate(ValidatedStatuses):
    url: str
    source: str
    version_type: str = 'unknown'
    access_basis: str = ''
    license: str | None = None
    identity_status: IdentityStatus = IdentityStatus.UNVERIFIED
    identity_evidence: list[str] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    access_status: AccessStatus = AccessStatus.UNKNOWN
    validation_status: PDFValidationStatus = PDFValidationStatus.UNVERIFIED
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    bytes_read: int = 0
    pdf_signature_confirmed: bool = False
    validation_evidence: list[str] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)
    request_context: dict = field(default_factory=dict)
    query_error_type: str | None = None


@dataclass
class PDFProviderResult(ValidatedStatuses):
    source: str
    candidates: list[PDFCandidate] = field(default_factory=list)
    status: ProviderStatus = ProviderStatus.OK
    errors: list[str] = field(default_factory=list)
    landing_pages: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class PaperPDFResult(ValidatedStatuses):
    entry_index: int
    title: str
    doi: str | None
    ownership_status: OwnershipStatus
    ownership_evidence: list[str] = field(default_factory=list)
    pdf_candidates: list[PDFCandidate] = field(default_factory=list)
    best_pdf_url: str | None = None
    pdf_source: str | None = None
    access_status: AccessStatus = AccessStatus.UNKNOWN
    validation_status: PDFValidationStatus = PDFValidationStatus.NOT_PROCESSED
    version_type: str = 'unknown'
    error_details: list[dict] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    source: str | None = None
    source_paper_id: str | None = None
    external_ids: dict = field(default_factory=dict)
    source_author_id: str | None = None
    paper_metadata: dict = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)
    candidate_decisions: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


@dataclass
class PDFFileValidation(ValidatedStatuses):
    format_status: FormatStatus = FormatStatus.UNVERIFIED
    identity_status: IdentityStatus = IdentityStatus.NEEDS_REVIEW
    validation_status: PDFValidationStatus = PDFValidationStatus.NEEDS_REVIEW
    page_count: int = 0
    extracted_titles: list[str] = field(default_factory=list)
    extracted_dois: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)
    active_content_present: bool = False
    document_type: str = 'unknown'
    document_type_evidence: list[str] = field(default_factory=list)
    observations: dict = field(default_factory=dict)


@dataclass(slots=True)
class PaperDownloadResult(ValidatedStatuses):
    paper_key: str
    title: str
    ownership_status: OwnershipStatus
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    source: str | None = None
    pdf_source: str | None = None
    pdf_url: str | None = None
    final_url: str | None = None
    local_filename: str | None = None
    download_status: DownloadStatus = DownloadStatus.NOT_PROCESSED
    validation_status: PDFValidationStatus = PDFValidationStatus.NOT_CHECKED
    format_status: FormatStatus = FormatStatus.NOT_CHECKED
    identity_status: IdentityStatus = IdentityStatus.NOT_CHECKED
    file_size: int | None = None
    sha256: str | None = None
    version_type: str = 'unknown'
    error_details: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    duplicate_of: str | None = None
    resolver_error_details: list[dict] = field(default_factory=list)
    ownership_evidence: list[str] = field(default_factory=list)
    access_basis: str = ''
    license: str | None = None
    pdf_provenance: list[dict] = field(default_factory=list)
    source_paper_id: str | None = None
    source_author_id: str | None = None
    external_ids: dict = field(default_factory=dict)
    paper_metadata: dict = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)
    document_type: str = 'unknown'
    # Position of the paper in the collection (papers.json); joins back to the full record.
    entry_index: int | None = None
    search_sufficiently_satisfied: bool | None = False
    search_satisfaction_reason: str = 'no_integrity_checked_local_file'


@dataclass
class PDFResolutionResult:
    papers: list[PaperPDFResult]
    statistics: dict

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema_version': 1,
            'validation_scope': 'bounded_file_signature_and_source_metadata_identity_not_full_text',
            'statistics': self.statistics,
            'papers': [asdict(paper) for paper in self.papers],
        }
