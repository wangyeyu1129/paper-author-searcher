"""The search service's domain: jobs, their statuses, and the errors the service reports."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from paper_author_searcher.status import StrEnum


class JobStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    NEEDS_CONFIRMATION = 'needs_confirmation'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


FINISHED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.NEEDS_CONFIRMATION)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def seconds_since(moment: str | None) -> float | None:
    if not moment:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(moment)).total_seconds()


@dataclass(frozen=True)
class Job:
    id: str
    client: str
    request: dict[str, Any]
    status: str = JobStatus.QUEUED
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    # The candidate the caller confirmed ("source:author_id"), once they have.
    selection: str | None = None
    # File-store key of the identity checkpoint a confirmation continues from.
    identity_checkpoint: str | None = None
    progress: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    # Which worker runs it, when its latest run started and finished, and what the runs spent so far.
    worker_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class IdempotentResponse:
    """The first response to a request sent with an Idempotency-Key, replayed for every retry of it."""

    fingerprint: str
    status: int | None
    body: dict[str, Any] | None


class IdempotencyConflict(Exception):
    """The key was used before for a different request, or its first request is still being answered."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ErrorKind(StrEnum):
    """What went wrong, independent of any transport; the HTTP adapter maps each kind to a status code."""

    INVALID = 'invalid'
    NOT_FOUND = 'not_found'
    CONFLICT = 'conflict'
    IDEMPOTENCY_REUSED = 'idempotency_reused'
    IDEMPOTENCY_IN_PROGRESS = 'idempotency_in_progress'


class ServiceError(Exception):
    def __init__(self, kind: str, code: str, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.kind, self.code, self.message, self.details = kind, code, message, details or []


@dataclass(frozen=True)
class WorkerState:
    id: str
    started_at: str
    heartbeat_at: str
    job_id: str | None = None
