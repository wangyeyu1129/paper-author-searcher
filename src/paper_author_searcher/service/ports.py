"""Ports: what the search service needs from the outside, as interfaces. Adapters implement them.

    JobRepository   jobs, idempotency records and worker heartbeats  (sqlite.SqliteJobRepository)
    FileStore       PDFs, zips and identity checkpoints              (files.LocalFileStore, files.S3FileStore)
    SearchRunner    runs one search                                  (runner.WorkflowRunner)

The service core (facade, worker) depends only on these; HTTP, SQLite, S3 and the workflow are swappable.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .model import IdempotentResponse, Job, WorkerState

Progress = Callable[[str, int | None, int | None], None]


class JobRepository(Protocol):
    def create(self, job: Job) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def update(self, job_id: str, **changes: Any) -> Job: ...

    def transition(self, job_id: str, expected: str | None, **changes: Any) -> Job | None: ...

    def claim_next(self, worker_id: str) -> Job | None: ...

    def requeue_stale(self, stale_after_seconds: float) -> list[str]: ...

    def heartbeat(self, worker_id: str, job_id: str | None) -> None: ...

    def forget_worker(self, worker_id: str) -> None: ...

    def workers(self) -> list[WorkerState]: ...

    def counts(self) -> dict[str, Any]: ...

    def reserve(self, client: str, key: str, fingerprint: str) -> IdempotentResponse | None: ...

    def remember(self, client: str, key: str, status: int, body: dict[str, Any]) -> None: ...

    def release(self, client: str, key: str) -> None: ...

    def ping(self) -> None: ...


@dataclass(frozen=True)
class StoredFile:
    """Where a stored file can be read: a local path to stream, or a URL the caller may fetch directly."""

    path: Path | None = None
    url: str | None = None


class FileStore(Protocol):
    def put(self, key: str, source: Path, content_type: str) -> None: ...

    def fetch(self, key: str, destination: Path) -> bool: ...

    def exists(self, key: str) -> bool: ...

    def locate(self, key: str, download_name: str) -> StoredFile | None: ...

    def check(self) -> None: ...

    def create_bucket(self) -> bool: ...


@dataclass(frozen=True)
class SearchOutcome:
    """Where a run left the job: its next status, what the caller gets to see, and the files to keep."""

    status: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    # The resolved identity a confirmation continues from (a local file of this run).
    identity_checkpoint: Path | None = None
    # Checked PDFs by result paper ID (local files of this run).
    files: dict[str, Path] = field(default_factory=dict)
    cost_usd: float = 0.0
    run_directory: Path | None = None


class SearchRunner(Protocol):
    def run(self, job: Job, identity_checkpoint: Path | None, progress: Progress) -> SearchOutcome: ...
