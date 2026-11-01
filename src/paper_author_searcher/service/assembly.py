"""Composition root of the search service: builds the facade, its adapters and workers from ``Settings``."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path

from paper_author_searcher.composition import ServiceContainer, TransportLimits
from paper_author_searcher.settings import Settings

from .backup import last_backup_time
from .facade import SearchService
from .files import LocalFileStore, S3FileStore
from .model import SubmissionLimits
from .ports import FileStore, SearchRunner
from .retention import RetentionSweeper
from .runner import RunnerOptions, WorkflowRunner
from .s3 import S3Location
from .shared_limits import SqliteSharedLimits
from .sqlite import SqliteJobRepository
from .worker import SearchWorker

SECONDS_PER_HOUR = 3600.0


def _shared_transport(settings: Settings, data: Path) -> tuple[Settings, Callable[..., ServiceContainer]]:
    """Settings with the deployment's host intervals, and a container factory that keeps them across all workers."""
    service = settings.service
    shared = SqliteSharedLimits(data / 'limits.sqlite', service.maximum_daily_cost_usd)
    limits = TransportLimits(pacer_factory=shared.pacer, daily_budget=shared)
    paced = replace(settings, http=replace(settings.http, host_request_intervals=service.shared_host_intervals))
    return paced, partial(ServiceContainer, transport_limits=limits)


def file_store(settings: Settings) -> FileStore:
    service = settings.service
    if service.file_store == 's3':
        return S3FileStore(
            S3Location(service.s3_endpoint, service.s3_bucket, service.s3_region, service.s3_addressing),
            settings.credentials.s3_access_key_id or '',
            settings.credentials.s3_secret_access_key or '',
            prefix=service.s3_prefix,
            presign_seconds=service.presign_seconds,
            public_endpoint=service.s3_public_endpoint,
        )
    return LocalFileStore(Path(service.data_directory) / 'files')


@dataclass
class Deployment:
    """The assembled service: the facade front ends call, and the workers of this process (if any)."""

    service: SearchService
    workers: list[SearchWorker] = field(default_factory=list)
    threads: list[threading.Thread] = field(default_factory=list)

    def start_workers(self) -> None:
        for worker in self.workers:
            thread = threading.Thread(target=worker.run_forever, name=f'worker-{worker.id}', daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self, timeout: float | None = None) -> None:
        for worker in self.workers:
            worker.stop()
        for thread in self.threads:
            thread.join(timeout)


def assemble(
    settings: Settings,
    workers: int,
    download_link: Callable[[str, str], str],
    runner: SearchRunner | None = None,
    files: FileStore | None = None,
) -> Deployment:
    """The service plus ``workers`` worker threads (not started); ``runner``/``files`` replace the defaults."""
    service_config = settings.service
    data = Path(service_config.data_directory)
    repository = SqliteJobRepository(data / 'jobs.sqlite', service_config.idempotency_hours)
    store = files or file_store(settings)
    run_settings, container = _shared_transport(settings, data)
    runner = runner or WorkflowRunner(
        run_settings,
        RunnerOptions(
            data / 'runs',
            maximum_cost_usd=service_config.maximum_cost_usd,
            http_cache_directory=service_config.http_cache_directory or None,
            http_cache_mode=service_config.http_cache_mode,
        ),
        download_link,
        container,
    )
    sweeper = RetentionSweeper(
        repository,
        store,
        service_config.retention_days,
        service_config.http_cache_directory or None,
        service_config.http_cache_retention_days,
    )
    pool = [
        SearchWorker(
            repository,
            runner,
            store,
            data / 'scratch',
            poll_seconds=service_config.worker_poll_seconds,
            heartbeat_seconds=service_config.heartbeat_seconds,
            stale_after_seconds=service_config.stale_after_seconds,
            keep_run_directories=service_config.keep_run_directories,
            maximum_attempts=service_config.maximum_attempts,
            housekeeping=sweeper.sweep,
            housekeeping_seconds=service_config.housekeeping_hours * SECONDS_PER_HOUR,
        )
        for _ in range(workers)
    ]
    required = (
        {'dblp_index': settings.dblp_dump.index_path, 'catalog_store': settings.catalogs.store_path}
        if service_config.require_indexes
        else {}
    )

    def wake() -> None:
        for worker in pool:
            worker.wake()

    service = SearchService(
        repository,
        store,
        stale_after_seconds=service_config.stale_after_seconds,
        required_paths=required,
        on_submit=wake,
        last_backup=partial(last_backup_time, data),
        limits=SubmissionLimits(
            service_config.maximum_active_searches,
            service_config.maximum_searches_per_hour,
            service_config.rate_limit_retry_seconds,
        ),
    )
    return Deployment(service, pool)
