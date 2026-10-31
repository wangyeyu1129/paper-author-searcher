"""Housekeeping: finished searches and their files are deleted after a while, so storage does not grow without end."""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Job, checked_file_ids
from .ports import FileStore, JobRepository
from .worker import ARCHIVE_KEY, job_key, paper_key

logger = logging.getLogger(__name__)

BATCH = 100
SECONDS_PER_DAY = 86_400.0


class RetentionSweeper:
    def __init__(
        self,
        repository: JobRepository,
        files: FileStore,
        retention_days: float,
        http_cache_directory: str | Path | None = None,
        http_cache_retention_days: float = 0.0,
    ) -> None:
        self.repository = repository
        self.files = files
        self.retention_days = retention_days
        self.http_cache_directory = Path(http_cache_directory) if http_cache_directory else None
        self.http_cache_retention_days = http_cache_retention_days

    def sweep(self) -> tuple[int, int]:
        """Delete what has expired; returns how many searches and HTTP cache entries went."""
        jobs, entries = self._expired_jobs(), self._expired_cache_entries()
        if jobs or entries:
            logger.info('Housekeeping deleted %d searches and %d HTTP cache entries', jobs, entries)
        return jobs, entries

    def _expired_jobs(self) -> int:
        if not self.retention_days:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        moment = cutoff.isoformat(timespec='seconds')
        deleted = 0
        while batch := self.repository.finished_before(moment, BATCH):
            for job in batch:
                # Files first: a failure here leaves the job to be swept again, never a file nothing refers to.
                self._delete_files(job)
                self.repository.delete(job.id)
                deleted += 1
        return deleted

    def _delete_files(self, job: Job) -> None:
        keys = [paper_key(job.id, paper_id) for paper_id in checked_file_ids(job)]
        keys.append(job_key(job.id, ARCHIVE_KEY))
        if job.identity_checkpoint:
            keys.append(job.identity_checkpoint)
        for key in keys:
            self.files.delete(key)

    def _expired_cache_entries(self) -> int:
        """HTTP cache files last modified before the cutoff; a run that needs one again simply fetches it anew."""
        directory = self.http_cache_directory
        if directory is None or not self.http_cache_retention_days or not directory.is_dir():
            return 0
        cutoff = time.time() - self.http_cache_retention_days * SECONDS_PER_DAY
        deleted = 0
        for path in directory.rglob('*'):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError:
                # Another worker got there first.
                continue
        return deleted
