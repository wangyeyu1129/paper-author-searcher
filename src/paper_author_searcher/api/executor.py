"""Run queued jobs in the background and record where each run leaves its job."""

import logging
import time
from concurrent.futures import Executor, ThreadPoolExecutor

from .backend import SearchBackend
from .jobs import JobStatus, JobStore

logger = logging.getLogger(__name__)


class JobExecutor:
    """Hands queued jobs to the backend on worker threads; the store is the only state they share.

    Searches pace their requests per host inside one run, not across runs, so ``workers`` defaults to 1: more
    workers finish more searches at once but send the sources proportionally more traffic.
    """

    def __init__(
        self,
        store: JobStore,
        backend: SearchBackend,
        pool: Executor | None = None,
        workers: int = 1,
        progress_interval: float = 1.0,
    ) -> None:
        self.store = store
        self.progress_interval = progress_interval
        self.backend = backend
        self.pool = pool or ThreadPoolExecutor(max_workers=workers, thread_name_prefix='search')

    def submit(self, job_id: str) -> None:
        self.pool.submit(self.process, job_id)

    def recover(self) -> int:
        """Queue again the jobs a previous server left queued or running (a run restarts from its beginning)."""
        pending = self.store.with_status(JobStatus.QUEUED, JobStatus.RUNNING)
        for job in pending:
            self.store.update(job.id, status=JobStatus.QUEUED, progress=None)
            self.submit(job.id)
        return len(pending)

    def process(self, job_id: str) -> None:
        job = self.store.transition(job_id, JobStatus.QUEUED, status=JobStatus.RUNNING, progress={'stage': 'Starting'})
        if job is None:
            return
        last_stage, last_write = '', 0.0

        def progress(stage: str, completed: int | None = None, total: int | None = None) -> None:
            # Counters tick per paper; write a new stage at once, a counter at most every progress_interval s.
            nonlocal last_stage, last_write
            moment = time.monotonic()
            if stage == last_stage and moment - last_write < self.progress_interval:
                return
            last_stage, last_write = stage, moment
            self.store.update(job_id, progress={'stage': stage, 'completed': completed, 'total': total})

        try:
            outcome = self.backend.run(job, progress)
        except Exception as exc:
            logger.exception('Search %s failed', job_id)
            self.store.update(
                job_id,
                status=JobStatus.FAILED,
                progress=None,
                error={'code': 'internal_error', 'message': 'The search failed unexpectedly', 'details': [str(exc)]},
            )
            return
        self.store.update(
            job_id,
            status=outcome.status,
            progress=None,
            run_directory=outcome.run_directory,
            identity_checkpoint=outcome.identity_checkpoint or job.identity_checkpoint,
            candidates=outcome.candidates or job.candidates,
            result=outcome.result,
            error=outcome.error,
        )

    def shutdown(self, wait: bool = True) -> None:
        self.pool.shutdown(wait=wait)
