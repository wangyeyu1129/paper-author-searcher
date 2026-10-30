"""What runs a search for a job: the ``SearchBackend`` interface and the workflow-backed implementation."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import AmbiguityPolicy
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.settings import Settings, load_settings
from paper_author_searcher.status import RunStatus
from paper_author_searcher.workflow import load_checkpoint

from .jobs import Job, JobStatus
from .results import ResultView, candidates

Progress = Callable[[str, int | None, int | None], None]


@dataclass(frozen=True)
class SearchOutcome:
    """Where a run left the job: its next status and what the caller gets to see."""

    status: str
    run_directory: str | None = None
    identity_checkpoint: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class SearchBackend(Protocol):
    def run(self, job: Job, progress: Progress) -> SearchOutcome: ...

    def file_path(self, job: Job, paper_id: str) -> Path | None: ...


def query_from_request(request: dict[str, Any]) -> ProfessorQuery:
    hints = request.get('hints') or {}
    return ProfessorQuery(
        name=request['name'],
        university=request['university'],
        department=request.get('department'),
        research_interests=list(hints.get('research_interests') or []),
        university_aliases=list(hints.get('university_aliases') or []),
        orcid=hints.get('orcid'),
        homepage=hints.get('homepage'),
        email=hints.get('email'),
        known_papers=list(hints.get('known_papers') or []),
        scholar_url=hints.get('scholar_url'),
        profile_urls=list(hints.get('profile_urls') or []),
        publication_pages=list(hints.get('publication_pages') or []),
    )


@dataclass(frozen=True)
class BackendOptions:
    """Per-job run choices the server applies to every search."""

    output_directory: Path
    # Spend cap per search (OpenAlex and other metered calls); every job has its own ledger.
    maximum_cost_usd: float = 0.5
    timeout: float | None = None
    http_cache_dir: str | None = None
    http_cache_mode: str | None = None


class WorkflowBackend:
    """Runs the paper-search workflow for a job; a confirmed job continues from its identity checkpoint."""

    def __init__(
        self,
        options: BackendOptions,
        settings_factory: Callable[[], Settings] = load_settings,
        results: ResultView | None = None,
    ) -> None:
        self.options = options
        self.settings_factory = settings_factory
        self.results = results

    def run(self, job: Job, progress: Progress) -> SearchOutcome:
        request = job.request
        container = ServiceContainer(self.settings_factory(), self._run_options(job))
        workflow = container.workflow(progress=progress)
        limits = request.get('limits') or {}
        # A confirmed job continues from the identity its first run resolved, without resolving it again.
        checkpoint = job.identity_checkpoint if job.selection is not None else None
        continuing = checkpoint is not None
        report = workflow.run(
            query=None if checkpoint is not None else query_from_request(request),
            saved=load_checkpoint(checkpoint) if checkpoint is not None else None,
            output_dir=self.options.output_directory / job.id,
            mode=request.get('mode', 'full'),
            selected_author=job.selection if continuing else None,
            include_needs_review=bool(request.get('include_needs_review')),
            maximum_pdf_papers=limits.get('max_pdf_papers'),
            maximum_downloads=limits.get('max_downloads'),
        )
        return self.outcome(job, report)

    def outcome(self, job: Job, report: dict[str, Any]) -> SearchOutcome:
        directory = report.get('output_directory')
        status = report.get('run_status')
        errors = [str(item.get('error') or item) for item in report.get('errors') or []]
        if status == RunStatus.IDENTITY_CONFIRMATION_REQUIRED:
            found = candidates(report.get('identity') or {})
            if not found or job.selection is not None:
                return SearchOutcome(
                    JobStatus.FAILED,
                    directory,
                    error={
                        'code': 'identity_not_found' if not found else 'selection_rejected',
                        'message': 'No researcher matching the request could be identified'
                        if not found
                        else 'The confirmed candidate could not be used',
                        'details': errors,
                    },
                )
            checkpoint = str(Path(str(directory)) / (report.get('artifacts') or {}).get('professor', 'professor.json'))
            return SearchOutcome(JobStatus.NEEDS_CONFIRMATION, directory, checkpoint, found)
        if status not in (RunStatus.COMPLETED, RunStatus.PARTIAL):
            return SearchOutcome(
                JobStatus.FAILED,
                directory,
                error={'code': 'search_failed', 'message': f'The search ended as {status}', 'details': errors},
            )
        view = self._view(job)
        return SearchOutcome(
            JobStatus.SUCCEEDED,
            directory,
            job.identity_checkpoint,
            result=view.build(report, bool(job.request.get('include_needs_review'))),
        )

    def file_path(self, job: Job, paper_id: str) -> Path | None:
        if not job.run_directory:
            return None
        report_path = Path(job.run_directory) / 'report.json'
        if not report_path.exists():
            return None
        return self._view(job).file_path(load_checkpoint(report_path), paper_id)

    def _view(self, job: Job) -> ResultView:
        return self.results or ResultView(lambda paper_id: f'/v1/searches/{job.id}/papers/{paper_id}/pdf')

    def _run_options(self, job: Job) -> RunOptions:
        return RunOptions(
            mode=job.request.get('mode', 'full'),
            timeout=self.options.timeout,
            ambiguity_policy=AmbiguityPolicy.COLLECT_CONTENDERS
            if job.request.get('on_ambiguity') == 'collect_contenders'
            else AmbiguityPolicy.STOP,
            max_cost_usd=self.options.maximum_cost_usd,
            cost_ledger='api:' + job.id,
            http_cache_dir=self.options.http_cache_dir,
            http_cache_mode=self.options.http_cache_mode,
        )
