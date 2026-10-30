"""SearchService: the one interface the outside world uses to search (Facade over the ports).

Front ends (the HTTP API, a CLI, a test) call only this class. It queues searches as jobs (commands the workers
carry out), answers what a job's state is, takes namesake confirmations, hands out stored files, and makes
submissions idempotent. It knows nothing of HTTP: failures are ``ServiceError`` kinds the front end maps.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import agent_view
from .model import ErrorKind, IdempotencyConflict, Job, JobStatus, ServiceError, seconds_since
from .ports import FileStore, JobRepository, StoredFile
from .results import selections
from .sqlite import new_job_id
from .worker import ARCHIVE_KEY, job_key, paper_key


@dataclass(frozen=True)
class Submission:
    job: Job
    # True when an earlier request with the same Idempotency-Key created the job.
    replayed: bool = False


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ok: bool
    detail: str = ''


class SearchService:
    def __init__(
        self,
        repository: JobRepository,
        files: FileStore,
        stale_after_seconds: float = 120.0,
        expect_workers: bool = True,
        required_paths: dict[str, str] | None = None,
        on_submit: Callable[[], None] | None = None,
    ) -> None:
        self.repository = repository
        self.files = files
        self.stale_after_seconds = stale_after_seconds
        # Readiness wants a live worker unless searches are run some other way (tests, a one-off batch).
        self.expect_workers = expect_workers
        # Files readiness requires, by name (the DBLP index, the catalog store).
        self.required_paths = required_paths or {}
        # Wakes in-process workers early; separate worker processes find new jobs by polling.
        self.on_submit = on_submit

    # Commands

    def submit(self, client: str, request: dict[str, Any], idempotency_key: str | None = None) -> Submission:
        """Queue a search (an already validated request)."""

        def create() -> Job:
            job = self.repository.create(Job(new_job_id(), client, request))
            self._wake()
            return job

        return self._idempotent(client, idempotency_key, ('submit', request), create)

    def confirm(self, client: str, job_id: str, selection: str, idempotency_key: str | None = None) -> Submission:
        """Pick a candidate of a job waiting for confirmation; the job is queued to continue for that person."""

        def queue() -> Job:
            job = self.job(client, job_id)
            if job.status != JobStatus.NEEDS_CONFIRMATION:
                raise ServiceError(ErrorKind.CONFLICT, 'not_awaiting_confirmation', f'The search is {job.status}')
            allowed = selections(job.candidates or [])
            if selection not in allowed:
                raise ServiceError(
                    ErrorKind.INVALID, 'unknown_selection', "Pick one of the candidates' selections", sorted(allowed)
                )
            queued = self.repository.transition(
                job.id, JobStatus.NEEDS_CONFIRMATION, status=JobStatus.QUEUED, selection=selection, error=None
            )
            if queued is None:
                raise ServiceError(
                    ErrorKind.CONFLICT, 'not_awaiting_confirmation', 'The search was confirmed by another request'
                )
            self._wake()
            return queued

        return self._idempotent(client, idempotency_key, ('confirm', job_id, selection), queue)

    # Queries

    def job(self, client: str, job_id: str) -> Job:
        job = self.repository.get(job_id)
        # Another caller's job is reported as missing, not as forbidden: its existence is not theirs to know.
        if job is None or job.client != client:
            raise ServiceError(ErrorKind.NOT_FOUND, 'not_found', 'No such search')
        return job

    def agent_view(self, client: str, job_id: str) -> dict[str, Any]:
        """The search as the cold-email agent may use it (agent.agent_view)."""
        return agent_view(self.job(client, job_id))

    def paper_file(self, client: str, job_id: str, paper_id: str) -> StoredFile:
        job = self.job(client, job_id)
        known = job.status == JobStatus.SUCCEEDED and paper_id in _file_ids(job)
        stored = self.files.locate(paper_key(job.id, paper_id), f'{job.id}-{paper_id}.pdf') if known else None
        if stored is None:
            raise ServiceError(ErrorKind.NOT_FOUND, 'not_found', 'This search has no checked PDF for that paper')
        return stored

    def archive(self, client: str, job_id: str) -> StoredFile:
        job = self.job(client, job_id)
        stored = (
            self.files.locate(job_key(job.id, ARCHIVE_KEY), f'{job.id}-pdfs.zip')
            if job.status == JobStatus.SUCCEEDED and _file_ids(job)
            else None
        )
        if stored is None:
            raise ServiceError(ErrorKind.NOT_FOUND, 'not_found', 'This search has no checked PDFs')
        return stored

    def readiness(self) -> list[ReadinessCheck]:
        checks = [self._check('jobs_database', self.repository.ping), self._check('file_store', self.files.check)]
        if self.expect_workers:
            alive = self.live_workers()
            checks.append(ReadinessCheck('workers', alive > 0, f'{alive} alive'))
        for name, path in self.required_paths.items():
            present = bool(path) and Path(path).is_file()
            checks.append(ReadinessCheck(name, present, path or 'not configured'))
        return checks

    def snapshot(self) -> dict[str, Any]:
        """Queue and job statistics for metrics."""
        counts = self.repository.counts()
        oldest = seconds_since(counts['oldest_queued_at'])
        return {**counts, 'oldest_queued_seconds': oldest or 0.0, 'workers_alive': self.live_workers()}

    def live_workers(self) -> int:
        return sum(
            1
            for worker in self.repository.workers()
            if (seconds_since(worker.heartbeat_at) or 0.0) < self.stale_after_seconds
        )

    # Helpers

    def _idempotent(
        self, client: str, key: str | None, operation: tuple[Any, ...], action: Callable[[], Job]
    ) -> Submission:
        if key is None:
            return Submission(action())
        fingerprint = hashlib.sha256(json.dumps(operation, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            stored = self.repository.reserve(client, key, fingerprint)
        except IdempotencyConflict as conflict:
            if conflict.reason == 'reused':
                raise ServiceError(
                    ErrorKind.IDEMPOTENCY_REUSED,
                    'idempotency_key_reused',
                    'This Idempotency-Key was used for a different request',
                ) from conflict
            raise ServiceError(
                ErrorKind.IDEMPOTENCY_IN_PROGRESS,
                'idempotency_in_progress',
                'The first request with this key is still running',
            ) from conflict
        if stored is not None:
            # A retry gets the job its first request acted on, as it stands now.
            return Submission(self.job(client, (stored.body or {})['job_id']), replayed=True)
        try:
            job = action()
        except ServiceError:
            # Refused before any state changed: a corrected retry may reuse the key.
            self.repository.release(client, key)
            raise
        self.repository.remember(client, key, 1, {'job_id': job.id})
        return Submission(job)

    def _wake(self) -> None:
        if self.on_submit is not None:
            self.on_submit()

    @staticmethod
    def _check(name: str, probe: Callable[[], None]) -> ReadinessCheck:
        try:
            probe()
        except Exception as exc:
            return ReadinessCheck(name, False, str(exc))
        return ReadinessCheck(name, True)


def _file_ids(job: Job) -> set[str]:
    """IDs of the result's papers and editions that have a checked file."""
    papers = (job.result or {}).get('papers') or []
    return {
        edition['id']
        for paper in papers
        for edition in [paper, *(paper.get('editions') or [])]
        if edition['full_text']['status'] == 'file'
    }
