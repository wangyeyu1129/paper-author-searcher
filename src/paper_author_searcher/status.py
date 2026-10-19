"""Closed status vocabularies shared by every pipeline stage, report and test.

Each status is a ``str`` enum: members compare, hash and JSON-serialise exactly like their values, so saved reports
and checkpoints written before this module existed load and compare unchanged. ``str(member)`` is the bare value.
"""

from enum import Enum


class StrEnum(str, Enum):
    """``str`` mixin enum whose ``str()`` is its value (the standard library's StrEnum needs Python 3.11)."""

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def values(cls) -> tuple:
        return tuple(member.value for member in cls)


class ResolutionStatus(StrEnum):
    """Outcome of resolving a professor query to one researcher identity."""

    RESOLVED = 'resolved'
    AMBIGUOUS = 'ambiguous'
    AMBIGUOUS_CONTENDERS = 'ambiguous_contenders'  # several plausible profiles kept for joint collection
    NEEDS_REVIEW = 'needs_review'
    UNKNOWN = 'unknown'


class UniversityStatus(StrEnum):
    MATCHED = 'matched'
    MISMATCH = 'mismatch'
    UNKNOWN = 'unknown'


class OwnershipStatus(StrEnum):
    """Whether a collected paper belongs to the resolved researcher."""

    VERIFIED = 'verified'
    NEEDS_REVIEW = 'needs_review'
    REJECTED = 'rejected'


class FetchStatus(StrEnum):
    """Completeness of one source's paper listing for one author ID."""

    COMPLETE = 'complete'
    COMPLETE_WITH_PARSE_ISSUES = 'complete_with_parse_issues'
    COMPLETE_WITH_METADATA_ISSUES = 'complete_with_metadata_issues'
    PARTIAL = 'partial'
    UNAVAILABLE = 'unavailable'


class RunStatus(StrEnum):
    """Lifecycle of a workflow run or of one benchmark person."""

    RUNNING = 'running'
    COMPLETED = 'completed'
    PARTIAL = 'partial'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    IDENTITY_CONFIRMATION_REQUIRED = 'identity_confirmation_required'


class StageStatus(StrEnum):
    COMPLETED = 'completed'
    REUSED = 'reused'


class ProviderStatus(StrEnum):
    """Result of one PDF source lookup for one paper."""

    OK = 'ok'
    PARTIAL = 'partial'
    ERROR = 'error'
    NOT_FOUND = 'not_found'
    NOT_APPLICABLE = 'not_applicable'
    DISABLED = 'disabled'
    DEFERRED = 'deferred'


class PDFValidationStatus(StrEnum):
    """Validation of a PDF URL (per paper) or of a downloaded file."""

    VERIFIED = 'verified'
    UNVERIFIED = 'unverified'
    NEEDS_REVIEW = 'needs_review'
    INVALID = 'invalid'
    REJECTED = 'rejected'  # a downloaded file whose content is another paper
    NOT_FOUND = 'not_found'
    NOT_PROCESSED = 'not_processed'
    NOT_CHECKED = 'not_checked'
    SKIPPED_OWNERSHIP = 'skipped_ownership'


class IdentityStatus(StrEnum):
    """Whether a PDF (URL metadata or file content) is the paper it claims to be."""

    VERIFIED = 'verified'
    UNVERIFIED = 'unverified'
    NEEDS_REVIEW = 'needs_review'
    REJECTED = 'rejected'
    CONFLICTING = 'conflicting'
    NOT_CHECKED = 'not_checked'


class FormatStatus(StrEnum):
    VALID = 'valid'
    INVALID = 'invalid'
    UNVERIFIED = 'unverified'
    NOT_CHECKED = 'not_checked'


class AccessStatus(StrEnum):
    PUBLICLY_ACCESSIBLE = 'publicly_accessible'
    UNREACHABLE = 'unreachable'
    UNKNOWN = 'unknown'


class DownloadStatus(StrEnum):
    NOT_PROCESSED = 'not_processed'
    DOWNLOADING = 'downloading'
    DOWNLOADED = 'downloaded'
    INTERRUPTED = 'interrupted'
    FAILED = 'failed'
    DUPLICATE_CONTENT = 'duplicate_content'
    REJECTED_CONTENT = 'rejected_content'
    NON_FULL_DOCUMENT = 'non_full_document'
    SKIPPED_EXISTING = 'skipped_existing'
    SKIPPED_LIMIT = 'skipped_limit'
    SKIPPED_OWNERSHIP = 'skipped_ownership'
    SKIPPED_UNVERIFIED_URL = 'skipped_unverified_url'


class AttemptStatus(StrEnum):
    """One download attempt of one candidate URL."""

    STARTED = 'started'
    COMPLETED = 'completed'
    FAILED = 'failed'
    IDENTITY_MISMATCH = 'identity_mismatch'
    NON_FULL_DOCUMENT = 'non_full_document'


class CacheStatus(StrEnum):
    HIT = 'hit'
    MISS = 'miss'
    NOT_QUERIED = 'not_queried'


class RequestOutcome(StrEnum):
    NETWORK_RESPONSE = 'network_response'
    CACHED_RESPONSE = 'cached_response'
    CACHED_FAILURE = 'cached_failure'
    REQUEST_FAILURE = 'request_failure'
    COOLDOWN_SKIP = 'cooldown_skip'
    SOURCE_UNAVAILABLE_SKIP = 'source_unavailable_skip'
    QUERY_BUDGET_EXHAUSTED = 'query_budget_exhausted'


class AnnotationState(StrEnum):
    """Review state of a ground-truth file."""

    CANDIDATE_COLLECTION = 'candidate_collection'
    PENDING_HUMAN_REVIEW = 'pending_human_review'
    REVIEW_INCOMPLETE = 'review_incomplete'
    PARTIALLY_HUMAN_REVIEWED = 'partially_human_reviewed'
    ASSISTANT_REVIEWED = 'assistant_reviewed'
    HUMAN_REVIEWED = 'human_reviewed'


class TruthState(StrEnum):
    """How far a benchmark person's ground truth can be trusted when scoring."""

    HUMAN_REVIEWED = 'human_reviewed'
    ASSISTANT_REVIEWED = 'assistant_reviewed'
    PROVISIONAL = 'provisional'
    MISSING = 'missing'
