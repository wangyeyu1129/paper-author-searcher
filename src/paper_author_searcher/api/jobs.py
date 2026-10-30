"""Search jobs and their durable store (SQLite, stdlib): jobs, their state, and idempotency records."""

import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from paper_author_searcher.status import StrEnum


class JobStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    NEEDS_CONFIRMATION = 'needs_confirmation'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


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
    # The run directory of the latest run, and the identity checkpoint a confirmation continues from.
    run_directory: str | None = None
    identity_checkpoint: str | None = None
    progress: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


JSON_COLUMNS = ('request', 'progress', 'candidates', 'result', 'error')
COLUMNS = (
    'id',
    'client',
    'request',
    'status',
    'created_at',
    'updated_at',
    'selection',
    'run_directory',
    'identity_checkpoint',
    'progress',
    'candidates',
    'result',
    'error',
)


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


class JobStore:
    """Jobs and idempotency records in one SQLite file; every method is safe to call from any thread."""

    def __init__(self, path: str | Path, idempotency_hours: float = 24) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.idempotency_window = timedelta(hours=idempotency_hours)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute(
                'CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, client TEXT NOT NULL, request TEXT NOT NULL,'
                ' status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, selection TEXT,'
                ' run_directory TEXT, identity_checkpoint TEXT, progress TEXT, candidates TEXT, result TEXT,'
                ' error TEXT)'
            )
            db.execute(
                'CREATE TABLE IF NOT EXISTS idempotency (client TEXT NOT NULL, key TEXT NOT NULL,'
                ' fingerprint TEXT NOT NULL, status INTEGER, body TEXT, created_at TEXT NOT NULL,'
                ' PRIMARY KEY (client, key))'
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=30)
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def new_id() -> str:
        return 'srch_' + secrets.token_hex(12)

    def create(self, job: Job) -> Job:
        with self._connect() as db:
            db.execute(
                f'INSERT INTO jobs ({", ".join(COLUMNS)}) VALUES ({", ".join("?" for _ in COLUMNS)})',
                self._row(job),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._connect() as db:
            row = db.execute(f'SELECT {", ".join(COLUMNS)} FROM jobs WHERE id = ?', (job_id,)).fetchone()
        return self._job(row) if row else None

    def update(self, job_id: str, **changes: Any) -> Job:
        """Apply ``changes`` to the job and stamp ``updated_at``; unknown fields raise."""
        job = self.transition(job_id, None, **changes)
        if job is None:
            raise KeyError(job_id)
        return job

    def transition(self, job_id: str, expected: str | None, **changes: Any) -> Job | None:
        """Apply ``changes`` only if the job is in status ``expected`` (any status when None), atomically.

        None when the job is missing or in another status: someone else moved it first.
        """
        with self._connect() as db:
            row = db.execute(f'SELECT {", ".join(COLUMNS)} FROM jobs WHERE id = ?', (job_id,)).fetchone()
            if row is None or (expected is not None and self._job(row).status != expected):
                return None
            job = replace(self._job(row), **changes, updated_at=now())
            db.execute(
                f'UPDATE jobs SET {", ".join(f"{name} = ?" for name in COLUMNS[1:])} WHERE id = ?',
                (*self._row(job)[1:], job_id),
            )
        return job

    def with_status(self, *statuses: str) -> list[Job]:
        with self._connect() as db:
            rows = db.execute(
                f'SELECT {", ".join(COLUMNS)} FROM jobs WHERE status IN ({", ".join("?" for _ in statuses)})'
                ' ORDER BY created_at',
                statuses,
            ).fetchall()
        return [self._job(row) for row in rows]

    def reserve(self, client: str, key: str, fingerprint: str) -> IdempotentResponse | None:
        """Claim ``key`` for a new request, or return the stored response of the request that claimed it first.

        Raises IdempotencyConflict when the key's first request differs or is still being answered.
        """
        expired = (datetime.now(timezone.utc) - self.idempotency_window).isoformat(timespec='seconds')
        with self._connect() as db:
            db.execute('DELETE FROM idempotency WHERE created_at < ?', (expired,))
            row = db.execute(
                'SELECT fingerprint, status, body FROM idempotency WHERE client = ? AND key = ?', (client, key)
            ).fetchone()
            if row is None:
                db.execute(
                    'INSERT INTO idempotency (client, key, fingerprint, created_at) VALUES (?, ?, ?, ?)',
                    (client, key, fingerprint, now()),
                )
                return None
        stored = IdempotentResponse(row[0], row[1], json.loads(row[2]) if row[2] else None)
        if stored.fingerprint != fingerprint:
            raise IdempotencyConflict('reused')
        if stored.status is None:
            raise IdempotencyConflict('in_progress')
        return stored

    def remember(self, client: str, key: str, status: int, body: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                'UPDATE idempotency SET status = ?, body = ? WHERE client = ? AND key = ?',
                (status, json.dumps(body, ensure_ascii=False), client, key),
            )

    def release(self, client: str, key: str) -> None:
        """Forget a claim whose request was not carried out (it failed before any state changed)."""
        with self._connect() as db:
            db.execute('DELETE FROM idempotency WHERE client = ? AND key = ? AND status IS NULL', (client, key))

    @staticmethod
    def _row(job: Job) -> tuple[Any, ...]:
        values = []
        for name in COLUMNS:
            value = getattr(job, name)
            values.append(
                json.dumps(value, ensure_ascii=False) if name in JSON_COLUMNS and value is not None else value
            )
        return tuple(values)

    @staticmethod
    def _job(row: tuple[Any, ...]) -> Job:
        values = dict(zip(COLUMNS, row, strict=False))
        for name in JSON_COLUMNS:
            if values[name] is not None:
                values[name] = json.loads(values[name])
        return Job(**values)
