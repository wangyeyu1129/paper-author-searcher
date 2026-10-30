"""SearchRunner adapter: one search through the paper-search workflow, in a scratch run directory."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import AmbiguityPolicy
from paper_author_searcher.http_budget import release_ledger
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.settings import Settings
from paper_author_searcher.status import RunStatus
from paper_author_searcher.workflow import load_checkpoint

from .model import Job, JobStatus
from .ports import Progress, SearchOutcome
from .results import ResultView, candidates


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
class RunnerOptions:
    """Per-search run choices applied to every job."""

    run_directory: Path
    # Spend cap per run (OpenAlex and other metered calls); every run has its own ledger.
    maximum_cost_usd: float = 0.5
    http_cache_directory: str | None = None
    http_cache_mode: str | None = None


class WorkflowRunner:
    """Runs the workflow for a job; a confirmed job continues from its identity checkpoint, not resolving again.

    ``download_link(job_id, paper_id)`` names where callers fetch a paper's file; it belongs to the front end
    (the HTTP adapter's route), so the runner only fills it in.
    """

    def __init__(
        self,
        settings: Settings,
        options: RunnerOptions,
        download_link: Callable[[str, str], str],
        container: Callable[[Settings, RunOptions], ServiceContainer] = ServiceContainer,
    ) -> None:
        self.settings = settings
        self.options = options
        self.download_link = download_link
        self.container = container

    def run(self, job: Job, identity_checkpoint: Path | None, progress: Progress) -> SearchOutcome:
        request = job.request
        ledger = f'service:{job.id}:{job.attempts}'
        workflow = self.container(self.settings, self._run_options(job, ledger)).workflow(progress=progress)
        limits = request.get('limits') or {}
        continuing = job.selection is not None and identity_checkpoint is not None
        try:
            report = workflow.run(
                query=None if continuing else query_from_request(request),
                saved=load_checkpoint(identity_checkpoint) if continuing and identity_checkpoint else None,
                output_dir=self.options.run_directory / job.id,
                mode=request.get('mode', 'full'),
                selected_author=job.selection if continuing else None,
                include_needs_review=bool(request.get('include_needs_review')),
                maximum_pdf_papers=limits.get('max_pdf_papers'),
                maximum_downloads=limits.get('max_downloads'),
            )
        finally:
            spent = (release_ledger(ledger) or {}).get('estimated_cost_usd', 0.0)
        return self.outcome(job, report, spent)

    def outcome(self, job: Job, report: dict[str, Any], cost_usd: float = 0.0) -> SearchOutcome:
        directory = Path(str(report['output_directory'])) if report.get('output_directory') else None
        status = report.get('run_status')
        errors = [str(item.get('error') or item) for item in report.get('errors') or []]
        if status == RunStatus.IDENTITY_CONFIRMATION_REQUIRED:
            found = candidates(report.get('identity') or {})
            if not found or job.selection is not None:
                return SearchOutcome(
                    JobStatus.FAILED,
                    error={
                        'code': 'identity_not_found' if not found else 'selection_rejected',
                        'message': 'No researcher matching the request could be identified'
                        if not found
                        else 'The confirmed candidate could not be used',
                        'details': errors,
                    },
                    cost_usd=cost_usd,
                    run_directory=directory,
                )
            checkpoint = (
                directory / (report.get('artifacts') or {}).get('professor', 'professor.json') if directory else None
            )
            return SearchOutcome(
                JobStatus.NEEDS_CONFIRMATION,
                found,
                identity_checkpoint=checkpoint,
                cost_usd=cost_usd,
                run_directory=directory,
            )
        if status not in (RunStatus.COMPLETED, RunStatus.PARTIAL):
            return SearchOutcome(
                JobStatus.FAILED,
                error={'code': 'search_failed', 'message': f'The search ended as {status}', 'details': errors},
                cost_usd=cost_usd,
                run_directory=directory,
            )
        view = ResultView(lambda paper_id: self.download_link(job.id, paper_id))
        result = view.build(report, bool(job.request.get('include_needs_review')))
        files = {}
        for paper in result['papers']:
            for edition in [paper, *(paper.get('editions') or [])]:
                if edition['full_text']['status'] == 'file':
                    path = view.file_path(report, edition['id'])
                    if path is not None:
                        files[edition['id']] = path
        return SearchOutcome(
            JobStatus.SUCCEEDED, result=result, files=files, cost_usd=cost_usd, run_directory=directory
        )

    def _run_options(self, job: Job, ledger: str) -> RunOptions:
        return RunOptions(
            mode=job.request.get('mode', 'full'),
            ambiguity_policy=AmbiguityPolicy.COLLECT_CONTENDERS
            if job.request.get('on_ambiguity') == 'collect_contenders'
            else AmbiguityPolicy.STOP,
            max_cost_usd=self.options.maximum_cost_usd,
            cost_ledger=ledger,
            http_cache_dir=self.options.http_cache_directory or None,
            http_cache_mode=self.options.http_cache_mode if self.options.http_cache_directory else None,
        )
