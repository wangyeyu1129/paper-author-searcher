"""SQLite implementation of the JobRepository port: jobs, idempotency records and worker heartbeats.

One database file is shared by the API process and any number of worker processes (on one host or a shared
volume). Every write runs in a ``BEGIN IMMEDIATE`` transaction and every status change is a conditional UPDATE,
so two workers never claim the same job and two confirmations never both win.
"""

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .model import FINISHED, IdempotencyConflict, IdempotentResponse, Job, JobStatus, WorkerState, now

JSON_COLUMNS = ('request', 'progress', 'candidates', 'result', 'error')
COLUMNS = tuple(item.name for item in fields(Job))
COLUMN_TYPES = {'attempts': 'INTEGER NOT NULL DEFAULT 0', 'cost_usd': 'REAL NOT NULL DEFAULT 0'}


def new_job_id() -> str:
    return 'srch_' + secrets.token_hex(12)


class SqliteJobRepository:
    def __init__(self, path: str | Path, idempotency_hours: float = 24) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.idempotency_window = timedelta(hours=idempotency_hours)
        # WAL lets the API read while a worker writes; the mode is a property of the file, set outside any transaction.
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
        finally:
            connection.close()
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY)')
            present = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            # Columns added by later versions join existing databases in place.
            for name in COLUMNS:
                if name not in present:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {COLUMN_TYPES.get(name, "TEXT")}')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, created_at)')
            db.execute(
                'CREATE TABLE IF NOT EXISTS idempotency (client TEXT NOT NULL, key TEXT NOT NULL,'
                ' fingerprint TEXT NOT NULL, status INTEGER, body TEXT, created_at TEXT NOT NULL,'
                ' PRIMARY KEY (client, key))'
            )
            db.execute(
                'CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, started_at TEXT NOT NULL,'
                ' heartbeat_at TEXT NOT NULL, job_id TEXT)'
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
            except BaseException:
                db.execute('ROLLBACK')
                raise
            db.execute('COMMIT')
        finally:
            db.close()

    # Jobs

    def create(self, job: Job) -> Job:
        with self._transaction() as db:
            db.execute(
                f'INSERT INTO jobs ({", ".join(COLUMNS)}) VALUES ({", ".join("?" for _ in COLUMNS)})', self._row(job)
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._transaction() as db:
            return self._fetch(db, job_id)

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
        with self._transaction() as db:
            job = self._fetch(db, job_id)
            if job is None or (expected is not None and job.status != expected):
                return None
            job = replace(job, **changes, updated_at=now())
            db.execute(
                f'UPDATE jobs SET {", ".join(f"{name} = ?" for name in COLUMNS[1:])} WHERE id = ?',
                (*self._row(job)[1:], job_id),
            )
            return job

    def claim_next(self, worker_id: str) -> Job | None:
        """Take the oldest queued job for ``worker_id`` (queued -> running), or None if the queue is empty."""
        with self._transaction() as db:
            row = db.execute(
                'SELECT id FROM jobs WHERE status = ? ORDER BY created_at, id LIMIT 1', (JobStatus.QUEUED,)
            ).fetchone()
            if row is None:
                return None
            moment = now()
            db.execute(
                'UPDATE jobs SET status = ?, worker_id = ?, started_at = ?, finished_at = NULL, updated_at = ?,'
                ' attempts = attempts + 1, progress = ? WHERE id = ?',
                (JobStatus.RUNNING, worker_id, moment, moment, json.dumps({'stage': 'Starting'}), row[0]),
            )
            return self._fetch(db, row[0])

    def requeue_stale(self, stale_after_seconds: float) -> list[str]:
        """Queue again the running jobs whose worker stopped reporting (it died); returns their IDs."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat(timespec='seconds')
        with self._transaction() as db:
            rows = db.execute(
                'SELECT jobs.id FROM jobs LEFT JOIN workers ON workers.id = jobs.worker_id'
                ' WHERE jobs.status = ? AND (workers.id IS NULL OR workers.heartbeat_at < ?)',
                (JobStatus.RUNNING, cutoff),
            ).fetchall()
            ids = [row[0] for row in rows]
            for job_id in ids:
                db.execute(
                    'UPDATE jobs SET status = ?, worker_id = NULL, progress = NULL, updated_at = ? WHERE id = ?',
                    (JobStatus.QUEUED, now(), job_id),
                )
            db.execute('DELETE FROM workers WHERE heartbeat_at < ?', (cutoff,))
        return ids

    # Workers

    def heartbeat(self, worker_id: str, job_id: str | None) -> None:
        moment = now()
        with self._transaction() as db:
            db.execute(
                'INSERT INTO workers (id, started_at, heartbeat_at, job_id) VALUES (?, ?, ?, ?)'
                ' ON CONFLICT (id) DO UPDATE SET heartbeat_at = excluded.heartbeat_at, job_id = excluded.job_id',
                (worker_id, moment, moment, job_id),
            )

    def forget_worker(self, worker_id: str) -> None:
        with self._transaction() as db:
            db.execute('DELETE FROM workers WHERE id = ?', (worker_id,))

    def workers(self) -> list[WorkerState]:
        with self._transaction() as db:
            rows = db.execute('SELECT id, started_at, heartbeat_at, job_id FROM workers ORDER BY id').fetchall()
        return [WorkerState(*row) for row in rows]

    def counts(self) -> dict[str, Any]:
        """Jobs by status, finished runs by outcome with their total duration, spend, and the oldest queued job."""
        with self._transaction() as db:
            by_status = dict(db.execute('SELECT status, COUNT(*) FROM jobs GROUP BY status').fetchall())
            finished = db.execute(
                'SELECT status, COUNT(*), SUM((julianday(finished_at) - julianday(started_at)) * 86400.0)'
                f' FROM jobs WHERE finished_at IS NOT NULL AND status IN ({", ".join("?" for _ in FINISHED)})'
                ' GROUP BY status',
                FINISHED,
            ).fetchall()
            oldest = db.execute('SELECT MIN(created_at) FROM jobs WHERE status = ?', (JobStatus.QUEUED,)).fetchone()[0]
            spend = db.execute('SELECT COALESCE(SUM(cost_usd), 0) FROM jobs').fetchone()[0]
        return {
            'by_status': {status: by_status.get(status, 0) for status in JobStatus.values()},
            'finished': {row[0]: {'count': row[1], 'seconds': row[2] or 0.0} for row in finished},
            'oldest_queued_at': oldest,
            'cost_usd': spend,
        }

    def ping(self) -> None:
        with self._transaction() as db:
            db.execute('SELECT 1')

    # Idempotency

    def reserve(self, client: str, key: str, fingerprint: str) -> IdempotentResponse | None:
        """Claim ``key`` for a new request, or return the stored response of the request that claimed it first.

        Raises IdempotencyConflict when the key's first request differs or is still being answered.
        """
        expired = (datetime.now(timezone.utc) - self.idempotency_window).isoformat(timespec='seconds')
        with self._transaction() as db:
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
        with self._transaction() as db:
            db.execute(
                'UPDATE idempotency SET status = ?, body = ? WHERE client = ? AND key = ?',
                (status, json.dumps(body, ensure_ascii=False), client, key),
            )

    def release(self, client: str, key: str) -> None:
        """Forget a claim whose request was not carried out (it failed before any state changed)."""
        with self._transaction() as db:
            db.execute('DELETE FROM idempotency WHERE client = ? AND key = ? AND status IS NULL', (client, key))

    # Rows

    def _fetch(self, db: sqlite3.Connection, job_id: str) -> Job | None:
        row = db.execute(f'SELECT {", ".join(COLUMNS)} FROM jobs WHERE id = ?', (job_id,)).fetchone()
        return self._job(row) if row else None

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
